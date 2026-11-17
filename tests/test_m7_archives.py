import threading
import time
from decimal import Decimal

import pytest

from app.errors import DomainError
from app.modules.m7_archives import core
from app.modules.m7_archives.store import ArchiveStore


@pytest.fixture
def store(tmp_path):
    return ArchiveStore(str(tmp_path / "archives.db"))


def item(no, key, amount, payload=None):
    return {"voucher_no": no, "apportionment_key": key, "amount": amount, "payload": payload or {}}


def batch(org, seq, items, batch_no=None, stream=None):
    return {"source_org": org, "stream": stream or org,
            "batch_no": batch_no or f"B{seq}", "batch_seq": seq, "items": items}


def ingest(store, envelope):
    with store.write() as db:
        return core.ingest_batch(db, envelope)


def summary(store):
    with store.read() as db:
        return core.summary(db)


def adjudicate(store, key, amount, reason="复核会议"):
    with store.write() as db:
        return core.adjudicate(db, {"apportionment_key": key, "decided_amount": amount, "reason": reason})


def test_trap_1(store):  # TRAP-31 异地同批次号同凭证号：按批次号去重会吞掉异地凭证
    ingest(store, batch("华东", 1, [item("V-1", "AP-9", "500.00", {"scan": "hd.pdf"})], batch_no="004"))
    ingest(store, batch("西北", 1, [item("V-1", "AP-9", "500.00", {"scan": "xb.pdf"})], batch_no="004"))
    assert summary(store)["voucher_count"] == 2
    with store.read() as db:
        east = core.get_voucher(db, "华东", "004", "V-1")
        west = core.get_voucher(db, "西北", "004", "V-1")
    assert east["voucher"]["payload"] == {"scan": "hd.pdf"}  # 原号查回，原件保留
    assert west["voucher"]["payload"] == {"scan": "xb.pdf"}


def test_trap_2(store):  # TRAP-32 两地重复报送同一分摊：全部相加会重复计资
    ingest(store, batch("华东", 1, [item("V-1", "AP-9", "500.00")]))
    ingest(store, batch("西北", 1, [item("V-1", "AP-9", "500.00")]))
    ingest(store, batch("华南", 1, [item("V-7", "AP-9", "500.00")]))
    s = summary(store)
    assert s["voucher_count"] == 3
    assert s["apportionment_count"] == 1
    assert s["effective_total"] == "500.00"  # 全部相加会得到 1500.00，属重复计资


def test_trap_3(store):  # TRAP-33 接收中断重传：重试不得重复计资
    assert ingest(store, batch("华东", 1, [item("V-1", "AP-1", "100.00")]))["status"] == "RECEIVED"
    replay = ingest(store, batch("华东", 1, [item("V-1", "AP-1", "100.00")]))
    assert replay["status"] == "REPLAYED"  # 中断重试不重复计资
    assert summary(store)["effective_total"] == "100.00"
    with pytest.raises(DomainError) as gap:
        ingest(store, batch("华东", 3, [item("V-3", "AP-3", "300.00")]))
    assert gap.value.code == "INGEST_GAP"
    with store.read() as db:
        assert core.checkpoint(db, "华东")["last_seq"] == 1  # 断点位置保持
    assert ingest(store, batch("华东", 2, [item("V-2", "AP-2", "200.00")]))["status"] == "RECEIVED"
    assert summary(store)["effective_total"] == "300.00"
    with pytest.raises(DomainError) as conflict:
        ingest(store, batch("华东", 2, [item("V-2", "AP-2", "999.00")]))
    assert conflict.value.code == "REPLAY_CONFLICT"  # 同序号不同内容必须报警
    assert summary(store)["effective_total"] == "300.00"


def test_重试与批内重复不重复计资(store):
    first = batch("华东", 1, [item("V-1", "AP-1", "100.00"), item("V-2", "AP-1", "100.00")])
    ingest(store, first)
    assert summary(store)["effective_total"] == "100.00"  # 同地两张凭证同一分摊仍计一次
    second = ingest(store, batch("华东", 2, [item("V-1", "AP-1", "100.00"), item("V-3", "AP-1", "100.00")], batch_no="B1"))
    assert second["dedup_hits"] == 1 and second["new_vouchers"] == 1
    s = summary(store)
    assert s["voucher_count"] == 3 and s["effective_total"] == "100.00"


def test_金额不一致先隔离(store):
    ingest(store, batch("华东", 1, [item("V-1", "AP-5", "100.00")]))
    receipt = ingest(store, batch("西北", 1, [item("V-1", "AP-5", "120.00")]))
    assert receipt["quarantined"] == ["AP-5"]
    s = summary(store)
    assert s["quarantined_count"] == 1 and s["effective_total"] == "0.00"  # 隔离不计入有效总额
    with store.read() as db:
        disputes = core.list_disputes(db, "OPEN")
        view = core.get_attribution(db, "AP-5")
    assert len(disputes) == 1 and disputes[0]["cause"] == "AMOUNT_CONFLICT"
    assert {m["amount"] for m in view["members"]} == {"100.00", "120.00"}


def test_更正后自动解除隔离(store):
    ingest(store, batch("华东", 1, [item("V-1", "AP-5", "100.00")]))
    ingest(store, batch("西北", 1, [item("V-1", "AP-5", "120.00")]))
    assert summary(store)["quarantined_count"] == 1
    ingest(store, batch("西北", 2, [item("V-1", "AP-5", "100.00")], batch_no="B1"))  # 同号更正
    s = summary(store)
    assert s["quarantined_count"] == 0 and s["effective_total"] == "100.00"
    with store.read() as db:
        assert core.list_disputes(db, "CLOSED_AUTO")
        voucher = core.get_voucher(db, "西北", "B1", "V-1")
    assert voucher["voucher"]["version_no"] == 2 and len(voucher["history"]) == 1  # 旧版本留痕


def test_trap_5(store):  # TRAP-35 裁决后来源变更或新异议：旧裁决必须失效
    ingest(store, batch("华东", 1, [item("V-1", "AP-5", "100.00")]))
    ingest(store, batch("西北", 1, [item("V-1", "AP-5", "120.00")]))
    adj = adjudicate(store, "AP-5", "110.00")
    assert len(adj["bound_versions"]) == 2  # 裁决绑定已见来源版本
    assert summary(store)["effective_total"] == "110.00"
    ingest(store, batch("西北", 2, [item("V-1", "AP-5", "120.00", {"rev": 2})], batch_no="B1"))
    with store.read() as db:
        view = core.get_attribution(db, "AP-5")
    assert view["attribution"]["status"] == "QUARANTINED"  # 来源版本变化，旧裁决失效
    assert view["adjudications"][0]["status"] == "INVALIDATED"
    adjudicate(store, "AP-5", "115.00")
    assert summary(store)["effective_total"] == "115.00"
    with store.write() as db:
        obj = core.raise_objection(db, {"apportionment_key": "AP-5", "note": "单价存疑"})
    assert obj["invalidated_adjudications"] == 1  # 新增异议使旧裁决失效
    s = summary(store)
    assert s["quarantined_count"] == 1 and s["effective_total"] == "0.00"
    with store.read() as db:
        causes = [d["cause"] for d in core.list_disputes(db, "OPEN")]
    assert causes == ["OBJECTION"]


def test_失败批次不留痕迹(store):
    ingest(store, batch("华东", 1, [item("V-1", "AP-1", "100.00")]))
    with pytest.raises(DomainError) as err:
        ingest(store, batch("华东", 2, [item("V-2", "AP-2", "200.00"), item("V-3", "AP-3", "N/A")]))
    assert err.value.code == "INVALID_VALUE"
    s = summary(store)
    assert s["voucher_count"] == 1 and s["effective_total"] == "100.00"  # 整批回滚
    with store.read() as db:
        assert core.checkpoint(db, "华东")["last_seq"] == 1
        with pytest.raises(DomainError):
            core.get_voucher(db, "华东", "B2", "V-2")


def test_trap_4(tmp_path):  # TRAP-34 并发接收与裁决：不得暴露半份汇总
    store = ArchiveStore(str(tmp_path / "concurrent.db"))
    errors, polls = [], []
    done = threading.Event()

    def ingester(org, amount):
        try:
            for seq in range(1, 6):
                ingest(store, batch(org, seq, [item("V-1", f"AP-S{seq}", "100.00"),
                                               item("V-2", f"AP-C{seq}", amount)]))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def adjudicator():
        try:
            while not done.is_set():
                with store.read() as db:
                    keys = [d["apportionment_key"] for d in core.list_disputes(db, "OPEN")]
                for key in keys:
                    adjudicate(store, key, "65.00", "并发裁决")
                time.sleep(0.002)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def poller():
        last = -1
        try:
            while not done.is_set():
                snap = summary(store)
                assert snap["voucher_count"] >= last, "汇总出现回退"
                last = snap["voucher_count"]
                assert snap["effective_count"] + snap["quarantined_count"] == snap["apportionment_count"]
                polls.append(snap)
                time.sleep(0.001)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=ingester, args=("华东", "60.00")),
               threading.Thread(target=ingester, args=("西北", "70.00")),
               threading.Thread(target=adjudicator), threading.Thread(target=poller)]
    for t in threads:
        t.start()
    for t in threads[:2]:
        t.join()
    while True:  # 收尾：全部争议裁决完毕
        with store.read() as db:
            keys = [d["apportionment_key"] for d in core.list_disputes(db, "OPEN")]
        if not keys:
            break
        for key in keys:
            adjudicate(store, key, "65.00", "收尾裁决")
    done.set()
    for t in threads[2:]:
        t.join()
    assert not errors
    assert polls, "轮询未执行"
    final = summary(store)
    assert final["voucher_count"] == 20
    assert final["quarantined_count"] == 0
    assert final["effective_total"] == str(Decimal("825.00"))  # 5×100.00 + 5×65.00


def test_api_flow(client):
    east = batch("华东", 1, [item("V-1", "APPORT-1", "500.00", {"scan": "hd.pdf"})],
                 batch_no="004", stream="hd")
    west = batch("西北", 1, [item("V-1", "APPORT-1", "520.00", {"scan": "xb.pdf"})],
                 batch_no="004", stream="xb")
    assert client.post("/api/archives/ingest", json=east).json()["status"] == "RECEIVED"
    assert client.post("/api/archives/ingest", json=west).json()["quarantined"] == ["APPORT-1"]
    assert client.get("/api/archives/summary").json()["quarantined_count"] == 1
    voucher = client.get("/api/archives/vouchers/华东/004/V-1").json()
    assert voucher["voucher"]["payload"] == {"scan": "hd.pdf"}
    view = client.get("/api/archives/attributions/APPORT-1").json()
    assert view["attribution"]["status"] == "QUARANTINED" and len(view["members"]) == 2
    assert len(client.get("/api/archives/disputes?status=OPEN").json()["disputes"]) == 1
    adj = client.post("/api/archives/adjudications",
                      json={"apportionment_key": "APPORT-1", "decided_amount": "510.00"}).json()
    assert adj["status"] == "ADJUDICATED"
    assert client.get("/api/archives/summary").json()["effective_total"] == "510.00"
    obj = client.post("/api/archives/objections",
                      json={"apportionment_key": "APPORT-1", "note": "单价存疑"}).json()
    assert obj["invalidated_adjudications"] == 1
    assert client.get("/api/archives/summary").json()["effective_total"] == "0.00"
    assert client.get("/api/archives/checkpoint?stream=hd").json()["last_seq"] == 1
    assert client.get("/api/archives/vouchers/华东/004/NOPE").status_code == 422
    assert client.get("/api/archives/attributions/NOPE").status_code == 422
    page = client.get("/archives")
    assert page.status_code == 200 and "panel" in page.text


def test_api_sample_idempotent(client):
    first = client.post("/api/archives/ingest", json={}).json()
    assert first["status"] == "SAMPLE"
    s1 = client.get("/api/archives/summary").json()
    assert s1["voucher_count"] == 4 and s1["effective_total"] == "500.00"
    assert s1["quarantined_count"] == 1  # APPORT-1002 两地金额不一致
    second = client.post("/api/archives/ingest", json={}).json()
    assert all(r["status"] == "REPLAYED" for r in second["receipts"])
    assert client.get("/api/archives/summary").json() == s1  # 重跑样例不改变结果

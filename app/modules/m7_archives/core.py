from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app.errors import DomainError
from app.manifest import canonical_digest

CENT = Decimal("0.01")

# 样例：两地同批次号同凭证号报送同一分摊；APPORT-1002 金额不一致，先入隔离。
SAMPLE_BATCHES = [
    {"source_org": "华东", "stream": "hd-archive", "batch_no": "2026-004", "batch_seq": 1, "items": [
        {"voucher_no": "V-001", "apportionment_key": "APPORT-1001", "amount": "500.00",
         "payload": {"title": "差旅费分摊单", "scan": "hd-2026-004-001.pdf"}},
        {"voucher_no": "V-002", "apportionment_key": "APPORT-1002", "amount": "300.00",
         "payload": {"title": "设备购置分摊单", "scan": "hd-2026-004-002.pdf"}},
    ]},
    {"source_org": "西北", "stream": "xb-archive", "batch_no": "2026-004", "batch_seq": 1, "items": [
        {"voucher_no": "V-001", "apportionment_key": "APPORT-1001", "amount": "500.00",
         "payload": {"title": "差旅费分摊单", "scan": "xb-2026-004-001.pdf"}},
        {"voucher_no": "V-002", "apportionment_key": "APPORT-1002", "amount": "360.00",
         "payload": {"title": "设备购置分摊单", "scan": "xb-2026-004-002.pdf"}},
    ]},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def money(value) -> str:
    if value is None or str(value).strip() == "":
        raise DomainError("EMPTY_VALUE", "金额为空")
    try:
        amount = Decimal(str(value).replace(",", "").strip())
        quantized = amount.quantize(CENT)
    except (InvalidOperation, ValueError):
        raise DomainError("INVALID_VALUE", f"金额无法解析: {value!r}")
    if not amount.is_finite() or quantized != amount:
        raise DomainError("INVALID_VALUE", f"金额超出分位精度: {value!r}")
    return str(quantized)


def _uid(source_org: str, batch_no: str, voucher_no: str) -> str:
    return f"{source_org}/{batch_no}/{voucher_no}"


def _content_hash(item: dict) -> str:
    return canonical_digest({"apportionment_key": item["apportionment_key"],
                             "amount": item["amount"], "payload": item["payload"]})


# ---------- 接收 ----------

def _normalize_item(raw, line: int) -> dict:
    if not isinstance(raw, dict):
        raise DomainError("INVALID_VALUE", f"第{line}条凭证不是对象")
    for field in ("voucher_no", "apportionment_key"):
        if not str(raw.get(field) or "").strip():
            raise DomainError("EMPTY_VALUE", f"第{line}条凭证缺少 {field}")
    return {"voucher_no": str(raw["voucher_no"]).strip(),
            "apportionment_key": str(raw["apportionment_key"]).strip(),
            "amount": money(raw.get("amount")),
            "payload": raw.get("payload", {})}


def _normalize_envelope(payload) -> dict:
    if not isinstance(payload, dict):
        raise DomainError("INVALID_VALUE", "接收报文必须是对象")
    source_org = str(payload.get("source_org") or "").strip()
    batch_no = str(payload.get("batch_no") or "").strip()
    stream = str(payload.get("stream") or source_org).strip()
    if not source_org or not batch_no or not stream:
        raise DomainError("EMPTY_VALUE", "source_org/batch_no/stream 不能为空")
    raw_seq = payload.get("batch_seq")
    if raw_seq is None:
        raise DomainError("EMPTY_VALUE", "batch_seq 不能为空")
    if isinstance(raw_seq, bool) or (isinstance(raw_seq, float) and not raw_seq.is_integer()):
        raise DomainError("INVALID_VALUE", "batch_seq 必须是正整数")
    try:
        batch_seq = int(raw_seq)
    except (TypeError, ValueError):
        raise DomainError("INVALID_VALUE", "batch_seq 必须是正整数")
    if batch_seq < 1:
        raise DomainError("INVALID_VALUE", "batch_seq 必须是正整数")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise DomainError("EMPTY_VALUE", "items 不能为空")
    return {"source_org": source_org, "stream": stream, "batch_no": batch_no,
            "batch_seq": batch_seq,
            "items": [_normalize_item(x, i) for i, x in enumerate(items, 1)]}


def _apply_item(db, env: dict, item: dict, ts: str):
    key = (env["source_org"], env["batch_no"], item["voucher_no"])
    content_hash = _content_hash(item)
    payload_text = json.dumps(item["payload"], ensure_ascii=False, sort_keys=True)
    row = db.execute(
        "SELECT * FROM arch_vouchers WHERE source_org=? AND batch_no=? AND voucher_no=?", key).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO arch_vouchers (source_org, batch_no, voucher_no, apportionment_key, amount,"
            " content_hash, version_no, payload, stream, batch_seq, received_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (*key, item["apportionment_key"], item["amount"], content_hash, 1,
             payload_text, env["stream"], env["batch_seq"], ts))
        return "new_vouchers", None
    if row["content_hash"] == content_hash:
        return "dedup_hits", None
    db.execute(
        "INSERT INTO arch_voucher_versions (source_org, batch_no, voucher_no, version_no,"
        " apportionment_key, amount, content_hash, payload, received_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (row["source_org"], row["batch_no"], row["voucher_no"], row["version_no"],
         row["apportionment_key"], row["amount"], row["content_hash"], row["payload"], row["received_at"]))
    db.execute(
        "UPDATE arch_vouchers SET apportionment_key=?, amount=?, content_hash=?, version_no=?,"
        " payload=?, stream=?, batch_seq=?, received_at=?"
        " WHERE source_org=? AND batch_no=? AND voucher_no=?",
        (item["apportionment_key"], item["amount"], content_hash, row["version_no"] + 1,
         payload_text, env["stream"], env["batch_seq"], ts, *key))
    return "new_versions", row["apportionment_key"]


def ingest_batch(db, payload) -> dict:
    """接收一批异地档案：整批单事务；流内序号连续；重放同摘要直接跳过。"""
    env = _normalize_envelope(payload)
    digest = canonical_digest(env)
    cp = db.execute("SELECT * FROM arch_checkpoints WHERE stream=?", (env["stream"],)).fetchone()
    expected = 1 if cp is None else cp["last_seq"] + 1
    if env["batch_seq"] < expected:
        if cp is not None and digest == cp["last_digest"]:
            return {"status": "REPLAYED", "stream": env["stream"], "batch_seq": env["batch_seq"],
                    "received": 0, "new_vouchers": 0, "new_versions": 0, "dedup_hits": 0,
                    "affected": [], "quarantined": [],
                    "checkpoint": {"stream": env["stream"], "last_seq": cp["last_seq"]}}
        raise DomainError("REPLAY_CONFLICT",
                          f"流 {env['stream']} 批次 {env['batch_seq']} 与已存摘要不符")
    if env["batch_seq"] > expected:
        raise DomainError("INGEST_GAP",
                          f"流 {env['stream']} 期望批次 {expected}，收到 {env['batch_seq']}，请从断点续传")
    ts = _now()
    stats = {"new_vouchers": 0, "new_versions": 0, "dedup_hits": 0}
    affected = set()
    for item in env["items"]:
        kind, old_key = _apply_item(db, env, item, ts)
        stats[kind] += 1
        affected.add(item["apportionment_key"])
        if old_key and old_key != item["apportionment_key"]:
            affected.add(old_key)
    results = {key: reconcile(db, key) for key in sorted(affected)}
    db.execute(
        "INSERT INTO arch_checkpoints (stream, source_org, last_seq, last_digest, updated_at)"
        " VALUES (?,?,?,?,?) ON CONFLICT(stream) DO UPDATE SET source_org=excluded.source_org,"
        " last_seq=excluded.last_seq, last_digest=excluded.last_digest, updated_at=excluded.updated_at",
        (env["stream"], env["source_org"], env["batch_seq"], digest, ts))
    return {"status": "RECEIVED", "stream": env["stream"], "batch_seq": env["batch_seq"],
            "received": stats["new_vouchers"] + stats["new_versions"], **stats,
            "affected": sorted(affected),
            "quarantined": sorted(k for k, r in results.items() if r["status"] == "QUARANTINED"),
            "checkpoint": {"stream": env["stream"], "last_seq": env["batch_seq"]}}


# ---------- 对账 ----------

def _observed(db, key: str) -> list[dict]:
    rows = db.execute(
        "SELECT source_org, batch_no, voucher_no, version_no, content_hash FROM arch_vouchers"
        " WHERE apportionment_key=? ORDER BY source_org, batch_no, voucher_no", (key,)).fetchall()
    return [{"voucher": _uid(r["source_org"], r["batch_no"], r["voucher_no"]),
             "version_no": r["version_no"], "hash": r["content_hash"]} for r in rows]


def _ensure_conflict_dispute(db, key: str, amounts: list[str], observed: list[dict], ts: str) -> None:
    open_row = db.execute(
        "SELECT id FROM arch_disputes WHERE apportionment_key=? AND cause='AMOUNT_CONFLICT'"
        " AND status='OPEN'", (key,)).fetchone()
    if open_row:
        return
    detail = json.dumps({"amounts": amounts, "versions": observed}, ensure_ascii=False, sort_keys=True)
    db.execute("INSERT INTO arch_disputes (apportionment_key, cause, status, detail, opened_at)"
               " VALUES (?,?,?,?,?)", (key, "AMOUNT_CONFLICT", "OPEN", detail, ts))


def reconcile(db, key: str) -> dict:
    """重建统一分摊归属：归属是派生状态，任何来源变化后重算，保证同一分摊只计一次。"""
    ts = _now()
    members = db.execute(
        "SELECT * FROM arch_vouchers WHERE apportionment_key=?"
        " ORDER BY source_org, batch_no, voucher_no", (key,)).fetchall()
    if not members:
        db.execute("DELETE FROM arch_attributions WHERE apportionment_key=?", (key,))
        db.execute("UPDATE arch_disputes SET status='CLOSED_AUTO', closed_at=?"
                   " WHERE apportionment_key=? AND cause='AMOUNT_CONFLICT' AND status='OPEN'", (ts, key))
        return {"apportionment_key": key, "status": "VACATED", "effective_amount": None}
    observed = [{"voucher": _uid(m["source_org"], m["batch_no"], m["voucher_no"]),
                 "version_no": m["version_no"], "hash": m["content_hash"]} for m in members]
    digest = canonical_digest(observed)
    amounts = sorted({m["amount"] for m in members})
    adjudication = db.execute(
        "SELECT * FROM arch_adjudications WHERE apportionment_key=? AND status='ACTIVE'"
        " ORDER BY id DESC LIMIT 1", (key,)).fetchone()
    if adjudication and adjudication["bound_digest"] != digest:
        db.execute("UPDATE arch_adjudications SET status='INVALIDATED', invalidated_at=? WHERE id=?",
                   (ts, adjudication["id"]))
        adjudication = None
    objection_open = db.execute(
        "SELECT COUNT(*) c FROM arch_disputes WHERE apportionment_key=? AND cause='OBJECTION'"
        " AND status='OPEN'", (key,)).fetchone()["c"] > 0
    if adjudication:
        status, effective = "EFFECTIVE", adjudication["decided_amount"]
    elif objection_open:
        status, effective = "QUARANTINED", None
    elif len(amounts) > 1:
        status, effective = "QUARANTINED", None
        _ensure_conflict_dispute(db, key, amounts, observed, ts)
    else:
        status, effective = "EFFECTIVE", amounts[0]
        db.execute("UPDATE arch_disputes SET status='CLOSED_AUTO', closed_at=?"
                   " WHERE apportionment_key=? AND cause='AMOUNT_CONFLICT' AND status='OPEN'", (ts, key))
    db.execute(
        "INSERT INTO arch_attributions (apportionment_key, status, effective_amount, member_count,"
        " observed_digest, updated_at) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(apportionment_key) DO UPDATE SET status=excluded.status,"
        " effective_amount=excluded.effective_amount, member_count=excluded.member_count,"
        " observed_digest=excluded.observed_digest, updated_at=excluded.updated_at",
        (key, status, effective, len(members), digest, ts))
    return {"apportionment_key": key, "status": status, "effective_amount": effective}


def adjudicate(db, payload) -> dict:
    """裁决：决定金额并绑定当前已见来源版本；之后的来源变更或异议都会使其失效。"""
    payload = payload or {}
    key = str(payload.get("apportionment_key") or "").strip()
    if not key:
        raise DomainError("EMPTY_VALUE", "apportionment_key 不能为空")
    attr = db.execute("SELECT * FROM arch_attributions WHERE apportionment_key=?", (key,)).fetchone()
    if attr is None:
        raise DomainError("NOT_FOUND", f"分摊归属不存在: {key}")
    decided = money(payload.get("decided_amount"))
    reason = str(payload.get("reason") or "复核裁决").strip()
    ts = _now()
    observed = _observed(db, key)
    digest = canonical_digest(observed)
    db.execute("UPDATE arch_adjudications SET status='INVALIDATED', invalidated_at=?"
               " WHERE apportionment_key=? AND status='ACTIVE'", (ts, key))
    cur = db.execute(
        "INSERT INTO arch_adjudications (apportionment_key, decided_amount, bound_digest,"
        " bound_versions, reason, status, decided_at) VALUES (?,?,?,?,?,?,?)",
        (key, decided, digest, json.dumps(observed, ensure_ascii=False), reason, "ACTIVE", ts))
    db.execute("UPDATE arch_disputes SET status='RESOLVED', closed_at=?"
               " WHERE apportionment_key=? AND status='OPEN'", (ts, key))
    reconcile(db, key)
    return {"status": "ADJUDICATED", "adjudication_id": cur.lastrowid, "apportionment_key": key,
            "decided_amount": decided, "bound_versions": observed,
            "attribution": get_attribution(db, key)["attribution"]}


def raise_objection(db, payload) -> dict:
    """登记异议：使现行裁决失效并重新隔离，等待再次裁决。"""
    payload = payload or {}
    key = str(payload.get("apportionment_key") or "").strip()
    if not key:
        raise DomainError("EMPTY_VALUE", "apportionment_key 不能为空")
    attr = db.execute("SELECT * FROM arch_attributions WHERE apportionment_key=?", (key,)).fetchone()
    if attr is None:
        raise DomainError("NOT_FOUND", f"分摊归属不存在: {key}")
    note = str(payload.get("note") or "").strip() or "复核异议"
    ts = _now()
    invalidated = db.execute(
        "UPDATE arch_adjudications SET status='INVALIDATED', invalidated_at=?"
        " WHERE apportionment_key=? AND status='ACTIVE'", (ts, key)).rowcount
    cur = db.execute("INSERT INTO arch_disputes (apportionment_key, cause, status, detail, opened_at)"
                     " VALUES (?,?,?,?,?)",
                     (key, "OBJECTION", "OPEN", json.dumps({"note": note}, ensure_ascii=False), ts))
    result = reconcile(db, key)
    return {"status": "OBJECTED", "dispute_id": cur.lastrowid, "apportionment_key": key,
            "invalidated_adjudications": invalidated, "attribution_status": result["status"]}


# ---------- 查询 ----------

def get_voucher(db, source_org: str, batch_no: str, voucher_no: str) -> dict:
    row = db.execute(
        "SELECT * FROM arch_vouchers WHERE source_org=? AND batch_no=? AND voucher_no=?",
        (source_org, batch_no, voucher_no)).fetchone()
    if row is None:
        raise DomainError("NOT_FOUND", f"凭证不存在: {_uid(source_org, batch_no, voucher_no)}")
    current = dict(row)
    current["payload"] = json.loads(current["payload"])
    history = []
    for old in db.execute(
            "SELECT * FROM arch_voucher_versions WHERE source_org=? AND batch_no=? AND voucher_no=?"
            " ORDER BY version_no", (source_org, batch_no, voucher_no)).fetchall():
        item = dict(old)
        item["payload"] = json.loads(item["payload"])
        history.append(item)
    return {"voucher": current, "history": history}


def get_attribution(db, key: str) -> dict:
    attr = db.execute("SELECT * FROM arch_attributions WHERE apportionment_key=?", (key,)).fetchone()
    if attr is None:
        raise DomainError("NOT_FOUND", f"分摊归属不存在: {key}")
    members = [dict(r) for r in db.execute(
        "SELECT source_org, batch_no, voucher_no, amount, version_no, content_hash, received_at"
        " FROM arch_vouchers WHERE apportionment_key=? ORDER BY source_org, batch_no, voucher_no",
        (key,)).fetchall()]
    disputes = [dict(r) for r in db.execute(
        "SELECT * FROM arch_disputes WHERE apportionment_key=? ORDER BY id", (key,)).fetchall()]
    adjudications = [dict(r) for r in db.execute(
        "SELECT * FROM arch_adjudications WHERE apportionment_key=? ORDER BY id", (key,)).fetchall()]
    return {"attribution": dict(attr), "members": members,
            "disputes": disputes, "adjudications": adjudications}


def list_disputes(db, status: str | None = None) -> list[dict]:
    if status:
        rows = db.execute("SELECT * FROM arch_disputes WHERE status=? ORDER BY id", (status,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM arch_disputes ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def checkpoint(db, stream: str) -> dict:
    row = db.execute("SELECT * FROM arch_checkpoints WHERE stream=?", (stream,)).fetchone()
    if row is None:
        return {"stream": stream, "source_org": None, "last_seq": 0, "last_digest": None}
    return dict(row)


def list_checkpoints(db) -> list[dict]:
    return [dict(r) for r in db.execute("SELECT * FROM arch_checkpoints ORDER BY stream").fetchall()]


def summary(db) -> dict:
    """有效总额：只对有效归属求和，同一分摊只计一次；单快照读取，不暴露半份汇总。"""
    attributions = db.execute("SELECT status, effective_amount FROM arch_attributions").fetchall()
    total = sum((Decimal(r["effective_amount"]) for r in attributions
                 if r["status"] == "EFFECTIVE" and r["effective_amount"] is not None), Decimal(0))
    count = lambda sql: db.execute(sql).fetchone()["c"]
    return {"status": "READY",
            "voucher_count": count("SELECT COUNT(*) c FROM arch_vouchers"),
            "version_count": count("SELECT COUNT(*) c FROM arch_voucher_versions"),
            "apportionment_count": len(attributions),
            "effective_count": sum(1 for r in attributions if r["status"] == "EFFECTIVE"),
            "quarantined_count": sum(1 for r in attributions if r["status"] == "QUARANTINED"),
            "effective_total": str(total.quantize(CENT)),
            "open_disputes": count("SELECT COUNT(*) c FROM arch_disputes WHERE status='OPEN'"),
            "stream_count": count("SELECT COUNT(*) c FROM arch_checkpoints")}

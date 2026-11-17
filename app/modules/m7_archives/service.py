from . import core


def ingest(store, payload) -> dict:
    with store.write() as db:
        if not payload:
            receipts = [core.ingest_batch(db, batch) for batch in core.SAMPLE_BATCHES]
            return {"status": "SAMPLE", "receipts": receipts, "summary": core.summary(db)}
        return core.ingest_batch(db, payload)


def adjudicate(store, payload) -> dict:
    with store.write() as db:
        return core.adjudicate(db, payload)


def raise_objection(store, payload) -> dict:
    with store.write() as db:
        return core.raise_objection(db, payload)


def summary(store) -> dict:
    with store.read() as db:
        return core.summary(db)


def page_data(store) -> dict:
    with store.read() as db:
        data = core.summary(db)
        data["disputes"] = core.list_disputes(db, "OPEN")
        return data


def voucher(store, source_org: str, batch_no: str, voucher_no: str) -> dict:
    with store.read() as db:
        return core.get_voucher(db, source_org, batch_no, voucher_no)


def attribution(store, key: str) -> dict:
    with store.read() as db:
        return core.get_attribution(db, key)


def disputes(store, status: str | None = None) -> dict:
    with store.read() as db:
        rows = core.list_disputes(db, status)
    return {"disputes": rows, "count": len(rows)}


def checkpoint(store, stream: str | None = None) -> dict:
    with store.read() as db:
        if stream:
            return core.checkpoint(db, stream)
        return {"checkpoints": core.list_checkpoints(db)}

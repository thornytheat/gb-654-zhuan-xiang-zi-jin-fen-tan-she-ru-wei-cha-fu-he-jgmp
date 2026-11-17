REASONS = {
    "BAD_COLUMNS": "字段数量不符", "EMPTY_VALUE": "必要值为空",
    "DUPLICATE_CONFLICT": "同一业务键取值冲突", "TRUNCATED": "尾记录不完整",
    "INVALID_VALUE": "值不符合定义", "STALE_VERSION": "提交版本已过期",
    "NOT_FOUND": "查询对象不存在", "INGEST_GAP": "批次序号不连续，需从断点续传",
    "REPLAY_CONFLICT": "重放批次与已存摘要冲突", "AMOUNT_CONFLICT": "同笔分摊金额不一致",
    "OBJECTION": "复核异议未决", "SOURCE_CHANGED": "来源凭证版本已变化",
}

def reason(code: str) -> str:
    return REASONS.get(code, "未登记原因")

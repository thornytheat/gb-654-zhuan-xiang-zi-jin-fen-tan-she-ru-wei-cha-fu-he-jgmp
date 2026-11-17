# 数据字典

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `run_id` | string | 否 | 输入摘要派生的稳定运行编号 |
| `source_line` | integer | 否 | 原文件物理行号 |
| `reason_code` | string | 是 | 隔离或冲突的稳定原因码 |
| `raw_value` | string | 是 | 未归一的原始证据 |
| `normalized_value` | string | 是 | 按项目规则得到的值 |
| `rule_version` | string | 否 | 计算规则版本 |
| `source_org` | string | 否 | 档案来源机构，与批次号、凭证号共同定位原凭证 |
| `batch_no` | string | 否 | 来源侧原始批次号，异地可同号 |
| `voucher_no` | string | 否 | 来源侧原始凭证号 |
| `apportionment_key` | string | 否 | 统一分摊归属业务键 |
| `content_hash` | string | 否 | 凭证内容摘要，标识来源版本 |
| `version_no` | integer | 否 | 凭证版本号，自 1 递增 |
| `effective_amount` | string | 是 | 归属有效金额，隔离时为空 |
| `bound_digest` | string | 否 | 裁决时已见来源版本集摘要 |
| `stream` | string | 否 | 接收流标识，检查点按流记录 |
| `batch_seq` | integer | 否 | 流内批次序号，自 1 连续 |
| `cause` | string | 否 | 争议起因：AMOUNT_CONFLICT / OBJECTION |

字符串编号逐字保留。金额与计量值用 `Decimal`。时间值带偏移并以 UTC 参与比较。区间统一采用左闭右开。

# 深度登记 专项资金分摊与舍入尾差复核

        - 规模：中型
        - 核心规则：先迭代封顶再按有效权重分配，最后用最大余额法落到分；输入顺序变化不得改变结果。
        - 使用者与工作流：导入随仓样例，逐模块复核，最后导出单一证据清单。

        ## 功能模块

        | 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
        |---|---|---|---|---|
        | M1 预算采信 | 运行样例并复核证据 | `/api/budgets/import` / `/budgets` | `app/modules/m1_budgets/core.py` `app/modules/m1_budgets/service.py` | `tests/test_m1_budgets.py` |
| M2 资格校验 | 运行样例并复核证据 | `/api/eligibility/evaluate` / `/eligibility` | `app/modules/m2_eligibility/core.py` `app/modules/m2_eligibility/service.py` | `tests/test_m2_eligibility.py` |
| M3 权重测算 | 运行样例并复核证据 | `/api/weights/calculate` / `/weights` | `app/modules/m3_weights/core.py` `app/modules/m3_weights/service.py` | `tests/test_m3_weights.py` |
| M4 金额分摊 | 运行样例并复核证据 | `/api/allocations/calculate` / `/allocations` | `app/modules/m4_allocations/core.py` `app/modules/m4_allocations/service.py` | `tests/test_m4_allocations.py` |
| M5 尾差裁决 | 运行样例并复核证据 | `/api/remainders/resolve` / `/remainders` | `app/modules/m5_remainders/core.py` `app/modules/m5_remainders/service.py` | `tests/test_m5_remainders.py` |
| M6 批次复核 | 运行样例并复核证据 | `/api/export/allocations.csv` / `/runs` | `app/modules/m6_runs/core.py` `app/modules/m6_runs/service.py` | `tests/test_m6_runs.py` |
| M7 档案合库 | 接收异地档案、对账裁决并复核汇总 | `/api/archives/ingest` / `/archives` | `app/modules/m7_archives/core.py` `app/modules/m7_archives/store.py` | `tests/test_m7_archives.py` |

        ## 陷阱登记

        | TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
        |---|---|---|---|---|---|
        | TRAP-05 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_budgets/core.py` | `tests/test_m1_budgets.py::test_trap_1` |
| TRAP-06 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_eligibility/core.py` | `tests/test_m2_eligibility.py::test_trap_1` |
| TRAP-07 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_weights/core.py` | `tests/test_m3_weights.py::test_trap_1` |
| TRAP-09 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_allocations/core.py` | `tests/test_m4_allocations.py::test_trap_1` |
| TRAP-10 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m5_remainders/core.py` | `tests/test_m5_remainders.py::test_trap_1` |
| TRAP-14 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m6_runs/core.py` | `tests/test_m6_runs.py::test_trap_1` |
| TRAP-19 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_budgets/core.py` | `tests/test_m1_budgets.py::test_trap_2` |
| TRAP-22 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_eligibility/core.py` | `tests/test_m2_eligibility.py::test_trap_2` |
| TRAP-24 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_weights/core.py` | `tests/test_m3_weights.py::test_trap_2` |
| TRAP-26 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_allocations/core.py` | `tests/test_m4_allocations.py::test_trap_2` |
| TRAP-31 | 异地同批次号同凭证号 | 按批次号去重吞掉异地凭证 | 复合主键（来源+批次号+凭证号），原号可查、原件保留 | `app/modules/m7_archives/core.py` | `tests/test_m7_archives.py::test_trap_1` |
| TRAP-32 | 两地重复报送同一分摊 | 全部相加重复计资 | 统一分摊归属派生汇总，同一分摊只计一次 | `app/modules/m7_archives/core.py` | `tests/test_m7_archives.py::test_trap_2` |
| TRAP-33 | 接收中断后重传 | 重试重复计资 | 流检查点+批次摘要，同摘要重放跳过、缺口拒收 | `app/modules/m7_archives/core.py` | `tests/test_m7_archives.py::test_trap_3` |
| TRAP-34 | 并发接收与裁决 | 暴露半份汇总 | 整批单事务提交，汇总单快照读取 | `app/modules/m7_archives/store.py` | `tests/test_m7_archives.py::test_trap_4` |
| TRAP-35 | 裁决后来源变更或新异议 | 旧裁决继续生效 | 裁决绑定已见来源版本摘要，变更即失效 | `app/modules/m7_archives/core.py` | `tests/test_m7_archives.py::test_trap_5` |

        ## 对账规则

        - 输入记录数 = 有效记录数 + 隔离记录数 + 空记录数。
        - 核心数值或事件数在转换、判定和导出之间保持守恒。
        - 同一输入摘要重跑复用原运行编号，不增加结果行。
        - 合库有效总额 = 各有效分摊归属金额之和，同一分摊键只计一次；隔离中的分摊不计入。
        - 接收流内批次序号严格连续；同摘要重放不产生新状态，断点从已存检查点续传。

        ## 已知不覆盖

        - 生产系统连接、身份认证和外部密钥不在演示范围内。

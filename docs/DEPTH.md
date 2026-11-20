# 深度登记 航运舱单拆并箱与重量守恒

规模：中型。使用者：货代单证员。核心规则：主提单与分提单拆并形成有向无环关系，件数和净毛重逐层守恒。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 舱单采信 | 执行并复核舱单采信 | `POST /api/intake` | `app/modules/intake/rules.py` | `tests/test_intake.py` |
| M2 包装映射 | 执行并复核包装映射 | `POST /api/packaging` | `app/modules/packaging/rules.py` | `tests/test_packaging.py` |
| M3 拆并编组 | 执行并复核拆并编组 | `POST /api/grouping` | `app/modules/grouping/rules.py` | `tests/test_grouping.py` |
| M4 危险品检查 | 执行并复核危险品检查 | `POST /api/hazard` | `app/modules/hazard/rules.py` | `tests/test_hazard.py` |
| M5 净毛重核验 | 执行并复核净毛重核验 | `POST /api/weight` | `app/modules/weight/rules.py` | `tests/test_weight.py` |
| M6 报关对账 | 执行并复核报关对账 | `POST /api/reconcile` | `app/modules/reconcile/rules.py` | `tests/test_reconcile.py` |
| M7 版本封存 | 执行并复核版本封存 | `POST /api/archive` | `app/modules/archive/rules.py` | `tests/test_archive.py` |
| A1 箱号继承 | 录入并箱两侧证据并生成继承回单、回滚旧版 | `POST /api/runs/{run_id}/receipts` | `app/modules/inheritance/rules.py` `app/store.py` | `tests/test_inheritance.py` |

## 箱号继承裁决规则

船开前临时并箱，两只及以上原箱货物进入同一只新箱；码头沿用旧箱号作业，船公司按重贴后的新箱号回执。回单按两侧证据能否闭合裁决：

- 申报侧：来源批次须为「可封存」；每张受影响主单的件数、净重、毛重与封签必须完整落入并箱原箱，跨箱或原箱无货即 `SOURCE_CONTAINER_AMBIGUOUS`。
- 码头侧：沿用旧箱号集合须与申报原箱集合逐箱相等（`TERMINAL_CONTAINER_MISMATCH`），且逐原箱留存封签证据（`SEAL_EVIDENCE_MISSING`）。
- 船公司侧：回执新箱号不得仍是旧箱号（`RECEIPT_BOX_UNLINKED`）、不得与并箱范围外在役箱号冲突（`NEW_CONTAINER_COLLISION`），回执合计与原箱逐主单合计逐项对平（`RECEIPT_TOTAL_MISMATCH`）。
- 拆并关系变化后，受影响主单整棵子树重新逐层核对件数/净重/毛重（`RECHECK_NOT_BALANCED`），不以平面求和替代。

任一阻断码出现则回单状态为「阻断待处理」，阻断回单只留痕、不顶替当前已确认版本；阻断码与说明全部写在回单上，不静默确认。回单让每张主单追到唯一最终箱号。

持久化保证（`inheritance_receipts` 表只追加）：

- 同 `(run_id, 幂等键)` 重试原样返回已存回单，不新增第二套；同键内容冲突返回 409，绝不覆盖。
- 同并箱事件换发新回单：旧版整行保留（含原箱封签证据）并下线，新版成为当前；`POST /api/runs/{run_id}/receipts/rollback` 可恢复指定或最近一版已确认旧版，无旧版可恢复时 422。
- 不同并箱事件的当前回单原箱不得交叉占用，冲突 409。
- 形状错误在写库前 422，库内旧版回单不受影响。

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码回退 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_legacy_source` |
| TRAP-02 | BOM表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_source` |
| TRAP-04 | 分段表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_segment_header` |
| TRAP-05 | 缺列隔离 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_column_isolation` |
| TRAP-14 | 编号冲突 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_conflict_all_isolated` |
| TRAP-18 | 空态三分 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_three_empty_states` |
| TRAP-15 | 吨与包装换算 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/packaging/rules.py` | `tests/test_packaging.py::test_pallet_real` |
| TRAP-07 | 空值哨兵 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/packaging/rules.py` | `tests/test_packaging.py::test_sentinel_real` |
| TRAP-06 | 全角千分位 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/packaging/rules.py` | `tests/test_packaging.py::test_fullwidth_decimal` |
| TRAP-20 | 拆并依赖环 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/grouping/rules.py` | `tests/test_grouping.py::test_cycle_real` |
| TRAP-21 | 缺失父级 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/grouping/rules.py` | `tests/test_grouping.py::test_missing_parent_real` |

## 对账规则

原始逻辑输入=采纳行+隔离行+空行+重复表头+同值重复。业务计算按 DATA-DICTIONARY.md 分别核验，不能把上级与下级重复加总。所有隔离保留编号、原值、原始行号和稳定原因码。

## 已知不覆盖

数据来自仓内样例或用户粘贴文件，不连接外部业务生产系统。平台不执行实际资金划转或行政审批。

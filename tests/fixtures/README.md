# 测试专用数据

单元测试（tests/test_*.py）只读取这个文件夹里的数据，**与 data/ 下的演示数据相互独立**。这样 B 调整演示数据时，不会让测试失败；反过来，修改这里的数据也不会影响演示。

| 文件 | 用途 |
| --- | --- |
| `seed_blocks_zh.json` | 中文版，大部分测试使用 |
| `seed_blocks_en.json` | 英文版，测试英文检索规则 |

两份文件一一对应：block_id、级别、范围、状态完全相同，只有标题和内容的语言不同。**修改任何一条数据，都要同步修改下面的预期表格和 tests/ 里写死的预期结果，** 否则测试会失败。

## 设计思路

- **三个演示 block 互相印证：** b-101（财务：华东区退款率 9 月飙升）、b-207（客服：9 月 13 日起支付失败工单激增，集中在华东和 Android v5.2）、b-318（发布记录：v5.2 于 9 月 12 日先在华东灰度上线，更换了支付网关 SDK）。三者合起来能推出预期结论。
- **b-318 故意设为 unverified，** 让 agent 在回答中体现“交叉核对后才引用”。
- **财务 agent 看不到的两条关键信息：** b-412（管理层已决定暂停 v5.2 并补偿用户，L3）和 b-501（根因：回调超时 15 秒即判失败，研发范围）。它们都会命中演示查询，服务日志里能看到 FILTERED，正好展示权限过滤。
- **干扰项：** b-102（华北区退款率基本持平）、b-208（退货流程，与支付失败退款无关）、b-413（Q4 预算）。

## 预期权限矩阵（按 block_id 读取）

test_policy.py 以此为准。

| block | 级别 / 范围 | 状态 | finance（L2） | intern（L0） |
| --- | --- | --- | --- | --- |
| b-001 | L0 / public | verified | ALLOW | ALLOW |
| b-002 | L0 / public | verified | ALLOW | ALLOW |
| b-101 | L2 / finance | verified | ALLOW | LEVEL_TOO_LOW |
| b-102 | L2 / finance | verified | ALLOW | LEVEL_TOO_LOW |
| b-207 | L1 / support | verified | ALLOW | LEVEL_TOO_LOW |
| b-208 | L1 / support | verified | ALLOW | LEVEL_TOO_LOW |
| b-318 | L1 / release | unverified | ALLOW | LEVEL_TOO_LOW |
| b-412 | L3 / mgmt | verified | LEVEL_TOO_LOW | LEVEL_TOO_LOW |
| b-413 | L3 / mgmt | verified | LEVEL_TOO_LOW | LEVEL_TOO_LOW |
| b-501 | L1 / rnd | verified | SCOPE_MISMATCH | LEVEL_TOO_LOW |

intern 读 b-501 时级别和范围都不满足，按约定只返回 LEVEL_TOO_LOW。

## 预期检索结果（按 interfaces.md v0.3 第 4.2 节规则模拟）



**中文数据（seed_blocks_zh.json）：**

| agent | query | 返回（按得分排序） | 被过滤 |
| --- | --- | --- | --- |
| finance | `Q3 华东区 退款率 上升` | b-101, b-102, b-207, b-318, b-001 | b-412 |
| finance | `华东区的退款率为什么上升了` | b-101, b-102, b-207, b-318, b-001 | b-412 |
| finance | `支付失败 9月` | b-207, b-208, b-318, b-001, b-002 | b-412, b-413, b-501 |
| intern | `华东区退款率为什么上升` | b-001 | b-101, b-102, b-207, b-208, b-318, b-412 |

**英文数据（seed_blocks_en.json）：**

| agent | query | 返回（按得分排序） | 被过滤 |
| --- | --- | --- | --- |
| finance | `Q3 East China refund rate increase` | b-101, b-102, b-207, b-318, b-001 | b-412 |
| finance | `payment failure September` | b-207, b-002, b-101, b-208, b-318 | b-412, b-413, b-501 |
| intern | `East China refund rate` | b-001 | b-101, b-102, b-207, b-208, b-318, b-412 |

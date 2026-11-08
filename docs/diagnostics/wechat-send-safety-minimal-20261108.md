# P0 三项发送安全失败：限定范围结论（2026-11-08）

本次按结束条件 B 交付：确认真实竞态，但现有候选缺少可与发送交接共同执行的权威授权/撤销接口，不能用发送器内增加检查独立满足要求。没有修改业务代码、生产数据、Collector、筛选或 UI；没有上传、恢复付费调用、启用同步或部署。原 Goal 未恢复。

## 读取基线

- 仓库 HEAD：`d59b11e88a7244c88da574dbe3bc483ce454af36`。已检查最近提交、暂存区及工作区差异；保留原有 27 个已跟踪文件的未提交修改与全部原有未跟踪材料。暂存区原为空。
- 暂停交接：`docs/diagnostics/wechat-five-source-readiness-20261106.md` 中的 2026-11-08 交接段落。
- 原任务执行记录：本机 `C:/Users/ericp/.codex/sessions/2026/11/05/rollout-2026-11-05T08-33-02-01a1a91f-9758-7110-aa0f-d4ccdffa04e0.jsonl`，2026-11-08T02:47:00Z 完成的测试输出：92 passed、3 failed，耗时 28.22 秒。
- 候选根目录：`desktop/attention-desk/artifacts/p0-20261104/wechat-live-integration-snapshot-20261107-a/`（Git 忽略）。下列代码与测试位置均相对该目录。
- 已读取 `INTEGRATION-VALIDATION.md`、当前代码与测试；v26 补丁与六个已变更文件不一致，不能当作当前候选或部署包。正式目录 CloudSync 的现有未提交下载/配置改动不属于本次修复。

## 三项根因与可复现证据

| 失败测试 | 根因 | 失败证据 |
| --- | --- | --- |
| `test_send_race_known_gap.py::test_changed_evidence_after_final_recheck_never_leaves_host` | `wechat_incremental_sender.py:217` 后已取得的 bundle 不再受数据库强制保护。测试在第三次 `_verified` 返回前改变来源；`client.post` 仍使用旧 bundle。`_snapshot_preview` 在返回前结束只读事务，`data_version` 只能发现组装期间的提交。 | 测试第 155 行：模拟发布次数为 1，要求 0。 |
| `test_send_race_known_gap.py::test_revoked_authorization_after_last_check_never_leaves_host` | `_verified` 把授权回调压缩为瞬时布尔值；其返回后授权撤销不改变已经取得的 True。随后 `pending_dispatch_stop` 只读协作停止标记，不能保护独立的授权来源。 | 测试第 172 行：模拟发布次数为 1，要求 0。 |
| `test_stopped_one_shot_race_known_gap.py::test_one_shot_permission_withdrawn_after_last_check_never_leaves_host` | `wechat_stopped_reconciliation.py:142–151` 的 `ready()` 已检查单次许可；随后读取的是原授权的撤销标记，独立单次许可在此时被撤回，仍会发出含 ID/摘要的核对请求。提前持久化许可消耗可防复用，不能阻止这次未授权请求。 | 测试第 20 行：模拟核对次数为 1，要求 0。 |

三项均为代码协议缺口。测试使用合成状态、临时数据库和 `httpx.MockTransport`，没有真实微信内容或实际云端发送。测试在确定的位置注入状态变化，证明可达竞态；不据此推算现实发生概率。没有修改测试断言、跳过失败或将失败改成预期失败。

## 为什么不能独立补一次检查

`wechat_record_guard.py` 的锁只保护参与协议的调用方；`wechat_guarded_store.py` 的临时触发器只作用于安装它们的连接。独立 SQLite 写入仍可绕过。对任意布尔回调重复检查，始终存在最后一次返回至发送之间的窗口；HTTP 连接建立、连接池等待或重连还能扩大窗口。

`wechat_incremental_policy.py:50–75` 只有只读验证入口，真正的独立授权与安全证明由外部回调提供；一次性核对同样没有实际授权提供者。现有接口没有“申请发送许可并持有至交接”“撤销与发送互斥”的操作，也没有可强制约束回调所代表外部状态的版本/租约。因此不能把一个新增本地锁宣称为真实授权保护。

已检查可复用实验：`wechat_loopback_dispatch_experiment.py` 使用有界 `BEGIN IMMEDIATE` 和 Windows 文件共享限制，保护合成数据库与合成授权文件至正文交接。但它仅允许回环目的地；`test_loopback_dispatch_lease_experiment.py:454` 明确复现独立停止标记不被执行的缺口。直接移植它仍不能解决授权撤销，且需验证真实传输的等待、重连和取消边界。

## 后续最小依赖与处理方案

1. 提供现有授权来源的权威、可串行化接口，把持续授权、单次许可及停止请求纳入同一发送许可协议。撤销先完成则拒绝交接；交接先完成则明确记录在途，不能宣称撤回已交出的数据。这是授权状态接口的接入，不是要求用户此时授权上传。
2. 仅在该协议成立后，在现有发送/核对入口加入有界保护：锁定最终来源、审核、五群脱敏上下文及授权版本直至实际请求交接。可评估复用数据库写锁与文件租约；必须测量对现有 Collector 写入的等待，不擅自修改 Collector 或全局持锁等待远端响应。
3. 连接恢复必须重新取得有效许可并重建待发内容；结果未知只在获准时按摘要核对，禁止自动重发。保留现有 journal、fence 和回执处理。验收只需扩展当前三项竞态及断线/回执丢失用例，加入独立连接/进程撤销、等待连接期间撤销和保护超时。

该依赖尚未提供；本次不新建授权系统、不拓展至其他开发阶段。AI 筛选可以另行开展离线验收，但不能以本次结果宣称发送安全前置条件通过，也不能进入持续云同步验收。

## 本次最小回归

沿用原命令的五个模块：`test_wechat_incremental_recovery.py`、`test_wechat_incremental_sender.py`、`test_stopped_reconciliation_once.py`、`test_send_race_known_gap.py`、`test_stopped_one_shot_race_known_gap.py`，均位于 `server/tests_wechat/`。使用原 `.venv`、原合成 legacy/profile 夹具和禁外网的 `test-network-guard/sitecustomize.py`，每次分配新的临时目录，禁用 pytest 缓存与字节码写入。

最初的受限执行卡在 Windows `asyncio` 创建事件循环的 `socket._fallback_socketpair -> accept`；20 秒线程转储定位后已中止两次等待中的运行。允许本机回环通信后相同测试正常完成，外网阻断仍有效。该环境问题与三项断言失败不同，没有通过修改夹具绕过安全断言。

最终命令为 `python -u -m pytest <上述五个模块> -p no:cacheprovider -q --tb=short -o faulthandler_timeout=45 --basetemp <新临时目录>`。结果：**92 passed、3 failed、1 warning，84.45 秒，退出码 1**。三个失败位置与原记录完全一致，均为 `assert 1 == 0`。唯一警告来自 Starlette 的 httpx 弃用提示，不是失败原因。

修复前基线与本次结果均为 92/3；没有业务代码修复，所以不存在“修复后已通过”的结果。在这 95 项相关回归范围内没有新增失败；未重跑全系统，不能据此声明全系统没有回归。

## 回滚与交付边界

本次仅新增此诊断文档；回滚只需反向提交本次文档提交，无需服务重启或数据库操作。继承原回滚约束：保留两个暂停开关，不覆盖正式库、不倒退游标、不部署旧 v26 包。没有修复后的通过结果；业务代码未变，不能宣称三项已解决。

# 发送授权与交接修复：进行中

最终状态已更新：隔离修复验收见 [结果记录](wechat-send-handoff-result-20261108.md)。以下保留实施过程，不应将中间未完成状态当作最终结果。

本文件记录 `8c3a65e` 后新目标的实施状态，不替代其失败证据，不表示目标完成。

## 已实施

新增未接入运行服务的 `server/cloud_sync/wechat_dispatch_authority.py`，由独立 SQLite 文件持有导入的授权、撤销状态、一次性消费与交接记录。授权导入要求独立验证器确认不可变的完整 Grant；它不生成用户许可。`reserve` 持久化一次性消费及请求字节摘要；`handoff` 先持久化未知结果，再在授权写事务与内容保护同时有效时调用同步网络交接适配器。失败、崩溃、授权撤销不退款；重启不能重放同一 attempt。`revoke` 与交接竞争同一 SQLite 写事务，忙/超时不能报告撤销成功。

`locked_source` 用 `BEGIN IMMEDIATE` 阻止独立 SQLite 写入者，并在保护内只允许读取。它不会修改 Collector、业务表或数据。网络连接准备必须放在该保护之外；适配器必须同步接受不可变字节且不得在内部重试/等待远端响应。该适配器尚未实现，不能直接用普通 async HTTP 请求替代。

新增 `server/test_wechat_dispatch_authority.py`：2026-11-08 本轮 **14 passed in 1.92s**。覆盖真实独立进程撤销、独立进程写入排斥、内容变化、一次性消费持久化、连接等待期间撤销、未知结果防重放等。全部使用临时库及模拟交接，没有真实网络或生产数据。

## 已确认的状态所有者

- 来源/批次/消息/Raw/Review：PersonalStore 的 SQLite。`PersonalStore.connection(write=True)` 使用写事务；采集 `wechat_collect/ingestion.py` 与 `service.py` 写入消息/批次，`inbox_store.py` 等写入 Raw/审核投影。独立连接也能写入，因此不能只依赖 Python 锁或连接局部触发器。没有改动这些入口。
- 配置：`CloudSyncSettings` 在 `server/cloud_sync/settings.py` 使用临时文件替换；群配置与个人上下文通过 recovery 的 `document_read/context_read` 回调读取。文件状态仍须独立冻结，数据库锁不覆盖它们。
- 原候选授权：`ScopedWeChatSender`、`ReadOnlyPolicyVerifier`、`reconcile_stopped_once` 只接收外部验证回调。搜索候选非测试代码没有发现实际 sender/verifier 的实例化入口；运行服务没有注册此候选。新 authority 将成为导入授权后的状态所有者，不能把原外部回调仍任意可变的状态说成已经受它保护。

## 下一步（仍必须完成）

1. 将候选 sender 与 stopped reconciliation 的实际交接接入新 authority，而非仅在末尾加检查。保持既有 journal/fence/reconcile 行为；单次许可的 binding 必须绑定确切 metadata 请求与原 grant。
2. 实现明确的网络交接接口：连接准备期间可撤销，真正交接前重新取得授权/内容保护；交接后请求在途，不承诺撤回。接入文件配置/画像保护，并限制保护时长。普通 HTTP 客户端隐藏的连接等待、重试或 async await 不能被宣称为同步边界。
3. 原测试夹具必须使用同一真实状态管理接口，保留原三项断言与变化时机，新增边界/独立写入反例，防止仅调整夹具令旧测试变绿。
4. 重跑原五模块 95 项相关回归和新增交接测试；当前尚未重跑这 95 项，原三个失败未被修复。本轮新增测试通过不代表它们通过。
5. 最后仅提交本目标文件及可复核的候选差异；不要将既有 27 个文件的工作区修改或整个忽略目录混入提交。目前新增代码与测试未提交，目标保持 active。

原候选根目录仍为 `desktop/attention-desk/artifacts/p0-20261104/wechat-live-integration-snapshot-20261107-a/`。当前新增模块位于仓库正常 `desktop/attention-desk/server/`，尚未复制或导入到候选。不得部署或启用它。

## 第二轮实施更新（以上未接线状态已推进）

候选已接入新 authority 与 `wechat_dispatch_transport.py`。`ScopedWeChatSender._post_guarded` 是发布、普通核对与停止后一次性核对的统一出口；未提供协调器时拒绝，不能回退到普通 HTTP 请求。`CooperativeRecordGuard.request_revocation` 在存在协调状态库时先撤销其中的授权，失败不能报告成功停止。连接准备发生在数据库保护之外；实际接受请求发生在授权与内容保护同时有效期间；等待回执时二者已释放。原业务 journal/fence 仍负责结果未知的核对与防重发。

候选测试 helper `server/tests_wechat/dispatch_fixture.py` 使用同一个真实 SQLite authority；旧 rig 的可变内容、配置与布尔许可移入临时 SQLite，通过 `PersistentMap` 保留原测试的读写表达式。回归中新许可/改变范围的许可必须明确登记独立合成授权，而不是单纯 replace 一个对象。原三项竞态文件的断言与注入点没有改动。取消测试改在明确的交接后回执等待阶段取消，保留未知结果断言。

当前结果：

- 原五模块 95 项 + 新 `test_dispatch_boundary.py` 7 项：**102 passed，160.85s**。含原三项失败测试全部通过，正常授权发送、断网恢复与回执丢失路径也通过。
- 修改撤销入口直接相关的 `test_cooperative_record_guard.py`：**37 passed，11.62s**。
- 正常仓库中的新 authority 与请求编码测试：**17 passed，1.65s**。
- 没有生产网络请求或真实微信内容，测试使用隔离库及 MockTransport；Python 事件循环测试保留外网阻断并允许本机回环。

新增 `server/cloud_sync/wechat_prepared_connection.py` 提供不重试的 HTTP/1.1 预连接实现：TLS 创建后，`accept` 同步将完整不可变请求交给 asyncio transport；`response` 在锁释放后等待回执。当前只有三项内存 writer 编码/配置测试，**还没有真实回环 HTTP/TLS 串联验证**，不能将它视为完成验收。它尚未复制到候选、没有在运行服务注册。

仍需完成的具体工作：

1. 实际配置/画像仍由文件与内存缓存持有；目前 SQLite 状态保护在合成夹具中成立，不能外推为真实文件已经受保护。需复用现有 `wechat_approval_lease_experiment.immutable_evidence` 或等价 Windows 文件租约，接入真实 recovery 的上下文来源，并验证文件替换/写入及缓存一致性。不得修改 Collector。已定位 `WeChatService.save_settings`（候选 `service.py:79` 起）持有 `settings_lock`、保存文件、更新 repository 与 `self.document`；实际上下文类为 `UserContextStore`，接下来按 import 追踪，无需全系统审计。
2. 用合成回环 HTTP/TLS 验证 PreparedBackend 的真实交接边界、取消、断线与撤销；检查保护时长、到期、停止标记与单次授权的最终绑定。绝不发往真实 ENDPOINT。
3. 做针对性完成审计与最终回归，然后保存可复核代码提交。当前仍未提交，目标 active，不具备宣称全部安全前置条件通过的证据。

本轮修改的候选文件：`wechat_incremental_sender.py`、`wechat_stopped_reconciliation.py`、`wechat_record_guard.py`，三个原回归 fixture 文件以及 `test_cooperative_record_guard.py`；新增 `dispatch_fixture.py` 与 `test_dispatch_boundary.py`。authority/transport 从正常仓库新增模块复制到候选；后续修改需确保两处一致。用于最终精确差异的原文件已保存在邻接 `artifacts/p0-20261104/send-handoff-original-20261108/`，其中 record_guard 原文件通过逆转本轮的确切新增块恢复，不涉及此前改动。原始三项竞态测试未修改。

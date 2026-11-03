# 微信通知采集实施报告

更新：2026-10-22，Asia/Shanghai。按照用户 conditional execution policy 开发独立基础设施，最新优先级改为 pyweixin 真实兼容性验证。

**18:55 续测更新：跨 Windows 完整重启验证已完成，当前本机最终推荐 FALLBACK_TO_CUSTOM。** 已证实 DWORD=1 在 18:47 启动前写入并保持，正确登录态目标群新窗口 PID=30928、HWND=68308 的 Win32/UIA 归属、渲染子窗、Raw View 和本地标题 OCR 均已核对。聊天 Qt 节点仍为 0，两个 API 实际调用失败，实验设置已恢复并独立核验。最新报告见 [WECHAT-POST-REBOOT-RESULT.md](WECHAT-POST-REBOOT-RESULT.md)；下文未重启 Windows 的叙述仅属旧阶段历史。

## 当前交付状态

**完整系统尚未验收，生产采集未启用。** 已实施存储、去重、过滤、Batch、模型契约、媒体处理、复核/Evidence、调度、桌面接口和设置界面；验证使用隔离库与合成数据。

**最后 Qt accessibility pass 已完成，当前本机推荐 FALLBACK_TO_CUSTOM。** 除用户级初始化和真实讲述人验证外，已获用户及 UAC 授权临时设置 64 位 ForceUIAForQt=1，保持 SPI / HKCU 标志并完整重启微信。登录页仍暴露 28 个后代、22 个 mmui；用户手动登录后聊天树仍为 2 个 Win32 后代、0 个 mmui。两个读取 API 在 HKLM=1 期间再次实际调用，均失败于窗口识别。设置已全部恢复并独立核验。Windows 未重启，其效果仍未知。详见 [QT-UIA-ACCESSIBILITY-PASS.md](QT-UIA-ACCESSIBILITY-PASS.md)，早期五 API 记录保留于 [PYWEIXIN-FEASIBILITY.md](PYWEIXIN-FEASIBILITY.md)。

此前纯 UIA/MSAA 只能读到窗口外层；后来主动本地视觉 spike 已确认指定群，并在有限滚动范围内证明顺序重叠和往返锚点，结论限于 **IDLE_ONLY 的局部验证**。没有足够证据认证生产消息读取器。现有代码全部保留，本轮 pyweixin 调研期间暂停扩展自研消息解析。

| 阶段 | 实际完成情况 |
|---|---|
| Phase 0 / 最新 Phase A | 真实版本、UIA/MSAA、局部视觉、滚动重叠及 pyweixin 五个 API 均有记录；pyweixin 不可用，真实生产 Adapter 未验收 |
| Phase 1 | 配置、UserContext、v10 迁移、存储/队列、旧入口停用；正式数据库尚未升级 |
| Phase 2 | 只读协议、可终止诊断、空闲门禁、Fake Adapter；真实读取保持拒绝未认证能力 |
| Phase 3 | 内容签名、有序重叠、重复实例、cursor、歧义/缺口；生产 OCR 对齐与完整历史覆盖未验收 |
| Phase 4 | 三层过滤、有界上下文、一次补读、可恢复状态；合成数据验证 |
| Phase 5 | transport、Flash 非 Thinking、Schema、时间/角色校验、预算、OCR/Vision 支持；真实模型调用 0 |
| Phase 6 | 原文、图片派生块、候选、人工确认与 Evidence 接入；不自动写正式事项 |
| Phase 7 | 静默、06:00/23:00、启动/恢复合并策略、后台入口和登录脚本；原生电源事件及设备验收未完成，未注册启动任务 |
| Phase 8 | 微信设置、统计、记录、画像、Today 候选、复核与原图展示；真实绑定/扫描保持不可用 |
| Phase 9 | 自动回归、浏览器检查和真实库副本迁移已执行；真实采集及连续 7 天试运行未完成 |

## 真实设备能力

测试群为「26车辆2班（公告群）」。诊断仅容忍空格与全角/半角圆括号变化，保留班级数字和身份文字；具体群名不写入业务逻辑。

| 能力 | 当前结论 | 实测范围与限制 |
|---|---|---|
| 版本 / 窗口定位 | pass | 4.1.15.13；Win32 可定位已运行窗口 |
| Framework | pass | 原生 Qt51514QWindowIcon；UIA FrameworkId=Win32；MMUIRenderSubWindow |
| UIA / MSAA 会话列表、消息正文 | fail | 仅外层结构，无可访问聊天对象 |
| 指定群标题 | pass，局部视觉 | 先前主动本地 OCR/CV 匹配指定群标题；pyweixin 未返回群名 |
| 有限消息顺序 / 旧锚点 | partial | 前台、既定布局、小步滚动及往返成立；不代表长历史或生产增量可靠性 |
| sender / 消息发送时间 / 消息类型 / 媒体引用 | not_tested | 未建立可靠字段输出，OCR 文本不冒充准确发送时间 |
| pyweixin 五个读取 API | fail | 原环境和临时辅助功能环境各测五次；没有取得消息 |
| 前台 / 后台 / 最小化 pyweixin 读取 | fail | 三种状态已实际核验，各 pull_messages 一次；目标会话控件均为 0 |
| 免打扰 / 手机已读 / 无未读 | not_tested | 不把源码推断或空返回记成运行通过 |
| 微信重启后稳定性 | partial | 已完整退出重启并手动登录；登录页有 Qt 树，聊天界面仍未暴露；多轮重启稳定性未验收 |
| 被动检查的焦点/鼠标影响 | pass | DPI 初始化修正后，结构诊断保持焦点与物理鼠标位置 |
| 读取中真实用户活动中止 | not_tested | 有合成覆盖；不冒充真实消息读取过程验收 |
| 静默门禁 | pass | 夜间未进行真实微信、截图、OCR或模型操作 |

关键设备记录均在被忽略的 `artifacts/`：

- `wechat-spike/2026-10-22-stable-dpi.json`、`2026-10-22-msaa-tree.json`：UIA/MSAA 外壳与焦点。
- `wechat-spike/2026-10-22-corrected-group.json` 及标题对照报告：更正名称、过窄裁剪与扩大标题区域的探索。
- `wechat-spike/hybrid-history-02/history-report.json`：8 次捕获、8 次本地 OCR、6 次小步滚动、重叠和往返验证。截图持久化版本仅保留脱敏布局与哈希。
- `wechat-spike/hybrid-history-01`：曾完成实际捕获/滚动后遇到报告序列化错误；其失败摘要的零计数不能当作未发生操作。
- `pyweixin-spike/baseline/`、`temporary-accessibility/`、`qt-runtime-matrix-02/`：13 次实际 API 调用及状态矩阵。
- `pyweixin-spike/env-before.json`、`env-after.json`：辅助功能恢复和版本记录。

pyweixin 结果不只是读取库更换失败：直接传入已知主窗口的 listen_on_chat 也找不到聊天 List。本轮已补测 SPI、HKCU Narrator、Default 桌面、新 UIA 客户端、完整重启、手动登录、真实讲述人，以及获明确授权的 HKLM ForceUIAForQt=1；聊天界面的 session_list、ChatSessionCell、ChatListView/ChatMasterView、chat_input_field 仍为 0。不能据此确定具体是账号、客户端实现还是其他环境因素；Windows 重启未测试。

## 已实现的数据与安全边界

1. 默认关闭、空 allowlist、未认证绑定不能采集；Fake Adapter 仅测试注入。真实适配器即使收到伪造 GO 绑定也拒绝读取。
2. UUID 只表示发生实例；跨扫描识别使用内容签名、有序重叠及可信邻接锚点。同文消息保留次数；歧义不硬判新增或无变化。
3. 消息、观测、cursor 同事务提交；分析进度独立。落盘中断可恢复组批，不重读、不重复付费；媒体失败不丢附近文本。
4. 三层过滤保存原因与规则版本；长文本、图片、URL 和上下文引用保守保留。Batch 默认 40 条/12,000 字，未完通知最多补读一次。
5. UserContext 角色区分 confirmed true / confirmed false / unknown；角色排除只作用于对应通知的证据，旧 context 不能单独重建候选。
6. 复用 DeepSeek 设置，显式关闭 Thinking；严格 JSON、引用、码点、时间及角色验证。未知日期不猜，范围端点分别校验。
7. 每日默认最多 30 个 HTTP 尝试、100,000 token、5 个 Vision，分析并发 1。逐次预约/结算；超时结果未知不自动重发，坏 JSON 不付费修复。
8. 图片留本地、本地 OCR 缓存；不足才允许有限 Vision。派生文字独立保存，未取得原图时不花模型 token 猜图。
9. 只进入 Raw Inbox / Review。用户确认后才写正式 Event / Deadline / Task；P0 建议不自动变正式高优先级。新转写形成不可变快照，旧候选不能确认。
10. WorkBuddy / 旧微信 Bridge 自动创建、监听和导出入口停用；旧接口返回 410；历史与独立 JSON/ZIP 导入保留。

No-change 的硬条件是：完整对齐且没有新增的扫描不创建分析 Job，归属该扫描的 LLM/Vision 调用为 0。此前发现的新增消息可以恢复原 Job，不因新的无变化扫描重分析历史。

## 正式数据与迁移

迁移 `010_wechat_collection.sql` 新增 14 张微信采集表并支持 image_vision；001–009 未改写。使用 SQLite backup API，事务中执行并检查外键，失败回滚。

真实 v9 库副本已完成 v10 验收：35 个历史表行数与完整内容指纹一致，foreign_key_check 通过。**正式数据库未升级，仍为 v9；本阶段再次只读核对数量为：**

| raw_item | raw_snapshot | Event | Deadline | Task | Evidence |
|---:|---:|---:|---:|---:|---:|
| 559 | 612 | 9 | 8 | 21 | 80 |

副本记录为 `artifacts/wechat-migration-acceptance/result.json`；本阶段记录为 `artifacts/pyweixin-spike/production-state-after.json`。没有启动使用正式库的新版后端，没有注册登录任务。

## 测试记录

上一次全量后端为 **1002 passed / 25 skipped**，当时微信专项 78 passed。后续增加 Phase 0、pyweixin 和 Qt pass 诊断测试，最新微信专项为 **104 passed / 0 failed / 0 skipped**，记录在 `artifacts/qt-accessibility-combined-tests.xml`；没有把专项增量结果冒充再次执行的全量回归。最初沙箱内启动被 asyncio 的本机回环 socketpair 阻塞，定位后在允许回环的环境重跑正常通过；未改产品逻辑。

前端上次记录：Node 34 passed、生产构建通过、Playwright 桌面和 390px 窄屏通过、静态界面审查 0 errors / 0 warnings。该阶段没有改前端，不重复宣称新跑的浏览器结果。浏览器仅使用合成 Attention Desk 数据。

专项覆盖：100 次无变化零模型、普通聊天零模型、重要候选、角色不符/未知、同文实例、缺失锚点、OCR 缓存/失败、有限 Vision、原图映射、API 结果未知、付费响应落盘恢复、SQLite lock、静默/恢复、旧来源停用、读取拦截、日志脱敏、辅助功能错误恢复，以及静默时不导入 pyweixin。

普通测试禁止真实 UIA/OCR 和模型联网；真实微信矩阵单独显式执行。既有 Starlette/httpx 弃用警告不影响通过数；skip 不算通过。

## 安全实绩与范围

| 项目 | 实际结果 |
|---|---|
| 发送微信消息 | **messages_sent = 0** |
| 聊天输入、发送触发、草稿与群设置修改 | **0** |
| 先前视觉诊断 | 有经授权的本地截图/OCR和有限滚动，持久化截图脱敏；没有上传模型 |
| 最新 pyweixin 阶段 | API 读取失败；正文取得 0、媒体文件 0、截图/OCR 0 |
| 真实 DeepSeek / Vision / 模型 token | **0 / 0 / 0** |
| WorkBuddy 运行监听/调用 | **0** |
| 00:00–06:00 微信/UIA/截图/OCR/Vision/DeepSeek | **全部 0** |
| HKLM 修改 | 明确授权临时写入 **1 项 64 位 DWORD**，已恢复原键/值缺失状态；SPI 与 HKCU Narrator 值也已恢复并独立核对 |
| 正式事项修改、正式库升级、登录任务注册 | **0 / 未执行 / 未执行** |

Phase 0 主动窗口切换是用户扩展的诊断授权，不能用于放松正式运行时的 120 秒空闲、焦点保护、allowlist 和静默规则。pyweixin 仅安装在隔离诊断环境，完整上游对象不暴露给业务层。

## 尚未完成

- 可靠的真实读取 Adapter、字段提取、分页历史覆盖及图片取得；当前不能开启无人值守采集。
- 免打扰/手机已读/无未读、真实活动中止、微信重启后的设备验收。
- 原生电源/微信恢复事件和登录任务的设备验收；目前有时间跳变恢复策略。
- 超长单条通知自动多片、跨群事件合并、已确认事项更新；目前保留原文并人工处理。
- 过滤历史前端恢复、完整日志明细与真实绑定体验。
- 连续 7 天核心群对照试运行。

pyweixin 消息读取仍未通过。最后 Qt accessibility pass 已完成，含获明确授权的 HKLM ForceUIAForQt=1 与微信完整重启；登录后的聊天 Qt 节点仍为 0，当前本机最终建议为 FALLBACK_TO_CUSTOM。未验证 Windows 重启后的效果。历史记录、上游固定源码和局部视觉证据全部保留，本轮不继续扩展大面积 OCR。没有采用解密、Hook、注入、模拟登录、降级微信或其他禁止方案。

## 文件清单

既有实现与诊断代码全部保留。以下为本轮累计工作区文件；`artifacts/` 不提交，保存固定源码、隔离 venv、测试和脱敏诊断记录。

<!-- FILE_INVENTORY -->

| 状态 | 文件 |
|---|---|
| 修改 | [README.md](C:/Users/ericp/Desktop/reference/touxian/README.md) |
| 修改 | [DESIGN.md](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/DESIGN.md) |
| 修改 | [README.md](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/README.md) |
| 修改 | [UX-CONTRACT.md](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/UX-CONTRACT.md) |
| 修改 | [server/documents/service.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/documents/service.py) |
| 修改 | [server/extraction_errors.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/extraction_errors.py) |
| 修改 | [server/extraction_models.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/extraction_models.py) |
| 修改 | [server/extraction_provider.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/extraction_provider.py) |
| 修改 | [server/extraction_service.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/extraction_service.py) |
| 修改 | [server/main.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/main.py) |
| 修改 | [server/official_rules.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/official_rules.py) |
| 修改 | [server/personal_store.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/personal_store.py) |
| 修改 | [server/requirements.lock.txt](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/requirements.lock.txt) |
| 修改 | [server/requirements.txt](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/requirements.txt) |
| 修改 | [server/review_api.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/review_api.py) |
| 修改 | [server/review_store.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/review_store.py) |
| 修改 | [server/source_metadata.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/source_metadata.py) |
| 修改 | [server/test_actions_worker.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/test_actions_worker.py) |
| 修改 | [server/test_inbox.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/test_inbox.py) |
| 修改 | [src/App.jsx](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/src/App.jsx) |
| 修改 | [src/personal/DocumentSources.jsx](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/src/personal/DocumentSources.jsx) |
| 修改 | [src/personal/Evidence.jsx](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/src/personal/Evidence.jsx) |
| 修改 | [src/personal/ExtractionWorkspace.jsx](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/src/personal/ExtractionWorkspace.jsx) |
| 修改 | [src/personal/PersonalWorkspace.jsx](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/src/personal/PersonalWorkspace.jsx) |
| 修改 | [src/personal/ReviewEditor.jsx](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/src/personal/ReviewEditor.jsx) |
| 修改 | [src/personal/reviewHelpers.js](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/src/personal/reviewHelpers.js) |
| 修改 | [start.ps1](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/start.ps1) |
| 新增 | [WECHAT-COLLECTOR.md](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/WECHAT-COLLECTOR.md) |
| 新增 | [WECHAT-IMPLEMENTATION-REPORT.md](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/WECHAT-IMPLEMENTATION-REPORT.md) |
| 新增 | [pytest.ini](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/pytest.ini) |
| 新增 | [scripts/register-desktop-startup.ps1](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/scripts/register-desktop-startup.ps1) |
| 新增 | [scripts/wechat_uia_spike.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/scripts/wechat_uia_spike.py) |
| 新增 | [scripts/wechat_header_spike.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/scripts/wechat_header_spike.py) |
| 新增 | [server/ai_transport.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/ai_transport.py) |
| 新增 | [server/config/wechat-output.schema.json](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/config/wechat-output.schema.json) |
| 新增 | [server/config/wechat.example.yaml](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/config/wechat.example.yaml) |
| 新增 | [server/desktop_entrypoint.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/desktop_entrypoint.py) |
| 新增 | [server/migrations/010_wechat_collection.sql](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/migrations/010_wechat_collection.sql) |
| 新增 | [server/requirements-wechat.lock.txt](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/requirements-wechat.lock.txt) |
| 新增 | [server/tests_wechat/conftest.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/conftest.py) |
| 新增 | [server/tests_wechat/test_batch_followup.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_batch_followup.py) |
| 新增 | [server/tests_wechat/test_capture_recovery.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_capture_recovery.py) |
| 新增 | [server/tests_wechat/test_cost_review.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_cost_review.py) |
| 新增 | [server/tests_wechat/test_foundations.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_foundations.py) |
| 新增 | [server/tests_wechat/test_migration_media.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_migration_media.py) |
| 新增 | [server/tests_wechat/test_pipeline.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_pipeline.py) |
| 新增 | [server/tests_wechat/test_readonly_contract.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_readonly_contract.py) |
| 新增 | [server/tests_wechat/test_spike_privacy.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_spike_privacy.py) |
| 新增 | [server/tests_wechat/test_visual_spike.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_visual_spike.py) |
| 新增 | [server/tests_wechat/test_vision.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_vision.py) |
| 新增 | [server/user_context.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/user_context.py) |
| 新增 | [server/wechat_collect/__init__.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/__init__.py) |
| 新增 | [server/wechat_collect/adapter.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/adapter.py) |
| 新增 | [server/wechat_collect/analysis.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/analysis.py) |
| 新增 | [server/wechat_collect/api.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/api.py) |
| 新增 | [server/wechat_collect/batching.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/batching.py) |
| 新增 | [server/wechat_collect/config.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/config.py) |
| 新增 | [server/wechat_collect/filtering.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/filtering.py) |
| 新增 | [server/wechat_collect/identity.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/identity.py) |
| 新增 | [server/wechat_collect/ingestion.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/ingestion.py) |
| 新增 | [server/wechat_collect/legacy.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/legacy.py) |
| 新增 | [server/wechat_collect/media.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/media.py) |
| 新增 | [server/wechat_collect/models.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/models.py) |
| 新增 | [server/wechat_collect/scheduler.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/scheduler.py) |
| 新增 | [server/wechat_collect/service.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/service.py) |
| 新增 | [server/wechat_collect/spike.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/spike.py) |
| 新增 | [server/wechat_collect/store.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/store.py) |
| 新增 | [server/wechat_collect/vision.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/vision.py) |
| 新增 | [server/wechat_collect/visual_spike.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/visual_spike.py) |
| 新增 | [server/wechat_collect/windows.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/windows.py) |
| 新增 | [server/wechat_collect/worker.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/wechat_collect/worker.py) |
| 新增 | [src/personal/UserContextSettings.jsx](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/src/personal/UserContextSettings.jsx) |
| 新增 | [src/personal/WeChatCandidates.jsx](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/src/personal/WeChatCandidates.jsx) |
| 新增 | [src/personal/WeChatSettings.jsx](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/src/personal/WeChatSettings.jsx) |
| 新增 | [src/personal/wechat.css](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/src/personal/wechat.css) |
| 新增 | [src/personal/wechatHelpers.js](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/src/personal/wechatHelpers.js) |
| 新增 | [tests/wechat-browser.mjs](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/tests/wechat-browser.mjs) |
| 新增 | [tests/wechat.test.mjs](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/tests/wechat.test.mjs) |
| 新增 | [PYWEIXIN-FEASIBILITY.md](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/PYWEIXIN-FEASIBILITY.md) |
| 新增 | [scripts/pyweixin_readonly_spike.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/scripts/pyweixin_readonly_spike.py) |
| 新增 | [scripts/pyweixin_environment_spike.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/scripts/pyweixin_environment_spike.py) |
| 新增 | [scripts/wechat_phase0_probe.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/scripts/wechat_phase0_probe.py) |
| 新增 | [scripts/wechat_phase0_cv.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/scripts/wechat_phase0_cv.py) |
| 新增 | [server/requirements-wechat-spike.lock.txt](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/requirements-wechat-spike.lock.txt) |
| 新增 | [server/tests_wechat/test_pyweixin_spike.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_pyweixin_spike.py) |
| 新增 | [server/tests_wechat/test_pyweixin_environment.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_pyweixin_environment.py) |
| 新增 | [server/tests_wechat/test_phase0_probe.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_phase0_probe.py) |
| 新增 | [server/tests_wechat/test_phase0_cv.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_phase0_cv.py) |
| 新增 | [QT-UIA-ACCESSIBILITY-PASS.md](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/QT-UIA-ACCESSIBILITY-PASS.md) |
| 新增 | [scripts/wechat_qt_accessibility_probe.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/scripts/wechat_qt_accessibility_probe.py) |
| 新增 | [scripts/wechat_qt_accessibility_pass.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/scripts/wechat_qt_accessibility_pass.py) |
| 新增 | [server/tests_wechat/test_qt_accessibility_pass.py](C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk/server/tests_wechat/test_qt_accessibility_pass.py) |

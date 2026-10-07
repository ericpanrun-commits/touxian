# DeepSeek 结构化提取 + 人工确认：实现与验收状态

日期：2026-10-07。**本阶段已完成，包括真实 DeepSeek 调用、预览人工修正、选择参加、正式确认及前后端进程重启后的持久化验收。** 当前运行地址为 http://127.0.0.1:5173/。

## 1. AI Provider

`ExtractionProvider` 提供异步 `extract(snapshot, config, SecretStr)` 协议，返回原始输出、token usage 和实际响应模型。只实现 `DeepSeekProvider`。领域 Schema、引用校验、日期、缓存、参与决定和正式数据库写入均位于 provider 之外。旧评分流程保留用于旧聊天队列，新个人链路不沿用评分输出。

## 2. DeepSeek 配置

页面「提取设置」支持 base_url、model、api_key、timeout、max_output_tokens、thinking。默认 https://api.deepseek.com、deepseek-flash、45 秒、4096 tokens、thinking 关闭。Key 用当前 Windows 用户 DPAPI 加密保存到数据库旁的 ai-settings.json；默认在被 Git 忽略的 desktop/data/attention-desk 下。支持后端环境变量，不使用前端 localStorage，不回传 Key，不打印认证头或远程错误正文。密钥安全存储已用合成测试密钥验证加密、重启解密、留空保留和显式清除。

## 3. Schema

权威定义为 server/extraction_models.py，完整 JSON Schema 可通过 GET /api/personal/extraction-schema 读取，版本 1。顶层 events/evidence；事项包含标题、摘要、分类、受众、参与性质、通知性质、资格、多日期、行动、材料、地点和不确定项。日期含 role、原文、规范值/空、精度、行动引用、依据引用、不确定标记和新旧/取消关系。行动区分 explicit/suggested；引用含准确 quote 和码点 start/end。严格类型且禁止额外字段，risk/importance/urgency/priority 不在 Schema 中。

## 4. Evidence validation

每条引用绑定当前 RawSnapshot，验证原文[start:end]与 quote 完全相同，按 Unicode 码点计且 end 不包含。无效引用、重复编号和不存在的引用会给相关字段警告。用户可编辑引用和位置、查看原句高亮、移除错误引用。正式确认时再次验证当前快照和每个保留引用；错误会回滚整个 Event/Deadline/Task/Evidence 事务，不存入正式依据。

## 5. Preview workflow

先事务保存 RawItem/RawSnapshot，再启动持久分析任务。AI 结果仅为候选；确认前没有正式 Event/Deadline/Task。预览可修改事项、相关性、参与、多个日期、行动来源和日期关联、材料、资格、地点、引用和不确定项。每个保留日期需单独确认，警告需明确核对。支持显式草稿保存、取消和恢复。参加才创建任务；稍后决定保留 needs_review 和行动计划，正式任务入口也受参与状态约束；不参加为 cancelled，未创建任务且不等同 done。延期/取消通知不自动改已有事项。

## 6. AIExtraction 持久化

SQLite migration 002 增加 ai_extraction、extraction_request、raw_review、event_context、event_task_plan。分析保存快照、provider/model、Prompt/Schema 版本、创建/完成时间、原始/解析输出、验证及错误、用量、耗时、thinking、实际配置/响应模型、缓存来源。正式事项引用对应分析记录。相同内容并发合并，持久缓存包含内容 hash、source_time、provider/model、Prompt/Schema、thinking、服务地址和输出上限。主动 force 才生成新版本；幂等请求重试不重复付费。

## 7. 日期逻辑

代码校验真实日期、精度和时区。仅日期保存 YYYY-MM-DD，不补 23:59；分钟保存 YYYY-MM-DDTHH:mm，使用 Asia/Shanghai（UTC+08:00）。按当前本地日期/时刻计算逾期和剩余天数。缺少年份、相对日期缺少可靠 source_time、月日不一致、虚构时刻和模型疑问都需人工核对，不用导入时间推算“明天”。

## 8. Priority 边界

新链路不接受 AI 分数。个人事项最多 P1，仅由已确认待办关联的逾期/未来三天日期触发；其他 P2，完成/取消无等级。旧混合 reducer 保留独立的本地风险、明确行动和公告依据；P0 需要该依据满足条件，远程 risk/action_required 无法独自升级。缺少可信本地依据的历史远程结果也不能触发 P0。判断记录提供 p0_basis。

## 9. 本轮修改文件

本表只列本阶段；工作区另有已验收 Phase 0/1 的未提交变更，不将它们误算成本轮新增功能。

| 范围 | 文件 |
|---|---|
| 新提取后端 | server/ai_settings.py、extraction_errors.py、extraction_models.py、extraction_provider.py、extraction_service.py、extraction_validation.py、personal_priority.py、review_api.py、review_store.py |
| 数据及接入 | server/migrations/002_extraction_review.sql；server/personal_store.py；server/main.py；server/test_personal_events.py 的 migration 版本断言 |
| 回归测试 | server/test_extraction_review.py |
| 新前端组件 | src/personal/AISettingsForm.jsx、EventContext.jsx、ExtractionWorkspace.jsx、RawCollectionForm.jsx、ReviewEditor.jsx、formHelpers.js、reviewHelpers.js |
| 复用组件 | src/personal/Forms.jsx、Evidence.jsx、PersonalWorkspace.jsx、personal.css；src/App.jsx（显式个人页面链接优先于记忆的旧页面） |
| 旧 P0 修复 | desktop/.agents/skills/attention-announcement-triage/scripts/triage_announcements.py |
| 说明与验证配置 | README.md、EXTRACTION.md、DESIGN.md、UX-CONTRACT.md、premium-ui.json、PHASE-2-REPORT.md；仓库 .gitignore（忽略验证截图） |

没有新增生产依赖；未改 Android 或 LICENSE，未 commit/push。详细接口和约束见 EXTRACTION.md。

## 10. 测试结果

- 全套 pytest：**310 passed**（原有 276 + 本轮 34）。一个既有 Starlette/httpx TestClient 弃用提示。
- 用户要求的 12 类案例均覆盖：单日期/行动、多日期/行动、可选参加、无源时间的相对日期、无年份、延期、取消、缺少附件、虚假引用、虚假高风险、超时、截断 JSON。
- 另验证草稿/版本冲突/原文幂等、确认重试、无效引用整笔回滚、延期任务关联、参与后仅创建一次任务、正式入口不能绕过参与选择、重启缓存、并发合并、force 幂等、thinking 配置、DPAPI 和敏感信息遮蔽。
- npm run build：通过。
- 新个人模块严格 UI 审计：0 errors / 0 warnings。
- 官方 @google/design.md lint：0 errors / 0 warnings。
- git diff --check：通过。

## 11. 实际运行验证

使用现有浏览器运行时和已安装 Edge 验证，没有给项目添加 Playwright。用户在本机保存了密钥，后端通过 Windows DPAPI 读取；本次只检查公开的 has_api_key 标志。

**真实输入：**原通知逐字保存为 RawSnapshot `45361356-6620-4eb0-bbe3-900a4e0ff4c8`，没有 source_time：

> 请有意报名大学生创新创业训练计划的同学于
> 10 月 12 日前填写附件 1，并发送到 xxx 邮箱。

该原文有三条分析历史：早期 missing_key 失败记录、真实 Prompt 1 输出、真实 Prompt 2 输出。两次真实调用均为 deepseek-flash、thinking 关闭：

| Prompt | 分析 ID | 耗时 | 用量 | 观察 |
|---|---|---|---|---|
| notice-extraction-1 | 44762e29-c215-4cda-ad7a-74a2ff80fb6e | 3063 ms | 输入 1970 / 输出 356 / 共 2326 tokens | 标题正确，年份不明；填写和发送合为一个行动，引用偏移不匹配 |
| notice-extraction-2 | 79f688f1-18ee-4081-8bc7-df7488ac9d90 | 2764 ms | 输入 2031 / 输出 370 / 共 2401 tokens | 两个独立 explicit 行动，共用同一截止日期；日期引用正确，其余三条引用偏移不匹配 |

第一次真实结果暴露了复合行动问题，因此 Prompt 2 增加通用的逐步骤提取约束，并更新缓存版本；没有写死样例答案。两次真实用量合计 4727 tokens，未估算金额。第二次结果的状态是 needs_review，normalized_date=null，模型没有自行决定年份或参与。

**预览及确认：**在实际页面中选择与我相关、参加，按验收样例人工确认年份 2026，日期保存为 `2026-10-12` / precision=date。逐条核对并修正引用偏移，明确保留“原文未提供年份”“xxx 为占位邮箱”“附件未提供”的核对记录。点击截止日期依据时，原文区域获得焦点并高亮准确的“10 月 12 日前”。保存草稿后正式事项仍为原有 2 件。相同配置再次请求分析返回同一 Prompt 2 分析 ID，没有新增分析或再次付费。

正式确认后新增事项 `d743d32d-e42c-42bd-b64e-5ba0e7a8276b`：

- 标题：大学生创新创业训练计划报名；状态：needs_review；参与：yes。
- 一条日期：2026-10-12，仅日期、Asia/Shanghai、confirmed_by_user=true，无 23:59。
- 两个 todo 任务：填写附件 1、发送到 xxx 邮箱；均为 explicit，均关联该日期。
- 七条正式 Evidence 全部绑定当前快照且准确匹配原文码点范围。
- 来源：deepseek / deepseek-flash / notice-extraction-2 / schema 1 / thinking 关闭。
- 本地 Priority=P2，当前距离日期 5 天；未使用模型评分。

**真实重启：**停止本次拥有的前后端进程，重新运行同一启动脚本；关闭旧浏览器会话，再以新的会话打开该事项。重启前后 Event API 的完整响应相同，原文、日期、两个任务、依据、参与决定和模型来源均保留，AIExtraction 的完整记录也相同。正式事项总数为 3，原有两件事项及状态保留；SQLite foreign_key_check 无违规。重启后再次点击日期依据得到准确高亮，无浏览器 JS 页面错误。

可直接打开[验收事项](http://127.0.0.1:5173/?personal_event=d743d32d-e42c-42bd-b64e-5ba0e7a8276b)。真实验收文件位于工作区外的本地可视化目录：

- 确认前的真实候选及已保存草稿：`deepseek-real-review.png`
- 确认后的事项、日期和两个任务：`deepseek-real-confirmed.png`
- 进程重启后的原文依据高亮：`deepseek-real-restarted-evidence.png`
- 真实验收记录 JSON：`deepseek-real-acceptance.json`

**此前本机验证：**缺 Key 时原文保留和手动回退、非法日期聚焦、草稿刷新、导航保护、取消恢复、旧聊天队列及未配置的官网界面已验证。针对设置入口难以找到，`?personal_settings=1` 直达链接优先于记忆的旧页面；刷新和返回原文均通过验证。

**模拟验证边界：**FixtureProvider / httpx.MockTransport 用于失败、并发和领域合同测试；另一个仅拦截返回值的浏览器测试页面验证 explicit/suggested 区分、默认稍后决定及保存失败后的输入保留。该页面没有把模拟输出或正式事项写入真实数据库。被忽略的 artifacts/ui-simulated-candidate-review.png 仍仅属于模拟 UI 证据，与上述真实调用分开记录。

## 12. 仍然存在的问题

1. 真实模型仍会算错引用位置，第一版 Prompt 也曾合并行动。本次 Prompt 2 返回了两个步骤，但依据和日期仍必须经过严格校验、预览修正及明确确认；原始错误输出保留供追溯。
2. 样例没有年份、真实邮箱或附件文件。2026 是本次人工验收选择，并非原文事实；邮箱和附件缺失已保留为核对记录。
3. 延期和取消只形成可审阅的新候选，不自动关联、覆盖或取消旧事项，符合本轮范围。

本阶段完成并停止；未进入官网抓取或其他扩展阶段。

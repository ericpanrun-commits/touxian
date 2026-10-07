# DeepSeek 候选提取与人工确认

本阶段仅扩展个人工作台。输入为手动粘贴文字；不新增官网、OCR、手机端、Calendar、Windows 微信读取或其他 provider。

## 本地配置

运行 `./start.ps1` 后，在个人工作台的「提取设置」填写配置。默认 base URL `https://api.deepseek.com`、model `deepseek-flash`、timeout 45 秒、max_output_tokens 4096、thinking 关闭；六项均可配置。

配置保存在数据库旁的 `ai-settings.json`，默认路径为 `desktop/data/attention-desk/ai-settings.json`。密钥以当前 Windows 用户绑定的 DPAPI 密文保存；配置 API 只返回 `has_api_key` 和来源，不返回密文或明文。留空保留密钥，清除操作显式选择。也支持后端进程环境变量 `ATTENTION_DEEPSEEK_API_KEY` / `DEEPSEEK_API_KEY`，已保存的密钥优先。文件和数据库目录被 Git 忽略。换 Windows 用户或机器时应重新填写密钥。

只有主动分析时，当前原文和可靠原消息时间发送到配置的 HTTPS 服务。密钥不进入前端源码、浏览器 localStorage、数据库分析记录或日志。应用仍只监听本机回环地址。

## Provider 与严格 Schema

`ExtractionProvider` 定义 `extract(snapshot, config, SecretStr) -> ProviderResult`。DeepSeek HTTP 实现仅负责调用及返回文本、用量和响应模型；领域验证、缓存、日期、参与选择和数据库写入由独立模块控制。

`ExtractionOutput` 是唯一权威 Schema（extra=forbid、严格类型）。`GET /api/personal/extraction-schema` 返回完整 JSON Schema 和版本，当前为 `2`。Prompt 版本为 `notice-extraction-4`，明确要求把可独立勾选的并列/顺序动作拆成各个步骤。历史提取记录保留自己的版本。

文件解析后，同一 RawItem 的新快照保存正文和文件来源块。模型输入使用 `source_blocks`，每条 Evidence 引用 `block_id` 和块内偏移；文件 ID、页码、段落及 OCR 区域由代码从已保存块推导，模型不能指定。长文最多选择 80 块、24,000 字，优先日期、行动和资格线索；全文保存在本机，遗漏范围显示在预览。OCR、正文/附件日期冲突及未解析文件均提示人工核对。模型失败不影响本地文件、文字或手动整理。图片字节不会发送到 DeepSeek。详见 [文件解析契约](DOCUMENTS.md)。

顶层是 `events[]` 和 `evidence[]`。每个 event 包含 id、title、summary、category、audience、participation（required/optional/unclear）、notice_kind（new/update/cancellation/information）、eligibility、deadlines、actions、materials、locations、uncertainties。事实用 `{text,evidence_ids}`。日期保留 id、role、original_text、normalized_date/null、precision（date/minute/unknown）、related_action_ids、evidence_ids、uncertain、relation（current/previous/cancelled）。行动包含 id、title、kind（explicit/suggested）、evidence_ids。引用包含 id、quote、start、end；偏移按 Unicode 码点计，end 不包含。Schema 不接受 risk、importance、urgency 或 priority。

采用 DeepSeek JSON object 模式，并在 Prompt 放入 Schema 和形状示例。API 的 JSON 模式不代替本地 Schema 校验。thinking 参数始终显式发送 enabled/disabled，默认 disabled。依据：[JSON 模式](https://api-docs.deepseek.com/guides/json_mode/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion)。

## 保存与审阅边界

1. `POST /raw` 在事务中保存 Source、RawItem、不可变 RawSnapshot、待整理状态。没有 Event/Deadline/Task。
2. `POST /raw/{id}/analyze` 立即保存 AIExtraction 版本并返回后台任务；页面轮询状态。关闭页面不中断任务；关闭应用标记中断，不自动再次付费。
3. 严格解析 JSON，拒绝重复键、非有限值、围栏、未知字段、错类型和截断。引用必须满足当前快照 `original_text[start:end] == quote`，无效引用的相关字段标为待核对。
4. 预览可以修改多个候选、相关性、参与、日期、行动来源、行动关联日期、材料、地点、资格、依据和不确定项。可以显式保存草稿，取消后可恢复。逐项确认日期并核对警告后才能提交。
5. `POST /raw/{id}/confirm` 校验版本和当前分析身份，事务写入选中的正式 Event、Deadline、Evidence、UserDecision、来源上下文和行动计划。无效引用或关联失败会回滚整个确认；同内容重复确认返回已有事项。
6. 参加才创建 Task；稍后决定保持 needs_review 和行动计划，稍后可主动创建；不参加或无关保存 cancelled，未创建任务，不等同完成。取消通知不会创建行动任务，也不自动取消其他事项。延期保留旧日期为候选，默认只选新日期，不自动覆盖已有事项。

旧 `POST /collect` 保留给 Phase 1 人工显式创建的兼容 API；新页面始终走 raw→review→confirm。

## 日期和 Priority

日期合法性由代码校验。date 仅保存 YYYY-MM-DD，不补时分；minute 为 YYYY-MM-DDTHH:mm，按 Asia/Shanghai（UTC+08:00）计算。date 在本地当天不显示为逾期，次日起逾期；minute 按实际时刻。倒计时每天重新计算。

缺少年份、相对日期缺少可靠 source_time、月日不一致或凭空添加时刻都标为需人工核对。不会把导入时间当成原消息时间。每个保留日期必须显式确认。

个人事项的 Priority 不接受 AI 分数；仅使用用户确认的待办和日期，最多 P1（待办关联已逾期或未来三天日期），其他 P2，终态无等级。旧聊天混合 reducer 的 P0 现在还要求独立保留的本地明确行动、公告和风险/逾期依据；远程 risk/action_required 无法独自触发 P0。

## 故障与付费缓存

无密钥、401、403、402、429、网络错误、超时、服务错误、空结果、JSON/Schema 错误或截断均保留原文及分析失败记录，允许手动整理或主动重分析。429/502/503 最多自动重试一次；超时不自动重复付费。API 错误体和请求认证头不输出到日志或 UI。

持久缓存键包含 snapshot hash、source_time、provider、model、Prompt/Schema 版本、thinking、base URL 和输出上限。同内容并发请求合并；不同原文记录复用输出时重新绑定其自己的快照依据。失败也缓存，需主动新版本重试。force 操作有明确 UI 确认；分析请求的 UUID 幂等键避免连接中断后的同请求重复付费。

AIExtraction 保存快照、provider/model、Prompt/Schema、创建/完成时间、原始/解析输出、验证状态和警告、错误码/信息、token usage、latency、thinking、实际请求配置/响应模型、缓存来源。正式事项可回溯对应记录。

## 验证

`server/test_extraction_review.py` 覆盖请求中的十二类案例、正式记录前置边界、Unicode 引用、事务回滚、多个日期关联、参与选择、重启持久化、缓存/并发/force、DPAPI、错误重试上限和 P0 门控。运行：

```powershell
$env:ATTENTION_INBOX_POLL='0'
../.venv/Scripts/python.exe -m pytest -q
npm run build
```

2026-10-07 已使用用户在本地配置的 Key 完成真实 DeepSeek 原文→候选→预览修正→参加→确认→进程重启验收。Prompt 2 实际返回两个独立 explicit 行动；人工确认样例年份为 2026 并修正引用偏移。重启前后事项、日期、任务、依据和分析来源一致，缓存返回同一分析记录。完整记录及实际模型局限见 PHASE-2-REPORT.md；MockTransport / FixtureProvider 另用于合同及故障测试。

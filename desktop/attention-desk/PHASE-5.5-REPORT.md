# Phase 5.5：AI / 长文档提取可靠性

## 1. 长 PDF 原始失败根因（修改前记录，2026-10-09）

以当前源码和真实数据库为依据；Git 基线为 `b67e441a2126862fcafe7a8b7ae1e84bb4728a55`，与 `phase5-mobile-collector-stable` 一致。请求中所贴 hash 有笔误，不用它推断代码状态。

- `extraction_messages` 将原文或 `select_model_blocks` 选出的最多 24,000 字/80 个来源块一次发送。它没有独立 chunk extraction / merge。
- `AIConfig.max_output_tokens` 默认 4096，可在既有提取设置配置为 256–32768；本机当前仍为 4096、deepseek-flash、thinking off。不会直接把全局配置提高到巨大值。
- 真实失败 PDF 的不可变解析快照保存 26 个 PDF 页块、12,528 字。那次请求的 prompt_tokens=13011、completion_tokens=4096、total_tokens=17107，记录 error_code=truncated；原件、全文和 Review 保留。
- Provider 已检查 `finish_reason=length`，也拒绝非 stop、空输出、非法 JSON/schema；但 service 把这些统一记为整份 failed。没有截断后的缩小范围重试，没有部分结果和 chunk 缓存。
- 原 parser 的 2500 字切片按字符硬切，保留页码却不是语义提取分块；长文选择只按关键词/日期排名，缺少邻近上下文和逐区域覆盖记录。
- Provider 对 429/502/503 最多两次 HTTP 尝试；timeout 不自动再次调用。原缓存只针对整份 snapshot/config，改一页会让整份缓存失效。
- 现有 Evidence 代码逐字校验 block/start/end，并保留 PDF 页、DOCX section 和 OCR box；这应继续作为最终事实校验边界。分析从不直接创建正式 Event。

DeepSeek 官方 [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) 将 length 定义为输出或上下文上限导致的不完整输出；[JSON Output](https://api-docs.deepseek.com/guides/json_mode/) 也要求合理设置 max_tokens 以避免中途截断。本次真实记录的 completion_tokens 与配置上限一致，证明本例触及输出上限；不能把所有非法 JSON 都断言为相同原因。

## 2. 新分块策略

完整 RawSnapshot、原件、解析块和 OCR 坐标继续保留。超过 6000 字、超过 3 个 PDF 页或超过 80 个来源块进入分块路径；其他通知保留原有单次调用。80 块判断防止大量短 DOCX 表格行落回旧的有损输入选择。

每个块按来源类型、文件名、页/section、内容和原始范围生成稳定内容 ID。附件 UUID 不作为跨版本内容身份。原有 block_id 仍是 Evidence 的最终锚点。长粘贴文本只新增一个派生 TEXT_BODY_001 来源块，不改写原始快照。

全文超过 24,000 字时，本地规则选择日期/行动/资格等线索及相邻块；无匹配时保留全部候选。所有未选区域记录在计划里，用户可主动继续分析；筛选不删除原文。

## 3. 大小和边界

主区域目标上限 1800 个 Unicode 字符，保留 PDF 页、DOCX 段落/表格位置和附件边界，优先换行/段落，再选语句或空白边界；只有无可用边界的长串才按硬上限切开。分界范围无缝覆盖原文。

主区域带有限邻近上下文（约 350 字），同一原块内也保留边界重叠。实际请求可能比 1800 字略长。缩小重试移除额外邻近上下文，最深两层；小于等于 450 字不继续拆分。原 parser 的 2500 字存储切片也改用相同的无损语义边界；不重写历史来源块。

## 4. AI 调用策略

短文一次提取；长文逐区域提取，再由确定性代码合并，不额外调用合并模型。每份任务串行处理区域，既有服务级并发上限为 2。保留 provider abstraction、deepseek-flash、thinking off 和全局默认输出上限 4096。没有更换模型或修改本机提取配置。

每次尝试记录来源范围、input hash、模型、配置、父块、原因、时间、响应与用量。JSON/schema/依据未通过的响应只保留为诊断记录，不进入成功缓存。

## 5. Merge 策略

按规范化后的具体事项标题和分类保守分组；同一行动、日期角色/规范日期/精度/关系分别去重并合并全部依据。保留不同用途的多个截止时间及行动关联，不选取一个“最终日期”。跨块关联只在用途与行动标签精确匹配时补齐。

明确延期语句、两个日期、匹配的角色或行动，以及属于该变更语句的引用共同成立时，将旧日期标 previous、新日期标 current；不同章节中相同月日不会继承无关延期。无法确定时保留全部 current 候选并提示冲突。不会自动覆盖既有正式事项。

合并后再次通过严格 ConsolidatedOutput 和 Evidence 校验。主预览最多 32 个事项，每事项每类 100 个候选、2000 条依据；触及候选上限明确标 partial，完整候选仍保存在区域记录。依据容量超过上限时停止合并并保留区域结果。

## 6. 截断检测

检查 finish_reason=length、其他非 stop、completion_tokens 达到配置上限、JSON EOF/未闭合字符串、重复键/非有限值/非法 JSON、required fields 和严格 schema。EOF 标 truncated；完整但不符合 schema 的响应标 invalid_schema，不能把所有 schema 错误断言为 token 截断。

新增 analysis_state：running、validated、partial、truncated、failed、cancelled。兼容现有 Collector 的 legacy status 保留。validated 表示提取与依据校验完成，不表示用户已经确认。整个分析路径不创建正式 Event/Deadline/Task。

## 7. 有限恢复与 Retry

截断、非法 JSON 或 schema 优先缩小输入，再分析子区域。不能再拆的小区域最多三次提取尝试；默认 4096 时，必要的最后一次可临时提高至最多 8192，不改全局设置。timeout、空输出或无效依据等最多两次。鉴权、余额、连接和不可用等明确故障停止后续区域，成功结果保留。

429/502/503 的 provider 内部 HTTP 重试也计入同一预算。失败、截断响应的已知 token 用量计入任务汇总；取消时无法确定的请求结果仍占保守预算，不假定免费或未发送。所有恢复受整份任务的调用/token 上限约束。

另有无需模型调用的有限本地恢复：只检查同一确切输入/配置最近六条 invalid_evidence 旧响应；必须重新通过完整 JSON/schema、逐字引用和重要字段校验，仅唯一空白差异可恢复日期原句。记录原尝试 ID 和人工核对警告，不接受截断 JSON。

## 8. 部分成功与取消

逐区域结果即时写入 SQLite。失败、预算耗尽、未覆盖、候选超限或取消时，成功区域合并为可核对预览，显示不完整范围。取消不删除 RawSnapshot、附件、成功区域或缓存。

重试只付费分析缓存未命中的区域；“分析尚未覆盖区域”保留既有成功区域并扩展覆盖。不会自动进入下一轮。实际进度、缓存复用、失败数、区域记录和具体候选可在桌面查看。区域记录按 20 条展开并可加载更多。

退出应用后可恢复已持久化的成功区域供核对；中断任务不会自动重新付费。模拟断网读取失败时预览保留，恢复连接后可继续读取。

## 9. Evidence 合并与定位

引用统一绑定原始 block_id 和块内 Unicode 偏移。请求内切片坐标由代码映射回完整来源；仅当原句逐字存在且唯一时可纠正模型给错的偏移，并保留警告。合并后再次逐字校验，PDF 页、DOCX section、OCR words/bounding box 由代码从原块推导。

日期 original_text 的唯一空白/换行差异可恢复为完整原文片段；不改写字词、不推断年份、不修复不匹配的 Evidence quote。含糊或多处匹配仍失败。真实 PDF 此类恢复留有原始响应和来源尝试记录。

正式确认继续由现有事务验证原句、引用、选中日期与行动关联，失败整体回滚。新增测试实际通过该确认合同创建 50 个日期和 50 个任务，来源页码完整；只操作临时测试数据库。

## 10. 缓存

精确 key 包含 source snapshot hash、chunk hash、provider/model、Prompt/Schema、thinking、base URL、输出设置及解释上下文。第二层内容复用 key 允许未变化页跨快照复用，重新绑定当前来源 ID 并校验。改变 source_time、模型或提取设置会失效。

邻近上下文也是输入的一部分，改变一页通常只影响该页及最多相邻两页；测试验证其余至少七页复用。相同服务内并发相同块共享请求。服务按现有单 worker 启动，不宣称支持多个独立 worker 的跨进程请求锁。

幂等 key 只依赖不可变请求输入，不受缓存覆盖变化影响。成功结果及缓存存于 SQLite，重启保留。

## 11. 调用预算

| 单轮限额 | 常规 | 明确确认后 |
| --- | ---: | ---: |
| 新主区域 | 8 | 32 |
| HTTP 尝试总数（含重试/子块） | 12 | 48 |
| 保守 token 上界 | 240,000 | 1,000,000 |

超过常规新区域数量先要求确认，不发送模型请求。缓存区域可一起合并，不占新付费区域数。token 预算按请求消息 UTF-8 字节数加输出上限保守预留，并计入实际重试次数；这是防失控上限，不是服务商收费估算。达到上限停止，继续下一轮必须明确发起。

## 12. 修改文件

所有路径相对于本应用目录，共 25 个文件。

| 范围 | 文件 |
| --- | --- |
| 解析边界 | server/documents/parsers.py；server/documents/text_boundaries.py |
| 计划、运行、合并 | server/extraction_chunks.py；server/extraction_long.py；server/extraction_merge.py |
| Provider、错误、Schema、校验 | server/extraction_provider.py；server/extraction_errors.py；server/extraction_models.py；server/extraction_validation.py |
| 既有服务与 Store/API | server/extraction_service.py；server/personal_store.py；server/review_store.py；server/review_api.py |
| 追加迁移 | server/migrations/005_long_extraction.sql |
| 桌面进度与恢复 | src/personal/ExtractionWorkspace.jsx；src/personal/AnalysisProgress.jsx；src/personal/analysisState.js |
| 测试 | server/test_long_extraction.py；server/test_extraction_review.py；server/test_personal_events.py；tests/analysis-progress.test.mjs |
| 文档 | README.md；EXTRACTION.md；UX-CONTRACT.md；PHASE-5.5-REPORT.md |

未修改 Mobile Collector pairing、手机页面、权限、网络检测或防火墙；Desktop API 仍只监听 127.0.0.1。没有新增来源、Calendar 或通知系统。

## 13. 新增测试

新增 36 项 Python 案例和 5 项前端案例。Python PDF 测试使用真实本地 PDF 生成/解析，模型和 HTTP 故障为确定性 fixture/MockTransport；不能将其称为真实 DeepSeek 验收。

| 要求 | 覆盖 |
| --- | --- |
| 1 短通知 | 一次调用、既有缓存；4,415 字官网通知保持单次路径 |
| 2、3 十页/五十页 PDF | 真实 parser、分轮覆盖、50 个日期/行动及页码 |
| 4 大量无关页 | 重要页及邻近页筛选，遗漏可追溯 |
| 5、6 多日期/跨块 | 不同角色保留、精确行动重连、引用去重 |
| 7、8 延期/冲突 | previous/current、冲突保留、不继承其他章节延期 |
| 9 单块截断 | 缩小恢复和有限失败，原文保留 |
| 10 timeout | 有限次数、不丢其他成功区域 |
| 11 部分成功 | 20 区域中 18 成功、2 失败，预览保留 |
| 12 malformed JSON | 非法 JSON、重复键、缺字段和 EOF 分类 |
| 13 finish_reason=length | 截断拒绝及输出上限判断 |
| 14 重跑缓存 | 不新增付费请求、并发共享、幂等复用 |
| 15 单页变化 | 当前来源 ID 重绑、仅该页及邻近输入失效 |
| 16 取消 | 成功区域保留、重试复用 |
| 17 DeepSeek 不可用 | 停止后续调用、原文保留 |
| 18 Evidence 定位 | 逐字坐标、PDF/DOCX/OCR 元数据与正式确认事务 |

额外覆盖模型/thinking/输出设置导致缓存失效、各种 HTTP 二次失败的真实请求计数、失败响应 token 统计、唯一空白恢复及歧义拒绝、旧响应本地恢复、迁移/中断重启、大量短 DOCX 行的预算门控、含 Unicode 的长粘贴文本来源与跨轮坐标缓存。

## 14. 完整验证结果

以下为最后生产代码与新增用例的完整复跑结果；没有仅用专项测试代替全量验证。

| 检查 | 结果 |
| --- | --- |
| 完整 Python pytest（含原有 Collector/security/SSRF/文件测试） | 576 passed、0 failed、1 条既有弃用提示；140.63 秒 |
| 前端 npm run test:frontend | 16 passed、0 failed |
| npm run build | 通过，最终正常启动 build 1.94 秒 |
| 严格 UI audit | 0 errors / warnings / violations |
| 浏览器交互 | 进度、取消后保留、重试确认、预算确认、窄窗口、区域候选、原文高亮、断网重读、恢复连接通过；0 page errors |
| Git diff --check | 通过；既有 CRLF 规范化提示不影响校验 |
| SQLite | quick_check=ok；foreign_key_check=0 |
| Git/私有数据审计 | 402 个已跟踪及非忽略候选文件；实际 Key/DPAPI、上传原件、手机原文、文档/OCR 匹配 0；405 个私有路径/忽略示例无失败 |

本机 Browser plugin 不可用，浏览器验证使用已有 Playwright 和 Microsoft Edge；不冒称 Safari/iPhone 测试。只有隔离预算/异步状态采用浏览器路由 fixture；真实 PDF 预览、区域 API、页图和逐字高亮直接连接真实本地后端。

Python 仍有一条既有 Starlette/httpx TestClient 弃用提示。静态审计不替代运行检查。详细 JSON、截图和数据库备份保存在仓库外，未加入 Git。

## 15. 真实长 PDF 结果

对当前曾失败的 26 页、12,528 字 PDF 使用现有 DeepSeek 配置重跑，原件和旧失败记录保留。初次分块运行得到 25/26 完整区域，另一区域出现原句空白/引用校验错误，明确显示 partial；后续修复与复用没有隐藏这次结果。

最终结果：

- 26/26 区域完成，无失败、无未覆盖区域；analysis_state=validated，review status=needs_review。
- 11 个事项候选、5 个相对时间约束、21 个行动候选（18 explicit、3 suggested）。
- 54 条依据全部逐字通过校验，覆盖 15 个实际来源页，包括修复区域的第 12 页。
- 最后一轮 25 个缓存区域、1 个旧响应本地恢复，0 次新模型请求、0 新付费 token 用量。
- 本轮三次受控真实分析合计 37 次 HTTP 请求；有回执响应合计 prompt_tokens=125403、completion_tokens=21927、total_tokens=147330。首次旧整份失败的 17107 token 不含在本轮合计内。
- 浏览器实际打开第 12 页图像并核对原句高亮与区域引用完全一致，0 page errors。
- 完成真实工作台进程重启，Review、完整 AIExtraction 和全部 26 条区域记录的 SHA-256 前后一致；重启后真实预览、区域记录、PDF 页图和高亮再次通过。
- 与修改前备份比对：RawItem、RawSnapshot、原附件/文件、来源块及全部既有 Event/Deadline/Task/Evidence/上下文/行动计划逐行及文件 hash 未变；仅增加可靠性迁移和分析记录。没有替用户创建正式事项。

## 16. 剩余限制与交付状态

这份 PDF 属于规定类长文档。5 个时间约束全部缺少转为具体日历日期的必要参照，normalized_date 仍为 null；需要用户依据学期周次、考试安排或成绩公布时间核对。界面保留 invalid_date/uncertain_year/model_uncertain 等警告，没有拿导入日期或当前年份硬算。行动候选需人工决定是否适用、是否执行；不会自动创建正式 Task。

确定性合并使用保守标题/分类匹配，语义相近但用词不同的事项可能仍需人工合并；相同通用标题也需核对范围。相关性筛选不证明全文没有其他事项，未覆盖范围始终可见。超额候选可从区域记录查阅并人工整理，不承诺自动处理任意 500 页文档或无须核对的完美语义提取。

实现、自动化和真实 PDF 技术链路已就绪；候选内容和时间解释仍由用户核对。此报告完成时未自动 commit/tag/push。用户随后明确授权收尾 Phase 5.5 并进入 Phase 6；对应本地提交与标签验证见 [checkpoint 记录](PHASE-5.5-CHECKPOINT.md)，不 push。

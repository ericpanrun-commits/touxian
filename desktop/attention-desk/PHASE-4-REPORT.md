# Phase 4：附件、文件、截图与图片解析完成报告

2026-10-07，Windows Desktop。统一 RawItem / RawSnapshot → Extraction → Preview → User Review → Event / Deadline / Task / Evidence 已接通并完成真实验收。没有新增另一套 AI 或事项系统。本报告只覆盖 Phase 4；不进入下一阶段。

工作台已运行：[打开截图验收事项](http://127.0.0.1:5173/?personal_event=7d9cfda8-c6bc-4f66-860d-c2dfe359b613)，右上角「收集通知」可选择/拖入文件；[打开真实 PDF 验收事项](http://127.0.0.1:5173/?personal_event=7a65ec9a-59d5-4713-a1d7-3a7328dadbcc)。

## 1. 支持哪些文件格式

PDF、DOCX、TXT、PNG、JPG/JPEG，以及上述格式的截图。两条入口共用原有链路：官网通知中用户点击「下载并解析」；「收集通知」中选择文件、拖入文件/图片或粘贴/拖入文字。支持同一次收集正文加多份文件。原有手动粘贴与官网来源保留。

官网链接的文件类型既可由文件名后缀识别，也可由 URL 后缀识别；没有后缀的官网下载名可在确切受支持 MIME 下识别，随后仍校验实际字节。DOC、XLS、ZIP、可执行文件等不作为本阶段支持格式执行或解析。

## 2. 使用哪些 parser

- 文本 PDF：pypdf 6.19.0，逐页提取。
- PDF 渲染：pypdfium2 5.14.0，生成受控 PNG；不执行 PDF 脚本或显示活动内容。
- DOCX：python-docx 1.2.0，读取正文中的段落、标题、表格单元格顺序及位置。
- TXT：Python 严格 UTF-8 / UTF-8 BOM，失败后尝试 GB18030。
- 图片读取、方向和尺寸检查：Pillow 12.3.0。

依赖已加入运行时锁定文件和普通 requirements。启动脚本按原有方式安装，不另设应用。

## 3. OCR 实现

默认使用本机 Windows.Media.Ocr，当前机器已安装并实际使用 zh-Hans-CN。原生 WinRT helper 需要 Windows PowerShell 5 的 .NET Framework 运行时；其余 Windows 命令使用 PowerShell 7。

扫描 PDF 检测到缺少有效文字层后逐页栅格化、逐页 OCR，保存 page、scanned_pdf / needs_ocr 和原页参考。纯空白页单独记录。长截图分片识别，重叠区按位置归属，避免整张缩小而丢失文字。原生 OCR 不提供置信度，保存 confidence=null、质量提示和 needs_review；所有 OCR 来源进入人工核对。

识别文字、原始 OCR 转写和文字区域坐标均保存。仅对识别/预览副本处理 EXIF 方向；原文件不变。图片字节没有发送到云端。API 说明见 [Microsoft OCR 文档](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine)。

## 4. 文件保存位置

默认数据库：`desktop/data/attention-desk/personal.sqlite3`（相对于仓库根目录）。

原文件：数据库同目录 `files/<SHA前两位>/<SHA256>.<扩展名>`。按哈希写入，先完成写入和同步，再发布文件；不会覆盖已引用的原文件。原文件记录禁止删除，没有清理原文件的 API。数据库、原文件、工作缓存均被 Git 忽略。

本轮迁移前备份：同目录 `personal-before-phase4.sqlite3`，Schema 3。备份整个工作台时须同时保存数据库和 files 目录；运行中的数据库使用 SQLite backup API。

## 5. Attachment schema

Schema 4 增加 attachment、snapshot_attachment、raw_source_block、snapshot_document、attachment_job。

Attachment 记录 id、raw_item_id、origin_snapshot_id、filename、original_url、local_path、MIME、extension、size、sha256、downloaded_at、parse_status/error、extracted_text、extractor/version、解析清单、needs_review 和文件版本。状态区分 not_downloaded、downloaded、parsing、parsed、unsupported、failed。

成功解析的版本不可修改；相同 URL 内容哈希改变会建立新文件版本。正文与精确文件版本形成同一 RawItem 的新不可变 Snapshot。旧 Snapshot、来源块、附件关联及正式 Evidence 保留。相同文件名的两份手动文件不会互相覆盖或被静默丢弃。

迁移前已有 96 个 RawItem、138 个 Snapshot、4 个 Event、5 个 Deadline、8 个 Task、28 条 Evidence。按旧列逐行比较，这些旧记录以及旧 AI/context/decision 记录均未改变；新列只扩展来源信息。

## 6. PDF Evidence

文本 PDF 保存逐页文本块，每条引用指向具体 block_id、page 和文件 ID。扫描 PDF 的引用类型是 image_ocr，同时保留 PDF 页码和原文件 ID。点击依据可显示对应原页 PNG、文字区域及准确 OCR 引用；原 PDF 可下载。

真实学校 PDF 第 1 页的「5月31日前」已核对原页，最终保存为 2026-05-31，仅日期，不补时分。多页相同引用位置不会被错误去重。不存在的块、页引用和不匹配的引用不能保存为正式 Evidence。

## 7. DOCX Evidence

保留段落序号、Heading 信息以及表格序号/行/列。引用可显示「表格 1 · 行 1 · 列 2」等具体位置，同时关联原文件。自动化测试完成 DOCX 段落、标题、表格截止日期到正式 Event/Deadline/Task/Evidence 的保存。

浏览器联合输入也实际验证了 DOCX 表格、TXT 和正文在同一 RawItem 中解析，冲突来源点击后显示准确表格内容。本阶段不重现 Word 完整版式。

## 8. Image Evidence

PNG/JPEG OCR 结果保存行文本、原始转写、码点位置及原图坐标。引用区域按相关词坐标合并，随图片缩放。Preview 和正式事项共用 OriginalNotice，显示原图、选中 OCR 行、准确引用与高亮区域。

实际测试截图保留了 OCR 将「邮箱」误识别为「由阝箱」的结果，原图中可以直接核对；候选行动可编辑，原 OCR 与原图保持不可变。没有伪造置信度或静默把 OCR 结果当作确定事实。

## 9. Chunking

本地提取全文，文本块最多 2,500 字；PDF 块保留页码，DOCX 块保留段落/表格位置，OCR 行独立成块。提取最多 100 万字、PDF 最多 200 页。

模型输入最多选择 80 块、24,000 字，按日期、截止、行动、提交、报名和资格线索优先选择，再恢复原顺序。未选块仍在本机，预览显示选中/总数及遗漏范围，要求核对。200 页上下文预算测试通过；没有将所有页无边界发送给模型。

## 10. DeepSeek 如何读取附件内容

仍使用现有 ExtractionService、DeepSeekProvider、缓存、Preview 和确认 API。文件解析不调用模型，不创建正式事项。用户主动分析时发送 source_blocks 和文件状态元数据，图片原始字节不上传。短文件全部选入；长文使用上述预算。

Evidence 必须指定具体 block_id 和块内 Unicode 码点偏移。附件 ID、来源类型、页码、段落和区域由代码从已保存块推导。模型不能自行指定这些定位信息。严格 Schema、日期和引用验证保留；API 失败时文件及本地全文仍在，用户可以手动整理。

当前 Prompt 为 notice-extraction-4，输出 Schema 2。历史提取记录保留原有版本，缓存包含文档来源与输入范围。

## 11. 冲突处理

正文与附件对同类截止要求给出不同日期时，代码保存 date_conflict，Preview 列出两处日期及可点击来源。模型也被要求保留不确定项。用户核对所保留日期，并确认警告后，才写正式记录。

实际界面验证了正文 2026-10-15、TXT / DOCX 表格 2026-10-18 的冲突。两份文件均保留，没有静默选择。此验收示例未创建正式事项，取消整理后原文和文件仍在。

## 12. 安全限制

官网下载只接受已有附件 ID；从官网配置取得精确 allowed_hosts。校验 HTTP(S)、公开 DNS、每次重定向的地址；最多 3 次重定向，有连接/读取超时与 60 秒流下载时间检查。限制 MIME、扩展名和实际魔数，拒绝压缩 HTTP 响应；HTML/登录/验证码页面不当作文档。即使命中已解析文件的哈希缓存，也重新检查本次下载 MIME。

15 MB 以上须确认；50 MB 单文件、60 MB 上传请求为硬上限，前端合计 59 MB 为 multipart 留空间。上传无 Content-Length 时也逐块限制，在 multipart 解析前阻止超过总上限的数据。

DOCX：2,000 条 ZIP、60 MB 展开总量、20 MB 单条目、250 倍压缩比；拒绝宏/可执行条目和异常路径。图片最多 3,000 万像素，PDF 最多 200 页，文本最多 100 万字。最多两份文件并行解析，隔离子进程 768 MiB 内存上限、每文件 180 秒、原生 OCR 每片 30 秒。退出结束自己的解析子进程，重启把中断工作标为可重试失败。

原文件下载提供 attachment disposition；PDF 预览仅提供受控 PNG，不执行附件、宏、脚本或表单动作。损坏、类型错误和解析失败保留已下载/上传原文件；有可用正文时可带未解析警告继续整理。

## 13. 修改 / 新增文件

应用目录：`desktop/attention-desk`（相对于仓库根目录）。以下是本阶段范围；实现验收时保留了既有 Phase 1–3 未提交文件，稳定状态随后按用户要求统一提交并创建标签。

- 新增：server/documents 下的 __init__.py、policy.py、parsers.py、windows_ocr.ps1、worker.py、resources.py、blocks.py、storage.py、downloader.py、service.py、api.py。
- 新增：server/migrations/004_document_sources.sql、server/test_documents.py、src/personal/DocumentSources.jsx、DOCUMENTS.md、本报告。
- 修改共享后端：main.py、personal_models/store.py、extraction_models/provider/service/validation.py、review_store.py、source_metadata.py、official_store/parsers.py。
- 修改依赖与既有版本断言：requirements.txt、requirements.lock.txt、test_personal_events.py、test_extraction_review.py。
- 修改共享前端：src/personal 的 api.js、RawCollectionForm、ReviewEditor、reviewHelpers、Forms、Evidence、ExtractionWorkspace、PersonalWorkspace、personal.css。
- 更新维护文档：README.md、EXTRACTION.md、DESIGN.md、UX-CONTRACT.md。

## 14. 测试结果

最终 pytest：**399 passed**（Phase 3 基线 368，新增 31 个参数化执行案例）；1 条既有 Starlette/httpx 测试客户端弃用提示。npm run build 通过，严格 UI 审计 0 错误/警告，DESIGN.md lint 0 错误/警告，git diff --check 通过（仅既有换行提示）。

| 要求案例 | 结果 |
|---|---|
| 1 文本 PDF 单日期；2 多页多日期 | 通过，含正式页码 Evidence |
| 3 扫描 PDF | 通过，实际 Windows OCR |
| 4 DOCX 段落；5 DOCX 表格日期 | 通过，保留结构位置 |
| 6 截图 OCR；7 低质量 OCR | 通过，实际 OCR、区域和强制核对 |
| 8 正文 + PDF 联合分析 | 通过，同一 RawItem/source_blocks |
| 9 正文 / 附件冲突 | 通过，代码验证与实际浏览器提示 |
| 10 同名同 URL 内容改变 | 通过，新哈希版本，旧 Evidence 不变 |
| 11 超大文件 | 通过，含 300 MB 声明和无长度流限制 |
| 12 错 MIME | 通过，含已解析哈希缓存的重新下载 |
| 13 损坏 PDF / DOCX | 通过，保留原文件 |
| 14 不存在的文件 Evidence | 通过，正式写入回滚 |
| 15 API 不可用 | 通过，本地全文和文件保留 |

附加覆盖：200 页模型预算、DOCX 压缩炸弹、重定向越界、同名两份文件、相同偏移的不同 PDF 页、上传后台解析、重启恢复、中断工作错误、失败附件仍可整理正文、官网 TXT/图片链接及无后缀下载名。

浏览器使用现有 Playwright runtime 和已安装 Microsoft Edge，原因是 Browser plugin not available。1440×1000 与 390×844 验证：页面有内容、无框架错误覆盖层、无运行时/控制台错误、无窄屏横向溢出；文件选择与拖放、预览确认、引用焦点、高亮、对话框安全焦点/Escape、官网入口和手动原文均通过。发现并修复了清空原生 file input 时未及时复制 FileList 导致偶发文件丢失的问题。

## 15. 真实附件验收

真实通知：[肇庆学院未来卓越教师“砚园班”2026年招生通知](https://www.zqu.edu.cn/info/1361/140441.htm)，由已验收的官网采集链路保存，原 Snapshot `50d00398-1e13-488d-b29e-2f02b8119608`。本阶段复用该实际 RawItem，点击官网附件解析后生成新 Snapshot，未另造通知或独立 Event 系统。

实际 PDF：`731250954A732CD223EE9237F72_EABBF9EF_12D2A7.pdf`，1,233,575 字节，2 页扫描件，逐页本地 OCR 后得到 45 个文件块及 1 个正文块。文件 SHA-256：`b01feca6090782fbf457de3e65ebfd57b88047544f896cf37c45553d70579098`。

| 流程 | 真实 PDF | 测试截图 |
|---|---|---|
| 新解析 Snapshot | f772e214-1943-47a6-ac35-cebc3d8297df | bf5f601e-5e74-4d7c-b50b-595647716c9d |
| 原文件保存 | 官网实际下载 | 实际拖入 test-screenshot.png |
| 解析 | 2 页 PDF OCR，46 来源块 | 本地 OCR，5 来源块 |
| 实际 DeepSeek | c1fc1537-ae35-4a34-a46e-ea512d4f35f3 | aecb12e2-18dd-4235-97b4-242c80c07792 |
| 模型 / 耗时 / tokens | deepseek-flash / 9,203 ms / 11,033 | deepseek-flash / 3,953 ms / 3,554 |
| 返回状态 | needs_review | needs_review |
| 界面核对 | 修正引用偏移、核对原页，拆分报名/填写行动 | 修正引用偏移、核对日期和 OCR 误字 |
| 界面确认后 Event | 7a65ec9a-59d5-4713-a1d7-3a7328dadbcc | 7d9cfda8-c6bc-4f66-860d-c2dfe359b613 |
| 正式日期 / 任务 / Evidence | 1 / 5 / 22；日期 2026-05-31 | 1 / 2 / 9；日期 2026-10-18 |
| 关闭并重启 API + Vite | 通过 | 通过 |
| 新浏览器会话查看原页/原图 | 通过 | 通过 |
| 重启前后完整 Event API 逐字段比较 | 完全一致 | 完全一致 |
| 下载原文件大小 / SHA-256 检查 | 匹配 | 匹配 |

截图原文件 71,143 字节，SHA-256：`20ebe89ca18b77cf52d3907069483a8b7a7c60e531ac67248ecbbb6301484b47`。原截图 1400×800，重启后仍显示准确引用区域。

这些是真实 API 调用和浏览器确认；测试用模拟 provider/network 的案例不算入这两次模型验收。AI 原始输出与校验警告保留，未将人工修正伪装为模型一次生成正确。验收专用事项标题均带「链路验收」。

证据保存在本机 Codex 可视化目录（未提交到 Git）：phase4-real-extractions.json、phase4-final-before-restart.json、phase4-after-restart.json、phase4-file-integrity.json、phase4-migration-preservation.json、phase4-ui-audit.json，以及 phase4-collect-drop.png、phase4-date-conflict.png、phase4-docx-table-source.png、phase4-pdf-after-restart.png、phase4-screen-after-restart.png、phase4-narrow-source.png、phase4-official-regression.png。

## 16. 仍未解决的问题

- OCR 会误识别字、标点和邮箱，Windows OCR 没有置信度；须对照原图确认。复杂表格图片只能保留 OCR 文本/区域，不保证理解完整布局。
- 模型可能逐字引用正确而偏移错误。实际 PDF 出现 14 条位置不匹配，截图出现 1 条；均在 Preview 中修正，错误引用不能直接正式保存。人工核对仍是必要步骤。
- 该真实通知的一个 DOCX 下载端点实际返回 HTML 页面，本轮安全拒绝，没有绕过网站限制。该文件的链接及错误保留；需要时可手动下载真实 DOCX 后收集。真实验收由可下载的学校 PDF 完成，DOCX parser 另有实际本地文件及表格验证。
- 本阶段图片/扫描件 OCR 仅支持 Windows。超出页数、尺寸、展开内容、文本或解析时间限制的文件需要拆分；原文件仍保留。
- 没有实现云端 Vision 增强，不显示无效的 Vision 操作。现有路径为本地 OCR 后文本分析。

18 项完成标准均已验证：文本 PDF、PDF 页引用、扫描 OCR、DOCX 段落和表格、图片 OCR、原文件保留、版本、联合提取、引用校验、冲突确认、DeepSeek 失败保留、文件拖入、真实附件和图片验收、重启、官网及手动输入回归。上述限制已明确显示或记录，无未完成的本阶段必需工作。工作台继续本地运行；Phase 4 在此停止。

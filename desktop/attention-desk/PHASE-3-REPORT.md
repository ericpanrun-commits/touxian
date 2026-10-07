# Phase 3：肇庆学院官网采集完成报告

验收日期：2026-10-07，Windows Desktop。五个来源已接入同一份 SQLite RawItem / RawSnapshot / DeepSeek / Review / Event 工作流。当前采集 91 条真实公开通知；五个来源均检查正常。所有来源已恢复启用，默认每 12 小时检查。应用当前运行于 [官网来源](http://127.0.0.1:5173/?official_sources=1)。

下文区分实际联网和真实模型验收、公开 HTML 固定样本测试、模拟失败与模拟正文修改。未扩展到下一阶段。

## 1. 实际接入的来源

校级通知公告、教务部课程通知、机械与汽车工程学院教学通知、创新创业学院通知公告、学生工作部学工通知，共五个。

车辆工程学院归属先通过学校[机构设置](https://www.zqu.edu.cn/jgsz.htm)确认机械与汽车工程学院的真实站点，再核对其[车辆工程专业页面](https://jxqc.zqu.edu.cn/bksjy/zyjs/clgc.htm)。没有根据学院名称猜域名。只参考 [School Watcher](https://github.com/jintairezhao/school-watcher) 的 HTTP 优先、配置、URL、快照和失败处理思想，没有复制其代码。

## 2. 每个来源的 URL

| 来源 ID | 来源 | 实际列表 URL |
| --- | --- | --- |
| zqu-school | 校级通知公告 | [校级列表](https://www.zqu.edu.cn/index/tzgg.htm) |
| zqu-jwc | 教务部课程通知 | [课程通知](https://jwc.zqu.edu.cn/jwgl2/kctz.htm) |
| zqu-jxqc | 机械与汽车工程学院教学通知 | [学院教学通知](https://jxqc.zqu.edu.cn/index/jxgz.htm) |
| zqu-iec | 创新创业学院通知公告 | [创新创业通知](https://iec.zqu.edu.cn/tzgg.htm) |
| zqu-xsc | 学生工作部学工通知 | [学工通知](https://xsc.zqu.edu.cn/xwgg/xgtz.htm) |

## 3. Parser 与分页

| 模式 | 实际列表 selector | 标题 / 发布日期 | 正文 |
| --- | --- | --- | --- |
| vsb_school | `li[id^="line_"] a[href*="info/"]` | `.arc-tit h1` / `.arc-tit .arc-info` | `.v_news_content`，备选 `#vsb_content` |
| vsb_div | `.btlb-tm a[href*="info/"]` | `#article-title` / `#article-info` | 同上 |
| vsb_table | `tr a[href*="info/"]` | `#article-title` / `#article-info` | 同上 |

学校用第一种，教务部用第二种，其余三种来源用第三种。列表日期读取专用日期单元格，避免把标题中的日期当发布时间。只接受已验证的 `/info/栏目/文章.htm` 链接和明确“下页/下一页”链接。已实访学校 `/index/tzgg/62.htm`、教务部 `/jwgl2/kctz/9.htm`、创新创业 `/tzgg/4.htm`、学生工作 `/xwgg/xgtz/74.htm` 分页。学院教学列表当前只有 11 条，没有下一页。列表按发布日期排列后取最多 20 条，每次最多三页。

## 4. 是否需要 JavaScript

五个列表及普通文章都能通过 HTTP 读取，不执行 JavaScript。没有给项目增加 Playwright 或浏览器采集依赖。

部分文章只有图片或嵌入 PDF：只读取 HTML 中的图片链接，以及已实际观察到的 VSB PDF 调用中的静态 URL 字面量作为未解析元数据；不执行脚本、不识别图片、不下载或解析 PDF。浏览器仅用于本地界面验收。Browser 插件不可用，因此复用现有运行时中的 Playwright Core 和已安装 Edge，项目依赖未增加。

## 5. Source schema

原 `source` 保留 id / kind / name / created_at。`official_source` 作为同一数据库的来源扩展保存配置 JSON/hash、enabled、polling_interval、status、last_checked_at、last_success_at、next_check_at、error、failure_count，以及发现、新快照、变更、未变和请求计数。

配置包含 id、name、school、department、category、list_url、allowed_hosts、parser_type、enabled、polling_interval、max_items、max_pages。默认五个已验证来源、12 小时。来源启停和检查结果持久化；来源变更由配置驱动，不依赖 sites.json。原温大适配器保留为示例，应用默认不实例化它。

## 6. RawItem / RawSnapshot 数据流

公开列表 → 先建立 RawItem 和来源关联 → HTTP 正文 → 静态正文解析 → 事务保存 RawSnapshot / 网页元数据 / raw_review → 统一待核对队列 → 用户显式分析或手动整理 → 编辑预览 → 确认 → 现有 Event / Deadline / Task / Evidence。

RawItem 保存 source_id、kind（API 同时提供 source_type）、external_id、canonical_url、original_url、title、published_at、discovered_at，以及抓取错误和尝试时间。正文失败也保留通知身份。RawSnapshot 保存完整 HTML 可见文本和 SHA-256；网页扩展保存抓取时间、网页标题、日期、URL、附件/图片元数据、正文状态、清理后的正文 HTML、版本号和版本 hash。列表中的 160 字预览不会截断数据库原文。

采集不写正式事项，不调用模型。原 HTML 不渲染到工作台、不进入系统指令；API 去除 body_html。HTTP 限制公开 DNS、精确域名、HTTP/HTTPS、三个重定向、2 MB HTML、12 秒请求超时；逐跳检查，禁用环境代理，不访问本地文件或附件内容。

## 7. 去重策略

canonical URL 去掉 fragment 和常见跟踪参数、规范域名/默认端口/查询参数，并通过唯一索引全局去重。URL alias 处理合法重定向；多个来源通过 official_item_source 关联到同一个 RawItem。相同版本复用 Snapshot，不新增 Review，不自动创建 Event。确认请求继续使用原有幂等与版本检查。

不同 URL 不做语义合并。同一 URL 的新版本可单独核对，不会自动修改已有事项。最近窗口之外的旧文章不会全站重抓。网站支持 ETag / Last-Modified 时使用条件请求，否则对取得的正文和元数据比对 hash。

## 8. Snapshot versioning

正文、标题、发布时间、附件/图片元数据或正文状态变化会创建新版本；RawSnapshot 及网页元数据都有数据库不可修改约束。旧 Event/Evidence 始终引用确认时的版本。

本轮开发验收补齐了 VSB 正文旁的附件清单，因此已保存的 42 篇通知新增了包含补全元数据的版本；这属于解析能力修正，不代表学校修改了 42 篇正文。旧版本保留。另补存一篇 PDF 正文页面的标题及文件元数据。最终官网历史 Snapshot 共 133 份，对应 91 个 RawItem；稳定重复检查新增版本为 0。界面展示版本号，待核对数量按 Snapshot 计，可能大于通知数量。

## 9. Attachment 处理

当前最新版本共记录 125 个附件链接，保存 filename、URL、extension、MIME、status=unparsed，与不可变 Snapshot 关联。包括正文内链接、VSB 正文旁附件清单和静态嵌入 PDF URL。没有下载附件，也没有 PDF/DOCX/OCR 解析。

最新 91 个版本中，86 篇有 HTML 可读正文，4 篇只有图片，1 篇正文在嵌入文件中。后五篇保存页面标题及未解析元数据并明确提示，不声称已读取图片/PDF 正文。模型上下文同样标记未解析状态，不允许猜测附件内容。

## 10. AI 触发方式

官网检查默认只采集原文。用户在共享预览中点击“使用 DeepSeek 分析”才调用已配置 Provider；支持继续手动整理或直接忽略。未确认结果不建立正式 Event/Task，模型认为不相关也不删除 RawItem。

沿用 DeepSeek Provider、严格 schema 1、确定性校验、失败记录、缓存和用户确认。Prompt 升至 notice-extraction-3，新增网页元数据的不可信输入边界、发布时间不等于 Deadline、附件/图片未解析约束。缓存包含正文、来源解释上下文、模型配置、prompt/schema 版本。无变化检查不调用 AI；手动分析实际验证了返回同一 extraction_id 的缓存复用。

本轮两次真实请求：官网考试通知 deepseek-flash，5,328 ms、3,826 tokens；手动回归通知 deepseek-flash，2,407 tokens。模型返回 needs_review；引用位置和不确定时间必须核对，没有绕过校验。Key 使用已配置的本机加密存储，没有打印、改写或提交到仓库。

## 11. Review Queue 如何工作

官网来源页只管理来源，按钮进入统一 RawInbox。`review_source` 保存来源筛选；手动与官网原文共用同一页面、Preview、Evidence、草稿、忽略/恢复及确认逻辑。共享 OriginalNotice 展示原文版本、独立发布时间、抓取时间、官网链接和未解析附件。

真实官网验收使用学院专业课期末考试通知：原文 Snapshot `83608287-c3ec-4948-a6db-58c92f24d066`，Extraction `9a3f0b98-84ea-4ed9-a226-0eda1221cd28`。在界面核对 14 处引用字段的位置，去掉无确切日期的两个进场节点，保留日期精度的考试开始/结束日期，并明确相关性、参与决定和不确定项。保存草稿、刷新核对，再点击确认。

生成 [学院期末考试安排 · 官网链路验收](http://127.0.0.1:5173/?personal_event=f96628b1-c178-4bc8-942c-911a17377549)：2 个日期、3 个任务、16 条精确 Evidence。它是历史通知的链路验收事项，未将它描述为当前待报名活动。原模型输出与人工编辑分别保留。手动回归原文分析后取消整理，未另建正式事项。

## 12. 本轮修改文件

以下路径相对于 desktop/attention-desk，仅列本阶段触及内容；前阶段已有但尚未提交的代码保留。

- server/main.py、official_monitor.py：切换默认来源服务，保留旧示例，提供来源启停接口。
- server/personal_store.py、review_store.py、review_api.py：迁移到 schema 3，保存共享原文，暴露来源筛选和网页元数据。
- server/extraction_models.py、extraction_provider.py、extraction_service.py、extraction_validation.py：Prompt 3、来源上下文、缓存和未解析提醒。
- server/test_personal_events.py：迁移版本断言。
- src/App.jsx、OfficialMonitor.jsx、official.css：来源管理及通往共享队列的导航。
- src/personal/ExtractionWorkspace.jsx、Evidence.jsx：来源筛选、忽略入口、原文与附件展示。
- README.md、DESIGN.md、UX-CONTRACT.md、premium-ui.json：使用说明、交互契约及审查范围。

## 13. 本轮新增文件

- server/config/official_sources.json。
- server/migrations/003_official_sources.sql。
- server/official_sources.py、official_http.py、official_parsers.py、official_store.py、official_service.py、source_metadata.py。
- server/conftest.py、test_official_sources.py；src/personal/OfficialSources.jsx（原入口保留转出，共享工作台模块及实际目录审查范围）。
- server/fixtures/official/：14 份真实公开 DOM 摘录及 manifest.json，包含 URL、捕获记录说明、原页面与测试文件 SHA-256。
- PHASE-3-REPORT.md。

公开抓取缓存、SQLite、Key 和验收 JSON/截图没有加入源码提交；本轮没有 commit/push，也未修改 Android。

## 14. 测试结果

`ATTENTION_INBOX_POLL=0 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`：368 passed，1 条原有 Starlette/httpx 弃用提示。本轮新增 58 项参数化结果；测试不调用远程模型。

`npm run build` 通过；strict UI audit 为 0 errors / 0 warnings；`npx --yes -p @google/design.md designmd lint DESIGN.md` 为 0 errors / 0 warnings；git diff --check 通过（仅换行转换提示）。

| 必需日期/版本 Case | 真实公开样本与验证 |
| --- | --- |
| 1 一个明确截止日期 | 创新大赛初赛通知 4495，明确提交日期 2026-03-30；完整原文先存，模拟 Provider 只生成候选，确认后才写正式记录 |
| 2 多阶段日期 | 学院毕业论文通知 5990，10-10 / 10-16 / 10-23 / 10-30，四阶段与行动关联 |
| 3 发布日期与 Deadline 不同 | 创新大赛通知发布 2026-01-26，提交日期 2026-03-30；额外验证标题含日期也不污染专用发布日期 |
| 4 附件未解析 | 同一大赛通知的 PDF/XLS/DOCX；另验证学院 11 个附件和校级正文旁附件 |
| 5 无 Deadline | 校级项目立项名单 4625，签发/发布日期 2026-06-05，没有虚构截止日期 |
| 6 延期 | 教务通知 9515，原申请期到 9-11，特定补考不及格重修可延期至 9-30；条件保留、旧日期不作为当前日期保存 |
| 7 正文变化 | 用真实学院通知正文在测试中把 10-23 改为 10-24；新 Snapshot、旧 Event/Evidence 不变。明确为模拟变化，未改学校网站 |
| 8 多栏目重复 | 5990 实际同时出现在学院首页和教学栏目；一个 RawItem / Snapshot，增加来源关联，不重复建 Event |

另外验证非法 URL、非公开 DNS、重定向、MIME、响应大小、超时、304、来源故障隔离、发现后正文失败仍留 RawItem、重定向 alias、来源启停持久化、定时到期判断、无 Key/配置时共享手动流程、忽略/恢复，以及原文和元数据的不可变性。

实际浏览器检查覆盖加载、未配置、读取失败/重试、写入失败保留状态、空筛选、键盘焦点、dialog Escape/恢复焦点、1024px 桌面宽度无水平溢出、来源筛选链接、草稿刷新、共享 Evidence 高亮、手动分析、旧群聊页面与重启。错误/空页面状态使用明确标识的本地响应模拟；页面没有 JS 异常。

## 15. 实际抓取与重启结果

实际联网总计 91 个官网 RawItem、133 份历史网页 Snapshot、125 个当前附件链接，canonical URL 重复数 0。HTML 可读正文保留完整文本，最长 6,570 个字符，超过旧 1,200 字摘要界限。

首次采集结束时 AIExtraction 仍为原来的 3 条，Event 仍为原来的 3 个；采集没有调用 AI 或创建事项。解析附件清单补全后，全来源稳定重复检查：新增 RawItem=0，新增 Snapshot=0，91 篇均未变化。加上本轮两次显式真实模型验收后，最终数据库共有 RawItem 96、RawSnapshot 138、AIExtraction 5、Event 4、Deadline 5、Task 8、Evidence 28。

实际关闭前后端和旧浏览器会话，使用同一数据库重新启动并打开全新浏览器。官网验收 Event 的完整 API 响应相同；2 个日期、3 个任务、16 条 Evidence、原文、AI provenance 及高亮均保留。五个来源的完整持久字段相同，测试停用的学工来源仍停用，随后恢复启用。所有表计数相同，SQLite integrity_check=ok。重启后再检查学院 11 篇，新增/变更均为 0，没有新增 AI 记录。

与迁移前 SQLite backup 对比：原有 3 个 Event、3 个 Deadline、5 个 Task、12 条 Evidence、4 个 RawItem / RawSnapshot、2 条 raw_review、3 条 AIExtraction 的原字段逐条不变。

验收文件保存在本机 Codex 可视化目录（未提交到 Git）：phase3-first-fetch.json、phase3-final-acceptance.json、phase3-unchanged-check.json、phase3-real-ai-before-review.json、phase3-manual-real-deepseek.json、phase3-confirmed-event-before-restart.json、phase3-restart-proof.json、phase3-restart-unchanged-check.json、phase3-ui-state-tests.json、phase3-ui-audit.json，以及对应界面截图。它们分别记录真实结果与模拟 UI 状态，不混作联网证据。

## 16. 每个来源成功 / 失败状态

| 来源 | 实际保存通知 | 当前附件链接 | 最终状态 | 稳定重复检查 |
| --- | ---: | ---: | --- | --- |
| 校级通知公告 | 20 | 30 | 正常 | 20 未变 |
| 教务部课程通知 | 20 | 30 | 正常 | 20 未变 |
| 机械与汽车工程学院教学通知 | 11 | 27 | 正常 | 11 未变；重启后再次检查仍 11 未变 |
| 创新创业学院通知公告 | 20 | 28 | 正常 | 20 未变 |
| 学生工作部学工通知 | 20 | 10 | 正常 | 20 未变 |

真实首次检查时，校级通知中的砚园班招生通知 140441 只有 PDF 嵌入，没有 HTML 正文，因此来源显示部分未取得，其余四个来源照常完成。核实其真实页面后支持静态嵌入 URL 与正文旁附件元数据，明确标记文件未解析，复查后该来源恢复正常。这是真实的单来源问题隔离与恢复记录；另外用 MockTransport 测试一个来源连接失败时其他来源继续，不把模拟称为网站实际故障。

来源失败保留 last_success 与原文，记录错误、失败次数、下次检查时间，按至少 6 小时且不短于配置周期的间隔退避。退避最高四天；若配置周期更长，仍遵守更长周期。应用启动、手动输入和已有事项不依赖来源联网成功。

## 17. 尚未解决的问题与范围

- 附件、正文图片和嵌入 PDF 未解析；后五篇不能从标题推断真实正文或期限。
- 每次仅最近 20 条、最多三页；窗口之外的历史修改和特别大的更新量可能不能及时覆盖。没有全校抓取或自动来源发现。
- 页面结构未来变化会报来源错误，需更新已验证 parser；不同 URL 的语义重复与变更通知对已有事项的自动合并未实现。
- DeepSeek 真实输出的码点位置仍可能错误，本次已实际遇到并通过预览修正；相对时间、日期范围及资格条件仍需人工核对。没有对所有官网通知自动付费分析。
- 历史 Snapshot 保留且可独立核对，待核对版本数量可多于 RawItem 数量；没有删除旧版本或自动取消旧预览。
- 定时到期和退避有测试，现场验证了启动/手动检查及默认自动检查线程；没有等待完整 12 小时做长时间运行观测。
- 原有测试客户端的弃用提示保留，没有为此更换基础依赖。

本轮停止于公开官网来源进入统一 RawItem Pipeline。没有 OCR、手机端、Calendar、文件内容解析、自动报名、自动邮件或表单提交。

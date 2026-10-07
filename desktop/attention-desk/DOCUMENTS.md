# 文件与截图解析契约

支持 PDF、DOCX、TXT（UTF-8 / GB18030）、PNG、JPG/JPEG。Windows 原生 `Windows.Media.Ocr` 负责中文 OCR，无云端图片上传。请在 Windows 安装中文文字识别语言；无 OCR 服务时原文件保留并提示手动整理/重试。跨平台可提取文本 PDF、DOCX 和 TXT，本阶段图片与扫描件 OCR 仅支持 Windows。

入口只有现有「收集通知」和官网通知的「下载并解析」。原文件保存在数据库旁 `files/<SHA前两位>/<SHA>.<扩展名>`，数据库默认 `desktop/data/attention-desk/personal.sqlite3`。备份必须包含数据库及 files 目录。`document-work` 保存受控解析输出和预览缓存，不代替原文件备份。

Schema 4 增加 `attachment`、`snapshot_attachment`、`raw_source_block`、`snapshot_document`、`attachment_job`。Attachment 记录原始链接、文件名、路径、格式、大小、哈希、下载时间、状态、错误、全文、解析器版本及页/段落/OCR 清单。状态为 not_downloaded/downloaded/parsing/parsed/unsupported/failed。解析成功的版本禁止修改；原文件记录禁止删除。重新下载哈希相同可复用，不同则创建新版本。旧 Event 的 Snapshot、文件关联和块不可变。

PDF 用 pypdf 逐页提取，缺少有效文字层时 pypdfium2 栅格化再逐页 OCR；空白页单独标记。DOCX 用 python-docx 按正文顺序读取段落、标题和表格单元格，并保存段落编号/表格行列。图片永久保存原图，OCR 保存识别文字、原始转写和原图坐标；EXIF 方向正规化仅作用于识别/预览副本。Windows OCR 不返回置信度，所有 OCR 块必须人工核对。

每份组合快照属于原有 RawItem，保存正文块和精确文件版本。每块最多 2,500 字（OCR 行独立成块）；全部提取文字保存在本机。DeepSeek 只接收最多 80 块、24,000 字，按日期、行动、资格关键词排序选择并恢复原顺序。长文省略范围提示人工核对。Evidence 引用 block_id 与块内 Unicode 码点偏移；附件 ID、类型、页码、段落和区域由服务器推导并精确验证。缺失块、伪造引用和错误位置不能进入正式记录。

正文与附件对同类截止要求给出不同月日/年份时，保存明确冲突及两处来源。模型也须输出不确定项。Review 显示两处引用，用户核对所保留日期并确认警告，才创建 Event/Deadline/Task。仅有文件名而尚无有效解析的手动输入不能分析或确认。文件解析、官网采集均不创建正式事项或调用模型。

官网下载只接受已保存的附件 ID，从可信来源配置读取精确 allowed_hosts。HTTP(S)、公开 DNS、每次重定向重新验证、最多 3 次重定向、60 秒总下载上限、单次连接/读取超时、无压缩 HTTP 响应、MIME/扩展名/魔数检查。登录页/验证码网页不会作为 DOCX/PDF 解析。不要绕过网站限制；可以手动下载真实文件再收集。

15 MB 以上须用户确认，50 MB 单文件硬上限，60 MB 上传请求总上限，前端文件合计上限 59 MB 为 multipart 留空间。原文件先落盘；不支持或损坏文件仍保留。DOCX 最多 2,000 个 ZIP 条目、60 MB 展开总量、20 MB 单条目、250 倍压缩比，拒绝宏/可执行条目及异常路径。图片最多 3,000 万像素，PDF 最多 200 页，提取最多 100 万字。Windows 解析进程设 768 MiB 内存限制，单文件 180 秒，原生 OCR 单图块 30 秒；最多两份文件同时解析。预览 PDF 只提供受控 PNG，原文件作为下载提供；不执行 PDF 脚本、宏或用户脚本。

进程退出会结束自己的解析子进程；下次启动将中断任务标为 failed，保留原文件，等待显式重试。DeepSeek 不可用时全文和原文件仍在本机，既有手动确认流程仍可用。Schema/Prompt 分别为 2 / notice-extraction-4，历史提取记录不重写。

API：POST `/collect-files`（multipart），POST `/raw/{snapshot}/documents`，GET `/document-jobs/{id}`，GET `/attachments/{id}/original`，GET `/attachments/{id}/image?page=N`，均在 `/api/personal` 下。最终分析、草稿、确认仍用已有 `/raw/{snapshot}/analyze|draft|confirm`。

实现位于 `server/documents/`，共享 Review/Evidence 位于 `src/personal/`。自动化案例见 `server/test_documents.py`；真实模型验收、重启证明及限制见 `PHASE-4-REPORT.md`。

原生 OCR API：[Microsoft 文档](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine)。PDF：[pypdf 文本提取说明](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)、[pypdfium2 API](https://pypdfium2.readthedocs.io/en/stable/python_api.html)。

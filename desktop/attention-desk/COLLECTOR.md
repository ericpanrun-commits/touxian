# 手机收集

打开正在运行的本地工作台中的「个人工作台 → 手机收集」。也可以使用 `?personal_collector=1` 直达该页。工作台重启后，收集器默认关闭。

## 当前验收状态

2026-10-08 已恢复实际共同 Wi-Fi 验收。用户手动设置 Private 后启用了受限 LAN 接收，真实手机已发送文字、截图和 PDF；原文件及 OCR/解析结果已核对。PDF 的模型输出截断，文件和正文仍保存。用户确认 Code Scanner 内置浏览器可用，但切换 Safari 后会话失效，因此 Safari 配对项需在新引导流程下重测。没有自动修改 Windows 网络类型或防火墙。续测已确认学校 URL（543 字正文、DeepSeek needs_review）、普通重复提交去重以及 Safari 无痕错误 token 拒绝；旧未确认邀请轮换、完整 Safari 持久会话、正式确认和重启验收仍待完成。

## 使用步骤

1. 让电脑与 iPhone 连接同一个可信 Wi-Fi。访客隔离、AP 隔离或不同子网可能使设备无法互访。
2. 在电脑检查 Windows 的网络配置为 **Private / 专用网络**。收集器拒绝 Public、DomainAuthenticated 或无法确认类型的网络；它不会修改配置。
3. 在「手机收集」选择物理网络接口，确认端口（默认 8473）。点击启用时重新检测当前地址；保存的偏好只有接口 ID 与端口，没有 IP。
4. 若 Windows 防火墙阻止访问，手动检查“允许应用通过专用网络防火墙”。如需入站规则，限定可信 Private 网络和本地子网；不要开放 Public 网络。界面只读检查，显示“未确认”不表示已放行，也不证明一定被阻止。
5. 准备拿起手机时点击「生成新二维码」，再用 iPhone 相机扫描二维码，在 Safari 打开配对引导页，再点击「完成配对」。打开/刷新仅验证，不消耗 token；二维码五分钟有效，在确认 POST 中原子消费一次。若从 iOS Code Scanner「扫码器」进入，请先通过菜单在 Safari 打开，再完成配对；引导页在确认前保留配对 URL，方便此切换。

批准后签发独立的七天 HttpOnly/SameSite=Strict 持久 cookie。刷新、关闭和重开同一个 Safari 不需再次扫码，只要会话未到期且电脑 Collector 持续开启、地址不变。凭证不进入 localStorage/sessionStorage；成功配对后才移除 URL token。扫码器与 Safari 不共享会话，无法可靠转移已经批准的扫码器 session；如果已经在扫码器点了确认，请在电脑生成新二维码，在 Safari 重新确认，原设备会话仍保留。
6. 选择文字、公开链接、截图或文件，填写可选来源信息，发送。手机显示实际上传及处理状态。
7. 在电脑「我的事项」点击「刷新原文列表」，选择来源「来自手机」，打开待确认预览。检查 OCR、日期、参与决定与依据，然后明确确认。只有这一步创建正式事项。

可在 Safari 分享菜单选择「添加到主屏幕」。未实现离线 PWA 或云端暂存；电脑关闭时首次打开地址会得到 Safari 的连接失败页面。已打开的收集页能显示电脑不可连接，并保留内存中的输入供恢复连接后重试。

## 地址变化、停止与重新配对

物理网卡/IPv4/Private profile 每二十秒重新检查；检测到变化就停止旧 listener 并撤销凭证。重新启用时检测当前 LAN，生成新的 public_base_url；准备扫码时再点击「生成新二维码」。启用本身不创建邀请。实际变化的发现还受只读网络探测耗时影响。不会自动迁移旧手机会话。

「生成新二维码」只轮换待确认 token，已批准设备继续有效。「撤销已配对设备」需要明确确认，撤销所有设备会话与未使用邀请，不自动生成新二维码。会话授权在电脑进程内，停止、重启或网络变化后需重新配对。「停止手机收集」立即撤销凭证并关闭入口。已经保存到电脑的原文、原文件和已接收后台任务仍保留。桌面应用关闭时中断任务按既有流程保留为可重试失败；重启后不会自动继续付费模型调用。

不要公开分享二维码。当前方案通过可信 LAN 上的 HTTP 传输，配对与内容没有传输加密；它仅授予有限的新内容提交与自身回执读取权限。它不适合不可信共享网络或公网暴露。

## 独立服务与有限 API

桌面前端和 API 继续精确绑定 `127.0.0.1`。收集器使用独立 FastAPI/Uvicorn 实例和独立端口，精确绑定所选物理 IPv4，没有 `0.0.0.0`。桌面控制接口 `/api/personal/collector` 仅接受本机和允许的 localhost Host/Origin。

| LAN 路由 | 方法 | 能力 |
|---|---|---|
| `/pair`、`/collect`、固定 icon/manifest、mobile 构建依赖 | GET | 轻量手机页面；其他静态资源不开放 |
| `/collector-config` | GET | 当前受限 LAN public_base_url，无凭证或桌面设置 |
| `/api/pair/validate` | POST JSON | 仅验证引导页 token，不消费、不签发 cookie |
| `/api/pair/confirm` | POST JSON | 用户明确确认后原子消费 token，签发受限持久 cookie |
| `/api/session` | GET | 当前会话 CSRF 值与新的提交 UUID |
| `/api/text` | POST JSON | 文字原文 |
| `/api/url` | POST JSON | 公开网页 URL |
| `/api/file` | POST multipart | 文件与可选补充正文 |
| `/api/submissions/{id}` | GET | 只读当前会话自己的回执，无原文、Raw ID 或文件路径 |

JSON 需 `submission_id`，正文为 `text` 或 `url`；可选 `source_name`、`source_time`、`again`。文件使用重复的 `files` 字段，附加同名文本字段；超过 15 MB 的文件需 `confirm_large=true`。已配对写请求使用 `X-Collector-CSRF`，浏览器自动携带 cookie。当前 LAN Origin/Host 必须准确匹配；跨来源浏览器请求拒绝。保留简单 POST 形状供未来快捷指令接入，本阶段未实现快捷指令。

## 输入、安全与保留

PNG/JPG/JPEG、PDF/DOCX/TXT 复用 Phase 4：文件名归一、扩展名/MIME/签名、图像与 DOCX 解压资源检查、SHA-256、内容寻址原文件、限额解析进程与 Windows 本地 OCR。HEIC 当前明确拒绝，需转换为 JPEG 或提交截图。

上传最多十文件，单文件 50 MB，超过 15 MB 确认；请求体 60 MB。手机预检文件合计 59 MB 留出 multipart 开销。文件接收有两个并行名额和九十秒超时。JSON 450 KB，正文十万字。无 Content-Length 的上传同样按累计实际字节拦截。身份与来源先于请求体读取验证。

每 IP 每分钟最多 12 次写入、100 次 API 读取；全入口每分钟最多 24 次写入。非消费的 token 验证按读取计数，仍执行 Host/Origin/JSON/体积与并发限制；配对确认按写入计数。最多 32 个未到期设备会话，达到上限时先显式撤销设备，拒绝不会消费待确认 token。最多八条整理任务，有限回执/请求映射和一小时回执保留。客户端双击保护、相同 submission UUID 幂等与两分钟 payload/file-hash 去重叠加；明确“再次提交”保存新的 RawItem。

URL 只允许公开 HTTP/HTTPS，常规端口、无用户密码。禁止本机、私网、link-local、共享地址、非公开 IPv6 和 IPv4 变形；DNS 全部答案必须公开，连接固定到已经校验的地址，HTTPS 校验原域名的证书。每次重定向重新验证；不使用系统代理、登录凭证或 cookie。不下载网页附件或正文图片，图片通知请直接上传截图。登录/无法读取/过大/不支持的网页仍保留最初 URL，手机提示改为截图或粘贴。

DNS 最多四个待解析工作线程，单次等待八秒；公开页面最多三次重定向、2 MB、约三十秒总期限，连接及逐块读取均受剩余时间限制。实现参考 [Python http.client](https://docs.python.org/3/library/http.client.html)、[Python TLS hostname verification](https://docs.python.org/3/library/ssl.html#ssl.SSLContext.wrap_socket) 和 [OWASP SSRF 防护](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)。二维码使用 [qrcode 8.2](https://pypi.org/project/qrcode/)，白底 SVG 保留四模块边距。

手机没有 DeepSeek key、设置、数据库管理、删除/修改事项、来源管理、Debug、目录浏览或文件下载接口。CSP、无缓存、no-referrer、禁用页面嵌入和 MIME sniffing；不信任 forwarded headers。配对 token/cookie 只保存散列，QR 只在内存。手机错误不含 traceback、环境变量、本地路径或提供方原始错误。本机日志只记录错误类型和源码 basename/函数/行号，不记异常值、正文、URL 或凭证。

## 数据与模型边界

永久内容仅保存在现有 SQLite 的 Source/RawItem/RawSnapshot/Attachment/RawReview/AIExtraction 等表，Source 固定为 `mobile`，展示名「来自手机」。没有新的永久手机 Inbox 数据库，没有 schema 升级。URL 和文件的新解析结果是同一 RawItem 的新不可变 snapshot；手机队列只显示最新版本，已有旧版本与正式 Evidence 保留。

LAN server 使用独立线程和 event loop。收到的内容通过桥接进入桌面主 event loop，再调用现有 DocumentService/ExtractionService/ReviewStore。AI key 仍由 Windows DPAPI 设置加载，手机不接触它；截图/PDF 原文件不发送给模型，只有本地提取文字按既有策略发送给 DeepSeek。收到手机内容不会直接创建 Event、Deadline、Task 或 Evidence，最终由桌面的明确用户确认写入。

## 隔离自动化与待执行真机清单

`server/test_collector.py` 使用临时 DB、模拟提供方/网络和真实本地 parser/OCR。`python -m server.collector.qa_harness --data <临时目录> --port 18473` 仅在 loopback 启动明确标记的 UI 夹具，不探测网络，不接触真实 DB/key。其临时配对 URL 只供测试工具读取，退出时删除。正常工作台不会导入或启动它。

待用户指定继续后，在实际共同 Wi-Fi 完成 LAN/Private/防火墙检查与十四项真实 iPhone 用例：扫码、文字、截图、PDF、学校 URL、重复发送、错误 token、撤销旧 token、不同网络、DeepSeek 故障、超大文件、恶意类型、私网 URL 拒绝、重启后重新启用。另完成真实截图与文字各一条「上传 → 原始保留 → OCR/Extraction → Preview → 用户确认 → 正式关系 → 重启持久化」。隔离自动化和 Edge 窄屏不替代真实 Safari 验收。

## 2026-10-08 无人值守收尾

完整自动化通过：Python 537 项（其中 Collector 138 项）、前端 11 项、Edge/Playwright 33 项；build 成功。真实 Safari 最终验收仍待用户回来进行，Phase 5 未标记 stable。LAN 和实际手机提交文字/截图/PDF 已有成功证据，不需要重复修改网络或防火墙。

QR 固定目标为当前 public_base_url 下的 `/pair?token=…`；HTTP GET 只验证并显示引导页，确认 POST 才消费。生产 public_base_url 必须是当前物理 RFC1918 IPv4，拒绝 loopback/localhost/IPv6 loopback 和 URL 中的其他内容。手机在加载时核对当前 origin；所有后续 API、manifest 和成功导航使用相对路径，确认后统一为 `/collect`。桌面管理 API 继续精确绑定 localhost。

Windows 每次生成显示 HH:MM:SS 与逐秒剩余时间，SVG 请求带独立 pairing_id 与进程 epoch。后端校验当前邀请 id/有效性，并禁用缓存；前端阻止旧轮询响应覆盖新状态、移除过期/已消费/停止的图像，图片失败可重新生成。重启默认关闭且不恢复旧邀请，窗口轮询会清除旧二维码。

`collector-pairing-state.json` 与现有 personal.sqlite3 同目录，仅保存 UTC 时间、12 位 SHA256 id、状态转换与有限请求诊断。状态包括 created/validated/confirmed/consumed/expired/revoked；confirmed → consumed 在同一原子确认中完成，history 保留两个转换。记录 created_at/expires_at/validated_at/confirmed_at/consumed_at/revoked_at/consume_reason；不写完整 token、cookie/session secret、CSRF secret、API Key、正文或查询串。保留最近 64 个邀请和 128 条请求。进程重启只读取诊断元数据，并将遗留未确认邀请记为 revoked/collector_restart；不恢复授权能力。

服务端默认 UTC-aware datetime；前端倒计时使用收到的服务端剩余秒数和 performance.now 的经过时间，避免 Windows/iPhone 本地时区影响显示。PairingAuthority 由 Desktop 主进程唯一 CollectorController 管理，LAN Uvicorn 线程引用同一对象；启动固定单 worker、无 reload，没有独立 child process 生成另一份 token。

今夜不生成最终配对二维码。回来后在工作台「手机收集」点击「生成新二维码」，用 Camera → Safari 完成配对，随后重开 Safari，并验证 Code Scanner 在确认前转到 Safari 的流程。详细根因、测试矩阵和剩余真机项目见 PHASE-5-REPORT.md。


## Safari 无痕续测说明（2026-10-08）

用户确认“重开需要配对”的当前标签页属于无痕浏览。电脑端同一 epoch、同一 LAN 地址仍有 1 个批准会话；不能据此判定普通 Safari 持久 Cookie 失效，也不能标记普通 Safari 通过。日常入口须在普通标签页单独配对；无痕与普通 Safari、Code Scanner 的 Cookie 各自独立。确认 POST 后，页面新增 GET /api/session 回读，浏览器没返回 Cookie 时不展示已配对收集入口，而显示恢复提示。新增最近 64 个设备状态和 128 条 /api/session 诊断，只有短 hash、时间、撤销原因、Cookie 是否存在和固定结果，不含凭证或原始 headers。新增 3 个 Python 回归，全量 540 项、前端 11 项通过，额外浏览器 8 项通过；build 成功。普通 Safari 真机配对/重开仍待用户。


## 最终验收状态（2026-10-08）

原始十四项真实 iPhone 验收已完成；Case 10 为短时无效模型触发的真实提供方拒绝，原配置已恢复。最终 Python 540/540、前端 11/11、build 和严格 UI audit 通过；8 条手机记录/12 快照及全部正式关系和原件经实际重启保持一致。完整当前报告见 PHASE-5-ACCEPTANCE.md 与 PHASE-5-REPORT.md。此前“待验收”段落为阶段历史记录。长 PDF 的模型输出截断仍保留为待整理事项，不等同数据丢失。验收结束时入口已启用，无 active QR；下次准备使用再生成并重新配对。

2026-10-09 稳定 checkpoint：见 [PHASE-5-CHECKPOINT.md](PHASE-5-CHECKPOINT.md)。本地标签 `phase5-mobile-collector-stable`；完整复跑 Python 540、前端 11、build 通过；历史 Safari localhost 原始触发链仍未完全复原，当前 URL 防护与真实复验已通过。没有 push 或进入下一阶段。

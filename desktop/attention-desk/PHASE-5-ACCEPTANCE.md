# Phase 5 Mobile Collector 最终验收报告

2026-10-08。**原始十四项真机清单、Windows 人工确认、实际工作台重启及最终完整自动化验证已完成。**2026-10-09 稳定 checkpoint 见 [PHASE-5-CHECKPOINT.md](PHASE-5-CHECKPOINT.md)。提供方失败使用临时无效模型触发真实 DeepSeek 请求拒绝；服务端上传边界、DOCX/TXT parser、宏/path 等使用自动化补充，未宣称全部由 iPhone 实测。配对专项十四项报告见 PHASE-5-REPORT.md。

## 1. Collector 架构

主 Windows 后端拥有唯一 CollectorController、PairingAuthority 与既有 PersonalStore/DocumentService/ExtractionService。独立 LAN FastAPI 在线程中运行，提交桥接回主服务事件循环。永久数据使用既有 SQLite 模型，回执仅为有界临时状态。

## 2. LAN server 权限隔离

2026-10-08 重启后实测监听：Desktop API 127.0.0.1:8765；Desktop frontend 127.0.0.1:5173；Collector 为当次检测到的 192.168.1.195:8473。API 和 Collector 同一 Python PID，frontend 独立进程。地址仅是验收观察，不写入配置。

LAN 路由不挂载 Desktop 管理接口、docs/debug、数据库或任意文件下载。精确 Host/Origin、有限 GET/POST、会话认证和写入 CSRF 在请求体读取前检查；有并发、速率、大小和时间限制。手机仅提交新内容及读取自己会话的有限回执。

## 3. Pairing / token 机制

启用 Collector 不开始邀请计时。点击生成 QR 才创建 32 随机字节、高熵五分钟邀请。GET /pair 和 POST validate 均只验证；用户点击完成配对才原子确认并消费。重复确认拒绝。轮换撤销旧未确认邀请，保留批准设备；撤销设备、停止、重启或网络变化清授权。

独立 device cookie 七天有效，HttpOnly、SameSite=Strict、Path=/、host-only、Max-Age/Expires。LAN HTTP 不设置 HTTPS-only Secure。URL/localStorage/sessionStorage 不保存 session secret。确认后移除邀请 query，再 GET /api/session 验证浏览器确实返回 cookie，避免假成功。

UTC-aware 日期，拒绝 naive clock。状态与 history 区分 created/validated/confirmed/consumed/expired/revoked；confirmed→consumed 为同一原子操作。安全诊断只存短 hash、时间、固定方法/路径/结果与撤销原因。邀请/设备/请求有界，重启只恢复诊断，不恢复凭证。没有完整 token、Cookie/header、CSRF、正文或 API Key 日志。

## 4. QR Code

目标始终为动态 public_base_url + /pair?token=…；控制 API 仅 localhost。QR 使用 invitation id + server epoch 作为图像和 React 身份，no-store，旧 id 拒绝，消耗/过期/停止/重启清图，旧轮询不能覆盖写入。显示本地生成时间和剩余倒计时。邀请与已配对会话分离。

最初问题有多项证据：旧前端打开后自动 POST，扫码器先消费邀请；扫码器/Safari Cookie 隔离；网络地址曾变化；静态二维码等待超过 TTL。旧 QR 缓存身份风险已修复，但没有证据将所有历史失败都归因于缓存。没有发现 Desktop/Collector 两份独立 token state 或时区导致立即过期。

## 5. 支持的输入

文字、公开网页 URL、PNG/JPG/JPEG、PDF/DOCX/TXT。来源名称与消息时间可选。HEIC 提示转 JPEG 或发送截图，本轮未新增转换。真实 iPhone 已发送文字、截图、PDF 和学校 URL；DOCX/TXT 为自动化 parser/Collector 证据。

## 6. SSRF 防护

只接受公开 HTTP/HTTPS、常规端口、无用户密码。拒绝 loopback、private、link-local、非公开地址、file/ftp、DNS 混合答案和特殊 IP 变形。DNS 校验全部答案并固定连接目标；HTTPS 验证原域证书，每次重定向重新检查。不使用系统代理、登录 cookie 或自动下载正文附件。无法抓取时保留原 URL 并建议截图/粘贴。

## 7. 文件安全

复用 Phase 4 的扩展名/MIME/magic 与安全 parser；单文件 50 MB、15 MB 以上明确确认、手机最多十件/合计 59 MB、HTTP 请求硬上限 60 MB。限制像素、解压内容、ZIP 路径/宏/执行内容及 parser 资源。未认证请求在读 body 前拒绝。原件保留、SHA256 校验、解析版本不可变；手机不能下载电脑任意文件。

## 8. iPhone 页面

四种收集入口，使用现有表单、校验、确认框、IME 防重复提交和可读错误。上传进度区分字节上传与 Windows 解析/分析状态。分析失败显示内容已保存。没有移动 Event 编辑器、DeepSeek 调用、离线云队列或浏览器长期内容存储。提供 manifest/apple-touch-icon 与添加到主屏幕提示。

真实 Safari 关闭重开通过。一次失败续测中用户确认实际使用无痕标签；不能视作普通 Safari Cookie 属性失败。引导现在明确使用普通标签页。Code Scanner GET 后、确认前打开普通 Safari 成功；同邀请两次 GET/validate 有效，随后唯一 confirm，再 authenticated。没有用 session URL 或 cookie 转移 workaround。

## 9. Desktop 设置

启用/停止、物理接口选择、端口、当前 LAN、生成/轮换 QR、单独撤销设备和诊断反馈。按钮在无 active QR 时可用。设置只保存端口和接口 ID；启动/重新启用重新枚举物理 Private LAN，排除 loopback/VPN/Hyper-V/虚拟适配器。网络地址/接口/profile 变化时停止授权，界面清旧 QR。桌面 sibling 页面保持原有行为。

## 10. RawItem 接入

共用 source=mobile，kind 为 mobile_text/mobile_url/mobile_file。文字直接保存 RawSnapshot；文件保存 Attachment 后解析/OCR 形成不可变 snapshot/block；URL 保存输入和网页版本。既有 Review Queue 展示来自手机，文件仅排队最新 snapshot，不建第二个永久 inbox。submission UUID/hash、file hash 与两分钟普通重复窗口去重；明确再次提交另建记录。

## 11. DeepSeek 复用

仅 Windows 后端使用既有 provider、schema、validation 和 ReviewStore；凭证留在 DPAPI 本机存储。远程候选不直接变成正式事项，引用与日期需要核验。真实文字/截图/学校 URL 进入 needs_review。

真实 PDF 本地解析 12,528 字成功，模型输出在用户 4,096 上限下截断。原 PDF 和正文留存，受控失败进入可审阅状态；未自动改设置或新增付费重试。这不等于 PDF AI 分析成功，也不等于实测提供方断网。

## 12. 修改 / 新增文件

- server/collector/：控制、网络发现、监听、URL 安全、提交桥接、配对及安全日志；QA harness 仅隔离自动化使用。
- server/main.py、review_store.py、documents/storage.py：服务生命周期、来源参数及手机队列复用。
- mobile/、src/mobile/：独立手机入口、导航/transport/pairing。
- src/personal/MobileCollectorSettings.jsx、collectorPairing.js、api.js、ExtractionWorkspace.jsx、PersonalWorkspace.jsx、personal.css 及 src/App.jsx：桌面入口与共享来源标识。
- start.ps1、vite.config.js、package.json、requirements：构建、固定 single worker、本机启动与依赖。
- server/test_collector.py、test_collector_pairing_lifecycle.py、tests/collector-pairing.test.mjs：自动化验证。
- DESIGN.md、UX-CONTRACT.md、README.md、COLLECTOR.md、PHASE-5-REPORT.md、本报告：行为约定与证据。

真实审核准备只保存可修改草稿：唯一原文匹配校正引用坐标；用户明确截图年份 2026 后填写 date-only，日期/参与确认仍由用户完成。原文与原 AI 输出不改动。

## 13. 测试结果

全部真机操作后完整 Python **540/540**，其中 Collector **141**，63.74 秒；前端 **11/11**，零失败/跳过；production build 通过（独立构建 2.21 秒，最终启动脚本构建 2.01 秒）。唯一 warning 为既有 Starlette/httpx TestClient 弃用提醒。最初重跑遭旧 pytest 临时目录/缓存访问拒绝，使用本次任务下全新目录后完整通过；未修改系统权限。

历史隔离浏览器 33 项覆盖 QR/TTL/旧响应/移动布局等；本轮 Cookie 补强另有 8 项，覆盖 cookie 回读、真实浏览器进程重开、轮换保留、阻断 Set-Cookie 无假成功、独立容器隔离及无运行时错误。新验证使用独立 HTTP 代理显式转发浏览器 Cookie，防止 Playwright route.fetch 的代理 cookie jar 混入。使用安装的 Edge；没有 WebKit/Safari 自动化引擎。真机结论另见第 14 项。

验收当天严格 UI audit 0 findings，diff check 通过（既有 CRLF/LF 警告）。2026-10-09 checkpoint 完整复跑结果另见 PHASE-5-CHECKPOINT.md；没有 push。

补充独立 Edge 文件替换回归：超大 TXT 无上传请求，更换为小 PDF 伪装 PNG 后文件列表替换、大小错误立即清除，模拟类型拒绝正常展示，零运行时错误。会话/上传响应隔离模拟，未向真实队列提交；不计为真机 Case 12 通过。

## 14. 真实 iPhone 验收

| 用例 | 当前证据 |
|---|---|
| 1 Safari QR | 普通 Safari 配对、刷新、关闭重开通过；Code Scanner 确认前转普通 Safari 通过 |
| 2 文字 | 保存、AI、人工确认、重启通过；用户选择不参与，保留两项未激活行动方案，零正式任务，无伪造截止 |
| 3 截图 | 原图/OCR 32 块/AI/人工确认/重启通过 |
| 4 PDF | 上传、原件、本地解析通过；真实 AI 截断受控保留 |
| 5 学校 URL | zqu.edu.cn 正文 543 字、needs_review 通过 |
| 6 重复提交 | 用户见无需重复发送；无多余 RawItem，通过 |
| 7 错误 token | 独立无痕容器拒绝，通过 |
| 8 轮换 | 旧未确认邀请明确 revoked；批准普通 Safari 会话轮换后刷新仍有效 |
| 9 不同网络 | 真机关闭 Wi-Fi 发送提示电脑不可连接并保留输入，重连后恢复原 URL 拒绝；会话和数据未变，通过 |
| 10 提供方暂时失败 | 先准备手机输入，再短时设置无效模型；真实 DeepSeek 返回 provider_error，手机明确显示已保存但自动分析失败；原文/Review Queue 保留，原模型自动恢复，通过。未宣称真实服务停机 |
| 11 超大文件 | 真机选择 51 MiB TXT 后提示最多 50 MB、请拆分，前端拒绝；无新 RawItem/Attachment，通过。服务端请求上限另有自动化证据 |
| 12 恶意/伪装类型 | 用户按 PDF 副本改 PNG 指示重选发送，明确显示文件类型或内容无法安全处理；无新 RawItem/Attachment，通过。具体文件名未另行抄录；宏/path 等另有自动化证据 |
| 13 localhost URL | 真机明确拒绝本机/内网/非公开地址，RawItem 数量未增加；自动化 SSRF 通过 |
| 14 重启/再启用 | 实际工作台重启、重新检测/启用、手机重新配对通过 |

Windows 人工确认产生 3 个 Event、1 个截止日期、6 个正式任务、21 条 Evidence。截图安全事项用户设为 done；慕课参与选择稍后，文字选择不参与；未自动激活相应行动方案。最终实际重启前后，全部 8 个手机 RawItem、12 个快照的原文/源块/Review/AI 结果/正式关系/原件逐行指纹完全一致；2 个原件 SHA256 正确，quick_check=ok，foreign_key_check=0。重启后的三个事项和 provider_error 待确认记录重新渲染。8 条手机记录含原始 5 条输入与 3 条临时失败验收文字；前两条因提交晚于保护恢复而正常分析，第三条触发失败。测试文字均未创建正式事项。

证据目录 C:/Users/ericp/.codex/visualizations/2026/10/07/01a11436-7d94-7aa0-99e3-9175f5b8bee4：phase5-real-acceptance.json（用户确认/短 hash/状态）、phase5-all-phone-before-final-restart.json 与 phase5-all-phone-after-final-restart.json（全部手机关系、AI 结果与原件指纹）、phase5-final-restart-verification.json、phase5-provider-failure-real.json、phase5-final-verification.json、phase5-final-runtime-browser.json（最终 UI）。不会用隔离夹具代替实际记录。

## 15. 网络 / 防火墙限制

Windows/iPhone 在用户实际宿舍随身 Wi-Fi 已可互访；WLAN Private。IP 曾 .199→.195，入口按设计自动停用撤权，再检测重启。没有写死 IP、修改 Windows 防火墙或 Public/Private、代理、公网配置。防火墙匹配规则检查无法完整确认时显示未完整检查，不据此宣称不存在防火墙限制；真实互访是独立成功证据。

只支持受信任 Private LAN HTTP；停止/重启/网络变化需要重新配对，浏览器关闭重开在监听持续、地址不变时无需扫码。初次电脑离线由 Safari 显示连接失败；已打开页保留内存输入并显示无法连接。无云暂存。

## 16. 仍未解决 / 未验收项

原始十四项手机清单无剩余验收项目。真实 PDF 的 4,096 token 输出截断仍是这份文档的待整理状态；原文/解析已保留，尚未再次付费重试或正式确认，不能宣称 PDF AI 成功。手机最初文字/截图/PDF来自 Code Scanner，普通 Safari 与 handoff 另有实测证据。历史 Safari 变为 localhost 的具体触发链未能复原；严格 public URL 与两种 iOS 路径已复验。HEIC 转换、离线 PWA、HTTPS 和 Shortcuts 未实现（既定范围/可选项）。

2026-10-09 已获用户授权建立本地 `phase5-mobile-collector-stable` checkpoint，提交前审计和完整复跑见 PHASE-5-CHECKPOINT.md；两项非阻塞问题仍如实保留。没有进入 Calendar/下一阶段，没有推送。2026-10-08 验收结束时 Collector 已重新检测并启用当时 Private WLAN，生成新二维码按钮可用，没有 active pairing token。最后重启按设计撤销旧手机会话；下次使用须在 Windows 生成新二维码并重新配对。

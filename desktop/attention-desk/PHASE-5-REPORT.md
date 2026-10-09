# Phase 5 Mobile Collector 配对修复与最终验收报告

2026-10-08。实现、完整自动化和原始十四项真实 iPhone 验收已完成。2026-10-09 经用户授权建立稳定 checkpoint，提交前审计和最终复跑见 [PHASE-5-CHECKPOINT.md](PHASE-5-CHECKPOINT.md)。没有 push，没有进入 Calendar 或下一阶段。完整架构、输入和逐项真机证据见 PHASE-5-ACCEPTANCE.md。

## 1. 二维码立即失效的根因

已证实的原因包括：原页面打开后自动 POST 确认，Code Scanner 提前消费邀请，而 Safari 不共享其 Cookie；静态五分钟 QR 在等待期间过期；网络检测变化触发安全停用与撤权。不能把所有历史失败归于单一原因。

## 2. stale QR / GET consume / process / restart / timezone

| 项目 | 最终结论 |
|---|---|
| stale QR | 旧 revision 重启后可重用、图片接口忽略该参数，存在已修复风险；没有证据证明每次失败均为缓存。现在 invitation 短 hash id + server epoch 同时约束 React key、图像 URL 和后端查询，no-store，使用/过期/停止/重启清图，旧轮询不覆盖新写入 |
| GET consume | 后端 GET 没有直接消费；旧前端自动 POST 才提前确认。现在 GET /pair 与 validate 不消费，十次 GET 仍有效 |
| process state | 未发现两份不同 state。唯一主进程 PairingAuthority 与 LAN 线程共享；控制 API/QR/LAN 确认一致，single worker，无 reload |
| restart | 凭证不恢复，新 epoch、默认关闭，无旧 QR；诊断保留但不能重新授权。Windows 知道二维码已失效 |
| timezone | 未发现历史时区导致立即过期。现统一 UTC-aware、拒绝 naive，299 秒有效、300/301 秒到期；UI 使用相对倒计时 |
| 其他 | 无痕浏览与普通 Safari 的容器隔离已由用户确认；普通 Safari 重新配对后刷新/关闭重开通过。启用不开始邀请 TTL，明确生成时才开始 |

## 3. 最终 pairing lifecycle

启用 Collector → 用户生成 QR → created → GET /pair?token=… / validate → validated → 明确点击完成配对 → POST confirm → 同一锁内 confirmed、consumed → 独立 device session → 相对 /collect。

32 随机字节高熵邀请，五分钟有效，只存 SHA256；确认 single-use，第二次拒绝。轮换只撤销旧未确认邀请。状态另区分 expired/revoked。安全诊断保存短 id、UTC created/expires/validated/confirmed/consumed/revoked、consume_reason、有限 history；仅固定 method/path/result，不存完整 token、Cookie、CSRF、正文或 API Key。短期本地诊断跨进程保留；实际权限由同一服务锁管理，重启恢复诊断并撤销授权。

## 4. Device session lifecycle

独立 32 随机字节会话，最长七天，仅提交新内容和读取自己会话的有限回执。浏览器刷新/关闭重开保持；重新生成 QR 保留批准设备。Windows 有单独撤销设备操作；停止、工作台重启、网络变化或到期撤销，数据不删除。设备/诊断容量有界，凭证不写本地 state 文件。

## 5. Safari Cookie 设置

ad_collector_session 为 HttpOnly、SameSite=Strict、Path=/、host-only、Max-Age=604800 和 Expires。LAN HTTP 使用 Secure=False；写入仍校验精确 Host/Origin 与 CSRF。session secret 不进 URL、localStorage、sessionStorage 或日志。确认后清邀请 query，再 GET /api/session 验证 Cookie 回读；未保存时不会展示假成功。普通 Safari 持久化已实测。

## 6. Safari 127.0.0.1 的根因与证据限度

**当次 iOS 导航链未保存，具体触发点未能确定。**源码发现旧 create_app 未校验 origin，隔离 QA 可生成 loopback 测试 QR，早期 QA 截图曾包含该测试图；这是能产生 localhost 手机地址的路径，不能推断用户必然扫了它。

生产 public URL 现拒绝 localhost/127.0.0.1/::1、0.0.0.0、公网及非法 path/query/fragment；loopback 仅隔离测试明确 opt-in，正常 Controller 不启用。QA 图遮蔽。QR → pair → confirm → home、Location/config 的自动化回归与真实 Code Scanner/Safari handoff 已通过，不再观察到手机跳向 loopback。

## 7. public_base_url 修复

Desktop API 仍 127.0.0.1:8765，frontend 127.0.0.1:5173。Collector 每次启用重新枚举实际物理 Private LAN，精确绑定当前 RFC1918 IPv4:port；偏好仅 interface_id/port，不存 IP。网络变化停止并撤权，重新启用检测新地址。

QR 使用当前 public_base_url + /pair?token=…；手机校验 /collector-config 的 origin。API、manifest、form 请求、home/history/Location 优先相对路径。最后观察为 WLAN Private、192.168.1.195:8473，仅为观察值；没有把该地址或旧热点 172.20.10.8 写进配置。

## 8. Code Scanner / Safari

推荐 Camera → 普通 Safari → 明确完成配对。landing 明示“建议在 Safari 中打开后完成配对”，确认前保留邀请，可由扫码器菜单打开 Safari。GET/prefetch/reload 不消费。扫码器与 Safari、普通与无痕分别持有 Cookie；没有不安全会话转移 workaround。

真实普通 Safari 关闭重开通过；真实 Code Scanner GET、确认前转 Safari、同邀请第二次 GET/validate、唯一 POST confirm 后认证通过。已经在扫码器批准的会话不能假定转给 Safari，应生成新邀请由 Safari 单独批准，原批准设备保留。

## 9. 修改文件

应用根目录 C:/Users/ericp/Desktop/reference/touxian/desktop/attention-desk。

- server/collector/：security、controller、control_api、endpoint、app、network/public_url、submissions、safe_logs；qa_harness 仅隔离自动化。
- server/main.py、review_store.py、documents/storage.py：生命周期、既有数据模型与来源参数复用。
- src/mobile/CollectorApp.jsx、pairing.js、transport.js 和 mobile/：独立手机入口、明确确认、Cookie 回读、相对导航与上传。
- src/personal/MobileCollectorSettings.jsx、collectorPairing.js、api.js、ExtractionWorkspace.jsx、PersonalWorkspace.jsx、personal.css 及 src/App.jsx：Desktop 收集入口、QR 状态、手机来源标识。
- start.ps1、vite.config.js、package.json、server/requirements*.txt：构建、启动及依赖。
- server/test_collector.py、test_collector_pairing_lifecycle.py、tests/collector-pairing.test.mjs：关键自动化。
- DESIGN.md、UX-CONTRACT.md、README.md、COLLECTOR.md、两份 Phase 5 报告：约定与验收记录。

正式事项由用户在 Windows 确认；代理仅保存可修改的引用/日期草稿，没有代点确认。原文与原 AI 输出未篡改。

## 10. 新增测试

Collector 141 个参数化用例覆盖：创建/十次 GET/一次确认/二次拒绝，299/301 秒，轮换旧邀请及批准会话保留，撤销、重启与 UI QR 身份，LAN 全链无 localhost，Cookie、无认证上传、最小权限和重复提交。

另覆盖 SSRF DNS/redirect/rebinding、超时、MIME/extension/magic/宏/ZIP 路径、上传硬/软限制、限流、断线任务继续、文件原件/解析版本、OCR/Review/正式确认持久化及安全日志。新增三项 Cookie 容器诊断/七天到期与重启不恢复授权/记录有界无 secret。前端 11 项检查 public URL、相对 home、倒计时与 QR 清图。

隔离 Edge 历史 33 项、Cookie 补强 8 项，另有文件替换场景；独立代理显式转发浏览器 Cookie，避免 route.fetch 测试 jar 混入。最终真实工作台三事项、provider_error 审阅页和无 QR 的生成按钮渲染通过。Browser plugin/WebKit 不可用，使用已安装 Edge；未将其当作 iOS 实测。

## 11. 完整测试结果

| 检查 | 最终结果 |
|---|---|
| 完整 Python | 540 passed，63.74 秒，其中 Collector 141；唯一已有 Starlette/httpx 弃用 warning |
| 前端 | npm run test:frontend：11 passed，0 failed/skipped |
| 静态 UI audit | strict：0 errors/warnings/violations |
| diff check | 通过，仅已有 CRLF/LF 提示 |
| 实际数据重启 | 8 RawItem、12 snapshot、所有 Review/AI/正式关系逐行指纹一致；2 原件 SHA256 正确；数据库 integrity=ok、foreign_key_check=0 |

首次最终重跑被旧 pytest 临时目录/缓存权限阻止，未改系统 ACL；选用本次任务中的全新 temp/cache 后完整重跑通过，没有用少数新增测试替代全套。

## 12. Build 结果

npm run build 成功（2.21 秒），最终 start.ps1 启动时再次构建成功（2.01 秒）。Desktop/mobile 双入口正常，实际监听由 OS 确认：127.0.0.1:8765、127.0.0.1:5173、当前 WLAN:8473；Desktop API 与 Collector 同 owning process。

## 13. 安全边界检查

手机不能 Settings/secrets/删除/数据库/debug/任意文件下载；Desktop 管理 API 未暴露至 LAN。DeepSeek 仅 Windows 后端调用，Key 留 DPAPI；未打印完整 token/session/API Key。上传与 SSRF 限制保留。没有修改 Windows Firewall、Public/Private、系统代理、公网访问、Tailscale/Tunnel。

Case 10 通过短时无效模型触发真实 provider_error，手机显示“已保存，但自动分析失败”；原文/Review Queue 保留。捕获后自动恢复原模型，其余公开提取设置与凭证来源未变，未写 Key 字段。两次较早提交晚于保护恢复而正常分析，第三次先准备输入再启用后才通过；无效模型未留在当前配置，所有保护脚本已结束。

## 14. 真实 iPhone 最终状态与剩余限制

原始 14 项均完成，逐项证据见 PHASE-5-ACCEPTANCE.md；Case 10 是受控真实 API 拒绝，不宣称遇到自然服务停机。普通 Safari 持久化、扫码器确认前 handoff、文字/截图/PDF/官网 URL、去重、错误/轮换邀请、断线恢复、超大/伪装文件、私网 URL 拒绝、Windows 人工确认和实际重启均通过。

用户确认后产生 3 个正式 Event、1 个 date-only Deadline、6 个 Task、21 条 Evidence。文字用户选择不参与，保留两项未激活行动方案，无固定日期所以未制造截止；截图慕课年份 2026 由用户明确提供。三条测试文字没有正式化。

真实长 PDF 解析成功，但当前 4,096 token 下 AI 输出截断，仍待用户主动重试或手动整理；原件与全文保留。HEIC 转换、HTTPS、完整离线 PWA/Shortcuts 未实现（既定范围/可选项）。历史 localhost 导航具体触发点未能复原，已通过严格 URL 守卫与两种真实 iOS 路径复验。

2026-10-08 验收结束时 Collector 已按当时 Private WLAN 启用，“生成新二维码”按钮可用，没有 active pairing token。最后重启按设计清旧会话，下次使用需新二维码配对。2026-10-09 稳定 checkpoint 记录见 [PHASE-5-CHECKPOINT.md](PHASE-5-CHECKPOINT.md)，本地标签为 `phase5-mobile-collector-stable`；到此停止，不进入下一阶段。

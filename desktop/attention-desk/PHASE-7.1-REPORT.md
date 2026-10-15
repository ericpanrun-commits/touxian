# 【Phase 7.1 Bark iPhone Notification Report】

验收日期：2026-10-15。起始版本 a7e9b37。真实 Bark 凭据已通过后端环境加载验证，连接测试与受控自动推送均已被 Bark 接受。本报告不把 HTTP 接受当作手机横幅实收。

| 验收项 | 结果 |
|---|---|
| 1. 架构 | 审计后扩展现有官网抓取、通知记录及 scheduler；Information Item → Policy → Event → Dispatcher → Windows / Bark。 |
| 2. Bark Client | 独立 JSON POST /push；UTF-8、3 秒连接/8 秒读取/5 秒写入超时、固定错误码、不跟随重定向、支持分组/声音/链接/图标/copy/level；不使用持续响铃。 |
| 3. Dispatcher | 复用官网 30 秒调度周期，数据库队列和跨进程原子领取；Bark 失败不回滚文章或影响 source run。 |
| 4. Policy | 复用 Phase 7 relevance/importance/summary/时间线索；集中 NotificationPolicyConfig + 持久偏好，无额外 LLM。 |
| 5. 立即推 | 可信近期 critical；high + relevance≥80；车辆工程最高相关；学院本年级执行事项；高相关核心教务。普通合格消息在静默结束发送。 |
| 6. 不推 | 旧数据、来源首次 backfill、未知/过旧发布日期、过期、low、低相关、宣传新闻、非可信或停用来源。 |
| 7. 防轰炸 | 首次启用以写事务建立 activation_at 并登记全部已有官方信息 historical；持久 backfill 标记。500 旧 + 1 新测试为 0 + 1。 |
| 8. 永久去重 | channel/item/type、channel/转载组/type、dedup_key 唯一；sent 永不重发，重启/重复扫描/文章更新/更换 Key 均保留历史。 |
| 9. Retry | 普通 4xx 不重试；连接失败、429、5xx 最多 3 次，队列退避 30/60 秒。读写超时/请求后崩溃记 delivery_unknown，不自动重发；不宣称外部 exactly-once。 |
| 10. Quiet hours | Asia/Shanghai 23:30–07:30，可关闭；普通持久延后，critical 绕过；发送前重查有效期。 |
| 11. 点击去向 | 官方 canonical_url，校验配置内 ZQU HTTP/HTTPS 域名；拒绝危险协议、伪造后缀和 localhost。 |
| 12. Settings | 新增 Bark 分类，显示配置/连接/失败/关闭、最近成功失败、基线、策略开关/门槛/静默及分页历史；详情显示推送状态。 |
| 13. Test Notification | 明确标记 test、每分钟最多一次、与正式去重分离；已通过正式后端测试接口发送且接受 1 条，随后另接受 1 条受控自动链路通知。 |
| 14. Migration | 008 增量扩展 official_notification、raw_item 并增加 notification_preferences/索引，无 drop/reset。已备份真实库，副本核对 32 张旧表和 559 条 RawItem，FK/quick check 通过。 |
| 15. Secret | 仅后端环境变量/忽略的 .env；完整 Push URL 可 override；前端只获是否配置。扫描源文件、diff、history、bundle、日志无 Bark secret 命中；截图无 Key。 |
| 16. Backend tests | 全量 774 passed；1 条既有 Starlette/httpx 弃用提示。覆盖真实 crawler 流程、mock HTTP、策略、状态、并发、500 旧数据、恢复、禁用、接口。配置文件不可读隔离另行实测通过。 |
| 17. Frontend tests | 28 passed；新增安全错误显示、状态映射和静默时间校验。生产 build 通过。新 Python Ruff F/E9 通过，新前端帮助模块 TypeScript checkJs 通过；仓库没有原有全项目 lint/typecheck 任务，不冒充完成全 JSX 静态类型迁移。 |
| 18. Browser tests | Playwright Chromium 19 场景通过，1440×1000 / 390×844；首页、Latest、相关、学院、教务、来源、设置、Bark、Reminder、Calendar、键盘/窄屏/冲突/断网/空态/加载/测试按钮。应用错误 0；模拟 409/断网产生的预期网络日志单列。strict UI audit 和 DESIGN lint 均 0 错误。Browser plugin 不可用，使用 Playwright。 |
| 19. 实际 iPhone | 真实 Key 已加载；Settings 从 Configured 变为 Healthy。连接测试 accepted=true；受控自动通知真实 HTTP 200。iPhone 应收到两条明确的验收通知，横幅实际显示仍需由设备确认。 |
| 20. 重复通知 | 真实自动通知事件再次分发和新建 Dispatcher 后未发生第二次 HTTP 请求；历史/低相关均未推。无法宣称外部 APNs/Bark 端到端 exactly-once。 |
| 21. Backend 关闭 | 不能继续抓取；关闭网页且后端保留可继续。 |
| 22. 电脑关机 | 不能继续抓取或推送。 |
| 23. Cloud 状态 | 无 Fly/Docker/Procfile/云 worker/部署 Actions/云数据库方案；未部署、未创建付费资源。唯一真源仍为本机 SQLite。 |
| 24. 24/7 缺口 | 唯一常驻云后端、SQLite/附件持久卷迁移与备份、统一调度/通知历史、云 secret、监控及费用确认；禁止双独立 SQLite 同时通知。 |
| 25. Commit | 功能提交 e6aee70，消息 feat: add Bark iPhone notification delivery；本次真实验收结果由后续文档提交记录。稳定功能版本可 git show phase7.1-bark-notifications-stable。 |
| 26. Tag | phase7.1-bark-notifications-stable；同名若占用则使用递增后缀，实际值以最终回复为准。 |
| 27. Git status | 完成提交及标签后核对 clean，不 push；真实 DB、备份、.env、工具、截图/日志均不提交。 |

## 配置与当前运行状态

`desktop/attention-desk/server/.env`（gitignore）现已由用户填入真实 Key，并重启 8765 后端生效。不需要改代码或清库。以下仅为配置格式示例，禁止把真实值写入本文：

```dotenv
BARK_ENABLED=true
BARK_SERVER_URL=https://api.day.app
BARK_DEVICE_KEY=your_bark_device_key_here
```

连接测试已完成，不需要反复发送。使用指南、诊断、关闭、换 Key、恢复边界见 BARK.md。digest 和手动强制重发为明确未启用的可选功能。

**A. 学校出现新重要信息后自动推到 iPhone？** 是。真实 Key 已生效，受控新信息经过现有 crawler、scheduler、policy、数据库事件和真实 Bark 验证；正常后台运行时自动判断并发送。

**B. 需要手动点发送？** 不需要。测试按钮仅用于连接验收；正式通知随抓取与调度自动处理。

**C. 电脑完全关机后仍能抓取并推送？** 不能。下一阶段需上述唯一云端常驻方案，本阶段没有伪装成 24/7。

## 真实验收补充记录

- 后端身份核对后重启，server/.env 的 Key 与运行配置匹配，仅输出 configured/enabled 等安全状态。真实 Settings 页面渲染 Healthy，没有页面错误，Key 仅显示已配置。
- 正式数据库首次启用生成 539 个历史分组抑制记录；当时 pending=0、sent=0。连接测试独立于正式去重。
- 受控验收采用隔离 SQLite 和模拟官网 HTML，运行原有 HTML client/parser、OfficialSourceMonitor 的真实 30 秒调度循环和 NotificationDispatcher；Bark 请求为真实网络请求。
- high/relevance=98 的新信息自动发送并进入 sent，Bark 返回 HTTP 200。历史 backfill 与 high/低相关信息均 suppressed；重复分发、重建 Dispatcher 均无重发。
- 曾遇到一次连接建立超时；无接受响应且属于发送前连接失败。网络诊断无 Key 的 /ping 证实瞬时超时后恢复。后续受控验收成功即停止；测试按钮没有再次发送。
- 隔离数据库已移除，正式 raw_item 中三种测试标题计数为 0。只在忽略的 artifacts 中保留无密钥验收结果，不写入正式信息流。
- 本次相关回归 180 passed（Bark/官网抓取/官网信息/提醒），前端 28 passed；既有 Starlette/httpx 弃用提示 1 条。真实 Key 精确扫描覆盖 460 个跟踪文件、diff、全部 git history、前端 bundle 和日志，泄露命中 0，server/.env 仍被忽略。

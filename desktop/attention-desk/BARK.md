# Bark iPhone 自动通知（Phase 7.1）

Bark 是接收自定义推送的 iPhone 应用。本渠道仅发送可信肇庆学院官网的公开通知摘要；本地聊天、私人事项、密码和附件不进入 Bark。

## 配置与测试

1. 在 iPhone 安装并打开 Bark，允许通知。
2. 复制 Device Key 或完整 Push URL。不要发到 GitHub 或写进前端。
3. 复制 `server/.env.example` 为 **`server/.env`**：

   ```dotenv
   BARK_ENABLED=true
   BARK_SERVER_URL=https://api.day.app
   BARK_DEVICE_KEY=your_bark_device_key_here
   BARK_GROUP=ZQU
   BARK_DEFAULT_SOUND=
   ```

   将占位符替换为自己的 Key。也可设置 `BARK_PUSH_URL=https://api.day.app/你的Key`，它优先于 Device Key，内部提取 Key 后仍使用 POST JSON `/push`。自建服务有路径前缀时同时设置对应 BARK_SERVER_URL。不要在 URL 中附加账号密码。

4. 重启后端（正常使用 `./start.ps1`）。需明确设置 `BARK_ENABLED=true` 才允许连接测试，默认关闭。开发后台默认不自动抓取或 Bark；生产自动推送另需唯一生产权限及数据验证。优先级：进程环境 > `server/.env` > 工作台 `.env` > `desktop/.env`。
5. 个人工作台 → 设置 → **Bark 手机通知** → **发送测试通知**。
6. 成功表示 Bark API 接受请求，请在 iPhone 核对横幅。测试标记 `[test]`，不占用正式去重，每分钟最多一次；健康检查不会定时给手机发消息。

Settings 不接受/返回 Key，只显示是否配置、服务主机、最近结果和启用时间。`.env`、`.env.local`、`.env.production` 被 gitignore，只有无密钥 `.env.example` 可提交。不要创建 VITE_BARK_* 变量。无真实 Key 时显示未配置，其余功能继续工作。

## 自动链路

```text
官方 Source → 现有 crawler → RawItem/RawSnapshot/official_item 提交
                                    ↓
                  NotificationPolicy（已有 relevance/importance）
                                    ↓
                  现有 official_notification 持久化队列
                                    ↓
                    NotificationDispatcher（现有调度周期）
                       ├─ WindowsNotifier（原策略/隐私设置）
                       └─ BarkClient JSON POST → Bark → iPhone
```

Phase 7.1 的本机链路曾由现有官网 scheduler 每 30 秒检查队列。Phase 7.2-Free
把唯一生产自动权限交给 GitHub 小时任务，复用同一 crawler、策略与 dispatcher，
每轮有限运行后退出。高/普通优先级至少间隔一小时，低优先级四小时。本地开发
默认关闭自动抓取和自动 Bark；连接测试独立可用。`ATTENTION_OFFICIAL_POLL=0`
仍用于关闭本地后台官网轮询。云端工作流尚未推送、启用，当前不能宣称自动送达。

复用 Phase 7 档案：肇庆学院、车辆工程、机械与汽车工程学院、2026 级。不调用新的 LLM，不修改 relevance、importance 或已确认的 Reminder/Calendar 事实。

## 推送策略

默认推送：

- 近期可信 official source 的 critical，绕过相关度门槛与静默时间。
- high 且 relevance ≥ 80。
- normal 且已有专业原因判断车辆工程直接相关，relevance ≥ 98。
- 学院要求本年级执行的高相关通知，以及高相关核心教务、考试、选课、课程、成绩、学籍事项。
- 竞赛、国际交流、实习、奖助等遵循同一 high + relevance 门槛，专业直推开关仍适用。

默认不推：历史/首次来源 backfill、发布日期未知或超过 2 个自然日、已结束的通知、low、低相关、普通校园新闻/宣传、非官方来源及已停用来源。critical 仍受历史、可信来源和过期保护。

`NotificationPolicyConfig` 集中保存开关、minimum_importance、minimum_relevance、categories（空代表不限）、source_priorities、fresh_days、max_per_hour、quiet hours、timezone。Settings 提供常用开关/门槛/静默时间；其他字段可通过同一受校验、带版本号的本机 API 调整。digest 暂未实现并明确拒绝启用；不提供强制重发按钮。

## 历史保护与永久去重

迁移 008 只追加字段、索引和偏好表，扩展**现有 official_notification**，不建第二套消息历史，不删除旧数据。首次具备凭据且启用时，同一 SQLite 写事务记录 activation_at，并将全部已有官方 RawItem 登记为 suppressed/historical。

拒绝 activation 前发现的信息。RawItem 的 official_backfill 持久标记保护首次来源扫描及失败后续抓。启用后新发现的旧公告仍受 backfill、发现时间和两天新鲜度窗口限制。

唯一约束：每个 channel、RawItem、new_item 一次；每个 channel、转载 display_group、new_item 一次；dedup_key 另有唯一索引。Bark 在原主键上使用 bark: 命名空间，旧 Windows 主键保留。

sent、suppressed 和终止失败不会因重启、重复同步、更新正文、换 Key 或重算分类重新入队；转载同组只推一条。队列不依赖内存 set。抓取先提交，后续调度找回提交后还未创建事件的新信息。

单独测试通过：500 条旧信息 → 启用 → 0 推送；新增 1 条高相关 high → 仅 1 推送。

## HTTP、重试、并发与崩溃恢复

使用当前 Bark 官方 JSON POST `/push`。Client 可传 title/body/group/url/sound/icon/level/copy。紧急展示使用 timeSensitive，普通 active；不使用 critical 响铃或持续呼叫。标题最多 120 字符，正文最多 400，复用 summary，优先展示来源和截止；不足 24 小时只增强措辞，不修改 importance。

连接超时 3 秒、读取 8 秒、写入 5 秒；不跟随重定向，不继承环境代理。普通 4xx 不重试。连接失败/连接超时、429、5xx 最多 3 次；持久队列退避 30/60 秒，无嵌套 HTTP 重试。普通每小时最多领取 3 条，积压延后并再次校验期限；critical 绕过普通限额。

SQLite BEGIN IMMEDIATE + 原子领取防两个 worker 同发。pending/sending/sent/failed/suppressed、计划/尝试/发送/失败时间、次数、安全错误码和 provider id 持久化。请求前另存 request_started_at。两分钟过期的“领取后、请求前”事件可恢复；已开始请求的中断事件转为 delivery_unknown 并停止自动重发。启动不会抢走活跃 worker 的领取。

**外部 API 没有端到端 exactly-once 保证。** 读取/写入超时可能发生在 Bark 已接受之后，因此不盲目重试，代价是极小窗口可能漏报。稳定 id 复用 Bark 更新语义降低重复风险，不宣称其提供事务性幂等。HTTP 成功不能证明手机已显示横幅。本阶段优先不重复、不刷屏。

sent 永不重发。发送失败时文章已入库，source run 不失败。更换 Key 只改 `.env` 并重启，不清数据库；以后新通知正常判断，旧终止记录保留。delivery_unknown 应直接查看原文，不通过清历史强制重放。

API 参考：[官方教程](https://github.com/Finb/Bark/blob/master/docs/en-us/tutorial.md)、[Server V2](https://github.com/Finb/bark-server/blob/master/docs/API_V2.md)、[消息参数](https://github.com/Finb/Bark/blob/master/docs/en-us/params.md)。

## 静默与打开原文

默认 Asia/Shanghai **23:30–07:30**。普通通知延迟到静默结束，发送前重查静默和截止；critical 立即发送。可关闭静默，支持跨午夜/同日范围，起止相同代表无静默。

点击打开 canonical_url 官方原文，仅允许可信配置内 ZQU HTTP/HTTPS 域名。不跳 localhost，不接受 javascript/file/data、账号密码 URL 或伪造大学后缀。

## 记录、诊断与关闭

- Settings → 最近手机通知：20 条一页，标题、时间、状态、尝试次数和中文错误原因。
- 官网详情显示手机通知已发送/等待/过滤/失败/未发送。
- 已配置：尚无成功发送；连接正常：来自最近接受结果，不代表主动探测。
- 未配置：填写后端 `.env` 后重启；invalid_config：检查协议、主机、Key 和布尔格式。
- unauthorized / invalid key：核对 Bark Key，更新配置并重启，无需删除记录。
- 网络/5xx/429：查看失败和 scheduled_at，队列自动有限重试，三次后停止。
- 数据库暂时忙：下个周期继续；抓取已提交的信息不回滚。
- 关闭：Settings 取消启用并保存，或 BARK_ENABLED=false 后重启。Windows/Reminder 独立运行。

日志只记录候选/过滤/排队/发送/失败、item id 和固定错误码，不记录正文、请求/响应体、Key 或含 Key 的 URL。provider_message_id 用本地稳定 id，不保留可能夹带敏感内容的供应商响应。

## 部署与 24/7

Phase 7.2-Free 已把学校公开信息、来源、通知历史和 baseline 导入已有 Supabase
Free 项目的私有 `zqu_worker` schema。私人事项、聊天和本地附件继续留在 SQLite。
以后生产学校数据以远端为准，本地 SQLite 作为保留副本，不能再开启第二套自动
推送。当前页面设置/历史仍属于当前后台；尚未接入私有云状态或云策略管理。

本地 GitHub workflow 已准备：普通 Ubuntu runner 每小时 :17 执行一次 Python
worker，无 Docker、FastAPI、React 常驻进程。GitHub concurrency、数据库租约和
原子领取防并发重复。Bark 和数据库凭据仅由 GitHub Secrets 提供，工作流不加载
本地 `.env`。不使用 Fly，不执行仓库保留的旧 Fly 准备脚本。

**当前还不能在电脑关机后自动抓取或推送**：工作流尚未 push，当前 GitHub
身份只有 READ 权限，Secrets 未配置。首轮必须 dry_run=true，验证历史零推送
后再做受控真实验收、启用 schedule。步骤和限制见
[Phase 7.2-Free 报告](PHASE-7.2-FREE-REPORT.md)。

## 验证

```powershell
../.venv/Scripts/python.exe -m pytest -q -o cache_dir=artifacts/pytest-cache --basetemp=artifacts/pytest-check
npm run test:frontend
npm run build
# 先启动本机前后端。浏览器拦截测试推送，不发送真实 Bark。
$env:ATTENTION_QA_URL='http://127.0.0.1:5173'
npm run test:browser
```

Playwright 为开发依赖。截图/报告输出系统临时目录，可用 ATTENTION_QA_OUTPUT 指定，不提交图片/日志。受限 Windows 沙箱需允许本机回环连接才能运行 TestClient/浏览器，socketpair 被阻止不是业务死锁。

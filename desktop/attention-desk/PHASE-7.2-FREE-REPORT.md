# Phase 7.2-Free Production Launch Report

2026-10-17（Asia/Shanghai）：**Production Live。**

首轮真实 GitHub `schedule` 任务已成功完成，随后于北京时间 **06:27** 开启正式自动 Bark。
生产自动抓取和通知仅由 `ericpanrun-commits/touxian/main` 的 GitHub Actions 执行，
不依赖本机、前端、Docker Desktop 或 Fly。新增固定月费用 **$0**。

## 上线检查表

| # | 检查项 | 实际结果 |
| --- | --- | --- |
| 1 | Git 修复方式 | 使用 Codex 已安装 Git 的完整路径及进程临时 PATH；仓库凭据助手复用 gh 安全登录，未重装 Git或修改系统网络 |
| 2 | GitHub 授权账号 | ericpanrun-commits，用户自己的账号，push/admin=YES；未切换账号或使用第三方凭据 |
| 3 | Push | YES；正常向前推送 origin/main，全部历史提交保留，upstream 保持原样 |
| 4 | Remote HEAD | 最终 main 与稳定标签见 GitHub；后端功能代码 d22d3471fdff0409273830faca557c73bc1c53ad，生产定义显示名称更新 ed8c88c；后续仅更新文档 |
| 5 | Workflow | ZQU official information，ID 386902574，main，active；真实 schedule 已执行 |
| 6 | Environment | zqu-production，仅 main |
| 7 | Secrets | SUPABASE_DB_URL=YES；BARK_DEVICE_KEY=YES；BARK_SERVER_URL=YES |
| 8 | Variables | PRODUCTION_DATABASE_ID=9ecdb99fd389aa9d51a43765c119ff3c；ZQU_ACTIONS_ENABLED=true；ZQU_NOTIFICATION_DRY_RUN=false |
| 9 | First dry run | 成功；真实连接、抓取和写入，dry_run=true、sent=0；first_dry_run_verified 由真实 worker 写入 |
| 10 | Source fetch | 首轮手动 4 来源成功；首轮真实定时 5 来源中 4 成功、1 项预算延后，更新 3 条，sent=0 |
| 11 | Historical sent | 0 |
| 12 | Historical replayable | 0 |
| 13 | Acceptance Bark | Bark 服务接受唯一 1 条明确标记 [test] 的通知；永久 receipt=accepted |
| 14 | Second acceptance | 新 runner 重跑：sent=0、duplicate_prevented=1；未重置或删除 receipt |
| 15 | Schedule dry run | 成功；真实事件 schedule，run 38974389683，dry_run=true、sent=0，数据库记录与日志一致 |
| 16 | Production live | YES；真实定时静默验收及数据库保护检查通过后才解除 dry-run |
| 17 | Supabase persistence | 新连接、新 runner 通过；最新 10 表一致性备份在新建隔离本机 PG 数据库恢复，全部行哈希一致 |
| 18 | Production authority | 唯一 GitHub Actions repo/main；数据库 identity、租约及原子 claim 保护；临时诊断不访问学校数据或 Bark |
| 19 | 本地 automatic Bark | NO；development 默认 automatic_sync/automatic_bark=false，不拥有生产自动权限；人工连接测试单独保留 |
| 20 | Docker | NO；未启动、调用或修复 Docker Desktop |
| 21 | Fly | NO；未创建、部署或修改 Fly 资源，照片应用保持独立 |
| 22 | 新增固定月成本 | $0；公开仓库标准 runner + 已有 Supabase Free，无收费资源或 billing 变更 |
| 23 | Tests | 后端 965 通过，其中原生 PG/云 worker/生产上下文/CA/轮转专项 38；前端 30、浏览器 24、build 通过；最新 workflow 检查通过 |
| 24 | Remaining issues | 免费 cron 可能延迟/跳过；60 天无仓库活动会停用 schedule；一次批次不保证扫描完 29 来源；官网一篇旧公示 404，原正文和历史保留 |

稳定标签：`phase7.2-cloud-worker-stable`。完整最终 commit hash 由 git/远端标签核对。

## 实际云端证据

| 验收 | GitHub run | 结果 |
| --- | --- | --- |
| 首轮静默抓取 | [38938718465](https://github.com/ericpanrun-commits/touxian/actions/runs/38938718465) | success；4 来源成功，新增 1、更新 6，sent=0、would_send=0 |
| 唯一 Bark 受控验收 | [38939971818](https://github.com/ericpanrun-commits/touxian/actions/runs/38939971818) | success；sent=1、low_relevance_suppressed=1 |
| 跨 runner 重复验收 | [38940266643](https://github.com/ericpanrun-commits/touxian/actions/runs/38940266643) | success；sent=0、duplicate_prevented=1 |
| 当前代码冷启动静默检查 | [38943829381](https://github.com/ericpanrun-commits/touxian/actions/runs/38943829381) | success；3 来源成功、2 项未完整完成；一个旧链接 404，一个预算到期，sent=0 |
| 调度上下文诊断 | [38962679697](https://github.com/ericpanrun-commits/touxian/actions/runs/38962679697) | success；记录 repo/main、变量与门控布尔值，不读取生产 Secrets，不访问 DB/Bark |
| 首轮真实定时静默运行 | [38974389683](https://github.com/ericpanrun-commits/touxian/actions/runs/38974389683) | success；event=schedule，5 来源、4 成功，新增 0、更新 3，dry_run=true、sent=0 |

真实定时任务于北京时间 06:12 创建，06:20 完成。其成功来源为
`zqu-jwc-competition`、`zqu-jwc-internship`、`zqu-jxqc-notices`、`zqu-jxqc-news`；
`zqu-jxqc-students` 因本轮时间预算到期而部分完成。单一来源不完整不会阻断其他来源。
日志只提取公开身份、状态和数值；worker 将同一份安全计数写入 GitHub Job Summary。
Bark 验收证据为服务接受请求，未把该结果写成观察到手机横幅。

原有 539 条通知按原导入时间划定固定集合，身份、status、suppressed_reason 和
sent 标记的完整摘要与已验证备份一致：`02480adcbee0150295a2d8c42a0bef14`。
当前 540 个通知 dedup 全部唯一，historical_sent=0、historical_replayable=0；
基线、activation、原始快照哈希和 accepted receipt 均未重置。

正式开关现在为 false（允许真实生产推送），数据库 cloud_status.dry_run=true
表示上一轮已观察任务的模式。未伪造数据库状态；下一自动轮次将读取正式开关。
当前 last_successful_cloud_run=2026-10-16T22:20:36.20449+00:00，stale=false。

## 工作流与运行规则

- `.github/workflows/zqu-official-info.yml`：唯一生产 worker；UTC/北京时间均每小时 :50，支持手动 cycle/acceptance，手动默认 dry-run。
- `.github/workflows/zqu-schedule-diagnostic.yml`：临时无 Secrets 诊断，现已停用；定义及成功运行记录保留供审计，不参与自动生产。
- 标准 Ubuntu 24.04，Python 3.12，锁定最小依赖、固定 Action SHA，12 分钟 job 上限，固定 concurrency 且 cancel-in-progress=false。
- 一次性 worker 复用现有 crawler/parser/classifier/policy/dispatcher；不启动 FastAPI、React 或常驻 scheduler。
- 数据库租约与 PostgreSQL 原子 claim/唯一约束保护跨 runner 并发；未知投递结果不自动重发，sent 不重放。
- 高/普通优先级至少间隔一小时，低优先级四小时；有限批次优先处理最早到期来源，同期限才按优先级排序，防止来源饥饿。
- 没有 on.push 通知触发器。健康读取不抓取、不发 Bark；详细运行日志保留 30 天，identity、baseline、dedup 和永久验收 receipt 保留。

## 迁移与现有业务保护

只安装已批准的 `supabase/migrations/20261015042812_zqu_actions_state.sql`，
SHA256 `5F8E5C7202609651E5769A00A0059E121716C80526308C52CF8B3C4952FCB927`。
远端迁移版本为 `20261016114712 zqu_actions_state`。
DROP、DELETE、TRUNCATE、UPDATE without WHERE、ALTER DROP、CASCADE 均为 0。
没有执行旧弹窗对应 SQL；上线修复没有重装迁移或清库。

仅使用私有 zqu_worker 的 meta、sources、items、source_refs、aliases、
notification_events、leases、crawl_runs、worker_runs、cloud_status 十张表。
RLS 开启，anon/authenticated/PUBLIC 不获学校表权限，没有公开管理 API。
原有 17 张 public 业务表在安装、导入及真实定时运行后逐表计数均与原基线一致；
没有对 public/auth/storage/extensions 执行写操作。

| 数据 | 初始导入 | 真实定时运行后 |
| --- | ---: | ---: |
| sources | 29 | 29 |
| items | 543 | 544 |
| source_refs | 543 | 544 |
| aliases | 543 | 544 |
| notification_events | 539 | 540 |
| crawl_runs | 711 | 725 |

SQLite 迁移前备份 integrity_check=ok，初始数据数量、内容聚合与全部原身份已验证。
原 activation 为 `2026-10-15T02:23:03.077645+00:00`，生产 database_id 为
`9ecdb99fd389aa9d51a43765c119ff3c`。不回滚生产数据、不删除历史来修复故障。
连接使用 Supabase 官方公开 CA 与 verify-full；实际认证及错误主机名拒绝测试通过。

## 最新备份与恢复

用单条只读 SQL 的一致性快照导出全部十张学校表，不读取其他业务数据。
最新备份为 `artifacts/phase72-free-production-backup/cloud-state-20261016T223447773Z.json`，
捕获时间北京时间 06:34，包含 29 来源、544 信息、540 通知、725 抓取记录、4 次 worker 记录。
随后在全新隔离本机 PostgreSQL 中恢复，逐表全部行规范化哈希完全一致，
外键、database identity、baseline、dedup 和永久 accepted receipt 均保留。
恢复验证不写生产、不运行 dispatcher，Bark=0；本机测试数据库已正常关闭。

备份 SHA256：`79557f1bda3f644d32295c2d12ffa4e3a01d2b66335f9128ff58864543a45615`。
同目录 `.verification.json` 保存计数和校验结果；旧检查点备份仍保留。
这些文件均在 gitignore 内，没有上传 GitHub，不是持续镜像或付费 PITR。
后续按同样的一致性快照流程手动备份、验证并保留有限份数，不无限积累。

## 费用、容量与运行边界

30 天 720 次、31 天 744 次生产 workflow。真实定时 worker 耗时 444.44 秒；
按含准备时间每轮约 8 分钟估算为 5,760/5,952 分钟，12 分钟上限对应
8,640/8,928 分钟上界。公开仓库标准 runner 免费，没有 larger runner、付费缓存或 artifact。
临时诊断已停用，不计入长期生产频率。依据 [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。

现有 Supabase Free 项目 mantuehuzptactymjpqg，数据库实测 19,369,107 bytes（19.37 MB），
学校关系含索引 4,866,048 bytes（4.87 MB）。Free 数据库额度 500 MB，未购买 IPv4、
compute、PITR 或其他 addon；依据 [Supabase Free](https://supabase.com/pricing)。
尚无完整月增长样本。仅作容量情景：按每个新增 identity 约 9 KB 估算，100 条/日
约 27 MB/月、1,000 条/日约 270 MB/月，不包含其他业务、更新和日志开销，不能当作预测。
接近免费配额时暂停并报告，不升级、不清理无关业务。

GitHub cron 可能延迟或跳过；公开仓库 60 天无活动会停用 schedule。Free 服务
不提供 24/7 SLA，不保证准点推送或全部来源每小时扫描完成；不得把一次成功
写成已经观测连续 24 小时。应检查近期成功任务、stale 和免费容量。
依据 [GitHub schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。

云端：crawler、周期触发、Bark、生产数据库、通知历史与去重。
本地：工作台前端、开发 SQLite、Windows Toast、Reminder/Calendar 和桌面能力。
本地界面不是云数据库镜像；云状态从 GitHub runs 或私有 Supabase 状态读取，
不把管理员凭据放进前端，也不为本阶段新增公网后台。

## 回归、安全与回退

后端最终日志 `artifacts/free-production-final-backend-recheck.log`：965 passed，
其中真实原生 PG/云 worker 专项等 38 passed。前端 30、浏览器 24 和 build 通过；
前端业务代码未改。最新生产 workflow 检查通过，执行配置与已验收版本一致，
只增加触发方式显示名称。实际云端运行与本机模拟 HTTP 测试分别列证据。
真实 .env、备份、测试数据库、原始学校数据和日志均忽略；最终 secret audit
检查 12,279 个文件与历史对象，findings=0；结果见 `artifacts/phase72free-secret-audit.json`，
不记录真实 key、密码或连接串。

故障时先关闭 ZQU_ACTIONS_ENABLED 并开启 ZQU_NOTIFICATION_DRY_RUN，等当前任务结束。
检查安全日志与 DB identity，使用兼容 schema_version=1 的已验证代码，通过新的
revert 提交回退。保留全部提交、数据、baseline、dedup 和 accepted receipt，
不 force push、不回退迁移、不删除通知强行重发。

## 最终六问

| 问题 | 答案 |
| --- | --- |
| A 电脑关机后继续抓取？ | YES；真实 GitHub 定时任务已验证，生产抓取不依赖本机 |
| B 电脑关机后继续 Bark？ | YES；云端投递验收通过，正式自动权限已开启，按原通知策略发送 |
| C 不打开前端仍运行？ | YES；一次性 cloud worker 没有前端或入站请求依赖 |
| D 需要 Docker？ | NO |
| E 需要 Fly？ | NO |
| F 新增固定月费是 $0？ | YES；复用现有 Free 数据库与公开仓库标准 runner |

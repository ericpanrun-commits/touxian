# Phase 7.2-Free Production Launch Report

2026-10-17（Asia/Shanghai）：**手动云端验收通过，真实定时静默验收尚待完成，
当前未开放正式自动 Bark。**

生产仓库为用户自己的 `ericpanrun-commits/touxian`，账号具有 push/admin 权限，
默认分支 main，公开仓库标准 Ubuntu runner。代码已正常推送，包含原始
`1e31cd6` 及后续权限、TLS、来源公平轮转修正；当前生产代码为 `d22d347`。
upstream 仅保留原来源，本次上线不修改它、不切换账号、不索取第三方账号凭据。

`zqu-production` 限定 main，三个 Environment Secrets 均已配置。定时门控
ZQU_ACTIONS_ENABLED=true，ZQU_NOTIFICATION_DRY_RUN=true。最后一个开关仅在
真实 schedule 事件通过后解除；手动运行不会作为定时验收证据。

已实际完成 GitHub Linux runner → Supabase 读写 → 官方抓取 → 原通知策略与
Bark 链路。首轮 4 个来源成功、0 失败、新增 1 条、更新 6 条、静默发送 0；
受控验收发送 1 条明确标记 [test] 的通知，低相关对照被抑制；新的 runner
重跑同一验收发送 0、duplicate_prevented=1，永久 receipt 为 accepted。
所有历史发送和历史 replayable 均为 0，基线未重置。

数据库连接使用 Supabase 官方公开 CA 与 verify-full，真实认证连接成功；
错误主机名拒绝测试通过。没有调用 Docker、Fly、收费资源或修改 billing。

## 上线检查表

| # | 检查项 | 实际结果 |
| --- | --- | --- |
| 1 | Git 修复方式 | 使用 Codex 已安装的 Git 完整路径；仓库级凭据助手复用 gh 安全登录；未重装 Git 或修改系统 PATH/网络 |
| 2 | GitHub 授权账号 | ericpanrun-commits，用户自己的账号，push/admin=YES；没有使用第三方账号 |
| 3 | Push | 成功，正常向前推送 origin/main；原始提交和上游来源保留 |
| 4 | Remote HEAD | 生产功能代码 d22d3471fdff0409273830faca557c73bc1c53ad；后续仅文档提交以远端 main 为准 |
| 5 | Workflow | ZQU official information 已上线，main，workflow ID 386902574，状态 active |
| 6 | Environment | zqu-production，仅 main |
| 7 | Secrets | SUPABASE_DB_URL=YES；BARK_DEVICE_KEY=YES；BARK_SERVER_URL=YES |
| 8 | Variables | PRODUCTION_DATABASE_ID=9ecdb99fd389aa9d51a43765c119ff3c；ZQU_ACTIONS_ENABLED=true；ZQU_NOTIFICATION_DRY_RUN=true |
| 9 | First dry run | 成功，真实 GitHub runner、真实 Supabase 连接，dry_run=true、sent=0；first_dry_run_verified 由 worker 写入 |
| 10 | Source fetch | 首轮 4 个来源成功；新 runner 另验证 3 个来源成功，1 个官网 404、1 个预算到期 |
| 11 | Historical sent | 0 |
| 12 | Historical replayable | 0 |
| 13 | Acceptance Bark | 服务接受 1 条 [test]；永久 receipt=accepted，未再发送测试 |
| 14 | Second acceptance duplicate result | 新 runner，sent=0、duplicate_prevented=1 |
| 15 | Schedule dry run | 等待首轮真实 schedule；手动运行未冒充定时事件 |
| 16 | Production live | 尚未解除 dry-run |
| 17 | Supabase persistence | 新连接、新 runner 持久性通过；学校 10 表一致快照在独立本机 PostgreSQL 恢复，逐表全部行哈希相同 |
| 18 | Production authority | 唯一 GitHub Actions repo/main；数据库 identity、lease、首轮静默门控保护 |
| 19 | 本地 automatic Bark | NO；development 的 automatic_sync/automatic_bark=false，scheduler disabled |
| 20 | Docker | NO；未启动、调用或修复 |
| 21 | Fly | NO；未创建/调用 Fly 资源或远程 builder |
| 22 | 新增固定月成本 | $0；公开仓库标准 runner + 已有 Supabase Free，没有新增收费资源 |
| 23 | Tests | 后端 965；其中原生 PG/云 worker/生产上下文/CA/轮转专项 38；此前前端 30、浏览器 24、build 成功 |
| 24 | Remaining issues | 首轮真实 schedule 静默验收和正式开关；官网旧链接 404；免费 cron 没有准点/24×7 SLA |

以下保留架构、迁移和恢复证据，便于后续审计。

| # | 项目 | 当前结果 |
| --- | --- | --- |
| 1 | Architecture | GitHub 单次 Python worker → 现有 Supabase Free 私有 schema → 原 Bark client；复用 crawler、parser、分类、relevance、policy、dispatcher |
| 2 | GitHub Actions schedule | 每小时 :50，Ubuntu 24.04，12 分钟上限、固定 concurrency；手动验收通过，首次真实 schedule 仍保持 dry-run |
| 3 | Repo private/public | 用户名下新建 PUBLIC 仓库 ericpanrun-commits/touxian；原上游可见性未改变；默认 main |
| 4 | 每月 workflow 次数 | 30 天 720 次，31 天 744 次，另加少量手动验收 |
| 5 | 实际单次 workflow 耗时 | 首轮 worker 439.06 秒；Bark 受控验收 33.86 秒；重复验收 3.82 秒。整个 job 另含 runner/dependency 启动时间 |
| 6 | 每月 Actions minutes | 实测后用次数 × 向上取整单次计费分钟；12 分钟 job 上限对应 8,640/8,928 分钟上界，不是典型耗时预测 |
| 7 | 当前免费 Actions allowance | 用户名下 PUBLIC repo 的标准 runner 免费；未使用 larger runner/cache/artifact |
| 8 | Persistent DB | 复用项目 mantuehuzptactymjpqg，仅 zqu_worker 10 张私有表；不同连接读回确认持久保存 |
| 9 | DB current size | 首轮云端验收后项目 19,008,659 bytes（19.01 MB）；学校关系含索引 4,530,176 bytes（4.53 MB） |
| 10 | DB free quota | 组织再次确认 Free，数据库 500 MB；实际月增长尚未观测，不能保证无限期不满 |
| 11 | Production authority | 仅指定 repo/main 的 schedule 或手动生产任务，通过数据库 identity 和首轮 dry run 才可自动 Bark；DB lease + 原子 claim |
| 12 | Local automatic Bark | 运行中开发后台 automatic_sync=false、automatic_bark=false、scheduler=disabled；连接测试独立可用 |
| 13 | Historical protection | 539 条 suppressed/historical，全体 dedup 历史保留，replayable=0，原 activation 未改变 |
| 14 | Dedup | DB 唯一约束按 channel/item/group/type；sent 永不重发；不明确投递结果终止自动重试 |
| 15 | Bark cloud test | 已成功发送 1 条 [test]，连接和完整策略/队列/dispatcher 合并验收；新 runner 重跑发送 0、重复阻止 1，永久 receipt accepted |
| 16 | Secrets | SUPABASE_DB_URL=YES、BARK_DEVICE_KEY=YES、BARK_SERVER_URL=YES；仅记录配置状态，不显示值、不传给浏览器 |
| 17 | Tests | 最终后端 965 通过，含 CA、生产白名单、来源轮转、迁移与原生 PG 测试；此前前端 30、浏览器 24、build 通过且前端未改；1 项既有依赖弃用 warning，无失败 |
| 18 | Git commit | 原始 1e31cd6、所有历史提交均保留；权限修正 136618c、官方 CA 修正 49d7772、来源轮转修正 d22d347 已推送；尚未给等待定时验收的状态打 stable 标签 |
| 19 | Git status | 本地提交后复核；生产导出、备份、日志、测试数据库和真实 .env 均忽略 |
| 20 | Push | 已正常推送 ericpanrun-commits/touxian/main，原始提交全部保留；无 force、reset、rebase，不推送 upstream |
| 21 | 是否手动创建 Free Supabase project | **否**；复用已有 Free 项目，未创建新项目 |
| 22 | 新增固定月成本 | **$0**；未创建收费资源/升级/改变 billing；仅采用已有 Free DB 和公开仓库标准 runner |

GitHub 费用与额度依据：[官方 Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。
数据库额度依据：[Supabase Free](https://supabase.com/pricing)。

## Hosted 验收证据

| 验收 | 实际 GitHub run | 结果 |
| --- | --- | --- |
| 首轮静默抓取 | [38938718465](https://github.com/ericpanrun-commits/touxian/actions/runs/38938718465) | success，4 个来源成功，sent=0，would_send=0 |
| 唯一 Bark 受控验收 | [38939971818](https://github.com/ericpanrun-commits/touxian/actions/runs/38939971818) | success，sent=1，low_relevance_suppressed=1 |
| 新 runner 重复验收 | [38940266643](https://github.com/ericpanrun-commits/touxian/actions/runs/38940266643) | success，sent=0，duplicate_prevented=1 |
| 当前版本静默恢复 | [38943829381](https://github.com/ericpanrun-commits/touxian/actions/runs/38943829381) | workflow success；worker partial，5 个来源中 3 个成功、2 个未完整完成，dry_run=true、sent=0 |
| 首次真实 schedule | 待 GitHub 自动创建 | 仍保持 dry_run=true，未据此声明 Production Live |

已从云端真实抓取成功的来源：校级通知公告、教务部课程通知、机汽学院教学通知、
创新创业学院通知公告。第一轮限时批次没有扫描完全部 29 个来源；后续按最早到期
轮转，同一到期时间才按优先级排序，避免普通/新闻来源持续被排到后面。
每小时一次是 worker 触发频率，并非全部来源每轮都完成扫描的保证。

当前版本的新 runner 已成功读取持久状态并继续轮转，期末考试、成绩管理和学籍通知
抓取成功；教务通知中的一篇已存档公示在官网返回 404，调停课程来源触及本轮预算。
原正文、历史通知和 dedup 均保留。单条官网失效与限时批次不会通过删除数据来修复，
也不会阻断其他来源。实际日志的 allowlist 计数与 worker 写入 job summary 的计数
来自同一对象；匿名 GitHub 页面未返回渲染后的 summary，未声称读取该页面正文。

所有 29 个原来源、543 个原信息 ID、539 个原通知 ID 均逐项读回确认存在。
17 张 public 业务表逐表 row count 再次与安装前一致；没有对其执行写操作。
first_dry_run_verified=true 由真实成功的静默流程写入，没有手工伪造。
当前共 540 个不同通知 dedup；原有 539 条加上首次抓取发现的 1 条旧信息均被历史
保护，historical_sent=0、historical_replayable=0，受控验收 receipt=accepted。

## 一致性备份与恢复验证

在单条只读 SQL 的一致性快照中导出 zqu_worker 全部 10 张表，不读取其他业务数据。
备份保存在忽略目录 `artifacts/phase72-free-production-backup/`，没有上传 GitHub。
`cloud-state-20261016T173806417Z.json` 包含 29 个来源、544 个信息 ID、540 个通知、
544 个引用和别名、715 条抓取记录，以及 meta/lease/worker/status 状态。
随后在本机新建的隔离测试 PostgreSQL 中恢复，每张表全部行的规范化哈希一致，
外键、数据库 identity、baseline、dedup 和永久 accepted receipt 均保留。
该验证不写生产、不运行 dispatcher，不产生 Bark。验证结果在同名 verification.json。

备份 SHA256：`baf8066a3868e85c3d7701b1cf1e57aee5b8d80f4e64b1403df6a3dc6371c3a4`。
这是一个可恢复的生产检查点，不代表自动日备份或最新云数据的持续镜像。
后续备份同样仅导出学校 schema 的一致性快照，校验后保留有限份数；不购入存储或 PITR。

## Migration 与导入证据

只安装已批准修订文件 `supabase/migrations/20261015042812_zqu_actions_state.sql`。
安装 SHA256 `5F8E5C7202609651E5769A00A0059E121716C80526308C52CF8B3C4952FCB927`。
六项危险 SQL 全为 0，无 destructive warning。Supabase 迁移版本
`20261016114712 zqu_actions_state`。原 17 张 public 表在前、后、导入后逐表计数一致。
没有对 public/auth/storage/extensions 业务对象执行 DDL/DML。

| 数据 | 迁移前导出 | 迁移后 |
| --- | ---: | ---: |
| sources | 29 | 29 |
| items | 543 | 543 |
| source_refs | 543 | 543 |
| aliases | 543 | 543 |
| notification_events | 539 | 539 |
| crawl_runs | 711 | 711 |

SQLite 备份 integrity_check=ok，导入前确认当前 SQLite 与快照一致。543 个 raw
identity 包括尚未投影为文章的发现记录；539 个通知 group 去重后对应 539 个事件。
原激活时间 `2026-10-15T02:23:03.077645+00:00`，生产数据库身份
`9ecdb99fd389aa9d51a43765c119ff3c`。原始快照哈希与远端 meta 一致，所有正文的
独立聚合校验一致。539 条均 suppressed/historical，可领取旧通知 0。

连接器整份请求超过大小上限，拒绝后确认没有写入。随后使用 INSERT-only 分批
导入，每批事务逐行校验，最后数量一致才写 meta，避免半成品启动。没有清表、
覆盖已有行或修改已批准 SQL。实际证据位于忽略目录：
`artifacts/phase72-free-final-migration/cloud-migration-verification.json`、
`cloud-import-verification.json`，以及原始 SQLite 备份。

RLS 已开启且没有 anon/authenticated/PUBLIC 表授权。Supabase advisor 对学校表
给出的 [RLS enabled, no policy](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)
属于本设计的默认拒绝访问；后台角色使用特权连接。未修改其他业务 advisor 发现的
对象。私有 cloud_status 没有浏览器读取通道：本地设置/历史页面仍属于本地后台，
不得拿它当作云端在线证据或认为本地修改会同步生产策略。这个读取通道需另行审计。

## 已完成的仓库交接与安全运行顺序

交接复核发现远端 main 更新至 `b9d77eb`（Android 1.26.1）。已通过本地 merge
`cb09244` 纳入，未重写原有提交或标签。确认学校工作台和 workflow 与 `b722e6f`
完全一致，Android 与根 README 与上游完全一致。当前 HEAD 包含已检查的上游
main，可以正常向前推送；如果所有者再次更新 main，push 前应重新 fetch 并检查，
不得 force push。随后已使用本机现有 ericpanrun-commits 授权推送到用户自己的
同名公开仓库，并完成 Environment、Secrets 和手动云端验收。原上游保持不变。

以下是可复核的运行顺序；初始 Variables 值不是当前状态。当前状态以本报告顶部
和 GitHub 仓库 Variables 为准。前三项和 acceptance 已执行，不需要用户重做。

1. 审阅本地提交，使用现有 ericpanrun-commits 授权 push 到其自己仓库的 main。
   保留全部提交和原上游来源，不 force push，不重写历史。确认 Actions 已有此 workflow。
2. 创建 GitHub environment `zqu-production`，限制 main；Secrets 放在该 environment，
   下表 Variables 放在仓库级（尤其 schedule 的 job 条件需要仓库级 ZQU_ACTIONS_ENABLED）。
   连接从已有 Supabase 项目的 Connect 获取共享 pooler（支持 IPv4，不购买 addon）；
   密码必须 URL encode，TLS 校验开启。连接说明见
   [Supabase 官方文档](https://supabase.com/docs/guides/database/connecting-to-postgres)。

   | 类型 | 名称 | 配置 |
   | --- | --- | --- |
   | Secret | SUPABASE_DB_URL | 已有项目 PostgreSQL pooler DSN，含数据库密码；不要发聊天、写代码或 VITE 变量 |
   | Secret | BARK_DEVICE_KEY | 使用原设备 Key，不重置 baseline |
   | Secret | BARK_SERVER_URL | https://api.day.app，或已有 Bark 服务 |
   | Repository Variable | PRODUCTION_DATABASE_ID | 9ecdb99fd389aa9d51a43765c119ff3c |
   | Repository Variable | ZQU_NOTIFICATION_DRY_RUN | true |
   | Repository Variable | ZQU_ACTIONS_ENABLED | false，验收后才启用 |

3. 在 main 手动运行 workflow，明确 `dry_run=true`。未完成真实首轮前，数据库
   `first_dry_run_verified=false` 保持不变；生产 live 会拒绝执行。核查 job summary、
   worker_runs/cloud_status、来源成功/失败、历史 replay=0 和 Bark sent=0。
   如果没有任何来源成功、would_send 超过保护门槛或出现错误，先修复，勿开 live。
4. 首轮通过后，手动选择 **mode=acceptance、dry_run=false**。此模式只处理明确
   标记 `[test]` 的合成样本，复用 parser/classifier/policy/queue/dispatcher/Bark，
   不抓官网、不领取普通队列。先验证低相关样本被抑制，最多发送 **1 条**，合并
   连接与 pipeline 验收。确认 iPhone 收到并可打开官方来源页面；这不是学校真实
   公告，不需要办理任何事项。测试样本按外键顺序清理，不改历史记录或 baseline。
   本次 Bark 服务已经接受唯一一条测试请求；未把服务成功等同于观察到手机横幅。
   再运行同样的 acceptance（新 runner）：sent=0、duplicate_prevented=1。
   发送前永久记录尝试预算，未知结果/5xx/中断均不自动重试。receipt 保留在
   `zqu_worker.leases` 的 cloud-acceptance-bark-v1，运行日志到期也不移除。
   receipt=reserved/failed_or_unknown 时停止检查，不重置、不删除来强行重发。
   常规任务排除未清理的 acceptance 记录，避免测试遗留触发自动 Bark。
5. 验收通过后仅设 ZQU_ACTIONS_ENABLED=true，保持 ZQU_NOTIFICATION_DRY_RUN=true。
   等待真实 schedule 事件运行并确认 dry_run=true、sent=0，随后才将
   ZQU_NOTIFICATION_DRY_RUN=false。手动 workflow_dispatch 不能替代首次真实 schedule。
   观察 scheduled runs，包括本地后台退出、电脑关机期间；核对跨小时租约、
   抓取、Bark、重启持久和去重。完成之前不宣称 24/7 已验收。

现有免费架构的定时抓取已启用；**正式自动 Bark 尚待真实 schedule 验收**。GitHub schedule 可能
延迟或跳过，公开仓库 60 天无活动会停用 schedule，Free 服务不提供 24/7 SLA。
应检查最近成功运行与 stale 状态；每小时 :50 运行，避开整点。依据
[GitHub schedule 文档](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。
不创建新的提醒/监控付费服务，不自动提交空 commit 来绕过停用规则。

## 数据增长和测试边界

故障时先由所有者关闭 ZQU_ACTIONS_ENABLED，并保持 ZQU_NOTIFICATION_DRY_RUN=true；
确认当前任务已结束后再检查或回退工作流/代码。不回滚数据库，不重装 migration，
不删除通知、baseline 或验收 receipt。需要代码回退时通过新的 revert 提交，保留
当前远端数据和所有历史提交，不 force push。已有 SQLite 完整备份与导出对应
迁移时的初始数据；云端已经产生新数据，另已保存并验证上述一致性学校 schema
备份。不能把旧 SQLite 副本当作最新云数据恢复。

当前学校总关系空间约每个 article identity 7.6 KB（包含来源、通知、运行记录及
索引，不能当作精确单条正文大小）。仅举例：每天新增 100 条、30 天约 23 MB，
每天 1,000 条约 228 MB，均未包含其他业务增长及长正文差异；实际增长要在 hosted
运行后观测。细节 crawl/worker logs 保留 30 天，文章 identity/baseline/dedup 永久
保留。接近 Free 配额时应暂停任务并报告，不升级、不清理无关业务。

最终后端全量日志 `artifacts/free-production-final-backend-recheck.log`：965 passed。
其中原生本机隔离 PostgreSQL 专项、权限上下文、来源轮转和证书测试 38 passed，真实事务/权限/并发/断点测试，
没有 Docker。前端 30 passed、build 成功；浏览器报告
`artifacts/phase72free-browser/browser-results.json`：24 passed，应用错误 0。
浏览器 Bark 由路由 fixture 拦截；PG 链路测试采用模拟 HTTP。这些不替代真正的
GitHub → 学校网站 → Bark → iPhone 验收。

新增验收覆盖：只领取合成样本而不发送普通积压，新连接重跑 0 请求，5xx/未知
尝试不重发，schedule/dry run/未完成首轮静默均拒绝，低相关负例先检查后发送，
中断遗留测试事件不被普通 worker 领取或恢复。本轮上线修复没有新增/重执行
migration，没有重置永久 baseline。学校数据由真实 worker 抓取更新；原 public
业务数据未变化。安装 SQL 的已批准指纹保持不变。

Secret audit 检查当前代码、全部 Git 历史、workflow、README、frontend bundle、
日志与导出，最新一轮共 12,263 个文件/对象，没有真实 Bark Key 或 Supabase service key
命中；真实 .env 与生产数据已确认忽略。锁定依赖与独立 minimal worker 环境一致。

## 当前六个回答

| 问题 | 当前状态 |
| --- | --- |
| A 电脑关机仍抓取？ | **云端任务已启用**；真实定时事件尚待观察，手动云端抓取已通过 |
| B 电脑关机仍 Bark？ | **正式自动推送尚未开放**；唯一测试已经成功，等待真实 schedule 静默验收 |
| C 不打开前端仍运行？ | **是**；云端 worker 不依赖前端或本机后台 |
| D 需要 Docker？ | **否** |
| E 需要新的 Fly 资源？ | **否** |
| F 新增固定月费 $0？ | **是**；未新增任何收费资源 |

这是可复核的安全 checkpoint，Phase 7.2-Free 整体未完成。后续继续上述未验收项，
保留已安装 schema、生产数据、Bark 基线和全部代码/测试结果。

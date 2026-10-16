# Phase 7.2-Free Report

2026-10-16：已完成安全安装与学校数据导入，**尚未完成云端上线验收**。
未调用 Docker、未创建 Fly/收费资源、未修改 billing、未 push，真实 Bark 0 条。
当前阻塞：GitHub 身份对 `sssssjw11/touxian` 只有 READ，无 push/admin 权限；
默认 main 分支没有工作流，生产 Secrets 未配置。需要 push 才能激活 GitHub Actions。

| # | 项目 | 当前结果 |
| --- | --- | --- |
| 1 | Architecture | GitHub 单次 Python worker → 现有 Supabase Free 私有 schema → 原 Bark client；复用 crawler、parser、分类、relevance、policy、dispatcher |
| 2 | GitHub Actions schedule | 本地 YAML 每小时 :17，Ubuntu 24.04，12 分钟上限、固定 concurrency；远端尚未上线 |
| 3 | Repo private/public | 原本就是 PUBLIC，未改变可见性；默认 main |
| 4 | 每月 workflow 次数 | 30 天 720 次，31 天 744 次，另加少量手动验收 |
| 5 | 实际单次 workflow 耗时 | 未运行 hosted workflow，不能用本机测试耗时代替 |
| 6 | 每月 Actions minutes | 实测后用次数 × 向上取整单次计费分钟；12 分钟 job 上限对应 8,640/8,928 分钟上界，不是典型耗时预测 |
| 7 | 当前免费 Actions allowance | 已有 PUBLIC repo 的标准 runner 免费；未使用 larger runner/cache/artifact。当前身份无法读取所有者 private 额度使用情况 |
| 8 | Persistent DB | 复用项目 mantuehuzptactymjpqg，仅 zqu_worker 10 张私有表；不同连接读回确认持久保存 |
| 9 | DB current size | 导入后全项目 18,508,947 bytes（18.51 MB）；学校关系含索引 4,120,576 bytes（4.12 MB） |
| 10 | DB free quota | 组织再次确认 Free，数据库 500 MB；实际月增长尚未观测，不能保证无限期不满 |
| 11 | Production authority | 仅指定 repo/main 的 schedule 或手动生产任务，通过数据库 identity 和首轮 dry run 才可自动 Bark；DB lease + 原子 claim |
| 12 | Local automatic Bark | 运行中开发后台 automatic_sync=false、automatic_bark=false、scheduler=disabled；连接测试独立可用 |
| 13 | Historical protection | 539 条 suppressed/historical，全体 dedup 历史保留，replayable=0，原 activation 未改变 |
| 14 | Dedup | DB 唯一约束按 channel/item/group/type；sent 永不重发；不明确投递结果终止自动重试 |
| 15 | Bark cloud test | 未执行，真实发送 0；需 hosted dry run 后进行最多 1 连接测试 + 1 受控自动链路测试 |
| 16 | Secrets | 本地真 Key 仍在忽略的 server/.env；云端使用 GitHub Secrets，尚未上传；不传给浏览器 |
| 17 | Tests | 后端 954、前端 30、浏览器 24 通过；生产 build、新增 Python 静态检查、YAML 结构测试通过；1 既有依赖 deprecation warning |
| 18 | Git commit | 本报告所在本地功能提交；可用 git log -1 查看准确 hash，不给未上线状态打 stable 标签 |
| 19 | Git status | 本地提交后复核；生产导出、备份、日志、测试数据库和真实 .env 均忽略 |
| 20 | 是否需要 push | **是**；遵守用户不擅自 push 的要求，所有者还需配置 environment/Secrets |
| 21 | 是否手动创建 Free Supabase project | **否**；复用已有 Free 项目，未创建新项目 |
| 22 | 新增固定月成本 | **$0**；未创建收费资源/升级/改变 billing；仅采用已有 Free DB 和公开仓库标准 runner |

GitHub 费用与额度依据：[官方 Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。
数据库额度依据：[Supabase Free](https://supabase.com/pricing)。

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

## 所有者上线步骤

1. 审阅本地提交，使用有写权限的身份 push 到既有 repo 的默认 main；当前代理
   不 push、不切换账号、不改变仓库可见性。确认 Actions 已有此 workflow。
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
4. 首轮通过后，进行受控真实验收：最多 1 条标记清晰的云连接测试和 1 条自动
   pipeline 测试，确认 iPhone 收到及点击官方原文；重跑同一事件/新进程不能重发。
   测试前检查待发送队列，避免用无约束 live run 代替受控测试。
5. 验收通过再设 ZQU_NOTIFICATION_DRY_RUN=false、ZQU_ACTIONS_ENABLED=true。
   观察多个 scheduled runs，包括本地后台退出、电脑关机期间；核对跨小时租约、
   抓取、Bark、重启持久和去重。完成之前不宣称 24/7 已验收。

现有免费架构可以在电脑关闭后运行；**当前还未激活**。GitHub schedule 可能
延迟或跳过，公开仓库 60 天无活动会停用 schedule，Free 服务不提供 24/7 SLA。
应检查最近成功运行与 stale 状态；计划每小时 :17 减少整点拥堵。依据
[GitHub schedule 文档](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。
不创建新的提醒/监控付费服务，不自动提交空 commit 来绕过停用规则。

## 数据增长和测试边界

当前学校总关系空间约每个 article identity 7.6 KB（包含来源、通知、运行记录及
索引，不能当作精确单条正文大小）。仅举例：每天新增 100 条、30 天约 23 MB，
每天 1,000 条约 228 MB，均未包含其他业务增长及长正文差异；实际增长要在 hosted
运行后观测。细节 crawl/worker logs 保留 30 天，文章 identity/baseline/dedup 永久
保留。接近 Free 配额时应暂停任务并报告，不升级、不清理无关业务。

后端全量日志 `artifacts/phase72free-full-backend.log`：954 passed。
其中原生本机隔离 PostgreSQL 专项 27 passed，真实事务/权限/并发/断点测试，
没有 Docker。前端 30 passed、build 成功；浏览器报告
`artifacts/phase72free-browser/browser-results.json`：24 passed，应用错误 0。
浏览器 Bark 由路由 fixture 拦截；PG 链路测试采用模拟 HTTP。这些不替代真正的
GitHub → 学校网站 → Bark → iPhone 验收。

Secret audit 检查当前代码、全部 Git 历史、workflow、README、frontend bundle、
日志与导出，共 10,035 个文件/对象，没有真实 Bark Key 或 Supabase service key
命中；真实 .env 与生产数据已确认忽略。锁定依赖与独立 minimal worker 环境一致。

## 当前六个回答

| 问题 | 当前状态 |
| --- | --- |
| A 电脑关机仍抓取？ | **尚不能**；工作流未 push/配置/运行 |
| B 电脑关机仍 Bark？ | **尚不能**；同上，首轮静默与真实云推送未验收 |
| C 不打开前端仍运行？ | **云端尚未启用**；设计不依赖前端 |
| D 需要 Docker？ | **否** |
| E 需要新的 Fly 资源？ | **否** |
| F 新增固定月费 $0？ | **是**；未新增任何收费资源 |

这是可复核的安全 checkpoint，Phase 7.2-Free 整体未完成。后续继续上述未验收项，
保留已安装 schema、生产数据、Bark 基线和全部代码/测试结果。

# Supabase Migration Safety Audit

**最终结论：修订版 SAFE TO APPLY；用户已明确确认，安装与学校数据导入已验证。**

下方审计主体保留执行前快照；2026-10-16 实际执行与核对结果见文末。

本审计只评价下列安装迁移的安全性，不代表 Phase 7.2-Free 已上线，也不授权
数据导入、执行 worker、推送 GitHub 或发送 Bark。

- 文件：`supabase/migrations/20261015042812_zqu_actions_state.sql`
- 审计版本 SHA256：`5F8E5C7202609651E5769A00A0059E121716C80526308C52CF8B3C4952FCB927`
- 目标 Free 项目：`mantuehuzptactymjpqg`
- 唯一目标 schema：`zqu_worker`，不向 public 暴露。
- 原稿结论：**NOT SAFE TO APPLY**，不应直接提交含 DROP POLICY 的原稿。

## 执行前远程快照和旧请求未执行证据

原挂起调用已结束，返回 `Invalid or expired requestState`。随后只读检查确认：

- `zqu_worker` schema 不存在。
- 原稿中的 `public.zqu_worker_status` 不存在。
- 远程 migration 历史中没有 `zqu_actions_state`。
- 数据库大小仍为 14,191,763 bytes。

在该执行前审计期间没有重新调用 apply_migration，没有远程 DDL/DML、数据导入或清理操作。
远程检查仅使用 SELECT 和 migration-history 列表。未读取 auth/storage/vault
业务内容或 secret 值。

现有非系统 schema 清单：auth、cron、extensions、graphql、graphql_public、
information_schema、private、public、realtime、storage、supabase_migrations、vault。
修订迁移不对这些 schema 发出对象创建、修改或权限变更语句。

| 已有 schema | 已核对的普通表数量 | 本次处理 |
| --- | ---: | --- |
| public | 17 | 全部保留，不触碰 |
| auth | 27 | 全部保留，不触碰 |
| storage | 8 | 全部保留，不触碰 |
| cron | 2 | 不触碰已有任务或记录 |
| realtime | 3 | 不触碰 |
| supabase_migrations | 1 | 仅只读查看历史；本轮不写入 |
| vault | 1 | 仅核对表名；不读取 secrets 内容 |
| extensions、graphql、graphql_public、private | 0 张普通表 | 不触碰其中已有函数、类型、视图或扩展 |

public 的 17 张现有业务表：
`ai_analysis_jobs`、`ai_worker_heartbeats`、`categories`、`ielts_daily_plans`、
`ielts_listening_attempts`、`ielts_plan_profiles`、`ielts_reading_sessions`、
`ielts_speaking_results`、`ielts_study_sessions`、`ielts_user_preferences`、
`ielts_writing_drafts`、`ielts_writing_reviews`、`ielts_writing_submissions`、
`quick_entries`、`savings_goals`、`transactions`、`user_settings`。

## 原稿危险 SQL：完整清单

### 1. 删除并重建策略

```sql
DROP POLICY IF EXISTS zqu_safe_status_read ON public.zqu_worker_status;
```

- Schema：`public`。
- 对象：表 `zqu_worker_status` 上的策略 `zqu_safe_status_read`。
- 原用途：为了重复安装时能够重新 CREATE 同名策略。
- 直接删除的对象：一条 RLS policy，不直接删除业务行。
- 风险：首次安装根本不需要删除策略；如果同名对象已经存在，会改变其访问控制。
  不能仅以“表名看起来属于学校项目”替代归属检查。
- 实际远程影响：无，远程没有该表或本次迁移记录。
- 修订：移除整个 public 状态表/策略安装段，状态表改为
  `zqu_worker.cloud_status`，保持私有。无需任何 DROP。

### 2. 来源引用表的级联外键

```sql
CREATE TABLE IF NOT EXISTS zqu_worker.source_refs (
    item_id text NOT NULL REFERENCES zqu_worker.items(id) ON DELETE CASCADE,
    source_id text NOT NULL REFERENCES zqu_worker.sources(id),
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(item_id,source_id)
);
```

- Schema：`zqu_worker`。
- 对象：`source_refs.item_id` 到 `items.id` 的外键。
- 原用途：以后删除一条 item 时自动清除其来源引用。
- 是否立即删除：否，这是 CREATE 中的约束定义，不是 DELETE DML。
- 原稿最坏后果：未来删除一个学校 item 时，其所有 source_refs 自动删除。
  不直接涉及 public/auth/storage 等已有业务表，但这种自动删除不是安装所必需。
- 实际远程影响：无，该 schema 没有创建。
- 修订：移除 ON DELETE CASCADE，采用 PostgreSQL 默认 NO ACTION；不会自动删除引用行。

### 3. URL alias 表的级联外键

```sql
CREATE TABLE IF NOT EXISTS zqu_worker.aliases (
    url text PRIMARY KEY, item_id text NOT NULL REFERENCES zqu_worker.items(id) ON DELETE CASCADE
);
```

- Schema：`zqu_worker`。
- 对象：`aliases.item_id` 到 `items.id` 的外键。
- 原用途：以后删除学校 item 时自动清除其 URL aliases。
- 是否立即删除：否。
- 原稿最坏后果：未来删除学校 item 会自动删除对应 aliases；不涉及其他业务表。
- 实际远程影响：无。
- 修订：移除级联，采用默认 NO ACTION。运行时合并新占位记录的代码已同步调整，
  必须先复制引用、重定向 alias，且占位记录没有正文和通知历史，才可进行带 WHERE
  的学校内部占位合并；不会依靠级联删除历史。

### 其他被要求检查的语句

原稿没有 DELETE DML、TRUNCATE、UPDATE without WHERE、ALTER ... DROP、
DROP TABLE、DROP SCHEMA 或 DROP ... CASCADE。
原稿的全部危险命中就是上面 1 条 DROP POLICY 和 2 处 ON DELETE CASCADE。

## 修订迁移的完整操作范围

将创建独立 schema 和以下 10 张表，全部使用 `CREATE TABLE IF NOT EXISTS`：

| Schema | 表 | 用途 |
| --- | --- | --- |
| zqu_worker | meta | 身份、历史基线、导入标记和策略 |
| zqu_worker | sources | 来源配置与健康状态 |
| zqu_worker | items | 学校公开信息、分类、相关度和日期 |
| zqu_worker | source_refs | 文章与来源引用 |
| zqu_worker | aliases | URL identity / alias |
| zqu_worker | notification_events | 持久通知状态、去重与领取记录 |
| zqu_worker | leases | worker 互斥租约 |
| zqu_worker | crawl_runs | 抓取结果记录 |
| zqu_worker | worker_runs | 单次任务结果记录 |
| zqu_worker | cloud_status | 私有运行状态时间戳 |

另外使用 `CREATE INDEX IF NOT EXISTS` 创建 6 个业务索引；主键及唯一约束会创建
各自必要的索引。所有外键只引用 `zqu_worker` 内的表，均为默认 NO ACTION。
不创建或修改任何持久 function、trigger、policy、cron job 或 extension。

权限与 RLS 操作只针对上述学校 schema/表：

- 设置学校归属 comment 标记。
- 开启学校表 RLS。
- 撤销 PUBLIC/anon/authenticated 对学校 namespace/表的权限。
- 向 service_role 授予学校 namespace 使用和学校表权限。
- 不修改角色本身，不设置全局 default privileges，不改变已有业务对象的 ACL/RLS。

重复安装只允许复用带 `zqu-workspace:actions-state:v1` 标记的学校 namespace/表。
若同名 schema 或表没有该标记，或 namespace 内存在额外业务关系/函数，迁移会
先拒绝执行，不会通过重建、清空或接管权限解决冲突。

**将修改哪些当前已有对象：无。** 远程当前没有学校 schema/表；现有其他业务
对象不属于迁移操作范围。将来重复执行时，仅重申本迁移拥有的学校对象注释、
RLS 和权限，不改写已导入的业务行或基线。

## 修订版 destructive SQL 和最坏情况

修订文件（去掉注释后）中：

- DROP：0。
- DELETE：0。
- TRUNCATE：0。
- UPDATE（包括无 WHERE）：0。
- ALTER ... DROP：0。
- CASCADE：0。
- public/auth/storage/extensions 等 schema 的对象 DDL：0。

不会删除任何现有表、schema、function、trigger、policy 或业务行。
最坏情况是权限/命名冲突或版本不兼容导致安装失败；应以单一事务执行安装。
迁移不运行数据导入、不启动 worker，也不执行运行期日志保留清理。

因此可改为且已改为完全 non-destructive 的安装迁移。状态表没有公开读取接口；
未来前端状态读取方式需要单独审计，不能为此把 service-role/数据库写凭据交给浏览器。

## 测试证据

仅使用本机独立 PostgreSQL 17.6 测试实例，无 Docker，无远程 mutation。
测试连接被限制到 127.0.0.1 与专用测试用户/数据库；每个测试创建全新数据库，
不使用 DROP SCHEMA、DROP TABLE 或级联清理重建测试环境。

最终结果：**23 passed**，一个既有依赖 deprecation warning。
日志：`artifacts/supabase-migration-safety-final.log`。

其中验证了：

- SQL 静态检查：没有全部六类破坏式操作，没有跨 schema DDL。
- 初次安装及重复安装，已导入学校数据、通知历史和 baseline 完全保留。
- 非本项目同名 schema/table 与额外业务对象：拒绝安装，保留原数据及 ACL。
- public/auth/storage/extensions 代表性既有表：两次迁移后行、列定义、RLS 和权限均不变。
- 所有外键目标均在学校 schema 内，删除动作全部为 NO ACTION。
- 不依赖 CASCADE 的新占位合并保留原通知历史及 aliases。
- dry run 不发 Bark，新连接重复运行不重发，数据库领取原子化。

新增/相关 Python 静态检查通过；`git diff --check` 通过。

## 用户确认后的执行记录（2026-10-16）

用户明确批准上述 SHA256 对应的修订版，要求继续 Phase 7.2-Free。
执行前再次核对文件指纹及全部六项危险语句计数：均为 0。SQL 文件未再修改。
仅本修订版安装成功；Supabase 记录版本 `20261016114712`，名称
`zqu_actions_state`。旧版没有执行，没有再次出现 destructive SQL warning。
Supabase 自己记录迁移历史；本项目 SQL 仅操作 `zqu_worker`。

安装后立即验证 10 张学校表、RLS 和 ownership marker。原有 17 张 public
业务表全部存在，安装前、安装后、数据导入后的逐表行数完全一致。
未修改 public/auth/storage/extensions 业务对象或数据。

当前 SQLite 与已校验快照完全一致，备份 integrity_check=ok。连接器拒绝整份
数据请求（Request body is too large），确认没有写入后，改为 INSERT-only 分批
导入：每批事务校验逐行内容，已有冲突拒绝覆盖；最后检查全部数量后才写入
meta 激活屏障。中断时 worker 因 meta 不存在拒绝启动；可重试相同数据，不清库。
这没有修改已审计的安装 SQL，也没有增加任何 DDL。

| 数据 | SQLite 导出 | 远端导入 |
| --- | ---: | ---: |
| sources | 29 | 29 |
| items | 543 | 543 |
| source_refs | 543 | 543 |
| aliases | 543 | 543 |
| notification_events | 539 | 539 |
| crawl_runs | 711 | 711 |

539 条通知均为 suppressed/historical，539 个 dedup key 和 group 保留，
可重放历史通知为 0。activation 原值 `2026-10-15T02:23:03.077645+00:00`。
快照 SHA256 `3abd6dc9a2b124232f8184dc0b0b183cd5a60d62cccf2cff90cecade8aeace9b`。
所有文章正文在独立查询中校验一致；anon/authenticated/PUBLIC 表权限数为 0。

远端 `first_dry_run_verified=false`、worker_runs=0、cloud_status=0，尚未假设
首轮 GitHub 验证成功。实际 Bark 发送 0 条。数据库 18,508,947 bytes，学校表及
索引 4,120,576 bytes。原始备份和逐表证据保存在忽略目录
`artifacts/phase72-free-final-migration/`，不提交生产数据。

新增连接器导入原子回滚、分批中断恢复、内容冲突拒绝与激活保护测试通过。
当前完整验证：后端 954、前端 30、浏览器 24 项通过，生产构建通过。
此前 23 项审计结果仍保留，当前学校 PostgreSQL 专项为 27 项通过。

**结论：修订版 SAFE TO APPLY，已按批准范围安装并验证。**
GitHub 默认分支尚无工作流，当前身份没有 push/admin 权限；云端 dry run 与
真实手机验收尚未执行。后续停点见 `PHASE-7.2-FREE-REPORT.md`。

# Eric OS Backend Stability Hardening

日期：2026-11-03。**本轮代码收口与隔离验证完成；生产发布未执行。** 原运行工作区和业务数据未修改。没有新增业务功能、修改 UI 结构/样式、推进 Phase 6 或引入基础设施。

## 1. 审计范围、基线和原有风险

实际源工作区：`C:/Users/ericp/Desktop/reference/touxian`，源 HEAD 为 `4e66bbe8a192c77677cc1e3bbcc989ca5a7db4d2`。当前实现还包含大量未提交文件，故不能仅审计 HEAD，也不能使用旧恢复副本。本次隔离仓库为 `D:/Eric OS/backend-stability-hardening`，分支 `backend/stability-hardening`。当前源工作区快照保存在基线提交 `5cb6366` / 标签 `stability-audit-baseline`。

**审查命令：`git diff stability-audit-baseline`。** 基线快照中的既有 UI 等修改不属于本轮；不要把整条分支相对原 origin 的差异当成本轮补丁。交付补丁仅比较该快照之后的稳定性修改。

实际审计了 SQLite migrations 001–026、PersonalStore/FK/事务、Course、Matter（当前持久化名为 event）、Lecture、Textbook、Evidence/来源校验、Review、Learning/Retest、Capture/附件、OCR/Vision/AI 边界、Publisher/CloudProjection、CloudSync/Commands、Notes 接收与归档、PWA 传输队列，以及对应测试。通过 Supabase 只读工具核对了线上迁移、关键函数、schema、任务状态和部署 Functions。Integration/IELTS 以线上实际定义及 IELTS 当前同步实现为准。

| 优先级 | 原有风险 | 实际影响 |
| --- | --- | --- |
| P0 | Intake 分类后吞掉 Learning 异常 | 投影失败仍保留分类后的成功/完成状态 |
| P0 | Notes 入口引用缺失的 archive.ts | 初始 Node 测试 128 通过、1 失败，无法重新构建真实 Function 入口 |
| P0 | IELTS INSERT 触发器吞掉 outbox 写失败 | 来源行保存成功但 Integration 事件可能永久丢失 |
| P1 | Capture/Command 没有 attempts 上限 | 崩溃或持续异常可无限重领 |
| P1 | worker 不及时消费租约续期失败 | 失去租约的旧 owner 仍可能继续主体工作 |
| P1 | 新 AI 时间戳覆盖人工预览 | PostgreSQL 红测复现人工确认内容被覆盖 |
| P1 | 空 Vision completed 与乱序 stage | 空分析被当作成功；同时间戳的 pending 可压过 completed |
| P1 | 永久错误进入自动重试 | schema/权限错误普遍变成 503；Integration 永久 4xx 仍重排 |
| P1 | PWA 持续网络失败无限尝试 | 没有单轮重试耗尽状态 |
| P1 | Learning 后台通用异常只写日志 | capture 或本地 artifact 可能仍显示待处理/running |
| P2 | 日志缺少关联 ID | 不能直接从原件/任务定位异常 |
| 测试 | 5 个旧 Vision 测试调用过期接口 | 测试未调用当前纯投影函数 project_snapshot |

## 2. 实际修改

- **Intake / Learning：** Learning 投影异常进入现有持久化 failed，保留原件、收据与本地 retry 入口；使用 learning_projection_failed。Learning runner、后台 capture 和本地笔记异常写 needs_review/interrupted 和安全错误；状态更新避免覆盖后来已确认的 capture，笔记沿用 NoteWork 的来源 CAS。
- **Cloud worker：** 仅网络、429、5xx 和 SQLite busy/locked 等明确临时异常可重放；schema、权限、校验、文件版本变化与未知程序异常停止自动重排。续租和主体任务并行监测；续租失败取消旧主体并消费所有子任务异常。非 JSON 网关错误和错误形状异常保留 HTTP 状态，避免错误解析本身掩盖真实失败。
- **云端任务：** Capture/Command 最多 5 次总尝试（初次 + 最多 4 次重试），复用 attempts/error/next_attempt_at 及原退避。最后一次崩溃后，下一次 claim 将其置为 failed/retry_exhausted；缺失租约的孤立 processing 可重新领取。成功移交本地且已有 raw_item_id 的 capture 不会被误当未接收任务重领。
- **诊断时间：** 两类云端任务各增加 last_attempt_at、completed_at；触发器更新，续租不修改最后尝试时间。DTO 暴露诊断字段，继续隐藏 lease_token 和内部 request_hash。
- **人工优先 / Vision：** newer 自动结果保留已有 human 预览；允许更新模型元数据。HTTP 与数据库同时拒绝空 completed 或携带失败原因的 completed；相同分析时间的 pending/failed 不能回退已经 completed 的状态。历史结果继续追加，重复 payload 继续复用既有哈希身份。
- **Notes 归档：** 从部署的 eric-notes-ingest v13 bundle 恢复缺失 archive 模块。保留原有哈希、分段、长期保存确认和重复提交协议；不是新增上传能力。
- **Integration / IELTS：** 永久 3xx/4xx（408/429 除外）直接 failed；临时失败继续沿用 max_attempts（默认 6）和现有退避。Reading/Speaking 来源与 outbox 同事务确认；outbox 失败让来源写入返回错误。已检查 IELTS 同步：失败保留本地 record/error metadata，成功后才记 synced；重送沿用 user_id/record_key，未新增重试系统。
- **PWA 传输：** 仅修改 client.js 传输逻辑。每个连接/打开周期最多 5 次失败尝试，之后 failed；轮询不能清零预算。重新联网/打开应用恢复同一记录，累计 attempts 不清零，保留原 ID、文字与文件。schema 错误进入现有 needs_confirmation。没有更改 JSX、CSS 或页面交互结构。
- **日志：** 复用 mobile-collector.log，增加 action/job_id/entity/status，保留时间、异常类型和文件名/函数/行号；不记录异常正文、Authorization、key、token 或完整机器路径。即使“写失败状态”本身发生 DB 错误，Intake/Learning 的任务完成回调也消费异常并记录关联 ID，避免无人消费的后台异常。Integration 继续利用 delivery 的 id/status/attempt_count/last_error/updated_at 定位。
- **测试：** 补充故障注入、真实 SQL、实际归档模块、诊断 DTO 和双引擎 IndexedDB 传输测试；修复旧 Vision 测试对当前 project_snapshot 的调用，保留原断言。

## 3. 修改原因与保留的现有机制

以上修改针对实际失败路径，目标是消除假成功、无界重试、永久 processing 和人工覆盖，不重写业务流程。

- PersonalStore 写事务已有 BEGIN IMMEDIATE、FK、FULL synchronous 和异常 rollback。
- cloud_command_receipt 与纯领域修改同事务；Intake owner/request_id 收据与内容 hash、School 请求签名、Learning capture/artifact/exercise/attempt 的既有 UNIQUE 继续提供后端幂等。
- Review 继续追加版本、显式采用 revision，并验证来源和课程边界；自动流程不把建议当人工确认。
- 付费 AI 的未知结果继续 unknown/interrupted，保留尝试与费用记录；不会为追求自动恢复而盲目重放未知付费调用。
- 原件先持久化再确认接收；Notes 长期归档未确认时不清理 transit。文档、School、AI、Inbox 沿用重启恢复路径。
- PWA 未确认上传不会释放本地文件；云端原件接收独立于 Windows worker 在线状态。

## 4. Schema / migration 影响

两个新文件均由 Supabase CLI migration new 创建：

1. `20261103012218_eric_app_stability_hardening.sql`：两张既有云端任务表共四个可空时间列、一个生命周期触发器函数及两个表触发器；替换 claim/finish、Vision 接收和人工预览保护函数。旧行时间不伪造补填。
2. `20261103013801_integration_delivery_stability.sql`：替换已有失败分类和两个 IELTS outbox 触发器函数。存在 shared schema/函数才执行，不创建 Integration 业务表，独立 Eric 安装可以保持原样。

SQLite SCHEMA_VERSION 仍为 26。无历史行删除、关系重排、新 cascade 或扩大 anon/authenticated 权限。SQL migration 含完整 CREATE OR REPLACE 函数定义是 DDL 要求，实际行为差异集中于上述守卫与状态转换。

生产项目 `mantuehuzptactymjpqg` 由 Eric OS、LifeLedger、IELTS 共用。当前仓库不是完整共享迁移历史；旧 Vision 本地文件名与线上版本 `20261102083852` 不一致，本轮未修改旧迁移。**不能全量 reset 或盲目 db push。** 本轮未远程 apply migration、deploy Function、重启生产 worker 或改生产数据。

## 5. 测试命令与结果

应用目录：`desktop/attention-desk`；PowerShell 7；Python 使用原项目的 `desktop/.venv/Scripts/python.exe`。测试环境为 PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1、PYTEST_DISABLE_PLUGIN_AUTOLOAD=1。既有 conftest 禁止无人值守轮询和 Bark 推送。

真实 PostgreSQL 在 127.0.0.1:65438 的专用测试 cluster；ERIC_APP_TEST_DSN 指向 postgres，ZQU_TEST_PG_DSN 指向 zqu_test/zqu_actions_tests。测试自己创建合成数据库；未连接生产 DSN。

| 范围 / 命令 | 结果 | 证据（D:/Eric OS/test-data 下） |
| --- | --- | --- |
| 全量基线 pytest | 1703 passed、5 failed、44 skipped；5 个失败均为旧 Vision 测试接口 | stability-baseline-full.xml |
| 最终全量：两套 PostgreSQL DSN 启用后 `python -m pytest -q -p no:cacheprovider` | **1805 passed、1 skipped**，237.56s；包含最后的状态写失败与错误 body 守卫 | stability-acceptance.xml |
| HTTP 边界专项：backend_stability + cloud_sync_worker | **29 passed** | stability-http-final.xml |
| 最后的状态写失败回调 + Intake/Learning/Note recovery 专项 | **66 passed** | stability-final-status.xml |
| 云端 PostgreSQL：`python -m pytest server/test_cloud_sync_sql.py` | **32 passed**；其中原人工覆盖红测已由 failed 转为 passed | stability-sql-vision.xml |
| Integration/Actions：`python -m pytest server/test_integration_stability.py server/test_actions_worker.py` | **56 passed** | stability-actions-integration.xml |
| Learning/Note recovery/并发人工确认专项 | **56 passed** | stability-learning.xml |
| `node --test tests/*.test.mjs` | **133 passed** | 工具输出；包含 DB 假成功、archive、DTO、Vision guards |
| 线上实际 Integration/IELTS SQL 快照在隔离库回放，注入事件/feedback 写失败和 stale worker | **5 passed** | stability-shared-replay.xml；可重跑 stability-shared-source/test_snapshot.py |
| 部署的 dispatcher / IELTS API 快照 + 原有 4 个 Vision 测试的显式 Node 兼容执行 | **8 passed**；不是 Deno runtime 验收 | stability-shared-source/api-regression.test.mjs |
| IELTS 当前源仓库既有 sync、syncErrors、schemas 测试，关闭缓存 | **26 passed** | stability-ielts-client.json |
| `node tests/cloud-delivery-stability-browser.mjs`，ERIC_WEB_ENGINE 分别 chromium / webkit | **两引擎均通过**：5 次耗尽；保留文件；关页重开；联网恢复同 ID；丢 commit 响应不二次上传；永久 schema 错误停止 | stability-browser/chromium-transport.json、webkit-transport.json |
| `node scripts/build-cloud-functions.mjs` | 两个 Function bundle 构建成功 | 应用 artifacts/cloud-functions（忽略生成物） |
| `git diff --check` | 通过 | 无格式化整个代码库 |

原工作区 11 个受影响既有文件已逐一与审计基线只读比较，内容均未变化。本轮 diff 没有 JSX、CSS、HTML、依赖清单或 lockfile 修改。交付补丁为 `D:/Eric OS/backend-stability-hardening.patch`，仅包含基线快照之后的 19 个后端/传输/测试/报告文件；使用 `git apply --check` 检查对当前原工作区的可应用性，不自动覆盖运行代码。

完整后端回归覆盖 Course（test_courses/course_school/workspace_migration）、Lecture/Textbook（test_school/textbook_library）、Evidence/Review（test_extraction_review/school_reliability/source_correction/review_queue/review_recovery）、Learning/Retest（test_learning/learning_analysis/learning_cycle）、Today（planning/reminders 与 cloud-app-api Today 测试）、Capture/Notes（eric_inbox/collector/notes_*）、Integration、IELTS。单项协议测试不被用于宣称全 UI 验收。

唯一跳过项是 WeChat real shard mapper，需要未安装的隔离 DB crypto runtime；不是业务测试失败。另有既有 FastAPI TestClient 弃用提示。沙箱内原生依赖曾被应用程序控制阻止，已中止该诊断并经自动审批改用原 Python 环境，没有把停滞/收集失败计为通过。

## 6. 数据审计、未解决风险和验证边界

- 本地生产 SQLite 全程只读：quick_check=ok、foreign_key_check=[]。当时有 10 courses、2 lectures、3 lecture_note、4 lecture_section、2 textbooks、5 documents、13 review_artifact、13 learning_capture、27 learning_attempt、21 learning_exercise、2 learning_followup、15 inbox_receipt。
- 进一步只读聚合：lecture_section 与 textbook 课程、exercise 与 topic/lecture 课程、capture 与 lecture 课程、capture 与 snapshot 原件、lecture_note 与 attachment 原件，**六类 mismatch 均为 0**。没有擅自清理或修复历史数据。
- 云端当时 2 captures（completed 1、needs_confirmation 1）、1 completed command，无未接收的 stale processing；Integration 4 succeeded deliveries。event dedup key、event/consumer delivery、IELTS feedback event_id 均无重复，deliveries/feedback 无 orphan，stale delivery=0。
- **尚未发布：** 当前生产仍运行旧定义。本报告证明补丁和隔离验证完成，不宣称生产已经获得这些修复。
- **完整 PWA 构建有既有 UI 缺陷：** Pages.jsx 已在基线中引用缺失的 ../personal/EricInbox，实际源只有 src/EricInbox.jsx。build:web 因此失败，closeBundle 的 missing index.html 又掩盖了根因。按本轮禁止改 UI 的边界保留未改。双引擎验证单独打包真实 client.js，用合成 auth/API 和真实 IndexedDB；不能据此宣称整个生产 PWA UI build 成功。
- **实机/外部依赖：** 未使用真实 owner 登录、蜂窝网络、物理 iPhone/iPad 或真实付费 AI；测试模拟 provider、断网与丢响应。Chromium/WebKit 是引擎覆盖，不等于所有物理设备验收。
- **遗留 LAN Collector：** 未发送表单不等于已确认接收；旧 LAN 页面关闭可能丢失尚未提交的输入，本轮没有为其另造离线系统。持久化离线发送保证适用于既有云端 PWA outbox；后端收到的原件/收据由本轮测试覆盖。
- **历史缺失事件：** 新触发器保证后续来源/outbox 原子性；不会自动补造此前 warning 可能丢失的历史事件，需按明确来源键另行核对，避免重复推送。
- **共享项目既有 advisor：** deny-by-default 服务表的 RLS/no-policy INFO 保持原状；另有 extension in public、authenticated 可执行既有 definer 函数、密码保护提示。未修改其他应用或扩大权限。参考 [extension](https://supabase.com/docs/guides/database/database-linter?lint=0014_extension_in_public)、[definer](https://supabase.com/docs/guides/database/database-linter?lint=0029_authenticated_security_definer_function_executable)、[password protection](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection)。
- 浏览器没有 Browser 插件，按 frontend-testing-debugging 技能使用现有 Playwright。只验证传输协议，没有进行 UI 重设计。

## 7. 推荐下一步与验收矩阵

生产落地时，先保留当前函数定义与备份，逐条审查并仅应用这两个新迁移，再发布两个已构建的 Function bundle 和 worker/PWA transport。共享 Integration 迁移涉及 IELTS 写失败时的原子行为，需与 IELTS 同步方一起观察 error -> retry -> synced。已有累计 attempts>=5 的未接收任务会在下一次 claim 进入 failed，不会被静默丢弃或删除。

由 UI 维护任务单独修复现有导入错误后，再运行完整 PWA 构建和实机断网验收。若回退代码，保留本轮新增时间列和历史任务/文件；不要为回退删除数据。按 event/source key 核对历史 IELTS outbox 缺口，确认后再决定是否补发。

| 完成标准 | 本轮证据 |
| --- | --- |
| 失败可见、原因可查 | Intake/Learning 持久状态，云端 error/attempts/timestamps，安全关联日志与故障注入 |
| 网络恢复不丢数据 | Chromium/WebKit 保留真实文件字节、关闭再开、同 ID 恢复、丢确认不重传 |
| 重复请求不重复创建 | SQLite 事务收据、真实 SQL UNIQUE/claim、Vision hash、Notes archive、Integration/IELTS 快照回放 |
| worker 崩溃不会永久 processing | lease 过期重领、失租取消、最终尝试崩溃 -> retry_exhausted、既有启动恢复测试 |
| AI 不覆盖人工修改 | human preview SQL 红/绿测、NoteWork CAS、capture 已确认状态测试、现有 Review/source-correction 回归 |
| DB 失败不冒充成功 | 本地事务回滚、CloudStore 错误分类、archive commit 失败、IELTS source/outbox 原子失败、dispatcher/feedback API 故障注入 |
| 可按 job/event 定位 | raw/job/epoch 日志关联、公开诊断 DTO（隐藏 lease）、既有 delivery/event ledger |

本轮交付为可审查、可独立应用的后端与传输稳定性补丁；上列发布、UI 既有缺陷和物理设备验证不被描述为已经完成。

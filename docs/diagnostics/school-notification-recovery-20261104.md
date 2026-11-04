# Attention Desk P0 采集恢复记录（2026-11-04）

本记录对应既有 `D:/Eric OS/docs/diagnostics/school-notification-audit-20261104.md`。最新状态：**阶段一本地正式恢复已完成，五来源真实入库、去重、原15分钟周期和常驻健康监控通过；阶段二云端内容传输及DeepSeek付费重试独立保持暂停。** 最新授权边界、正式验收和待同步清单见 [本地恢复与云端审阅报告](wechat-local-restoration-and-sync-review-20261104.md)。下文保留早期修复依据；其中“尚未正式启用”的旧验收状态以新报告为准。

## 根因与真实版本验证

实际微信版本为 4.2.0.9，生效报告仍验证 4.1.15.13。`db_worker.main()` 在 `make_reader()` 之前比较版本，不一致立即返回 `wechat_version_changed_requires_verification`。因此该故障发生在版本门控阶段，尚未进入会话读取或消息解析。`wechat_version_unavailable` 是另一种进程/版本探测失败，不能一律解释为登录失效。

10:41 使用原有只读 `db_reader` 和固定上游核心提交 `d322c4a9b1124228820bab363e5676016f188fc6`，针对原账号、原绑定及原增量游标验证 5 个已授权群：

| 群 ID | 游标之后真实消息 | 重复 ID/内容验证 | 第二次增量正文查询 |
| --- | ---: | --- | ---: |
| db_a3fb9b271808bceb | 72 | 通过 | 0 |
| room_d407e66634bc907b861e | 112 | 通过 | 0 |
| room_b6e5744b89c559a1d1e1 | 89 | 通过 | 0 |
| room_f31dddc4734ffef8c4e4 | 0 | 通过，正常空扫 | 0 |
| room_4d6381262cf288d0294f | 28 | 通过 | 0 |

合计 301 条；发送者前缀不匹配 0，越权正文读取 0，发消息 0，模型请求 0。版本在读取前后再次检查。结果表明**现有核心在当前账号和这五个群上可读取 4.2.0.9**，不能推广成所有微信版本或所有账号的兼容声明。

候选证书保存真实版本、验证时间、群/账号绑定、读取摘要与核心文件 SHA256。适配器对新版证书同时检查完整证据和核心文件摘要，单独修改版本字符串不会获准。未升级依赖、未修改微信文件、未注入或写入微信进程，也没有重置游标。

候选证据：`desktop/attention-desk/artifacts/p0-20261104/compatibility-candidate.json`；正式报告尚保持原版，候选证书未生效。

## 历史积压 dry-run

对正式 SQLite 使用 `mode=ro` 和 `query_only=ON`，检查 15 个未完成 job、HTTP attempt 及原 review/batch。也只读检查已有 DPAPI 恢复文件，没有新建恢复队列。

| 分类 | 数量 | 处理决定 |
| --- | ---: | --- |
| budget_deferred、未发出 HTTP | 8 | 保留，付费请求需审批 |
| pending、未发出 HTTP | 2 | 保留，付费请求需审批 |
| error，原 review 已取消/归档或 batch 被替代 | 3 | 跳过；不改变用户决定，不再付费 |
| error，仍有效且存有响应 | 2 | invalid_evidence / invalid_vision；现有输出不满足校验，保留待核对 |
| 结果未知 | 0（本次 15 个范围） | 无擅自重试；机制仍拦截 unknown/running |
| 可安全免费应用的完整响应 | 0 | 无需执行免费恢复写入 |

本次付费 HTTP 0，自动创建 Matter/Task 0。原始消息、Raw、快照、来源 ID 均保留。

10 个未请求作业的可审核计划：模型 deepseek-flash；最多 10 次文本 HTTP、0 次 Vision、并发 1，每周期最多 4 批。保守预留总量 226,097 tokens（含最大输出 40,960）；保留现有每天 100,000 tokens / 30 HTTP，不增加预算。按预留分三组为 83,562 / 97,049 / 45,486，分预算日执行，并给已有业务留出余量；若实际可用预算不足则继续等待。未知结果立即停，不自动重发。

按 [DeepSeek 官方价格表](https://api-docs.deepseek.com/quick_start/pricing/)当日文本峰时输入 $0.30/M、输出 $1.20/M，以消息 UTF-8 字节作为保守输入上界，估计约 $0.105，建议授权上限 **US$0.12**。此为当前计划的保守估计，尚未执行，也没有扣费上限执行器或新的获批重试任务；必须在真正执行时再次核对计划、原始输入、批准范围、预算与当前费率。

逐 job 计划及原消息 ID：`artifacts/p0-20261104/backlog-dry-run.json`。付费授权问题已提交，未收到明确同意前保持不执行。

## 已提交代码

代码提交：`28e50bc`（首轮 12 个文件）、`d3e75a3`（免费恢复/独立来源健康）、`1f8d3d3`（真实健康告警验收脚本）；均为本地提交，未推送。另有本报告的文档提交。

- `server/wechat_collect/db_adapter.py`：候选证书可隔离验证，正式默认路径保留；新版证书需真实证据及核心摘要。
- `analysis.py`、新增 `recovery.py`：历史 job、历史输入、收到过响应、未知结果、已替代/取消输入不进入隐式付费请求。停更超过一天后的补采 scan 另加预算审批标记，防止恢复采集时顺带大量请求。保留已完成 Vision 转写后继续文本分析与显式重开媒体预览的原路径。
- `service.py`、`store.py`：原 Collector、原 ID、去重、过滤、入库继续复用；读取前失败也写入既有 `wechat_chat_scan`，以后可计算独立来源失败次数。无需迁移 Schema。
- 新增 `health.py`，接入 `server/main.py`：独立于采集循环的每分钟健康任务；复用 WindowsNotifier，不重建推送系统，不修改 UI。
- `tests_wechat/test_collection_health.py`：空扫、夜间、连续失败、告警去重、未知投递、历史/补采零付费保护、证书字符串伪兼容、同步摘要确认、同步观察失败隔离。
- `scripts/wechat_compatibility_probe.py`、`wechat_recovery_acceptance.py`、`wechat_backlog_plan.py`、`wechat_recovery_checkpoint.py`：可复核的只读/隔离验证与备份入口，不负责模拟消息。

没有修改现有未提交的 UI、两个同步模块或 Preview 文件；未触及 IELTS / Life Ledger。

第二轮修复补充：

- 空扫仍允许已持久化消息继续免费的本地过滤/分批；保持空扫零 DeepSeek 请求。避免一次分批异常在游标推进后永远卡住原文。
- 被审批保护、已替代、未来退避中的旧 job 不再占住请求批次名额，新消息可进入原处理流程。保护在选择及正式请求预留前均重查；没有为通过测试而重置旧状态。
- 在既有群扫描日志保留采集成功，并用 `staging_errors`、`latest_error_stage`、`pending_staging_messages` 区分后续暂存失败。待分析 job 只统计当前 extraction，不把已被替代的 job 重复计数。
- 同步状态逐来源按自己的已确认 source_hash 判断；其他来源的积压不会把已确认同步的来源标为 pending。
- `supabase/functions/eric-app-api/official_health*.ts`、`handler.ts`、`index.ts` 及固定依赖锁：现有 API 新增受既有 worker 身份/撤销规则保护的 `GET /worker/official-health`。只读事务读取官网源状态及数量，不读取/返回通知正文，不更改 Schema 或权限。依赖固定为 postgres 3.4.7，连接池上限 1、TLS 校验开启、禁用 prepared statements、SQL 超时 5 秒、每工作区缓存 60 秒。
- `prepare_official_health_package.py`：实际 API18 的 handler/store/vision 与本地分支存在此前差异，发布包因此基于下载的 API18，仅覆盖本次入口与新 GET，保留其他模块原样。没有将本地其他未发布的 Vision/重试改动带入正式环境。

## 健康监控契约与边界

本机 `/api/personal/wechat/status.health` 提供逐授权群：最近扫描、最近成功扫描、最近原消息入库、连续失败、最近错误、待分析与分析错误、状态及是否需告警。`no_change` 为正常空扫，不按最近通知时间判断故障。

本机 `/api/health?check_integrations=false` 的 `collection_health` 在监控运行后提供：上述来源状态、待同步记录数、同步错误、前端可见性证据状态。同步按具体 Raw/Inbox source_hash 与已有 worker publish 确认摘要对照，心跳或 HTTP 200 本身不等于全部同步。前端没有浏览器证据时记 `unverified`，不谎称已显示。

告警条件为连续失败至少 3 次，或有效采集时间超过 45 分钟未成功扫描；00:00–06:00 不主动采集、不告警，并从陈旧时间计算中排除。正在进行的正常限时扫描给予最多 5 分钟宽限。复用 Windows 原生通知，发送前持久化 claim；同来源六小时内不重复、全部来源合并、全局最短间隔 15 分钟。通知结果未知也保留 claim，避免重启风暴。OS 接受不代表用户实际看到。

旧记录没有每群失败历史，只保留最后群状态；过渡期明确标注 `legacy_global_lower_bound`，不伪造历史精确群级计数。启用后新失败记录为 `per_source_journal`。

云端同步观察/凭证失败不会阻止微信健康判断与告警。官网生产采集由既有云 worker 管理，新 GET 从实际云状态提供 29 个官网来源独立结果。SQL 按 worker 验证后的 workspace 过滤 Eric 投影，只返回来源/时间/状态/计数，不把 45 个缺失映射称作已经存在传输队列中的积压；无原文快照的历史占位 Raw 不被误算成待同步 Inbox。

本机持续监控代码通过独立任务每 5 分钟 GET 官网状态，单次最多 10 秒；失败每分钟观察一次，连续 3 次后可合并告警。15 分钟前的快照标 unknown/stale_observation，保留 last_known 状态，不假装当前健康。官网逐来源过期阈值至少 12 小时或该来源轮询间隔的 3 倍，以容纳现有有限时批次轮转；连续失败至少 3 次不等待这一时间。微信仍为 45 个有效采集分钟，告警仍遵守夜间与持久去重规则。

**API19 已实际发布；本机常驻监控尚未随原服务启动，需要获准重启后加载。** 一次性真实健康检查和真实合并告警已完成，详见下文。官网到 Eric Raw 的缺失内容适配仍未实施；学习通仍只有手动学习记录入口，不伪称具备自动通知扫描。

## 验证结果

| 要求 | 当前证据与结果 |
| --- | --- |
| 当前微信真实访问 | 通过；沙箱外现有用户会话可读，5 授权群无越权 |
| 正常产生新 Raw | **隔离库通过**：正式库备份中 390→691 消息，660→742 Raw（+82），77→159 批次 |
| 重复扫描不重复入库 | **隔离库通过**：第二轮 no_change，消息/Raw/批次/attempt 数量完全不变 |
| 原处理流程 | 原过滤与分批通过：seen/new=301，filtered=51；原快照/候选占位保留。模型处理及最终确认未执行 |
| 数据完整性 | 隔离库 FK 错误 0；Matter 11、Task 23、课程10、Review13 全内容哈希前后相同 |
| 原本机停更被识别 | 正式只读数据五群均 failed；旧历史计数口径明确，不凭进程正常判成功 |
| 离线回归 | 第二轮完整微信+启动器+云投影套件 **334 passed**；实际 API18 最小覆盖包及健康读模型 **37 项通过**，Deno 2.6.4 冻结依赖类型检查通过。Python 报告 `pytest-p0-expanded-results.xml`；此前的 326 项与单独健康回归也保留 |
| 调度不变 | 保留原 15 分钟、00:00–06:00 静默与 06:00 catch-up；相应原离线测试通过 |
| 新 Raw 正式入库与云同步 | **待正式启用，未通过** |
| 同一新 ID 的 Eric OS API 原文 | **待正式启用与用户认证验证，未通过** |
| Inbox 全部来源实显 | **既有本机真实样本通过**（见下）；新补采数据和云端页面仍待正式启用，未通过 |
| 正式告警投递 | **真实故障的一次合并本机告警通过 OS 接受，重复检查无新 claim**；常驻循环待原服务加载，横幅实显和用户是否读到仍 Unknown，未发送 Bark |

真实隔离验收：`artifacts/p0-20261104/real-capture-acceptance.json`。隔离库仅在被 Git 忽略的 artifacts 内，未被当作正式库。全部验证使用真实授权群消息；离线回归中的合成消息仅在各测试临时库。

### 官网健康 API 与真实告警

API19 发布前已保存实际 API18 源码与回滚包。发布后从 Supabase 再次读取源码，确认 Store、Vision、文件、视图及 deno.json 与 API18 原文一致；只新增两个健康模块并挂接入口/worker GET。使用现有独立 worker 身份真实 GET 返回 200/29 来源；匿名为 401，原公开 `/health` 仍为 200。这里的通过依据是来源数据及身份隔离，未只看 200。

11:38 云端采样：1 disabled、14 stale、6 partial、8 healthy_idle；教务通知为 partial、连续 11 次 http_error，需告警。45 个 source-item 关联无 Eric Raw，实际待补 Inbox 投影关联 0。计数明确以 source-item 关联为单位，同一条多来源通知仍保留原 ID，不靠重复入库凑数。查询计划实测约 0.49 秒，未新增索引或迁移。

11:44 一次性调用同一个 CollectionHealthMonitor，正式 SQLite 使用只读连接，既有授权群及官网状态均来自真实数据。先 dry-run 确认 25 个异常来源，再经已有 WindowsNotifier 合并为 **一条**真实故障提醒。结果 `os_accepted`，不是虚构测试故障。去重文件在现有本机数据目录 `collection-health-alerts.json`；随后再次执行同一路径，`new_delivery_claim=false`，未重复发送。没有创建第二个 Collector、常驻进程或计划任务；没有推进游标、写业务表或调用模型。Windows 已接受不等于横幅实际显示或用户已阅读。

证据：`official-health-live.json`、`api19-source-verification.json`、`official-api-deployment.json`、`official-source-readonly-rows.json`、`official-source-query-plan.json`、`collection-health-live-dryrun.json`、`collection-health-live-notification.json`、`collection-health-live-repeat.json`，均位于 `artifacts/p0-20261104/`。

### 既有本机 Inbox 的浏览器基线

本会话没有 Browser 插件，按前端测试技能使用现有 Playwright Chromium。目标流为 `http://127.0.0.1:8765/?personal_view=eric-inbox` → 切换来源范围“全部来源” → 打开真实 Raw `42f027ab-3e68-4525-bf05-3a46b4559a3c` → 逐一切换原文区块。

1440×1000 视口中，页面身份为 `Eric Inbox · Attention Desk`，全来源 659 条；DOM 非空，无框架错误覆盖层，pageerror/console error 为 0。该 Raw 的本机 API 原文 544 字符，SHA256 `53fb549931e0596e0cca31a864ea955dbcf4435fdb1eeb000ae956d55190be7b`。9 个 text 来源区块逐个与 DOM 精确比较通过；最后一个区块对应原消息 `f665723c-cf8f-4fd1-96a4-f869ca273987`，203 字符。截图也核对了真实申请材料通知正文。

浏览器拦截所有非 GET/HEAD 和所有非本机请求，实际未出现需拦截请求；没有点击重试、分析、确认、标记或发送按钮。此为现有正式库内容的只读显示验收，不代表候选采集已在正式环境恢复，亦不代表云 Eric OS API 的交互用户认证验收。

证据在仓库外 `C:/Users/ericp/Desktop/reference/p0-notification-evidence-20261104/`：`inbox-baseline.json`、`inbox-all-baseline.png`、`inbox-original-baseline.png`。只检查桌面 Chromium；移动端、Production PWA 和 Preview 尚未验证。现有长文件名列表有溢出，但不影响本次原文读取，本任务没有修改 UI。

## Production、授权边界及回滚（最新）

用户已明确授权阶段一本地正式恢复，并要求云端 Raw/附件上传和付费重试继续单独暂停。本次先加入持久化云写入/付费AI暂停控制，通过367项隔离回归，再备份正式库、停止原任务、切换已验证证书并启动原AttentionDesk-Local。新PID43560，12:38补扫新增301条消息及82条批次Raw；免费原媒体管线另生成13条Raw，共95条。12:40重复扫描和12:45原周期扫描均no_change，正式消息691、Raw755，原有数据未改写，核心四表哈希和调用账本不变。常驻健康监控已实际加载。

此前自动审批拒绝的是将服务启用与未授权敏感内容云传输合并的操作，该命令未执行。用户本次将授权拆分后，本地恢复安全完成；没有重试或绕过被拒绝的云端传输，也不再以该拒绝阻塞本地修复。

阶段二仅完成只读盘点与实际权限核查：95条待同步Raw、131条附件关联记录、82份跨群内容去重附件，全部approved=false。现有全量publish不能直接恢复，需先确认来源/时间/字段、敏感数据最小化与读取边界，再基于精确批次单独申请上传。DeepSeek付费重试继续暂停。

最新备份位于 `desktop/attention-desk/artifacts/p0-20261104/checkpoint-043741/`。保留暂停文件、新消息和游标，不覆盖旧库或删除真实数据；旧代码可能忽略暂停文件，回滚时须先停止原任务，避免直接重启上传。API19只读健康接口及API18回滚包维持此前记录，不改Schema/Secret。详尽证据、测试和回滚边界见新报告。

# P0 验收对照：本地恢复与限定云端端到端验收完成

本对照保留原目标的完整范围。用户明确批准固定批次后，同一限定命令经平台复审获准，2条真实脱敏Raw、8条投影、0附件已完成正式发布、幂等、本人API与正式Inbox验收，证据见 [正式云端验收](wechat-cloud-acceptance-20261104.md)。原本地采集独立运行；后台全量上传与DeepSeek付费重试继续暂停。

完整Raw与八投影摘要、上传前权限审查见 [最终审查](wechat-final-raw-and-access-review-20261104.md)。正式执行载荷哈希为908e472419f0d680b01a0fda841aba970e51342cddac39df80ae6a78a675e60b，两次同批次发布回执均13760，八条完整payload和source_hash与冻结内容一致。用户自行登录的独立验收窗口使用同一owner；原五个会话保留，新验收会话按原流程登记，RLS和用户级隔离不变。

## 要求与证据

下列JSON/XML/截图的基准目录为desktop/attention-desk/artifacts/p0-20261104，浏览器证据另在工作区p0-notification-evidence-20261104。

| 要求 | 验收状态与直接证据 |
| --- | --- |
| 查明微信版本兼容故障 | 已查明：版本校验在创建读取器前拒绝4.2.0.9；不是已证明的登录/会话损坏。根因及调用路径见school-notification-recovery-20261104.md |
| 不仅修改版本字符串 | 已验证：compatibility-candidate.json含真实5群读取、稳定ID、发送者一致性、第二次增量0及读取器关键文件哈希；候选与生效证书一致 |
| 真实已授权群，不发送消息或读取未授权正文 | 真实兼容性探针5群PASS，unauthorized_message_bodies_read=0、messages_sent=0、llm_calls=0；正式采集原5配置来源无扩权 |
| 原Collector、原ID、过滤与去重 | 原AttentionDesk-Local和原SQLite继续使用；301条消息产生82条批次Raw，原免费媒体链路另产生13条Raw。重复及原周期扫描no_change；历史消息/Raw/快照未删改 |
| 白天15分钟、夜间静默及06:00补扫 | 多轮正式periodic扫描已观测；配置逐字节不变。真实11月5日00:00–06:00两种扫描日志均0行；06:00:03合并catch_up,periodic补扫五来源complete，之后每15分钟正常，见night-and-dawn-acceptance.json；另有隔离边界测试 |
| 原15个积压分类与免费恢复 | backlog-original-versus-new-audit.json：原8个budget_deferred、2个pending、5个error作业行未变；3个失效错误跳过、2个有效错误待复核、10个无调用作业保持暂停。没有可安全提交的历史保存输出，不强行恢复或重置状态 |
| 不重复调用成功消息、未知结果不重试 | wechat_llm_attempt仍59且完整旧行未变；dispatch_hold、共享传输和调度三处控制；结果未知/已收响应另有恢复分类测试 |
| 付费数量和预算先报告 | 修正后的dry-run含新增12个媒体作业：全部22个无调用作业最多22次文字+11次视觉；已知首阶段预约373,552 tokens，视觉后文字预算未定。不是执行授权或美元报价，不自动调整预算，实际新增调用0 |
| 不自动创建未经确认Matter/Task | 正式event11、task23、course10、review_artifact13，四表完整内容哈希与重启前相同；外键错误0 |
| 健康时间、失败、错误与各来源独立状态 | 正式常驻CollectionHealthMonitor已运行，5群正常空闲，错误为空、连续失败0；官网29来源以只读GET独立观测。15:04原任务再次运行后监控有更新、暂停仍有效；15:09证据PASS |
| 待分析计数准确 | 新只读核对health-pending-count-audit.json：5来源待分析21/35/29/0/13，与distinct待处理批次完全一致，作业与未排队批次交集均0，没有重复相加 |
| 待同步计数与单位 | 原健康接口跟踪raw_item/inbox_item/inbox三种投影，因此95条Raw对应285条待同步投影；完整清单另含raw_review，为380条。两条审阅样本的4种投影为8条。不能把285或380误写成Raw数量或重复消息 |
| 无新增与采集失败/后处理失败区分 | 正式no_change对应healthy_idle；真实版本故障曾被识别为failed。入库中断和分析错误与采集失败分开；相关test_capture_recovery、test_recovery_progress、test_collection_health通过 |
| 告警与防重复 | 真实故障已由原WindowsNotifier发一条合并告警，OS接受；随后重复检查没有新claim。6小时来源去重/15分钟总节流保留。未伪称横幅实显或用户已读；健康告警不上传微信群内容 |
| 新Raw进入现有本地处理流程 | 正式采集、过滤、快照、待分析/Review和免费媒体处理均有证据；模型步骤依用户指令暂停。本机Inbox新Raw1755字符及来源区块逐项匹配通过，见inbox-local-restored.json/PNG |
| 新内容经正式云Eric OS API读取 | **完成**。实际本人登录会话读取两条/inbox及两条/raw共4次200，正文与已批准节选逐字一致，revision与数据库对应；owner-api-inbox-acceptance.json，无模拟响应 |
| 新内容在正式云Inbox全部来源实显 | **完成**。真实正式站点Inbox从我提交的切到全部来源，两条详情及来源块匹配、脱敏标识可见、附件0、页面异常0，cloud-owner-notice-0/1.png |
| 正式云端来源ID与重放幂等 | **完成**。两次原样发布回执均13760；数据库8个唯一kind/id，8条对应变更，无重复记录/变更，完整payload和source_hash均一致，见database-validation.json |
| 复用现有链路、不改Schema/新建系统 | 使用原SQLite、Collector、同步记录表和现有API；本地预览构造无数据库或服务。未修改Schema、RLS、设备/读取权限、UI或Preview |
| 只准备必要文字、去除PII及附件 | 仅2条学校事务节选；保留真实“计划”和“不强制”限定，不猜测课程或精确截止时间。完整8条投影排除发送者、微信数据库身份、群名、其他消息、草稿/模型输出和附件，383项回归与离线读模型通过 |
| 来源、时间、课程与去重保留 | 2条样本保留原来源/Raw/消息/快照ID、源发布时间与可靠性；原已确认课程关联为空并保持空，去重键与整体bundle_hash稳定。原文哈希/截取位置仅在本地证据中保留 |
| 云端表与权限先展示 | 已展示eric_app_records的8投影、eric_app_changes变更索引、eric_app_workers发布元信息，以及worker/hello更新既有eric_app_processors.last_seen_at的副作用，资源/附件表不写；正式库复核11表RLS开启、客户端无直接读写或26个RPC执行权限。5个本人会话经有效登录和设备校验后可读，无按群隔离，服务端已有管理权限保留；实际匿名/伪造/越权GET均拒绝 |
| 写前说明、可回滚、授权分离 | 本地恢复前说明影响并做checkpoint-043741；正式上传和付费请求持久化分别暂停。回滚不得覆盖旧库、倒退游标或通过旧版本无控制地重启上传。当前所有脱敏预览均upload_authorized=false |

## 当前边界与后续方案

1. 本轮用户要求的两条真实通知正式流转已完成，不再以先前的条件性授权拒绝或已过时的“仅准备”指令阻塞该批次；该次平台拒绝已通过补充明确执行批准及原命令复审解决。
2. 持续增量方案已准备于wechat-incremental-sync-plan-20261104.md，复用原Collector、差异队列、API和调度；尚未实施或启动。扩大来源、数量、内容、附件或时间范围前须另行核实最小化并获得相应授权。
3. 原历史付费任务继续依用户明确要求暂停；没有为完成验收而调用模型、重置历史状态或自动创建Matter/Task。

采集、健康监控与后台上传/付费授权保持独立。正式云端验收已使用真实来源完成；不把这次小批量成功扩写为全部积压已同步或持续上传已启用。

## 对应交付

- 根因、初次真实验证、生产健康API及告警：school-notification-recovery-20261104.md。
- 本地正式恢复、完整积压清单、权限与回滚：wechat-local-restoration-and-sync-review-20261104.md，提交e623916。
- 两条必要文字样本、预算修正与当前权限边界：wechat-minimized-text-review-20261104.md，提交7cbd525。
- 完整8条现有读模型预览与字段/证据约束：提交7c3b11e；原任务重新运行后的只读复核报告f222795。
- 当前精确载荷：wechat-minimized-projection-preview.json；bundle_hash=908e472419f0d680b01a0fda841aba970e51342cddac39df80ae6a78a675e60b；resources=[]、upload_authorized=false。
- 此前完整隔离测试：complete-projection-test-results.xml，383 passed；补回采集时间后针对性测试captured-at-compatibility-tests.xml，20 passed；限定上传器limited-publish-tests.xml，8 passed。正式生产验收另见limited-upload下实际API、数据库和本人页面证据，不以离线测试替代。
- 固定8条载荷的原API19发布入口离线预检：minimized-publish-contract.json。请求体原样交给原RPC，验证处理器身份和无效发布标识拒绝、陈旧发布冲突及未知结果不自动重试，并补齐处理器活跃时间元信息写入范围。RPC响应为夹具；没有真实数据库提交或网络请求，不替代正式去重/上传验证。

所有Git提交仅在本地，未向Git远端推送私人清单、载荷或原文；本次仅向用户批准的原Eric OS服务传输了固定脱敏批次。用户既有未提交的同步配置及UI改动保留。

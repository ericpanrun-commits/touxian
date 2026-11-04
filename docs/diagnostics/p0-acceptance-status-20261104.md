# P0 验收对照：本地完成，云端待审阅及单独授权

本对照保留原目标的完整范围，不将本地成功或离线读模型成功替代正式云端端到端验收。用户最新指令优先：本地采集独立运行，云端只准备必要通知的脱敏文字、附件0份，正式上传需另行授权；DeepSeek付费重试继续暂停。

后续最终审查已完成，用户已确认五个本人登录会话可保留。完整Raw与八投影摘要、当前权限和未执行的授权草案见 [最终审查](wechat-final-raw-and-access-review-20261104.md)。Review版本元信息变化及补回现有页面必需的原采集时间字段后，当前固定载荷哈希为908e472419f0d680b01a0fda841aba970e51342cddac39df80ae6a78a675e60b；两条正文、权限及0附件范围未变。下文旧哈希仅保留为历史验收依据，不可直接用于正式申请。

## 要求与证据

下列JSON/XML/截图的基准目录为desktop/attention-desk/artifacts/p0-20261104，浏览器证据另在工作区p0-notification-evidence-20261104。

| 要求 | 验收状态与直接证据 |
| --- | --- |
| 查明微信版本兼容故障 | 已查明：版本校验在创建读取器前拒绝4.2.0.9；不是已证明的登录/会话损坏。根因及调用路径见school-notification-recovery-20261104.md |
| 不仅修改版本字符串 | 已验证：compatibility-candidate.json含真实5群读取、稳定ID、发送者一致性、第二次增量0及读取器关键文件哈希；候选与生效证书一致 |
| 真实已授权群，不发送消息或读取未授权正文 | 真实兼容性探针5群PASS，unauthorized_message_bodies_read=0、messages_sent=0、llm_calls=0；正式采集原5配置来源无扩权 |
| 原Collector、原ID、过滤与去重 | 原AttentionDesk-Local和原SQLite继续使用；301条消息产生82条批次Raw，原免费媒体链路另产生13条Raw。重复及原周期扫描no_change；历史消息/Raw/快照未删改 |
| 白天15分钟、夜间静默及06:00补扫 | 多轮正式periodic扫描已观测；配置逐字节不变。夜间/06:00边界由test_foundations及test_db_adapter等隔离测试覆盖，不伪称已等待到当晚实测 |
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
| 新内容经正式云Eric OS API读取 | **未完成且未执行上传**。原API19只读健康接口已发布验证，与这项新内容验收是不同事项。两条脱敏样本仅在离线API19读模型通过 |
| 新内容在正式云Inbox全部来源实显 | **未完成**。本机真实原文浏览器验收已通过；本地8条脱敏投影的全部来源/详情/debug/Raw Review兼容性通过，不替代正式云页面同ID验证 |
| 复用现有链路、不改Schema/新建系统 | 使用原SQLite、Collector、同步记录表和现有API；本地预览构造无数据库或服务。未修改Schema、RLS、设备/读取权限、UI或Preview |
| 只准备必要文字、去除PII及附件 | 仅2条学校事务节选；保留真实“计划”和“不强制”限定，不猜测课程或精确截止时间。完整8条投影排除发送者、微信数据库身份、群名、其他消息、草稿/模型输出和附件，383项回归与离线读模型通过 |
| 来源、时间、课程与去重保留 | 2条样本保留原来源/Raw/消息/快照ID、源发布时间与可靠性；原已确认课程关联为空并保持空，去重键与整体bundle_hash稳定。原文哈希/截取位置仅在本地证据中保留 |
| 云端表与权限先展示 | 已展示eric_app_records的8投影、变更索引与原worker元信息，资源/附件表不写；正式库复核11表RLS开启、客户端无直接读写或26个RPC执行权限。5个本人会话经有效登录和设备校验后可读，无按群隔离，服务端已有管理权限保留；实际匿名/伪造/越权GET均拒绝 |
| 写前说明、可回滚、授权分离 | 本地恢复前说明影响并做checkpoint-043741；正式上传和付费请求持久化分别暂停。回滚不得覆盖旧库、倒退游标或通过旧版本无控制地重启上传。当前所有脱敏预览均upload_authorized=false |

## 当前真正剩余的验收

1. 两条完整脱敏Raw、8条投影字段与权限审查及独立上传申请草案均已准备；用户已确认5个本人会话可以保留，本次要求仅准备申请，不执行上传。
2. 等待对固定2条Raw/8条投影、0附件及当前bundle_hash的单独正式上传授权。不能通过解除总暂停恢复全量积压或后来新增内容。
3. 只有取得该授权后，才可限定执行并核对实际回执、同ID远端去重、Eric OS API返回与正式Inbox“全部来源”实显。未获授权时不执行这些动作。

上述云端审阅/授权边界不影响本地采集继续运行。付费重试维持用户明确要求的暂停，不将自动续跑或样本准备选择当作收费批准。原目标仍有正式云端端到端项目未完成，不能标记全目标完成。

## 对应交付

- 根因、初次真实验证、生产健康API及告警：school-notification-recovery-20261104.md。
- 本地正式恢复、完整积压清单、权限与回滚：wechat-local-restoration-and-sync-review-20261104.md，提交e623916。
- 两条必要文字样本、预算修正与当前权限边界：wechat-minimized-text-review-20261104.md，提交7cbd525。
- 完整8条现有读模型预览与字段/证据约束：提交7c3b11e；原任务重新运行后的只读复核报告f222795。
- 当前精确载荷：wechat-minimized-projection-preview.json；bundle_hash=908e472419f0d680b01a0fda841aba970e51342cddac39df80ae6a78a675e60b；resources=[]、upload_authorized=false。
- 此前完整隔离测试：complete-projection-test-results.xml，383 passed；本次补回采集时间后针对性测试captured-at-compatibility-tests.xml，20 passed。当前API19离线读模型及本机Inbox两条脱敏详情均通过；生产写入/云写入/模型请求均0，不宣称正式云端验收已完成。

所有Git提交仅在本地，没有把私人清单、载荷或原文推送到外部；用户既有未提交的同步配置及UI改动保留。

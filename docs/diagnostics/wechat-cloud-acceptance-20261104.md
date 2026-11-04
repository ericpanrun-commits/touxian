# 微信通知到 Eric OS：限定批次正式云端验收

2026-11-04 23:19（北京时间）。真实微信消息 → 本地既有 Raw/快照 → 审核后的脱敏投影 → 原 Eric OS 云端 API → Supabase → 本人登录的正式 Inbox，已完成验收。没有使用离线响应、测试账号或虚构通知替代正式链路。

## 授权及实际范围

本次只执行已审核的两条必要学校通知：“校纪校规考试安排（计划）”“晚自习学习要求”。两条完整业务 Raw 和八条投影字段见 [上传前最终审查](wechat-final-raw-and-access-review-20261104.md)。附件、图片、OCR、文件元信息均为0；course关联保持原先的空值，没有猜测课程。

固定载荷 SHA256：`908e472419f0d680b01a0fda841aba970e51342cddac39df80ae6a78a675e60b`。

平台首次拒绝执行，因为当时对“获得正式授权后执行”的指令仍按条件性授权处理，认为缺少明确执行批准。该次进程未启动，执行回执文件不存在。随后用户明确答复“我现在明确批准这一次固定批次正式上传及幂等验收”，同一命令重新提交平台复审后获准执行。没有改用数据库直写、其他接口或间接执行绕过拒绝。

原 API：`https://mantuehuzptactymjpqg.supabase.co/functions/v1/eric-app-api`。

原工作区：`50e5a3ac-dd15-4f0a-a93c-ace4b735d16d`；owner：`fd779e27-adcd-41f5-b9b0-748ea9267956`；原处理器：`bc5c2a70-d5c1-449c-b8c3-d72b8a02624d`。发布前确认该处理器未撤销、只有这一有效处理器、无活跃并行发布者，八个目标键均不存在。

## 正式写入与幂等性

通过原协议执行3次POST：一次 `/worker/hello`、一次 `/worker/publish`、一次原样 `/worker/publish` 幂等重放。两次发布使用完全相同的8条记录、来源ID、source_hash、epoch及generation=1。另有24次只读GET分页核对记录目录，没有调用默认全量publish。

| 原Raw | 来源ID | 投影kind及云端revision |
| --- | --- | --- |
| d5fc2590-6e46-4a2e-8489-26094fd8e6c0 | wechat:db_a3fb9b271808bceb | raw_item 13753；inbox_item 13754；inbox 13755；raw_review 13756 |
| 1b26a8b3-cd55-4284-be7f-36cd20c26da2 | wechat:room_d407e66634bc907b861e | raw_item 13757；inbox_item 13758；inbox 13759；raw_review 13760 |

raw_review沿用原快照ID，分别为`5670fdb2-c494-401d-8108-85d99c46aa5c`及`91a3c166-c39f-4831-8930-06a683fcd662`。

首次发布回执为13760，幂等重放回执仍为13760。数据库只读复核：八个kind/id唯一；八条完整payload与本机冻结载荷逐字段相等，source_hash全部相等；对应变更总数8，重放未增加记录或变更。八条数据库revision为13753–13760，均非deleted，source_generation均为1。

业务载荷写入原`public.eric_app_records`，触发器记录原`eric_app_changes`；原`eric_app_workers`记录发布epoch/generation及时间，`eric_app_processors.last_seen_at`更新活跃时间。上传时间窗内，records仅有上述四种kind各两条发生更新，没有event/task/course等投影变化。没有新增资源，也没有为两条通知创建capture关联、Matter、Task或分析作业。

## 本人账号 API 和正式 Inbox

用户按选择在独立验收浏览器中自行登录。浏览器为Playwright Chromium，1440×1000；Browser plugin not available。使用正式站点及原登录流程，没有注入登录令牌、导出凭据或模拟API响应。

正式站点：`https://eric-os-workbench-ericpanrun.choicechar22.chatgpt.site`。

操作路径：打开Inbox → 来源范围从“我提交的”切换为“全部来源” → 分别打开两条通知 → 查看“原始通知”及来源区块。

| 检查 | 实际结果 |
| --- | --- |
| 正式站点及页面身份 | Inbox · Eric OS，页面包含真实业务内容 |
| 本人登录绑定 | owner与上述工作区一致；本人自行登录，没有测试用户或伪造JWT |
| 原API读取 | 两条`/inbox/{raw_id}`及两条`/raw/{snapshot_id}`共4次200；revision与数据库对应记录一致 |
| 正文及来源 | 两条API和页面正文均与已批准节选逐字相等；原来源ID、消息ID、快照ID和发布时间在投影中保留 |
| 脱敏及附件 | 显示“【脱敏节选】”，附件/附件任务均为空，无发送者、群名、其他原文、草稿或模型输出混入 |
| 页面错误 | 无页面运行异常或框架错误层 |
| 不额外执行 | 浏览器只允许正常登录登记和读取；没有确认Matter/Task、发起分析或发送通知 |

已有其他资料卡片尝试获取附件预览票据时，验收窗口阻断了8次相关POST；这些是其他已存在资料的预览请求，与本批0附件无关。没有为此修改正式UI或扩大附件处理范围，不把该限制当作两条脱敏通知的采集故障。

独立登录按现有流程新增一个本人会话`7811809f-7544-40a3-886c-3064d331a6fc`，创建于23:14:22；原先确认的五个会话未被撤销。当前新增会话归属同一owner，没有新增其他账号。验收窗口随后关闭，没有替用户撤销会话；今后可使用原Eric OS设备页管理它。RLS、设备权限规则及工作区owner绑定未改。

验收结束后已删除该次临时浏览器配置目录，未在工作区保留此窗口的登录令牌；后续同类验收脚本改为使用不持久化的浏览器上下文。服务端会话登记行仍可由本人在设备页管理，没有对原会话执行退出或撤销。

本次真实页面验证覆盖一个桌面Chromium会话的两条通知，不声称已经逐一实测所有手机或原五个终端。

## 本地恢复和费用保护

23:19只读核对：原AttentionDesk-Local仍为PID42536，没有为本次上传重启服务。五来源23:15周期扫描均正常，连续失败0。现有00:00–06:00静默、06:00补扫、白天15分钟规则不变。

正式本地库Raw762、快照836、微信消息709；恢复前已有Raw/快照/消息/LLM调用记录均未删除或改写。Matter/event11、task23、course10、review_artifact13的完整内容哈希与恢复前一致，外键错误0。微信LLM调用账本仍59条且完整内容不变，原历史付费积压仍保持暂停。

`hold_cloud_uploads=true`、`pause_paid_ai=true`持续保留。一次受控发布没有启动后台全量同步，没有上传未经审核的群聊、附件或其他来源，没有重复Bark推送或付费模型请求。

原健康监控仍可能显示待同步：它按未脱敏完整投影的source_hash统计，本批脱敏投影是独立审核内容。不能把这次两条成功描述为全部积压已同步，也不能用脱敏回执替完整原文伪造确认。

隔夜收尾复核于11月5日07:29完成：11月5日00:00–06:00的wechat_scan及wechat_chat_scan均为0行；06:00:03原调度以`catch_up,periodic`合并补扫，五来源全部complete，之后06:15、06:30、06:45、07:00、07:15原15分钟周期持续正常。PID、原业务四表哈希、调用账本及两个暂停开关均未变，Raw762/快照836/消息709/调用59不变。夜间与清晨边界现在同时有真实运行日志和隔离测试证据。

## 证据与修改

证据基准目录：`desktop/attention-desk/artifacts/p0-20261104/limited-upload/`。

- `remote-preflight.json`：上传前身份、并发及目标空集核查。
- `execution-receipt.json`：3次正式POST、24次GET、两次13760回执及持久化状态。
- `database-acceptance.json`、`database-validation.json`：八条正式数据库记录与冻结载荷逐字段比对，唯一键与变更计数。
- `owner-api-inbox-acceptance.json`：真实本人会话、4次API200、两条正式页面结果，mocked_responses=0。
- `postflight-boundaries.json`：RLS/普通客户端权限未变，新增同owner验收会话，资源新增0。
- `night-and-dawn-acceptance.json`：真实00:00–06:00零扫描、06:00:03补扫及后续15分钟周期。
- `final-persistence-check.json`：11月5日07:31再次读取正式八条记录，ID、source_hash及revision均与发布后相同。
- `../limited-publish-tests.xml`：限定上传器8项隔离测试全部通过；另保留此前383项采集/恢复/健康等回归，以及采集时间修复的20项针对性测试。

截图与交互脚本在工作区`p0-notification-evidence-20261104/`：`cloud-owner-notice-0.png`、`cloud-owner-notice-1.png`、`cloud-owner-acceptance.cjs`。浏览器私有配置不作为交付或Git文件，不包含在报告中。

新增`scripts/wechat_limited_publish.py`及8项隔离测试。该一次性入口绑定固定hash、重验原始证据和原身份，只允许原登记/发布路径、最多3次POST，未知结果不自动重试；已存在执行日志时禁止再次执行。没有将其加入Collector、队列或调度。预览文件保留upload_authorized=false作为上传前内容快照；实际批准和已执行状态以用户明确批准、平台工具结果和独立执行回执为准，不能靠编辑预览标志授权。

## 持续增量方案与回滚

下一步方案见 [持续增量同步方案](wechat-incremental-sync-plan-20261104.md)。本次只准备方案，没有启动持续上传或扩大许可范围。

代码回滚不能撤回已经上传的8条记录。若以后需要撤回云端副本，应另行授权，使用原机制按精确kind/id处理，不改RLS或绕过原删除限制；不自动删除历史记录。保留本地原文、原ID和游标，不用旧数据库覆盖真实采集。后台云上传与付费暂停文件继续保留。旧发布epoch仅为发布者版本栅栏，后续正规发布应取得新的epoch，不盲目恢复旧epoch。

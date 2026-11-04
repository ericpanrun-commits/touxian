# 微信两条Raw与八条投影：最终只读审查

审查时间：2026-11-04 16:10:36（北京时间）。本次只准备申请，不执行上传。

固定范围：2条既有Raw、8条现有记录投影、0附件。bundle_hash：`908e472419f0d680b01a0fda841aba970e51342cddac39df80ae6a78a675e60b`。

## 两条完整脱敏Raw

以下为完整拟同步业务Raw的脱敏副本。原Raw索引投影只含元信息，正文存入同一条Raw关联的Inbox/Review快照；原消息、原快照ID不变。本机未脱敏原文不进入本批载荷。

本轮冻结前发现晚自习Raw的Review.version在15:04服务再次运行后由1变2、updated_at同步变化；旧预览哈希92642d99…因此被校验拦截。正文、来源、课程和权限未变。本报告与申请草案使用重新核对后的当前8条载荷；旧版预览保留在projection-before-final-review.json作对照，不再用于申请。

随后在现有Inbox页面预演时发现脱敏消息缺少captured_at，页面格式化日期时报Invalid time value。已仅在脱敏消息中补回原库采集时间，未修改UI、正文或读取权限。当前哈希因此从084bae10…更新为本报告顶部的908e4724…；旧载荷保留在projection-before-captured-at-compatibility.json，不再用于申请。

### Raw 1：校纪校规考试安排（计划）

```json
{
  "id": "d5fc2590-6e46-4a2e-8489-26094fd8e6c0",
  "source_id": "wechat:db_a3fb9b271808bceb",
  "source_snapshot_id": "5670fdb2-c494-401d-8108-85d99c46aa5c",
  "source_message_ids": [
    "d80a33aa-d113-4079-9503-c501ac9ab535"
  ],
  "published_at": [
    "2026-10-29T09:42:46+08:00"
  ],
  "publication_time_reliability": [
    "reliable"
  ],
  "title": "校纪校规考试安排（计划）",
  "text": "学院计划在11月18日周三晚修时间，在晚修课室进行校纪校规考试，请大家合理安排时间，准时参加。\n另外请大家及时查阅学生手册，熟悉相关校纪校规。",
  "course": null,
  "course_link_basis": "not_established",
  "schedule_quotes": [
    "11月18日周三晚修时间"
  ],
  "attachments": [],
  "minimization_policy": "selected-text-review-v1",
  "redacted_excerpt": true
}
```

### Raw 2：晚自习学习要求

```json
{
  "id": "1b26a8b3-cd55-4284-be7f-36cd20c26da2",
  "source_id": "wechat:room_d407e66634bc907b861e",
  "source_snapshot_id": "91a3c166-c39f-4831-8930-06a683fcd662",
  "source_message_ids": [
    "521fd588-e436-49f4-a2e7-5ddf0ac0b2f9"
  ],
  "published_at": [
    "2026-11-03T14:10:06+08:00"
  ],
  "publication_time_reliability": [
    "reliable"
  ],
  "title": "晚自习学习要求",
  "text": "学生在晚自习教室自主完成作业，预复习课程知识。\n学生自愿存放手机。不强制统一上交\n严禁迟到、早退、玩手机等违禁行为。",
  "course": null,
  "course_link_basis": "not_established",
  "schedule_quotes": [],
  "attachments": [],
  "minimization_policy": "selected-text-review-v1",
  "redacted_excerpt": true
}
```

## 八条投影字段摘要

| 序号 | kind / 记录ID | 用途 | 目标表 | 新建Matter / Task |
| --- | --- | --- | --- | --- |
| 1 | raw_item / d5fc2590-6e46-4a2e-8489-26094fd8e6c0 | 原Raw索引、来源与快照关联 | public.eric_app_records | 否 / 否 |
| 2 | inbox_item / d5fc2590-6e46-4a2e-8489-26094fd8e6c0 | Inbox全部来源列表卡片 | public.eric_app_records | 否 / 否 |
| 3 | inbox / d5fc2590-6e46-4a2e-8489-26094fd8e6c0 | Inbox详情、脱敏正文及来源区块 | public.eric_app_records | 否 / 否 |
| 4 | raw_review / 5670fdb2-c494-401d-8108-85d99c46aa5c | 原快照的Review读取模型，不提交确认 | public.eric_app_records | 否 / 否 |
| 5 | raw_item / 1b26a8b3-cd55-4284-be7f-36cd20c26da2 | 原Raw索引、来源与快照关联 | public.eric_app_records | 否 / 否 |
| 6 | inbox_item / 1b26a8b3-cd55-4284-be7f-36cd20c26da2 | Inbox全部来源列表卡片 | public.eric_app_records | 否 / 否 |
| 7 | inbox / 1b26a8b3-cd55-4284-be7f-36cd20c26da2 | Inbox详情、脱敏正文及来源区块 | public.eric_app_records | 否 / 否 |
| 8 | raw_review / 91a3c166-c39f-4831-8930-06a683fcd662 | 原快照的Review读取模型，不提交确认 | public.eric_app_records | 否 / 否 |

### 投影1：raw_item

**顶层字段**：`id`, `source_id`, `kind`, `source_type`, `created_at`, `title`, `published_at`, `inbox_original_snapshot_id`, `inbox_snapshot_id`, `inbox_course_id`, `inbox_version`, `redacted_excerpt`, `minimization_policy`。

载荷大小：533字节；source_hash：`73ba2a39e9dd5592f053a2edf4666fe972d0907d2e878ebc37e3f7f041a0bdc8`。

### 投影2：inbox_item

**顶层字段**：`id`, `created_at`, `source`, `source_kind`, `input_kind`, `title`, `preview`, `preview_source`, `vision_notice`, `ocr_preview`, `processing_status`, `processing_stage`, `original_snapshot_id`, `snapshot_id`, `course`, `project_ref`, `classification`, `error`, `proposal_count`, `review_status`, `version`, `attachments`, `managed`, `needs_manual`, `redacted_excerpt`。

载荷大小：905字节；source_hash：`8f44e0c215d99e624fb24c05a567fba3bb674ffac9e97b7c78c44e134cc6936d`。

### 投影3：inbox

**顶层字段**：`id`, `created_at`, `source`, `source_kind`, `input_kind`, `title`, `preview`, `preview_source`, `vision_notice`, `ocr_preview`, `processing_status`, `processing_stage`, `original_snapshot_id`, `snapshot_id`, `course`, `project_ref`, `classification`, `error`, `proposal_count`, `review_status`, `version`, `attachments`, `managed`, `needs_manual`, `redacted_excerpt`, `original`, `review_available`, `review`, `extraction_quality`, `school_documents`, `receipts`。

载荷大小：6128字节；source_hash：`8342637142eeb973ed8138d0bdc8d9554ea1b31196a24705d1b0fdfd71965945`。
嵌套快照仅含选定文字、来源消息ID与时间、脱敏来源块；attachments/attachment_jobs/analysis为空，database_identity/sender/chat_name不导出。Review的draft/extraction为空，confirmed_event_ids/wechat_candidates/source_update.events/source_update.proposals为空。全部字段路径见projection-field-summaries.json，完整值见八条载荷JSON。

### 投影4：raw_review

**顶层字段**：`raw_snapshot_id`, `title_hint`, `status`, `version`, `created_at`, `updated_at`, `snapshot`, `item`, `source`, `extraction_id`, `draft`, `extraction`, `confirmed_event_ids`, `wechat_candidates`, `course_suggestions`, `source_update`。

载荷大小：3067字节；source_hash：`a4dd9320b12954c48d4aa5543a57a5d3824301b6fdde04ed3b21a4d88af3d743`。
嵌套快照仅含选定文字、来源消息ID与时间、脱敏来源块；attachments/attachment_jobs/analysis为空，database_identity/sender/chat_name不导出。Review的draft/extraction为空，confirmed_event_ids/wechat_candidates/source_update.events/source_update.proposals为空。全部字段路径见projection-field-summaries.json，完整值见八条载荷JSON。

### 投影5：raw_item

**顶层字段**：`id`, `source_id`, `kind`, `source_type`, `created_at`, `title`, `published_at`, `inbox_original_snapshot_id`, `inbox_snapshot_id`, `inbox_course_id`, `inbox_version`, `redacted_excerpt`, `minimization_policy`。

载荷大小：524字节；source_hash：`b88a3dd5fe8b2b91feda85ae7570ebbf640a10d1029e7590f85ce77e2a6f4cb8`。

### 投影6：inbox_item

**顶层字段**：`id`, `created_at`, `source`, `source_kind`, `input_kind`, `title`, `preview`, `preview_source`, `vision_notice`, `ocr_preview`, `processing_status`, `processing_stage`, `original_snapshot_id`, `snapshot_id`, `course`, `project_ref`, `classification`, `error`, `proposal_count`, `review_status`, `version`, `attachments`, `managed`, `needs_manual`, `redacted_excerpt`。

载荷大小：857字节；source_hash：`66ffb66cddb2ec14e175127d7e92e5ef4f7248e89a1c140dc995a0a5f8c1b9e4`。

### 投影7：inbox

**顶层字段**：`id`, `created_at`, `source`, `source_kind`, `input_kind`, `title`, `preview`, `preview_source`, `vision_notice`, `ocr_preview`, `processing_status`, `processing_stage`, `original_snapshot_id`, `snapshot_id`, `course`, `project_ref`, `classification`, `error`, `proposal_count`, `review_status`, `version`, `attachments`, `managed`, `needs_manual`, `redacted_excerpt`, `original`, `review_available`, `review`, `extraction_quality`, `school_documents`, `receipts`。

载荷大小：5869字节；source_hash：`275de1c57dfbd6b022f3d369a78f37ccd17aef139dffb7b6706f0cf176a3f263`。
嵌套快照仅含选定文字、来源消息ID与时间、脱敏来源块；attachments/attachment_jobs/analysis为空，database_identity/sender/chat_name不导出。Review的draft/extraction为空，confirmed_event_ids/wechat_candidates/source_update.events/source_update.proposals为空。全部字段路径见projection-field-summaries.json，完整值见八条载荷JSON。

### 投影8：raw_review

**顶层字段**：`raw_snapshot_id`, `title_hint`, `status`, `version`, `created_at`, `updated_at`, `snapshot`, `item`, `source`, `extraction_id`, `draft`, `extraction`, `confirmed_event_ids`, `wechat_candidates`, `course_suggestions`, `source_update`。

载荷大小：2952字节；source_hash：`ca495c9ebfc8ed46d06abdd7315c8fc14a8ae30c7ff0fca78fce8b45783a4586`。
嵌套快照仅含选定文字、来源消息ID与时间、脱敏来源块；attachments/attachment_jobs/analysis为空，database_identity/sender/chat_name不导出。Review的draft/extraction为空，confirmed_event_ids/wechat_candidates/source_update.events/source_update.proposals为空。全部字段路径见projection-field-summaries.json，完整值见八条载荷JSON。

## 写入与Matter / Task副作用

正式数据库的eric_app_publish_records、begin_publish及两个记录触发器已只读核对；实际API19的worker/hello入口亦已核对。未来若获授权，本批业务载荷写eric_app_records，eric_app_changes记变更，eric_app_workers记发布epoch/generation/时间，并使用原revision序列。worker/hello还会更新既有eric_app_processors行的last_seen_at；这是此前三表说明中遗漏的元信息写入，现已补全。不会新增处理器、修改其凭据或撤销状态，也不会写登录设备/会话/RLS。没有event/task投影，没有Matter/Task表写入，没有确认动作或分析作业派发。

本结论以未来仅发布这份固定批次为前提：不调用worker/claim、commands、capture-results、receipts、resources/files等路径，不启动默认全量publish。现有CloudSync默认publish会先处理所有资源，不能用于本批。本轮未调用任何写入路径。

核查时这8个kind/id在远端均不存在。原发布函数会保留已有Inbox视觉字段，因此正式执行前必须重新检查目标ID；若出现意外已有内容、哈希变化或并发发布，则停止并报告，不合并未知原文。

## 登录身份与读取范围

工作区owner：`er***@outlook.com`；用户ID：`fd779e27-adcd-41f5-b9b0-748ea9267956`。已验证邮箱、authenticated角色、未删除、未封禁。

**此前“5台设备”应更正为5个未撤销登录会话。** 它们不一定代表5台不同物理设备。五个session均存在且user_id与同一owner一致，无跨账号记录；用户本轮已确认全部由本人发起并可保留。没有新增/撤销会话或修改权限。

| 会话设备ID前缀 | 标签 / 浏览器 | 创建时间 | 最近登记活跃 | 身份及会话核对 |
| --- | --- | --- | --- | --- |
| 7e86be9c | Eric iPhone / Safari / iPhone | 2026-10-30 12:38:55 | 2026-11-03 09:08:02 | 同一owner，会话存在，未撤销；本人已确认 |
| 8e0d6ba4 | Eric iPhone / Safari / iPhone | 2026-10-31 03:06:48 | 2026-11-04 11:24:25 | 同一owner，会话存在，未撤销；本人已确认 |
| 265bc6a8 | Windows Eric OS / Edge / Windows | 2026-11-02 13:09:58 | 2026-11-03 21:35:26 | 同一owner，会话存在，未撤销；本人已确认 |
| 56184d57 | Windows Eric OS / Chrome / Windows | 2026-11-02 16:20:50 | 2026-11-02 16:30:04 | 同一owner，会话存在，未撤销；本人已确认 |
| 505f3881 | Windows Eric OS / Edge / Windows | 2026-11-04 09:25:05 | 2026-11-04 09:26:26 | 同一owner，会话存在，未撤销；本人已确认 |

“未撤销”不是“当前在线”：这些会话的not_after均未设置，访问仍需有效的Supabase JWT及Eric OS设备登记。未读取、导出或重放真实owner JWT，未替每台设备重新登录；当前归属由数据库/已部署访问控制代码、离线合同测试和用户识别共同核实。

## RLS、RPC与实际访问检查

- 已部署API确认为19；从云端重新读取源代码。API校验Supabase签名/用户、邮箱确认、工作区owner及未撤销设备，每次查询限定workspace。
- 11张eric_app表RLS均开启，无客户端放行策略；anon/authenticated不能直接读写关键记录、资源、设备、工作区表，也不能执行26个eric_app RPC。Auth用户/会话表也未对这两种客户端角色开放。
- 现网GET核查：匿名Inbox/Raw、伪造owner JWT、处理器密钥冒充owner会话、伪造文件票据及错误处理器密钥均401；现有处理器只读目录200，返回kind/id/source_hash而非正文。
- 当前实际下载的API19代码合同测试33项通过，涵盖身份、独立处理器、工作区读取边界与设备撤销等路径；离线8条脱敏读模型、此前383项Python回归通过。
- 本次采集时间兼容修复后，针对性20项Python测试及当前8条API19离线读模型检查通过。现有本机Inbox的“全部来源→详情→原始通知/来源块”两条样本均能展示，脱敏标识和正文匹配，页面异常0；这是本地离线响应预演，不替代正式云端端到端验收。
- 当前固定8条载荷另经下载的API19发布入口离线预检：原发布RPC收到相同的kind/id/payload/source_hash；无效或撤销处理器、无效epoch/generation被拒绝，陈旧发布及结果未知返回错误且无自动重试。核对了worker/hello的处理器活跃时间副作用。数据库RPC响应为隔离夹具，未执行真实SQL，不据此声称正式去重/提交成功。
- 这五个本人会话如持有效JWT，可经现有API读取该owner工作区已发布内容；目前没有群级或单条Raw读取白名单。服务端service_role、数据库管理权限及原处理器保留其已有权限，本次不扩大权限。
- 设备revoked_at生效后API拒绝访问，同一已撤销session不能通过重新登记恢复。仅Supabase登出可能让已签发JWT继续有效至到期；现有API不额外以auth.sessions行存在性执行即时登出检查。这是需要保留的现有会话边界，不能宣称所有登出方式均即时失效。[Supabase说明](https://supabase.com/docs/guides/auth/signout)
- 过去24小时已查到的owner认证审计事件为1次login、4次token_refreshed和4次token_revoked，浏览器类型与登记会话一致；token_revoked可来自刷新轮换，不能直接当作设备撤销或入侵。日志时间窗2026-11-03 15:30至2026-11-04 15:30，不作为全历史无异常的绝对证明。

**结论：本次审查范围内未发现匿名、跨账号或跨工作区读取本批内容的通道，五个读取会话亦已由本人确认。** 这是当前配置和本批载荷的范围结论，不等于对所有历史访问、所有终端或服务端管理人员作绝对安全保证。

## 本地页面预演证据

环境为http://127.0.0.1:8765、1440×1000、Playwright Chromium。Browser plugin not available，因此使用现有Playwright；不安装依赖、不改UI、不重新构建Preview。列表与详情响应来自当前固定8条投影经实际API19 Views生成的离线结果，其他网络仅允许本机GET/HEAD，所有写请求和外网请求均被拦截。

| 检查 | 结果 |
| --- | --- |
| 页面身份与非空内容 | Eric Inbox · Attention Desk，两条详情均正常显示 |
| 框架错误层与控制台 | 无错误层，相关错误0 |
| 操作路径 | 提交来源切换到全部来源，分别打开两条详情，查看原始通知与来源区块 |
| 正文与脱敏标签 | 两条逐字匹配，仅脱敏节选，附件0 |
| 本次写入或收费 | 生产写入0、云写入0、模型请求0 |

证据在工作区p0-notification-evidence-20261104/inbox-minimized-preview.json与inbox-minimized-0.png、inbox-minimized-1.png。执行脚本为同目录inbox-minimized-preview.cjs。仅覆盖当前桌面Chromium两条详情，未替代手机、五个真实终端或正式云端验证。

## 既有项目级提示

安全检查的RLS Enabled No Policy在本链路中是客户端关闭直读、经服务端API访问的现有设计，不能为了消除提示而增加客户端读取策略。[说明](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)

项目还报告public中的pg_net、四个其他业务SECURITY DEFINER RPC及泄露密码保护未开启等既有提示。已核对这四个RPC无eric_app直接引用或动态EXECUTE，且含auth.uid检查；本次未调用或改动它们，不把其他业务的项目级告警声称已修复。[扩展提示](https://supabase.com/docs/guides/database/database-linter?lint=0014_extension_in_public) / [函数提示](https://supabase.com/docs/guides/database/database-linter?lint=0029_authenticated_security_definer_function_executable) / [密码保护](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection)

## 当前状态

本机只读复核时间：2026-11-04 16:04:15。五个来源均健康、连续失败0，最新一轮原15分钟周期扫描完成。正式event/task/course/review_artifact完整内容哈希未变，既有微信LLM调用账本仍59条，外键错误0。

本地AttentionDesk-Local采集与健康监控继续运行，云写入与DeepSeek付费分析独立暂停。本轮没有上传Raw、附件或投影，没有创建Matter/Task，也没有改变账号、会话、RLS、设备或其他业务配置。独立上传授权申请已另存草案，upload_authorized=false，未执行。

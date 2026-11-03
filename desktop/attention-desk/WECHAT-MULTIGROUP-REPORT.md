# Attention Desk · 多微信群验收

2026-10-23，Asia/Shanghai。实现已加载到本机后台；真实授权范围是用户明确指定的 4 个群。微信数据库保持只读，候选先人工确认后进入正式事项。

## 使用方式

打开「设置 → 微信采集 → 添加群」：搜索群名，选择高/中/低优先级，点「加入」，确认具体群名及群 ID 后才授权读取正文。搜索只查询 contact/session 元数据，不打开消息分片，也不读取会话预览。新群首次回溯最近 24 小时，之后按独立 cursor 增量读取。

授权群卡片支持即时启停、调整优先级和移除。移除保留消息、Evidence、候选、正式事项和 cursor，重新授权同一群后接续。群 ID、分片水位、成功时间、错误和本次 AI 调用放在折叠区。预算/总开关单独保存，未保存的输入不会被群操作覆盖。搜索失败可重试，设置冲突保留输入，明确放弃后才重新读取。

## 真实 4 群结果

以下是本轮新增数据，不包含之前已经保存的 11.8 体测等历史事项。

| 群 | 优先级 | 新消息 | 本地过滤 | DeepSeek 请求 | 模型 Candidate | 分片水位 |
|---|---|---:|---:|---:|---:|---:|
| 26车辆2班（公告群） | 高 | 3 | 0 | 1 | 3 | 247 |
| 26车辆2班（闲聊群） | 中 | 0 | 0 | 0 | 0 | 1320 |
| 26车辆工程年级群 | 高 | 18 | 1 | 5 | 4 | 313 |
| 26下半年周四89节体能班 | 中 | 24 | 7 | 3 | 1 | 31 |
| 合计 | | 45 | 8 | 9 | 8 | 各自维护 |

闲聊群最近活跃时间为 10 月 21 日，首次回溯窗口内没有新消息；只记录分片水位 1320，没有取回更旧正文。它的 last_local_id 为 null，表示本轮没有正文消息；分片 watermark 已正常建立，后续新 local ID 会继续增量采集。没有为了验收扩大到 24 小时以外。

9 次请求属于首次回溯中不同群、不同时间段的上下文批次。不能简单按“群数”计费：本次 1 + 0 + 5 + 3，实际总用量回执为 43,478 tokens。所有群共享原有每日 30 次请求 / 100,000 tokens 上限。

连续 3 轮真实手动扫描均为 no_change，新增消息 0、正文查询 0、模型调用仍从 9 保持为 9。后台随后按原有 15 分钟计划运行。00:00–06:00 静默规则保留。

## 归属、去重与确认边界

- 每群配置、绑定、分片 cursor、扫描状态和错误独立。失败群不会中断其他群。
- source、primary/context、batch、snapshot、候选和 Evidence 按 chat_id 隔离。model 只能引用本批提供的消息和 block，不能跨群关联旧候选。
- 原始 message identity 保留 account scope / chatroom / shard / local_id / server_id；sender 和不可变原文继续随 Evidence 保存。
- 实际数据库审计：跨群 batch message = 0，跨群 snapshot mapping = 0，错误 source 归属 = 0，重复稳定 message identity = 0。
- 本轮新候选自动确认数 = 0；新建正式 Event / Task / Deadline 均为 0。8 条候选仍在待确认。
- 微信 messages_sent = 0；unauthorized_message_bodies_read = 0。

## 闲聊与模型成本验证

真实数据本地过滤了 8/45 条；真实闲聊群在获准的 24 小时窗口内没有新正文，因此本轮没有真实高流量聊天可测。

隔离 SQLite 压力场景投入 1,000 条收到、哈哈、表情及闲聊，再加 1 条作业：1,000 条不作为分析主消息，只保留有界相邻上下文；只有 1 次模型调用和 1 个 Candidate。作业、调课、考试、报名、截止、集合、材料提交、@所有人、明确时间和补充说明在高/中/低优先级下均保留。

6 群 / 2 群变化场景只分析这 2 群，重复无变化循环不再调用模型；未变化群的旧 pending job 不能占用变化群的调用名额。高优先级保留更多不确定短消息；低优先级减少普通短闲聊及非紧急 P2/P3 手机推送，所有优先级仍经过 Evidence / Review。

## Bark

标题为「Attention Desk · 微信」，正文使用简短事项标题和已知截止时间，来源群作为 subtitle。考试、报名等类别里明确要求执行且有 Evidence 的行动同样可以推送，避免类别标签遗漏真正的任务。普通信息留在工作台；紧急、当天截止及临时变化使用 timeSensitive。推送不确认正式事项。

本轮真实年级群的重要行动候选已发送 1 条：state=sent，attempt_count=1，level=active，last_error=null。连续扫描、后台重启后没有增加同一 Candidate 的推送次数。其他真实群本轮没有达到手机推送条件的候选；没有把它们伪装成重要通知来凑多群推送。

两个不同来源群各发一次、Candidate 更新不重推、进程重启不重推、失败有限重试和低优先级策略，另有隔离的模拟 HTTP / SQLite 验证。先前 Bark 连接测试用户已明确反馈「已收到」。本轮候选发送成功以 Bark 服务端回执和持久化记录为证据，未将该回执等同于新的 iPhone 收到确认。

Device Key 继续走已有环境 secret 或 Windows DPAPI，API 返回配置状态，不返回明文。没有密钥进入 URL、普通日志、浏览器存储或 Git。

## 本轮修复

原公告群早上开始的 database_read_error 已定位为 Windows 子进程 CP936 输出遇到 Unicode/emoji 时失败。只将子进程 JSON 协议改成无损 ASCII 转义，恢复了原 cursor 后的采集；没有更换 DB 解密、增量查询或原始消息解析实现。

真实模型响应暴露了日期多带秒/时区、派生 block ID 被当作 message ID、以及中文引用 offset 计数错误。现只做确定性的本地规范化：同一已提供 block 中唯一的逐字 quote 可重定位；已知 block alias 可映射回其所属消息；唯一且已验证的 evidence 可纠正 ID 类型；北京时间零秒的 ISO 写法无损转换。错误文本、跨群引用、歧义/重叠引用、错误日期仍拒绝或保留 unknown。所有恢复结果仍待人工确认。已收到的响应直接本地恢复，没有为这些格式问题重发模型请求。校验失败也保存实际用量回执，避免预留 token 被误当作实际花费。

新授权始终绑定精确 chatroom ID，已有名称授权会锁定到原验证绑定。群同名、分页搜索或账号变化不会把正文读取切到其他群。

## 验证与产物

- 完整后端：1,151 passed，25 skipped；跳过项为已有可选环境/集成测试。
- 前端：37 passed；生产构建通过。
- 原有日常工作台浏览器：10 组流程通过，包括详情/编辑/确认、单候选确认、错误恢复、手机通知密钥和深色模式。
- 新群管理浏览器：7 组流程通过，覆盖搜索取消、明确授权、两群增量、启停、优先级、移除重加、冲突保留/明确重读，以及 390px 深色 sheet；无页面异常、无横向溢出。
- 设计静态审计与 DESIGN.md lint：0 errors / 0 warnings。

可复现的群管理验收：`python -m server.tests_wechat.multigroup_fixture`，再运行 `npm run test:browser:wechat-groups`。它使用独立合成数据库，mock DeepSeek，关闭真实 Bark，不接触真实微信。

本地截图与详细 JSON 在本轮可视化目录：

- `C:/Users/ericp/.codex/visualizations/2026/10/23/01a16715-9cd7-7982-96de-6f03cfeffa0c/multi-real-desktop.png`
- 同目录 `multi-real-search.png`、`multi-real-mobile-dark.png`、`multi-real-search-mobile-dark.png`、`multi-real-inbox.png`
- 同目录 `multi-real-audit.json`、`groups-browser-results.json`、`desk-browser-results.json`、`multi-premium-audit.json`

## 后续最值得优化

1. 闲聊群有新的真实高流量后，在既有授权与 24 小时边界内补自然流量验收；目前的千条压力结果属于合成测试。
2. 公告群的 3 条模型候选中有 2 条是「图片内容不可识别」的独立卡片。原图和来源已保留；下一轮可把这类卡片并入原通知的附件层，进一步减少待确认噪声。
3. 卡片可进一步区分“本次扫描新增”和“仍待处理的重要事项”，避免一轮 no_change 后的 0 被误解为没有未处理通知。

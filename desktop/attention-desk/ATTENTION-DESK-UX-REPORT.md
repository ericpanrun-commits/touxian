# Attention Desk 日常体验与 Bark 验收

2026-10-23 · 本机工作台：http://127.0.0.1:5173/?personal_view=today

## 1. 两个 source block 为什么只有一个 Candidate

核对的是原体测批次 e961b604-0988-480f-8ab9-0d7dc6f3b170，来源群为「26车辆2班（公告群）」。

| 来源块 | 消息时间（北京时间） | 实际原文 |
|---|---|---|
| WX_8a9ff664-a5e3-4211-a62e-752404863177 | 2026-10-21 21:11:19 | “邓嘉炜（文体委员）”撤回了一条消息 |
| WX_1a72bb16-f774-42be-b11e-8db2ab3c234a | 2026-10-21 21:14:19 | 11.8体测 |

第一块只有系统撤回提示，无法证明被撤回的原文与体测有关，也没有足够内容形成另一项通知。第二块才包含体测信息。DeepSeek 实际只返回一项，evidence_message_ids 仅引用第二条消息，结果为「11.8体测通知（日期无年份，信息不完整）」，confidence 0.35；年份、钟点、地点和面向人群均未补造。因此不是把两条不同通知过度合并。2/2 指处理了两个来源块，不代表存在两个事项。

当前依据分为两层：

- batching：同群按顺序组成上下文；可靠时间间隔超过 300 秒分批，默认最多 40 条 / 12,000 字符，带有限前后文。这里两条相隔三分钟，因此属于一个输入批次。
- Candidate：模型判断独立事项并返回多项；每项必须引用有效消息和精确 Evidence，并至少涉及新增 primary 内容。本地没有跨标题/相似度的模糊合并器。入库以 (batch_id, model_item_id) 唯一；同标识更新时保留 accepted/ignored 状态，_pending 占位候选会被正式分析结果替换。一个 batch 可以包含多条不同 Candidate。

代码依据：server/wechat_collect/batching.py、analysis.py 的 validate_output / project_output / _commit。以上代码没有因本次调查而改动。

体测候选在本次开始前已经是 accepted；本次真实库没有新增或修改正式事项。现在 Inbox 中另一条待整理通知是独立批次「砚遇霜降，“易”启秋谜」，既有分析任务停在 invalid_json；新界面如实显示「待整理 / 可手动编辑并确认」，不假装仍在整理，也未为此另发模型请求。

## 2. 重新设计的页面

- 首页：今天安排、未来七天截止、微信待确认、需要行动。真实历史逾期数据保留为低干扰提示。
- 导航：日常四个入口与设置；手机采用五项底部导航。
- Inbox：标题、两行内摘要、来源群与时间、优先级/状态、存在才显示的截止时间；主要操作为忽略、编辑、确认。
- Candidate 详情：点击卡片打开 sheet，原文、sender、Evidence 和 AI 理由在折叠区。
- 编辑/确认：标题、类型、日期、地点、行动五组核心字段；更多内容折叠。保留必要的参与决定和日期/相关性核对。
- 正式事项：标题、摘要、时间、行动、来源优先；原文、模型信息、日期细节、ICS 与历史折叠；编辑使用 sheet。
- 设置：简洁的 Bark 连接表单，原有学校通知/云端推送收进独立折叠区域。
- 视觉：冷浅灰、白色圆角面板、细分隔线、少量蓝色、本地系统字体；浅色/深色/跟随系统；克制动效与 reduced-motion。

## 3. 确认流程

卡片「确认」→ 同一个简洁核对 sheet →「确认并保存事项」。通常两次点击；需要修改时直接在 sheet 内编辑。只创建当前 Candidate，其他候选保留。

「保存修改」只保存草稿并返回同一 Inbox，不会提前创建正式事项。失败保留输入、支持重试；未保存退出有应用内提示；忽略可立即撤销。未知年份和不确定相关性不能悄悄变成确定日期/任务。普通 Notice 可以不创建行动。

## 4. Bark 接入

复用已有 BarkClient，通过官方 POST /push JSON 接口发送，新增独立微信候选 outbox，不改变学校通知规则。官方接口依据：https://github.com/Finb/bark-server/blob/master/docs/API_V2.md

设置提供开关、HTTPS Bark Server、Device Key 和发送测试通知。复用已有后端环境密钥；从界面填写的新密钥使用当前 Windows 用户的 DPAPI 加密，存放于被 Git 忽略的本地库。API 只返回是否已配置，普通日志、URL、Git 和浏览器持久存储不含密钥。输入默认隐藏，保存后清空。

重要且明确相关的 required Task/Deadline 使用 active；紧急、当天截止、课程临时变更等使用 timeSensitive；普通信息不推。推送标题为 Attention Desk · 微信通知，来源群放 subtitle，正文仅简短事项内容。推送不创建正式记录。

通知状态持久化为 not_sent / sending / sent / failed，以 Candidate ID 唯一。成功终止，有限网络重试最多三次；无法确认是否已送达的结果停止重发。首次开启跳过已有历史，00:00–06:00 暂停自动候选推送。Bark 已在真实设置页开启。

Migration 013 只增加独立设置与通知状态表；现有迁移流程自动创建了 v13 升级前备份。原微信 DB 只读 adapter、allowlist、cursor、dedupe、Evidence、DeepSeek、采集时段及 messages_sent=0 保持不变。

## 5. 测试通知和验证结果

2026-10-23 13:10（北京时间）通过真实设置页发送一条连接测试。Bark 返回 accepted=true / sent，用户随后明确回复「已收到」，iPhone 真实链路通过。没有通过反复测试轰炸手机。

| 检查 | 结果 |
|---|---|
| 后端完整回归 | 1088 passed，25 skipped；既有依赖弃用提示一项 |
| 前端单元测试 | 37 passed |
| 生产构建 | 通过 |
| 严格界面静态审计 | 0 errors / 0 warnings |
| DESIGN.md 官方校验 | 0 errors / 0 warnings |
| 桌面 / 手机浏览器 | 1440×1000、390×844；浅色、深色、sheet 无横向溢出 |
| 页面身份 / 非空 / 框架遮罩 | 通过 |
| JavaScript 运行错误 | 0 |
| 详情 / 编辑 / 确认 / 忽略撤销 | 通过 |
| 保存失败 / 读取失败 / 重试 | 通过，输入保留、导航可用 |
| 日期 / 相关性人工校验 | 无年份日期和未知相关性阻止错误确认，焦点到对应字段 |
| 键盘 / Escape / 未保存保护 | 通过；native select 保留系统行为 |
| 候选隔离 / 模型调用 | 合成数据仅确认指定事项，其他保留；调用数不增加 |
| Bark 隐私 / POST / 重启去重 / 重试 | 19 项新增测试通过，已包含在完整回归 |
| 真实微信状态 | database adapter；授权群 1；messages_sent=0；unauthorized_message_bodies_read=0；automatic_formal_writes=false |

Browser 插件在本次环境不可用，按验证技能使用 Playwright。所有浏览器写入测试都先验证 synthetic_only，仅使用项目已有独立 HTTP/SQLite/模型夹具。唯一真实外发是用户授权的 Bark 连接测试；真实微信和正式事项未被测试改写。

主要复验入口：npm run test:frontend、npm run build、python -m pytest -q -p no:cacheprovider（项目 venv）；浏览器先启动 server.tests_wechat.workbench_fixture:app 的全新隔离实例，再运行 npm run test:browser。静态审计与 DESIGN.md 官方 lint 单独通过。原 test:browser:official-bark 保留原学校通知测试入口。

## 6. 后续值得优化的 UX

- 给手机通知增加经过验证的安全候选直达入口；目前用户收到 Bark 后手动打开 Attention Desk，未自动开放新的网络服务。
- 将历史验收数据与真实日常事项提供可见的归档/筛选入口，避免旧任务混入行动列表；本次没有自动清除用户记录。
- 对长期待整理通知增加更明确的恢复入口和标题辅助；当前失败内容仍可手动编辑确认，底层分析任务保持现状。
- 当前手机宽度已实测，完整 iPhone Safari 工作台交互仍值得在安全访问入口就绪后补一轮真机验收。

后续可安装 Browser 插件，便于直接在应用内查看页面和完成同样的交互验收。

# DeepSeek 自动通知分析：条件 B 交接（2026-11-08）

结论：完成离线诊断和必要的目的地校验修复；**没有恢复真实自动分析，未达到条件 A**。按用户定义的条件 B 结束。未发起真实模型请求、读取真实密钥、上传群聊、写生产库、重启或部署服务；未修改采集逻辑、CloudSync、官网同步或 Eric OS UI。没有恢复旧长期 Goal，也不进入五群持续同步。

## 已读证据与暂停原因

- 发送安全提交 `5f516bd93b3b36a0b0229a8b7da313519961cfb6` 及 `wechat-send-handoff-result-20261108.md`：这是独立发送交接修复，不能直接视为 DeepSeek API 授权，本轮没有重复实现。
- `wechat-backlog-disclosure-20261105.md`、`collection-health-followup-20261105.md`：历史 22 个作业未获内容传输或付费授权。11 个预计无需新增视觉请求的作业仍全部包含上下文、发送者和画像，7 个包含 OCR 派生文字。历史估算不是当前待执行清单，也不是费用许可。
- `wechat-ai-screening-acceptance-20261108.md` 及本机 `artifacts/p0-20261104/ai-screening-acceptance-20261108/{label-evidence,read-only-results}.json`：复用已有 25 条人工标签（9 有效、16 无关，隐私均 clean），没有重新标注。7 条可追溯有效通知只有 `_pending`，没有可重放的最终模型分类。第五群有效标签仅有序号、缺稳定关联，不将计数或序号当永久标签。
- 先前本地 Qwen 验收受模型未加载和内存保护线限制；这与 DeepSeek 的内容授权暂停是不同问题。现有 `AnalysisQueue → DeepSeekTransport` 路径未发现 Qwen/LM Studio 调用或回退，本轮不引入本地模型。
- 既有真实样本检查中，4 条整体敏感暂留、1 条适用对象锚点变化未解决；不能据此宣称脱敏通过。上述数据是读取已有验收产物，本轮未再次读取生产消息或运行其数据库评估脚本。

## 管线核对

| 项目 | 现状及边界 |
| --- | --- |
| 脱敏 | `wechat_collect/analysis.py:build_messages` 直接携带 snapshot 的文字、sender、旧上下文、user_context；没有接入可审核的最小化请求投影。云同步预览脱敏不能代替模型入口脱敏。直接删改正文会破坏当前引用偏移与原文证据校验，不能简单套一个正则后上线。 |
| API 授权 | 配置密钥由环境变量或 Windows DPAPI 提供；密钥存在不等于用户许可。调度及 transport 均检查付费暂停，历史恢复另有 dispatch_hold；这些不是逐请求的内容、用途、有效期和费用授权。没有修改实际暂停配置，也没有检查真实账号余额或连通性。 |
| 预算 | 代码默认每天 30 次 HTTP、100,000 tokens、5 次视觉、单并发、每周期 4 批。每次请求前事务预留，已知 usage 替代预留，未知结果保留预留。它是请求/token 约束，不是货币硬上限，也不代表本机当前配置已获批准。 |
| 重试 | 队列一次 transport 调用只尝试一次；429/502/503 按 Retry-After 延迟，作业累计尝试小于 2 才重试。401/402/403 不自动重试。超时、连接中断、取消或存储异常的未知结果不自动重复付费。已保存响应恢复不再请求模型。 |
| 输出 | `wechat.v1` 严格结构：items/evidence、重要性、相关性、action/fyi/ignore、完整性、等待上下文、事项/时间/行动及引用。引用校验 message/block、逐字 quote 与 Unicode 偏移，日期核对来源时间；OCR 日期要求复核。结果进入候选审核，正式事项仍需明确确认。 |

## 本次修改

仅在分析入口收紧官方目的地：HTTPS、api.deepseek.com、默认或 443 端口，以及根路径、/v1、/chat/completions、/v1/chat/completions。拒绝同域异常端口及其他路径，拒绝发生在读取密钥和费用预留之前。未修改共享 transport 或其他业务入口。

新增 `server/tests_wechat/test_deepseek_destination.py`，覆盖正常目的地、拒绝目的地零请求/零预留、429/502/503 限次等待、401/402/403 不重试、token 预算拒绝。

新增 `scripts/test-deepseek-offline.py`，复用现有测试样本和模拟响应，覆盖新测试及原 pipeline、cost_review、backlog_cost_plan、attention_quality、recovery_progress、recovery_controls。仅测试进程设置假密钥，阻断外网/DNS，仅保留 Windows asyncio 所需数字回环连接。测试库由 tmp_path 创建。

## 验证与复现

完整回归：**93 passed，12.87 秒**。只有已有 Starlette/httpx 弃用警告。XML 位于本机忽略目录 `desktop/attention-desk/artifacts/deepseek-offline-20261108.xml`。之后将 token 拦截测试的日额度改为配置允许的最小值 4096，补跑该用例：**1 passed，92 deselected，0.89 秒**，属于重复验证而非新增覆盖。

首次环境检查发现系统 Python 没装 pytest，使用项目 `desktop/.venv/Scripts/python.exe`。初版网络阻断影响 Windows socketpair，调整为仅允许数字回环；受限进程仍卡在 socketpair accept，停止后用已批准的本机执行完成测试。默认临时目录的权限错误通过独立工作区目录解决。新测试首轮漏掉显式 enqueue，修正夹具后通过；未放宽业务断言。

从 `desktop/attention-desk` 用 PowerShell 7 执行：

```powershell
$testTemp = Join-Path (Get-Location) ('artifacts/deepseek-test-' + [guid]::NewGuid().ToString('N'))
../.venv/Scripts/python.exe scripts/test-deepseek-offline.py --basetemp=$testTemp --junitxml=artifacts/deepseek-offline-20261108.xml --tb=short
```

通过的是离线管线与安全机制测试，不是 DeepSeek 对真实标注的准确率，也不是脱敏漏检率。没有用模拟输出填补真实分类空缺。

## 最小后续方案及启用条件

1. 保持付费暂停，在分析模块内准备独立的最小化文本请求投影：移除非必要发送者和画像，限制主消息/必要上下文，默认排除图片和未审阅 OCR；保留脱敏前后证据映射，验证日期、行动及适用对象未改变。整体暂留内容继续拒发。不得改写生产原文来迁就模型引用。
2. 用已有标签与私有映射验证投影；缺失的稳定关联只能从原始执行材料补证，不要求用户重新标注。缺证部分明确不计准确率。当前不能离线生成尚不存在的 DeepSeek 真实输出。
3. 在本地先形成完整、可审阅的请求清单，固定来源/消息/快照/上下文/画像/模型/配置/请求摘要，并实现消费和撤销校验、每次尝试费用预留及未知结果不退款；不能只把 paid_ai_paused 改为 false，也不能把 CloudSync 的许可当 API 许可。
4. 准备好上述具体清单后，单独取得**向 DeepSeek 传输该脱敏内容**及**付费调用**的明确授权：用途、批次、有效期、模型、最多 HTTP 次数、token 与货币总上限、是否允许有限重试。OCR/图片、其他来源、历史积压和持续自动运行都不能从一次文本试验授权推定。执行时再核实服务价格和剩余预算；本轮未报价、未请求笼统授权。
5. 在批准范围内先做有限真实验证，对照既有可追溯人工标签及引用准确性。通过后再另行决定自动分析范围；云同步和 UI 完全独立，不在本交接后自动启动。

## 提交与回滚

仅提交上述分析校验、测试、离线运行脚本及本交接记录；工作区已有的采集、CloudSync、UI 改动和此前未提交验收记录均不纳入。本次未部署，回滚只撤销本次提交，不改数据库、游标、密钥、暂停状态或既有发送安全修复。

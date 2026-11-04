# Attention Desk P0 采集恢复记录（2026-11-04）

本记录对应既有 `D:/Eric OS/docs/diagnostics/school-notification-audit-20261104.md`。当前状态：**代码与真实隔离验收完成，正式启用等待明确的云端传输授权；整个目标尚未验收完成。** 没有把隔离库中的成功写成正式恢复。

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

代码提交：`28e50bc`（仅本次 12 个文件，本地提交，未推送）。

- `server/wechat_collect/db_adapter.py`：候选证书可隔离验证，正式默认路径保留；新版证书需真实证据及核心摘要。
- `analysis.py`、新增 `recovery.py`：历史 job、历史输入、收到过响应、未知结果、已替代/取消输入不进入隐式付费请求。停更超过一天后的补采 scan 另加预算审批标记，防止恢复采集时顺带大量请求。保留已完成 Vision 转写后继续文本分析与显式重开媒体预览的原路径。
- `service.py`、`store.py`：原 Collector、原 ID、去重、过滤、入库继续复用；读取前失败也写入既有 `wechat_chat_scan`，以后可计算独立来源失败次数。无需迁移 Schema。
- 新增 `health.py`，接入 `server/main.py`：独立于采集循环的每分钟健康任务；复用 WindowsNotifier，不重建推送系统，不修改 UI。
- `tests_wechat/test_collection_health.py`：空扫、夜间、连续失败、告警去重、未知投递、历史/补采零付费保护、证书字符串伪兼容、同步摘要确认、同步观察失败隔离。
- `scripts/wechat_compatibility_probe.py`、`wechat_recovery_acceptance.py`、`wechat_backlog_plan.py`、`wechat_recovery_checkpoint.py`：可复核的只读/隔离验证与备份入口，不负责模拟消息。

没有修改现有未提交的 UI、两个同步模块或 Preview 文件；未触及 IELTS / Life Ledger。

## 健康监控契约与边界

本机 `/api/personal/wechat/status.health` 提供逐授权群：最近扫描、最近成功扫描、最近原消息入库、连续失败、最近错误、待分析与分析错误、状态及是否需告警。`no_change` 为正常空扫，不按最近通知时间判断故障。

本机 `/api/health?check_integrations=false` 的 `collection_health` 在监控运行后提供：上述来源状态、待同步记录数、同步错误、前端可见性证据状态。同步按具体 Raw/Inbox source_hash 与已有 worker publish 确认摘要对照，心跳或 HTTP 200 本身不等于全部同步。前端没有浏览器证据时记 `unverified`，不谎称已显示。

告警条件为连续失败至少 3 次，或有效采集时间超过 45 分钟未成功扫描；00:00–06:00 不主动采集、不告警，并从陈旧时间计算中排除。正在进行的正常限时扫描给予最多 5 分钟宽限。复用 Windows 原生通知，发送前持久化 claim；同来源六小时内不重复、全部来源合并、全局最短间隔 15 分钟。通知结果未知也保留 claim，避免重启风暴。OS 接受不代表用户实际看到。

旧记录没有每群失败历史，只保留最后群状态；过渡期明确标注 `legacy_global_lower_bound`，不伪造历史精确群级计数。启用后新失败记录为 `per_source_journal`。

云端同步观察/凭证失败不会阻止微信健康判断与告警。官网生产采集由既有云 worker 管理，本地旧镜像明确标为 `external_observation_required`。本次已只读读取 29 个云官网来源独立状态（例如教务通知连续 11 次 http_error）；**尚未把云官网健康持续接入本机告警，也未修复官网到 Eric Raw 的缺失适配**。学习通目前仅有手动学习记录入口，不伪称具备自动通知扫描。

## 验证结果

| 要求 | 当前证据与结果 |
| --- | --- |
| 当前微信真实访问 | 通过；沙箱外现有用户会话可读，5 授权群无越权 |
| 正常产生新 Raw | **隔离库通过**：正式库备份中 390→691 消息，660→742 Raw（+82），77→159 批次 |
| 重复扫描不重复入库 | **隔离库通过**：第二轮 no_change，消息/Raw/批次/attempt 数量完全不变 |
| 原处理流程 | 原过滤与分批通过：seen/new=301，filtered=51；原快照/候选占位保留。模型处理及最终确认未执行 |
| 数据完整性 | 隔离库 FK 错误 0；Matter 11、Task 23、课程10、Review13 全内容哈希前后相同 |
| 原本机停更被识别 | 正式只读数据五群均 failed；旧历史计数口径明确，不凭进程正常判成功 |
| 离线回归 | 完整微信+启动器+云投影套件 326 passed；随后最后新增的观察隔离用例随 9 项健康测试通过。报告为 `pytest-final-results.xml`、`pytest-health-final.xml` |
| 调度不变 | 保留原 15 分钟、00:00–06:00 静默与 06:00 catch-up；相应原离线测试通过 |
| 新 Raw 正式入库与云同步 | **待正式启用，未通过** |
| 同一新 ID 的 Eric OS API 原文 | **待正式启用与用户认证验证，未通过** |
| Inbox 全部来源实显 | **待浏览器真实数据验证，未通过** |
| 正式告警投递 | **未启用/未发送**；单元测试不代替实际 OS 或手机投递 |

真实隔离验收：`artifacts/p0-20261104/real-capture-acceptance.json`。隔离库仅在被 Git 忽略的 artifacts 内，未被当作正式库。全部验证使用真实授权群消息；离线回归中的合成消息仅在各测试临时库。

## Production、审批及回滚

正式启用方案：验证任务身份和无在途模型请求，停止原 `AttentionDesk-Local` 任务，原子替换候选证书，启动同一任务。Collector 将把真实群原文、来源 ID/时间及原链路保存的相关附件写入现有 SQLite，并由现有 worker 同步到既有私有 Supabase Project `mantuehuzptactymjpqg`，API 为 `https://mantuehuzptactymjpqg.supabase.co/functions/v1/eric-app-api`。用户/工作区、访问控制、去重和调度配置不改。会有短暂本机服务中断；云端内容发送不能通过代码回滚撤回。补采/旧作业不隐式付费，不自动确认 Matter/Task。

**自动审批拒绝了上述启用动作，命令未执行。** 理由：虽有概括恢复授权，但缺少对这批敏感 Raw 发往该具体云端目的地的明确授权，并存在正式服务中断及不可逆数据扩散风险。没有通过其他命令绕过拒绝。生效证书仍为 4.1.15.13，正式服务仍运行旧进程。本次源码修改没有在正式进程载入；没有重启、替换生效证书、写正式 Raw、部署前端/Preview、改变生产配置或发送新 Bark/Windows 通知。

启用前完整 SQLite 备份、旧证书及配置位于 `artifacts/p0-20261104/checkpoint-025809/`，清单 `artifacts/p0-20261104/checkpoint.json`。备份是在正式库只读连接上通过 SQLite backup API 取得，包含 WAL 已提交内容。

代码回滚：`git revert 28e50bc`；仅涉及上述提交，不包含用户此前未提交改动。尚未启用时无需动数据库或服务。若日后启用，需要受控停原任务、将 checkpoint 内旧报告原子恢复并启动同一任务；保留新采集历史与原 ID，不还原旧库覆盖新数据、不删除消息、不倒退游标。云端同步过的原文不会因回滚代码消失。

后续必须完成：明确启用与既有云端传输授权；正式新 Raw/去重验收；按原 ID 与原文哈希对照云记录和经过授权的 API；真实 Inbox 全部来源渲染；实际健康检查启用与故障识别；付费积压在批准范围内有界执行，或记录用户不批准的决定。目标保持 active。

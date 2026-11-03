# 微信只读采集：实施状态与运行边界

**2026-10-22 最新：DB_READ = PASS，已切换数据库为正式主路径并启用本机后台任务。** `wechatauto-replica` 固定 commit d322c4a9 的只读核心在微信 4.1.15.13 读出目标群最近20条，发送者/顺序/稳定ID验证通过，真实增量轮次无变化时正文查询和模型调用均为0。当前 allowlist 只有「26车辆2班（公告群）」。实施、隐私边界、媒体限制、迁移与运行证据见 [DB-0 报告](WECHAT-DB-0-REPORT.md)。以下 UIA 记录为历史实验，不作为日常采集路线。

**18:55 最新状态：ForceUIAForQt=1 跨 Windows 完整重启验证已完成，当前本机建议 FALLBACK_TO_CUSTOM。** 正确目标群窗口的身份、渲染子窗、Raw View 和标题 OCR 均已交叉核验，聊天业务节点仍为 0，两个 API 均实际调用失败。实验设置已恢复，完整证据见 [WECHAT-POST-REBOOT-RESULT.md](WECHAT-POST-REBOOT-RESULT.md)。生产采集保持关闭，以下早期实验记录保留。

更新：2026-10-22。**最后 Qt accessibility pass 已完成，当前本机建议 FALLBACK_TO_CUSTOM。** 经用户及 UAC 授权临时设置 64 位 ForceUIAForQt=1，保持 SPI / HKCU 标志、重启微信并由用户手动登录后，聊天树仍为 2 个 Win32 后代、0 个 mmui，两个读取 API 均在窗口识别阶段失败。设置已恢复并独立核验。未重启 Windows，不对其未验证效果作结论。最新记录见 [QT-UIA-ACCESSIBILITY-PASS.md](QT-UIA-ACCESSIBILITY-PASS.md)，早期五 API 失败保留于 [PYWEIXIN-FEASIBILITY.md](PYWEIXIN-FEASIBILITY.md)。生产采集仍关闭，本轮不继续扩展大面积 OCR。

## 当前状态

已实现可离线验证的配置、v10 迁移、消息实例与有序重叠去重、保守过滤、Batch、一次合成补读、模型契约、受限 Vision 转写、调用账本、原文/Evidence/人工复核接入、设置界面及后台启动工具。Astra 总计划仍有效；本轮没有重新设计这些模块。

真实读取适配器保持拒绝未验证读取的行为。前端不能启用未验证采集，绑定接口拒绝未经验证的绑定。Fake Adapter 只允许测试代码显式注入，配置和 API 都不能选择它。正式数据库仍为 v9，没有启动使用正式库的新版后端。

## 当前设备与 pyweixin 评估

运行中的微信版本为 4.1.15.13，目标测试群为「26车辆2班（公告群）」。原生类 `Qt51514QWindowIcon`，UIA FrameworkId 为 `Win32`，渲染子窗为 `MMUIRenderSubWindow`。主窗口 UIA 只有 Window 和两个 Pane；Text / Scroll / Selection Pattern 不可用，标准 MSAA 也没有聊天对象。

| 实际测试 | 结果 |
|---|---|
| pywechat127 1.9.8 / pyweixin，固定 commit 8589baa | 上一轮五 API 全部 FAIL；本轮初始化后另实际复测 pull_messages / check_new_messages，均失败 |
| 原环境 / 临时屏幕阅读器与 Narrator 标志 | 各执行五个 API，均在窗口或聊天控件查找处失败 |
| Qt wake 引用 + 实际前台 / 后台 / 最小化 | 三次 pull_messages 均 FAIL；session_list、ChatSessionCell、SessionCell 均为 0 |
| 临时设置恢复 | pass；screen_reader=false、HKCU RunningState=0，结束后再次只读核对 |
| HKLM ForceUIAForQt / 重启微信 | 已授权临时启用 DWORD=1，完整重启并人工登录；聊天树仍未暴露。已恢复原状态，未重启 Windows |
| 免打扰 / 手机已读 / 新到达消息 | NOT_TESTED；消息读取前置条件失败 |

使用独立 `artifacts/pyweixin-spike/venv` 安装审阅后的固定源码，没有把 pyweixin 加入生产依赖；完整安装清单随诊断记录保存。没有发送、回复、草稿写入、群设置修改或真实模型调用。

## 保留的 Phase 0 调研

最初纯 UIA / MSAA 和过窄的标题 OCR 结果不能代表所有本地视觉路径。用户扩大 Phase 0 探索权限后，前台本地截图/OCR/CV 已在指定测试群确认标题，并完成有界滚动及重叠消息锚点验证。`hybrid-history-02/history-report.json` 记录 8 次捕获、8 次本地 OCR、6 次小步滚动；该有限 spike 为 **IDLE_ONLY**，不是生产 Collector 验收。

发送者、发送时间、媒体引用、新消息实际到达、长时间历史覆盖仍未完整验证；不会因为这个局部结果打开生产采集。本轮优先 pyweixin 评估，没有继续扩展自研消息解析。既有调研、脱敏几何图和可复用代码全部保留。

工具按用途区分：

- `scripts/wechat_uia_spike.py`：有界、被动 UIA/MSAA 结构检查，遵守静默和空闲。
- `scripts/wechat_header_spike.py`：局部标题截图/OCR，保留过窄与扩大标题区域的对照结果。
- `scripts/wechat_phase0_probe.py`、`scripts/wechat_phase0_cv.py`：用户明确授权的主动 Phase 0 探索，不能作为正式运行策略。
- `scripts/pyweixin_readonly_spike.py`、`scripts/pyweixin_environment_spike.py`：隔离读取 API、临时辅助功能恢复及 Qt 窗口状态矩阵。

主环境的 UIA 依赖锁定于 `server/requirements-wechat.lock.txt`；先前本地 CV 诊断依赖锁定于 `server/requirements-wechat-spike.lock.txt`。这些安装不代表生产读取器通过，pyweixin 的依赖另留在隔离环境。

## 不可绕过的边界

- 默认 `enabled=false`，目标群为空；只允许已配置并经实测绑定的群。
- Asia/Shanghai：06:00–24:00 每 15 分钟；00:00–06:00 不做微信探测、UIA、截图、OCR、Vision、DeepSeek。06:00 补扫；23:00 核心群补漏。
- 静默由 `SchedulerPolicy` 集中控制。状态接口只读数据库，不探测微信。
- 当前用户交互式会话，空闲至少 120 秒；真实窗口操作尚未认证。
- 没有发送、键盘输入、粘贴、点击发送或修改群设置的 API。聊天 Edit/发送区域列入 denylist；当前适配器不提供任何控制写入动作。
- WorkBuddy 与旧微信 Bridge/exporter 不初始化。旧 `/api/wechat/*`、`/api/inbox*` 返回 410，独立 JSON/ZIP 导入保留。
- 模型只使用现有官方 DeepSeek 配置，强制非 Thinking；未经批准的其他 Endpoint 不接收微信数据。
- 默认每日最多 30 个 HTTP 尝试、100,000 token、5 个 Vision 尝试，并发 1；达到上限留队。
- 全部候选先复核。模型成功、置信度高、恢复或重试都不会自动写 Event/Task/Deadline/Reminder。
- 随机 UUID 只标识数据库实例；跨扫描去重依靠内容签名、消息顺序、邻近锚点和 cursor。
- 角色断言为 confirmed true / confirmed false / unknown；unknown 不当作 false。

## 数据与恢复

迁移 010 新增微信采集表，保留既有 001–009；增加 `image_vision` 来源类型。迁移前使用 SQLite backup API 备份，失败事务回滚，末尾检查外键。

所有开发与浏览器验收使用临时数据库，没有主动升级用户实际数据库。真实 v9 库另经 SQLite backup API 复制后迁移验收：35 个历史表内容一致，外键检查通过，正式库仍为 v9。运行新版服务首次初始化正式 v9 库时，才会备份并应用 v10。

采集 cursor 与分析状态分离；读取失败/无法找到旧锚点为 partial/gap，不冒充无变化。已落盘但尚未组批的消息从持久化状态恢复，不重新读取微信；图片取得失败不会丢掉相邻文字。消息正文与 Evidence 映射不可变。消息发送时间未知时不使用采集时间替代。

模型响应有本地恢复文件（Windows 使用 DPAPI）；已付费成功响应写库失败时只恢复落盘，不重新请求。超时/断线结果未知不自动重发。JSON 错误不自动付费修复。

图片原文件进入内容寻址存储；OCR 独立于原文且保留来源。OCR 失败才允许最多两张候选局部图片进行一次 Vision 转写，保存为新的不可变原文块，继续联合文本分析。每张图片自动 Vision 最多一次；大于 4 MB 或长宽比超过 4 的图片先留待人工核对。没有取得的图片保持待人工核对，不花文本模型 token 猜图。

## 界面与启动

个人工作台 → 设置 → 微信采集 / 用户画像；Today 展示微信候选。配置尚未认证时，扫描和启用控件不可用。候选可继续使用现有复核与精确原文高亮。

配置位于个人数据库同目录的 `wechat.yaml`，画像位于 `user-context.json`。保存采用版本校验；未保存输入受现有导航保护。

`scripts/register-desktop-startup.ps1` 可注册当前用户登录启动；`-Check` 检查、`-Remove` 移除，只作用于当前项目所属任务。脚本不会立即启动程序或登录微信。本次没有注册登录任务，也未启动使用正式数据库的后台服务。

后台入口是 `pythonw -m server.desktop_entrypoint`，仅监听 loopback，使用命名互斥锁。开发启动脚本复用已识别的常驻后端，退出时不会误停它。

## 尚未验收

1. 真实 UIA 选择器、群绑定、字段提取、消息分页和后台/最小化能力。
2. 真实图片取得与屏幕局部定位；fallback 只能在实测证据上增加。
3. 真实用户活动中止、微信恢复、系统睡眠恢复及登录任务的设备验收。
4. 跨群事件合并和正式事项更新，需要单独验证来源归属，当前不会自动修改正式事项。
5. 单条超过 12,000 字的通知目前保留完整原文并转人工整理，长消息的自动多片分析尚未实现。
6. 连续 7 天真实核心群试运行。

## 验证

新增测试在 `server/tests_wechat/`；只使用 Fake Adapter、MockTransport 和临时库，测试夹具禁止真实 Windows 探测与 native OCR。

前端辅助测试：`npm run test:frontend`。界面验收：`node tests/wechat-browser.mjs`，默认连接隔离服务 `127.0.0.1:18765`，仅合成 API 数据。没有 Browser 插件，使用 Playwright。

实际测试数量、最终变更清单与截图位置见 `WECHAT-IMPLEMENTATION-REPORT.md`。

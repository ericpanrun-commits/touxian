# 微信完整 Windows 重启后 Qt UIA / pyweixin 实测结果

2026-10-22，Asia/Shanghai。**最终建议：FALLBACK_TO_CUSTOM，适用于本机当前微信 4.1.15.13 / pywechat127 1.9.8（pyweixin）。**

这次已经验证 ForceUIAForQt=1 保持经过完整 Windows 重启，重新枚举了全部微信进程和原生窗口，交叉核验 UIA/Win32 PID、HWND、Raw View、渲染子窗，并通过本地 OCR 确认当前目标群标题。登录态聊天 Qt 控件仍未暴露；两个读取 API 的实际调用共同失败于窗口识别。不是沿用旧句柄或先前未重启 Windows 的结论。

此结论针对当前客户端与环境，不能确定具体内部原因或断言所有微信 4.x 都不可用。没有取得可供 pyweixin 使用的文本或发送者，因此当前也没有足够证据采用“pyweixin 主路径 + 少量 OCR”的 HYBRID。已有本地视觉调研保留，完整生产消息读取器尚未认证；本轮不继续扩展大面积 OCR 消息解析。

## 1. 系统启动与实际配置

| 证据 | 本次实测 |
|---|---|
| 单一 64 位 DWORD 写入 | 18:39:08，ForceUIAForQt=1，已有用户授权及 Windows UAC |
| Windows 完整启动时间 | **18:47:15.5**，晚于写入时间；由 Win32_OperatingSystem 实际查询 |
| 微信主进程启动 | **18:48:59.589844**，晚于 Windows 启动时间 |
| 重启后配置首次读回 | 18:50:01，64 位实际为 **REG_DWORD 1**；32 位键/值不存在 |
| 结构和两个 API 测量前后 | 实际读回均为 DWORD 1；不是仅凭日志推测 |
| SPI / HKCU RunningState | 本次临时开启 true / 1，测量结束恢复 false / 0 |
| 默认桌面 / COM | 先核验 WinSta0/Default，再使用新进程和新 UIAutomation 客户端 |
| Windows 重启方式 | 用户手动完成；脚本没有关机、重启或自动登录动作 |

上一次 18:21 启动时注册表值已经被旧实验回滚，不能算作该值保持为 1 的验证。本轮的 18:47 重启已经补齐这一前置条件。

## 2. 正确登录态窗口与多进程排除

微信实际路径为 `C:\Program Files\Tencent\Weixin\Weixin.exe`，版本 **4.1.15.13**。当前 5 个进程为 30928、32852、29864、10736、24188；后四个为 30928 的子进程。各进程都在本次 Windows 启动之后创建。

| 字段 | 当前实测 |
|---|---|
| 主窗口 PID / UIA ProcessId | **30928 / 30928** |
| 主窗口 HWND / UIA NativeWindowHandle | **68308 / 68308** |
| Win32 / UIA ClassName | **Qt51514QWindowIcon / Qt51514QWindowIcon** |
| UIA FrameworkId | **Win32** |
| 原生标题 | 微信 |
| 状态 | 可见、未最小化、顶层窗口，无 owner |
| 渲染子窗 | HWND **68310**，ClassName `MMUIRenderSubWindowHW`，PID 30928，根为 68308 |
| 辅助子窗 | HWND **68312**，2×2 像素，PID 30928，根为 68308 |

枚举没有预先按 Qt ClassName 过滤，而是覆盖所有当前微信 PID 的全部顶层和原生子窗口。只有一个符合条件的可见主窗口；其余顶层窗口为隐藏的托盘、电源、输入法等系统消息窗口。直接对渲染子窗和辅助子窗重新获取 UIA，也没有聊天子树。

仅截取客户端标题带 `[425,48,1277,132]`，852×84 像素，DPI=144；本地 Windows OCR 得到 `26 车 辆 2 班 （ 公 告 群 ） （ 37 ）`，经只忽略空白、括号样式和末尾人数的精确匹配，确认 **26车辆2班（公告群）**。未截图消息正文、未访问聊天输入区域，也未用 OCR 代替消息读取。

这排除了旧句柄、登录页、错误 PID、隐藏托盘窗口及遗漏渲染子窗的可能。

## 3. UIA Tree

| 项目 | 本次完整重启后结果 |
|---|---:|
| 主窗口直接子节点 | 2 |
| 主窗口 descendant count | **2** |
| mmui 节点，包含根 | **0** |
| mmui::MainWindow | 0 |
| session_list | 0 |
| mmui::ChatSessionCell / mmui::SessionCell | 0 / 0 |
| mmui::ChatListView / mmui::ChatMasterView | 0 / 0 |
| mmui::ChatInputField / chat_input_field | 0 / 0 |
| Raw View 节点，包含根 | 3，均为 Win32 外壳，mmui=0 |
| 渲染子窗 / 辅助子窗的后代 | 0 / 0 |

Raw View 未截断。指定属性条件查询与实际遍历相符。新进程、ControlFromHandle、保持根引用以及 SPI / HKCU 标志均未使登录态聊天 Qt 业务对象出现。

## 4. pyweixin 实际复测与结构化结果

使用隔离 venv 中的 pywechat127 **1.9.8**，固定上游 commit **8589baa049bb91d3a500602c167f07b2f8397a13**。未新增生产依赖。

| API | 本轮结果 | 实际调用 | 连接 HWND | 输入拦截 |
|---|---|---|---:|---|
| Messages.pull_messages，目标群最近 20 条 | **FAIL / NotFoundError** | 是 | 0 | guards=[] |
| Messages.check_new_messages | **FAIL / NotFoundError** | 是 | 0 | guards=[] |

两项都在 `Navigator.open_weixin` / 窗口识别阶段失败，未进入消息解析。输入拦截器没有阻断一个已经工作的读取流程。原生 HWND 存在，而 pyweixin 的 Qt 业务控件前提仍不成立。

实际结果摘要如下；`messages=[]` 表示没有取得消息，**不表示群里没有消息**：

```json
{
  "chat_name": "26车辆2班（公告群）",
  "read_status": "unavailable_due_to_window_accessibility_not_empty_chat",
  "error": "NotFoundError",
  "messages": [],
  "field_availability": {
    "sender": "not_observed",
    "content": "not_observed",
    "type": "not_observed",
    "time": "not_observed",
    "order": "not_observed"
  }
}
```

没有真实最近消息可输出或核对顺序；不提供模拟样例冒充结果，也不拿采集时间替代发送时间。免打扰、手机已读、新消息到达、图片/附件等运行场景仍未通过验收。本轮只重测了上述两个 API，早期其他三个 API 的失败结果仍保留在 PYWEIXIN-FEASIBILITY.md。

## 5. 恢复与安全

依照用户“保留至重启后复测，再恢复”的授权，18:54:55 通过 Windows UAC 执行单值恢复。18:55:08 普通用户独立核验：

- 64 位 ForceUIAForQt 及本次新建的空 Accessibility 子键恢复为不存在。
- 32 位视图未写，仍为键/值不存在。
- SPI=false、HKCU Narrator RunningState=0，无 Narrator 进程，恢复辅助进程已退出。
- **messages_sent=0**；没有聊天输入、草稿修改、回复、转发、群设置修改。
- DeepSeek / Vision 调用为 0。仅一次本地标题 OCR；没有 OCR 消息 Collector。
- 本轮未启动正式采集或写入正式 Event / Deadline / Task 数据，未注册后台任务。Astra 总计划和既有代码均保留。

本轮新增的受控注册表脚本已实际完成 Apply / Inspect / Restore 并通过独立读回，PowerShell 语法检查通过。没有修改业务模块或重跑无关前后端回归；上一阶段 104 项微信离线测试结果仍为历史记录，不冒充新的实机消息读取通过。

## 6. 原始证据

目录：`artifacts/pyweixin-spike/post-reboot-20261022-1850/`。

- `initial-tree.json`：重启后第一次新窗口发现及指定控件计数。
- `correlated-identity.json`：全部当前原生窗口、DPI-aware 几何、主窗口与两个子窗 Win32/UIA 对照、Raw View、本地标题 OCR、会话标志恢复，以及实际注册表前后读回。
- `actual-api-retest.json`：最新 Windows boot time、5 个进程及创建时间、配置实际值、两个 API 结果、字段可用性与会话标志恢复。
- `pull_messages/result.json`、`check_new_messages/result.json`：原始异常与调用栈，不含私人消息正文。
- `independent-restoration.json`：普通用户独立核验恢复结果。
- `final-summary.json`：本轮验收检查与最终推荐。

注册表备份与 Apply/Restore 日志：`artifacts/pyweixin-spike/force-uia-reboot-experiment/registry-before.json`、`status.json`。标题带截图：`artifacts/wechat-spike/post-reboot-20261022-1850-header/`。

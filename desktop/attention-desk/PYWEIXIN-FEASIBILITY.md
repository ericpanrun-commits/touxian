# pyweixin feasibility：本机实测报告

日期：2026-10-22，Asia/Shanghai。目标测试群：**26车辆2班（公告群）**。

**18:55 更新：本轮跨 Windows 完整重启实验已完成，当前本机最终推荐 FALLBACK_TO_CUSTOM。** DWORD=1 已保持经过 18:47 重启；当前真实目标群的 PID/HWND、渲染子窗、Raw View 和本地标题 OCR 已交叉核验，聊天 mmui=0，两个 API 再次实际调用均报 NotFoundError。设置已恢复并独立核验。详见 [WECHAT-POST-REBOOT-RESULT.md](WECHAT-POST-REBOOT-RESULT.md)。以下保留早期未重启 Windows 阶段的记录，其限制不代表最新实验仍未完成。

**最终推荐：FALLBACK_TO_CUSTOM，限定于本机微信 4.1.15.13 及已实测条件。** 最后一轮 Qt accessibility pass 已完成：在用户和 Windows UAC 授权下临时启用 64 位 ForceUIAForQt=1，保持 SPI / HKCU 标志并完整重启微信。用户手动登录后，聊天窗口仍只有 2 个 Win32 后代、0 个 mmui 控件；两个读取 API 再次实际调用均失败于窗口识别。原设置已恢复并独立核验。Windows 未重启，其效果仍未验证；不据此声称所有微信环境都不兼容。完整证据见 [QT-UIA-ACCESSIBILITY-PASS.md](QT-UIA-ACCESSIBILITY-PASS.md)。以下 13 次调用为早期记录，另有用户级 pass 的 2 次及 HKLM pass 的 2 次读取复测。

本次按新优先级暂停自研消息解析的扩展，保留既有 UIA/OCR/CV 代码与报告；没有将 pyweixin 加入生产依赖，也没有启用自动采集。Astra 总计划中的 staging、cursor、去重、过滤、Batch、DeepSeek、Evidence、复核和调度边界保持有效。

## Compatibility 与安装

| 项目 | 实际值 |
|---|---|
| 运行微信 | `C:\Program Files\Tencent\Weixin\Weixin.exe`，文件版本 **4.1.15.13** |
| 原生窗口 / UIA | `Qt51514QWindowIcon` / `FrameworkId=Win32`；子窗含 `MMUIRenderSubWindow` |
| pywechat 固定 commit | `8589baa049bb91d3a500602c167f07b2f8397a13`，2026-09-26 |
| 安装发行包 / 导入模块 | **pywechat127 1.9.8** / **pyweixin** |
| 相关近期修改 | `ebe8d4be9347816d221c38843e5b9e5e480bd8fb`，2026-09-10，修改 pull_messages 稳定性 |
| wechat-notify 参考 commit | `2605806b01b9c226030b4c0a7e4ad6254931c6e1`，2026-05-14 |
| Python | 项目 Python 3.12.14 创建的独立 venv |
| 安装方法 | 审阅源码后，对固定 commit 的 `src` 执行 editable 安装；没有安装其 Skill/MCP 服务 |
| 是否连接微信 | **否**；Win32 能发现正在运行的窗口，但 `Tools.get_weixin_hwnd()` 为 0 |

独立环境位于 `artifacts/pyweixin-spike/venv`。主要依赖为 pywinauto 0.6.9、comtypes 1.4.17、pywin32 312、PyAutoGUI 0.9.54、Pillow 13.0.0、psutil 7.2.2；完整清单见 `artifacts/pyweixin-spike/installed-versions.txt`。另安装与主项目一致的 pydantic 2.13.5、PyYAML 6.0.3、tzdata 2026.5，以复用静默策略。隔离环境 `pip check` 通过；本阶段没有替换主项目已有依赖或修改生产 requirements。

## API matrix：真实调用

第一组保持原有辅助功能设置，第二组临时启用屏幕阅读器标志和当前用户 Narrator RunningState。每个 API 各调用两次；另在 Qt 唤醒引用保持期间，分别在前台、后台、最小化状态调用一次 pull_messages，共 **13 次实际读取 API 调用**。

| API | 原环境 | 临时辅助功能环境 | 总结与失败位置 |
|---|---|---|---|
| `Messages.pull_messages()` | FAIL | FAIL | 请求目标群最近 20 条；`Navigator.open_weixin` 抛 `NotFoundError` |
| `Messages.check_new_messages()` | FAIL | FAIL | 同上，未进入会话列表读取 |
| `Monitor.listen_on_newMessages()` | FAIL | FAIL | 设置 2 秒有界监听；打开主窗口时失败，没有进入监听循环 |
| `Monitor.listen_on_chat()` | FAIL | FAIL | 直接传入已验证的主窗口句柄，仍找不到聊天 List，`ElementNotFoundError` |
| `Messages.save_media()` | FAIL | FAIL | 请求最多 1 项、仅写临时测试目录；打开历史记录前即失败，下载/保存文件 **0** |

这些失败均发生在输入拦截器触发之前，`guards=[]`。因此不是“安全策略拦住了一个本可工作的方法”。直接传入原生窗口的 listen_on_chat 测试也失败，说明问题超出了顶层窗口选择器。

免打扰、手机已读、PC 无未读数字的**运行行为均为 NOT_TESTED**：读取前置条件失败，未制造新消息或修改免打扰设置。不能把空结果当作这些场景已通过。

## Qt / 辅助功能交叉诊断

按 [wechat-notify 的实现](https://github.com/CueBeiXing/wechat-notify/blob/2605806b01b9c226030b4c0a7e4ad6254931c6e1/src/core/wechat_monitor.py) 研究并测试：

- `SPI_GETSCREENREADER / SPI_SETSCREENREADER`：false → true → false；仅广播，不写入用户配置持久值。
- 已存在的 HKCU `SOFTWARE\Microsoft\Narrator\NoRoam\RunningState`：0 → 1 → 0；未创建新键，也未启动 Narrator 程序。
- 保持 `uiautomation.ControlFromHandle()` 引用，复现其 Qt wake 路径。
- 查询 AutomationId=`session_list`、ClassName=`mmui::ChatSessionCell` / `mmui::SessionCell`；三个实际窗口状态下均为 **0 个**。
- pywinauto/UIA 结构仍只有一个 Window 和两个 Pane；先前标准 MSAA 交叉诊断也没有暴露消息对象。

| 请求状态 | 实际状态核验 | pull_messages | 会话控件 |
|---|---|---|---|
| 前台 | 已确认 foreground=true、minimized=false | FAIL | 0 |
| 后台未最小化 | 已确认 foreground=false、minimized=false | FAIL | 0 |
| 最小化 | 已确认 minimized=true | FAIL | 0 |

首次普通 SetForegroundWindow 被系统拒绝。后续仅对已验证的顶层 Window 调用 UIA SetFocus，实际进入前台后重测；没有对 Edit 设置焦点或输入。最初两次矩阵诊断因此提前结束，没有读取 API 结果；有效矩阵单独保存在 `qt-runtime-matrix-02`。

这组早期矩阵测试时 HKLM ForceUIAForQt 尚不存在，也未重启微信。后续完整 pass 已补齐两次微信重启、人工登录、实际 Narrator 运行，以及获明确授权的 64 位 DWORD=1 临时实验；HKLM=1 时两个 API 仍在窗口识别失败。登录页能暴露 28 个后代和 22 个 mmui 节点，登录后聊天树却仍为 2 个外壳后代、0 个 mmui。17:59 独立核验 SPI=false、HKCU RunningState=0、两个视图键/值均恢复不存在，Narrator 及管理员辅助进程已退出。未重启 Windows。

另只读核对诊断 Python 与微信进程的位数和令牌：均为原生 x64、未提权、Medium Integrity（RID 8192），未发现“微信以管理员运行、读取进程权限较低”或 32/64 位不一致的情况。记录在 `process-security.json`，没有通过提权规避测试结果。

上游自己的 [微信 4.1+ UI 说明](https://github.com/Hello-Mr-Crab/pywechat/blob/8589baa049bb91d3a500602c167f07b2f8397a13/Weixin4.0.md) 也提示版本支持之外还存在 UI 树可见性的前提。结合本机实测，可以确认当前没有可供这两个项目读取的控件；**不能据此证明具体是账号策略、客户端实现还是其他环境因素导致**。

## Data quality

| 字段 | 本次实际取得 |
|---|---|
| 群名 | 未由 pyweixin 读出；传入配置值不算返回数据 |
| sender | 未取得 |
| content | 未取得，返回消息数 0 |
| type | 未取得 |
| send time | 未取得 |
| order | NOT_TESTED，没有消息列表可核对 |
| image/file reference | 未取得，临时媒体目录内文件数 0 |

额外源码审查发现：该 commit 的 [pull_messages](https://github.com/Hello-Mr-Crab/pywechat/blob/8589baa049bb91d3a500602c167f07b2f8397a13/src/pyweixin/WeChatAuto.py#L3915) 最终字典只有“消息发送人、消息内容、消息类型”，没有发送时间。[OpenClaw API Reference](https://github.com/Hello-Mr-Crab/pywechat/blob/8589baa049bb91d3a500602c167f07b2f8397a13/Skill/OpenClaw/pyweixin-rpa/references/api_reference.md#L182) 对字段的描述与此处实现不一致；相关 Skill 中“不滚动”的文字也不能代替 traverse_message 的实际行为审查。即使以后恢复 UIA，也必须另行验证时间来源；不能拿 captured_at 代替 send time。

## Runtime behavior：实测与源码分开

- **是否必须前台 / 是否抢焦点：**本机三种状态都没有读成功，无法认证可后台运行；本次诊断按授权主动切换窗口。源码中的 pull_messages 会打开聊天并遍历记录，不是天然被动读取。
- **是否滚动：**源码读取历史涉及滚动、多选及键盘导航；本阶段失败在这些步骤之前，实际聊天滚动 0。安全包装必须逐项重审这些动作，不能把方法名“读取”当作无副作用保证。
- **独立聊天窗口：**listen_on_chat 文档和实现允许主聊天窗口，也允许独立窗口。本次实际传主窗口失败；没有额外打开独立窗口，稳定性/重启后选择器仍未验证。
- **多群扩展：**没有通过读取，不能给出可靠容量或 CPU 指标。源码长期监听循环没有显式休眠；listen_on_newMessages 还会打开文件传输助手、读取全局未读状态。其默认监听模式可能调整音量/阻止息屏，本次已拦截这些系统副作用。
- **未读不能替代 cursor：**[check_new_messages 使用的扫描器](https://github.com/Hello-Mr-Crab/pywechat/blob/8589baa049bb91d3a500602c167f07b2f8397a13/src/pyweixin/utils.py#L384) 依赖未读并排除免打扰；listen_on_newMessages 也有对应过滤。因此即使连接成功，这两个 API 也不能承担“手机已读/免打扰仍补齐”的完整变化检测责任。
- **图片：**save_media 图片分支使用查看器区域截图，不应默认标为客户端原始图片文件。本次未走到该步骤；图片质量、原图下载和文件引用都没有运行验证。

## Safety

| 项目 | 本阶段实际值 |
|---|---|
| 微信发送 | **messages_sent = 0** |
| 回复、转发、@、修改联系人/群设置 | **0** |
| 聊天输入、粘贴、键盘输入、草稿修改 | **0** |
| 真实聊天内容 / 媒体取得 | **0 / 0** |
| 截图 / OCR / DeepSeek / Vision | **0 / 0 / 0 / 0**；本阶段没有继续自研图像路线 |
| HKLM 写入 | 早期矩阵为 0；最后 pass 经用户及 UAC 授权临时写入 **1 项 64 位 DWORD**，已恢复原缺失状态，32 位未写 |
| 临时辅助功能设置 | **已恢复并再次核对** |
| 静默期真实微信操作 | **0**；全部真实测试在 06:00–24:00 内，调用前复用 SchedulerPolicy |
| 正式库升级 / 正式事项写入 | **0 / 0**；仍为 v9，9 Event / 8 Deadline / 21 Task / 80 Evidence |
| 生产启用 / 登录任务注册 | **未执行 / 未执行** |

测试进程只暴露五个审阅过的读取入口，对键盘、Edit 写入、按钮操作及鼠标输入设拦截；其 API 请求读取 allowlist 外聊天时会被丢弃。stdout/stderr 仅留在内存，落盘报告只有结构、数量、字段名、哈希与异常类型，不记录私人消息。该脚本是隔离诊断器，不是生产 ReadOnlyAdapter，也不会被桌面服务导入。

## 离线测试

最新微信专项 **104 passed / 0 failed / 0 skipped**，有一条既有 Starlette/httpx 弃用警告，结果为 `artifacts/qt-accessibility-combined-tests.xml`。新增测试覆盖输入拦截、输出脱敏、夜间不导入上游、异常时设置恢复，以及受控实验租约/回滚进程/实际注册表值失效时停止测量。早期 97 项记录仍保留在 `artifacts/wechat-pyweixin-final-tests.xml`。

最初沙箱内重跑阻塞于 Windows asyncio 初始化的 socketpair 回环连接，另一次有界重跑在 45 秒后超时；这些未记为通过。堆栈定位后，在允许本机回环的环境按原命令执行全部专项，正常通过；没有为通过测试修改产品逻辑、关闭业务校验或运行真实 Adapter/模型。

## 文件、证据与后续边界

新增诊断：`scripts/pyweixin_readonly_spike.py`、`scripts/pyweixin_environment_spike.py`；新增测试：`test_pyweixin_spike.py`、`test_pyweixin_environment.py`。其他既有 Collector 模块、UIA/CV 调研文件全部保留。完整安装清单、固定源码和脱敏运行记录在被忽略的 `artifacts/pyweixin-spike/` 内。

关键记录：

- `env-before.json` / `env-after.json`：版本与辅助功能前后状态。
- `baseline/*/result.json`：原环境五个 API。
- `temporary-accessibility/environment-report.json`：临时设置、五个 API 及恢复验证。
- `qt-runtime-matrix-02/environment-report.json`：实际前台、后台、最小化及 Qt 控件查询。
- `production-state-after.json`：正式库只读计数及采集仍关闭。
- `compatibility-report.json`：五 API、13 次调用、窗口状态和安全结果的结构化汇总。
- `installed-versions.txt` / `install-report.json`：安装与依赖记录。

既有 `artifacts/wechat-spike/hybrid-history-02/history-report.json` 保存了先前前台本地 OCR/CV 的有界验证：8 次捕获、8 次本地 OCR、6 次小步滚动，重叠区间和往返锚点成立，能力限于 **IDLE_ONLY 的局部 spike**；发送者、时间、媒体、新消息到达和生产读取器仍未验收。`hybrid-history-01` 的报告导出曾失败，不能把其计数为零的失败摘要当作真实操作账本。

最后的完整 accessibility pass 已完成，最终建议为 **FALLBACK_TO_CUSTOM**：本机当前版本的聊天 Qt 子树在所授权初始化、HKLM=1 与完整微信重启后仍未暴露，无法把 pyweixin 或相同控件路线认证为可用读取器。三种未读/免打扰场景、媒体和消息顺序仍未通过运行验收。生产采集关闭，不默认继续大面积 OCR 开发；Astra 总计划、固定上游源码、既有安全模块及局部视觉调研全部保留。未来若客户端或另行测试的 Windows 重启使聊天树恢复，优先重新验证 pyweixin。

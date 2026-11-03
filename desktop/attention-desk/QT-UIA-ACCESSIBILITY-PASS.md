# Qt UIA accessibility pass：验证记录

**最新状态（2026-10-22 18:55）：已完成 ForceUIAForQt=1 跨 Windows 完整重启实验，当前本机最终建议 FALLBACK_TO_CUSTOM。** 18:47 重启后，新主窗口 PID=30928、HWND=68308 的归属与目标群标题已独立确认；聊天 mmui 仍为 0，两个 API 均实际调用失败。实验设置已恢复。完整新报告见 [WECHAT-POST-REBOOT-RESULT.md](WECHAT-POST-REBOOT-RESULT.md)，不再沿用旧阶段“Windows 未重启”的限制。

以下是截至 18:06、尚未重启 Windows 的上一阶段完成记录，保留原始结论及其适用范围。

2026-10-22，Asia/Shanghai。**结论：FALLBACK_TO_CUSTOM，限定于本机微信 4.1.15.13 和本报告实际验证的条件。** 已完成当前会话初始化、真实讲述人验证，以及用户和 Windows UAC 授权的 HKLM 64 位 `ForceUIAForQt=1` 临时实验。保持 SPI / HKCU 标志及 HKLM 值生效，完整重启微信、等待用户手动登录后，聊天 Qt 子树仍未暴露；两个读取 API 均在窗口识别阶段失败。设置已回滚并独立核验，生产采集继续关闭。

该结论来自完整的本次授权实验，不是把先前零节点的结果直接外推。**没有重启 Windows，因此没有证明重启 Windows 后也必然无效。** 不建议在当前环境采用 pyweixin 作为可用生产适配器，也不把这份诊断报告当作大面积 OCR Collector 的开发验收。

## 已执行的当前用户 / 当前会话修复

1. 实测运行中的微信，版本仍为 **4.1.15.13**。
2. 临时设置 SPI_SETSCREENREADER=true，并把已存在的 HKCU Narrator RunningState 从 0 改为 1。
3. 参照 [LAVARONG 的实现](https://github.com/LAVARONG/wechat-automation-api/blob/a9f426e3323f02075f5550e3c61c7d62b30c5d75/scripts/wechat_controller.py)，在导入 UIA/COM 前 OpenDesktop / SetThreadDesktop，重新绑定 WinSta0/Default。原诊断线程本来就在 Default；不能把重新绑定本身宣称为已证明的修复原因。
4. 每个阶段在新的诊断进程内初始化 MTA / UIAutomation，通过 ControlFromHandle 获取新的根，并保持引用完成查询。只读取 ClassName、ControlType、FrameworkId 和指定 AutomationId 的计数，没有读取 Name / Value / Text。
5. 保持 SPI=true、HKCU RunningState=1，**完全退出并重新启动微信**，分别在启动后约 3 秒和 18 秒观察 UIA 树。

重启使用 Windows [Restart Manager](https://learn.microsoft.com/en-us/windows/win32/api/restartmanager/nf-restartmanager-rmshutdown) 的正常关闭流程，仅注册已核验的微信主进程。没有使用强制终止、注入或任意 WM_COMMAND。RmShutdown 返回 351，随后独立核验旧主进程 **1620 已退出**，原先五个微信 PID 均已消失；RmRestart 返回 0 但没有重启实例，遂启动已核验的原安装文件。新主进程 **84844**，新窗口 **2688020**。没有把错误码当成退出成功证据，退出结论来自进程消失与新进程出现。

没有自动登录：重启后由用户手动登录并明确确认打开「26车辆2班（公告群）」。随后重新启用会话标志、使用新 UIA 客户端检查聊天主窗口，仍为 Win32 外壳。另实际启动 Windows 讲述人再次验证，而不只是修改其 RunningState。

## UIA Tree：真实计数

“Qt 业务控件为零”和“UIA 根下面没有任何节点”是不同口径。本轮基线还存在 Win32 外壳，具体如下：

| 阶段 | root ClassName / FrameworkId | 直接子节点 | 全部后代节点 | mmui 节点（含 root） |
|---|---|---:|---:|---:|
| 本轮可见窗口基线 | Qt51514QWindowIcon / Win32 | 1 | 1 | 0 |
| 当前会话标志 + Default 桌面 + 新 UIA 客户端 | Qt51514QWindowIcon / Win32 | 1 | 1 | 0 |
| 保持标志并完整重启后，早期 | **mmui::LoginWindow / Qt** | **2** | **28** | **22** |
| 重启后稳定等待，再次观察 | **mmui::LoginWindow / Qt** | **2** | **28** | **22** |
| 用户登录后，尚未重新设置会话标志 | Qt51514QWindowIcon / Win32 | 2 | 2 | 0 |
| 用户登录后，会话标志开启 + 新 UIA 客户端 | Qt51514QWindowIcon / Win32 | 2 | 2 | 0 |
| 用户登录后，会话标志开启 + 真实 Narrator 运行 | Qt51514QWindowIcon / Win32 | 2 | 2 | 0 |
| HKLM 64 位 DWORD=1 + 会话标志，重启前聊天窗口 | Qt51514QWindowIcon / Win32 | 2 | 2 | 0 |
| HKLM DWORD=1 + 会话标志，第二次完整重启后的登录页 | **mmui::LoginWindow / Qt** | **2** | **28** | **22** |
| 保持上述全部标志，用户登录后的聊天主窗口 | Qt51514QWindowIcon / Win32 | 2 | 2 | 0 |
| 完成回滚后，被动复核聊天主窗口 | Qt51514QWindowIcon / Win32 | 2 | 2 | 0 |

**已经证明登录界面的 Qt UIA 子树可在本机暴露；登录后的聊天主窗口仍不可读。** 登录界面的变化不能等同于消息读取恢复，也不能仅凭这些结果确定账号策略或具体客户端内部原因。同时改变了启动时标志与客户端生命周期，没有单独证明是哪一项产生差异。

以上命名控件已经在**登录后的实际聊天主窗口**逐项检查：`mmui::MainWindow`、`session_list`、`mmui::ChatSessionCell`、`mmui::SessionCell`、`mmui::ChatListView`、`mmui::ChatMasterView`、`mmui::ChatInputField`、`chat_input_field` 各项计数全部为 0。第一阶段为主进程 84844、句柄 986164；HKLM 实验重启后为新主进程 **72424**、聊天主窗口 **10291520**，登录页句柄为 789446。没有沿用已销毁窗口的结果。

## Environment

| 项目 | 实验期间 | 结束后再次核对 |
|---|---|---|
| SPI screen-reader 标志 | false → true | **已恢复 false** |
| HKCU Narrator RunningState | 0 → 1 | **已恢复 0** |
| Narrator 程序 | 已实际短暂启动，PID 69944；运行期间也未暴露聊天树 | **已退出，最终进程枚举为空** |
| Default 桌面绑定 | 成功；原来也是 Default | 没有切换用户可见桌面 |
| 新 UIAutomation / ControlFromHandle | 已执行 | 每次使用新诊断进程 |
| 微信客户端完整重启 | **共执行两轮**，每轮旧 PID 全部消失 | 每轮由用户手动登录并打开测试群 |
| HKLM ForceUIAForQt，64 位视图 | 原键/值不存在 → **REG_DWORD 1** | **恢复键/值不存在** |
| HKLM ForceUIAForQt，32 位视图 | 键/值不存在，未写入 | 保持不存在 |
| Windows 重启 | 未执行，未获该项授权 | 其影响不在实测结论范围 |

SPI 标志与 Narrator 程序不是同一个状态；[Microsoft 文档](https://learn.microsoft.com/en-us/windows/win32/winauto/screen-reader-parameter) 特别说明 Narrator 本身并不设置这组 SPI 标志。本次分别记录了 SPI、注册表值和程序运行情况。

讲述人清理记录：普通进程关闭以及 WM_CLOSE 被 Windows 权限边界拒绝，未尝试管理员提权。随后通过 [Windows 讲述人设置页](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings)，核验唯一的 `SystemSettings_Accessibility_Narrator_IsEnabled_ToggleSwitch`（名称“讲述人”、类型 ToggleSwitch），仅将运行开关从 1 切回 0。未修改自启动、语音等其他开关。等待异步退出后再次枚举确认无 Narrator 进程，SPI=false、HKCU RunningState=0，恢复完成。没有把最初失败的关闭请求记为清理成功。

## ForceUIAForQt：已授权执行，已恢复

目标为 HKLM 64 位视图的 `SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Accessibility\ForceUIAForQt`，类型 REG_DWORD、值为 1。备份确认原值及 Accessibility 子键均不存在；以下评估在修改前已报告并取得用户明确授权。

- **为什么可能有用：**[wechat-notify](https://github.com/CueBeiXing/wechat-notify/blob/2605806b01b9c226030b4c0a7e4ad6254931c6e1/src/core/wechat_monitor.py) 将它列为 Qt UIA 的环境兼容性措施。它是需要另行验证的假设，不是修复 pyweixin 业务 API 的补丁。
- **证据边界：**没有查到 Microsoft / Qt 对这个键效果的正式保证。Qt 5.15 的[公开 UIA 初始化路径](https://github.com/qt/qtbase/blob/5.15/src/plugins/platforms/windows/uiautomation/qwindowsuiaaccessibility.cpp) 在处理 WM_GETOBJECT 时激活 provider；这条代码路径没有引用该键。本机已经在未设置它的条件下暴露登录页，因此不能断言该键是所有 Qt UIA 的必要条件。
- **权限：**在 HKLM 下创建子键和值需要管理员授权。用户确认 UAC 后，仅注册表辅助进程提权；微信重启与读取仍以普通用户权限执行。
- **重启：**如获授权测试，至少应重新启动微信再比较。参考项目还建议重启 Windows；本机未验证是否必需，不能保证只重启微信足够。不会自行重启电脑。
- **风险：**它属于机器级设置，可能改变其他用户/Qt 应用的辅助功能行为；本机效果与兼容性未知。不能承诺不会带来额外开销，也不能保证解决聊天控件不暴露。
- **回滚：**先记录键/值是否存在、类型和原值。当前原状态缺失，回滚应删除本次创建的值；仅当新建的 Accessibility 子键仍为空且确由本次创建，才删除该空子键。不得删除 Explorer 或其他已有设置。原值存在时应恢复其原类型和数值。回滚后再重启受影响客户端验证。

**实际执行：**2026-10-22 17:53:04，管理员辅助进程 41360 备份原缺失状态，只创建这一项 64 位 DWORD=1，32 位视图未写。17:57 普通用户进程保持 SPI=true、HKCU RunningState=1，并完整退出旧微信 84844，所有旧微信 PID 均消失，正常启动新实例 72424。Restart Manager 仍返回 shutdown=351、restart=0，退出证据来自实际进程状态。登录页暴露 Qt 树；用户随后手动登录并打开指定群。17:58 的聊天树及两个 API 测量前后均独立读取 HKLM，确认 DWORD=1 仍生效，回滚辅助进程仍存活。

用户已选择“允许这项受控测试”。新增 `scripts/wechat_force_uia_experiment.ps1`，只处理这一个 64 位注册表 DWORD：写入前保存缺失状态，最长 20 分钟，收到结束信号或到期时恢复；若值被外部修改则保留并报告冲突。仅删除本次新建且仍为空的子键，绝不递归删除。管理员辅助进程不启动或操作微信。`scripts/wechat_force_uia_pass.py` 在普通用户权限下保持临时会话标志，重启微信、等待人工登录、复测 UIA 与两个读取 API，然后请求回滚。每次测量前重新核验实际注册表值、剩余时限及回滚进程存活，不能只凭状态文件宣称设置仍在生效。

**实际恢复：**17:58:44 收到结束信号后恢复原缺失状态；17:59:55 普通用户诊断独立复核，64 位和 32 位键/值均不存在、SPI=false、HKCU RunningState=0、真实 Narrator 进程为空、管理员辅助进程已退出。回滚后另做被动 UIA 查询，聊天主窗口仍为 2 个外壳后代、0 个 mmui 节点。没有重启 Windows。

## pyweixin retest

| API | 本轮结果 | 原因 |
|---|---|---|
| Messages.pull_messages | **FAIL，HKLM 实验期间再次实际调用** | 用户登录后，SPI / HKCU / HKLM 标志均开启；`Tools.get_weixin_hwnd()` 仍返回 0，`NotFoundError`；输入拦截器未触发 |
| Messages.check_new_messages | **FAIL，HKLM 实验期间再次实际调用** | 相同窗口识别失败，未进入会话列表或消息读取；输入拦截器未触发 |

这两项失败仍来自同一个未暴露聊天树的问题，不能分别归因为各 API 的消息解析逻辑不兼容。没有取得群名、发送人、内容、类型、发送时间或媒体；顺序没有可核验数据。本轮重测是新客户端与用户手动登录后的真实结果，没有复用上一轮记录。

**最终三选一推荐：FALLBACK_TO_CUSTOM。** 本机这版微信在本次完整 accessibility 初始化与客户端重启后仍未暴露聊天 Qt 树，当前不能采用 pyweixin 或另外两个基于相同 Qt 控件的参考实现作为可用读取器。失败共同位于底层可访问性，未证明各个 API 的消息解析代码分别不兼容。保留上游固定版本和自研调研；生产采集保持关闭。未来如新客户端或另行授权的 Windows 重启使聊天树出现，重新开启 pyweixin 评估，优先于扩展自研 OCR。

## 安全、测试与证据

本轮 **messages_sent=0**；没有聊天输入、回复、转发、草稿修改、自动登录或群设置修改。没有截图/OCR、DeepSeek/Vision、正式数据库写入或 Windows 重启。HKLM 仅有明确授权的这一项临时写入，已经恢复。讲述人已退出，辅助功能设置已恢复；生产采集继续关闭。

新增诊断脚本：`scripts/wechat_qt_accessibility_probe.py`、`scripts/wechat_qt_accessibility_pass.py`、`scripts/wechat_force_uia_experiment.ps1`、`scripts/wechat_force_uia_pass.py`。等待人工登录时不反复改变登录控件焦点。`server/tests_wechat/test_qt_accessibility_pass.py` 的 7 项测试覆盖夜间不调用桌面/UIA、结构探测不读取 Name/Value、Restart Manager 拒绝无关 PID，以及租约到期、回滚进程退出、实际值消失或状态已回滚时拒绝继续测量。全部微信离线回归最新 **104 passed**，记录 `artifacts/qt-accessibility-combined-tests.xml`；不把离线通过当作两个 API 的实机通过。

真实证据目录：`artifacts/pyweixin-spike/qt-accessibility-pass/`：

- `before-visible.json`、`after-session-init.json`：修复前与当前会话初始化后。
- `restart-active-flags/before-restart.json`：重启前标志生效时基线。
- `restart-active-flags/restart-result.json`：正常关闭、旧进程退出、新进程启动。
- `restart-active-flags/after-restart-early.json`、`after-restart-settled.json`：新 Qt 登录树。
- `restart-active-flags/environment-report.json`：临时设置与恢复。
- `current-login-state.json`：等待用户期间的被动登录页核验，没有切换焦点。
- `current-environment.json`：SPI/Narrator、HKLM 两种视图的最新只读状态。
- `after-user-login.json`、`after-user-login-active-flags.json`：用户登录后的真实主窗口结构及命名控件计数。
- `while-narrator-running.json`、`narrator-environment.json`：真实 Narrator 初始化与聊天树结果。
- `narrator-cleanup.json`：Windows 设置关闭运行开关，以及随后确认的空进程列表。
- `retest-pull_messages/result.json`、`retest-check_new_messages/result.json`：两个 API 的实际复测。
- `retest-environment.json`、`after-all-init-environment.json`：最后的设置恢复、Narrator 退出和 HKLM 缺失核对，2026-10-22 13:59 Asia/Shanghai。

第二阶段完整证据位于 `force-uia-controlled-test/`：`registry-before.json`（原状态）、`status.json`（应用及回滚）、`with-hklm-before-restart.json`、`with-hklm-after-restart.json`、`with-hklm-after-manual-login.json`（三个阶段结构）、两个 API 子目录的 `result.json`、`diagnostic-report.json`（标志持续生效、重启、API 与恢复）、`independent-after-rollback.json` 和 `after-rollback-tree.json`（独立恢复核验）。

**本次 Qt UIA pass 已完成；真实生产 Collector、Windows 重启后的行为和 7 天运行验收并未完成。** Astra 总体 Implementation Plan 及先前代码全部保留。本轮不继续扩展大面积 OCR 解析。

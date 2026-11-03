# 微信跨 Windows 重启验证：已完成，历史检查点保留

**2026-10-22 18:55 更新：本轮已完成。** 已验证 18:39 写入的 DWORD=1 跨过 18:47 Windows 完整重启，确认新 PID/HWND 对应真实目标群；聊天 mmui 仍为 0，两个 API 实际调用均失败。实验设置已恢复并独立核验。最终建议及全部证据见 [WECHAT-POST-REBOOT-RESULT.md](WECHAT-POST-REBOOT-RESULT.md)。不再需要为本轮重复重启。

以下保留 18:41 等待重启时的历史检查点，不能将其中的待执行步骤当作当前未完成事项。

更新：2026-10-22 18:41，Asia/Shanghai。**本轮暂不作 USE_PYWEIXIN / HYBRID / FALLBACK_TO_CUSTOM 最终选择。** 用户已授权让唯一的 HKLM 64 位 `ForceUIAForQt=1` 保留至下一次手动 Windows 重启及实测完成，再恢复原状态。没有授权或执行自动 Windows 重启。

## 已确认的事实

- Windows 当前启动时间为 **18:21:06.5**，微信主进程 **32128** 于 18:22:18 启动，版本 **4.1.15.13**。
- 18:31 首次读回发现，ForceUIAForQt 的键和值均不存在。上一轮 20 分钟临时实验已经按约定回滚，因此 18:21 的重启没有验证“该值保持 1 跨重启”的效果。
- 枚举全部 5 个当前微信进程的所有顶层窗口和原生子窗口，只有一个可见主窗口；其他为隐藏的托盘、电源、输入法等窗口。
- 主窗口 **HWND 132398**、Win32 PID **32128**、UIA ProcessId **32128**、UIA NativeWindowHandle **132398** 一致。原生和 UIA ClassName 均为 `Qt51514QWindowIcon`，FrameworkId 为 `Win32`。
- 渲染子窗 **132394** 与 1×1 辅助子窗 **459838** 均归属于同一主窗口和 PID；直接读取这两个 HWND 的 UIA 也没有聊天 Qt 子树。
- 标题区域截图肉眼核对显示 **26车辆2班（公告群）(37)**，确认是登录后的目标群，未扫描消息正文。标题带底部较窄，本地 OCR 返回 `ocr_empty`；不把该失败写成 OCR 匹配通过，也不把截图文字冒充 pyweixin 结果。
- 本次所有 HWND 均来自重启后的新枚举，没有复用上一轮的 986164 / 10291520 / 登录页句柄。

## 新授权设置与当前复测

用户明确选择“允许保留至手动重启后复测”，扩展了先前只保持 20 分钟的授权。

18:39:08，Windows UAC 授权后，仅设置 HKLM **64 位**：

```text
SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Accessibility
ForceUIAForQt = REG_DWORD 1
```

32 位视图未写入。原状态和写入日志位于 `artifacts/pyweixin-spike/force-uia-reboot-experiment/registry-before.json`、`status.json`。18:41:28 普通用户独立读回仍为 DWORD 1；该值将保留，不按旧实验的 20 分钟时限回滚。

18:41，在该值为 1、临时 SPI=true / HKCU RunningState=1、新 UIA 客户端条件下，再次得到：

| 项目 | 结果 |
|---|---:|
| 直接子节点 / 全部后代 | 2 / 2 |
| mmui 节点 | 0 |
| session_list / chat_input_field | 0 / 0 |
| MainWindow / ChatSessionCell / SessionCell | 均 0 |
| ChatListView / ChatMasterView / ChatInputField | 均 0 |

`pull_messages` 和 `check_new_messages` 均再次实际调用，`connected=false`、`NotFoundError`、`guards=[]`。没有取得消息。结构化结果为 `messages=[]`、`message_read_status=unavailable_due_to_ui_accessibility_not_an_empty_chat`，sender/content/type/time/order 均 `not_observed`；不能解释为群中没有消息。

临时 SPI 与 HKCU 状态已恢复为 false / 0。**这次写入发生在 18:21 启动之后，尚不能用它替代“值为 1 跨 Windows 重启”的验证。**

## 下一轮必须按此顺序继续

1. 等用户手动完整重启 Windows、登录微信并打开目标群。不得自动重启或自动登录。
2. 只读检查当前 boot time 必须晚于日志 applied_at（18:39:08），64 位实际值仍为 DWORD 1；读取 HKLM 不依赖旧进程或状态文件。若前置条件不满足，不下最终兼容性结论。
3. 重新枚举所有微信 PID 与所有顶层窗口，校验启动时间、路径、版本、Win32/UIA PID 与 HWND。不得复用本页的 32128/132398。
4. 在当前登录态真实主窗口，用新的 COM/UIA 客户端检查全部指定节点；必要时核对标题以排除登录页/错误窗口，不扩展为 OCR 消息 Collector。
5. 若树暴露，立即实际读取目标群最近 10–20 条；明确输出 sender/content/type/time/order。只允许已审阅的读取与导航，聊天输入/发送能力始终拒绝。现有隔离诊断器默认阻止全部输入；如安全读取导航被它阻止，标为 guard 限制而不是 API 不兼容，按源代码审查逐步收窄到安全导航。
6. 树仍为零时，再交叉核验主窗口、渲染子窗和多进程，确认后才给当前环境的三选一结论。
7. 实测完成后使用已授权的同一脚本 `scripts/wechat_force_uia_reboot_experiment.ps1 -Action Restore`（需要 Windows 管理员确认）恢复原缺失状态，并独立核验两种注册表视图。仅删除本次值和本次创建且仍为空的键，保留任何外部新增设置。

只读预检：`pwsh -NoProfile -File scripts/wechat_force_uia_reboot_experiment.ps1 -Action Inspect`。脚本没有关机、重启、计划任务、微信输入或发送操作。

## 证据和边界

- `artifacts/pyweixin-spike/post-reboot-20261022-1831/initial-tree.json`：重启后新窗口首次结构。
- 同目录 `all-native-windows.json`：全部当前微信进程的原生窗口枚举；原枚举为 DPI 虚拟化坐标，不用来推断物理截图位置。
- `correlated-identity.json`：Win32/UIA HWND/PID 对应、渲染子窗、标题截图及 OCR 实际结果；截图几何使用 DPI-aware 坐标。
- `after-reapply-before-next-windows-reboot.json`：临时标志开启时的新客户端结构。
- `current-pull_messages/result.json`、`current-check_new_messages/result.json`：两个实际 API 结果。
- `current-state-with-reapplied-setting.json`：实际注册表前后读回、调用、派生结果与 SPI/HKCU 恢复。
- `artifacts/wechat-spike/post-reboot-20261022-header/`：仅标题带图片，不含消息正文。

`messages_sent=0`，草稿/群设置修改为 0，模型调用为 0，正式数据未写入。生产采集继续关闭。旧 Astra 总计划、所有已有源码和此前实验报告均保留；上一轮未重启 Windows 的结论不能直接替代本轮待完成实验。

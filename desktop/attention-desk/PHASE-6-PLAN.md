# Phase 6 实施与验收合同

基线：f2b370f497b8e17d8c2d6a4641e3b7280e32dbf2 / phase5.5-long-document-stable，提交前完整 Python 576、frontend 16、build 通过，Git 干净。

## 必须交付

- Today / Upcoming：同一已确认结构化数据库的查询视图；今天/逾期/当日提醒及关联未完成任务；未来 7/14/30 天；默认排除终态，说明每项入选原因。
- 日期：保留 date、minute（datetime）、range、unknown；不制造 23:59。已确认且不含未解决不确定性的有效日期才产生主动提醒和日历投影。旧显式时区保留，新日期默认系统 IANA 时区；空时区按系统解释。
- Reminder：独立持久模型与审计；日期型 3/1/0 天前 09:00，精确型 24/2 小时前；事件/任务/日期变更重新投影；保留历史、稳定去重、不重复补弹；6 小时 grace；可取消、关闭、恢复、snooze 10 分钟/1 小时/明天、dismiss。
- Scheduler：独立服务、轻量周期、干净关闭、原子领取与防重复；离线运行；通知失败有限重试、提醒中心明确兜底；日志只含 ID、时间、尝试与固定结果。
- Windows 通知：Python WinRT adapter，文本作为数据并正确 XML 转义；默认标题+Deadline，完整/隐藏模式；设置中测试按钮；不依赖管理员或防火墙。是否视觉显示由 Windows 决定，不能冒称人工确认。
- 设置：总开关、简单策略、隐私、grace、测试通知；Startup 只能用户明确开启后创建当前用户快捷方式。Web app 不重写 desktop shell/tray。
- Calendar：Month + 选中日列表，复用查询；只投影已确认日期，date 全天、datetime 精确时刻、range 保留范围；多个 Deadline 独立显示；状态不只靠颜色。
- ICS：单 Deadline、单 Event、未来全部已确认日期；稳定 UID、DTSTAMP、DTSTART、LAST-MODIFIED、角色/任务/来源简述与安全公开 URL；date 使用 VALUE=DATE，时间使用 aware UTC；标准库序列化/解析、UTF-8 折行、转义、无绝对路径和秘密；无 OAuth/订阅/双向同步。
- 来源更新：新快照提示，旧人工事实不被 AI 覆盖；明确确认才更新原 Deadline（稳定 ID），同时更新投影；新旧来源 Evidence 可追溯同一 RawItem。
- Migration：使用既有编号迁移，追加提醒表并安全拓展日期精度；必要表重建在原子事务中逐行保留、FK 检查后提交；真实库先备份与副本升级验证，不删用户数据。
- 真实验收：仓库外隔离数据与临时端口，“Phase 6 测试事项”、两个 Task、几分钟后的 Deadline；真实 Scheduler/WinRT 通知、Calendar、ICS 解析与重启；真实用户事实不增加测试事项。
- 回归：用户列出的日期 13、Reminder 16、Calendar 10、ICS 11 类，以及完整 Python/frontend/build、Collector/security、Documents、Long PDF、Official、DeepSeek；浏览器状态/键盘/窄窗口/断网；提交前真实秘密/文件/OCR/数据库/ICS/日志/cache 审计。
- 最终：仅严重数据/安全/重复提醒问题阻止稳定标签；满足门槛后 commit `feat: complete phase 6 reminders and calendar` + tag `phase6-reminders-calendar-stable`，clean Git，不 push；报告 A–N 并停止。

## 模块和 UI 所有者

不把业务堆入 main.py/App.jsx。server/planning 负责时间、查询与 ICS；server/reminders 负责 policy/store/service/Windows adapter；各自 API 边界。main 只注册路由和 lifespan。

现有 PersonalWorkspace 管导航；Today/Upcoming 是工作台子视图，Calendar 是同级入口，提醒中心从工作台打开，设置共享既有入口。Forms.Field/SubmitButton、useDraft、typed-date、native select、Evidence.OriginalNotice/ConfirmDialog 和现有纸白/墨色 tokens 继续为 canonical owner。业务写入悲观提交，错误保留输入，脏表单离开确认，读取可取消并防过期结果覆盖。

## 主要外部规范

- [Windows-Toasts](https://github.com/DatGuy1/Windows-Toasts)：WinRT Python adapter，不将通知内容拼入 shell。
- [Microsoft desktop toast](https://learn.microsoft.com/en-us/windows/win32/shell/quickstart-sending-desktop-toast)：应用身份及 XML 文本节点。
- [tzlocal](https://github.com/regebro/tzlocal)：系统 IANA 时区与 zoneinfo。
- [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545.html)、[icalendar](https://pypi.org/project/icalendar/)：标准事件、日期精度、UTC 与序列化验证。

实施中以测试、真实运行与安全审计证明各项，不以静态清单代替结果。完成证据写入 PHASE-6-REPORT.md。

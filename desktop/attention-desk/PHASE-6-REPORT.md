# Phase 6 · Today、提醒与日历验收报告

日期：2026-10-09。范围：Phase 5.5 checkpoint，以及 Phase 6 本地工作台、Reminder、Windows notification、Calendar、ICS、来源更新确认。未进入 Phase 7，未 push。

## A. Phase 5.5 checkpoint

- Commit：`f2b370f497b8e17d8c2d6a4641e3b7280e32dbf2`
- Tag：`phase5.5-long-document-stable`
- 创建前完整 Python **576**、frontend **16**、build 均通过，工作区干净，真实秘密/数据库/原件/OCR 审计无发现。
- 真实 26 页 PDF 的已验证提取、26 个区域及 Review 记录，在 Phase 6 迁移及真实后端重启后再次逐值哈希核对一致；沿用此前确认的 54 条结构化记录，不重新调用模型。

## B. Today / Upcoming

工作台默认进入 Today；Upcoming 与全部事项是同一工作台的视图。只查 SQLite 结构化事实，不读 PDF、不重新 OCR、不调用 AI。

Today 包含今天截止、逾期未完成和当日计划/已触发提醒的时间节点，按逾期、今天、当日提醒排序，显示入选原因、相关未完成任务和 Evidence 入口。逾期保留为 `OVERDUE`，不会自动完成或归档。Upcoming 默认从今天起 7 天，可选 14/30 天，按最近时间排序；同一 Event 的多个 Deadline 分组展示，保留独立身份。默认排除 done/cancelled，可显式包含；全部事项另有状态筛选。

## C. Reminder Engine

新增独立 `reminder_settings`、`reminder`、`reminder_attempt`。Deadline 仍为事实；Reminder 与 Calendar 是投影。提醒记录包含计划时间、状态、去重键、策略版本、事实指纹、发送尝试、触发/关闭/Snooze 时间及重试时间。

- 仅已确认、无未解决不确定性、日期/时区有效的 Deadline 可提醒。unknown、未确认和无效日期不安排。
- 日期型默认提前 3/1/0 天 09:00；精确时间默认提前 24/2 小时。日期本身不补造 23:59；范围以结束边界安排截止提醒。
- 独立服务每 30 秒运行一次，线程执行有限工作；启动恢复，关闭等待结束，不阻塞 API 事件循环。
- SQLite 原子领取；稳定去重键基于 Deadline、事实及策略偏移。重复 tick、进程重启、系统时间回拨不会重复领取已完成提醒。
- 默认 6 小时补发窗口；唤醒时同一 Deadline 合并过时的多个提醒，每轮最多 3 条。窗口外取消，避免旧提醒集中弹出。
- 日期/策略变化取消旧未来投影并生成新计划；done/cancelled、关联任务全部完成会取消未来提醒。恢复只重建未来安排，已触发历史保留。
- 通知失败最多尝试 3 次，间隔 120/600 秒，失败及下次重试可见。崩溃时无法确认是否送达的领取记录标记 `delivery_unknown` 并停止自动重发，可手动 Snooze。
- 日志只含提醒 ID、计划时间、尝试次数和固定结果码。

实际链路验收发现并修复：空时区 Deadline 在展示投影时被解析后的系统时区覆盖，事实指纹不一致导致发送前被拒绝。现在分别保留原始 `timezone`、`resolved_timezone` 与 `display_timezone`，并增加日期型、精确时间型两项回归。

## D. Windows notification

使用 Windows-Toasts 1.3.1 / Python WinRT，直接调用 Windows 通知 API，无 shell 命令拼接。只注册当前用户 `AttentionDesk.Desktop` 的显示身份；没有管理员操作、Startup、更改通知权限、防火墙或网络配置。通知字符串作为 XML 文本节点 / NotificationData，恶意标签、命令字符保持字面文本。点击通知仅打开经过 UUID 与端口校验的本地事项页面。

隐私支持完整、仅标题 + Deadline（默认）、隐藏敏感内容；完整模式最多显示少量未完成任务，不放入原始私人通知。设置中的“发送测试提醒”独立于 Deadline。

**Windows Toast 真实验收已通过。** 原报告将早期 `DISABLED_FOR_USER` 诊断解读为用户关闭通知，该判断已撤回。后续定位到通知状态查询兼容问题，具体修复与证据见 O 节；真实接口返回 `os_accepted` / `ENABLED`，测试通知进入 Windows 通知历史，用户已确认真实验收通过。没有自动更改 Windows 设置。系统明确禁用通知时仍拒绝投递，Reminder Center 保留失败记录并有限重试。

参考：[Windows-Toasts](https://github.com/DatGuy1/Windows-Toasts)、[自定义 AUMID](https://windows-toasts.readthedocs.io/en/latest/custom_aumid.html)、[Microsoft desktop toast](https://learn.microsoft.com/en-us/windows/win32/shell/quickstart-sending-desktop-toast)。

## E. Snooze / Dismiss

提醒中心提供 10 分钟、1 小时、明天默认提醒时刻。Snooze 持久化并增加发送代次，防止旧异步失败回调覆盖新计划。Dismiss 仅关闭当前 Reminder；不会完成 Event 或 Task。点击事项回到原有详情与 Evidence。

## F. Calendar

Month view 使用原生表格与日期按钮；点击日期显示当天 Deadline 和关联事项。date 为全天，minute 为精确时刻，range 保留范围；同一 Event 多个时间节点分别投影。Calendar 使用当前系统显示时区，空事实时区跟随系统，既有显式时区保留。unknown、未确认、不确定日期排除。默认排除终态，用户可显式包含。界面保留既有纸白/墨色、共享表单和确认对话框。

## G. ICS

支持单 Deadline、单 Event 和未来全部已确认 Deadline。每个 VEVENT 的 UID 使用稳定 Deadline UUID，编辑日期或确认来源延期后不变。date 使用 `VALUE=DATE` 与排他结束日；minute 使用 aware UTC `DTSTART`，范围保留 `DTEND`。包含 DTSTAMP、LAST-MODIFIED、SEQUENCE、角色、少量未完成任务和 Evidence 位置简述。

URL 只包含安全公开来源；不导出原文、原件、本地绝对路径、凭据或携带 token/key 的链接。显式导出已取消事项标记 CANCELLED。采用 icalendar 序列化和解析，验证中文、逗号/分号/换行转义及 UTF-8 75 字节折行。匿名浏览器实际下载文件解析出 **4 个 VEVENT：3 个全天、1 个 aware datetime**，结构有效。

无订阅、OAuth 或双向同步。手动重新导入是否更新由外部日历软件决定。参考：[RFC 5545](https://www.rfc-editor.org/rfc/rfc5545.html)、[icalendar](https://pypi.org/project/icalendar/)。

## H. Migration 与来源更新

沿用编号迁移，新增 `006_planning_reminders.sql`。扩展日期精度并重建 Deadline/Evidence，全部旧列值与 ID 保留。Evidence 增加同一 RawItem 约束，使新旧不可变 Snapshot 都能保留准确引用；迁移事务提交前进行完整外键检查，错误会回滚，正常事务仍开启外键。

真实库先用 SQLite backup API 备份至仓库外，再升级副本：**26 张旧表、1,591 行、全部旧列逐值哈希一致**，包括 **9 Event、8 Deadline、21 Task、80 Evidence**；**6 份原件字节哈希一致**。副本通过后升级真实库，首次启动再次完整核对；恢复默认官网轮询后重启，人工事实、AI/长文档记录、引用和原件再次一致。`quick_check=ok`，外键错误 0。匿名测试事项仅在隔离数据库中创建。

新 Snapshot 的提取候选只显示比较和“来源发生变化”，不会覆盖人工确认。只有用户选择原 Deadline、核对新 Evidence 并明确批准，才更新同一 Deadline、重新投影提醒/日历；旧 Snapshot、旧 Evidence、历史决策保留。测试覆盖实际 ExtractionService + 本地 fixture provider 到比较、批准、重排及重启的闭环，没有额外付费模型调用。

## I. Settings

提醒总开关、日期/精确时间偏移、日期型时刻、通知隐私、0—24 小时补发窗口、测试提醒。设置有版本冲突保护；失败保留输入，通过确认对话框重新读取保存值。AI 提取设置仍在同一设置入口的子视图。

Startup 采用用户允许的“UI + 清晰说明”范围：选项禁用，说明尚未集成；**没有创建启动项**。目前只在后端运行期间提醒；完全退出后不保证系统通知。没有新增 tray 或重写桌面 shell。

## J. 修改 / 新增文件

| 范围 | 文件 |
|---|---|
| 时间、投影、ICS、来源比较 | `server/planning/{time,query,ics,source_update,api,__init__}.py` |
| 提醒领域与 WinRT | `server/reminders/{policy,store,service,windows,api,__init__}.py` |
| 模型、持久化、路由与依赖 | `server/personal_models.py`、`personal_store.py`、`personal_priority.py`、`personal_api.py`、`review_store.py`、`main.py`、`requirements.lock.txt`、`migrations/006_planning_reminders.sql` |
| 工作台与共享日期输入 | `src/App.jsx`、`src/personal/{PersonalWorkspace,ExtractionWorkspace,Forms}.jsx`、`api.js` |
| 新视图与读/导出所有者 | `PlanningViews.jsx`、`ReminderViews.jsx`、`SourceUpdate.jsx`、`IcsExport.jsx`、`usePersonalResource.js`、`planningHelpers.js`、`planning.css` |
| 新自动测试 | `server/test_planning_time.py`、`test_planning.py`、`test_reminders.py`、`test_windows_notifications.py`、`planning_test_helpers.py`、`tests/planning.test.mjs` |
| 既有合同测试更新 | `server/conftest.py`、`test_collector.py`、`test_official_sources.py`、`test_personal_events.py` |
| 文档 | `PHASE-6-PLAN.md`、本报告、`README.md`、`DESIGN.md`、`UX-CONTRACT.md` |

## K. 完整测试

Toast 修复后重新运行最终完整 Python **686 passed**（65.08 秒，0 failed，1 条现有 deprecation warning）；frontend **21 passed**（0 failed）；production build **通过**（1,610 modules，3.04 秒）。另行运行全部 Reminder / Windows notification 专项 **46 passed**（4.68 秒）。此前严格 UI audit **0 errors / 0 warnings / 0 findings**，DESIGN.md lint **0 errors / 0 warnings**；本次未修改前端，`git diff --check` 再次通过。

| 专项（包含于完整 suite） | 用例数 / 覆盖 |
|---|---|
| 时间与迁移 | 21：date/minute/range/unknown、DST、空时区、旧库逐值保留、回滚 |
| Reminder | 34：默认策略、并发领取、重复 tick、重启、grace、编辑/状态/任务、Snooze/Dismiss、失败/隐私/离线 |
| Planning / Calendar / ICS / 来源更新 | 35：跨月年、时区、终态、稳定 UID、标准解析、秘密/路径排除、显式批准 |
| Windows adapter | 12：实际库 XML/NotificationData、四类系统禁用、有符号/无符号状态查询兼容、其他查询错误、接受/失败、稳定标签、安全链接 |
| Collector / pairing | 149：含新增 Today/Calendar/ICS/Reminder 不对 LAN 暴露、手机 session 不能修改设置/提醒/来源 |
| Document pipeline / 长文档 | 31 / 36 |
| 官网 monitor / sources | 12 / 58 |
| DeepSeek E2E / provider / review | 5 / 23 / 34，使用 fixture/transport，不发起本轮真实付费调用 |

浏览器：Browser plugin not available，使用现有 Playwright + Microsoft Edge，1365×950 与 390×844。主流程 **9 项**、加载/空态/慢响应/冲突/键盘/窄屏/减弱动效 **7 项**通过。框架覆盖层与 pageerror 为 0；状态测试唯一 console error 是主动构造的 HTTP 409，已验证恢复；没有未解释的 console warning/error。真实用户库只读浏览器验收 Today、Upcoming、Calendar、提醒设置均通过。

命令：`python -m pytest -q -p no:cacheprovider --basetemp <仓库外独立目录>`、`npm run test:frontend`、`npm run build`、premium `audit_project.py . --mode strict`、`npx -p @google/design.md designmd lint DESIGN.md`。无单独 formatter/typecheck 脚本；运行了 manifest 规定的全部 test/build 和 `git diff --check`。

设计一致性复核：表单复用 Field/useDraft，错误和重读使用原确认对话框；Calendar 为原生表格而非新日期控件；颜色、字体、焦点沿用运行时 CSS tokens。窄屏按钮曾逐字挤压，已修复为整按钮换行并重截图检查；没有扩大视觉改造范围。截图与浏览器脚本保存在仓库外 proof 目录，不纳入 Git。后续可选安装 Browser 插件以便在应用内检查，但本轮验证已由 Playwright 完成。

## L. 实际运行与安全验收

- 隔离库创建“Phase 6 测试事项”、当前时间约 3 分钟后的 Deadline、测试任务 A/B；实际后台 Scheduler 调用真实 WinRT，正确记录 OS 受阻。独立 Python 进程重开无重复发送，Snooze/Dismiss 保留，Event/Deadline/Task 事实哈希不变。
- 匿名 date + datetime 在实际 Calendar 中显示，ICS 由浏览器下载后程序解析成功。来源更新经过明确确认，新的引用能打开正确不可变原文并高亮。
- 真实用户库迁移、两次启动/重启与 26 页提取保留均通过；工作台已恢复在 `http://127.0.0.1:5173/`，API 为 `127.0.0.1:8765`。默认官网轮询恢复。
- Collector 保持停止，没有 active final pairing token，没有生成等待用户回来的短期 QR。LAN 仅原有 Collector 的受限 submit 权限，新增领域 API 不挂载到手机应用。
- 提交审计比对真实 DPAPI API Key、6 份原件、12 个手机快照、112 个文档/OCR 块和运行目录忽略规则；检测值只在内存，不打印秘密。数据库、原件、OCR、测试 ICS、日志、缓存、截图与验收脚本均未加入 Git。
- 没有修改 Windows Firewall、网络类型、通知设置、Startup；没有开放 Desktop API 到 LAN、公网或创建 tunnel。没有 push。

## M. 尚存人工项与范围限制

1. **Windows Toast 真实人工验收已通过**：用户已确认测试提醒成功。通知状态查询兼容问题已修复，应用内兜底、失败容错与去重也已重新验证；没有为了显示通知而自动改系统限制。
2. 完全退出后端期间不保证通知；唤醒/重启仅在 grace 内补发。Startup/tray、Calendar 双向同步不在本轮实现范围。
3. 测试套件有一条现有 Starlette/httpx deprecation warning；没有测试失败，不为本轮升级 transport 依赖。

原 checkpoint 披露的 OS 视觉人工项现已完成，Phase 6 收尾包括本次 Toast 修复及完整回归。

## N. Phase 6 checkpoint

- 原 Phase 6 实现提交：`8382c0581b690f8414843dd9b15a938fceb9ccb9`，`feat: complete phase 6 reminders and calendar`。
- Toast 修复 commit message：`fix: restore Windows toast notification delivery`。
- Annotated tag：`phase6-reminders-calendar-stable`
- 按用户明确授权，将未 push 的本地 annotated stable tag 更新到 Toast 修复提交；不改动原实现提交。完整提交哈希、最终 `git status` 与标签指向在最终响应中列出，也可用 `git rev-parse phase6-reminders-calendar-stable^{commit}` 核对。
- 提交门槛：staged/committed 秘密与私有数据审计均无发现，Git 干净；不 push，停止在 Phase 6。

## O. 2026-10-09 测试提醒后续验证

用户要求发送测试提醒。首次通过真实接口调用时，读取 `ToastNotifier.Setting` 抛出 `0x80070490 / ERROR_NOT_FOUND`，使应用在调用原生 Show 前退出。使用同一个 Attention Desk AUMID 发送匿名测试通知，原生 Show 成功，且该测试已进入 Windows 通知历史；这证明该次状态查询错误并不代表系统禁止投递。

适配器现在仅对上述特定状态查询错误继续调用原生 Show，由 Windows 执行通知策略。其余状态查询错误仍报告失败，用户/应用/组策略/manifest 禁止通知时仍不调用 Show；每次发送清空旧的权限诊断，避免显示过期状态。没有修改 Windows 通知设置、其他应用身份、Firewall 或网络配置。

修复后重启原有 loopback 后端，通过真实 `/api/personal/reminders/test` 再次发送：`result=os_accepted`、`accepted=true`、`notification_setting=ENABLED`。系统接受与通知历史已验证，用户随后确认 Windows Toast 真实验收通过；最终收尾时再次调用真实接口，同样返回接受 / ENABLED，桌面 API 仍仅监听 `127.0.0.1:8765`。

修复初步回归 **81 passed**：Windows adapter 12、Reminder 34、Planning 35。正式收尾重新执行了完整 Python / frontend / build 与全部 Reminder / Windows notification 专项，结果见 K 节。本次仅修改后端适配器、对应测试与本报告。

额外隔离验收模拟通知后端抛出异常：Engine 正常结束该轮，Reminder Center API 返回可见失败记录与下次有限重试时间；后端恢复后重试成功，随后 10 次 tick 与重新打开同一隔离库均不重复投递。Event / Deadline / Task 事实保持原值，日志不包含通知原文或异常中的敏感数据。此验收不触碰真实用户库。

提交审计重新检查 431 个 Git 文件、本地真实 API Key/DPAPI 密文、6 份原件、12 条手机快照、112 个文档/OCR 块及 453 个运行文件/忽略示例，秘密匹配、私人文件、凭据模式、ignore 失败均为 0。证明脚本、匿名验收数据库、日志、缓存与构建输出均留在仓库外或原有忽略目录。按用户要求提交修复并更新本地 stable tag，不 push。

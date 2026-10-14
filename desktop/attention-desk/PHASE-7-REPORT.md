# Phase 7 完成情况

验收日期：2026-10-15（Asia/Shanghai）。基线 `a1fb8a4`，本阶段仅本地提交，不 push。

1. **架构**：React 18/Vite 6 + FastAPI/Uvicorn/Python + SQLite，无 Supabase。完整起始审计见 PHASE-7-ARCHITECTURE.md。扩展原 OfficialSourceMonitor/HTTP/VSB parser/OfficialStore，继续使用 source、RawItem、不可变 RawSnapshot、附件和统一核对流程。
2. **新增功能**：自动官网信息流、首页相关摘要、10 个筛选、分页、全文详情、附件/图片链接、已读/收藏、来源管理/健康/run 日志、启动后台检查、手动同步、跨进程租约、增量与更新、规则画像和保守时间提取。
3. **实际网站**：学校 www、教务 jwc、学院 jxqc、创新创业 iec、学工 xsc、国际交流 wsc、图书馆 lib，均为 zqu.edu.cn 官方子域。每个真实栏目 URL 与导航证据见 OFFICIAL-INFORMATION.md。
4. **学院确认**：机械与汽车工程学院；学校机构设置链接到学院，学院简介、车辆工程专业栏目和车辆工程系交叉确认。学院 9 个 source 全部 high，覆盖通知、新闻、教学、学生、奖助、就业实习、培养、课程及专业信息；竞赛由学院相关栏目与校级竞赛源共同覆盖。
5. **Source 数量**：29 个配置，28 个启用/正常，1 个明确停用的动态智慧就业平台；学院就业与教务实习源正常工作。
6. **真实数据**：542 条已有正文的信息（其中本阶段新增 451 条），精确转载合并显示为 538 条。964 条版本化官网附件元数据，未执行附件；57 条信息具有可靠结构化时间线索。既有历史信息保留。
7. **Backfill**：有限页数/篇数，当前新历史导入窗口为 90 天、每 source 最多 3 页/30 篇；保留开发前及旧后台已经保存的更早记录。旧记录不删除，后续不会因窗口外未知旧 URL 而持续导入往年档案，该回归已验证。
8. **自动频率**：高 30 分钟、普通 60 分钟、学校新闻 4 小时。启动 30 秒内后台检查到期源，不阻塞启动。90 秒 SQLite 租约 + 15 秒心跳，跨进程互斥。工作台后端运行时持续自动获取，完全退出后端停止，没有新增系统启动项。
9. **去重**：规范 URL、HTTP/HTTPS/学校 www 存储别名、标题及正文 SHA-256、精确内容转载展示分组；同标题不同正文不粗暴合并。来源引用、原始 URL、不可变历史和人工证据保留。正文变更生成新版本，阅读/收藏保留。
10. **分类**：19 类本地规则、标题优先、栏目与正文辅助，无模型请求。
11. **relevance**：集中 ZQU 车辆工程/机汽学院/2026 级本科一年级画像，显示命中依据；研究生、教师、毕业年级及其他年级降权。规则版本更新可本地重建投影。
12. **importance**：critical/high/normal/low，变更与截止规则、可靠临近截止、专业/学院要求、报名申请和宣传新闻分别处理；不从不完整日期编造 Deadline。
13. **通知**：复用原 WindowsNotifier 与提醒总开关/隐私；首次补全和正文更新静默，后续仅近期 critical 或 high+relevance>=80，最多三条/小时。原子发送前领取和永久展示身份去重，失败/未知结果不重复弹。通知不创建或修改 Event/Deadline。
14. **迁移**：编号 007，纯新增字段/四张投影运维表/索引。SQLite backup 后副本迁移与重跑，28 张旧表、1,593 行旧列逐值一致。真实库 18 类事实/历史表原行仍存在，9 Event、8 Deadline、21 Task、80 Evidence 和 6 份原件字节哈希保留；quick_check=ok，外键错误 0。来源的检查时间、原文最新状态及 Reminder 投递状态可按正常后台运行变化。
15. **测试**：完整后端 **733 passed / 0 failed**，80.77 秒（含 Phase 7 47 项、原 Reminder/Toast/Calendar/Collector 等）；另验证数据库临时忙碌导致租约释放失败后，本机任务锁仍释放并可恢复。frontend **24 passed / 0 failed**；production build 通过。严格 UI audit 0 errors/0 warnings/findings，DESIGN.md lint 0 errors/0 warnings，diff check 通过。保留一条既有 Starlette/httpx deprecation warning。沙箱阻断 Python socketpair 的问题通过获准的本机测试执行解决，没有修改网络设置。
16. **浏览器验收**：Browser plugin not available，使用已安装 Playwright + Edge，1365×950 / 390×844。主流程 17 项 + 状态恢复 8 项通过：首页摘要、所有分类/相关筛选、全文/原文链接、已读/收藏持久化和恢复、统一核对入口、29 源管理/开关、后台手动同步、分页、Calendar、Reminder Center、设置、全部事项与旧聊天导航；加载/空态/503 恢复/旧请求取消/保存失败保留/键盘/窄屏/减弱动效通过。主流程 pageerror/console error/warning/network error/框架 overlay 均 0；故障验收只包含两次主动模拟 HTTP 503，恢复正常。已查看桌面和窄屏实际截图。
17. **小问题/边界**：动态智慧就业网暂未启用，原因在来源卡中说明；已有学院就业/实习抓取覆盖。部分官网栏目长期没有更新，正确显示零新增；公开网页以外、登录页、微信、附件全文下载解析不是本阶段自动抓取范围。后端需保持运行。
18. **Commit**：本报告随 `feat: automate official university information ingestion` 本地提交；准确 hash 由最终答复及 git show 标签提供，避免文档自引用 hash。
19. **Tag**：本地 annotated `phase7-official-info-ingestion-stable`；不推送。
20. **git status**：验收后提交全部本阶段源码/配置/文档，工作区 clean。真实数据库、原件、私有 key、日志、测试库、浏览器脚本和截图未提交。

## 真实增量与健康验收

真实校站、教务、学院、创新创业、学工、国际交流、图书馆列表全部经过 HTTP 实测，28 个启用 source 的标题/日期/原文 URL 均可解析。七站详情抽查发现 PDF、DOC、DOCX、XLSX 与图片 URL，并核对正文容器不含导航。浏览器触发全部同步后：**28/28 完成，新增 0，更新 0，失败 0**；已保存文章跳过，通常每源仅 1 个列表请求。数据库文章与快照没有因重复检查增长，说明当前增量并非重爬整站。

代码测试另实际覆盖：列表→详情→SQLite 插入，重复运行仅列表，正文变化产生 version 2 且保留 read/save；来源连续失败达到 degraded 后恢复；跨进程和六线程只有一个租约领取成功；中断租约回收；首次静默、永久去重及三条通知限额；007 在旧库连续执行两次安全。

验收证据保存在工作区外的当前 Codex proof 目录（before-phase7.sqlite3、migration-proof.json、source-probe.json、final-stats.json、browser-proof.json、browser-state-proof.json、实际截图），测试 XML 留在被忽略的 artifacts 目录。

**现在可以做到：工作台后端保持运行时，不需要每天人工点击，它会持续获取肇庆学院及机械与汽车工程学院官网的新通知。**

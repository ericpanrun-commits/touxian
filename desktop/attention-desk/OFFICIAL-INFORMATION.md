# 肇庆学院官网信息系统 · Phase 7

## 架构与数据边界

复用 Phase 3 的 `OfficialSourceMonitor`、`PublicHTMLClient`、配置化 VSB parser 和 `OfficialStore`，继续写同一个 SQLite：

`source → HTML 列表 → 发现 URL → 详情 → RawItem / 不可变 RawSnapshot → 附件元数据 → official_item 投影 → 信息流 / 本地通知`

`official_item` 以既有 RawItem 为主键，只保存分类、摘要、相关度、重要性、指纹、展示分组、阅读/收藏及时间线索，不复制正文。`raw_snapshot_web` 保留正文版本、发布时间、原始/规范 URL、图片和附件；既有 `attachment` / `snapshot_attachment` 保存附件关联。一个信息可以关联多个来源和别名，正式事项、Reminder、Calendar 仍走既有人工确认流程。没有 Supabase、第二数据库、付费 AI 调用或微信扩展。

迁移 `007_official_information.sql` 只增加字段、四张投影/运维表和索引，不 drop/reset/清空表。`official_source.fetch_interval` 是当前频率；旧 `polling_interval` 保留兼容旧约束。来源的组织、类型、优先级、入口、域名、适配器和解析器版本保存在既有 config_json，并由管理 API 展开。迁移失败由原事务机制回滚并检查外键。

## 已确认学校与学院

车辆工程属于 **机械与汽车工程学院**，证据链为：

- [学校机构设置](https://www.zqu.edu.cn/jgsz.htm) → [机械与汽车工程学院官网](https://jxqc.zqu.edu.cn/)
- [学院简介](https://jxqc.zqu.edu.cn/xyjj/xyjj.htm)明确列出车辆工程本科专业。
- [车辆工程专业栏目](https://jxqc.zqu.edu.cn/bksjy/zyjs/clgc.htm)及[车辆工程系](https://jxqc.zqu.edu.cn/szdw/clgcx.htm)交叉确认。

所有入口来自原有配置或以上官网及部门导航，不从标题猜地址。以下 28 个启用栏目均通过真实 HTTP 列表、标题、日期及原文 URL 验证；学校、教务、学院、创新创业、学工、国际交流、图书馆七站另抽查详情。

| 官网 | 已启用栏目 |
|---|---|
| [学校](https://www.zqu.edu.cn/) | [校级通知](https://www.zqu.edu.cn/index/tzgg.htm)、[学校要闻](https://www.zqu.edu.cn/index/xxyw.htm) |
| [教务部](https://jwc.zqu.edu.cn/) | [课程通知](https://jwc.zqu.edu.cn/jwgl2/kctz.htm)、[教务通知](https://jwc.zqu.edu.cn/jwgl2/jwtz.htm)、[期末考试](https://jwc.zqu.edu.cn/kszx2/qmks.htm)、[成绩管理](https://jwc.zqu.edu.cn/xjgl2/cjgl.htm)、[学籍通知](https://jwc.zqu.edu.cn/xjgl2/xjtz.htm)、[调停课程](https://jwc.zqu.edu.cn/jwgl2/dtkc.htm)、[学科竞赛](https://jwc.zqu.edu.cn/sjjx2/xkjs.htm)、[专业实习](https://jwc.zqu.edu.cn/sjjx2/zysx.htm) |
| [机械与汽车工程学院](https://jxqc.zqu.edu.cn/) | [教学通知](https://jxqc.zqu.edu.cn/index/jxgz.htm)、[通知公告](https://jxqc.zqu.edu.cn/index/tzgg.htm)、[学院新闻](https://jxqc.zqu.edu.cn/index/xyxw.htm)、[学生活动](https://jxqc.zqu.edu.cn/xsgz/xshd.htm)、[奖助学金](https://jxqc.zqu.edu.cn/xsgz/jzxj.htm)、[就业与实习](https://jxqc.zqu.edu.cn/zsjy/jyxx.htm)、[培养计划](https://jxqc.zqu.edu.cn/bksjy/pyjh.htm)、[课程建设](https://jxqc.zqu.edu.cn/bksjy/kcjs.htm)、[车辆工程专业](https://jxqc.zqu.edu.cn/bksjy/zyjs/clgc.htm) |
| [创新创业学院](https://iec.zqu.edu.cn/) | [通知公告](https://iec.zqu.edu.cn/tzgg.htm) |
| [学生工作部](https://xsc.zqu.edu.cn/) | [学工通知](https://xsc.zqu.edu.cn/xwgg/xgtz.htm)、[助学金](https://xsc.zqu.edu.cn/zzgl/zxj.htm)、[奖励项目](https://xsc.zqu.edu.cn/xsgl/jlxm.htm) |
| [对外交流合作部](https://wsc.zqu.edu.cn/) | [通知公告](https://wsc.zqu.edu.cn/index/tzgg.htm)、[学期交流](https://wsc.zqu.edu.cn/jwxx/xqjlxm.htm)、[寒暑期研学](https://wsc.zqu.edu.cn/jwxx/hsqyxxm.htm)、[学位留学](https://wsc.zqu.edu.cn/jwxx/xwlxxm.htm) |
| [图书馆](https://lib.zqu.edu.cn/) | [资讯与服务通知](https://lib.zqu.edu.cn/zxdt1.htm) |

第 29 个配置为[学校智慧就业网企业招聘](https://jyzd.zqu.edu.cn/career/zpxx/zpxx)：入口来自学校导航，但列表属于独立动态平台，当前明确停用，没有猜测接口或执行网页 JS。学院就业与教务实习栏目正常覆盖就业/实习信息。学院竞赛与活动由学院教学/学生活动和教务、创新创业栏目共同覆盖。

## 添加来源和 adapter

编辑 `server/config/official_sources.json`，先从官方导航核实 list_url，再添加唯一 id、name、school、department、category、list_url、精确 allowed_hosts、parser_type、source_type、priority、enabled、polling_interval、max_pages/max_items。不要把整个互联网域名放入允许列表；跨站转载需要核实并显式增加那个官方主机。

已有 adapter：`vsb_school`（学校日期块）、`vsb_div`（部门 div 列表）、`vsb_table`（学院/部门表格）、`vsb_generic`（通用 VSB 文章链接）。`GenericListAdapter` 合并集中 `PATTERNS` 与 source 的 `selectors` 配置，可覆盖 `links/title/date/body`。例如图书馆只覆盖 `links` 为 `.text-list a[href*="info/"]`。正文必须匹配已验证容器，失败不能退回整页文本；新结构先增加匿名 DOM fixture 与解析测试，再启用。无需 RSS，不下载/执行附件或远程代码。

配置保存后重启后端生效；新来源开始有限补全，已有启用/停用状态持久保留。规则变更提高 `official_profile.json.version`，重启时本地重建信息投影，不重新抓正文、不调用模型、不修改原文或事实。`ATTENTION_OFFICIAL_SOURCES` / `ATTENTION_OFFICIAL_PROFILE` 可指定独立配置文件。

## 同步与增量

- 后端 lifespan 启动原有后台 scheduler，第一次 tick 在 30 秒内，网页打开/关闭不影响常驻后端调度。
- 学院与核心通知 30 分钟，普通来源 60 分钟，学校新闻 4 小时。仅到期、启用来源参与自动检查；完全退出后端停止。没有新建 Windows Startup、任务计划或防火墙规则。
- 首次最多 3 页/30 篇，新导入日期窗口 90 天；无法可靠确定日期的项目仍受页数/篇数限制。既有历史记录不删除。后续一般只读首列表页，已保存正文 24 小时内跳过；每天每来源轮换最多 3 篇较旧文章复查，ETag/Last-Modified 支持 304。
- 同一进程串行批次；SQLite 原子租约有效期 90 秒，每 15 秒心跳，多进程只允许一个批次。异常退出后租约自然过期，下一批标记未结束 run 为 interrupted；旧记录保留。
- 单批最多 240 篇、15 分钟，每 source 最多 3 页/30 篇加最多 3 篇复查；达到上限的来源下一轮继续。每 host 同时 1 个请求，至少间隔 1 秒。最多 3 次请求尝试，退避 1/2 秒；超时 12 秒、HTML 2 MB、最多 3 次重定向，每次校验允许域名、公开 DNS、http/https 和内容类型。详情连续失败 3 次便停止该来源本轮文章处理。
- 规范 URL 去除片段、跟踪参数、点路径并排序参数；存储身份合并 HTTP/HTTPS 和学校根域 www 别名。正文/标题指纹识别精确转载，合并显示但保留来源引用。相同标题不同正文不会仅因标题相同而合并。变化生成不可变新版本，旧证据、已读和收藏保留。

## 手动同步与诊断

页面右上角「同步官网信息」，或「信息源 → 全部同步 / 立即同步」。任务在后台运行，可以离开页面；完成显示新增、更新与失败来源数。也可从 PowerShell 调用同一接口：

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/api/personal/official/scan' -ContentType 'application/json' -Body '{}'
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/api/personal/official/scan' -ContentType 'application/json' -Body '{"source_ids":["zqu-jxqc"]}'
Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/personal/official/overview'
Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/personal/official/runs?source_id=zqu-jxqc'
```

来源卡显示最近检查/成功/下次检查、错误、连续失败及最近 run。`official_run` 保存 start/finish、source、scanned/discovered/inserted/updated/skipped/failed/requests 与固定错误码。>=3 次来源失败显示黄色警告，成功后清零并恢复。失败来源指数退避，其他来源继续。

排查顺序：原始栏目是否公开可访问 → last_error/error_code → parser 结构变化 → 精确 allowed_hosts/重定向 → HTTP timeout/429/5xx。不要关闭 TLS 校验、改网络配置或执行网页脚本。列表暂时空会显示零新增；解析失败明确显示错误，不能伪装成空列表。`ATTENTION_OFFICIAL_POLL=0` 仅用于测试或有意关闭自动同步。

## 分类、相关度、重要性、通知

19 类规则和关键词集中在 `official_profile.json`。标题优先，其次栏目目的和正文；摘要为本地正文摘录。画像为肇庆学院/车辆工程/机械与汽车工程学院/2026 级本科一年级：专业、学院、教务考试选课最高，竞赛、国际交流、实习其次。研究生、教师、毕业年级、其他年级明确降权，界面显示相关依据。

重要性为 critical/high/normal/low。停调课、截止变更等规则及可靠的临近截止提高重要性；新闻宣传降低。只抽取完整、合法、角色明确的绝对日期，冲突、缺年份或不可靠范围保留 null；界面显示原文依据和“待核对”，不会自动生成 Deadline。

原有 WindowsNotifier 被复用，不修改 Phase 6 提醒引擎。首次补全、正文更新、普通新闻不弹；后续新信息须 critical，或 high 且相关度>=80，并且发布时间在最近两天内。每小时最多三条，display_group 在发送前原子领取；失败或未知结果不自动重发同一条，避免轰炸。遵守原提醒总开关/隐私，通知账本为 `official_notification`。完整退出后端不能持续同步或通知。

## 测试

`python -m pytest -q`、`npm run test:frontend`、`npm run build`。Phase 7 合同测试覆盖 URL/DNS/日期/解析/sanitize/附件/分类/相关度/重要性/时间提取、真实 SQLite 列表到详情增量更新、来源隔离、跨进程租约、并发领取、通知限量/去重、分页 API、迁移旧行保留。完整 suite 仍覆盖 Reminder、Calendar、Toast、文件解析与原有收集流程。

真实官网测试需限量限速；日常 suite 使用 fixture/mock transport，不能每次测试都请求学校。真实运行报告与最终统计见 [Phase 7 验收报告](PHASE-7-REPORT.md)。

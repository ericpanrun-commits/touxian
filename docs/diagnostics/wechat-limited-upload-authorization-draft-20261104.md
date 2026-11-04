# 独立云端上传授权申请（草案，未批准、未执行）

本次只准备申请；需要后续明确授权才能执行。样本审阅及本人会话确认不视为上传批准。

拟申请范围：一次性发布两条已审阅脱敏Raw的8条现有读模型投影，附件、图片、OCR、文件元信息均为0。

固定载荷SHA256（bundle_hash）：`908e472419f0d680b01a0fda841aba970e51342cddac39df80ae6a78a675e60b`。

目标：既有Supabase项目mantuehuzptactymjpqg，工作区50e5a3ac-dd15-4f0a-a93c-ace4b735d16d。API：https://mantuehuzptactymjpqg.supabase.co/functions/v1/eric-app-api。

主表：public.eric_app_records，kind限定raw_item/inbox_item/inbox/raw_review；原eric_app_changes及eric_app_workers只记录对应变更和发布元信息。原ID和去重机制保留，不新建表或服务。

正文仅为“校纪校规考试安排（计划）”“晚自习学习要求”的脱敏节选；course为空并保持原状，来源/原消息/快照ID和发布时间保留。具体8条记录如下：

- raw_item / d5fc2590-6e46-4a2e-8489-26094fd8e6c0；source_hash=73ba2a39e9dd5592f053a2edf4666fe972d0907d2e878ebc37e3f7f041a0bdc8
- inbox_item / d5fc2590-6e46-4a2e-8489-26094fd8e6c0；source_hash=8f44e0c215d99e624fb24c05a567fba3bb674ffac9e97b7c78c44e134cc6936d
- inbox / d5fc2590-6e46-4a2e-8489-26094fd8e6c0；source_hash=8342637142eeb973ed8138d0bdc8d9554ea1b31196a24705d1b0fdfd71965945
- raw_review / 5670fdb2-c494-401d-8108-85d99c46aa5c；source_hash=a4dd9320b12954c48d4aa5543a57a5d3824301b6fdde04ed3b21a4d88af3d743
- raw_item / 1b26a8b3-cd55-4284-be7f-36cd20c26da2；source_hash=b88a3dd5fe8b2b91feda85ae7570ebbf640a10d1029e7590f85ce77e2a6f4cb8
- inbox_item / 1b26a8b3-cd55-4284-be7f-36cd20c26da2；source_hash=66ffb66cddb2ec14e175127d7e92e5ef4f7248e89a1c140dc995a0a5f8c1b9e4
- inbox / 1b26a8b3-cd55-4284-be7f-36cd20c26da2；source_hash=275de1c57dfbd6b022f3d369a78f37ccd17aef139dffb7b6706f0cf176a3f263
- raw_review / 91a3c166-c39f-4831-8930-06a683fcd662；source_hash=ca495c9ebfc8ed46d06abdd7315c8fc14a8ae30c7ff0fca78fce8b45783a4586

读取边界：现有owner已验证账号及本人确认保留的5个未撤销登录会话，无新增读者或分享链接；RLS不变，无按群隔离，服务端已有管理权限不变。会话撤销/登出边界见最终审查报告。

明确不申请：全量积压或后来新采集Raw/附件同步、任何附件或资源登记、其他来源数据发布、DeepSeek或其他付费模型请求、领取/执行云端命令、创建或确认Matter/Task、修改课程关联、变更RLS/设备/账号配置。

执行约束：后台全量同步继续暂停；复用原客户端/接口，仅受控发送必要publisher元信息和固定8条载荷，不能调用默认全量publish。发布前再次核对原消息证据、全部字段/哈希和远端目标ID；任何内容变化、意外旧记录或并发冲突均停止，不擅自扩大范围或覆盖。

验收：按原ID核对实际回执及远端去重，再通过已授权owner会话核对Eric OS API与Inbox“全部来源”的脱敏节选。失败先查已有回执，不盲目重复请求。

风险与恢复：正式上传后，符合上述读取边界的会话及服务端管理权限可以访问文字副本；代码回滚不会撤回已上传内容。当前尚无上传，8个目标记录亦不存在。本地原文、来源ID和游标不改。

**草案状态：未提交执行、未获得上传授权。本轮仅准备该申请，禁止根据这份文件自动上传。**

# 微信 DB-0 实测及正式接入结果

2026-10-22（Asia/Shanghai）：**DB_READ = PASS**。

## 实测对象

- 库：`fanyuantaier/wechatauto-replica` 1.2.7.0，固定 commit [`d322c4a9b1124228820bab363e5676016f188fc6`](https://github.com/fanyuantaier/wechatauto-replica/commit/d322c4a9b1124228820bab363e5676016f188fc6)。
- 只保存上游 `db.py`，位于 `server/wechat_collect/replica_db_core.py`。唯一 vendor 修改是把包级 logger 导入改为标准 logging；上游许可证保留在 `REPLICA-LICENSE.txt`。
- 不安装/导入上游包入口、WeChat UI、发送、自动回复、朋友圈、accessibility 或 media 的 UI 驱动。
- 原始源码 SHA-256：`9afa42e47e67795c82d2b7b8e0320ebaaf4bb0e3aee53245f2d5a5ca9ea20df0`；vendor 文件 SHA-256：`6506d991ade15a8f16b02b899fab13539121dc7c4f332cb6935b621c854611ee`。
- 实际运行微信版本：**4.1.15.13**，从运行进程对应 EXE 的版本资源只读获取。版本变化会阻断正式读取，要求重新验证。
- 精确目标群：`26车辆2班（公告群）` → **`43900660377@chatroom`**；不使用模糊匹配，同名多个结果拒绝绑定。

## 最近 20 条结果

完整结构化结果：`artifacts/wechat-db0/results/latest-20.json`（私有、Git 忽略）。实测摘要：`report.json`，后续接入验收：`integration.json`、`runtime.json`、`migration.json`、`listener.json`。

- 20/20 读出，时间范围为 2026-10-21 09:05:35 至 2026-10-22 14:03:21（北京时间）。
- 正文、原始类型数值/类型名称、Unix 时间/带时区时间、local ID、server ID、sort_seq、shard、内部群 ID、内容指纹均提供。
- 19 条普通消息的发送者通过**消息所在分片的 Name2Id**解析，并与群消息正文里的发送者前缀交叉核对：19 一致、0 不一致。显示名取 contact 的备注/昵称，未声称一定等于群名片。1 条系统撤回通知没有可靠的人类发送者，保持 null。
- 输出按 `(sort_seq, shard, local_id)` 升序；时间非降序；两次独立读取的 ID 完全一致，20 个稳定 ID 唯一。
- local_id 只在分片内唯一；稳定身份使用账号 + 内部群 ID + 分片 + local_id，另保留 server_id 和指纹。
- 有 8 条图片、2 条链接/小程序、9 条文本和 1 条系统通知。图片保留独立媒体引用，卡片提取标题/描述/链接。不会将媒体 XML 内的 AES 密钥写入结构化结果或普通日志。

## 媒体

- 8/8 图片均由目标消息的 packed_info 缓存 MD5，定位到**该群专属附件目录**；不能把 XML 中首个 32 位字符串当图片 ID（它可能是密钥）。
- 5/8 已解密为 PNG 并通过 Pillow 验证；3/8 为当前转换器未支持的本地格式，保留加密原件和可追踪引用，不冒充成功。
- 只使用本地缓存和 DB 密钥读取顺带取得的 cfg 数据；无独立图片密钥堆扫描、CDN 请求、查看原图点击或整窗 OCR。
- 正式 Collector 把可读图片/文件和不可解码的加密原件保存到 Attention Desk 内容寻址 Evidence 目录；失败不丢相邻文字。
- 本批卡片是链接和小程序，没有真实文件附件样本。**共享 msg/file 中同名文件不能证明消息归属**，文件引用保持待核对，取得经过验证的消息绑定资源路径前拒绝读取共享文件内容。

## 隐私及只读边界

**messages_sent = 0；unauthorized_message_bodies_read = 0。**

上述正文计数指查询/解码为消息记录的正文；用户已允许的密钥扫描和共享加密分片物理解密会读取原始字节。共享分片副本只存在项目 Git 忽略的 `artifacts/wechat-db0/private`、`artifacts/wechat-db-runtime`；不会将其他聊天解码成消息对象、导出、写入 Attention Desk 或交给模型。

上游默认行为没有直接采用：

1. `get_sessions()` 原本含 summary；替换为 username / last_timestamp 等必要元数据。
2. `PRAGMA quick_check` 原本遍历全库页；替换为 schema 检查，并在目标表查询遇到错误时停止。WAL 未完整合并也拒绝作为完整结果。
3. `QueryBoundary` 在 SQLite authorizer 层拒绝未授权消息表、session 摘要及所有写入/ATTACH；消息读取前还会验证精确群 ID allowlist。
4. 禁止原始数据库写入。Windows 进程仅以 `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION` 打开；没有进程写入、注入、Hook 或客户端设置修改。
5. 密钥缓存为当前 Windows 用户 DPAPI 密文，保存在项目临时目录；禁用上游项目外缓存和自动切换工作目录。
6. Listener 只注册目标群，执行一次有界轮询；未启用 `add_all` 或自动发现。正式调度使用自有增量 Adapter，避免 sort_seq 相同消息跨轮次遗漏。

## 已接入的正式链路

`ReadOnlyWeChatDBAdapter` → 隔离 DB worker → allowed_chats → 分片 local ID 增量 → 原有本地过滤/组批 → DeepSeek 文本理解 → Raw Inbox / Pending Review。

每群保存 internal_chat_id、每分片 local ID 水位、最近 local ID / create_time / sort_seq、secondary_fingerprint、last_successful_scan。新消息和游标在同一个 Attention Desk 事务提交。新增群默认 24 小时回溯，后续重启/隔夜沿用游标；相同 sort_seq 的新增 local ID 不会漏掉。ID 重置、账号变化、消息身份冲突或读取失败会停止并保留游标。

真实隔离接入：首轮采到 24 小时内 **8 条**，落盘 **8 个稳定身份、2 个待复核批次**；第二轮 **0 条新增、1 次元数据查询、0 次消息正文查询、0 次 DeepSeek**，外键检查通过。

正式库从 v9 迁移到 v11，已自动保存 `personal.sqlite3.before-v11.backup`。迁移前后 Event=9、Deadline=8、Task=21、Evidence=80，数量不变；副本验证还覆盖 RawItem / Snapshot / SourceBlock 数量及外键。

首次真实生产分析共 2 次 DeepSeek 文本请求：1 批验证成功，1 批因 JSON 后附额外文本被严格拒绝（invalid_json）。两批都留在 Pending Review；未付费自动修复/重试，未创建正式事项。所有模型候选，包括低重要性候选，均强制 needs_review；DB 主路径禁用 DeepSeek Vision。

## 后台运行

- 当前用户 Windows 任务：`AttentionDesk-Local`，登录自动启动 `pythonw -m server.desktop_entrypoint`，状态已验证为 Running。
- 本地工作台：`http://127.0.0.1:8765`。后台程序由 Windows 任务启动，不依赖 Codex / ChatGPT Work。
- 北京时间 06:00–24:00 每 15 分钟；00:00–06:00 禁止 DB 读取和模型队列执行；06:00 沿游标补扫。数据库路径不接受手动越过静默。
- 无变化轮次不会执行 DeepSeek 队列（包括重试）；已有失败结果保持待人工处理。
- 配置：`desktop/data/attention-desk/wechat.yaml`，当前 enabled=true，allowed_chats 仅含测试群。状态 API 只读本地 Collector 元数据，不探测微信。
- 无窗口入口已处理 pythonw 的空标准流和 Uvicorn 控制台日志兼容问题，使用本地滚动日志。

## 验证与复现

完整项目回归：1055 passed、25 skipped；后续微信/DeepSeek 专项 138 passed，最终部署版本的 Adapter/边界/模型复核/迁移回归 25 passed。测试使用模拟模型和临时库；真实读取仅针对上述明确允许群。

PowerShell 7：

```powershell
.\scripts\install-wechat-db-runtime.ps1
.\artifacts\wechat-db0\venv\Scripts\python.exe .\scripts\wechat_db0.py
..\.venv\Scripts\python.exe .\scripts\wechat_db_acceptance.py
..\.venv\Scripts\python.exe .\scripts\wechat_db_runtime_status.py
.\scripts\register-desktop-startup.ps1 -Check
```

隔离依赖锁：`server/requirements-wechat-db.lock.txt`；未安装到现有 Attention Desk venv。停用采集可将本机 YAML 的 enabled 改为 false；登录任务可用 `register-desktop-startup.ps1 -Remove` 移除。

剩余限制：3 张目标图片格式转换未完成；真实文件附件的资源路径未验证；新消息实际到达和跨真实睡眠/登录的长期稳定性需要后续自然运行观察。不会以 UIA 或整窗 OCR 替代这些验证。

# Windows 桌面入口

2026-10-24 已安装并验收。

## 日常使用

双击桌面或开始菜单的 **Attention Desk**，默认浏览器会打开“今天”工作台。关闭标签页后，下次继续双击即可。Collector 独立在后台运行，不随浏览器退出。

当前快捷方式位置：

- 桌面：`C:\Users\ericp\Desktop\Attention Desk.lnk`
- 开始菜单：`C:\Users\ericp\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Attention Desk.lnk`

两个入口均使用当前用户的 `desktop/.venv/Scripts/pythonw.exe`，运行 `scripts/attention-desk-launcher.pyw`；工作目录固定为项目根目录。图标复用现有 PNG，打包为多尺寸 `public/attention-desk.ico`。快捷方式未设置管理员启动标志。

## 启动器行为

1. 使用原生命名互斥对象合并正在进行的重复启动。
2. 检查本地服务身份、项目标识、存储初始化状态和前端构建资源；健康检查不访问邮件或其他可选集成，也不使用网络代理或跳转到其他地址。
3. 前端资源缺失时，使用已有 Node/Vite 静默构建。Collector 可以先于构建启动，随后生成的页面无需重启 Collector 即可提供。
4. 服务没有监听时，独立启动现有 `server.desktop_entrypoint`；服务已运行时直接复用。后台原有单实例保护继续生效。
5. 等待后端与实际页面就绪，再交给 Windows 默认浏览器打开“今天”。后台进程不属于浏览器生命周期。
6. 缺少环境、启动超时、地址被占用或浏览器无法打开时，显示简单中文提示。日志只记录状态、错误类别和进程号，位于 `desktop/data/attention-desk/logs/launcher.log`。

无需管理员权限；启动器、后台和按需构建都使用无控制台方式。没有新增常驻前端开发服务器，也没有改变微信 Adapter、allowlist、cursor 或分类与 Bark 规则。

## 实际验证

- **冷启动真实双击**：确认工作台端口不再监听后，于 00:05:59 用真实鼠标双击桌面的 Attention Desk 图标。00:06:01 后台就绪，记录 `mode=started`，默认 Microsoft Edge 打开“今天 · Attention Desk”。
- **关闭页面**：只关闭 Attention Desk 标签页，服务 PID 31248 保持；Collector 心跳从 00:08:20 继续到 00:08:25，`collector_alive=true`。
- **已有服务真实双击**：00:11:03 再次用真实鼠标双击同一桌面图标。启动日志记录 `mode=reused pid=31248` 和 `browser_open`；默认浏览器再次显示工作台，未新建服务实例。
- **最终服务与开始菜单**：最终代码重新通过桌面快捷方式启动为 PID 60980；从开始菜单快捷方式打开后仍为同一 PID。现有登录任务再次执行返回 0，也复用同一服务。
- **登录 Collector**：`AttentionDesk-Local` 任务定义与实施前导出的 XML 一致，依然是当前用户 Interactive / Limited 登录任务。任务与浏览器相互独立。
- **当前状态**：只有一个监听工作台端口的服务实例；对应 Python 启动包装进程及实际服务均为 `pythonw.exe`，窗口句柄为 0。当前为夜间静默时段，Collector 心跳正常，四个授权群保持，微信发送和未授权群正文读取均为 0。
- **页面**：对正式入口实际渲染检查通过，“今天”和“微信待确认”已显示，无页面运行错误。真实默认浏览器打开与独立渲染校验分别验证。
- **测试**：启动器专项与现有健康/页面相关回归共 40 项通过；实际静默前端构建通过。覆盖冷启动、服务复用、慢启动、超时、无关端口、页面缺失恢复、原生互斥释放及错误提示；重复安装快捷方式通过。

验收原始记录和截图保存在本次私有 QA 目录，未写入 Git。快捷方式重建命令见 README 的“Windows 日常入口”。

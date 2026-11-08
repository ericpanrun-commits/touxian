# Phase 6 — 本地工程作品集与验收说明

## 架构选择

已检查现有 React/Vite 界面、Windows 本地 FastAPI 服务和独立构建的公共 Web/PWA。`start.ps1` 已使用本机 `127.0.0.1` 启动既有服务，后端能够提供本地构建产物。因此复用本地服务，增加只读扫描接口和延迟加载的作品墙，不新增服务、数据库或浏览器目录授权体系。

「刷新作品」重新扫描磁盘，不维护持久索引，不增加文件监听线程。每个直接子文件夹代表作品，标题来自目录名，递归发现其中的支持文件。重启后按保存的目录重新读取。

PR 基于当前已发布 PWA 源码分支 `fix/windows-pwa-updates` 的 `c5d30a7`，以该分支为目标。远端 `main` 的 `d662e1f` 尚未包含当前 PWA 界面，选择该目标避免混入整个 PWA 历史。原工作区的未提交修改未被覆盖。本次不合并、不部署。

## 使用与人工复验

1. 在资源管理器创建 `D:\Engineering-Portfolio`，模块不会自动创建或移动作品目录。
2. 每件作品建立一个子文件夹，例如：

   ```text
   D:\Engineering-Portfolio\转向节设计\
     cover.png
     转向节.SLDPRT
     零件图.dwg
     手绘.pdf
   ```

3. 在 `desktop/attention-desk` 按原方式运行 `pwsh -File .\start.ps1`；首次安装仍沿用原有 `-Install`。
4. 从侧栏进入「工程作品集」，或访问 `http://127.0.0.1:8765/?personal_view=portfolio`。开发入口为 `http://127.0.0.1:5173/?personal_view=portfolio`。
5. 将新作品文件夹拖入总目录，点击「刷新作品」。搜索作品名称，或选择实际文件扩展名筛选。
6. 点击卡片查看文件名、类型、大小和修改时间。查看图片、在新标签页本地预览 PDF，再点击「打开作品文件夹」。
7. 修改/添加工程文件后刷新；将作品目录改名或移出后刷新。重启本地工作台，核对作品仍依据原目录存在，原文件保持不变。
8. 「修改目录」只保存路径，不移动文件。保存失败保留输入并聚焦错误字段；未保存输入沿用既有离开确认。

支持 JPG、JPEG、PNG、PDF、SLDPRT、SLDASM、SLDDRW、DWG、DXF、STEP、STP，大小写不敏感。根层 cover.jpg / cover.png 优先，之后以稳定文件名顺序选择图片；无图片显示图标。CAD/SW/STEP 文件只展示元数据，不转换、不渲染。

## 数据与访问边界

- 默认 `D:\Engineering-Portfolio`。路径偏好保存在 `%LOCALAPPDATA%\Eric OS\engineering-portfolio.json`，仅含 `root` 字段，独立于作品及 Git。
- 可用 `ERIC_PORTFOLIO_ROOT` 指定启动路径，界面不会覆盖该配置；取消该变量后恢复界面的路径偏好。
- 根目录必须是本机独立绝对目录，不能位于当前仓库或其他含 `.git` 的工作树中。拒绝盘符根、UNC/网络路径、符号链接和目录联接。
- 不跟随子目录中的 Git 仓库、符号链接或目录联接，忽略点号开头内容与 `~$` 临时文件。解码后重新校验路径，阻止越界、绝对路径和 NTFS 附加流。
- 仅在 `server.main` 挂载接口，云端和手机 Collector 均不挂载。检查连接来源、Host、Origin、浏览器跨站标记及 desktop 运行身份，不向公网/局域网开放。
- `GET /api/personal/portfolio` 返回当前元数据；`PUT /root` 只保存路径；`GET /projects/{id}/preview/{file}` 只提供原始图片/PDF；`POST /projects/{id}/open` 只打开已验证作品文件夹。
- 无新表、迁移、作品记录、索引文件、原件副本或云端上传/同步。预览响应 `no-store`；封面地址包含修改时间/大小，覆盖同名封面后刷新会读取新内容。
- 最多 500 个作品；每件最多 2,000 个支持文件、20,000 个目录项、12 层嵌套。达到上限或读取失败有明确提示。卡片每批显示 24 个，并可「加载更多」。

## PWA 限制

公共 HTTPS Web/PWA 无法读取 D 盘。其「工程作品集」只显示限制和「打开本机作品集」链接，不调用作品接口，不将作品写入云端请求、离线队列或应用外壳缓存。链接要求存放作品的 Windows 电脑正在运行本地工作台；自定义端口从本地工作台进入。手机、iPad、其他电脑无法读取这些文件，不引入跨设备同步。

## 已执行验收（Windows，2026-11-08）

全部使用仓库外的合成文件，未读取或改动真实作品。合成 SLDPRT/DWG 验证文件发现与元数据，不代表 CAD 软件打开或模型正确性测试。

| 检查 | 结果 |
| --- | --- |
| 空目录中新增包含封面、SLDPRT、DWG、PDF 的作品文件夹后手动刷新 | 识别卡片及封面，通过 |
| 默认图标、大小写扩展名、递归、封面优先及回退 | 通过 |
| 搜索、清除、类型筛选、无结果、筛选刷新后恢复 | 通过 |
| 文件清单、原始图片字节、本地 PDF 响应及新标签预览 | 通过 |
| 覆盖同名封面后刷新 | 请求地址更新并读取新封面，通过 |
| 新增文件、目录改名、将作品移出总目录后刷新 | 通过 |
| 更改作品目录并实际停止/重启测试服务，再恢复原目录并重启 | 偏好和作品均保留，通过 |
| 浏览期间对比原文件 SHA-256、大小、mtime | 完全一致，通过 |
| 路径穿越、Git 目录、Windows junction、远程/跨站访问、cloud 身份、CAD 预览拒绝 | 通过 |
| 真实资源管理器打开 | 已打开合成作品目录；Windows Shell 确认标题和准确路径后，仅关闭该测试窗口 |
| 空、加载、错误/重试、错误输入保留/聚焦、Enter/Escape/焦点返回、原生类型选择 | 通过 |
| 1440×1000 / 390×844、浅色/深色、减少动画 | 通过，无横向溢出、框架错误、应用运行错误或本地流程外部请求 |
| PWA 桌面/手机入口及既有完整 PWA 浏览器回归 | 通过；明确本地限制，无云端作品数据请求 |

复跑命令（使用现有 Python 环境，在 `desktop/attention-desk`）：

```powershell
$env:ATTENTION_INBOX_POLL = '0'
python -m pytest -q server/test_engineering_portfolio.py
npm run test:frontend
npm run build
$env:ERIC_TEST_PYTHON = (Get-Command python).Source
npm run test:browser:portfolio
# 另开终端：npm exec vite preview -- --host 127.0.0.1 --port 4178 --outDir dist/client
$env:ERIC_WEB_PREVIEW = 'http://127.0.0.1:4178'
npm run test:web
```

新增后端 **21 通过**；既有前端 **46 通过**；本地浏览器流程 **9 组通过**；完整 PWA 浏览器回归通过；桌面/PWA 构建通过；strict UI 静态检查 0 错误/0 警告。构建保留原有 PDF 大分包提示，未新增依赖。

全后端回归：**1009 通过、25 跳过、2 失败**。两个失败在未修改的 `c5d30a7` 工作树运行相同测试完全复现，均在 `server/test_actions_worker.py`：系统证书上下文含两张证书而旧测试要求一张；既有工作流多出 archive 选项而旧测试仅预期 cycle/acceptance。本 PR 未修改证书、工作流或这些测试，不将全量回归表述为全绿。

截图和机器报告保留于 `D:\Eric OS\test-data\phase6-portfolio-evidence` 与 `D:\Eric OS\test-data\phase6-cloud-evidence`，不进入 Git。真实日常作品和安装升级请在审阅并选择采用本 PR 后复验；本次未变更运行实例或发布版本。

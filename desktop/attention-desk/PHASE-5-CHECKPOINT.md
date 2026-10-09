# Phase 5 Mobile Collector 稳定 checkpoint

2026-10-09。真实 iPhone 验收已完成。本 checkpoint 保存 Phase 5 实现、自动化测试与验收文档；不进入 Calendar 或下一阶段。

提交信息：`feat: complete phase 5 mobile collector`。本地标签：`phase5-mobile-collector-stable`。不 push。

## 提交前数据与凭证检查

检查范围为整个仓库现有 tracked files 和所有未忽略的待提交文件，并在 staging 后再次核对实际 Git index。

- 本机 DPAPI 解密后的 DeepSeek API Key 仅在内存中与待提交字节比对，不输出或保存密钥；密钥和 DPAPI 密文均未出现。
- 检查完整 pairing URL token、device Cookie secret、Bearer 和硬编码凭证模式；没有实际凭证。配对诊断只允许短 hash 与有限状态，测试中的凭证由测试临时生成。
- 用户数据库、上传原件、OCR/解析内容、日志与缓存未纳入 Git。实际原件按字节 hash 比对，真实手机快照和文件源块按长原文片段比对，没有命中待提交文件。
- 对本机运行数据目录中的每个实际文件及代表性的数据库、截图/PDF/DOCX、OCR、日志、缓存、环境文件、构建和依赖路径执行 `git check-ignore`；全部匹配。没有已被忽略却仍 tracked 的文件。
- `.gitignore` 保留完整运行数据目录隔离，并补充任意层级的 Collector 本地状态文件及生成的 `premium-audit.json`。后者保留本地，不提交。

审计辅助脚本及详细结果保存于本次任务的仓库外证据目录，不作为产品代码提交。安全日志与诊断凭证脱敏由现有 Collector 测试覆盖。

## 最终验证

| 检查 | 2026-10-09 checkpoint 结果 |
|---|---|
| 完整 Python tests | 540 passed，38.09 秒；1 条已有 Starlette/httpx 弃用 warning |
| frontend tests | 11 passed，0 failed / skipped |
| frontend build | `npm run build` 成功，2.37 秒；desktop/mobile 双入口 |

完整 Python 使用新建的仓库外临时目录和禁用 pytest cache 的方式运行，测试范围未缩小。首次沙箱 build 被 esbuild 父目录读取权限阻止；正常用户权限下重跑通过，未修改系统 ACL。API Key 比对同样仅在正常当前用户权限下读取原 DPAPI 存储，未更改凭证或模型设置。

## 已知但不阻塞 Phase 5 的问题

1. **长 PDF 的 AI 输出可能受当前 4096 output token 上限截断。**真实长 PDF 上传、原件保存和本地解析已通过；截断时原文、解析版本与 Review Queue 记录保留，不能宣称该 PDF 的 AI 分析成功。没有自动提高上限或新增付费重试；仍可由用户主动重试或手动整理。
2. **历史 Safari localhost 跳转的原始触发条件未完全复现。**当次导航链未保存，具体触发点无法确定。当前生产 `public_base_url` 拒绝 localhost、127.0.0.1、::1 等地址，手机后续导航使用相对 URL；URL 自动化回归和真实 Camera/Safari、Code Scanner 转 Safari 验收均已通过。不将未复原的历史触发点写成已确定根因。

两项问题不会削弱 Collector 的权限边界，也不阻塞已完成的 Phase 5 验收。完整真机证据及行为说明见 [最终验收报告](PHASE-5-ACCEPTANCE.md)、[配对修复报告](PHASE-5-REPORT.md) 和 [Collector 说明](COLLECTOR.md)。

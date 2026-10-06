# 自动化测试与真实验收

## 一键运行

有 Docker Desktop 后，在项目目录用 PowerShell 执行：

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Test-Project.ps1

脚本运行前端逻辑回归、后端单元测试、真实 PostgreSQL 接口集成测试。后端测试采用模型流测试替身，不调用真实模型，不消耗额度。测试用单独的 interview_test 数据库和 Compose 项目；普通应用数据库、账号业务数据、真实模型密钥不参与测试。

也可以分别执行：

    docker compose -f compose.test.yaml run --rm frontend-test
    docker compose -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from test test
    docker compose -f compose.test.yaml down

如果本机已有 Node.js，可直接执行 node tests/frontend.cjs。

如果出现 `failed to resolve reference docker.io/...`、`registry-1.docker.io:443` 超时或 `Docker Desktop has no HTTPS proxy`，表示镜像下载失败，测试容器尚未启动，不能把它记为测试不通过。先确认 Docker Desktop 的网络/代理设置，单独重试 `docker pull node:22-alpine`；下载成功后再运行一键脚本。后续 Python 和 PostgreSQL 镜像也需要可访问的镜像仓库。

若已切换网络仍无法访问 Docker Hub，可明确选择可访问且可信任的镜像站。项目支持临时设置 `IMAGE_REGISTRY`，同时替换测试和应用所用的 Node、Python、PostgreSQL、Nginx 镜像来源，不修改默认来源。例如在同一个 PowerShell 窗口中执行 `$env:IMAGE_REGISTRY='docker.m.daocloud.io/library'`，先运行 `docker pull docker.m.daocloud.io/library/node:22-alpine` 验证镜像站可用，再运行上述一键脚本。这个镜像站是第三方服务；若拉取仍失败，应保留错误并检查网络或使用自己信任的可访问仓库，不要把镜像未下载当成测试通过。

GitHub Actions 配置会在仓库推送或 PR 时运行同一批检查。当前文件仅提供工作流配置；没有创建远程仓库，也没有已通过的 CI 运行记录。

## 覆盖范围

前端回归覆盖流解析、跨网络分片的 CRLF 事件边界、结束事件即时释放页面、分段播报与停止、录音最终结果保留、识别失败与拒绝权限、旧回调失效、退出清理、账号隔离、过期页面请求、首字时钟与页面可见性、离开页面后旧计时回调失效、重复创建、重复确认、取消生成和完成状态。

后端单元测试覆盖密码校验、百分位数与完整性、95% 成功率边界、确认文字进入追问上下文、非有限延迟值验证、流式“正在思考”占位内容不计首字、真实 SSE 协议解析规则及断流失败。

数据库集成测试覆盖三轮状态机、并发重复确认、不同答案冲突、跨用户读取/生成/提交/指标写入拒绝、模型失败重试、缺少模型密钥时不生成虚假样本、并发生成去重、过期轮次拒绝、空回答、认证、越界延迟值拒绝、指标首写保留、CSV 导出、应用重启后数据恢复及中断生成清理。

## 边界

这些自动化测试中的问题与语音回调都是明确标识的测试替身，不能充当真实对话模型、麦克风、语音合成或延迟达标证据。

数据库重启测试验证应用连接关闭、重新初始化后的业务恢复；还须另按 README 在保留数据卷的情况下重新创建 Docker 容器，完成真实部署验收。

真实语音与模型验收仍按 validation.md 操作，并保留原始 CSV 和录像。当前执行结果见 frontend-test-results.json 和 delivery-status.md。

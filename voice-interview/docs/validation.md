# 验收记录

## 环境与证据来源

- 日期：2026-10-06，Asia/Shanghai。Windows 桌面、Docker Desktop、Edge 154；原始 browser_info 同时保留 Chrome 154 标识，不宣称整批浏览器标识一致。
- Docker CLI 29.8.2、Compose v5.5.1。模型为百炼北京地域 qwen-plus，temperature=0.5、max_tokens=200。密钥只在服务端本机 `.env`。
- ASR 使用浏览器 SpeechRecognition/webkitSpeechRecognition，TTS 使用 speechSynthesis。用户确认麦克风识别和录屏可用。
- 两组虚构输入来自 `frontend/samples.json`，岗位描述与经历摘要合计 546 和 580 字符。
- 正式原始 CSV：`evidence/formal-bob-20261006.csv`；官方字段对应表：`evidence/requests-bob-20261006.csv`；调试失败：`evidence/debug-alice-20261006.csv`。
- 录像：用户提供的 `C:\Users\asus\Videos\Captures\面试练习室 · 语音模拟面试 和另外 3 个页面 - 个人 - Microsoft​ Edge 2026-10-06 16-43-30.mp4`，7 分 29 秒、476878174 字节。用户确认录屏检查完成；Agent 无独立视频播放能力，未虚构时间点。视频未放入源码 ZIP。

## 场景 1：三轮语音与追问

用户完成 demo-bob 的 7 场三轮面试，确认语音识别可用并提供录屏。后端复核 7 场均为 complete，21 组问题和回答均非空；此前 4 场第二题均正确引用保存回答片段。用户确认录像检查完成。

## 场景 2：保存、重建与幂等

用户执行 `docker compose up -d --force-recreate` 后提供截图，7 场历史仍显示。后端读取正式 CSV 的 7 个 interview_id，7 场状态均为 complete，21 组问答仍可读取。重复确认和进度获取由已通过的数据库集成测试覆盖；重建操作依据用户反馈，Agent 无 Docker 引擎权限。详见 `evidence/persistence-20261006.md`。

## 场景 3：账号隔离与模型失败恢复

alice 读取 bob 的一场记录返回 HTTP 404。此前 OpenAI 连接失败和切换百炼后恢复调用的完整记录保留在 alice CSV。26 项后端/数据库测试覆盖模型失败后保留确认回答、重试统计、未登录访问、错误轮次及重复提交。麦克风拒绝权限等浏览器异常未单独人工测试，列为限制。

## 正式性能结果

正式 demo-bob 批次包含 21 个不同请求、7 场三轮面试，全部成功，0 失败、0 重试、0 浏览器错误。此前 12 条记录全部保留，未重复计数或挑选样本；时间范围为北京时间 16:16:44 至 16:46:26。

| 指标 | 实测 | 门槛 |
|---|---:|---:|
| 成功率 | 100%（21/21） | ≥95% |
| 模型 TTFT P50 | 609.261 ms | ≤2000 ms |
| 模型 TTFT P95 | 961.475 ms | ≤5000 ms |
| 页面首字 P95 | 1037.200 ms | ≤6000 ms |
| 首段语音 P95 | 1318.300 ms | ≤8000 ms |

以上分位数按 nearest-rank 独立复算。官方 Python 汇总脚本尚未执行，不声明官方脚本已通过；具体核对见 `evidence/review-20261006-final-batch.md`。

## 自动化与限制

用户 Docker 环境通过 25 项前端逻辑检查、16 项后端单元测试和 10 项 PostgreSQL 集成测试。模型和浏览器测试替身不替代真实测量。源码 ZIP 不含 `.env`；录像需另附可访问位置。仓库 URL、最终 SHA 和材料回执尚未完成，本文不是交卷凭证。
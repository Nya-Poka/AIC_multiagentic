# Research Mesh MVP

面向“智能体互联”赛道的科研协作最小闭环。当前版本使用官方 `acps-sdk 2.2.0`
的 AIP Direct RPC，完成：

```text
研究请求
  -> 本地能力发现
  -> 文献 / 实验 / 分析 Partner 并行执行
  -> 规范复核 Partner
  -> 带 AIP 任务轨迹的结构化研究报告
```

## 当前完成范围

- 1 个科研 Leader 服务（端口 `8000`）；
- 4 个独立进程、独立任务存储的 Partner 服务（端口 `8011`～`8014`）；
- 官方 `TaskCommand` / `TaskResult` / `TaskState` / `AipRpcClient`；
- `start -> awaiting-completion -> complete -> completed` 状态闭环；
- 缺少必要输入时进入 `awaiting-input`；
- 按技能动态选择 Partner；
- 并行执行前三个研究步骤；
- 文献 Agent 通过 Crossref 官方 REST API 检索真实书目数据；
- 结构化产物、规范复核与 provenance 轨迹；
- FastAPI 接口和自动化测试。

当前能力发现是明确标注的本地开发适配器，不等同于官方 ADP。身份绑定也仅在本地
HTTP测试中关闭。接入梧桐平台时需要替换为官方 Registry/Discovery、平台分配的 AIC、
CAI 证书和 mTLS。

## 环境

- Python 3.11～3.13；
- Windows PowerShell；
- 安装过程会从官方仓库的固定提交安装 `acps-sdk`。

```powershell
.\scripts\bootstrap.ps1
```

如果已经有可用的官方 SDK 本地副本，可以先安装 SDK，再安装本项目时使用
`--no-deps`。

## 运行闭环演示

```powershell
.\scripts\run-demo.ps1
```

输出包含四个 Partner 的结果和每次 AIP 调用的任务ID、最终状态及耗时。

## 真实文献检索

文献 Agent 默认使用 [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
的 `/works` 接口，以 `literature_query`（未提供时使用 `question`）检索真实论文元数据。
Crossref 官方公开接口不要求注册或 API Key。建议在环境变量中填写联系邮箱，以使用
polite pool：

```powershell
$env:CROSSREF_MAILTO = 'team@example.com'
```

返回结果包含 DOI、作者、年份、期刊或会议、引用数、Crossref 相关度、数据源、检索时间和
摘要可用状态。没有摘要时会明确标记，不会生成论文内容。系统会缓存成功结果 15 分钟；
网络失败时保留用户提供的种子文献，并把 provider 状态标记为 `unavailable`。
`literature_query` 会发送给 Crossref；不要在检索词中放入未公开数据或个人敏感信息。

请求中可控制查询和结果数量：

```json
{
  "question": "学习时间是否与测验成绩有关？",
  "objective": "查找相关研究并形成实验方案。",
  "literature_query": "study time academic performance test scores",
  "max_literature_results": 5,
  "documents": []
}
```

## 运行五个独立服务

分别打开五个 PowerShell 终端：

```powershell
.\scripts\run-partner.ps1 -Agent literature
.\scripts\run-partner.ps1 -Agent experiment
.\scripts\run-partner.ps1 -Agent analysis
.\scripts\run-partner.ps1 -Agent review
.\scripts\run-api.ps1
```

服务映射：

| 服务 | 端口 | 对外接口 |
| --- | ---: | --- |
| Leader | 8000 | `/research/run`、`/dev/agents` |
| Literature Partner | 8011 | `/rpc`、`/acs`、`/health` |
| Experiment Partner | 8012 | `/rpc`、`/acs`、`/health` |
| Analysis Partner | 8013 | `/rpc`、`/acs`、`/health` |
| Review Partner | 8014 | `/rpc`、`/acs`、`/health` |

常用入口：

- `GET http://127.0.0.1:8000/health`
- `GET http://127.0.0.1:8000/dev/agents`
- `POST http://127.0.0.1:8000/research/run`
- `GET http://127.0.0.1:8000/docs`
- `GET http://127.0.0.1:8011/acs`（其余 Partner 同理）

演示请求可参考 `src/research_mesh/sample_data.py`。

Partner 地址可通过 `.env.example` 中的环境变量覆盖，便于后续改为容器地址或
梧桐平台服务地址。

## 测试

```powershell
.\scripts\test.ps1
.\scripts\smoke-literature-live.ps1
.\scripts\smoke-independent.ps1
```

第一条运行无网络依赖的单元与内存 HTTP 集成测试；第二条真实访问 Crossref 并要求至少
返回一条文献；第三条启动五个操作系统进程，验证健康检查、ACS、跨进程 AIP 调用和完整
四 Agent 闭环，结束后会自动清理进程。

## 下一步

1. 用当前四份本地 ACS 申请平台 AIC，并补齐正式 provider、安全方案和公网端点；
2. 用官方 ADP `discovery-server` 替换 `LocalCapabilityRegistry`；
3. 配置 CAI 证书、mTLS 和身份绑定；
4. 增加全文获取、第二文献源交叉核验与受限代码执行沙箱；
5. 增加故障重发现、基线实验和梧桐平台访问证据。

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
- 1 个统一 LLM Gateway 服务（端口 `8020`）；
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

## 统一 LLM Gateway

所有 Agent 可通过 `LLMGatewayClient` 使用同一套请求和响应模型，不直接依赖具体云厂商
SDK。Gateway 当前适配 OpenAI-compatible `/v1/chat/completions`，因此可连接支持该协议的
云模型服务或本地 Ollama。统一接口包括：

- `POST /v1/complete`：非流式文本或 JSON completion；
- `GET /v1/models`：查看当前允许的模型；
- `GET /health`：查看启用、禁用或配置错误状态；
- 统一返回 provider、model、content、finish reason、token 用量、延迟和请求 ID。

默认配置为 `disabled`，不会调用任何模型，也不需要 API Key。使用本地 Ollama 的配置示例：

```powershell
$env:RESEARCH_MESH_LLM_PROVIDER = 'openai-compatible'
$env:RESEARCH_MESH_LLM_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:RESEARCH_MESH_LLM_MODEL = '你的本地模型名称'
.\scripts\run-llm.ps1
```

使用云服务时，再通过环境变量提供密钥：

```powershell
$env:RESEARCH_MESH_LLM_PROVIDER = 'openai-compatible'
$env:RESEARCH_MESH_LLM_BASE_URL = 'https://服务商地址/v1'
$env:RESEARCH_MESH_LLM_API_KEY = '仅保存在本机环境中的密钥'
$env:RESEARCH_MESH_LLM_MODEL = '服务商模型ID'
$env:RESEARCH_MESH_LLM_GATEWAY_TOKEN = '内部网关的强随机令牌'
.\scripts\run-llm.ps1
```

密钥只保存在 Gateway 进程中，不出现在健康检查、统一响应或 Git 仓库中。默认不允许请求
临时切换模型，并限制最大输出 token。部署到非本机网络时必须设置 Gateway Token，并在
入口增加 TLS 和访问控制。Prompt 会发送给所配置的上游服务，不应包含无权外发的数据。

## 运行六个独立服务

分别打开六个 PowerShell 终端：

```powershell
.\scripts\run-partner.ps1 -Agent literature
.\scripts\run-partner.ps1 -Agent experiment
.\scripts\run-partner.ps1 -Agent analysis
.\scripts\run-partner.ps1 -Agent review
.\scripts\run-llm.ps1
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
| LLM Gateway | 8020 | `/v1/complete`、`/v1/models`、`/health` |

常用入口：

- `GET http://127.0.0.1:8000/health`
- `GET http://127.0.0.1:8000/dev/agents`
- `POST http://127.0.0.1:8000/research/run`
- `GET http://127.0.0.1:8000/docs`
- `GET http://127.0.0.1:8011/acs`（其余 Partner 同理）
- `GET http://127.0.0.1:8020/health`

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
返回一条文献；第三条启动六个操作系统进程，验证健康检查、ACS、LLM Gateway、跨进程
AIP 调用和完整四 Agent 闭环，结束后会自动清理进程。

## 下一步

1. 用当前四份本地 ACS 申请平台 AIC，并补齐正式 provider、安全方案和公网端点；
2. 用官方 ADP `discovery-server` 替换 `LocalCapabilityRegistry`；
3. 配置 CAI 证书、mTLS 和身份绑定；
4. 让实验设计和报告综合 Agent 按任务策略调用统一 LLM Gateway；
5. 增加全文获取、第二文献源交叉核验与受限代码执行沙箱；
6. 增加故障重发现、基线实验和梧桐平台访问证据。

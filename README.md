# 基于多智能体协作的一站式科研助理平台

面向“智能体互联”赛道的科研协作最小闭环。当前版本使用官方 `acps-sdk 2.2.0`
的 AIP Direct RPC，完成：

```text
研究请求
  -> 本地能力发现或梧桐 ADP 在线发现
  -> 文献 / 实验 Partner 并行执行
  -> 证据分析 Partner 审计检索结果
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
- 并行执行文献检索与实验设计，随后执行证据分析和规范复核；
- Leader 输入只包含研究问题、目标、检索范围和约束，不再要求手填数值样本；
- 文献 Agent 并行调用 Crossref、OpenAlex、Semantic Scholar，执行去重和多源核验；
- 可选通过统一 LLM Gateway 调用 DeepSeek 扩展英文检索词；
- 结构化产物、规范复核与 provenance 轨迹；
- FastAPI 接口和自动化测试。
- Leader 本身可通过 `/rpc` 被其他智能体按 AIP 调用；
- 梧桐平台模式、五套独立 AIC、mTLS、身份绑定和 AMP 日志；
- 五份 ACS v02.02 生成器与平台就绪检查。

本地模式使用确定性注册表并关闭身份绑定；平台模式会调用官方 ADP `discover`，使用
ACPs CA 签发证书执行 mTLS，并强制绑定 peer certificate AIC 与 AIP `senderId`。平台账号、
人工审核、正式 AIC、证书签发和公网域名仍必须由参赛者本人取得。

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

## 简洁 Web 前端

一条命令启动 Leader、四个 Partner 和 LLM Gateway，然后打开
`http://127.0.0.1:8000/`：

```powershell
.\scripts\run-local.ps1
```

按 `Ctrl+C` 会统一关闭全部六个服务。

页面提供一条清晰的科研协作闭环：

- 填写研究问题、目标、文献检索词和约束，运行完整四 Agent 闭环；
- 展示每个外部文献源的可用状态、去重证据、摘要、DOI 和开放获取链接；
- 展示证据来源、摘要、DOI、开放获取和发表年代覆盖率；
- 前端不接收、保存或传输模型 API Key，模型配置只存在于服务器环境变量中。

## 真实文献检索

文献 Agent 默认并行使用以下公开学术接口，以 `literature_query`（未提供时使用
`question`）检索真实论文元数据：

- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)；
- [OpenAlex API](https://docs.openalex.org/)；
- [Semantic Scholar Academic Graph API](https://api.semanticscholar.org/api-docs/)。

配置示例：

```powershell
$env:RESEARCH_MESH_LITERATURE_PROVIDERS = 'crossref,openalex,semantic_scholar'
$env:CROSSREF_MAILTO = 'team@example.com'
$env:OPENALEX_MAILTO = 'team@example.com'
# 可选：申请后再填写，提高相应数据源的调用额度
$env:OPENALEX_API_KEY = 'server-side-key'
$env:SEMANTIC_SCHOLAR_API_KEY = 'server-side-key'
```

返回结果包含 DOI、作者、年份、摘要、期刊或会议、引用数、开放获取位置、命中数据源和
检索时间。系统先剥离叮当路由、DAG 摘要等平台包装，再把研究问题拆成人群、暴露/干预、
结果和测量维度；按 DOI 或规范化标题去重，并结合 Reciprocal Rank Fusion、概念覆盖和
关键词覆盖进行主题相关性重排。候选池默认是最终结果数的 5 倍，相关证据不足时使用干净的
同义检索式自动重试。仍未达到质量门禁时，Review 会拒绝形成确定性报告，而不是用无关文献
补足数量。单个数据源失败不会阻断其他来源；全部失败时保留用户提供的种子文献。成功结果
默认缓存 15 分钟。`literature_query` 会发送给所启用的数据源，不要在检索词中放入未公开
数据或个人敏感信息。

质量控制参数：

```powershell
$env:RESEARCH_MESH_LITERATURE_FETCH_MULTIPLIER = '5'
$env:RESEARCH_MESH_LITERATURE_MIN_RELEVANCE = '0.35'
$env:RESEARCH_MESH_LITERATURE_MIN_RELEVANT_RATIO = '0.6'
$env:RESEARCH_MESH_LITERATURE_AUTO_RETRY = 'true'
```

文献产物同时记录结构化研究意图、实际查询、候选数、每条证据的相关性分数与命中概念、
被过滤记录及原因、自动重试查询和最终质量门禁结论，便于复核检索过程。

如果 LLM Gateway 已连接 DeepSeek，可启用检索词扩展：

```powershell
$env:RESEARCH_MESH_LITERATURE_QUERY_EXPANSION = 'true'
$env:RESEARCH_MESH_LITERATURE_MAX_QUERIES = '3'
$env:RESEARCH_MESH_LLM_GATEWAY_URL = 'http://127.0.0.1:8020'
```

文献 Partner 会让 DeepSeek 生成少量互补英文检索式，再由服务端白名单数据源执行请求。
LLM 或 Gateway 不可用时自动回退原始检索词，不影响基础检索。

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

DeepSeek 配置示例：`RESEARCH_MESH_LLM_BASE_URL=https://api.deepseek.com`，模型 ID 使用
账号实际可用的值，例如 `deepseek-flash`。如果 Gateway 返回 transport failed，应先检查
运行 Gateway 的服务器能否访问公网、DNS、系统代理和防火墙。官方调用示例见
[DeepSeek First API Call](https://api-docs.deepseek.com/)。

### Leader 输入标准化

Leader 的 AIP 入口会把以下输入统一转换为 `ResearchRequest`：

- AIP `StructuredDataItem` 中的标准对象；
- 严格 JSON，以及带 `request`、`input`、`payload` 或 `data` 包装的对象；
- Markdown 代码块、说明文字中嵌入的 JSON；
- 单引号、尾随逗号、常见中文字段名等宽松 JSON；
- 普通自然语言科研问题。

统一结果固定包含 `question`、`objective`、`literature_query`、
`max_literature_results`、`documents` 和 `constraints`。配置了统一 LLM Provider 时，
自然语言默认先由 LLM Gateway 提取研究目标与约束；Gateway 不可用时会自动回退本地
确定性转换，不阻断 AIP 调用。可显式关闭 LLM 标准化：

```powershell
$env:RESEARCH_MESH_INPUT_NORMALIZER_USE_LLM = 'false'
```

Leader 的 AIP 结果同时包含 `text/plain` 正文和 `application/json` 结构化报告：叮当等
交互式调用方可以直接展示可读报告，程序调用方仍可读取完整字段和可追溯记录。

输入转换只生成请求结构，不会编造文献、引文或实验数据。原始输入及标准化结果仍应按
科研数据安全要求处理，不要向未获授权的模型服务发送敏感数据。

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
| Leader | 8000 | `/rpc`、`/acs`、`/research/run`、`/dev/agents` |
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

## 梧桐平台接入

代码已包含 Registry/CA 外的所有运行时接入组件。完整的 ACS 生成、注册审核、AIC 同步、
clientAuth/serverAuth 证书签发、平台模式配置、启动和排障命令见：

- [梧桐 ACPs 接入手册](docs/WUTONG_INTEGRATION.md)
- [ACPs 部署资产说明](deploy/acps/README.md)

生成五份待注册 ACS 并检查平台配置：

```powershell
.\scripts\generate-acps.ps1 -BaseUrl 'https://agents.example.edu.cn'
.\scripts\check-platform.ps1
```

预检在账号尚未审核、AIC 未同步或证书未签发时失败是预期行为；输出会逐项告诉你缺什么。

## 测试

```powershell
.\scripts\test.ps1
.\scripts\smoke-literature-live.ps1
.\scripts\smoke-independent.ps1
```

第一条运行无网络依赖的单元与内存 HTTP 集成测试；第二条真实访问已启用的学术数据源并
要求至少返回一条文献；第三条启动六个操作系统进程，验证健康检查、ACS、LLM Gateway、跨进程
AIP 调用和完整四 Agent 闭环，结束后会自动清理进程。

## 仍可扩展的能力

1. 让实验设计和报告综合 Agent 按任务策略调用统一 LLM Gateway；
2. 增加开放全文安全抓取、PDF 分段解析与受限代码执行沙箱；
3. 增加 ADP 多候选故障重发现、基线实验和比赛演示证据自动归档；
4. 按平台运维约定把本地 AMP NDJSON 接入 Fluent Bit/Kafka/Monitor。

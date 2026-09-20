# Research Mesh 接入梧桐 ACPs

本文针对当前代码的 Leader + 4 Partner 架构。平台账号、人工审核、AIC 分配、域名所有权和证书签发不能由代码替你完成；其余协议接入已落到项目中。

## 1. 已在代码中完成

- Leader 和四个 Partner 都提供 ACPs AIP JSON-RPC `POST /rpc`；
- 五个 Agent 各自读取独立 AIC 和 serverAuth 证书；
- Leader 使用 clientAuth 证书访问 ADP Discovery 与 Partner；
- 平台模式默认开启 mTLS peer certificate 与 AIP `senderId` 身份绑定；
- Leader 动态调用 `POST {discovery}/discover`，支持最多五次 307 转发；
- AIP 出站访问日志与五个进程心跳写入 AMP NDJSON；
- ACS v02.02 生成器、平台配置预检和 fail-closed 启动检查；
- 本地模式仍可运行，无需 AIC、证书或 API Key。

## 2. 你必须准备的外部条件

1. 在 [梧桐 Registry](https://wt.ioa.pub/registry/) 完成账号登录；若页面要求验证码，必须由你手工填写。
2. 准备一个公网可达且归你或学校所有的域名，以及对应 ICP/WHOIS 登记信息。
3. 在防火墙放行五个 HTTPS 端口：Leader `8000`、Literature `8011`、Experiment `8012`、Analysis `8013`、Review `8014`。也可给五个服务分配不同域名并在 `.env` 中逐项填写 URL。
4. 等待平台管理员审批五个 Agent。审批和 AIC 分配无法在本地绕过。
5. 确认平台提供 Monitor/AMP Forwarder 的部署约定；本项目会写标准 NDJSON，但把文件转发至平台 Kafka/Monitor 通常由平台运维完成。

## 3. 生成五份 ACS

复制配置并填写真实组织、联系人和域名信息：

```powershell
Copy-Item .env.example .env
notepad .env
```

首次注册前，AIC 可以保持 `local.*`；生成器会按官方草稿格式把这些占位值写成空字符串，待 Registry 分配。默认使用“同一域名、五个直连端口”的方式：

```powershell
.\scripts\generate-acps.ps1 -BaseUrl 'https://agents.example.edu.cn'
```

输出位于 `deploy/acps/generated/`。逐份检查 `provider`、`endPoints`、`certificate.altNames`、能力边界和示例，不要直接提交占位信息。

## 4. 配置并登录 acps-cli

官方仓库当前将 CLI 作为 `acps-cli` 子项目提供。若电脑尚未安装，可在项目外另建工具目录（不要把它或 token 提交到本仓库）：

```powershell
git clone --branch v2.2.0 --depth 1 https://github.com/AIP-PUB/ACPs-community.git C:\tools\ACPs-community
Set-Location C:\tools\ACPs-community\acps-cli
uv sync
uv run acps-cli --help
Set-Location C:\Users\MSI-NB\Documents\ChatGPT\AIC
```

如果使用上面的源码环境，把后续命令开头的 `acps-cli` 替换为 `uv run --project C:\tools\ACPs-community\acps-cli acps-cli`；若已经把 CLI 安装到 PATH，则可直接使用文中的命令。

```powershell
Copy-Item deploy/acps/acps-cli.toml.example acps-cli.toml
acps-cli --config .\acps-cli.toml auth login
acps-cli --config .\acps-cli.toml auth whoami --json
```

CLI 若采用账号密码模式，会安全提示输入；若平台启用 OIDC Device 登录，则会显示浏览器地址和一次性代码。不要把密码写进命令历史，也不要把 `.acps-cli/tokens/` 提交到 Git。

## 5. 注册、提交、同步 AIC

对每一份 ACS 执行 `save`。命令输出会包含该草稿的 `agent-id`：

```powershell
acps-cli --config .\acps-cli.toml agent save --acs-file .\deploy\acps\generated\leader.acs.json --json
acps-cli --config .\acps-cli.toml agent submit --agent-id '<LEADER_AGENT_UUID>' --json
```

对 `literature`、`experiment`、`analysis`、`review` 重复上述两条命令。平台审批通过后，分别同步；`sync` 的方向是“服务端最新 ACS → 本地文件”，不是上传：

```powershell
acps-cli --config .\acps-cli.toml agent sync --acs-file .\deploy\acps\generated\leader.acs.json --json
```

把五份同步后 ACS 中的正式 `aic` 填入 `.env` 的五个 `RESEARCH_MESH_*_AIC`。今后修改 ACS 的正确顺序是 `agent save` → 必要时 `agent submit` → 审批后 `agent sync`。

## 6. 为五个 Agent 签证书

每个 Agent 先获取 EAB。Leader 需要一份 clientAuth 和一份 serverAuth；四个 Partner 至少各需一份 serverAuth。证书用途不能合并：

```powershell
$Aic = '<正式 LEADER AIC>'
acps-cli --config .\acps-cli.toml cert eab fetch --aic $Aic --output .\secrets\leader-eab.json --json

acps-cli --config .\acps-cli.toml cert issue -a $Aic -u clientAuth `
  --eab-file .\secrets\leader-eab.json `
  --key-path .\secrets\leader-client.key `
  --cert-path .\secrets\leader-client.pem `
  --trust-bundle-path .\secrets\trust-bundle.pem

acps-cli --config .\acps-cli.toml cert issue -a $Aic -u serverAuth `
  --eab-file .\secrets\leader-eab.json `
  --key-path .\secrets\leader-server.key `
  --cert-path .\secrets\leader-server.pem `
  --trust-bundle-path .\secrets\trust-bundle.pem
```

对四个 Partner 重复 EAB 与 serverAuth 签发，并把所有路径填入 `.env`。私钥、EAB、token 与证书目录已被 `.gitignore` 排除；仍应限制本机文件权限。

## 7. 切换到平台模式

在 `.env` 中至少修改：

```dotenv
RESEARCH_MESH_MODE=platform
RESEARCH_MESH_HOST=0.0.0.0
RESEARCH_MESH_IDENTITY_BINDING=true
RESEARCH_MESH_MTLS_ENABLED=true
RESEARCH_MESH_DISCOVERY_URL=https://wt.ioa.pub/discovery
RESEARCH_MESH_DISCOVERY_FALLBACK_LOCAL=false
RESEARCH_MESH_AMP_ENABLED=true
```

五个 `*_URL` 必须改为与 ACS 一致的公网 HTTPS URL。若使用直连端口，应类似：

```dotenv
RESEARCH_MESH_LEADER_URL=https://agents.example.edu.cn:8000/rpc
RESEARCH_MESH_LITERATURE_URL=https://agents.example.edu.cn:8011/rpc
RESEARCH_MESH_EXPERIMENT_URL=https://agents.example.edu.cn:8012/rpc
RESEARCH_MESH_ANALYSIS_URL=https://agents.example.edu.cn:8013/rpc
RESEARCH_MESH_REVIEW_URL=https://agents.example.edu.cn:8014/rpc
```

运行预检：

```powershell
.\scripts\check-platform.ps1
```

只有输出 `Platform readiness check passed` 才启动服务。平台模式若缺正式 AIC、HTTPS URL、Discovery 或任何证书文件，会拒绝启动，不会静默降级。

## 8. 启动与验证

在五个终端中启动四个 Partner 和 Leader：

```powershell
.\scripts\run-partner.ps1 -Agent literature
.\scripts\run-partner.ps1 -Agent experiment
.\scripts\run-partner.ps1 -Agent analysis
.\scripts\run-partner.ps1 -Agent review
.\scripts\run-api.ps1
```

用 CLI 验证 Registry/Discovery：

```powershell
acps-cli --config .\acps-cli.toml agent list --json
acps-cli --config .\acps-cli.toml discover status
acps-cli --config .\acps-cli.toml discover query '可追溯文献检索' --limit 5
```

本机 AMP 文件应持续增长：`artifacts/amp/*-heartbeat.ndjson`，完成一次闭环后 Leader 的 `*-access.ndjson` 也应有记录。梧桐公开入口没有给出统一 Monitor URL；先向赛事方取得地址，并在 `acps-cli.toml` 增加 `[monitor]` 后再验证：

```powershell
acps-cli --config .\acps-cli.toml monitor status
acps-cli --config .\acps-cli.toml monitor heartbeat liveness '<AIC>'
```

## 9. 常见失败定位

- `formal AIC`：仍在使用 `local.*`；等审批后 `agent sync` 并回填 `.env`。
- `TLS material is missing`：证书路径为空、拼错或文件未复制到部署机。
- TLS hostname mismatch：ACS 的 `certificate.altNames` 与公网 URL 主机名不一致，需要更新 ACS 并重新签发 serverAuth 证书。
- AIP identity mismatch：消息 `senderId` 与 peer certificate 中的 AIC 不一致；检查是否拿错了 Agent 证书。
- ADP request failed：先用 `discover status/query` 验证账号、网络、mTLS 和 Discovery 地址；生产验收时保持 fallback 为 `false`。
- AMP 本地有日志但 Monitor 查不到：代码侧 emitter 已工作，检查 Fluent Bit/Forwarder 的采集目录、Kafka topic 和平台 Monitor 权限。
- 经 Nginx/网关后全部身份校验失败：TLS 在代理层被终止，应用拿不到 peer certificate。改用直连端口/TCP passthrough，或实现经过安全评审的代理证书身份传递扩展。

官方资料：[梧桐文档入口](https://wt.ioa.pub/docs.html)、[ACPs SDK 2.2.0](https://github.com/AIP-PUB/ACPs-community/blob/v2.2.0/acps-sdk/README.md)、[AIP SDK 教程](https://github.com/AIP-PUB/ACPs-community/blob/v2.2.0/acps-docs/tutorials/aip-sdk-tutorial.md)、[ADP 规范](https://github.com/AIP-PUB/ACPs-community/blob/v2.2.0/acps-specs/06-ACPs-spec-ADP/ACPs-spec-ADP.md)、[AMP 规范](https://github.com/AIP-PUB/ACPs-community/blob/v2.2.0/acps-specs/09-ACPs-spec-AMP/ACPs-spec-AMP.md)。

# v0.7.0 高级能力与升级说明

本版本在 v0.6.0 五智能体可信闭环之上实现四组能力：批量评测与证据归档、真实科研数据分析、开放全文证据与证据综合、ADP 多候选容错与持久化观测。

## 1. 兼容策略

本地模式默认启用 Dataset 和 Synthesis Partner。平台模式默认关闭这两个新 Partner，避免服务器拉取新代码后因尚未注册新 AIC 而破坏既有 v0.6.0 闭环：

```dotenv
RESEARCH_MESH_EVIDENCE_SYNTHESIS_ENABLED=false
RESEARCH_MESH_DATASET_ANALYSIS_ENABLED=false
```

完成新 Partner 的 Registry 审核、AIC 同步、证书签发和服务部署后，再将它们改为 `true`。开放全文抓取在任何模式都默认关闭。

## 2. 批量评测与比赛证据

启动全部本地服务后运行：

```powershell
.\.venv\Scripts\python.exe scripts\run-benchmark.py
```

默认读取 `benchmarks/cases.jsonl`，生成：

```text
artifacts/evaluations/<UTC时间>-<Git SHA>/
  health.json
  leader-acs.json
  cases.jsonl
  results.json
  summary.json
  latency.csv
  report.html
```

公网环境可添加基本认证和 mTLS 参数：

```bash
python scripts/run-benchmark.py \
  --base-url https://43.132.150.94 \
  --username "$BENCHMARK_USER" \
  --password "$BENCHMARK_PASSWORD" \
  --ca-file /path/trust-bundle.pem \
  --cert-file /path/leader-client.pem \
  --key-file /path/leader-client.key
```

服务器端脱敏证据包：

```bash
python scripts/collect-platform-evidence.py \
  --base-url https://43.132.150.94:8000 \
  --endpoint literature=https://43.132.150.94:8011 \
  --endpoint experiment=https://43.132.150.94:8012 \
  --endpoint analysis=https://43.132.150.94:8013 \
  --endpoint review=https://43.132.150.94:8014 \
  --ca-file /path/trust-bundle.pem \
  --cert-file /path/leader-client.pem \
  --key-file /path/leader-client.key \
  --amp-dir /var/lib/research-mesh/amp
```

脚本保存 health、ACS、Git SHA、服务状态、脱敏 journal、AMP 和 SHA-256 清单，但不会复制证书、私钥、`.env`、EAB 或 token。生成后仍应人工检查匿名化效果。

## 3. 科研数据分析

### 3.1 上传

浏览器可直接选择 CSV、JSON 或 XLSX。程序调用方使用原始请求体：

```http
POST /artifacts/datasets
Content-Type: text/csv
X-Filename: sleep.csv

sleep_hours,exam_score
6,70
7,76
```

响应是带哈希的引用，不返回服务器路径：

```json
{
  "artifact_id": "dataset-...",
  "sha256": "...",
  "filename": "sleep.csv",
  "media_type": "text/csv",
  "size_bytes": 42
}
```

### 3.2 研究请求

```json
{
  "question": "睡眠时长是否与测验成绩相关？",
  "objective": "结合文献和上传数据生成可复核研究报告。",
  "documents": [],
  "constraints": ["不得声称因果关系"],
  "dataset": {
    "artifact_id": "dataset-...",
    "sha256": "...",
    "filename": "sleep.csv",
    "media_type": "text/csv",
    "size_bytes": 42
  },
  "analysis_spec": {
    "outcome": "exam_score",
    "exposures": ["sleep_hours"],
    "covariates": [],
    "design": "observational"
  }
}
```

Dataset Partner 会重新检查引用、文件大小和 SHA-256，输出字段类型、缺失率、数值分布，以及具有足够完整样本时的 Pearson 相关系数和 Fisher-z 95% 区间。当前版本明确不执行协变量调整，保留协变量仅供 Review 和后续模型使用。

安全边界：

- 最大文件默认 10 MiB，硬上限 25 MiB；
- 最大 100000 行；
- 只接受 CSV、JSON、XLSX；
- 不执行用户代码或公式宏；
- 不向 LLM Gateway 发送原始行；
- 报告不返回原始数据预览；
- 数据引用与最终报告保留输入哈希。

## 4. 开放全文与证据综合

启用：

```dotenv
RESEARCH_MESH_FULLTEXT_ENABLED=true
RESEARCH_MESH_FULLTEXT_MAX_DOCUMENTS=3
RESEARCH_MESH_FULLTEXT_MAX_BYTES=8388608
RESEARCH_MESH_FULLTEXT_SEGMENTS_PER_DOCUMENT=3
RESEARCH_MESH_FULLTEXT_ALLOWED_HOSTS=pmc.ncbi.nlm.nih.gov,arxiv.org
```

系统只处理检索结果中已有的开放获取 HTTPS 地址，并执行：

- 凭据、协议和域名白名单检查；
- 每次重定向重新检查目标；
- DNS 解析后拒绝私网、回环、链路本地和保留地址；
- `Content-Length` 与实际流式字节双重大小限制；
- HTML/XML/text 解析和可选 PDF 解析；
- 文档内容和证据片段 SHA-256；
- 与研究问题重合度排序。

Synthesis Partner 读取书目、摘要和全文片段，生成证据矩阵、证据层级、类型/质量分布和范围性确定性提示。确定性引擎不会从摘要自动推断效应方向，也不把输出描述为正式 GRADE 评价。

## 5. 多候选路由与熔断

ADP 返回多个同技能候选时，Leader 依次调用最多三个候选：

```dotenv
RESEARCH_MESH_ROUTING_MAX_CANDIDATES=3
RESEARCH_MESH_ROUTING_FAILURE_THRESHOLD=2
RESEARCH_MESH_ROUTING_COOLDOWN_SECONDS=60
```

仅网络、RPC、协议或身份返回异常触发候选切换；`awaiting-input` 不触发切换，Review 对科研质量的拒绝也不触发切换。provenance 会记录候选数、尝试次数、是否 failover 和先前失败原因。

## 6. 持久化与集中观测

单机竞赛部署使用 SQLite WAL：

```dotenv
RESEARCH_MESH_PERSISTENCE_ENABLED=true
RESEARCH_MESH_STATE_DATABASE=artifacts/state/research-mesh.sqlite3
```

保存内容：

- run 的请求、状态、最终报告或失败原因；
- 每次候选调用的技能、AIC、端点、状态和耗时；
- Leader 产生的结构化运行事件。

接口：

```text
GET /research/runs/{session_id}
GET /metrics/research
```

这些接口可能包含研究问题和运行信息，只能置于现有 HTTPS、mTLS、基本认证或其他访问控制之后。SQLite 适合当前单机部署；多副本部署前应迁移 PostgreSQL/Redis。

## 7. 梧桐新增 Partner

新增服务：

| Partner | Skill | 端口 |
| --- | --- | ---: |
| Dataset | `dataset-analysis` | 8015 |
| Synthesis | `evidence-synthesis` | 8016 |

升级步骤：

1. 在本地生成 v0.7.0 ACS；
2. 分别提交 Dataset 和 Synthesis，等待审核；
3. 同步正式 AIC；
4. 分别获取 EAB 并签发 `serverAuth` 证书；
5. 在 `.env` 填入两个 AIC、URL 和证书路径；
6. 增加两个 systemd 实例并放行 8015、8016；
7. 分别用 `/health`、`/acs` 和 mTLS `/rpc` 验证；
8. 将两个功能开关改为 `true`，最后重启 Leader。

在两个新 Partner 审核完成前，不要在平台 `.env` 中提前开启功能开关。

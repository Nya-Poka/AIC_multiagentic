# 梧桐 ACPs 接入资产

- `acps-cli.toml.example`：梧桐 Registry、CA 与 Discovery 的 CLI 配置样例。
- `generated/*.acs.json`：运行 `scripts/generate-acps.ps1` 后生成的五份 ACS；该目录被 Git 忽略，因为可能包含联系人和域名登记信息。
- 证书、私钥、EAB 和 CLI token 必须放在 `secrets/` 或 `.acps-cli/`，两者均被 Git 忽略。

推荐直接暴露五个 Uvicorn HTTPS/mTLS 监听端口，让 SDK 能读取真实 peer certificate。若使用路径型反向代理，代理必须校验客户端证书，并以可信方式把已验证的 AIC 传递给应用；本项目当前没有实现该代理扩展，因此默认不要使用 `-Routing paths`。

完整流程见 [`docs/WUTONG_INTEGRATION.md`](../../docs/WUTONG_INTEGRATION.md)。

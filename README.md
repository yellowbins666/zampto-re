# GitHub Actions 自动续期

仓库自带 `.github/workflows/zampto-renew.yml`，每天 **北京时间 05:00**（UTC 21:00）自动运行，也可手动 `workflow_dispatch` 触发。

### CI 需要的 Secrets
在 `Repo → Settings → Secrets and variables → Actions → New repository secret` 中添加：

| Secret 名 | 对应脚本变量 | 说明 |
| --- | --- | --- |
| `ZAM_PTO_EMAIL` | `ZAM_PTO_EMAIL` | 登录邮箱 |
| `ZAM_PTO_PASSWORD` | `ZAM_PTO_PASSWORD` | 登录密码 |
| `TG_BOT_TOKEN` | `TG_BOT_TOKEN` | Telegram Bot Token（可选但建议） |
| `TG_CHAT_ID` | `TG_CHAT_ID` | Telegram Chat ID（可选但建议） |
| `NODE_LINK` | — | **仅 CI 使用**：填入代理节点链接（如 `hysteria2://`、`vless://`、`anytls://` 等）；`setup_proxy.sh` 据此生成 sing-box 配置并监听 `127.0.0.1:1081` |

> ⚠️ **注意 `NODE_LINK` 的特殊性**：它**不是** `renew.py` 直接读取的变量，而是被 workflow 的「设置 sing-box 代理」步骤消费。其值是真实**出口节点链接**（协议支持 hy2 / vless / anytls 等，例如 `hysteria2://user:pass@host:port`），`setup_proxy.sh` 用它拉起本地 sing-box 并监听 `127.0.0.1:1081`；随后脚本直接把流量交给该本地端口。若不使用代理，可删除该步骤并将 `IS_PROXY` 设为 `false`。

Workflow 中已硬编码 `IS_PROXY="true"` 以及 sing-box 本地端口 `http://127.0.0.1:1081`，无需在 Secrets 中重复设置；如需直连，请编辑 workflow 文件。

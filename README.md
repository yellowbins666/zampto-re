# ZamPTO 自动续期 (zampto-re)

一个使用 [SeleniumBase](https://github.com/seleniumbase/SeleniumBase) 驱动浏览器、自动登录 [ZamPTO](https://dash.zampto.net) 并续期所有服务器的脚本。支持通过 **Telegram** 推送续期结果与截图，并可在 **GitHub Actions** 上定时自动运行。

---

## 工作原理简述

1. 使用本地/远程浏览器打开 ZamPTO 登录页（`https://dash.zampto.net/auth/login`）。
2. 自动填写账号密码，并处理 Cloudflare Turnstile 人机验证。
3. 登录后遍历账户下所有服务器，逐个点击 **Renew Server** 完成续期。
4. 通过对比续期前后的剩余时间（`Expiry (Next Renewal)`）判定结果，并将结果 + 截图推送到 Telegram（若已配置）。

> ⚠️ 脚本默认以**非无头（headless=False）**模式运行，依赖一个可见/虚拟显示环境。本地需自备图形界面或虚拟显示器（如 `xvfb`），GitHub Actions 中通过 `xvfb-run` 提供。

---

## 环境变量总览

脚本通过环境变量读取所有配置。下表按**必填 / 可选**分类：

| 变量名 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `ZAM_PTO_EMAIL` | ✅ 必填 | 无 | ZamPTO 登录邮箱 |
| `ZAM_PTO_PASSWORD` | ✅ 必填 | 无 | ZamPTO 登录密码 |
| `TG_BOT_TOKEN` | ⬜ 可选 | 空 | Telegram Bot Token，用于推送通知 |
| `TG_CHAT_ID` | ⬜ 可选 | 空 | Telegram 接收 Chat ID |
| `IS_PROXY` | ⬜ 可选 | `true` | 是否启用代理（`true`/`false`） |
| `PROXY_SERVER` | ⬜ 可选 | `http://127.0.0.1:1081` | 代理服务器地址 |
| `LOGIN_STABILIZE_SECONDS` | ⬜ 可选 | `25` | 登录表单就绪后强制等待秒数（范围 20~30） |

> 📌 **仅当 `TG_BOT_TOKEN` 与 `TG_CHAT_ID` 同时配置时**，才会发送 Telegram 通知；缺任意一个都会跳过推送并打印提示。

---

## 变量详解

### `ZAM_PTO_EMAIL` — 登录邮箱（必填）
ZamPTO 账户的注册邮箱。脚本启动时若为空会直接报错退出（`SystemExit(1)`）。

```bash
export ZAM_PTO_EMAIL="yourname@example.com"
```

### `ZAM_PTO_PASSWORD` — 登录密码（必填）
ZamPTO 账户密码。与邮箱同时为必填项，缺失其一程序无法运行。

```bash
export ZAM_PTO_PASSWORD="your-strong-password"
```

> 🔒 **安全建议**：不要在明文命令历史或仓库中填写真实密码。本地请用 `.env` 文件（见下方「本地运行」），CI 中请使用 **Secrets**（见「GitHub Actions」）。

### `TG_BOT_TOKEN` — Telegram Bot Token（可选）
用于发送续期通知的 Telegram Bot 令牌，格式为 `123456789:AAE...`。向 [@BotFather](https://t.me/BotFather) 申请。
- 未配置（或 `TG_CHAT_ID` 缺失）时：跳过 Telegram 推送，仅本地打印日志。
- 配置后：登录失败、续期成功/跳过/异常等事件都会推送**文字 + 截图**到指定 Chat。

```bash
export TG_BOT_TOKEN="123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### `TG_CHAT_ID` — Telegram 接收 ID（可选）
接收通知的 Chat ID（可以是个人 ID 或群组 ID，例如 `8600129634`）。
**必须与 `TG_BOT_TOKEN` 成对出现**才生效。

```bash
export TG_CHAT_ID="8600129634"
```

### `IS_PROXY` — 是否启用代理（可选，默认 `true`）
- `true`（默认）：浏览器通过 `PROXY_SERVER` 指定的代理访问 ZamPTO，适用于需要特定出口 IP 才能访问/注册的地区。
- `false`：直连，不使用任何代理。

```bash
export IS_PROXY="true"      # 使用代理
export IS_PROXY="false"     # 直连
```
> 开启代理时，脚本会先访问 `https://api.ip.sb/ip` 校验出口 IP；若代理不可用会直接报错退出。

### `PROXY_SERVER` — 代理地址（可选，默认 `http://127.0.0.1:1081`）
代理服务器 URL，仅当 `IS_PROXY=true` 时生效。默认指向本机 `127.0.0.1:1081` 的 **sing-box** 本地监听端口（与 GitHub Actions 中的 `setup_proxy.sh` 搭建的代理一致）。

```bash
export PROXY_SERVER="http://127.0.0.1:1081"
```

### `LOGIN_STABILIZE_SECONDS` — 登录稳定等待秒数（可选，默认 `25`）
登录表单出现后、执行任何下一步操作前，脚本会强制等待一段时间，让页面与验证组件完整加载。可调范围被强制限制在 **20~30 秒**（超出会自动夹到该区间），默认 `25`。

```bash
export LOGIN_STABILIZE_SECONDS="25"
```
> 如遇到「页面刚出现验证组件脚本却只顾等 URL」之类的问题，可在区间内微调此值（例如网络较慢设 `30`）。

---

## 本地运行

### 1. 安装依赖
```bash
pip install seleniumbase requests
seleniumbase install chromedriver
```
（Linux 本地还需 `xvfb` 等虚拟显示组件；Windows/Mac 有图形界面则无需。）

### 2. 配置环境变量
推荐用 `.env` 文件 + `python-dotenv`，或直接在 shell 中 `export`：

```bash
# .env 示例（切勿提交到仓库！）
ZAM_PTO_EMAIL="yourname@example.com"
ZAM_PTO_PASSWORD="your-strong-password"
TG_BOT_TOKEN="123456789:AAE..."
TG_CHAT_ID="8600129634"
IS_PROXY="true"
PROXY_SERVER="http://127.0.0.1:1081"
LOGIN_STABILIZE_SECONDS="25"
```

> 🛡️ **最佳实践**：把 `.env` 加入 `.gitignore`，避免密钥泄露。仓库内绝不放真实凭证。

### 3. 运行
```bash
python renew.py
```

---

## GitHub Actions 自动续期

仓库自带 `.github/workflows/zampto-renew.yml`，每天 **北京时间 05:00**（UTC 21:00）自动运行，也可手动 `workflow_dispatch` 触发。

### CI 需要的 Secrets
在 `Repo → Settings → Secrets and variables → Actions → New repository secret` 中添加：

| Secret 名 | 对应脚本变量 | 说明 |
| --- | --- | --- |
| `ZAM_PTO_EMAIL` | `ZAM_PTO_EMAIL` | 登录邮箱 |
| `ZAM_PTO_PASSWORD` | `ZAM_PTO_PASSWORD` | 登录密码 |
| `TG_BOT_TOKEN` | `TG_BOT_TOKEN` | Telegram Bot Token（可选但建议） |
| `TG_CHAT_ID` | `TG_CHAT_ID` | Telegram Chat ID（可选但建议） |
| `NODE_LINK` | — | **仅 CI 使用**：用于 `setup_proxy.sh` 搭建本地 sing-box 代理（监听 `127.0.0.1:1081`） |

> ⚠️ **注意 `NODE_LINK` 的特殊性**：它**不是** `renew.py` 直接读取的变量，而是被 workflow 的「设置 sing-box 代理」步骤消费，用来拉起本地代理；该代理随后被 `PROXY_SERVER=http://127.0.0.1:1081` 指向。若不使用代理，可删除该步骤并将 `IS_PROXY` 设为 `false`。

Workflow 中已硬编码 `IS_PROXY="true"` 与 `PROXY_SERVER="http://127.0.0.1:1081"`，无需在 Secrets 中重复设置；如需直连，请编辑 workflow 文件。

---

## 注意事项与最佳实践

1. **凭证安全**：邮箱/密码只在 Secrets 或本地 `.env` 中存在，永不写入代码或日志。脚本会对日志中的邮箱做脱敏（`ma**il@example.com`）。
2. **最小权限**：Telegram 通知为可选功能，不需要时可完全不配置，脚本仍可正常续期。
3. **代理可靠性**：`IS_PROXY=true` 时务必保证 `PROXY_SERVER` 可达，否则启动即失败并推送错误通知。
4. **定时频率**：默认每天续期一次已足够（ZamPTO 续期周期通常远长于 1 天），无需提高频率。
5. **失败排查**：Workflow 在 `failure()` 时会自动上传 `*.png` 错误截图（保留 3 天），可下载查看具体失败原因（如登录失败、Turnstile 未通过等）。

---

## License

内部自用脚本，按需使用。

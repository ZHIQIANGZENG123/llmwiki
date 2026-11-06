# 局域网与离线部署

本文说明如何把 LLM Wiki 以 **源码直跑** 的方式部署在一台内网机器上，供局域网内其他设备访问，并在无外网的环境下运行。默认的 `llmwiki open <workspace>` 行为（仅回环、无密码）保持不变。

---

## 1. 形态与端口

只开放 **一个** 端口：

```
浏览器 / 局域网设备
        │  http://<服务器IP>:3000
        ▼
  ┌──────────────────┐
  │ Next.js (3000)   │  ← 唯一对外监听的进程
  │  /api/*  ────────┼──►  FastAPI  127.0.0.1:8000   （SQLite + 文件监听）
  │  /mcp    ────────┼──►  MCP      127.0.0.1:8100   （Streamable HTTP）
  └──────────────────┘
```

API 与 MCP **始终只监听回环**，由 Next.js 反向代理。这样对外只有一个入口，鉴权、Host 校验、CORS 都收敛到一处。

---

## 2. 联网机器上做首次准备

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r api/requirements.txt -r mcp/requirements.txt

cd web
npm ci          # 会顺带把 pdf.js worker 复制到 public/，离线也能渲染 PDF
cd ..
```

> 若激活被策略拦截：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`，或直接用 `.venv\Scripts\python`。

---

## 3. 启动局域网服务

```powershell
python llmwiki open D:\wiki --host 0.0.0.0
```

首次运行会：

1. 自动探测本机内网 IP，得出公开地址 `http://<IP>:3000`；
2. 生成一个共享口令，保存到 `<workspace>\.llmwiki\lan-password.txt`（下次启动复用）；
3. 执行一次 `next build`（**首次较慢**，之后除非配置变化不会重复构建）；
4. 依次拉起 API、MCP、Web 三个进程。

输出示例：

```
Starting LLM Wiki for: D:\wiki
  Web:  http://192.168.1.10:3000
  LAN:  exposed on 0.0.0.0:3000 — shared password required
  MCP:  http://192.168.1.10:3000/mcp  (Authorization: Bearer <password>)

  Workspace password: xxxxxxxxxxxxxxxxxxxxxxxxx
  Saved to: D:\wiki\.llmwiki\lan-password.txt
```

其他设备浏览器打开 `http://192.168.1.10:3000`，用该口令登录。

### 常用参数

| 参数 | 说明 |
|---|---|
| `--host` | Web 监听地址。`0.0.0.0` 或具体网卡 IP 即开启局域网模式；默认 `127.0.0.1` |
| `--port` | Web 端口，默认 `3000` |
| `--public-url` | 显式指定对外地址，例如多网卡、或走反向代理时 |
| `--password` | 显式指定共享口令 |
| `--rebuild` | 强制重新构建前端 |
| `--no-browser` | 不自动打开浏览器（服务器上用） |

> 内部端口固定为 API `8000`、MCP `8100`，因为 `web/next.config.ts` 把 `/api`、`/mcp` 的反代目标写死在这两个端口。只有对外端口 `--port` 可改。

固定口令与地址的写法：

```powershell
python llmwiki serve D:\wiki --host 0.0.0.0 `
  --public-url http://192.168.1.10:3000 --password "换成你自己的口令" --no-browser
```

---

## 4. 鉴权与安全

局域网模式下，**所有** 入口都要凭据：

| 通道 | 凭据 | 说明 |
|---|---|---|
| Web UI | 登录后下发 `llmwiki_session` Cookie | HttpOnly、SameSite=Lax、30 天；密码换掉即全部失效 |
| REST API | `Authorization: Bearer <口令>` | 供脚本使用 |
| 文件下载 `/v1/files/*` | 同上 Cookie | 图片/PDF 等静态资源同样受保护 |
| MCP | `Authorization: Bearer <口令>` | MCP 拥有整库读写权限，绝不可匿名暴露 |

另外两道限制仍然保留：

- **Host 白名单**：`Host` 头必须是回环地址或 `LOCAL_PUBLIC_URL` 里的主机，否则 `400`（防 DNS rebinding）；
- **Origin 白名单**：非安全方法的浏览器请求必须来自 `LOCAL_PUBLIC_URL` 或 `LOCAL_ALLOWED_ORIGINS`，否则 `403`（防跨站写入）。

### 建议

1. **务必配 HTTPS**。`http://192.168.x.x` 不是安全上下文，浏览器的剪贴板、Service Worker / PWA 会失效。可用 Caddy 或 nginx 反代 + 自签证书/内网 CA，然后把 `--public-url` 写成 `https://wiki.lan`。Cookie 会自动加 `Secure`。
2. **放行防火墙**只到需要的端口：`New-NetFirewallRule -DisplayName "LLM Wiki" -Direction Inbound -LocalPort 3000 -Protocol TCP -Action Allow`
3. 不要把端口映射到公网。这个形态没有按用户隔离，口令泄露等于整库泄露。
4. 口令文件在 `<workspace>\.llmwiki\lan-password.txt`，随工作区一起备份/管控。

---

## 5. 离线部署

推荐思路：**在联网机器上构建，把成品搬到离网机器上跑**。不要指望在离网机器上跑 `npm ci` / `next build`——前端构建需要下载字体等资源。

另一个硬约束：**离线包必须与目标机同架构、同 Python 小版本**。Python 轮子和 `node_modules` 里的原生模块（`sharp` / `lightningcss` / `@tailwindcss/oxide`）都是平台相关的，Windows 上打的包不能直接给 Linux 用。

### 5.1 本机 Windows 打包 → 部署到 Debian 12（无需 Linux 机器）

Windows 上打出的包**可以**直接给 Debian 12：关键在于绕开两处平台绑定。

```powershell
# 注意：执行策略可能禁止直接运行 .ps1，用 -ExecutionPolicy Bypass
powershell -ExecutionPolicy Bypass -File tools\build-bundle-windows.ps1 -Out C:\llmwiki-bundle
```

产物：`C:\llmwiki-bundle.tar.gz`，约 114 MB。

| 组件 | 怎么做到跨平台 |
|---|---|
| Web 运行时 | `next.config.ts` 的 `output: "standalone"` 会把前端裁成一个自包含目录。里面唯一的原生产物是 Windows 版 `sharp`，而本项目**没有用 `next/image`**，删掉它之后整个目录是纯 JS，可直接在 Linux 上跑 |
| Python 依赖 | 用 `uv pip install --python-platform x86_64-manylinux_2_28 --python-version 3.11` 按**目标环境**解析。普通 `pip download --platform` 不行：它仍用构建机的环境标记，会在 Windows 上把 `mcp` 的 `pywin32` 依赖算进来而失败 |
| Node 运行时 | 从 nodejs.org 官方 linux tarball 里**只解出 `bin/node`**；`bin/npm`、`bin/npx` 是符号链接，Windows 的 tar 建不了 |
| 可执行位 | Windows 的 tar 会把所有文件记成 `0666`，导致解包后 `deploy.sh`、`node` 不可执行。打包脚本先用 `tar -cf` 出裸归档，再用 Python 重写并压成 gz，把需要的条目恢复成 `0755` |

打包脚本会自检：`pylib` 里不能有 `.pyd`、`.so` 必须是 ELF（读 magic 判断），Web 目录里不能有 `.node`，`runtime/bin/node` 必须是 ELF。任一不满足就直接报错，不会产出坏包。

拷到 Debian 12 上：

```bash
sudo tar -xzf llmwiki-bundle.tar.gz -C /opt
cd /opt/llmwiki-bundle

sudo ./deploy.sh --check                                    # 只体检
sudo ./deploy.sh --install-service --workspace /srv/llmwiki --host 0.0.0.0
```

这个包**连 pip 都不需要**：依赖已经预装进 `pylib/`，`deploy.sh` 检测到就直接用。

> 前提：目标机的 Python 必须是 **3.11**（Debian 12 默认就是）。`deploy.sh` 会比对 `BUNDLE-INFO` 里的 `python_minor`，不一致直接拒绝。

### 5.2 在 Linux 机器上打包（有 Debian 12 时的备选）

目标机：Debian 12，x86_64 或 arm64，Python 3.11（bookworm 自带，正好是项目要求的版本）。

#### 第一步：在联网的 Linux 机器上打包

最好直接用一台 Debian 12 虚拟机/容器，保证 Python 是 3.11，轮子才对得上：

```bash
sudo apt install -y python3 curl git
# 构建机还需要 Node.js 20+；bookworm 自带的 nodejs 是 18，不够
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

git clone <this-repo> llmwiki
cd llmwiki
chmod +x tools/*.sh          # 从 Windows 拷过来的仓库没有可执行位
./tools/build-bundle-linux.sh --out /tmp/llmwiki-bundle
```

产物结构与 `deploy.sh` 一起放在 `/tmp/llmwiki-bundle/`，并打成一个 tar.gz：

```
/tmp/llmwiki-bundle/
├── repo/                # 源码 + web/node_modules + 预构建 web/.next
├── vendor/wheels/       # Python 轮子（含 pip 自身，用于离线引导）
├── runtime/             # 便携版 Node（目标机无需安装 Node）
├── deploy.sh            # 部署启动脚本
├── BUNDLE-INFO          # 构建元信息：Python 小版本、架构、Node 版本
└── README-OFFLINE.md
```

`BUNDLE-INFO` 里的 `python_minor` 会由 `deploy.sh` 在目标机上校验，不一致直接拒绝启动，避免装到一半才发现原生轮子不匹配。

#### 第二步：拷到离网机器并解包

```bash
sudo tar -xzf llmwiki-bundle.tar.gz -C /opt
cd /opt/llmwiki-bundle

sudo ./deploy.sh --check                                        # 只体检，不启动
sudo ./deploy.sh --workspace /srv/llmwiki --host 0.0.0.0 --port 3000
```

首次运行会：

1. 把轮子**离线**装进 `pylib/`——目标机不需要网络、不需要 `pip`、不需要 `python3-venv`（缺 pip 时从 bundle 里的 pip 轮子自举）；
2. 初始化工作区 `/srv/llmwiki`；
3. 生成共享口令，写入 `/srv/llmwiki/.llmwiki/lan-password.txt`；
4. 拉起 API / MCP / Web，对外只监听 `3000`。

#### 第三步：装成 systemd 服务（推荐）

```bash
sudo ./deploy.sh --install-service --workspace /srv/llmwiki --host 0.0.0.0

systemctl status llmwiki
journalctl -u llmwiki -f
```

服务以 `llmwiki` 系统用户运行；口令放在 `/etc/llmwiki/env`（0600），通过 `EnvironmentFile` 传入，不出现在 `ps` 里。

`SIGTERM` 会让 CLI 按 `Ctrl-C` 的同一条路径有序关停三个子进程（`KillMode=control-group` + `TimeoutStopSec=30` 兜底）。

#### 可选项（从 Debian ISO 安装）

| 包 | 用途 |
|---|---|
| `libreoffice-writer libreoffice-impress` | Word / PowerPoint 转换 |
| `default-jre-headless` | opendataloader 解析 PDF（默认 PDF 后端） |

`deploy.sh --check` 会检测 `soffice` / `java`，缺失只警告不中断。

#### deploy.sh 参数

| 参数 | 说明 |
|---|---|
| `--workspace DIR` | 知识库目录，默认 `/srv/llmwiki` |
| `--host ADDR` | 监听地址，默认 `0.0.0.0`；`127.0.0.1` 则回到单机模式 |
| `--port N` | Web 端口，默认 `3000` |
| `--password PW` | 指定共享口令；省略则自动生成并持久化 |
| `--python PATH` | Python 解释器，默认 `python3` |
| `--check` | 只做体检 |
| `--install-service` | 安装并启动 systemd 服务 |
| `--service-user NAME` | 服务运行账户，默认 `llmwiki` |
| `--force` | Python 小版本不一致时仍继续 |

### 5.3 部署到另一台 Windows 机器

在联网的 Windows 机器上（与目标机器同架构、同 Python 大版本）：

```powershell
pwsh tools/build-offline-bundle.ps1 -Workspace D:\wiki -Out D:\llmwiki-offline
```

在离网机器上安装：

```powershell
cd D:\llmwiki-offline\repo
python -m venv .venv
.venv\Scripts\python -m pip install --no-index --find-links ..\vendor\wheels -r ..\vendor\requirements.txt

.venv\Scripts\python llmwiki serve D:\wiki --host 0.0.0.0 --no-browser
```

`web\.next` 已存在且配置指纹一致，启动时会**跳过**构建，直接 `next start`。

### 5.4 离线相关的改动说明

| 项 | 处理 |
|---|---|
| pdf.js worker | 改为同源 `public/pdf.worker.min.mjs`，安装/构建时自动从 `node_modules` 复制（`web/scripts/copy-pdf-worker.mjs`）。原先走 unpkg CDN，断网即失效 |
| 字体 | 仍是 `next/font/google`，在**构建期**下载并内嵌进产物。所以只要构建发生在联网机器上，运行时完全离线 |
| 遥测 | 启动时设置 `NEXT_TELEMETRY_DISABLED=1` |
| Sentry / Logfire | 未设置 DSN/Token 即不发任何数据，离线无需处理 |
| PDF / Office 解析 | 默认 `opendataloader` + 本地 JRE，纯离线。`MISTRAL_API_KEY` 属可选增强，离网留空即可 |
| SSRF 防护 | `api/infra/safe_fetch.py` 会拒绝私有网段。**若需要抓取内网网页作为来源，需要额外放行** |

---

## 6. 接入本地大模型

LLM Wiki 本身不调用 LLM —— 知识库页面是由「外部智能体通过 MCP 读写」的。所以「接入本地大模型」= 让本地模型客户端连上本机的 MCP 端点。

**端点**：`http://<服务器IP>:3000/mcp`（Streamable HTTP）
**鉴权**：请求头 `Authorization: Bearer <共享口令>`

### 6.1 客户端选择

任选一个支持 **MCP + 本地模型** 的客户端，用 Ollama 或 vLLM 提供模型：

| 客户端 | 类型 | 备注 |
|---|---|---|
| Cherry Studio | 桌面 | 图形化配置 MCP，接 Ollama 最省事 |
| LibreChat | 自部署 Web | 支持 MCP 与多种本地推理后端 |
| Open WebUI | 自部署 Web | 支持 MCP 工具 |
| Cline / Roo Code | VS Code 插件 | 配置里填 `url` + 自定义 header |
| 任意自研脚本 | — | 用 `mcp` Python SDK 的 `streamable_http_client` |

配置里的关键点：**Streamable HTTP 传输 + 自定义请求头 `Authorization: Bearer <口令>`**。

### 6.2 仓库自带的最小 Agent（离线场景推荐）

不想额外装客户端时，用 `tools/wiki_agent.py`——它把 MCP 工具直接转成 Ollama 的 tool calling，不需要任何第三方软件：

```powershell
# 先验证 MCP 通不通，并看有哪些工具（不需要模型）
.venv\Scripts\python tools\wiki_agent.py --token "<口令>" --list-tools

# 拉一个支持 tool calling 的模型，然后让它维护知识库
ollama pull qwen2.5:14b
.venv\Scripts\python tools\wiki_agent.py --token "<口令>" --model qwen2.5:14b --verbose
```

默认执行的就是下面这条维护提示词；`--mcp-url`、`--ollama-url`、`--max-steps` 可按需覆盖。

### 6.3 让模型自动维护知识库

连上之后，把一个「维护提示词」配成定时任务（客户端自带定时/工作流，或用系统计划任务）即可对标 README 里 Claude Routine 的效果：

> 先调用 `guide` 了解工作区。找出上次运行以来新增的来源、剪藏与高亮。逐个阅读并更新 wiki：该新建页面的新建，该并入已有页面的并入，并修正受影响的交叉引用与脚注。

建议在 `guide` 之后固定调用 `lint` 收尾，让引用与孤儿页问题每轮自愈。

### 6.4 注意

- 本地模型对长上下文与工具调用的支持差异很大。**工具调用（tool calling）能力是硬门槛**，7B 级别模型通常不稳定，建议 14B 以上或专门的 function-calling 模型。
- 离线且无网络时，提示词里「otherwise research with your available tools」这一类兜底会失效，只能依赖本地已有来源。

### 6.5 已知限制：Chrome 扩展

浏览器扩展在局域网模式下**目前用不了**：它直接请求 API 且不会带凭据，而局域网模式下所有接口都要求 Cookie 或 Bearer。可选规避方式：

- 用 Web UI 拖拽上传，或直接把文件放进工作区目录（后台监听器会自动索引）；
- 若需要启用扩展，需要在 `extension/` 里加一个口令输入框，并把请求改为携带 `Authorization: Bearer <口令>`——服务端已经支持这种鉴权，只差客户端改动。

---

## 7. 故障排查

| 现象 | 原因与处理 |
|---|---|
| 局域网访问返回 `400 Invalid Host header` | `Host` 不在白名单。用 `--public-url` 指定实际访问地址，或加 `LOCAL_ALLOWED_HOSTS` |
| 写操作返回 `403 Origin not allowed` | 浏览器 `Origin` 未被允许。确认 `--public-url` 与地址栏一致（含端口、协议） |
| 一直跳回登录页 | Cookie 未生效：确认是 `http://<同一主机>:<同一端口>` 访问，且浏览器未禁用 Cookie |
| 页面能打开但接口全 401 | API 进程启动时没拿到口令。检查启动日志里 `LOCAL_AUTH_TOKEN` 是否为空 |
| 首次启动很慢 | 正在执行 `next build`。属正常，后续启动会跳过 |
| `next build` 失败 | 检查 `web/node_modules` 是否完整；离线机器请用第 5 节的预构建产物 |
| PDF 预览空白 | `web/public/pdf.worker.min.mjs` 缺失。执行 `node web/scripts/copy-pdf-worker.mjs` |

---

## 8. 回退到单机模式

不传 `--host` 即可，行为与改造前一致：

```powershell
python llmwiki open D:\wiki
```

此时 API、MCP、Web 全部只监听 `127.0.0.1`，无需口令。

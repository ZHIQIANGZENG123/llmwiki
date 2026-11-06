# LLM Wiki 离线部署与使用指南

> 适用场景：**目标机没有外网**，只能把成品从联网机器搬过去。
> 本文与 `DEPLOYMENT.md` 互补：那份讲局域网形态与安全，这份专讲「离线怎么打包、怎么装、怎么用」。
> 所有结论都基于本仓库源码实测（`llmwiki`、`tools/*`、`web/next.config.ts`、`web/.next` 实际产物）。

---

## 0. 一句话结论

> **在联网机器上构建，把成品整体搬过去。**
> 离线机**不能**跑 `npm ci`，也**不能**跑 `next build`——因为 `web/src/app/layout.tsx` 用了 `next/font/google`，构建阶段要从 Google 下载字体。

所以离线包里**必须**包含：

- `web/node_modules`（约 795 MB，含 `sharp` / `lightningcss` / `@tailwindcss/oxide` 等原生模块）
- `web/.next`（预构建产物，含 `BUILD_ID`）
- `web/public/pdf.worker.min.mjs`

**只发源码的包在离线环境一定跑不起来**，这一点没有折中方案。

---

## 1. 架构与端口

对外只开放 **一个** 端口：

```
局域网设备 / 浏览器
        │  http://<服务器IP>:3000
        ▼
  ┌──────────────────────┐
  │  Next.js  :3000      │  ← 唯一对外监听的进程
  │   /api/* ────────────┼──►  FastAPI  127.0.0.1:8000
  │   /mcp   ────────────┼──►  MCP      127.0.0.1:8100
  └──────────────────────┘
```

API 与 MCP **永远只监听回环**，由 Next.js 反向代理，鉴权/Host 校验/CORS 全部收敛在 3000 这一个入口。

> ⚠️ **8000 / 8100 不可改**。`web/next.config.ts` 把 `/api`、`/mcp` 的反代目标硬编码成了这两个端口，改端口会直接打断代理。只有对外端口 `--port` 可以改。

---

## 2. 准备清单

### 构建机（联网）

| 依赖 | 版本要求 | 说明 |
|---|---|---|
| Node.js | **20.9+** | `next@16.3.8` 的 `engines` 是 `>=20.9.0` |
| Python | **与目标机同小版本** | 决定了 `pip download` 下哪个 ABI 的轮子 |
| 网络 | 需要 | 拉 npm 包、Python 轮子、构建字体 |

### 目标机（离线）

| 依赖 | Windows 路线 | Debian 12 路线 |
|---|---|---|
| Node.js 20+ | **必须自备** | 自带（bundle 内含便携 Node） |
| Python | **必须自备**，同小版本 | 自带 3.11 即可，无需 pip |
| pip / venv | 需要 | **不需要**（从 bundle 里的 pip 轮子自举） |

> 关键差异：`build-offline-bundle.ps1` **不打包 Node 运行时**，而 `build-bundle-linux.sh` 会下载一份便携 Node 放进 `runtime/`。所以 Windows 目标机必须自己装 Node。

### 硬约束（文档 `DEPLOYMENT.md:120`）

**同 CPU 架构 + 同 Python 小版本。**

`sharp`、`lightningcss`、`@tailwindcss/oxide` 都是原生二进制；Python 轮子也是平台相关的。
**Windows 上打的包不能给 Linux 用**，反之亦然。这一步没有兼容层。

---

## 3. 路线 A：目标机是 Windows

### 3.1 在联网机器上打包

先确认 `web/.next` 已构建好：

```powershell
Test-Path web\.next\BUILD_ID          # 必须为 True
```

然后打包（**注意不要加 `-Build`**，原因见 3.2）：

```powershell
cd <仓库根目录>
pwsh tools\build-offline-bundle.ps1 `
  -Workspace D:\wiki `
  -Out D:\llmwiki-offline `
  -Python C:\Python311\python.exe
```

参数说明：

| 参数 | 说明 |
|---|---|
| `-Workspace` | 仅用于提示工作区是否初始化过，**只读不拷贝**，随便给 |
| `-Out` | 输出目录，存在会被整体删除重建 |
| `-Python` | 用于 `pip download` 的解释器，**必须是目标机的 Python 小版本** |
| `-Build` | ⚠️ 见下节，**别用** |

产物结构：

```
D:\llmwiki-offline\
├── repo\                       # 源码 + web\node_modules + 预构建 web\.next
├── vendor\
│   ├── wheels\                 # Python 轮子
│   └── requirements.txt
└── INSTALL-OFFLINE.md
```

### 3.2 三个必须规避的坑

#### 坑 1：`-Build` 会毁掉正确的构建 —— 别加

脚本 `tools/build-offline-bundle.ps1:63-73` 的 `-Build` 分支是：

```powershell
& 'npm.cmd' ci
& 'npm.cmd' run build      # ← 没有设置 NEXT_PUBLIC_MODE=local
```

而 `next.config.ts` 的 `rewrites()` 是**构建期求值**的：只有 `NEXT_PUBLIC_MODE=local` 时才会生成 `/api`、`/mcp` 反代，并写进 `.next/routes-manifest.json`。用 `-Build` 重建会产出一份**没有反代**的包 —— 装上去就是白屏 + 所有接口 404。

对比 `tools/build-bundle-linux.sh:97-98` 是正确的：

```bash
NEXT_PUBLIC_MODE=local NEXT_PUBLIC_API_URL=/api npm run build
```

**做法：直接复用现有 `.next`，不加 `-Build`。**

打包前可以自己验证当前构建是不是 local 模式：

```powershell
Get-Content web\.next\routes-manifest.json | Select-String '/api/:path\*'
# 能匹配到 → 这份构建是对的，可复用
```

#### 坑 2：缺 `.llmwiki-build` 标记 → 离线机首次启动会尝试联网重建

`llmwiki:239-249` 的判定逻辑：

```python
up_to_date = (
    (WEB_DIR / ".next" / "BUILD_ID").exists()
    and marker_path.exists()
    and marker_path.read_text().strip() == marker
)
if up_to_date and not rebuild:
    return          # ← 只有成立才会跳过构建
```

**`build-offline-bundle.ps1` 不会写这个标记**（`build-bundle-linux.sh:107-108` 会写）。标记缺失时，程序在离线机上会去跑 `next build` → 拉字体失败 → 启动失败或白等几分钟。

打包后**必须手动补上**，内容一字不差（`NEXT_PUBLIC_MCP_URL` 代码里从不设置，所以为空）：

```powershell
'NEXT_PUBLIC_MODE=local|NEXT_PUBLIC_API_URL=/api|NEXT_PUBLIC_MCP_URL=' |
  Set-Content D:\llmwiki-offline\repo\web\.next\.llmwiki-build -Encoding utf8 -NoNewline
```

#### 坑 3：Python 版本下错轮子

`pip download` 默认用调用它的那个解释器。若构建机是 Python 3.14、目标机是 3.11，下的是 `cp314` 轮子，搬过去装不上。

**用 `-Python` 显式指定与目标机一致的版本。**

### 3.3 瘦身（可选）

`web\.next\cache` 是构建缓存，实测占 `.next` 的 74%（171 MB / 232 MB），离线运行不需要：

```powershell
Remove-Item -Recurse -Force D:\llmwiki-offline\repo\web\.next\cache
```

但 **`BUILD_ID`、`standalone`、`static`、`routes-manifest.json` 绝对不能删**。

### 3.4 压缩搬运

```powershell
tar -czf D:\llmwiki-offline.tar.gz -C D:\ llmwiki-offline
```

用 `tar`（Windows 10+ 自带）比 `Compress-Archive` 快很多。体积大约 300 MB。

### 3.5 在目标机安装

解包后：

```powershell
cd D:\llmwiki-offline\repo

# 建虚拟环境（用目标机的 Python）
python -m venv .venv

# 完全离线安装依赖：--no-index 禁止访问网络，只用本地 wheels
.venv\Scripts\python -m pip install --no-index --find-links ..\vendor\wheels -r ..\vendor\requirements.txt
```

`--no-index` 是关键：它让 pip 完全不走 PyPI，只从 `..\vendor\wheels` 装。

### 3.6 启动

```powershell
# 仅本机使用（无口令，只监听回环）
.venv\Scripts\python llmwiki open D:\wiki

# 局域网共享（必须）
.venv\Scripts\python llmwiki serve D:\wiki --host 0.0.0.0 --no-browser
```

首次局域网启动会打印一个随机口令，并存到 `D:\wiki\.llmwiki\lan-password.txt`。

也可以直接用 `llmwiki.cmd`（内容就是 `python "%~dp0llmwiki" %*`，需 `python` 在 PATH 里）：

```cmd
llmwiki.cmd serve D:\wiki --host 0.0.0.0
```

---

## 4. 路线 B：目标机是 Debian 12

这条路线更省心：自带便携 Node、自带 pip 轮子自举、目标机连 pip 都不用装。

### 4.1 在联网 Linux 上打包

**必须在 Linux 上跑**（脚本会拒绝在 Windows 执行）：

```bash
sudo apt install -y python3 curl git

# 构建机需要 Node 20+；bookworm 自带的 nodejs 是 18，不够
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

git clone <仓库地址> llmwiki && cd llmwiki
chmod +x tools/*.sh          # 从 Windows 拷过来的仓库没有可执行位
./tools/build-bundle-linux.sh --out /tmp/llmwiki-bundle
```

常用选项：

| 选项 | 说明 |
|---|---|
| `--out DIR` | 输出目录（必填，不能是仓库内） |
| `--arch x64\|arm64` | 目标 Node 运行时架构，默认取本机 |
| `--node-version VER` | 打包的 Node 版本，默认取本机 `node` |
| `--skip-build` | 复用现有 `web/.next`，不重新构建 |

### 4.2 在目标机部署

```bash
sudo tar -xzf llmwiki-bundle.tar.gz -C /opt
cd /opt/llmwiki-bundle

sudo ./deploy.sh --check                    # 只体检，不启动
sudo ./deploy.sh --workspace /srv/llmwiki --host 0.0.0.0 --port 3000
```

首次运行会：

1. 把轮子**离线**装进 `pylib/`（不需要网络、pip、venv）
2. 校验 `BUNDLE-INFO` 里的 `python_minor`，不匹配直接拒绝启动
3. 初始化工作区 `/srv/llmwiki`
4. 生成口令写入 `/srv/llmwiki/.llmwiki/lan-password.txt`
5. 拉起 API / MCP / Web，只对外监听 3000

### 4.3 装成 systemd 服务

```bash
sudo ./deploy.sh --install-service --workspace /srv/llmwiki --host 0.0.0.0

systemctl status llmwiki
journalctl -u llmwiki -f
```

服务以 `llmwiki` 用户运行，口令放在 `/etc/llmwiki/env`（0600），不经过命令行，`ps` 里看不到。

### 4.4 `deploy.sh` 全部选项

| 选项 | 默认 | 说明 |
|---|---|---|
| `--workspace DIR` | `/srv/llmwiki` | 库目录 |
| `--host ADDR` | `0.0.0.0` | 监听地址；`127.0.0.1` 即单机模式 |
| `--port N` | `3000` | Web 端口 |
| `--password PW` | 自动生成 | 共享口令 |
| `--python PATH` | `python3` | 解释器 |
| `--check` | — | 只体检 |
| `--install-service` | — | 安装 systemd 单元 |
| `--service-user NAME` | root 时为 `llmwiki` | 服务账号 |
| `--rebuild` | — | 强制重建前端（离线通常做不到） |
| `--force` | — | Python 小版本不匹配也继续 |

---

## 5. 运行时配置

### 5.1 命令行参数

`open` 和 `serve` 共用同一组参数（`llmwiki:550-569`）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `workspace` | 必填 | 库目录路径 |
| `--host` | `127.0.0.1` | Web 监听地址。填 `0.0.0.0` 或具体网卡 IP 即开启局域网模式（自动要求口令） |
| `--port` | `3000` | 对外 Web 端口 |
| `--public-url` | 自动探测 | 对外地址，如 `http://192.168.1.10:3000`；多网卡或走反代时显式指定 |
| `--password` | 自动生成 | 共享口令 |
| `--rebuild` | 关 | 强制重建前端（**离线环境别用**） |
| `--no-browser` | 关 | 不自动开浏览器（服务器上用） |

### 5.2 环境变量不用手改

`llmwiki serve` 会在启动子进程前**自动注入**全部运行时变量（`llmwiki:324-341`）：

```
MODE=local                 WORKSPACE_PATH=<库目录>
DATABASE_URL=（清空）        APP_URL=http://<对外地址>
API_URL=<对外地址>/api      LOCAL_BIND_HOST=127.0.0.1
LOCAL_PUBLIC_URL=...       LOCAL_LAN_ENABLED=0|1
LOCAL_AUTH_TOKEN=<口令>     LOCAL_ALLOWED_HOSTS=<对外主机>,localhost:<端口>
LOCAL_MCP_TRANSPORT=streamable-http
LOCAL_MCP_HOST=127.0.0.1   LOCAL_MCP_PORT=8100
```

所以**不需要**手动编辑 `api/.env` 或 `web/.env.local`。`.env.example` 里那批变量只在「直接跑 uvicorn、绕过 CLI」时才手工设。

口令也可以通过环境变量传（systemd 就是这么做的），`--password` 优先级更高：

```
LLMWIKI_PASSWORD=<口令>
```

### 5.3 口令管理

| 场景 | 行为 |
|---|---|
| 局域网模式首次启动 | 随机生成，写入 `<workspace>/.llmwiki/lan-password.txt` |
| 之后启动 | 复用该文件 |
| 显式 `--password` | 覆盖文件里的值 |
| 单机模式（回环） | 不需要口令 |

改口令后，已登录的 `llmwiki_session` Cookie 全部失效。

---

## 6. 日常使用

### 6.1 命令一览

| 命令 | 作用 |
|---|---|
| `llmwiki init <目录>` | 初始化工作区（建 `.llmwiki/index.db`） |
| `llmwiki open <目录>` | 未初始化则先初始化，然后启动三个服务并打开浏览器 |
| `llmwiki serve <目录>` | 只启动服务（`open` 的子集，加 `--no-browser` 就是它） |
| `llmwiki mcp <目录>` | 跑 stdio 版 MCP 服务（给 Claude Desktop 用） |
| `llmwiki mcp-config <目录>` | 打印 Claude Desktop 的配置片段 |
| `llmwiki reindex <目录> --yes` | 强制重建索引 |

常用示例：

```powershell
# 初始化
.venv\Scripts\python llmwiki init D:\wiki

# 局域网共享，固定口令与地址
.venv\Scripts\python llmwiki serve D:\wiki --host 0.0.0.0 `
  --public-url http://192.168.1.10:3000 `
  --password "换成你自己的口令" --no-browser

# 只在 3000 以外的端口对外
.venv\Scripts\python llmwiki serve D:\wiki --host 0.0.0.0 --port 8080
```

### 6.2 启动输出

```
Starting LLM Wiki for: D:\wiki
  Web:  http://192.168.1.10:3000
  LAN:  exposed on 0.0.0.0:3000 — shared password required
  MCP:  http://192.168.1.10:3000/mcp  (Authorization: Bearer <password>)

  Workspace password: xxxxxxxxxxxxxxxxxxxx
  Saved to: D:\wiki\.llmwiki\lan-password.txt
```

### 6.3 接入 Claude Desktop（MCP）

```powershell
.venv\Scripts\python llmwiki mcp-config D:\wiki
```

输出一段 JSON，粘进 `claude_desktop_config.json` 的 `mcpServers`。走 stdio，不需要网络。

### 6.4 健康检查

```powershell
curl http://127.0.0.1:3000/api/health
```

### 6.5 reindex 注意

`reindex` 会重建搜索索引，但**丢弃**仅存在于 `index.db` 的数据：高亮、评论、回复、课程/测验进度、clip 元数据。执行前会自动备份到 `index.db.bak-<时间戳>`。

---

## 7. 安全建议

1. **务必配 HTTPS**。`http://192.168.x.x` 不是安全上下文，剪贴板 API、Service Worker/PWA 都会失效。用 Caddy / nginx 反代 + 自签或内网 CA，然后把 `--public-url` 写成 `https://wiki.lan`，Cookie 会自动加 `Secure`。
2. **防火墙只放行一个端口**：
   ```powershell
   New-NetFirewallRule -DisplayName "LLM Wiki" -Direction Inbound `
     -LocalPort 3000 -Protocol TCP -Action Allow
   ```
3. **不要映射到公网**。这个形态没有多用户隔离，口令泄露 = 整库泄露。
4. 口令文件 `<workspace>/.llmwiki/lan-password.txt` 纳入备份与权限管控。

鉴权覆盖全部通道：Web UI（`llmwiki_session` Cookie）、REST API（`Authorization: Bearer`）、文件下载、MCP。

---

## 8. 排错速查

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动时卡在 `Building the web app…` | `.llmwiki-build` 标记缺失或内容不匹配 | 按 3.2 坑 2 补标记；确认 `NEXT_PUBLIC_MODE=local` |
| 提示 `Node.js 20+ is required` | 目标机没有 Node，或版本 < 20.9 | 装 Node 22 |
| 页面加载了，但所有接口 404 | `.next` 是用非 local 模式构建的 | 在联网机用 `NEXT_PUBLIC_MODE=local` 重建，或换回正确构建 |
| `pip install` 报找不到包 | 轮子与目标 Python 小版本不匹配 | 用目标版本重新 `pip download` |
| `deploy.sh` 拒绝启动：Python 版本不符 | `BUNDLE-INFO` 校验失败 | 换匹配的解释器，或 `--force`（原生轮子可能崩） |
| PDF 预览加载不出来 | `public/pdf.worker.min.mjs` 缺失 | 在 `web/` 下跑 `node scripts/copy-pdf-worker.mjs` |
| 局域网其他设备打不开 | 防火墙 / 监听地址 | 确认 `--host 0.0.0.0`，放行端口 |
| `400` on Host header | Host 不在白名单 | 用 `--public-url` 指定正确对外地址 |
| `403` on write | Origin 白名单未命中 | 同上，或配 `LOCAL_ALLOWED_ORIGINS` |
| 开机自启（Windows） | 无内置服务安装 | 用任务计划程序或 NSSM 包装 `llmwiki serve` |

---

## 9. 升级流程

离线环境**不能**在目标机上更新依赖或重建前端：

1. 把新代码同步到**联网**构建机
2. 重新执行 `npm ci` + `npm run build`（**必须带 `NEXT_PUBLIC_MODE=local`**）
3. 重新跑第 3 或第 4 节的打包流程
4. 目标机：备份 `<workspace>/.llmwiki/`（含索引与口令），换包，重启

> 只改 `web/src` 的话，第 2 步重建 `.next` 就够了；`node_modules` 没变可以整目录复用，省一次 785 个包的下载。

---

## 附：为什么必须带 `node_modules`

静态文本类的包压缩率高，但 `node_modules` 里有大量原生二进制：

| 包 | 大小 | 平台相关 |
|---|---|---|
| `next` / `@next` | 177 + 101 MB | 含 `@next/swc-*` 编译器二进制 |
| `@napi-rs` | 36 MB | 原生 |
| `@img`（sharp） | 19 MB | 原生 |
| `lightningcss-win32-x64-msvc` | 9 MB | 原生 |
| `@tailwindcss/oxide` | — | 原生 |

`web/package-lock.json` 里这些包的 Windows / Linux / arm64 / musl / darwin 变体**全都记录在案**（共 785 个条目），所以联网机能正确解析。但离线机没有注册表访问权限，无法按平台筛选下载——**只能整目录搬**。

这也是为什么离线包必须与目标机**同架构**。

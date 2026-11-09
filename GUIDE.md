# LLM Wiki 使用与部署指南

> 面向本项目当前的实际改造版本。涵盖：单机使用、局域网共享、离线部署到 Debian 12、界面操作、MCP 接入、故障排查。
>
> 相关文件：本文件是总入口；[DEPLOYMENT.md](DEPLOYMENT.md) 是局域网/离线改造的设计说明；`tools/` 下是打包与部署脚本。

---

## 目录

1. [这是什么](#1-这是什么)
2. [三种部署形态](#2-三种部署形态)
3. [单机使用（最快上手）](#3-单机使用最快上手)
4. [局域网共享](#4-局域网共享)
5. [界面使用说明](#5-界面使用说明)
6. [接入 AI（MCP）](#6-接入-aimcp)
7. [离线部署到 Debian 12](#7-离线部署到-debian-12)
8. [离线开发](#8-离线开发)
9. [本机已部署实例](#9-本机已部署实例)
10. [故障排查](#10-故障排查)
11. [已知限制](#11-已知限制)

---

## 1. 这是什么

一个**由 AI 维护的个人/团队 Wiki**：

- 你把 PDF、Word、Excel、Markdown、网页剪藏丢进一个文件夹
- 系统把它们解析成文本、切块、建全文索引
- AI（Claude 或本地模型，通过 MCP）读取这些原文，写成人能读的 wiki 页面
- 人在浏览器里读 wiki、翻原始文档、做高亮和批注

**关键点：它自己不调用大模型。** 页面是外部 AI 通过 MCP 工具写进来的，所以离线部署时接什么模型由你决定。

### 架构

```
浏览器 ──HTTP──►  Web (Next.js)  ──┬── /api/* ──►  FastAPI  (SQLite + 文件监听)
                                   └── /mcp    ──►  MCP 服务  (AI 的工具接口)
                                                     │
                                                     └──► 你的文件（只读）+ wiki/ 页面
```

对外只需要 **一个端口**（默认 3000）：API 与 MCP 都只监听回环，由 Web 反向代理。这样防火墙、鉴权、跨域都收敛在一处。

---

## 2. 三种部署形态

| 形态 | 适用 | 口令 | 需要什么 |
|---|---|---|---|
| **单机** | 自己一台电脑上用 | 无 | Python 3.11+、Node 20+ |
| **局域网** | 团队内网共用、多人访问 | 必须有 | 同上 + 放行 3000 端口 |
| **离线单机/局域网** | 目标机无外网 | 必须有 | 一个提前打好的离线包 |

---

## 3. 单机使用（最快上手）

### 3.1 准备环境

需要 **Python 3.11+** 和 **Node.js 20+**。

```powershell
# 安装 Python 依赖（建议独立虚拟环境）
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r api/requirements.txt -r mcp/requirements.txt

# 安装前端依赖
cd web
npm ci
cd ..
```

> 若系统 Python 版本高于 3.12，`asyncpg` / `watchfiles` 可能没有预编译轮子。推荐用 uv 装一个 3.11：
> ```powershell
> python -m uv python install 3.11
> python -m uv venv .venv --python 3.11
> python -m uv pip install --python .venv\Scripts\python.exe -r api\requirements.txt -r mcp\requirements.txt
> ```

### 3.2 初始化工作区

选一个**已存在或新建的文件夹**作为知识库（不要放进本项目目录）：

```powershell
.venv\Scripts\python.exe llmwiki init "D:\我的知识库"
```

它会在里面建两个东西，**不会动你原有的文件**：

```
D:\我的知识库\
  wiki\              # AI 写的页面，普通 Markdown
  .llmwiki\          # index.db（索引）+ cache\（解析产物）
```

### 3.3 启动

```powershell
.venv\Scripts\python.exe llmwiki open "D:\我的知识库"
```

- 首次会先构建前端（约 1 分钟），之后不再重建
- 浏览器自动打开 <http://localhost:3000/wikis>
- `Ctrl+C` 停止（三个子进程会一起退出）

> `open` = 没初始化就先初始化 + 启动。`serve` 只启动，不初始化。

---

## 4. 局域网共享

### 4.1 启动

```powershell
.venv\Scripts\python.exe llmwiki serve "D:\我的知识库" --host 0.0.0.0 --no-browser
```

首次以 `--host` 非回环地址启动时：

1. 自动探测本机内网 IP，得出访问地址
2. **生成共享口令**，存到 `<工作区>\.llmwiki\lan-password.txt`
3. 只把 Web 暴露到 `0.0.0.0:3000`，API/MCP 仍在 `127.0.0.1`

其他设备访问 `http://<本机IP>:3000`，用该口令登录。

**指定口令：**

```powershell
.venv\Scripts\python.exe llmwiki serve "D:\我的知识库" --host 0.0.0.0 --password "你的口令"
```

口令也可以直接改 `.llmwiki\lan-password.txt` 后重启（会话用口令做签名密钥，换口令会让所有已登录浏览器掉线）。

### 4.2 参数

| 参数 | 说明 |
|---|---|
| `--host` | 监听地址。`0.0.0.0` 或具体网卡 IP 即开启局域网；默认 `127.0.0.1` |
| `--port` | Web 端口，默认 `3000` |
| `--public-url` | 显式指定对外地址（多网卡、走反代时用） |
| `--password` | 共享口令 |
| `--rebuild` | 强制重新构建前端 |
| `--no-browser` | 不自动开浏览器 |

> 内部端口固定为 API `8000`、MCP `8100`（`web/next.config.ts` 的反代目标写死），只有对外端口可改。

### 4.3 安全要点

- **放行防火墙**（需管理员）：
  ```powershell
  New-NetFirewallRule -DisplayName "LLM Wiki" -Direction Inbound -LocalPort 3000 -Protocol TCP -Action Allow
  ```
- **口令强度**：局域网内能连上 3000 的人拥有整库读写权限（含通过 MCP 删改页面）。不要用弱口令。
- **协议**：`http://内网IP` 不是安全上下文，剪贴板 API、Service Worker / PWA 会失效。需要的话前面挂 Caddy/nginx + 自签证书，并把 `--public-url` 设成 `https://...`（此时 Cookie 自动加 `Secure`）。
- 不要把端口映射到公网。这是单租户形态，没有按用户隔离。

---

## 5. 界面使用说明

左侧边栏 + 右侧内容区。核心是两处：

### 5.1 Files（文件视图）

`/wikis/<知识库名>/files`

| 操作 | 说明 |
|---|---|
| 拖文件进来 | 自动解析入库；支持 PDF / Word / PowerPoint / Excel / Markdown / 文本 / 网页 / 图片 |
| 右键空白处 | 新建便签、新建文件夹、上传 |
| 点文件夹 | 进入；**空文件夹也会显示**（直接读磁盘） |
| 顶部 `←` `→` | 目录前进/后退 |
| 面包屑 | 点击任意层级跳转，也可把文件拖到面包屑上移动 |
| 排序 | 按名称 / 日期 / 类型 |
| 右上角放大镜 | 搜文件**名** + 搜**内容**（见下） |

**搜索行为**：输入 2 个字符以上，会向 `/v1/search` 查正文，在网格上方显示「In document contents」结果区，每条带标题、页码、上下文片段，点一下直接打开文档。

**表格预览（xlsx / xls）**：走 **ZetaOffice**——LibreOffice 编译成的 WebAssembly 版，在 iframe 里渲染原件，公式、样式、合并单元格、100 行以后的数据都在（旧的文本表格视图只保留每个 sheet 前 100 行）。运行时打不开时会给出原因，「改用表格视图」可退回原来的纯文本表格。

体积要心里有数：运行时**磁盘上约 250 MiB**（`soffice.wasm` 154 MiB + `soffice.data` 95 MiB），CDN 走 Brotli 传输约 53 MB，浏览器按 URL 缓存一周。

> **中文字体是要单独装的**：上游运行时自带 138 个字体，一个中日韩字库都没有，中文会显示成方块（□□）。查看器会在引擎启动前把 `<运行时目录>/fonts/cjk.ttf` 写进 LibreOffice 的字体目录——这是唯一还来得及的时机（启动后注入不再生效）。
>
> ```powershell
> # 连网机器上：下载 4 个运行时文件 + Noto Sans SC（SIL OFL 1.1，约 10 MiB）
> node web/scripts/fetch-zetaoffice-runtime.mjs --cjk-font
> # 或者换成自己可再分发的字体
> $env:ZETAOFFICE_CJK_FONT_URL = "https://你的镜像/思源黑体.ttf"
> node web/scripts/fetch-zetaoffice-runtime.mjs
> ```
>
> 换字体后要留意缓存：运行时目录带 `Cache-Control: max-age=604800`，同一浏览器一周内还会用旧的 `cjk.ttf`，验证时用无痕窗口或换一个 URL（`?base=` 指向新目录）。

> 离线/内网部署：先在联网机器上执行 `node web/scripts/fetch-zetaoffice-runtime.mjs`，把运行时装进 `web/public/zetaviewer/runtime/`（已 gitignore），再打包；否则查看器会退回文本表格。
>
> 自托管时注意：Next 的静态响应不对 `application/wasm` 做压缩，所以**局域网首次打开要传约 250 MiB**（之后走浏览器缓存）。想省这一下，可以在前面挂 Caddy/nginx 开 Brotli/gzip 并预压缩该目录。
>
> 另外 `/zetaviewer/*` 必须带 `Cross-Origin-Opener-Policy: same-origin` 与 `Cross-Origin-Embedder-Policy: require-corp`（已写进 `web/next.config.ts`）——LibreOffice 用 pthreads，需要 `SharedArrayBuffer`，而浏览器只对跨源隔离的文档暴露它。若在前面加了反代，记得透传这两个头。

### 5.2 搜索（两个入口，行为一致）

| 入口 | 快捷键 | 搜索范围 |
|---|---|---|
| 侧边栏搜索 | `Ctrl + K` | wiki 页面标题、源文件名、**文档正文** |
| Files 视图放大镜 | 点开输入 | 当前目录的文件名、**文档正文** |

**匹配规则**：

- **字面子串匹配**，不是语义搜索
- 英文不区分大小写
- 多个词用空格分隔是 **AND**（同一段内都要出现），例如 `MOSA 架构`
- 中文能搜到句中任意位置——后端用 `LIKE` 绕过了 FTS5 的英文分词器（详见[已知限制](#11-已知限制)）
- **文件名与标题也参与匹配**：正文里压根没出现该词、但文件叫 `系统架构.pdf` 的文档同样会列出（这类结果的摘要取文件名，不带页码）
- MCP 侧（AI 通过 `search` 工具检索）与网页端行为一致：含中文走子串匹配，纯英文走 FTS5

举例：输入 `红黑` 会列出所有正文出现「红黑」的文档，每条显示命中页码和上下文。

### 5.3 wiki 页面

`/wikis/<知识库名>`（不带 `/files`）

这里是 AI 写出来的页面目录树。页面是普通 Markdown 文件，落在工作区的 `wiki/` 下，可以用任何编辑器改，也可以 git 管理。你在编辑器里改了，后台监听器会重新索引。

### 5.4 高亮与批注

打开任意文档（PDF/Word/网页/文本），选中文字即可高亮并写批注。这些内容存在 `.llmwiki/index.db` 里，**AI 通过 MCP 能看到**——所以 AI 写页面时可以参考你划过的重点和想法。

> ⚠️ `.llmwiki/index.db` 存了高亮、批注、Agent 回复、课程进度等只在索引里的数据。`llmwiki reindex` 会丢弃它们（会先自动备份）。

---

## 6. 接入 AI（MCP）

LLM Wiki 通过 MCP 暴露一组工具给 AI：`guide`、`search`、`read`、`create`、`edit`、`append`、`delete`、`lint` 等。

### 6.1 Claude Desktop / Claude Code（stdio，仅本机）

```powershell
.venv\Scripts\python.exe llmwiki mcp-config "D:\我的知识库"
```

把打印出的 JSON 贴进 `claude_desktop_config.json` 或 `.claude/settings.json`。一个工作区一个 MCP 条目。

### 6.2 局域网内的 AI 客户端（HTTP）

```
端点：http://<本机IP>:3000/mcp
鉴权：Authorization: Bearer <工作区口令>
```

任何支持 **Streamable HTTP + 自定义请求头** 的客户端都能接：Claude、Cherry Studio、LibreChat、Open WebUI、Cline/Roo 等。

### 6.3 完全离线的本地模型

仓库自带一个最小 agent，把 MCP 工具转成 Ollama 的 tool calling，不需要第三方软件：

```powershell
# 先验证 MCP 通不通（不需要模型）
.venv\Scripts\python.exe tools\wiki_agent.py --token "<口令>" --list-tools

# 让模型维护知识库
ollama pull qwen2.5:14b
.venv\Scripts\python.exe tools\wiki_agent.py --token "<口令>" --model qwen2.5:14b --verbose
```

> 工具调用（tool calling）是硬门槛。7B 级别模型通常不稳定，建议 14B 以上。

### 6.4 灵犀应答（内置 pi agent）

网页右下角的问答面板（「灵犀应答」）不是浏览器直接调模型，而是后端拉起一个 **pi** 子进程：

```
浏览器 ──SSE──► /v1/chat/stream ──spawn──► pi --mode rpc ──MCP──► 本机 MCP ──► 知识库
                                                └──► models.json 指定的模型
```

模型 Key 永远不进浏览器。pi 只被授予**只读**工具（`guide` / `list_knowledge_bases` / `search` / `read` / `ping`），
因为 pi 本质是 coding agent，默认自带的 read/bash/glob/grep 会去读它落脚的目录、拿那里的文件冒充知识库答案。

**安装**（在线机器上一次即可）：

```powershell
npm install --prefix .pi-runtime @earendil-works/pi-coding-agent
$env:LLMWIKI_PI_BIN = "node $PWD\.pi-runtime\node_modules\@earendil-works\pi-coding-agent\dist\bundle\cli.js"
```

Windows 上必须给**完整路径**（`CreateProcess` 跑不了 npm 的 `.cmd` 垫片），Linux 上可以用 bundle 相对路径。

**配置**在 `.pi-agent/`（随仓库发布，不含任何密钥）：

| 文件 | 作用 |
|---|---|
| `models.json` | 模型清单。默认内置 `deepseek`（读 `DEEPSEEK_API_KEY`）+ 一个 `local` provider 指向 `http://127.0.0.1:8000/v1`（vLLM / Ollama） |
| `mcp.json` | MCP 服务器。令牌写成 `${LLMWIKI_TOKEN}`，由后端注入，不落盘 |
| `APPEND_SYSTEM.md` | 追加给 pi 的系统提示：先 `guide`、检索用名词短语、必须给出处、检索不到就说没找到 |

验证 MCP 是否通（不需要模型）：

```powershell
$env:PI_CODING_AGENT_DIR = "$PWD\.pi-agent"
$env:LLMWIKI_TOKEN = "<工作区口令>"
node .pi-runtime\node_modules\@earendil-works\pi-coding-agent\dist\bundle\cli.js mcp list
# llmwiki: connected, 14 tools
```

**换模型**：改 `models.json` 里的 `providers.local.models[0].id` 与 `baseUrl`，再用环境变量指定默认：

```powershell
$env:LLMWIKI_PI_PROVIDER = "local"; $env:LLMWIKI_PI_MODEL = "qwen2.5-14b-instruct"
```

### 6.5 让 AI 自动维护

把这段提示词配成定时任务（客户端自带的定时/工作流，或系统计划任务）：

> 先调用 `guide` 了解工作区。找出上次运行以来新增的来源、剪藏与高亮。逐个阅读并更新 wiki：该新建页面的新建，该并入已有页面的并入，并修正受影响的交叉引用与脚注。最后调用 `lint` 并修掉它报告的问题。

---

## 7. 离线部署到 Debian 12

目标机无外网。思路：**在联网机器上构建，把成品搬过去**。

### 7.1 在 Windows 上直接打包（推荐，无需 Linux 机器）

```powershell
powershell -ExecutionPolicy Bypass -File tools\build-bundle-windows.ps1 -Out C:\Users\你\bundle
```

产物：`bundle.tar.gz`（约 114 MB）。

**为什么 Windows 上能打出 Linux 包**——四个关键点：

| 组件 | 做法 |
|---|---|
| Web 运行时 | `next.config.ts` 的 `output: "standalone"` 把前端裁成自包含目录；唯一原生产物是 Windows 版 `sharp`，项目没用 `next/image`，删掉后全是纯 JS |
| Python 依赖 | `uv pip install --python-platform x86_64-manylinux_2_28 --python-version 3.11` 按**目标环境**解析。普通 `pip download --platform` 不行：它仍用构建机的环境标记，会把 `pywin32` 之类算进来 |
| Node 运行时 | 从官方 linux tarball 只解出 `bin/node`（`bin/npm` 等是符号链接，Windows tar 建不了） |
| 可执行位 | Windows tar 全写成 `0666`，打包时用 Python 重写归档，把 `deploy.sh`/`node` 恢复成 `0755` |

打包脚本会自检：`pylib` 里不能有 `.pyd`、`.so` 必须是 ELF、Web 目录不能有 `.node`、`runtime/bin/node` 必须是 ELF。任一不满足直接报错，不会产出坏包。

### 7.2 在 Debian 12 上部署

```bash
sudo tar -xzf bundle.tar.gz -C /opt
cd /opt/bundle

sudo ./deploy.sh --check                                    # 只体检，不启动
sudo ./deploy.sh --workspace /srv/llmwiki --host 0.0.0.0 --port 3000
```

首次启动会：把依赖装进 `pylib/`（**目标机不需要 pip、不需要 venv、不需要网络**）→ 初始化工作区 → 生成口令 → 拉起三个服务。

**装成 systemd 服务：**

```bash
sudo ./deploy.sh --install-service --workspace /srv/llmwiki --host 0.0.0.0
systemctl status llmwiki
journalctl -u llmwiki -f
```

服务以 `llmwiki` 系统用户运行，口令放 `/etc/llmwiki/env`（0600），不进 `ps`。

### 7.3 有 Linux 构建机时（备选）

```bash
chmod +x tools/*.sh
./tools/build-bundle-linux.sh --out /tmp/bundle
```

### 7.4 目标机可选项

从 Debian ISO 装（离线包不含）：

| 包 | 用途 |
|---|---|
| `libreoffice-writer libreoffice-impress` | Word / PowerPoint 文本提取 |
| `default-jre-headless` | PDF 文本提取（默认 PDF 引擎 `opendataloader` 依赖 Java） |

`deploy.sh --check` 会检测 `soffice`/`java`，缺失只警告不中断。

### 7.5 硬前提

**目标机 Python 必须是 3.11**（Debian 12 默认就是）。`BUNDLE-INFO` 里记了 `python_minor`，不匹配会直接拒绝启动，避免装到一半才发现原生轮子不对。

---

## 8. 离线开发

想在离网 Debian 12 上改前端/后端，需要的是**开发包**（部署包只有构建产物，改不了）：

```powershell
powershell -ExecutionPolicy Bypass -File tools\build-devkit-windows.ps1 -Out C:\Users\你\devkit
```

产物约 324 MB，含完整源码 + Linux `node_modules`（806 MB 展开） + dev 版 `pylib` + Node 运行时。

```bash
sudo tar -xzf devkit.tar.gz -C /opt
cd /opt/devkit

./dev.sh check     # tsc --noEmit，先确认工具链正常
./dev.sh web       # next dev → http://127.0.0.1:3000
./dev.sh api       # uvicorn --reload → 127.0.0.1:8000（另开终端）
./dev.sh test      # pytest + vitest
./dev.sh shell     # 拿到一个装好环境变量的 bash
```

`next dev` 会把 `/api`、`/mcp` 反代到 8000/8100，两个都起着就是完整开发环境。

> 不要在开发机上跑 `npm install`——它会覆盖 Linux 依赖树且需要联网。加依赖要回联网机重新打开发包。

---

## 9. 本机已部署实例

| 项 | 值 |
|---|---|
| 访问地址 | <http://192.168.131.113:3000> |
| 口令 | 见 `C:\Users\zzq\Desktop\新建文件夹\.llmwiki\lan-password.txt` |
| 工作区 | `C:\Users\zzq\Desktop\新建文件夹` |
| 仓库 | `C:\Users\zzq\Desktop\知识库\llmwiki-master\llmwiki-master` |
| Python | 3.11.18（uv 托管，仓库内 `.venv`） |
| Java | `C:\Users\zzq\llmwiki-tools\jre21\jdk-21.0.13+9-jre` |
| Office 转换 | 用本机 **WPS** 的 COM 接口，经 `tools\wps-soffice\soffice.cmd` 冒充 `soffice`（LibreOffice 因无管理员权限装不上） |
| 索引结果 | 52 条文档、736 个 chunk、0 错误 |

**重启命令**（Java 和 soffice 兼容层已写入用户 PATH，新开终端即可）：

```powershell
cd C:\Users\zzq\Desktop\知识库\llmwiki-master\llmwiki-master
.venv\Scripts\python.exe llmwiki serve "C:\Users\zzq\Desktop\新建文件夹" --host 0.0.0.0
```

**改了口令后**：改 `.llmwiki\lan-password.txt` → 重启服务 → 浏览器需重新登录。

---

## 10. 故障排查

### 部署 / 启动

| 现象 | 原因与处理 |
|---|---|
| 局域网打不开页面 | ① 防火墙未放行 3000；② 用了 `127.0.0.1` 启动；③ 客户端与服务器不在同一网段 |
| `Invalid Host header` (400) | Host 不在白名单。用 `--public-url` 指定实际访问地址 |
| `Origin not allowed` (403) | 浏览器来源与 `--public-url` 不一致（含协议/端口）。或在 `LOCAL_ALLOWED_ORIGINS` 里补 |
| 一直跳回登录页 | Cookie 未生效：确认是 `http://同一主机:同一端口` 访问，且浏览器未禁 Cookie |
| 页面能开但接口全 401 | 口令不一致。检查 `.llmwiki\lan-password.txt` 与服务当前口令 |
| 首次启动很慢 | 正在执行 `next build`。属正常，后续跳过 |
| `next build` 报 `EPERM unlink .next` | 有运行中的 Next 进程锁着目录。先停服务，删 `web\.next`，再 `--rebuild` |

### 文档解析

| 现象 | 原因与处理 |
|---|---|
| PDF 只有文件名没内容 | 缺 Java。`java -version` 确认；或装 `default-jre-headless` |
| Word/PPT 只有文件名没内容 | 缺 LibreOffice。`soffice --version` 确认 |
| 解析全部失败且日志有 `unable to open database file` / `Write Error` | **临时目录不可写**。把 `TMP`/`TEMP`/`TMPDIR` 指到可写目录后重启 |
| 某文件 `status=failed` | Files 视图里点开该文档能看错误信息；修复环境后把数据库里该行 `status` 改回 `pending` 并重启，或用 `llmwiki reindex`（会丢高亮，先确认） |

### 搜索

| 现象 | 原因与处理 |
|---|---|
| 搜英文有结果、中文没有 | 若走的是 MCP（AI 调用），其 FTS5 分词器对中文无效——用网页端搜索，或给 MCP 加 `LIKE` 兜底 |
| 网页搜索无结果 | ① 未硬刷新（`Ctrl+Shift+R`）；② 关键词不足 2 字符；③ 该词确实不在任何文档正文里 |
| 点搜索结果没反应 | 已修复（`initialDocId` 同步 + `documents` 回退）。若仍复现请反馈 |

### 其他

```powershell
# 重建索引（会先备份 index.db，但会丢失高亮/批注/Agent 回复）
.venv\Scripts\python.exe llmwiki reindex "C:\...\知识库" --yes
```

---

## 11. 已知限制

1. **中文检索已修，但机制是「兜底」而非「分词」**：`chunks_fts` 的分词器仍是 `porter unicode61`，中文在其中是一个不可分的整词。因此 MCP 的 `search` 与网页 `/v1/search` 都改为：查询里含 CJK 字符时走 `LIKE` 子串匹配（多词为 AND），纯 ASCII 查询仍走 FTS5（保留英文词干化）。代价是中文查询没有词干与相关度排序，且随库增长是线性扫描；要让中文真正「按词」检索，需要给 `chunks_fts` 换 `trigram` 分词器或接 embedding（见第 3 条）。
2. **Chrome 扩展在局域网模式不可用**：它直接请求 API 且不带凭据。服务端已支持 `Bearer`，只差扩展端增加口令输入框。临时方案：用 Web UI 上传，或直接把文件放进工作区目录（后台监听器会索引）。
3. **搜索是字面匹配**，不支持语义/同义词。要"按意思找"需要接 embedding 模型（代码里预留了 `VOYAGE_API_KEY` 但未实现）。
4. **单租户**：没有按用户隔离，一个口令即整库权限。
5. **服务是前台进程**：关掉终端即停止。长期运行需自行做成 Windows 服务/计划任务，或用 Linux 的 systemd（`deploy.sh --install-service` 已支持）。

---

## 附：相对上游的改动

本项目在 [lucasastorian/llmwiki](https://github.com/lucasastorian/llmwiki) 基础上做了以下改造（上游 HEAD 为 2026-10-22 的 `9dc372f`，内容上已是同版）：

| 领域 | 改动 |
|---|---|
| 鉴权（新增） | 共享口令登录、HMAC 会话 Cookie、`/v1/files` 鉴权 |
| 局域网 | `LOCAL_*` 配置、Host/Origin 白名单化 |
| 检索（新增） | `/v1/search` 全文检索、`/v1/folders` 目录列举 |
| 中文检索（新增） | MCP `search` 与 `/v1/search` 统一：含 CJK 走 `LIKE` 子串匹配、纯 ASCII 走 FTS5；标题/文件名也参与匹配 |
| 表格预览（新增） | `web/public/zetaviewer/` 内嵌 ZetaOffice（LibreOffice WASM）渲染 xlsx/xls，带「改用表格视图」兜底；引擎启动前注入 CJK 字体 |
| 灵犀应答（新增） | `.pi-agent/` 内置 pi agent 配置（models/mcp/APPEND_SYSTEM），只读工具白名单，令牌走环境变量 |
| 表格抽取（新增） | `.xls`（BIFF）经 xlrd 抽取，上传白名单与处理类型一并放开；`spreadsheet_extract` 成为 xlsx/xls/csv 的唯一入口 |
| MCP | 可配置 host + Bearer 令牌 |
| CLI | `--host/--port/--password/--rebuild`、standalone 启动、GBK 控制台崩溃修复、SIGTERM 优雅退出 |
| 前端 | 字体本地化（`geist`）、本地登录页、相对 API 路径、中文 slug 路由修复、FilesGrid 返回按钮/空目录/内容搜索/打开文档修复 |
| 离线打包 | `tools/` 下 4 个脚本（Windows/Linux 打包、WPS→PDF 兼容层、Ollama agent）、`web/scripts/fetch-zetaoffice-runtime.mjs` 取 WASM 运行时 |
| 文档 | 本文件、`DEPLOYMENT.md`、README、`.env.example` |

> 当前副本不是 git 仓库。建议 `git init` 并把上游设为 `upstream` 远程，以便日后合并上游更新。

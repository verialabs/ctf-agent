# HuntingBlade（CTF Agent Fork）

一个面向 CTF（Capture The Flag）的自治解题代理系统。它会把同一道题同时交给多个大模型并行求解，谁先找到正确 flag，谁就赢。原项目在一个周末内搭起来，并在 **BSidesSF 2026 CTF** 中拿到了 **52/52 全解 + 第 1 名**。项目由 [Veria Labs](https://verialabs.com) 构建，团队成员来自 [.;,;.](https://ctftime.org/team/222911)（smiley），该队曾是 [CTFTime 2024 和 2025 年美国排名第 1 的 CTF 战队](https://ctftime.org/stats/2024/US)。

## 项目战绩

| 比赛 | 解出题数 | 结果 |
|------|:-------:|------|
| **BSidesSF 2026** | 52/52（100%） | **第 1 名（$1,500）** |

该系统覆盖常见 CTF 题型，包括：

- `pwn`
- `rev`
- `crypto`
- `forensics`
- `web`
- `misc`

## 它是怎么工作的

整体上分成两层：

- **Coordinator（协调器）**：负责盯盘整个比赛，检测新题、管理并发、查看各 solver 的进度、在卡住时补充策略提示。
- **Swarm（解题群）**：每道题启动一个 swarm，里面再并行跑多个模型实例，竞争寻找 flag。

```text
                        +-----------------+
                        | Competition     |
                        | Platform        |
                        | (CTFd / Lingxu) |
                        +--------+--------+
                                 |
                        +--------v--------+
                        |  Poller (5s)    |
                        +--------+--------+
                                 |
                        +--------v--------+
                        | Coordinator     |
                        | (Claude/Codex/  |
                        |  Headless)      |
                        +--------+--------+
                                 |
              +------------------+------------------+
              |                  |                  |
     +--------v--------+ +------v---------+ +------v---------+
     | Swarm:          | | Swarm:         | | Swarm:         |
     | challenge-1     | | challenge-2    | | challenge-N    |
     |                 | |                | |                |
     |  Opus (med)     | |  Opus (med)    | |                |
     |  Opus (max)     | |  Opus (max)    | |     ...        |
     |  GPT-5.4        | |  GPT-5.4       | |                |
     |  GPT-5.4-mini   | |  GPT-5.4-mini  | |                |
     |  GPT-5.3-codex  | |  GPT-5.3-codex | |                |
     +--------+--------+ +--------+-------+ +----------------+
              |                    |
     +--------v--------+  +-------v--------+
     | Docker Sandbox  |  | Docker Sandbox |
     | (isolated)      |  | (isolated)     |
     |                 |  |                |
     | pwntools, r2,   |  | pwntools, r2,  |
     | gdb, python...  |  | gdb, python... |
     +-----------------+  +----------------+
```

每个 solver 都运行在隔离的 Docker 容器里。容器内预装了大量 CTF 工具，solver 会持续尝试不同路线，直到找到正确 flag 或被其他 solver 抢先解决。

## 核心能力

- **多模型竞速**：同一题可以同时丢给多个模型。
- **多平台拉题**：协调器会从已接入竞赛平台自动发现新题并启动 swarm。
- **自动停题**：一旦题目在平台侧被确认已解，对应 swarm 会自动结束。
- **协调器调度**：在 `claude` / `codex` 模式下，顶层协调器会读取 solver trace，在 solver 卡住时发出更具体的技术提示。
- **跨 solver 共享发现**：同一道题的多个 solver 会通过消息总线互通阶段性发现。
- **Docker 沙箱隔离**：分析、利用、爆破和脚本执行都在容器里完成。
- **人工干预**：你可以在比赛进行中通过 `ctf-msg` 给协调器发送提示或指令。
- **单题/全场双模式**：既能跑整场比赛，也能单独调试某一道题。

## 默认模型编排

默认模型列表定义在 [backend/models.py](backend/models.py)：

| Model Spec | 提供方 | 说明 |
|------------|--------|------|
| `claude-sdk/claude-opus-4-6/medium` | Claude SDK | 均衡型 |
| `claude-sdk/claude-opus-4-6/max` | Claude SDK | 深度推理 |
| `codex/gpt-5.4` | Codex | 默认最强通用 solver |
| `codex/gpt-5.4-mini` | Codex | 更快，适合简单题 |
| `codex/gpt-5.3-codex` | Codex | 高推理强度 |

几点容易混淆但很重要：

- `--coordinator` 只决定“顶层协调器”怎么跑，支持 `claude`、`codex`、`none`。
- 每道题 swarm 里真正跑哪些 solver，由 `--models` 或 [backend/models.py](backend/models.py) 决定。
- `--coordinator none` 只是不再额外启一个总控大脑，并不等于自动把 solver 模型也改成 API 模型。
- 如果你本机没有 `codex` 或 Claude SDK 能力，就不要直接吃默认模型列表，应该显式传 `azure/...`、`bedrock/...`、`google/...`、`zen/...` 这类 API 型模型。

## 沙箱里有什么工具

每个 solver 都会拿到一个独立 Docker 容器。工具包括：

| 类别 | 主要工具 |
|------|----------|
| 二进制分析 | `radare2`, `gdb`, `objdump`, `binwalk`, `strings`, `readelf` |
| Pwn | `pwntools`, `ROPgadget`, `angr`, `unicorn`, `capstone` |
| Crypto | `SageMath`, `RsaCtfTool`, `z3`, `gmpy2`, `pycryptodome`, `cado-nfs` |
| 取证 | `volatility3`, `mmls`, `fls`, `icat`, `foremost`, `exiftool` |
| Stego | `steghide`, `stegseek`, `zsteg`, `ImageMagick`, `tesseract` |
| Web | `curl`, `nmap`, `requests`, `flask` |
| 其他 | `ffmpeg`, `sox`, `Pillow`, `numpy`, `scipy`, `PyTorch`, `podman` |

另外，代码里还提示可以在容器中运行：

```bash
cat /tools.txt
```

用来查看镜像里实际安装的工具列表。

## 项目目录说明

你可以先从这些目录和文件理解项目：

| 路径 | 作用 |
|------|------|
| [backend/cli.py](backend/cli.py) | CLI 入口，定义了 `ctf-solve`、`ctf-msg` 和 `ctf-import` |
| [backend/config.py](backend/config.py) | 环境变量和默认配置 |
| [backend/models.py](backend/models.py) | 默认模型列表、provider 解析、vision 支持 |
| [backend/agents/](backend/agents) | 协调器、solver、swarm 的核心逻辑 |
| [backend/platforms/](backend/platforms) | 竞赛平台抽象、平台工厂，以及 CTFd / 凌虚赛事 CTF 接入实现 |
| [backend/challenge_import.py](backend/challenge_import.py) | 手动导题逻辑，负责落盘 `metadata.yml` 和递归导入附件 |
| [backend/tools/](backend/tools) | solver 可以调用的工具封装 |
| [backend/sandbox.py](backend/sandbox.py) | Docker 容器的创建、挂载和执行逻辑 |
| [backend/ctfd.py](backend/ctfd.py) | CTFd 登录、拉题、交 flag |
| [backend/prompts.py](backend/prompts.py) | 给 solver 的系统提示词构造 |
| [pull_challenges.py](pull_challenges.py) | 手动从 CTFd 批量下载题目到本地 |
| [sandbox/](sandbox) | 沙箱镜像构建文件 |

运行时还会出现这些目录：

| 路径 | 作用 |
|------|------|
| `challenges/` | 拉下来的题目目录，默认保存位置 |
| `logs/` | 每个 solver 的 JSONL trace 日志 |

## 环境要求

- Python `3.14+`
- `uv`
- Docker
- 至少一种可用的平台接入方式：
  - CTFd 地址和访问凭据
  - 或凌虚赛事 CTF 的平台地址、赛事 ID 与有效 Cookie
- 如果要用 `codex/...` solver，或 `--coordinator codex`，需要本机能直接执行 `codex`
- 如果要用 `claude-sdk/...` solver，或 `--coordinator claude`，需要本机有 Claude SDK 对应能力
- 如果你使用 API 型模型并配合 `--coordinator none`，则不要求本机安装 Codex / Claude 作为顶层总控

- `.env` 里的环境变量会被自动读取。
- CLI 参数优先级高于 `.env`。
- 命令参数名保持英文；`--help`、README 和示例说明使用中文。
- 当前代码默认使用 Docker 沙箱，并且容器配置里包含 `SYS_PTRACE`、`SYS_ADMIN`、`/dev/loop-control` 等挂载项。

## 安装

### 1. 安装 Python 依赖

```bash
uv sync
```

### 2. 构建沙箱镜像

```bash
docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .
```

如果你打算使用别的镜像名，也可以后续在 `ctf-solve` 里通过 `--image` 指定。

### 3. 配置环境变量

先复制模板：

```bash
cp .env.example .env
```

当前代码中实际支持的主要环境变量如下：

```env
# CTFd
CTFD_URL=https://ctf.example.com
CTFD_TOKEN=ctfd_your_api_token_here
CTFD_USER=admin
CTFD_PASS=admin

# 常见模型凭据
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.masterjie.eu.cc/v1
GEMINI_API_KEY=

# 可选 provider / fallback 配置
AWS_REGION=us-east-1
AWS_BEARER_TOKEN=
AZURE_OPENAI_ENDPOINT=https://api.masterjie.eu.cc/v1
AZURE_OPENAI_API_KEY=sk-...
OPENCODE_ZEN_API_KEY=
```

建议优先使用 `CTFD_TOKEN`，其次才是 `CTFD_USER` / `CTFD_PASS` 登录方式。

如果你准备走 OpenAI 兼容网关，当前 README 示例统一按这个稳定地址写：

```env
OPENAI_BASE_URL=https://api.masterjie.eu.cc/v1
AZURE_OPENAI_ENDPOINT=https://api.masterjie.eu.cc/v1
```

它已经验证可用：

- `POST /v1/chat/completions`
- `POST /v1/responses`

因此：

- `azure/...` 这类 API 型 solver 可以直接吃这套 `.env`
- `--coordinator codex` 这类走 Codex `responses` 的路径，也可以配合本机 Codex 全局配置一起使用
- 如果你只想稳一点、不想再多占一个总控并发，优先用 `--coordinator none`

## 快速开始

这是最小可运行流程。下面给两个实用起点：

### 快速开始 A：CTFd + 无总控 + API 模型

如果你本机没有安装 Claude / Codex，或者你就是想先把整场自动调度跑通，建议从这个组合开始：

```bash
uv sync
docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .
cp .env.example .env
# 编辑 .env，填入平台凭据与 OPENAI 兼容网关密钥

uv run ctf-solve \
  --ctfd-url https://ctf.example.com \
  --ctfd-token ctfd_your_token \
  --coordinator none \
  --models azure/gpt-5.4 \
  --models azure/gpt-5.4-mini \
  --challenges-dir challenges \
  --max-challenges 3 \
  --msg-port 9400 \
  -v
```

### 快速开始 B：凌虚赛事 CTF + 无总控 + API 模型

如果你当前要打的是凌虚赛事 CTF，这条命令最贴近真实使用：

```bash
uv run ctf-solve \
  --platform lingxu-event-ctf \
  --platform-url https://ctf.yunyansec.com \
  --lingxu-event-id 198 \
  --lingxu-cookie-file .secrets/lingxu.cookie \
  --coordinator none \
  --models azure/gpt-5.4 \
  --models azure/gpt-5.4-mini \
  --challenges-dir challenges \
  --max-challenges 3 \
  --msg-port 9400 \
  --no-submit \
  -v
```

如果一切正常，协调器会：

1. 连接你指定的平台。
2. 初始化轮询器，每 5 秒检查一次新题和已解题。
3. 为当前未解题自动拉取题目数据。
4. 把题目写入 `challenges/<slug>/metadata.yml` 和 `distfiles/`。
5. 为每道题启动一个 swarm。
6. 在题目被解出后自动停止对应 swarm。

## 详细用法

### 用法 1：跑整场比赛（CTFd，推荐起手式）

如果你更在意“先稳定跑起来”，推荐直接用无总控模式：

```bash
uv run ctf-solve \
  --ctfd-url https://ctf.example.com \
  --ctfd-token ctfd_your_token \
  --coordinator none \
  --models azure/gpt-5.4 \
  --models azure/gpt-5.4-mini \
  --challenges-dir challenges \
  --max-challenges 3 \
  --msg-port 9400 \
  -v
```

说明：

- 不传 `--challenge` 时，`ctf-solve` 会进入 **协调器模式**。
- 协调器会自动发现未解题，并在容量允许时启动 swarm。
- `--coordinator none` 表示不用顶层大模型总控，只保留自动拉题、自动起 swarm、自动监控状态。
- 这个模式特别适合你不想额外占用总控并发、只想依赖 swarm 自动把整场比赛持续跑下去的场景。
- `--max-challenges 3` 表示最多同时处理 3 道题。
- 实际 solver 并发大约是：`max_challenges * 模型数`。
- 如果 `--coordinator` 是 `claude` 或 `codex`，还会额外多占用 1 个顶层协调器并发。
- 如果你的模型网关最大并发有限，例如只有 10，建议优先：
  - 减少 `--models`
  - 降低 `--max-challenges`
  - 或直接使用 `--coordinator none`
- 如果你打算人工发送提示，最好显式设置 `--msg-port 9400`；否则默认 `0` 表示随机挑一个空闲端口，只能从日志里看实际端口号。

### 用法 2：跑整场比赛（凌虚赛事 CTF）

当前凌虚接入支持范围如下：

- 仅支持“赛事 CTF”
- 仅支持 `FLAG` 模式题
- 支持环境型、外链型、附件型题目
- `check` 模式会被识别并跳过，不会启动 swarm

使用前提：

1. 先在浏览器中登录凌虚平台并进入目标赛事。
2. 导出当前站点 Cookie，至少包含 `sessionid`；如果浏览器里还有 `csrftoken`，可以一并保留，但不是必需。
3. 推荐把 Cookie 存到本地文件，例如 `.secrets/lingxu.cookie`。
4. 打开到赛事赛段页面即可，不需要先手动把每道容器题都“开启环境”。

示例：

```bash
uv run ctf-solve \
  --platform lingxu-event-ctf \
  --platform-url https://ctf.yunyansec.com \
  --lingxu-event-id 198 \
  --lingxu-cookie-file .secrets/lingxu.cookie \
  --coordinator none \
  --models azure/gpt-5.4 \
  --models azure/gpt-5.4-mini \
  --max-challenges 3 \
  --msg-port 9400 \
  --no-submit \
  -v
```

补充说明：

- `--platform` 不传时默认仍走 `ctfd`。
- `--platform-url` 是凌虚平台根地址，不是具体题目页地址。
- `--lingxu-cookie` 适合临时调试；长期使用更建议 `--lingxu-cookie-file`，避免命令历史泄露。
- 现网凌虚平台只要 `sessionid` 可用即可；浏览器里看不到 `csrftoken` 属于正常情况。
- 进入赛段页面后，程序会自动读取题单、自动拉取题面和附件。
- 对需要环境的题目，平台适配器会在求解前自动尝试准备连接信息；一般不需要你先手动逐题点“开启环境”。
- `--no-submit` 只是不真的提交 flag，不会阻止自动拉题、自动起 swarm、自动准备环境，但也不会触发平台确认后的自动释放环境。

### 用法 3：切换协调器后端

现在有三种模式：

```bash
# Claude 协调器（默认）
uv run ctf-solve --coordinator claude ...

# Codex 协调器
uv run ctf-solve --coordinator codex ...

# 无总控整场模式
uv run ctf-solve --coordinator none ...
```

补充：

- `--coordinator claude` 时，协调器默认模型是 `claude-opus-4-6`
- `--coordinator codex` 时，代码里实际默认模型是 `gpt-5.4`
- `--coordinator none` 时，不会再调用顶层协调器模型，只保留平台轮询、自动拉题、自动起 swarm、自动停题
- 即使是 `--coordinator none`，`--all-solved-policy` 自动收尾、平台确认后的环境释放、以及 `--writeup-mode` writeup 生成也都照常生效。
- `--coordinator-model` 可以覆盖默认协调器模型
- `--coordinator none` 适合：
  - 你本机没有装 Claude / Codex 作为总控
  - 你的网关并发比较紧张
  - 你不想额外占用总控并发，只想让 swarm 自动跑完整场
  - 你只想稳定自动跑整场，不追求顶层策略调度
- `--coordinator claude` / `codex` 适合：
  - 你希望有顶层大模型持续查看 trace、广播提示、动态调度

例如：

```bash
uv run ctf-solve \
  --coordinator codex \
  --coordinator-model gpt-5.4 \
  --ctfd-url https://ctf.example.com \
  --ctfd-token ctfd_your_token
```

如果你没有本机 Codex / Claude，但又想跑整场，不要只改 `--coordinator`，还要把 solver 模型一起换成 API 型：

```bash
uv run ctf-solve \
  --coordinator none \
  --models azure/gpt-5.4 \
  --models azure/gpt-5.4-mini \
  --ctfd-url https://ctf.example.com \
  --ctfd-token ctfd_your_token
```

### 用法 4：只跑单道题

适合本地调试、复现实验、研究单题策略。

```bash
uv run ctf-solve \
  --challenge challenges/example-challenge \
  --ctfd-url https://ctf.example.com \
  --ctfd-token ctfd_your_token \
  --no-submit \
  -v
```

这里有个关键前提：

- `--challenge` 接收的是一个本地题目目录
- 该目录下至少要有 `metadata.yml`
- 如果有附件，放在 `distfiles/` 子目录下

目录结构示例：

```text
challenges/example-challenge/
├── metadata.yml
└── distfiles/
    ├── chall
    └── note.txt
```

单题模式不会自动去任何平台搜索并下载这道题；你需要先准备好本地目录。

### 用法 5：手动导题到本地

当平台暂时没有自动接入、题目材料来自人工整理、或你只想快速把题目落成标准目录时，可以直接使用 `ctf-import`。

最小示例：

```bash
uv run ctf-import \
  --name "签到题" \
  --category misc \
  --description "阅读附件并找出 flag。" \
  --attachment ./downloads/task.zip \
  --output-dir ./challenges
```

带连接信息、附件目录、标签和提示的示例：

```bash
uv run ctf-import \
  --name "Web1" \
  --category web \
  --description "分析登录流程并获取管理员权限。" \
  --connection-info "http://target.example.com" \
  --attachment ./downloads/web1.tar.gz \
  --attachment-dir ./downloads/web1-assets \
  --tag login \
  --tag jwt \
  --hint "先看前端接口调用关系" \
  --output-dir ./challenges
```

导入后会生成：

- `metadata.yml`
- `distfiles/`

如果你传入 `--attachment-dir`，其中文件会被递归复制到 `distfiles/`，solver 在 prompt 中也能看到子目录文件列表。

### 用法 6：手动从 CTFd 拉题到本地

如果你想先把题目批量拉下来，再做离线调试，可以使用 [pull_challenges.py](pull_challenges.py)：

```bash
uv run python pull_challenges.py \
  --url https://ctf.example.com \
  --token ctfd_your_token \
  --output ./challenges
```

或者使用账号密码登录：

```bash
uv run python pull_challenges.py \
  --url https://ctf.example.com \
  --username myteam \
  --password s3cr3t \
  --output ./challenges
```

拉题完成后，每道题会生成：

- `metadata.yml`
- `distfiles/`

这样你就可以直接对某道题使用 `--challenge` 单独调试。

### 用法 7：运行过程中人工发消息

如果你想在比赛中途提醒协调器关注某条思路，或者告诉它“这类题都可能是同一种洞”，可以这样做：

先启动协调器：

```bash
uv run ctf-solve \
  --ctfd-url https://ctf.example.com \
  --ctfd-token ctfd_your_token \
  --coordinator codex \
  --msg-port 9400 \
  -v
```

再从另一个终端发送消息：

```bash
uv run ctf-msg --port 9400 "注意所有 web 题先检查共享的登录逻辑和 JWT 验签。"
```

默认 host 是 `127.0.0.1`。如果你不设置 `--msg-port`，协调器会随机选端口，那么你必须先从启动日志里确认实际端口号。

注意：

- 当 `--coordinator` 是 `claude` 或 `codex` 时，`ctf-msg` 会把你的提示送给顶层协调器。
- 当 `--coordinator none` 时，消息仍然能被接收并记录到事件日志里，但不会有顶层大模型替你消化和二次调度。

### 用法 8：限制参与竞速的模型

`--models` 可以重复传入多次：

```bash
uv run ctf-solve \
  --ctfd-url https://ctf.example.com \
  --ctfd-token ctfd_your_token \
  --models codex/gpt-5.4 \
  --models codex/gpt-5.4-mini \
  --models claude-sdk/claude-opus-4-6/max
```

如果你不传 `--models`，就会使用 [backend/models.py](backend/models.py) 中的默认列表。

### 用法 9：仅调试流程，不真的交 flag

```bash
uv run ctf-solve \
  --ctfd-url https://ctf.example.com \
  --ctfd-token ctfd_your_token \
  --no-submit \
  -v
```

这个模式会保留大部分真实流程，但不会真的向 CTFd 提交 flag，适合：

- 调试 prompt
- 调试模型组合
- 调试 Docker 沙箱
- 观察 solver trace

补充说明：

- `--no-submit` 不会自动释放平台环境，因为没有真实提交，也就没有平台确认成功这一步。
- `--writeup-mode confirmed` 依赖平台确认提交成功；在 dry-run / `--no-submit` 下通常不会生成 writeup。
- `--writeup-mode solved` 则按本地解题结果生成 writeup；即使 dry-run，只要 solver 真正解出题，也会落盘，并在复现备注里写明“未自动提交，需人工确认”。
- 当你同时使用 `--all-solved-policy exit` 或 `idle` 时，dry-run 模式下本地已解结果也会参与“是否全解”的判断，适合自动化回归整场流程。

### 用法 10：自动收尾、环境释放与 writeup

这几项能力现在可以单独组合使用，不依赖 `--coordinator claude` 或 `--coordinator codex`；即使使用 `--coordinator none`，也同样支持自动收尾、环境释放和 writeup。

`--all-solved-policy` 用于控制“整场题都解完之后怎么办”：

- `wait`：默认值。即使当前已全部解出，也继续轮询平台，等待新题或人工停止。
- `exit`：确认当前已无未解题、且没有活动 swarm 后，协调器立即退出。
- `idle`：先进入空闲观察期；如果持续空闲达到 `--all-solved-idle-seconds`，再退出。

`--all-solved-idle-seconds` 只在 `--all-solved-policy idle` 时生效，并且必须大于 `0`。

`--writeup-mode` 用于控制 writeup 何时生成：

- `off`：关闭 writeup。
- `confirmed`：只有平台确认 flag 正确后才生成 writeup，适合正式比赛归档。
- `solved`：只要 solver 本地解出题就生成 writeup；dry-run / `--no-submit` 也会生成，但会明确标注尚未自动提交。

`--writeup-dir` 用于指定 writeup 输出根目录；实际写入路径会按平台和赛事再拆一层，例如 `--writeup-dir writeups` 时，文件会写到 `writeups/lingxu-event-ctf-198/<challenge>.md`。如果你传 `--writeup-dir writeups/custom-root`，则最终路径会变成 `writeups/custom-root/lingxu-event-ctf-198/<challenge>.md`。

环境释放的触发条件要分清：

- 只有平台确认提交成功，且题目元信息里 `requires_env_start: true`，才会自动释放环境。
- 环境题也是在平台确认正确后才会触发自动释放，不会因为“本地看起来解出来了”就提前释放。
- `--no-submit` 不自动释放环境，因为不会产生平台确认成功结果。
- 非环境题即使提交成功，只要 `requires_env_start: false`，也不会调用自动释放。

三个贴近当前项目真实用法的示例：

正式打凌虚赛事，全部解出后立即退出，平台确认成功时自动释放环境，并把 writeup 落到独立目录：

```bash
uv run ctf-solve \
  --platform lingxu-event-ctf \
  --platform-url https://ctf.yunyansec.com \
  --lingxu-event-id 198 \
  --lingxu-cookie-file .secrets/lingxu.cookie \
  --coordinator none \
  --models azure/gpt-5.4 \
  --models azure/gpt-5.4-mini \
  --max-challenges 3 \
  --all-solved-policy exit \
  --writeup-mode confirmed \
  --writeup-dir writeups \
  --msg-port 9400 \
  -v
```

跑 CTFd 整场但不想额外占用总控并发，全部题目解完后先空闲 10 分钟再退出：

```bash
uv run ctf-solve \
  --ctfd-url https://ctf.example.com \
  --ctfd-token ctfd_your_token \
  --coordinator none \
  --models azure/gpt-5.4 \
  --models azure/gpt-5.4-mini \
  --max-challenges 3 \
  --all-solved-policy idle \
  --all-solved-idle-seconds 600 \
  --writeup-mode confirmed \
  --writeup-dir writeups/ctfd-main \
  --msg-port 9400 \
  -v
```

对凌虚赛事做整场 dry-run 回归，不提交 flag、不自动释放环境，但仍按本地解题结果生成 writeup：

```bash
uv run ctf-solve \
  --platform lingxu-event-ctf \
  --platform-url https://ctf.yunyansec.com \
  --lingxu-event-id 198 \
  --lingxu-cookie-file .secrets/lingxu.cookie \
  --coordinator none \
  --models azure/gpt-5.4-mini \
  --no-submit \
  --all-solved-policy idle \
  --all-solved-idle-seconds 300 \
  --writeup-mode solved \
  --writeup-dir tmp-writeups \
  -v
```

## 常用命令速查

```bash
# 查看主命令帮助
uv run ctf-solve --help

# 查看消息命令帮助
uv run ctf-msg --help

# 查看手动导题帮助
uv run ctf-import --help

# 构建沙箱镜像
docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .

# 跑整场（无总控，适合稳定起手）
uv run ctf-solve --ctfd-url https://ctf.example.com --ctfd-token xxx --coordinator none --models azure/gpt-5.4 --models azure/gpt-5.4-mini --msg-port 9400 -v

# 跑凌虚赛事 CTF（推荐）
uv run ctf-solve --platform lingxu-event-ctf --platform-url https://ctf.yunyansec.com --lingxu-event-id 198 --lingxu-cookie-file .secrets/lingxu.cookie --coordinator none --models azure/gpt-5.4 --models azure/gpt-5.4-mini --max-challenges 3 --msg-port 9400 --no-submit -v

# 跑单题
uv run ctf-solve --challenge challenges/example-challenge --ctfd-url https://ctf.example.com --ctfd-token xxx --no-submit -v

# 手动导题
uv run ctf-import --name "签到题" --category misc --description "阅读附件并找出 flag。" --attachment ./downloads/task.zip --output-dir ./challenges

# 手动拉题
uv run python pull_challenges.py --url https://ctf.example.com --token xxx --output ./challenges

# 给协调器发消息
uv run ctf-msg --port 9400 "检查 web 题是否复用同一套会话逻辑"
```

## 运行时会发生什么

理解这一段，对你后续二次开发非常有帮助。

### 协调器模式

入口在 [backend/cli.py](backend/cli.py)。

当你运行：

```bash
uv run ctf-solve ...
```

且没有传 `--challenge` 时，程序会：

1. 读取 `.env` 和命令行参数，构造 [Settings](backend/config.py)。
2. 计算 `max_containers = max_challenges * len(model_specs)`，用于限制容器启动并发。
3. 清理上一次异常退出残留的 `ctf-agent` 容器。
4. 启动协调器事件循环 [backend/agents/coordinator_loop.py](backend/agents/coordinator_loop.py)。
   - 如果 `--coordinator none`，这里不会再额外连接顶层 LLM，只会把事件记录到日志并自动起 swarm。
5. 通过 [backend/platforms/](backend/platforms) 中的平台工厂创建客户端，再用 [backend/poller.py](backend/poller.py) 每 5 秒轮询一次平台。
6. 对每道未解题调用 [backend/agents/coordinator_core.py](backend/agents/coordinator_core.py) 的 `do_spawn_swarm()`。
7. 如果本地没有对应题目目录，就先通过对应平台客户端自动拉题；CTFd 使用 [backend/ctfd.py](backend/ctfd.py)，凌虚赛事 CTF 使用 [backend/platforms/lingxu_event_ctf.py](backend/platforms/lingxu_event_ctf.py)。
8. 启动该题目的 `ChallengeSwarm`，让多个 solver 同时开跑。

### 单题模式

当你传入：

```bash
uv run ctf-solve --challenge challenges/example-challenge ...
```

程序会直接：

1. 读取 `metadata.yml`
2. 构造 `ChallengeSwarm`
3. 并发启动每个 solver
4. 某个 solver 成功确认 flag 后，取消其他 solver
5. 输出结果和 cost summary

### Solver 容器内部目录

每个 solver 容器里会挂载：

- `/challenge/metadata.yml`：题目元信息，只读
- `/challenge/distfiles/`：题目附件，只读
- `/challenge/workspace/`：solver 运行时工作目录，可写

注意：

- `workspace` 是临时目录，solver 停止后会被删除
- 如果你想保留某次分析产物，需要在代码中额外做导出

### 日志和 trace

每个 solver 都会把执行过程写到 `logs/trace-*.jsonl`。文件名里会带上：

- 题目名
- 模型名
- 时间戳

你可以直接这样看：

```bash
ls logs
tail -f logs/trace-*.jsonl
```

这对二次开发非常重要，因为你能据此判断：

- 模型具体调用了什么工具
- 哪一步开始循环
- 为什么没有交 flag
- bump 是否起效
- 某个模型是不是经常在同一种题上失误

## 参数说明

`ctf-solve` 当前支持的核心参数如下：

| 参数 | 作用 |
|------|------|
| `--ctfd-url` | 指定 CTFd 地址，覆盖 `.env` |
| `--ctfd-token` | 指定 CTFd API Token，覆盖 `.env` |
| `--platform` | 指定题目来源平台，支持 `ctfd` 和 `lingxu-event-ctf` |
| `--platform-url` | 平台根地址；使用凌虚赛事 CTF 时必填 |
| `--lingxu-event-id` | 凌虚赛事 ID；使用凌虚赛事 CTF 时必填 |
| `--lingxu-cookie` | 直接传入凌虚 Cookie 原文 |
| `--lingxu-cookie-file` | 从文件读取凌虚 Cookie，更适合长期使用 |
| `--image` | 指定 Docker 沙箱镜像名，默认 `ctf-sandbox` |
| `--models` | 指定 solver 模型，可重复传入 |
| `--challenge` | 指定本地单题目录，启用单题模式 |
| `--challenges-dir` | 题目保存目录，默认 `challenges` |
| `--no-submit` | 干跑，不真的提交 flag；不会触发平台确认后的自动释放环境 |
| `--coordinator-model` | 覆盖协调器模型 |
| `--coordinator` | 选择协调器后端：`claude`、`codex` 或 `none` |
| `--max-challenges` | 同时求解的题目上限 |
| `--all-solved-policy` | 全部题目已解后的收尾策略：`wait`、`exit`、`idle` |
| `--all-solved-idle-seconds` | 当 `--all-solved-policy idle` 时的空闲超时秒数，必须大于 `0` |
| `--writeup-mode` | writeup 生成策略：`off`、`confirmed`、`solved` |
| `--writeup-dir` | writeup 输出目录 |
| `--msg-port` | 协调器消息端口；`0` 表示自动分配 |
| `-v, --verbose` | 打开更详细日志 |

`ctf-msg` 支持：

| 参数 | 作用 |
|------|------|
| `MESSAGE` | 发送给协调器的消息正文 |
| `--port` | 协调器监听端口，默认 `9400` |
| `--host` | 协调器地址，默认 `127.0.0.1` |

`ctf-import` 支持：

| 参数 | 作用 |
|------|------|
| `--name` | 题目名称 |
| `--category` | 题目类型 |
| `--description` | 题目描述 |
| `--connection-info` | 连接信息，例如 URL 或 `nc host port` |
| `--attachment` | 单个附件文件，可重复传入 |
| `--attachment-dir` | 附件目录，会递归复制其中所有文件 |
| `--output-dir` | 导入后的题目根目录，默认 `challenges` |
| `--value` | 题目分值，默认 `0` |
| `--tag` | 题目标签，可重复传入 |
| `--hint` | 题目提示，可重复传入 |

## 二次开发时建议

1. [backend/cli.py](backend/cli.py)
   先弄清楚这个项目从哪里启动、有哪些运行模式。
2. [backend/agents/coordinator_loop.py](backend/agents/coordinator_loop.py)
   这是“整场比赛模式”的主循环。
3. [backend/agents/coordinator_core.py](backend/agents/coordinator_core.py)
   这里是协调器真正调用的工具逻辑。
4. [backend/agents/swarm.py](backend/agents/swarm.py)
   这里决定了多 solver 如何竞速、何时停、如何共享发现。
5. [backend/agents/codex_solver.py](backend/agents/codex_solver.py) 和 [backend/agents/claude_solver.py](backend/agents/claude_solver.py)
   这是具体 solver 的实现入口。
6. [backend/tools/](backend/tools)
   这里定义了 solver 可用能力，是扩展功能时最常改的区域。
7. [backend/prompts.py](backend/prompts.py)
   如果你想改 solver 风格、解题策略、题型偏好，先看这里。
8. [backend/sandbox.py](backend/sandbox.py)
   如果你要扩工具链、改挂载方式、保存分析产物、兼容不同平台，这里是重点。

## 常见注意事项

- `--challenge` 模式需要你本地已经有 `metadata.yml`，不是只给一个题目名字就能跑。
- 如果你想用 `ctf-msg`，最好固定 `--msg-port`，否则协调器端口是随机的。
- `--coordinator none` 只去掉顶层总控，不会自动替你把默认 solver 模型从 `codex/...` / `claude-sdk/...` 改成 API 模型。
- `--coordinator none` 适合不想占用总控并发、只靠 swarm 自动跑整场的场景；自动收尾、writeup 和平台确认后的环境释放仍然可用。
- `--all-solved-policy idle` 必须配合大于 `0` 的 `--all-solved-idle-seconds`。
- `--writeup-mode confirmed` 只有在平台确认提交成功后才会生成；`--writeup-mode solved` 则按本地解题结果生成，dry-run / `--no-submit` 也适用。
- 自动释放环境只会发生在平台确认 flag 正确且 `requires_env_start: true` 的题目上；环境题也必须等平台确认正确后才会释放。
- `--no-submit` 不自动释放环境。
- 默认模型越多，同时启动的容器就越多，CPU / 内存 / Docker 压力会明显上升。
- `distfiles` 是只读挂载，solver 生成的新文件应该写到 `/challenge/workspace/`。
- prompt 中会把 `localhost` / `127.0.0.1` 重写为 `host.docker.internal`，便于容器访问宿主机上的题目服务。
- 代码里带有 quota fallback 逻辑，但只有在你配置了对应 Bedrock / Azure / Zen 凭据时才真正有用。

## 致谢

- [es3n1n/Eruditus](https://github.com/es3n1n/Eruditus)：
  [pull_challenges.py](pull_challenges.py) 中的 CTFd 交互和 HTML 处理部分参考了该项目。

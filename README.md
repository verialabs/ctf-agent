# CTF Agent

Autonomous CTF (Capture The Flag) solver that races multiple AI models against challenges in parallel. Built in a weekend, we used it to solve all 52/52 challenges and win **1st place at BSidesSF 2026 CTF**.

Built by [Veria Labs](https://verialabs.com), founded by members of [.;,;.](https://ctftime.org/team/222911) (smiley), the [#1 US CTF team on CTFTime in 2024 and 2025](https://ctftime.org/stats/2024/US). We build AI agents that find and exploit real security vulnerabilities for large enterprises.

## Results

| Competition | Challenges Solved | Result |
|-------------|:-:|--------|
| **BSidesSF 2026** | 52/52 (100%) | **1st place ($1,500)** |

The agent solves challenges across all categories — pwn, rev, crypto, forensics, web, and misc.

## How It Works

A **coordinator** LLM manages the competition while **solver swarms** attack individual challenges. Each swarm runs multiple models simultaneously — the first to find the flag wins.

```
                        +-----------------+
                        |  CTFd Platform  |
                        +--------+--------+
                                 |
                        +--------v--------+
                        |  Poller (5s)    |
                        +--------+--------+
                                 |
                        +--------v--------+
                        | Coordinator LLM |
                        | (Claude/Codex)  |
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

Each solver runs in an isolated Docker container with CTF tools pre-installed. Solvers never give up — they keep trying different approaches until the flag is found.

## Quick Start

```bash
# Install
uv sync

# Build sandbox image
docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .

# Configure credentials
cp .env.example .env
# Edit .env with your API keys and CTFd token

# Run against a CTFd instance
uv run ctf-solve \
  --ctfd-url https://ctf.example.com \
  --ctfd-token ctfd_your_token \
  --challenges-dir challenges \
  --max-challenges 10 \
  -v
```

## Coordinator Backends

```bash
# Claude SDK coordinator (default)
uv run ctf-solve --coordinator claude ...

# Codex coordinator (GPT-5.6 Sol via JSON-RPC)
uv run ctf-solve --coordinator codex ...
```

## Solver Models

Default model lineup (configurable in `backend/models.py`):

| Model | Provider | Notes |
|-------|----------|-------|
| Claude Opus 4.6 (medium) | Claude SDK | Balanced speed/quality |
| Claude Opus 4.6 (max) | Claude SDK | Deep reasoning |
| GPT-5.6 Sol | Codex | Default Codex coordinator and solver |
| GPT-5.3-codex | Codex | Reasoning model (xhigh effort) |

DeepSeek's OpenAI-compatible API is also supported. For a lean setup that uses a
ChatGPT-authenticated Codex coordinator and two DeepSeek solvers per challenge:

```bash
export DEEPSEEK_API_KEY=your_key_here
uv run ctf-solve \
  --coordinator codex \
  --max-challenges 3 \
  --models deepseek/deepseek-v4-pro \
  --models deepseek/deepseek-flash \
  ...
```

DeepSeek thinking models are sent `tool_choice=auto`; this keeps bash, file, and
submission tools available without using the unsupported `required` mode.

Increase `--max-challenges` to 5 after validating local Docker and API capacity.

## Sandbox Tooling

Each solver gets an isolated Docker container pre-loaded with CTF tools:

| Category | Tools |
|----------|-------|
| **Binary** | radare2, GDB, objdump, binwalk, strings, readelf |
| **Pwn** | pwntools, ROPgadget, angr, unicorn, capstone |
| **Crypto** | SageMath, RsaCtfTool, z3, gmpy2, pycryptodome, cado-nfs |
| **Forensics** | volatility3, Sleuthkit (mmls/fls/icat), foremost, exiftool |
| **Stego** | steghide, stegseek, zsteg, ImageMagick, tesseract OCR |
| **Web** | curl, nmap, Python requests, flask |
| **Misc** | ffmpeg, sox, Pillow, numpy, scipy, PyTorch, podman |

## Features

- **Multi-model racing** — multiple AI models attack each challenge simultaneously
- **Auto-spawn** — new challenges detected and attacked automatically
- **Dynamic task pool** — priority queue backfills reusable challenge slots as work finishes
- **Task registry** — category, workspace path, attempts, and lifecycle state are visible centrally
- **Typed event bus** — queue/start/help/block/solve transitions are delivered consistently
- **Coordinator LLM** — reads solver traces, crafts targeted technical guidance
- **Cross-solver insights** — findings shared between models via message bus
- **Flag guardrails** — local placeholder/control-character checks plus deduplicated CTFd verification
- **Docker sandboxes** — isolated containers with full CTF tooling
- **Operator messaging** — send hints to running solvers mid-competition
- **Automatic write-ups** — confirmed solves create a redacted Markdown WP and preserve text solution files

## Automatic Write-ups

Write-up generation is enabled by default. After a solver confirms a flag, the agent writes:

```text
writeups/<category>/<challenge>/README.md
writeups/<category>/<challenge>/artifacts/...
```

The default WP contains challenge metadata, participating and winning models, solver findings,
reproduction guidance, verification state, flag, and sanitized text files from the winning
workspace. Common API key, token, cookie, and password forms are redacted.

Use `--writeups-dir PATH` to select another output directory or `--no-writeups` to disable it.

## Configuration

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```env
CTFD_URL=https://ctf.example.com
CTFD_TOKEN=ctfd_your_token
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
```

All settings can also be passed as environment variables or CLI flags.

### Ret2Shell platforms

Ret2Shell uses a different API from CTFd. Select its adapter and game explicitly:

```env
CTF_PLATFORM=ret2shell
CTFD_URL=https://ctf.example.com
RET2SHELL_TOKEN=current_bearer_session_token
RET2SHELL_GAME_ID=37
RET2SHELL_AUTO_START_INSTANCES=true
```

The same values can be selected with `--platform ret2shell --game-id 37`. The adapter
uses `/api/game/...`, downloads attachments, starts a challenge instance when required,
polls asynchronous flag submissions, handles `Set-Token` rotation in memory, and keeps
the existing coordinator/swarm interface unchanged.

### CTF+ platforms

CTF+ (`ctfplus.cn`) uses JSON endpoints and a browser token named `mario-token`.
Select the adapter and competition explicitly:

```env
CTF_PLATFORM=ctfplus
CTFD_URL=https://www.ctfplus.cn
CTFPLUS_TOKEN=current_mario_token
CTFPLUS_COMPETITION_ID=2105658865197518848
```

The same values can be selected with
`--platform ctfplus --ctfd-url https://www.ctfplus.cn --competition-id 2105658865197518848`.
The adapter polls CTF+ problem lists, downloads visible attachments, starts dynamic
containers when requested, submits flags through `challenge/submit`, and keeps the
existing coordinator/swarm interface unchanged.

## Requirements

- Python 3.14+
- Docker
- API credentials for at least one solver provider (Anthropic, OpenAI, Google, DeepSeek)
- A ChatGPT or API-key login for Codex-backed solvers/coordinators
- `codex` CLI (for Codex solver/coordinator)
- `claude` CLI (bundled with claude-agent-sdk)

The Codex executable is resolved to an absolute path before launch, including the
versioned location used by Codex Desktop on Windows. Set `CODEX_EXECUTABLE` to an
explicit executable path when using a custom installation.

## Acknowledgements

- [es3n1n/Eruditus](https://github.com/es3n1n/Eruditus) — CTFd interaction and HTML helpers in `pull_challenges.py`

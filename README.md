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

# Codex coordinator (GPT-6.1 Sol via JSON-RPC)
uv run ctf-solve --coordinator codex ...
```

## Solver Models

Default model lineup (configurable in `backend/models.py`, or per run with `--models`):

| Model | Provider | Notes |
|-------|----------|-------|
| Claude Opus 4.6 (medium) | Claude SDK | Balanced speed/quality |
| Claude Opus 4.6 (max) | Claude SDK | Deep reasoning |
| GPT-6.1 Sol | Codex | Default model of a ChatGPT-login Codex account |

A ChatGPT-login Codex only accepts the models your account lists; older ids such
as `gpt-5.4` are rejected. Pick others with e.g. `--models codex/gpt-6-astra`.

List the configured defaults and the Codex models available to your account by
passing `--models` without a value:

```bash
uv run ctf-solve --models
```

Repeat the valued form to race a custom lineup:

```bash
uv run ctf-solve \
  --models claude-sdk/claude-opus-4-6/max \
  --models codex/gpt-6-astra
```

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
- **Coordinator LLM** — reads solver traces, crafts targeted technical guidance
- **Cross-solver insights** — findings shared between models via message bus
- **Docker sandboxes** — isolated containers with full CTF tooling
- **Operator messaging** — send hints to running solvers mid-competition

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

Settings are read from, lowest to highest priority: `~/.config/ctf-agent/config.env`
(so an installed `ctf-solve` works from any directory), `./.env`, then environment variables.

### Claude and Codex commands

The `claude-sdk/*` and `codex/*` solvers run your own `claude` and `codex` commands,
so they use the same logins, settings and proxies as your terminal. No API key is
needed when both CLIs are logged in.

```env
# ~/.config/ctf-agent/config.env
CLAUDE_CLI=claude   # command on PATH or a path
CODEX_CLI=codex
```

Shell aliases are invisible to programs: if `claude` is an alias for a wrapper
(e.g. one that sets `ANTHROPIC_BASE_URL`), set `CLAUDE_CLI` to that wrapper. A
one-off override is `--claude-cli` / `--codex-cli`. The resolved commands are
printed at startup.

## Watching and talking to solvers

- **Traces:** `./logs/trace-<challenge>-<model>-<time>.jsonl` (in the directory you ran from)
  records each solver's messages (`model_response`), tool calls and results, delivered
  findings and its session id (`session`).
- **Full transcripts:** the CLIs keep their own. Claude: `~/.claude/projects/<run dir>/<session>.jsonl`,
  Codex: `~/.codex/sessions/<date>/rollout-*-<thread>.jsonl`. The ids are logged at startup.
  After a run, `<CLAUDE_CLI> --resume <session>` (from the same directory) or
  `codex resume <thread>` reopens the conversation interactively. Tools then run on
  your machine, not in the sandbox, so don't let them execute challenge files.
- **Live messages:** every run prints a port; `ctf-msg --port <port> "hint"` posts a
  message that every solver receives within about five tool calls.

## Running several challenges at once

Separate `ctf-solve --challenge ...` processes can run side by side. Each sandbox
container is labelled with the process that created it, and a starting run only
removes containers whose process is gone. Each run gets its own message port.
All runs share your Claude/Codex quotas and the Docker host's memory.

```bash
for d in /work/old_ecw/RSAd /work/old_ecw/SHAlphabet; do ctf-solve --challenge "$d" --no-submit > "$(basename $d).log" 2>&1 & done; wait
```

## Provider safety filters

A provider can stop a turn as a safety risk (e.g. OpenAI's "flagged for possible
cybersecurity risk"). Such a solver is restarted in a fresh session up to twice
(after 30s, then 60s); if it is blocked again it stops and the other models carry on.
For fewer blocks on OpenAI models, apply for OpenAI's cyber access program (the
link is in the error message).

## Requirements

- Python 3.14+
- Docker
- Logged-in `claude` CLI (for Claude solvers/coordinator) and/or `codex` CLI (for Codex)
- API keys only for the API-backed providers (Bedrock, Azure, Zen, Google)

## Acknowledgements

- [es3n1n/Eruditus](https://github.com/es3n1n/Eruditus) — CTFd interaction and HTML helpers in `pull_challenges.py`

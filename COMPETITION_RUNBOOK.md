# ctf-agent competition runbook

This project is a heavy CTF automation runner. Use it only for competitions or private sandbox challenges where you are authorized to submit flags.

## Current local paths

- Project: `C:\Users\111\Desktop\Codex-WorkSpace\CTF\ctf-agent`
- Challenge cache: `C:\Users\111\Desktop\Codex-WorkSpace\CTF\ctf-agent\challenges`
- Local env file: `C:\Users\111\Desktop\Codex-WorkSpace\CTF\ctf-agent\.env`
- Sandbox image name: `ctf-sandbox`

## Required runtime

Install these before running a real competition:

1. Python 3.14 or newer
2. uv
3. Docker Desktop with Linux containers enabled
4. Codex CLI, already detected on this machine
5. Claude CLI, already detected on this machine
6. At least one provider key in `.env`
7. CTFd URL and API token in `.env`

Current setup status on this machine:

- Python 3.14.4 installed and available as `py -3.14`
- uv installed, but existing terminals may need restart before `uv` appears in `PATH`
- Python dependencies synced into `.venv`
- `ctf-solve --help` verified through uv
- Docker Desktop is not installed yet because the installer requires Windows administrator/UAC confirmation
- WSL is not installed/enabled yet; Docker Desktop may prompt to install or enable it

## Fill `.env`

Edit `.env` and replace placeholders:

```env
CTFD_URL=https://ctf.example.com
CTFD_TOKEN=ctfd_your_api_token_here
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=
```

For Codex-first usage, set `OPENAI_API_KEY`. For the default Claude coordinator, set `ANTHROPIC_API_KEY`.

## First-time setup

Run from the project directory:

```powershell
cd C:\Users\111\Desktop\Codex-WorkSpace\CTF\ctf-agent
uv sync
docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .
```

The Docker build is large and may take a long time because it installs many CTF tools.

If `uv` is not recognized in the current terminal, restart the terminal first. In this Codex session, the installed uv executable was found at:

```powershell
C:\Users\111\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe
```

## Dry run a single local challenge

Each single challenge directory must contain `metadata.yml`.

```powershell
uv run ctf-solve --challenge challenges\some-challenge --no-submit --coordinator codex -v
```

## Run against CTFd

Start conservatively:

```powershell
uv run ctf-solve --coordinator codex --challenges-dir challenges --max-challenges 2 --no-submit -v
```

After confirming it behaves correctly, remove `--no-submit`:

```powershell
uv run ctf-solve --coordinator codex --challenges-dir challenges --max-challenges 2 -v
```

Increase `--max-challenges` only after checking CPU, memory, Docker stability, and API cost.

## Send a live hint

When the coordinator is running:

```powershell
uv run ctf-msg "Focus on the JWT kid parameter in web-auth."
```

## Pre-competition checklist

- `uv run ctf-solve --help` works
- `docker images` shows `ctf-sandbox`
- `docker run --rm ctf-sandbox python3 -c "import pwn, z3; print('ok')"` works
- `.env` contains the real CTFd URL and token
- Start with `--no-submit` for a few minutes
- Watch cost and container count before enabling full parallel solving

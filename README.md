# SAVVA Agent Runtime

SAVVA is a Python 3.12, standard-library-first, read-only computer agent. It
combines the WIP-001 tool registry with an OpenRouter model adapter, a guarded
multi-step orchestrator, and a local web interface. Ollama is implemented as an
OpenAI-compatible adapter but is never started by this WIP.

The graphical interface is a local, dependency-free HTML/CSS/JavaScript client
served by the Python backend. It requires no React build or frontend package
installation.

The default cloud model is the free
`nvidia/nemotron-3.5-lightning:free`, verified to execute registered tool calls.
`thinkingmachines/inkling-2-small:free` and `qwen/qwen3.5-9b` remain
configurable. Inkling is currently region-restricted in the development region.

## Commands

Run from the repository root:

```powershell
$env:OPENROUTER_API_KEY = "your-rotated-key"
python -m savva.cli serve
```

Open `http://127.0.0.1:8765`. The API key is read only by the Python backend,
is never returned by an endpoint, and is not written to Git, settings, or usage
logs. Do not place the key in a `.env` file or source code.

Diagnostics and tests:

```powershell
python -m savva.cli doctor
python -m savva.cli doctor --json
python -m savva.cli doctor --project "C:\path\to\project"
python -m savva.cli tools list
python -m savva.cli tools inspect system.summary
python -m unittest discover -s tests -v
```

No package installation is required for these commands.

## Agent flow

1. The user sends a message to the local Python backend.
2. `AgentOrchestrator` sends sanitized messages and registered tool schemas to
   OpenRouter.
3. The model may request several tools in one turn.
4. The orchestrator rejects unknown tools, validates arguments, checks
   permissions, executes the matching registered handler, and returns the
   structured result to the model.
5. The loop continues until a final answer or the configured tool-step,
   request, time, token, and cost limits are reached.
6. The web UI displays every tool call, its arguments, result, duration, token
   usage, and cost.

Free-model pricing is controlled by OpenRouter and can change. SAVVA records
the actual provider usage response for every model request.

## Configuration

Provider settings are available in the web UI. They are persisted without
secrets under `%LOCALAPPDATA%\SAVVA\settings.json`. Initial values can also be
set with environment variables:

| Variable | Default |
| --- | --- |
| `SAVVA_PROVIDER` | `openrouter` |
| `SAVVA_MODEL` | `nvidia/nemotron-3.5-lightning:free` |
| `SAVVA_BASE_URL` | `https://openrouter.ai/api/v1` |
| `SAVVA_MAX_TOKENS` | `2048` |
| `SAVVA_MAX_TOOL_STEPS` | `6` |
| `SAVVA_MAX_REQUESTS` | `12` |
| `SAVVA_MAX_REQUEST_COST` | `0.05` USD |
| `SAVVA_MAX_SESSION_COST` | `0.25` USD |
| `SAVVA_MAX_DURATION_SECONDS` | `120` |
| `SAVVA_WORKSPACE_ROOT` | `<repo>\workspace` |

For a future Ollama run, select `ollama` and use a loopback OpenAI-compatible
endpoint such as `http://127.0.0.1:11434/v1`. SAVVA does not install or start it.

## Registered tools

| Tool | Purpose | Permission |
| --- | --- | --- |
| `system.summary` | OS, CPU, RAM, GPU, and disk volumes | `system.read` |
| `software.detect` | Detect one known tool, or all known tools with `name=all` / no name | `system.read` |
| `development.inspect` | Core/web tooling, rule-based React + TypeScript readiness, safe-workspace dependency comparison | `system.read`, `workspace.read` |
| `workspace.list` | Bounded listing inside the safe workspace | `workspace.read` |
| `workspace.read` | Explicit UTF-8 project text reads, maximum 64 KiB | `workspace.read` |

Every result uses the same envelope:

```json
{
  "tool": "software.detect",
  "status": "success",
  "data": {
    "name": "node",
    "installed": true,
    "version": "24.22.1",
    "verified": true,
    "executable": "node.exe",
    "source": "PATH",
    "probe_error": null
  },
  "error": null,
  "duration_ms": 43
}
```

## Safety model

- Only `read_only` contracts with the implemented `system.read` or
  `workspace.read` permissions can be registered.
- `files.write`, `packages.install`, and `process.execute` are reserved
  permission names but are not executable in this WIP.
- Permission checks happen before parameter validation and handler execution.
- Handlers run behind a timeout and exception boundary. Unknown, denied,
  malformed, timed-out, and failed calls return structured errors.
- Version probes use fixed argument lists, `shell=False`, disabled stdin, output
  capture, and a three-second per-process timeout. The Ollama executable is
  located but never launched.
- Project inspection resolves one explicit directory and reads only
  `package.json`, `requirements.txt`, and `pyproject.toml`, each capped at
  1,000,000 bytes. Scripts are never executed.
- Agent workspace reads reject absolute paths, parent traversal, symlinks,
  binary files, unapproved extensions, and files larger than 64 KiB.
- Outbound model messages redact API keys, bearer tokens, passwords, private
  keys, email addresses, user-profile path components, and environment-like
  secrets.
- Usage logs contain metadata and cost only, not prompts, responses, tool
  content, or credentials.
- Final read-only answers are filtered to remove command, terminal, install,
  project-creation, and development-server instructions. The deterministic
  safe next step is to agree on a project name and template before any future
  write-enabled action.
- `Win32_Product` is not used.

Ollama, Docker, Global Memory, and other environment-dependent details are
reported as detected or unavailable; an executable on PATH is not treated as
proof that its full service stack is functional.

## Example output

The following human-readable result was produced on Windows 11. Hardware
details vary by machine.

```text
SAVVA Doctor
OS: Windows 11 (AMD64, build 26200)
CPU: AMD Ryzen AI 9 H 365 w/ Radeon 880M; cores=10; threads=20
RAM: 31.3 GiB total, 5.2 GiB available
GPU: AMD Radeon(TM) 880M Graphics

Software:
  Core:
    [OK] python: 3.12.10
    [OK] pip: 26.1
    [OK] node: 22.1
    [OK] npm: 11.19.1
    [OK] git: 2.54.0.windows.1
  Web:
    [OK] npx: 11.19.1
    [--] yarn: not found
    [--] pnpm: not found
    [--] typescript: not found
    [--] vite: not found
  Other:
    [OK] vscode: 1.143.0
    [OK] powershell: 5.1.26100.9711
    [OK] winget: 29.380
    [--] ollama: not found
    [--] docker: not found

React + TypeScript readiness: READY
  - Node.js, npm, and Git are available.

Diagnostics duration: 2812 ms
```

`doctor --json` returns the same information as standardized `ToolResult`
objects, including error codes and per-tool durations.

## Live OpenRouter verification

The acceptance prompt was sent through the real local HTTP endpoint:

```text
SAVVA, я хочу создать сайт на React. Проверь мой компьютер и скажи,
готово ли окружение
```

Observed result:

```text
Model:       nvidia/nemotron-3.5-lightning:free
Tool calls:  software.detect
Node.js:     22.1
npm:         11.19.1
Git:         2.54.0.windows.1
Python/pip:  3.12.10 / 26.1
Requests:    2
Cost:        $0.00000000
Status:      success
```

The answer reported the environment as ready, identified TypeScript and Vite
as optional missing tools, and ended with the deterministic read-only next
step. No package was installed and no project file was changed.

## Known limits

- Version checks prove that the related client command ran, not that an
  external service or full product installation is healthy.
- Ollama is detected by PATH only and is never launched.
- Registry timeouts bound the response returned to the caller. Registered
  handlers are trusted in-process code; process-level termination is reserved
  for a future executor.
- Project installation state is checked against the current Python environment
  and the selected project's `node_modules`.
- The current WIP intentionally registers no write, package-install, or
  process-execution tools. The UI includes a confirmation event surface for a
  future permission-aware executor, but no such action can currently run.

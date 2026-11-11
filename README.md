# SAVVA Agent Runtime

SAVVA is a Python 3.12, standard-library-first computer and project agent. It
combines a guarded OpenRouter model adapter, a multi-step orchestrator, a
workspace-confined project builder, and a local web interface. Filesystem writes
require a one-time approval of an exact verified proposal. Ollama is implemented
as an OpenAI-compatible adapter but is never started.

The graphical interface is a local, dependency-free HTML/CSS/JavaScript client
served by the Python backend. It requires no React build or frontend package
installation.

The default cloud model is the free
`nvidia/nemotron-3.5-lightning:free`, verified to execute registered tool calls.
`thinkingmachines/inkling-2-small:free` and `qwen/qwen3.5-9b` remain
configurable. Inkling still returns a regional `403` through the current
Netherlands exit node.

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
| `SAVVA_REQUIRE_TAILSCALE_EXIT_NODE` | `false` |
| `SAVVA_EXPECTED_EXIT_NODE` | empty |

For a future Ollama run, select `ollama` and use a loopback OpenAI-compatible
endpoint such as `http://127.0.0.1:11434/v1`. SAVVA does not install or start it.

## Tailscale model egress

Model requests inherit the Windows route of the Python backend. When Tailscale
is using an exit node, OpenRouter traffic therefore leaves through that exit
node without a system-wide configuration change made by SAVVA.

The local SAVVA settings on this machine require the online exit node
`squealing-orange`. Before every model run, the backend reads
`tailscale status --json` with fixed arguments and checks:

- Tailscale backend is `Running`;
- an exit node is selected and online;
- the selected node matches `expected_exit_node`, when configured.

If these checks fail, the request stops with
`TAILSCALE_EXIT_NODE_UNAVAILABLE` or `TAILSCALE_EXIT_NODE_MISMATCH` before any
OpenRouter call. `GET /api/network` exposes the current non-secret Tailscale
status to the UI. SAVVA never changes Tailscale, Windows routes, DNS, or system
proxy settings.

The verified current route is:

```text
Tailscale:   Running
Exit node:   squealing-orange
Node IP:     100.87.196.23
External IP: NL
OpenRouter:  HTTP 200
```

The OpenRouter free NVIDIA model works through this route. Free-model
availability and geographic restrictions remain controlled by OpenRouter.

## Registered tools

| Tool | Purpose | Permission |
| --- | --- | --- |
| `system.summary` | OS, CPU, RAM, GPU, and disk volumes | `system.read` |
| `software.detect` | Detect one known tool, or all known tools with `name=all` / no name | `system.read` |
| `development.inspect` | Core/web tooling, rule-based React + TypeScript readiness, safe-workspace dependency comparison | `system.read`, `workspace.read` |
| `project.inspect` | Bounded workspace tree; symlinks and junctions are skipped | `workspace.read` |
| `files.read` | Read an allowed UTF-8 file and return its current SHA-256 | `workspace.read` |
| `project.create` | Prepare the built-in React/TypeScript/Vite project proposal | `workspace.read` |
| `files.propose` | Prepare arbitrary allowed file changes without writing | `workspace.read` |
| `files.diff` | Return a unified diff for a proposal | `workspace.read` |
| `files.apply` | Request confirmation for an exact proposal; never auto-executes | `files.write` |
| `project.validate` | Validate project files, package configuration, catalog, and page structure | `workspace.read` |

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

- Read tools require `system.read` or `workspace.read`. The sole write tool,
  `files.apply`, requires `files.write` and is intercepted before execution.
- `packages.install` and `process.execute` remain reserved and cannot be
  registered.
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
- Project writes reject `..`, absolute and drive paths, Windows reserved names,
  trailing dots/spaces, reparse points, junctions, `.git`, `node_modules`,
  `.env*`, binary content, credential-like material, and disallowed extensions.
- Existing files require the SHA-256 returned by `files.read`. Every pending
  proposal is re-hashed immediately before apply, so concurrent changes fail
  with `STALE_PROPOSAL`.
- Confirmation tokens are random, session-bound, one-time, and tied to a
  fingerprint of exact paths and before/after hashes. Rejected, expired, used,
  or mismatched proposals never write files.
- Mutating HTTP endpoints require `X-SAVVA-Request: 1`, a loopback Host, and a
  matching same-origin header when Origin is present. The backend sends no CORS
  grants and does not log request bodies.
- Outbound model messages redact API keys, bearer tokens, passwords, private
  keys, email addresses, user-profile path components, and environment-like
  secrets.
- Usage logs contain metadata and cost only, not prompts, responses, tool
  content, or credentials.
- Model requests can be gated on a selected online Tailscale exit node. The
  gate is read-only and does not alter routing.
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
Node.js:     24.22.1
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

Node.js `22.1` in older output was a parser defect: `node --version` returns
`v24.22.1`, but the regex skipped the leading `v` and captured the suffix.
WIP-003 fixes the parser and adds a regression test.

## Controlled project builder

The write flow is:

1. The model calls `project.create` or `files.propose`.
2. SAVVA validates paths, size, content, symlink/junction boundaries, and secret
   patterns, then stores an in-memory pending proposal.
3. The UI fetches the proposal through a session-bound endpoint and displays
   every file plus a unified diff.
4. The user chooses Approve or Reject. Approval carries a random one-time token.
5. The backend re-hashes every existing target immediately before commit.
6. Files are staged and committed with rollback on failure. A second approval
   of the same proposal is rejected.

Projects and file content live under `<repo>/workspace`, which is excluded from
Git. The Projects tab provides a file tree, file viewer, proposal/diff dialog,
Approve/Reject controls, and the agent action journal.

The live acceptance created `workspace/savva-computers-store` only after model
tool calls and explicit HTTP approval:

```text
Model calls:       project.create
Files proposed:    19
Before approval:   project directory absent
Diff:              16,835 characters
After approval:    project directory with 19 files
project.validate:  valid
Model requests:    2
Cost:              $0.00
```

The generated page contains GAME, HOME, and AI cards, a responsive layout,
React components, TypeScript/Vite configuration, and no invented confirmed
prices or technical specifications. A second proposal was rejected and its
directory remained absent.

For an unattended browser acceptance run with Chrome remote debugging enabled:

```powershell
node scripts/ui_acceptance.mjs
```

The script drives the existing UI through the model request, diff review, and
approval. It requires a running SAVVA server and an API key in the server
process; neither is embedded in the script.

## Deployment on s13

SAVVA is deployed to the Tailscale Linux host `s13` under
`/home/sergey/savva-app` and runs as the lingering systemd user service
`savva.service`. The backend listens only on remote loopback
`127.0.0.1:8765`; it is not exposed directly to the Tailscale network.

The Windows launcher:

1. starts the remote user service over Tailscale SSH;
2. opens a private SSH tunnel from local `127.0.0.1:8765` to `s13`;
3. waits for the SAVVA health endpoint;
4. opens the UI in a Google Chrome app window.

```powershell
powershell.exe -NoProfile -WindowStyle Hidden `
  -ExecutionPolicy Bypass `
  -File C:\Users\Lenovo\Projects\savva\scripts\Start-SAVVA.ps1
```

The desktop shortcut `SAVVA` invokes this launcher. The remote key is stored in
`~/.config/savva/openrouter.env` with mode `0600` and is referenced by the
systemd service through `EnvironmentFile`; it is not present in the repository,
desktop shortcut, launcher, browser, or logs. `s13` uses the
`squealing-orange` Tailscale exit node for model egress.

## Known limits

- Version checks prove that the related client command ran, not that an
  external service or full product installation is healthy.
- Ollama is detected by PATH only and is never launched.
- Registry timeouts bound the response returned to the caller. Registered
  handlers are trusted in-process code; process-level termination is reserved
  for a future executor.
- Project installation state is checked against the current Python environment
  and the selected project's `node_modules`.
- Pending proposals are intentionally memory-only; restarting the server
  invalidates unapproved proposals.
- Writes are limited to UTF-8 text/config/source extensions and 1 MiB total.
- File commit uses same-directory staging and rollback. Individual filesystem
  writes cannot provide a distributed transaction across unrelated external
  processes, but all proposal targets are re-hashed immediately before commit.
- `packages.install`, `process.execute`, npm install, builds, and development
  servers remain disabled. Execution belongs to the proposed SAVVA-004 WIP.

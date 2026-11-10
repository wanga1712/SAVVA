# SAVVA 1.0 Core

SAVVA Core is a Python 3.12, standard-library-first, read-only inspection layer
for the future agent. It discovers bounded system and development information
and exposes it through a strict tool registry. It does not launch Ollama or an
LLM, install packages, modify settings, or execute arbitrary commands.

## Commands

Run from the repository root:

```powershell
python -m savva.cli doctor
python -m savva.cli doctor --json
python -m savva.cli doctor --project "C:\path\to\project"
python -m savva.cli tools list
python -m savva.cli tools inspect system.summary
python -m unittest discover -s tests -v
```

No package installation is required for these commands.

## Registered tools

| Tool | Purpose | Permission |
| --- | --- | --- |
| `system.summary` | OS, CPU, RAM, GPU, and disk volumes | `system.read` |
| `software.detect` | Detect one known tool, or all known tools when `name` is omitted | `system.read` |
| `development.inspect` | Core/web tooling, rule-based React + TypeScript readiness, optional project dependency comparison | `system.read` |

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

- Only `read_only` contracts with the implemented `system.read` permission can
  be registered.
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
  process-execution tools.

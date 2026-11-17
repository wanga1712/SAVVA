# SAVVA-004 implementation audit

Audit date: 2026-11-17. Scope: `Projects/savva` and a read-only inspection of
`Projects/Arduino_lab` because Arduino Lab is named in the acceptance scenarios.

## Repository and working state

- SAVVA: local Git branch `main`, clean working tree, no configured remote.
- SAVVA has no root or `docs/` control file named `WIP` or `WIP.md`, and no
  `AGENTS.md`. `README.md` describes WIP-003 parser fixes; no WIP-001/002
  implementation records were found in the repository.
- Arduino Lab: branch `feature/wip-001-arduino-lab-mvp`, tracking
  `origin/feature/wip-001-arduino-lab-mvp`. It contains a large set of
  untracked implementation files and task documents. `WIP-001_Arduino_Lab_MVP.md`
  is present. No changes were made there.
- SAVVA's documented test command is `python -m unittest discover -s tests -v`.
  Existing test modules cover agent/adapters, development inspection, projects,
  registry, server, software detection, and system inspection. Tests were not run
  during the initial read-only audit. The new workflow-specific tests below were
  run after implementation.

## Component map

| Component | Status | Existing implementation / evidence | SAVVA-004 gap |
| --- | --- | --- | --- |
| SAVVA-001/002/003 baseline | PARTIAL | `README.md` records controlled project proposals and WIP-003 parser fix; `savva/agent/projects.py`, `tests/test_projects.py`, `tests/test_agent.py` | No authoritative per-stage completion records for 001/002; prior work is inferred from current code and README. |
| ModelAdapter | IMPLEMENTED (single provider configuration) | `savva/agent/models.py`: `ModelAdapter`, OpenAI-compatible transport, OpenRouter and Ollama adapters | No per-role model map or provider-neutral role routing. Runtime chooses one adapter for the whole chat. |
| Agent Orchestrator | PARTIAL | `savva/agent/orchestrator.py`: tool loop, policy checks, limits, sanitized model context | Single assistant/tool loop; no development task decomposition, workflow lifecycle, repair loop, or resumable task record. |
| Tool Registry | IMPLEMENTED | `savva/core/registry.py`, `savva/core/permissions.py`, `savva/agent/tools.py` | Registry exposes no constrained project test/command tool. `process.execute` is intentionally reserved. |
| Projects API / proposal approval | IMPLEMENTED (bounded) | `savva/agent/projects.py`; `files.propose`, `files.diff`, `files.apply`, `project.validate` in `savva/agent/tools.py` | Proposals target configured SAVVA workspace; no project catalog/router for arbitrary existing repositories. Apply remains approval-gated. |
| Workspace safety | IMPLEMENTED (bounded) | `savva/agent/workspace.py`, `savva/agent/projects.py` | Existing workspace policy does not provide a separately authorized target-project root for development execution. |
| Context Builder | PARTIAL | bounded project tree and file reads in `savva/agent/projects.py`; sanitized outbound context in `savva/agent/sanitizer.py` | No role-specific context assembly from file maps, excerpts, diffs, and search results. |
| Workflow Engine and persistence | PARTIAL | New `savva/agent/workflow.py`: explicit transition graph, atomic JSON persistence, blocked/failed/cancelled states, repair attempt cap | Not wired to `SessionStore`/HTTP chat; budget is metadata and is not yet checked against usage. |
| Project Router | PARTIAL | New `ProjectRouter` in `savva/agent/workflow.py`: task type classification and explicit project registry lookup | Heuristic only; no GPT coordinator integration, dynamic project catalog, approval criteria, or route execution. |
| Windows Executor / real validation | MISSING | `savva/agent/tools.py` explicitly reserves `process.execute`; `savva/agent/orchestrator.py` forbids shell and project execution | No allowlisted Windows process runner, cwd/write boundary, real test invocation, captured test result, or rollback workflow. |
| Budget Controller | PARTIAL | `savva/agent/usage.py`, `savva/agent/config.py`; records tokens, cost, model, duration and enforces per-request/session cost, request and duration caps | Limits apply to chat sessions, not durable development tasks; no repair-attempt limit. |

## Available local tools

SAVVA provides read-only system/software/development inspection and bounded
project tree/file inspection. It can propose and diff UTF-8 file changes, apply
an exact proposal only after explicit UI approval, and structurally validate its
built-in React store template. It cannot run a project's tests or arbitrary
commands. OpenRouter and loopback Ollama share an OpenAI-compatible adapter;
the server selects one configured provider/model for the whole session.

## Safe implementation boundary

The existing proposal and approval flow is the canonical file-write mechanism
and must remain in control of changes. Arduino Lab's worktree is user data; its
untracked files and WIP documents must not be overwritten or staged as part of
SAVVA-004. Acceptance claims requiring real project tests, automatic repair, or
resume are blocked until a constrained executor and durable workflow are
implemented and exercised against a disposable test project.

## Incremental implementation and check

Added `savva/agent/workflow.py` as a foundation without changing the existing
approval path. The project router rejects unregistered project names; workflow
transitions reject illegal skips; JSON updates use a temporary file and atomic
replace; the repair counter stops at the configured maximum. This module is not
yet connected to chat or agent/model execution and must not be described as an
end-to-end development orchestrator.

Targeted check: `python -m unittest discover -s tests -p test_workflow.py -v`
passed 3 tests covering routing, persistence/transition rejection, and repair
attempt enforcement. No broader suite or acceptance scenario was run.

## SAVVA-004-FAILOVER-001 implementation status (2026-11-17)

The work is on local branch `wip/SAVVA-004-FAILOVER-001`. The original checkout had no Git remote; the GitHub connector search did not identify an accessible matching private SAVVA repository. Do not create one or push to an unrelated repository.

Implemented in this WIP:

- `savva/agent/codex_app_server.py`: ChatGPT-authenticated Codex App Server adapter, official account quota reads and quota update events, ephemeral threads, read-only sandbox, and SAVVA dynamic tools. Child process does not inherit API key/base URL environment variables.
- `savva/agent/failover.py`: quota parser across limit buckets and primary/secondary windows; failover only on confirmed quota; next-operation probing; fallback request/session/daily budget checks; provider status.
- `savva/agent/models.py`, `savva/agent/usage.py`, `savva/agent/config.py`: API failure classification, public OpenRouter pricing preflight, usage-based daily accounting, configurable limits.
- `savva/server.py`, `savva/web/app.js`: stable local chat/session restore, operation idempotency, failover metadata in chat results, pending operation handling in existing UI.
- `.gitignore`: local session/usage logs and common credential file patterns excluded.
- `tests/test_failover.py`, `tests/test_server.py`: mock quota/recovery/budget and session restart/idempotency checks.

Authorization was verified locally using `codex login status` (ChatGPT sign-in) and App Server `account/rateLimits/read`; the response exposed a Plus plan and primary/secondary quota windows. A prior real inference attempt returned no captured result, so successful real generation through App Server remains unconfirmed. No reset credits were redeemed. Automated fallback requests were mocked; API spend for tests was $0.00.

Targeted tests passed before the latest integration edits: `test_server.py` (8), `test_failover.py` (6), and `test_workflow.py` (3). The full suite and post-edit tests remain pending. Secret-history scanning, commit, and push remain pending. No merge or deployment was performed. This is an incremental failover implementation, not completion of all original SAVVA-004 development-orchestrator acceptance scenarios: arbitrary project execution and real test/repair workflow remain outside this WIP.

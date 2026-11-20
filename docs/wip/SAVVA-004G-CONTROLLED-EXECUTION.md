# SAVVA-004G — Controlled Execution Engine

**Depends on:** 004D task assignments, stable approval contract, project root ACL, resource policy  
**Scope:** fixed allowlisted Windows executors for permitted validation operations  
**Does not include:** arbitrary shell/PowerShell, unrestricted process spawning, production deploy

## Reuse and integration

Extend `ToolRegistry`, `PermissionPolicy`, `ProjectService`, and the existing approval workflow. `process.execute` is reserved in the audited checkout; do not expose it as a general-purpose model tool. The SelfDev branch executor is candidate code only and must be reviewed against this contract before selective reuse.

## Acceptance

- Executor accepts typed operation IDs and fixed structured args, never a user/model shell string.
- Working directory is an explicitly registered Candidate/project root. Resolve and re-check all paths, including reparse points, immediately before launch and write.
- Allowlisted operations have fixed executable, argument grammar, environment, timeout, output cap, process concurrency and cancellation policy.
- Capture true exit code, stdout/stderr evidence refs, duration and resource limits; distinguish tool error from failing test result.
- All writes remain approval-gated with exact path/content hash and rollback artifact; no auto-apply, push, merge or deploy.
- Tests attempt path traversal, symlink/junction escape, command injection, invalid args, timeout, output flood, parallel process, cancellation and restart/idempotency.
- E2E tests run only in disposable Candidate fixtures and prove Stable/production hashes are unchanged.

## Release gate

Do not enable any process tool for agent roles until the fixed-command catalog, permissions, resource caps, audit log, rollback, denial tests and human approval UX are accepted.

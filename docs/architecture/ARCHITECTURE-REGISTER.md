# SAVVA Architecture Register

**Register owner:** SAVVA Core maintainers
**Snapshot:** `wip/SAVVA-004-FAILOVER-003` / `40ec4d4dc0aa7a711a0e191e791169e4bb455626`
**User-provided ancestry baseline:** `4bc38f21e9985a5e4da93acb12b2a8c23f1d5c8a`

This is the index for architecture decisions, implementation dependencies, and accepted evidence. A proposal becomes accepted only when an explicit review changes its status and records the reviewer/date. Presence of an ADR or WIP document is not implementation or approval.

## Decision register

| ID | Decision | Status | Evidence / owner |
| --- | --- | --- | --- |
| ADR-001 | Core boundaries; deterministic orchestrator owns state, scoped repositories own data, supervisor is read-only | PROPOSED | [ADR-001](ADR-001-platform-boundaries.md); awaiting architecture review |
| DEC-002 | Preserve current quota failover, approval, and permission behavior during integrations | CONFIRMED CONSTRAINT | Existing FAILOVER-001–003 branch evidence and current `ToolRegistry`/`ProjectService` policy |
| DEC-003 | Do not merge divergent Bootstrap, Prompt Context, or UI branch wholesale | CONFIRMED INTEGRATION RULE | Branch comparison recorded in [Master Architecture](SAVVA-MASTER-ARCH-001.md); changes require selective integration |
| DEC-004 | Local-only profile must deny remote fallback and cloud document transfer | REQUIREMENT; DESIGN PROPOSED | User requirement; implementation acceptance belongs to 004A and 004C |
| DEC-005 | SQLite WAL + FTS5 plus separate private file vault | PROPOSED | Master Architecture; requires identity, retention, backup and deletion review |
| DEC-006 | Reputation affects ranking/review depth only; never permissions | PROPOSED SAFETY INVARIANT | Master Architecture and 004E |
| DEC-007 | Approved file changes remain exact-hash and user-confirmed | CONFIRMED CONSTRAINT | Existing proposal/confirm flow in `savva/agent/projects.py` and `savva/server.py` |

## Implementation dependency register

WIPs are acceptance-gated. Start the next WIP only after the previous required contracts and regression evidence are accepted.

| Order | WIP | Depends on | Principal reused code | Gate before next step |
| --- | --- | --- | --- | --- |
| 1 | [004A Model Registry & Dynamic Context](../wip/SAVVA-004A-MODEL-REGISTRY-CONTEXT.md) | ADR-001 review; branch-level selective integration | `models.py`, Codex adapter, failover, usage, Bootstrap/Prompt Context candidates | Verified capabilities, quotas/prices, context overflow and failover regression |
| 2 | [004B Memory Engine](../wip/SAVVA-004B-MEMORY-ENGINE.md) | 004A; identity, consent, retention, backup decisions | SessionStore and context contracts | Scope isolation, retrieval, export/delete, restart and restore |
| 3 | [004C Document Intelligence & Retrieval](../wip/SAVVA-004C-DOCUMENT-RETRIEVAL.md) | 004A/004B; approved local vault and retention policy | Workspace/project roots, ContextPack | Parser resource safety, citations, ACL, FTS5 and deletion evidence |
| 4 | [004D Multi-Agent Hierarchy](../wip/SAVVA-004D-MULTI-AGENT-HIERARCHY.md) | 004A/004B; workflow repository contract | AgentOrchestrator, WorkflowStore, FailoverEngine | Durable bounded assignments, no duplicate operations, restart and escalation |
| 5 | [004E Reputation & Governance](../wip/SAVVA-004E-REPUTATION-GOVERNANCE.md) | 004D; independent QA evidence and accepted rubric | Usage/task/approval events | Reviewer isolation, adjudication, appeal and no permission escalation |
| 6 | [004F Artifact Generation](../wip/SAVVA-004F-ARTIFACT-GENERATION.md) | 004A/004C; approval/output policy | ProjectService, registry, source refs | Correct output format, deterministic validation, approval and safe retention |
| 7 | [004G Controlled Execution](../wip/SAVVA-004G-CONTROLLED-EXECUTION.md) | 004D; project grants, approval and resource policy | ToolRegistry, PermissionPolicy, ProjectService; reviewed SelfDev candidate | Candidate-only command allowlist, path/process limits, denial tests, rollback |

The list is the delivery order requested for planning, not a claim that every WIP has a hard runtime dependency on its predecessor. 004F may be rescheduled after architecture review if artifact generation does not require completed governance; record that dependency change here before implementation. Until 004G is accepted, multi-agent work is limited to current safe read-only/proposal tools.

## Component and state ownership index

| Domain | Authoritative owner | Existing persistence | Target architecture reference |
| --- | --- | --- | --- |
| Model identity/capabilities | Model registry repository | Provider config/catalog is branch-specific | Master Architecture, 004A |
| Full conversation | SessionStore / future ConversationRepository | `sessions.json` | Master Architecture, 004B |
| Workflow task/WIP | WorkflowStore / future WorkflowRepository | Atomic JSON | Master Architecture, 004D |
| Exact proposal and decision | ProjectService + ApprovalRepository | Proposal memory + ActionJournal JSONL | Master Architecture, 004G |
| Model cost/quota | Usage/failover accounting | Usage JSONL + guarded reservations | Master Architecture, 004A |
| Personal/project/agent memory | Not implemented | None | 004B |
| Document original and chunks | Not implemented | None | 004C |
| Reputation and evaluations | Not implemented | None | 004E |
| Generated artifacts | Not implemented | None | 004F |

The full contracts and current component map are maintained in [SAVVA-MASTER-ARCH-001](SAVVA-MASTER-ARCH-001.md). Update this register whenever a decision is accepted, a WIP dependency changes, or a state owner moves.

## Verification policy

- Current regression command: `python -m unittest discover -s tests -v`.
- Architecture/document-only changes do not justify expensive live model calls or local-model loading.
- Before a data migration, attach migration, backup, restore and data-scope tests to the relevant WIP.
- Before changing WIP dependencies, run the affected contract tests and full suite if requested by the WIP; report real provider tests separately from mocks.
- No merge, push to `main`, production deploy, or authority expansion is implied by register status.

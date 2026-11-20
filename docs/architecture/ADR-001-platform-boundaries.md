# ADR-001 — SAVVA Core boundaries and authoritative state

**Status:** Proposed for architecture acceptance  
**Date:** 2026-11-20

## Context

The current application stores conversations, workflow records, usage and approvals in separate JSON/JSONL mechanisms. The model/tool loop, quota failover and user-confirmed proposal flow already work as bounded components. A unified AI platform needs additional model, memory, document, artifact and multi-agent capabilities without allowing models to own state or permissions.

## Decision proposal

1. Central Orchestrator code owns task lifecycle and dispatch. Model calls are stateless workers over a bounded request/response contract.
2. Global Chief Architect and Project Architect are model roles. They may recommend plans and decisions but cannot write task state except through orchestrator APIs.
3. Supervisor/Auditor is a separate read-only service with independent evaluation policy and append-only results. It is not a second author of the primary patch.
4. SQLite repositories own structured SAVVA records; a private file vault owns large originals and artifacts. Models and tools cannot submit raw SQL.
5. Full conversation history is authoritative and immutable except explicit user deletion/retention actions. Summaries and ContextPacks are derived, versioned views.
6. Memory, documents, projects, proposals, and artifacts are always scoped to a user and, when applicable, a project. Authorization is enforced in the repository/service, not by prompt text.
7. Existing adapters, `FailoverEngine`, `WorkflowStore`, `SessionStore`, `ToolRegistry`, `ProjectService`, and approval UI are wrapped and migrated incrementally. No parallel orchestrator, permission system, or billing ledger is introduced.
8. Existing file-approval semantics remain mandatory. Model reputation, confidence, or role cannot grant write/process/network privileges.

## Consequences

- Model selection, context assembly, storage, and execution can evolve independently behind contracts.
- Local-only use remains possible; networked models and document transfer are optional adapters guarded by consent and policy.
- SQLite is a proposed foundation, not an immediate rewrite. JSON data must have tested export/backup and migration paths before cutover.
- Multi-user operation requires a real identity source and ACL model; a session ID alone is insufficient.
- Evaluation/reputation metrics require human-reviewable evidence and cannot be self-reported as accepted quality.

## Open decisions

- User identity and project membership model.
- Data retention and deletion defaults, backup encryption, and backup destination.
- Model ranking weights and verification suite ownership.
- Reputation scoring and dispute policy.
- Artifact storage/expiry and renderer dependency policy.

## Rejected shortcuts

- Treating multiple provider APIs as multiple independent agents.
- Treating conversation transcript persistence as long-term memory.
- Sending whole documents/repositories to a prompt as document intelligence.
- Allowing the model to call arbitrary SQL, shell, or project paths.
- Letting the primary architect certify its own critical work.
- Merging branch tips that remove current failover/workflow code without a reviewed integration plan.

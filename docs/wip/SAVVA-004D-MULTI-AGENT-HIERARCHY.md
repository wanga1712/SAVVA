# SAVVA-004D — Multi-Agent Hierarchy

**Depends on:** 004A routing/context, 004B scoped memory, existing `WorkflowStore` transition contract  
**Scope:** role contracts and bounded task delegation on the Central Orchestrator  
**Does not include:** new permissions, arbitrary execution, reputation-driven privileges

## Reuse and integration

Extend existing `AgentOrchestrator`, `FailoverEngine`, `WorkflowTask`, `WorkflowStore`, and usage/budget interfaces. The orchestrator remains deterministic control-plane owner; role agents receive bounded assignments and return artifacts/evidence. Do not build a parallel agent scheduler.

## Roles

- Central Orchestrator: lifecycle, budgets, retries, idempotency and assignment.
- Global Chief Architect: cross-project standards and decomposition recommendations.
- Project Architect / Tech Lead: project-level contracts and sequencing.
- Senior/Middle/Junior: bounded implementation tasks with capability requirements.
- QA: independent test/acceptance evidence.
- Supervisor/Auditor: read-only independent review, further governed by 004E.

## Acceptance

- Each assignment has role, input/output schema, project scope, context refs, allowed tools, budget reservation, attempt cap and acceptance criteria.
- Dependencies and outputs persist; restart resumes at a safe boundary and does not duplicate a completed operation.
- Team size is minimal for task complexity; role selection is justified by verified capabilities.
- Escalation is bounded and preserves progress/diff/decisions.
- Junior or any role cannot modify accepted architecture contracts or self-approve writes.
- Unit/integration tests prove transition validity, cancellation, budget stop, failure escalation, restart and no duplicate execution.
- Until 004G passes, roles use existing read-only tools and proposal flow only.

## Deliverables

Role/assignment contracts, planner policy, orchestration changes, persistence integration plan, test evidence and a simple development-task E2E run using mocks plus a separately approved real model run.

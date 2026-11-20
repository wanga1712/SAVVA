# SAVVA-004B — Memory Engine

**Depends on:** 004A contracts and explicit identity/retention/backup decisions  
**Scope:** typed, scoped memory records and retrieval/consolidation lifecycle  
**Does not include:** document parsing, vector DB, automatic user profiling

## Reuse and integration

Keep `SessionStore` as conversation history owner until a tested `ConversationRepository` migration exists. Memory is a separate derived, user-controlled domain. Do not reinterpret `sessions.json` or summaries as verified memory.

## Acceptance

- Global, project, agent-long-term, agent-working and conversation scopes are explicit and isolated.
- Facts, hypotheses, decisions, preferences and agent experience carry source, timestamps, verification, confidence, version and retention metadata.
- Read, correction, export, deletion, opt-out and consent are available through a service/API contract.
- Project/user boundary tests prove no cross-scope retrieval, summaries or deletion.
- Consolidation records source IDs and supersession links; it never deletes original conversation evidence.
- Restart, backup and restore preserve records and access controls.
- Sensitive data filtering and cloud-transfer policy are applied before model context construction.

## Deliverables

Schema/migrations, repository, retrieval API, lifecycle service, privacy tests, migration/export/restore evidence, retention policy proposal and operator documentation.

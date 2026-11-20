# SAVVA-004A — Model Registry & Dynamic Context Manager

**Depends on:** architecture acceptance; selective review of Model Bootstrap and Prompt Context branches  
**Scope:** discovery, verified capability records, role selection, tokenizer-aware context packing  
**Does not include:** memory persistence, document parsing, autonomous teams, prompt self-optimization

## Reuse and integration

Reuse `ModelAdapter`, `OpenRouterAdapter`, `OllamaAdapter`, `CodexAppServerAdapter`, `FailoverEngine`, `ModelUsage`, `UsageLogger`, settings and the separately developed Bootstrap/Prompt Context candidate work. Integrate by reviewed file-level patches because the candidate branches diverge from failover and remove current failover/workflow files. Do not create a second provider abstraction or budget ledger.

## Acceptance

- Catalog metadata is stored separately from live capability evidence.
- A single timeout/auth failure updates availability evidence and does not erase model identity/capabilities.
- Role selection requires proven required capabilities and returns a reasoned candidate list.
- Codex subscription usage is reported as quota/tokens, not invented per-call dollar cost.
- Tokenizer-specific counts are used where available; estimates carry method/confidence and include tools, memory, documents, output reserve and safety margin.
- Calls exceeding the resolved context limit are rejected before transport; every compression/omission appears in a trace.
- Tests cover discovery, stale metadata, auth/quota/network failures, capability gates, budget block, context overflow, and current failover regression.
- OpenRouter key management keeps OS-protected secret storage, redacted status, explicit replace/delete, and never persists credentials in settings/logs.

## Deliverables

Capability schema, repository/provider adapters, selector policy, context packer, traces, tests, inventory of verified models, budgeted real checks only when separately approved, and migration note for bootstrap/context changes.

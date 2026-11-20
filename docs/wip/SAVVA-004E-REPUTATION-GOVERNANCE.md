# SAVVA-004E — Reputation & Governance

**Depends on:** 004D role/task events, independent QA evidence, accepted privacy policy  
**Scope:** explainable evaluation events, independent supervisor and bounded routing signal  
**Does not include:** autonomous permission escalation or unreviewable model self-scores

## Reuse and integration

Use append-only task/usage/approval evidence. Supervisor consumes immutable evidence references and emits separate evaluations. It cannot edit the primary result, approval state, policy, or its own prior evaluation.

## Acceptance

- Evaluation records identify evaluator, target, role/version, rubric version, evidence refs, severity, confidence, timestamp and review status.
- Separate metrics record accepted successes, found defects, missed defects, repairs, retry count, cost, latency and process violations.
- Positive/negative scoring is bounded, explainable, appealable and resistant to repeated easy-task inflation.
- Only adjudicated events affect persistent reputation. The evaluated agent cannot write reputation events about itself.
- Scores may change model ranking or review depth only, never tool permissions, approval rights or budget ceilings.
- Tests prove reviewer isolation, immutable evidence, appeals/corrections and routing policy bounds.

## Open decision before implementation

Approve the rubric, score decay/window, severity weights, appeal owner and separation of evaluator model/provider. Until then this WIP must not invent numeric weights.

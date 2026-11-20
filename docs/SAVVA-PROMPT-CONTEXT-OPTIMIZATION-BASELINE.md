# SAVVA prompt/context baseline

Base: `dd16d925786b5600b7acb5986b0cd2f95076210f` (`wip/SAVVA-MODEL-BOOTSTRAP-001`)

Captured before code changes by measuring UTF-8 text and JSON serialization. No paid generation was run for this WIP, so there is no provider-measured token baseline for an equivalent request.

| Input component | Baseline chars | UTF-8 bytes | Rough estimate (`ceil(bytes / 3)`) |
|---|---:|---:|---:|
| Global `SYSTEM_PROMPT` | 1,062 | 1,062 | 354 |
| All 10 tool schemas | 4,125 | 4,125 | 1,375 |
| Representative two-message Russian history | 153 | 221 | 74 |
| Simple answer after unconditional footer | 243 | 444 | 148 |

Configured output maximum was 2,048 tokens. Provider-reported input/output totals were available per call in usage logs, but were not separated by prompt/history/tool schema. The old simple answer before footer was 53 characters. The footer itself was 190 characters and was appended on every normal response.

The byte-divided-by-three figures are rough estimates only, not tokenizer counts. The baseline did not include a live request; therefore the requested 50% provider-token reduction and unchanged answer quality still require a controlled, budget-approved live comparison.

## Post-change static measurement

For a fresh short CHAT request (`What do you know about yourself?`), the assembled system message is 402 characters / 402 UTF-8 bytes (estimated 134 tokens), tool schemas are 0 characters / 0 schemas, selected history is 0, and output is capped at 160 tokens. These are local assembly measurements, not provider usage. A post-change generation response size and quality have not been measured against a live model.

## New routing behavior

- CHAT uses the versioned short prompt, zero tool schemas, bounded recent history, and a 160-token output cap for short requests.
- DEVELOPMENT and ACTION use separate prompts and selected existing read-only/proposal tool schemas.
- Document/presentation requests that the current application does not implement stop with an explicit unsupported response.
- Model-facing excerpts are transient; SessionStore retains the full conversation history.
- Component estimates are labeled `ESTIMATED`; provider totals remain separate. Cost is marked verified only when OpenRouter returns a numeric `usage.cost`.

# SAVVA-INCIDENT-001 — Failover forensics

**Captured:** 2026-11-20 10:03 UTC and follow-up checks
**Forensic branch base:** `wip/SAVVA-004-FAILOVER-003` at `40ec4d4dc0aa7a711a0e191e791169e4bb455626`
**Currently running backend commit:** NOT IDENTIFIABLE — no SAVVA backend process/listener was running during inspection.
**Report classification:** Confirmed present-state failure + confirmed code-path defects; historical first-error cause remains uncorrelated because no current backend logs/session file exist.

## Executive finding

Two separate failures are visible in the SAVVA browser page: `MODEL_CONNECTION_ERROR: The model provider could not be reached.` and `undefined: Failed to fetch`. The current API backend is stopped/unavailable, so the second error is explained by a refused local fetch. The earlier model error is reproducible in the provider transport, but its exact provider/request/time cannot be recovered from current evidence.

The code has a confirmed failover gap: `FailoverEngine.complete()` advances to OpenRouter only for confirmed primary quota exhaustion. A transient primary disconnect is re-raised without calling fallback. A local mock reproduced `MODEL_CONNECTION_ERROR` with `fallback_calls=0`. A second confirmed latch exists: a persisted `BLOCKED` session throws before calling any executor. These are code-level reproductions, not proof that either was the historical cause of the two browser messages.

OpenRouter has working public network reachability, but no active API key was found in process/user/machine environment. The checked-out `AppRuntime` reads only `OPENROUTER_API_KEY`; its settings file contains no key field. Therefore an authenticated fallback request cannot currently be made by this checkout. Ollama is absent, so there is no local model fallback. No deterministic Emergency Core exists in this checkout.

## Evidence and timeline

| Time (UTC) | Probe / event | Result | Correlation / limit |
| --- | --- | --- | --- |
| 2026-11-20, approximately 10:03 | Chrome window `SAVVA Agent`, URL `http://127.0.0.1:8765/` | Existing conversation DOM contains the two reported error strings and shows `Agent ready` | Page is stale relative to the stopped API. The messages have no visible timestamp or request id. Browser automation was stopped by physical Escape after inspection; no new message was submitted. |
| 2026-11-20, 10:03 | `GET http://127.0.0.1:8765/api/health` | Connection refused (`WebException`) | Confirms no backend on the configured browser origin at probe time. |
| 2026-11-20, 10:03 | Listener/process check | No Python process matching SAVVA; ports 8765 and 8768 have no listeners | There is no running process from which to extract a commit SHA or live configuration. Current repository checkout is not evidence of the commit that served the earlier page. |
| 2026-11-20, 10:03 | `codex login status` | PASS — ChatGPT login recognized | Read-only account check. |
| 2026-11-20, 10:03 | Codex App Server `account/rateLimits/read` | PASS; `ordinaryUsageAllowed=true`, status AVAILABLE; two windows, primary 0%, secondary 9% | The App Server process was started by the diagnostic adapter and closed afterward. |
| 2026-11-20, 10:03 | One direct Codex App Server turn | Completed with `SAVVA-DIAGNOSTIC-OK.`; input 13,685, output 15 | Direct adapter test, not through the running SAVVA backend. It consumed ChatGPT subscription quota. Configured model label was `codex-default`; actual server-selected model id was not returned by this adapter. |
| 2026-11-20, 10:03 | Tailscale status and saved settings | Backend Running; selected exit peer online; `RouteAll=true`; saved `require_tailscale_exit_node=true`; configured expected node matched | Current Tailscale preflight would pass. Hostname and address omitted from this report. This does not establish what route/config existed when the browser errors occurred. |
| 2026-11-20, 10:03 | OpenRouter network | DNS resolved; TCP 443 passed; HTTPS `GET /api/v1/models` returned 200 | TLS and public catalog path work through current network route. This is not authenticated key or inference verification. |
| 2026-11-20, 10:03 | OpenRouter key lookup | No process, user, or machine `OPENROUTER_API_KEY`; saved settings provider is OpenRouter, model `nvidia/nemotron-3.5-lightning:free`, and no key field | `/api/v1/key` was not called because no key was available. No value was read or logged. The active checked-out server code has no protected-key-store path. |
| 2026-11-20, 10:03 | Local runtime | `ollama` executable not found; loopback TCP 11434 closed; memory was sufficient for small local checks | Local model is not installed/available. No model was loaded or downloaded. |
| 2026-11-20, 10:03 | Local SAVVA metadata | Usage and action logs were stale; `sessions.json` absent | No current failure record or session state exists for correlating the browser messages. Request identifiers and exact timestamps are omitted. |

No paid OpenRouter inference was sent. The direct Codex turn was a real subscription-quota request; no API dollar charge was reported by that adapter.

## Reproduced failures and code locations

### `MODEL_CONNECTION_ERROR`

- `savva/agent/models.py:105-157`: `UrllibJsonTransport.post_json()` translates `URLError`, `TimeoutError`, or `OSError` to `ModelAdapterError(code="MODEL_CONNECTION_ERROR")`.
- Reproduction: one `OllamaAdapter` request to unreachable loopback `http://127.0.0.1:9/v1` returned exactly `MODEL_CONNECTION_ERROR`. This is a controlled local transport fault, not an OpenRouter or Codex inference attempt.
- `savva/agent/failover.py:171-181`: primary `ModelAdapterError` triggers fallback only if `failure_kind == "quota"` and an exhausted quota snapshot is confirmed. A transient/disconnected primary is raised immediately.
- Mock reproduction using a transient Codex-primary error: primary call count 1; OpenRouter fallback call count 0; state remains `PRIMARY_ACTIVE`.
- Historical attribution is unconfirmed: the browser message lacks request id/time, the usage log has no matching failure, and the backend process is gone. The shown code is the confirmed present implementation path, not proof of the exact older version that emitted that message.

### `undefined: Failed to fetch`

- The current browser displays this string while the local backend port refuses connections.
- `savva/web/app.js:42-57` calls `fetch()` and only assigns `error.code` for an HTTP response. A browser network `TypeError` has no `.code`.
- `savva/web/app.js:579-614` renders `${error.code}: ${error.message}` without a fallback code, producing `undefined: Failed to fetch`.
- This browser error is downstream of backend unavailability; it is distinct from an HTTP 502 model-provider error.
- Why the backend process stopped is UNKNOWN. There is no current SAVVA process, service installation was not attempted, and no crash log was available in the collected SAVVA usage/session files.

## Other route/state findings

- `savva/server.py:208-211` runs Tailscale inspection and `_validate_egress()` before creating/calling the primary Codex adapter. If the configured exit node is missing or mismatched, the request is rejected before Codex and no model failover occurs. Current saved expected node matches the online selected node, so this is not the present blocker.
- `savva/server.py:173-177` obtains OpenRouter credentials only from the process environment. The current checkout cannot read the key stored by the separate Bootstrap branch. This explains the user's report that the key was not persisted for this runtime; it does not prove the old browser error was caused by missing key.
- `savva/agent/failover.py:149-150` makes `BLOCKED` terminal for every subsequent `complete()` call. `savva/server.py:275-282` persists the adapter state after a request error. A mock initialized with `BLOCKED` returned `FAILOVER_BUDGET_BLOCKED` with both primary and fallback call counts zero.
- `sessions.json` is currently absent, so a `BLOCKED` latch is not evidenced as the cause of this incident. The behavior is a confirmed recovery defect and must be repaired.
- The checked-out failover implementation has two model levels only (Codex and OpenRouter). It has no local LLM stage and no deterministic emergency response path.

## Failure sequence (reconstructed with confidence labels)

1. **Observed in browser DOM, historical time unknown:** user request was followed by a `MODEL_CONNECTION_ERROR` response. The exact provider, request id and backend commit cannot be recovered.
2. **Observed in browser DOM, historical time unknown:** a later request/action displayed `undefined: Failed to fetch`.
3. **Confirmed at forensic probe time:** backend port 8765 had no listener, and `/api/health` failed before HTTP. The browser's stale `Agent ready` status did not reflect current backend health.
4. **Confirmed code path:** browser transport errors have no `error.code`; UI prints `undefined`.
5. **Confirmed mock path:** transient primary connection failures do not invoke OpenRouter; the fallback transition predicate accepts confirmed quota exhaustion only.
6. **Current provider availability:** standalone Codex is authorized, quota AVAILABLE, and completed a short direct turn. OpenRouter public network is reachable but no API key is configured. Ollama is unavailable.

The event sequence between steps 1 and 2, any retry, and the reason the backend exited remain **unconfirmed**. The visible browser strings cannot be correlated with Nov 10 JSONL records or an exact server commit.

## Minimum reproduction steps

1. With SAVVA backend stopped on `127.0.0.1:8765`, open the already loaded SAVVA page and trigger a chat request: browser fetch rejects; current `app.js` renders `undefined: Failed to fetch`.
2. Configure an OpenAI-compatible adapter to an unreachable loopback endpoint (for example port 9) and issue one mock diagnostic call: transport returns `MODEL_CONNECTION_ERROR`.
3. Pass that transient error from a mock Codex primary through `FailoverEngine`: observe fallback call count 0.
4. Recreate an engine with persisted state `BLOCKED`: observe `FAILOVER_BUDGET_BLOCKED` before primary/fallback calls.

These steps reproduce the error-handling and routing mechanisms without consuming provider API credits. The direct Codex availability check above was performed separately and consumed subscription quota.

## Phase 0 disposition

- **Confirmed:** no backend currently running; browser error formatting defect; transient-primary failover gap; terminal persisted `BLOCKED`; OpenRouter key not available to the current runtime; local Ollama unavailable.
- **Not confirmed:** historical backend commit; exact prior provider for the browser's `MODEL_CONNECTION_ERROR`; why backend stopped; correlation ids/timestamps for the old UI messages; authenticated OpenRouter status.
- **Tailscale as cause:** not supported by current evidence. Current configured exit node is online and matches expected name, but historical route state is unavailable.

No source code was changed during this phase. Fixes belong to the new `wip/SAVVA-EMERGENCY-001` branch based on FAILOVER-003; architecture and other WIP branches are not merged.

## Hotfix restore-service verification — 2026-11-21

**Branch/base:** `wip/SAVVA-EMERGENCY-001`, based on `0de04cefa4685633f2c3cff5c1b4119a2dcd3b67`.

### Confirmed settings defects and repair

- `SettingsStore._coerce_setting()` converted `failover_daily_budget_usd` to a string. Settings validation then raised `TypeError: '<=' not supported between instances of 'int' and 'str'`, which the HTTP handler masked as `500 INTERNAL_ERROR`.
- `failover_enabled` was also converted to the string `"False"`; because non-empty strings are truthy, disabling failover left Codex selected. Persisted legacy strings were not normalized on load.
- The hotfix normalizes both types on update and load. Regression tests cover numeric input, boolean toggling, persistence, and legacy settings.

### Live checks

- Main backend started from this checkout on loopback port 8765. `/api/health` and `/api/settings` returned successfully; UI and `/app.js` returned HTTP 200.
- A short request through SAVVA completed with provider `codex_chatgpt`, actual App Server model `gpt-6-luna`, and answer `SAVVA-CODEX-E2E-OK.`.
- Current Tailscale route gate passed. DNS and TCP 443 to OpenRouter passed; HTTPS `/api/v1/models` returned 200.
- SAVVA's `/api/openrouter/key-status` returned authenticated, source `windows_dpapi`, and `is_management_key=false`. The service exposed no key value. The API returned null for limit and remaining balance.
- The configured model catalog entry `nvidia/nemotron-3.5-lightning:free` reported prompt/completion price 0. One short chat through SAVVA returned `SAVVA-OPENROUTER-E2E-OK`; SAVVA recorded cost `$0.00`. The original `failover_enabled=true` setting was restored and verified.
- With the main backend stopped, `python -m savva.emergency doctor` independently reported backend unavailable, Codex authenticated/quota available, OpenRouter authenticated via DPAPI, local Ollama missing, and Tailscale ready. The main backend was restarted afterward.

### Acceptance still blocked

- Codex was available during the live run, so no genuine Codex failure occurred. A real automatic Codex-to-OpenRouter transition was not forced; the existing mock failover tests cover the classified auth/disconnection routes.
- Interactive browser verification was not completed. The installed browser automation launcher was rejected by the command policy, and no screen automation was used. HTTP delivery and direct SAVVA chat routes were verified, but the browser display of the actual provider has not been confirmed.
- Therefore this hotfix restores both provider routes at API level, but WIP acceptance remains `BLOCKED` until interactive browser acceptance and real automatic failover acceptance are completed.

# SAVVA-004F — Artifact Generation

**Depends on:** 004A capability contracts, 004C source/document rules, existing project approval service  
**Scope:** explicit format-specific writers and artifact validation  
**Does not include:** silent format substitution, production publishing, unapproved project writes

## Reuse and integration

Use project roots, proposal/hash verification, tool policy, and artifact source refs. The built-in React template is a narrow project proposal, not a general artifact generator. Keep writers as format adapters behind one typed request/result contract.

## Acceptance

- Explicit writers for DOCX, XLSX, PDF, PPTX and approved text/web outputs; unsupported formats return BLOCKED.
- Each writer records template/version, inputs/source refs, output hash, destination scope, and validation/render evidence.
- XLSX formulas/macros and document embedded code are never executed by QA.
- Writes require the existing exact-diff/hash approval policy or a separately approved artifact policy.
- Tests verify file signatures/openability, required content, correct output format, layout/render smoke, path confinement, overwrite protection and cleanup.
- Dependency additions are justified and approved in the WIP; no large package install occurs without the accepted dependency process.

## Deliverables

Writer interfaces, per-format modules, safe templates, output validation, test fixtures, artifact retention/export contract and UI handoff.

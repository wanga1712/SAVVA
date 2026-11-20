# SAVVA-004C — Document Intelligence & Retrieval

**Depends on:** 004A context/source contracts, 004B scope/retention policy, approved local storage design  
**Scope:** local ingestion, parsers, source-aware chunks, SQLite FTS5 retrieval  
**Does not include:** OCR, macros/formula execution, cloud upload by default, vector store

## Reuse and integration

Use the existing confined workspace/project roots and ContextPack contract. No document parser or FTS index is present in the audited checkout. Keep original bytes, extracted text, chunks/index and source references as separate records. Do not pass a complete document to an LLM as a substitute for retrieval.

## Acceptance

- Initial formats: PDF, DOCX, XLSX, PPTX, TXT, CSV, JSON, and allowlisted source text formats.
- Ingestion validates type/size, hashes/deduplicates, applies decompressed-size and nesting limits, bounded worker concurrency, timeout and memory policy, and handles corrupted/encrypted files without crashing chat.
- Macros, formulas, scripts, links and embedded code are never executed.
- Chunks carry immutable document version/hash plus exact page/slide/sheet/cell-range/line spans and extractor version.
- SQLite FTS5 retrieval supports user/project/document/time/type/access filters and returns verifiable citations.
- Deletion removes original, versions/chunks/index entries according to accepted retention policy and is idempotent.
- Tests cover malformed files, archive bombs, resource limits, encrypted files, duplicate imports, citation fidelity, access isolation and deletion.
- Original bytes stay local; any cloud excerpt requires explicit scoped consent and outbound sanitization.

## Deliverables

Ingestion job interface, parser adapters, file vault, DB migrations, FTS5 retriever, source citation API/UI contract, retention/cleanup service and acceptance corpus with expected source locations.

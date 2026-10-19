# Structured ingestion and catalog routing

The pipeline adds Docling and its HybridChunker to the existing LightRAG/PostgreSQL/Ollama stack. Parsing, OCR, layout analysis, and chunking happen during ingestion. Queries reuse stored chunks; they do not rerun the parser. No LlamaIndex, LlamaParse cloud, or additional database is required.

## Upgrade existing data

1. Stop the API and worker for the schema upgrade; install the frozen dependencies.
2. Install Tesseract and the required language data. Run `cd backend && uv run rag-prepare-parser` to provision local PDF layout/table models. Docker builds provision them in the image.
3. Run `uv run alembic upgrade head` using the deployment's migration credential.
4. Restart the services. Use **Reindex** on existing documents, which creates a new version and isolated build using the current parser profile. Existing ready documents remain manually queryable until they are reindexed; their empty legacy catalogs do not participate in automatic routing. Reindexing makes that document unavailable until its new active version is ready, as with the existing version workflow.
5. Wait for the ingestion queue, run manual and automatic routing evaluation, and review quality before wider use.

The migration adds version-scoped catalog/provenance fields and a generated, GIN-indexed `tsvector`, plus candidate IDs and stage timings on query records. It does not parse or rewrite existing documents inside the schema migration. Tenant FORCE RLS and runtime database privileges are unchanged.

## Ingestion and provenance

PDF/DOCX/Markdown conversion retains structure in a Docling document; TXT becomes a literal text block. HybridChunker respects headings and repeats table headers when splitting tables. It also emits heading-only sections so a sparse OCR page classified as a heading is not silently discarded. Provenance includes heading paths and PDF page numbers where available. DOCX and Markdown do not claim stable page numbers.

Chunks use a 1,000-token `cl100k_base` budget by default, matching the existing LightRAG token estimator. This is a budget estimate, not the Qwen tokenizer. Embedding and generation context limits remain independently bounded. There is no fixed overlap added to structural chunks.

The LightRAG adapter injects the precomputed chunks through the explicit `C` pipeline selector, with document ID and `document:version` source path. It preserves pipeline status, graph extraction, embeddings, retries, and publication checks. It does not use the deprecated custom-chunks API that omits the required file path. Application provenance is keyed by SHA256 of normalized chunk content and joined to retrieved evidence before token truncation.

Each immutable source has a private `.parsed.json` cache keyed by source checksum, parser dependency versions, revision, chunk size, OCR languages, and text limits. Cached values contain parsed text, chunks, and provenance, so they are sensitive derived data. A retry reuses them; a profile/source change creates a different cache. The worker verifies the original checksum on each attempt. Document purge removes caches as well as every source/index version after the full parser/runtime grace period. Backups of the source directory include the cache and inherit the same retention policy.

## Routing and limits

`select_documents()` queries the active ready version, with tenant, user/group/all ACL, deletion, and retention/legal-hold conditions applied in SQL before relevance ranking and LIMIT. An OR query of normalized question terms uses PostgreSQL's `simple` configuration, which is language-neutral lexical tokenization, with a small Indonesian/English question-word stoplist. Title matches receive a larger weight. Default automatic candidate count is five and can be configured up to the manual cap of 20. Permission/version revalidation still runs around generation. Routing never grants access.

The catalog contains all heading context up to 20,000 characters plus excerpts spread across chunks, bounded around 180,000 characters per version to control index size. A keyword outside those excerpts can be missed. Synonyms, stemming, translations, and semantic catalog matching are not implemented in this first stage. A manually selected document bypasses catalog matching but must pass the same access/version checks. Zero candidates means abstention; no arbitrary documents are selected as a fallback.

Graph retrieval remains scoped to each document workspace. This change does not merge graphs across documents or establish corpus-scale capacity.

## Verification

`uv run pytest -m 'not integration and not live'` verifies structural Markdown/DOCX chunking, table context, cache reuse/invalidation, source identity, provenance and evaluation metrics. PostgreSQL integration tests cover private hits outnumbering the candidate limit, group access, tenant isolation, expiry, deleted/inactive versions, no-match abstention, and permission revocation during generation. `uv run pytest -m live` additionally needs prepared parser artifacts and Tesseract for digital PDF and scanned-PDF OCR checks.

`python scripts/smoke.py --auto-routing --mode mix` uses real LightRAG, PostgreSQL and Ollama. Its report records upload-to-ready time, query stage timings, headings/pages, and explicit plus automatic unauthorized-access checks. Compare old and new profiles on the same corpus/questions; this report alone is not a performance or company-quality acceptance.

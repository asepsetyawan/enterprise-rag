"""One-operation SDK process. Per-tenant credentials never share LightRAG's global pool."""
import asyncio
import json
import logging
import os
import sys
import uuid
from contextlib import redirect_stdout

from .config import settings
from .storage import extract_text, validate_workspace


def tenant_store(tenant):
    cfg = settings()
    stores = json.loads(cfg.tenant_stores_file.read_text())
    store = dict(stores[str(uuid.UUID(str(tenant)))])
    if cfg.tenant_db_host:
        store["host"] = cfg.tenant_db_host
    if cfg.tenant_db_port:
        store["port"] = cfg.tenant_db_port
    return store


def validate_embedding_configuration(configuration, cfg):
    if (configuration.get("embedding_model", cfg.embedding_model) != cfg.embedding_model
            or configuration.get("embedding_dim", cfg.embedding_dim) != cfg.embedding_dim
            or configuration.get("embedding_digest", "") != cfg.embedding_digest):
        raise ValueError("Embedding artifact changed; reindex required")


async def execute(payload):
    cfg = settings()
    if payload["operation"] == "parse":
        if payload.get("configuration", {}).get("parser"):
            from .parsing import parse_document
            return parse_document(payload["relative_path"], payload["configuration"]["parser"])
        return {"text": extract_text(payload["relative_path"])}
    tenant = uuid.UUID(payload["tenant_id"])
    document = uuid.UUID(payload["document_id"])
    workspace = payload["workspace"]
    validate_workspace(workspace, tenant, document)
    store = tenant_store(tenant)
    if payload["operation"] == "purge":
        import psycopg
        from psycopg import sql
        with psycopg.connect(**store) as connection:
            tables = connection.execute("SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='workspace' AND table_name LIKE 'lightrag_%' ORDER BY table_name").fetchall()
            for (table,) in tables:
                connection.execute(sql.SQL("DELETE FROM {} WHERE workspace=%s").format(sql.Identifier(table)), [workspace])
        return {"purged": True}
    configuration = payload.get("configuration", {})
    validate_embedding_configuration(configuration, cfg)
    env = {"POSTGRES_HOST": store["host"], "POSTGRES_PORT": str(store["port"]),
           "POSTGRES_USER": store["user"], "POSTGRES_PASSWORD": store["password"],
           "POSTGRES_DATABASE": store["dbname"], "POSTGRES_VECTOR_INDEX_TYPE": "",
           "POSTGRES_SSL_MODE": store.get("sslmode", "disable"),
           "POSTGRES_CONNECTION_RETRIES": "3", "POSTGRES_CONNECTION_RETRY_BACKOFF": "1",
           "POSTGRES_CONNECTION_RETRY_BACKOFF_MAX": "5", "POSTGRES_MAX_CONNECTIONS": "4",
           "TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "1"}
    os.environ.update(env)
    for name in ("sslrootcert", "sslcert", "sslkey"):
        if store.get(name):
            os.environ[{"sslrootcert": "POSTGRES_SSL_ROOT_CERT", "sslcert": "POSTGRES_SSL_CERT", "sslkey": "POSTGRES_SSL_KEY"}[name]] = store[name]
    import httpx
    import numpy as np
    from lightrag import LightRAG, QueryParam
    from lightrag.kg.shared_storage import initialize_share_data
    from lightrag.utils import EmbeddingFunc
    logging.getLogger("lightrag").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.ERROR)
    initialize_share_data(workers=1)
    headers = {"X-Internal-Token": cfg.internal_token.get_secret_value()}
    async with httpx.AsyncClient(base_url=cfg.gateway_url, headers=headers, timeout=cfg.model_timeout + 60,
                                  trust_env=False) as client:
        async def llm(prompt, system_prompt=None, history_messages=None, **kwargs):
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages += history_messages or []
            messages.append({"role": "user", "content": prompt})
            format_ = kwargs.get("format")
            response_format = kwargs.get("response_format")
            if response_format and not format_:
                format_ = "json" if response_format.get("type") == "json_object" else response_format.get("json_schema", {}).get("schema")
            response = await client.post("/internal/chat", json={"messages": messages, "format": format_, "purpose": "extract"})
            response.raise_for_status()
            return response.json()["content"]

        async def embed(texts):
            response = await client.post("/internal/embed", json={"texts": texts})
            response.raise_for_status()
            return np.array(response.json()["vectors"], dtype=np.float32)

        chunk_size = configuration.get("chunk_size", cfg.chunk_size)
        overlap = configuration.get("chunk_overlap", cfg.chunk_overlap)
        directory = cfg.runtime_root / tenant.hex / workspace
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        rag = LightRAG(working_dir=str(directory), workspace=workspace,
                       kv_storage="PGKVStorage", vector_storage="PGVectorStorage",
                       graph_storage="PGTableGraphStorage", doc_status_storage="PGDocStatusStorage",
                       llm_model_func=llm, llm_model_name=cfg.llm_model,
                       tiktoken_model_name="gpt-4" if configuration.get("parser") else "gpt-4o-mini",
                       llm_model_max_async=1, embedding_func_max_async=1,
                       embedding_batch_num=4, max_parallel_insert=1,
                       chunk_token_size=chunk_size, chunk_overlap_token_size=overlap,
                       enable_llm_cache=False, entity_extract_max_gleaning=0,
                       embedding_func=EmbeddingFunc(embedding_dim=cfg.embedding_dim, max_token_size=8192,
                                                   model_name=cfg.embedding_model, func=embed),
                       addon_params={"language": "English", "chunker": {"fixed_token": {
                           "chunk_token_size": chunk_size, "chunk_overlap_token_size": overlap}}})
        await rag.initialize_storages()
        try:
            if payload["operation"] == "ingest":
                source = f"{document}:{payload['version_id']}"
                if configuration.get("parser"):
                    from .structured import ingest_structured
                    await ingest_structured(rag, payload, str(document), source)
                else:
                    await rag.ainsert(payload["text"], ids=str(document), file_paths=source,
                                      track_id=payload["track_id"])
                status = await rag.doc_status.get_by_id(str(document))
                status_value = status.get("status") if isinstance(status, dict) else getattr(status, "status", None)
                status_value = getattr(status_value, "value", status_value)
                if str(status_value).lower() != "processed":
                    raise RuntimeError("LightRAG indexing did not reach PROCESSED")
                return {"status": "processed", "workspace": workspace}
            if payload["operation"] == "retrieve":
                params = payload.get("parameters", {})
                query = QueryParam(mode=params.get("mode", "mix"), top_k=params.get("top_k", cfg.top_k),
                                   chunk_top_k=params.get("chunk_top_k", cfg.chunk_top_k),
                                   max_total_tokens=cfg.context_tokens, max_entity_tokens=2000,
                                   max_relation_tokens=2000, enable_rerank=False, include_references=True)
                return await rag.aquery_data(payload["question"], query)
            raise ValueError("Unknown operation")
        finally:
            await rag.finalize_storages()


def main():
    payload = json.load(sys.stdin)
    try:
        with redirect_stdout(sys.stderr):
            result = asyncio.run(execute(payload))
        print(json.dumps({"ok": True, "result": result}, default=str))
    except Exception as error:
        # No provider messages, SQL, document text, credentials or question content leave this process.
        print(json.dumps({"ok": False, "error": type(error).__name__}))
        sys.exit(1)


if __name__ == "__main__":
    main()

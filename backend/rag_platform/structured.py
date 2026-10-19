"""Use LightRAG's explicit C pipeline to preserve chunks AND source identity."""


async def ingest_structured(rag, payload, document_id, source):
    chunks = payload.get("chunks", [])
    if not chunks or len(chunks) > 10000 or sum(len(c) for c in chunks) > 4_000_000:
        raise ValueError("Invalid structured chunk budget")
    budget = payload["configuration"]["parser"]["chunk_size"]

    def chunking_func(tokenizer, content, split_character, split_only, overlap, chunk_size):
        result = []
        for index, chunk in enumerate(chunks):
            tokens = len(tokenizer.encode(chunk))
            if tokens > budget or not chunk.strip():
                raise ValueError("Invalid structured chunk")
            result.append({"content": chunk, "tokens": tokens, "chunk_order_index": index})
        return result

    rag.chunking_func = chunking_func
    await rag.apipeline_enqueue_documents(payload["text"], ids=[document_id], file_paths=[source],
                                           track_id=payload["track_id"], process_options="C")
    await rag.apipeline_process_enqueue_documents()

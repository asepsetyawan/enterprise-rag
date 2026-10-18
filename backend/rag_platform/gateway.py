import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .config import settings
from .metrics import metrics_response
from .observability import MODEL, configure_logging
from .security import internal_auth


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    cfg = settings()
    app.state.client = httpx.AsyncClient(timeout=cfg.model_timeout, trust_env=False)
    app.state.chat_slots = asyncio.Semaphore(cfg.generation_concurrency)
    app.state.embed_slots = asyncio.Semaphore(cfg.embedding_concurrency)
    yield
    await app.state.client.aclose()


app = FastAPI(title="Private Ollama inference gateway", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/metrics")
async def metrics(request: Request):
    return metrics_response(request)


class ChatInput(BaseModel):
    messages: list[dict[str, str]] = Field(min_length=1, max_length=50)
    format: Any = None
    output_tokens: int | None = Field(default=None, ge=32, le=8192)
    purpose: Literal["answer", "extract"] = "answer"


class EmbedInput(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=32)


async def call_ollama(kind, payload):
    cfg = settings()
    slots = app.state.chat_slots if kind == "chat" else app.state.embed_slots
    try:
        await asyncio.wait_for(slots.acquire(), cfg.gateway_queue_timeout)
    except TimeoutError as exc:
        raise HTTPException(429, "Inference capacity reached", headers={"Retry-After": "5"}) from exc
    start = time.monotonic()
    try:
        for host in cfg.ollama_hosts.split(","):
            try:
                if cfg.llm_digest or cfg.embedding_digest:
                    tags = await app.state.client.get(host.strip().rstrip("/") + "/api/tags")
                    tags.raise_for_status()
                    installed = {item["name"]: item["digest"] for item in tags.json()["models"]}
                    expected = cfg.llm_digest if kind == "chat" else cfg.embedding_digest
                    if expected and installed.get(payload["model"]) != expected:
                        continue
                response = await app.state.client.post(host.strip().rstrip("/") + f"/api/{kind}", json=payload)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError):
                # Local standby only; there is no provider-cloud fallback.
                continue
        raise HTTPException(503, "Local inference unavailable", headers={"Retry-After": "10"})
    finally:
        MODEL.labels(kind).observe(time.monotonic() - start)
        slots.release()


@app.get("/health")
async def health():
    cfg = settings()
    for host in cfg.ollama_hosts.split(","):
        try:
            response = await app.state.client.get(host.strip().rstrip("/") + "/api/tags", timeout=3)
            response.raise_for_status()
            installed = {item["name"]: item["digest"] for item in response.json()["models"]}
            if cfg.llm_model in installed and cfg.embedding_model in installed:
                if cfg.llm_digest and installed[cfg.llm_model] != cfg.llm_digest:
                    continue
                if cfg.embedding_digest and installed[cfg.embedding_model] != cfg.embedding_digest:
                    continue
                return {"status": "ready"}
        except (httpx.HTTPError, ValueError, KeyError):
            continue
    raise HTTPException(503, "Required local models unavailable or digest mismatch")


@app.get("/internal/models", dependencies=[Depends(internal_auth)])
async def models():
    cfg = settings()
    output = []
    for host in cfg.ollama_hosts.split(","):
        try:
            response = await app.state.client.get(host.strip() + "/api/tags")
            response.raise_for_status()
            output.append({"host": host, "models": [{"name": item["name"], "digest": item["digest"]}
                                                     for item in response.json()["models"]]})
        except (httpx.HTTPError, KeyError):
            output.append({"host": host, "status": "unavailable"})
    return {"nodes": output, "generation": cfg.llm_model, "embedding": cfg.embedding_model,
            "embedding_dim": cfg.embedding_dim}


@app.post("/internal/chat", dependencies=[Depends(internal_auth)])
async def chat(body: ChatInput):
    cfg = settings()
    if any(message.get("role") not in {"system", "user", "assistant"} for message in body.messages):
        raise HTTPException(422, "Invalid message role")
    if sum(len(message.get("content", "")) for message in body.messages) > 200_000:
        raise HTTPException(413, "Model input exceeds budget")
    payload = {"model": cfg.llm_model, "messages": body.messages, "stream": False, "think": False,
               "options": {"num_ctx": cfg.llm_context, "num_predict": body.output_tokens or cfg.llm_output_tokens,
                           "temperature": 0.1}}
    if body.format is not None:
        payload["format"] = body.format
    result = await call_ollama("chat", payload)
    content = result.get("message", {}).get("content", "")
    if not content:
        raise HTTPException(502, "Model returned empty content")
    return {"content": content, "model": cfg.llm_model,
            "usage": {"input_tokens": result.get("prompt_eval_count", 0), "output_tokens": result.get("eval_count", 0)}}


@app.post("/internal/embed", dependencies=[Depends(internal_auth)])
async def embed(body: EmbedInput):
    import math
    cfg = settings()
    if any(len(text) > 100_000 for text in body.texts):
        raise HTTPException(413, "Embedding input exceeds budget")
    result = await call_ollama("embed", {"model": cfg.embedding_model, "input": body.texts,
                                        "truncate": False, "options": {"num_ctx": 8192}})
    vectors = result.get("embeddings", [])
    if len(vectors) != len(body.texts) or any(len(row) != cfg.embedding_dim or not all(math.isfinite(x) for x in row) for row in vectors):
        raise HTTPException(502, "Invalid embedding shape or values")
    return {"vectors": vectors, "model": cfg.embedding_model, "dimensions": cfg.embedding_dim}

import asyncio
import json
import os
import signal
import sys
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .config import settings
from .metrics import metrics_response
from .observability import configure_logging
from .security import internal_auth
from .storage import validate_workspace


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    app.state.slots = asyncio.Semaphore(settings().runtime_concurrency)
    yield


app = FastAPI(title="Private LightRAG runtime", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/metrics")
async def metrics(request: Request):
    return metrics_response(request)


class Operation(BaseModel):
    operation: str
    tenant_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID | None = None
    workspace: str = Field(max_length=160)
    text: str = Field(default="", max_length=2_000_000)
    question: str = Field(default="", max_length=8000)
    track_id: str = ""
    configuration: dict = Field(default_factory=dict)
    parameters: dict = Field(default_factory=dict)


async def run_process(payload, timeout=None):
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "rag_platform.rag_process",
                                                   stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(json.dumps(payload, default=str).encode()),
                                           timeout or settings().runtime_timeout)
        if len(stdout) > 16 * 1024 * 1024:
            raise RuntimeError("Runtime output exceeds budget")
        result = json.loads(stdout)
        if process.returncode or not result.get("ok"):
            raise RuntimeError(f"RAG subprocess failed ({result.get('error', 'unknown')})")
        return result["result"]
    except BaseException:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        raise


@app.get("/health")
async def health():
    return {"status": "up"}


@app.post("/internal/operate", dependencies=[Depends(internal_auth)])
async def operate(body: Operation):
    if body.operation not in {"ingest", "retrieve", "purge"}:
        raise HTTPException(422, "Unknown operation")
    try:
        validate_workspace(body.workspace, body.tenant_id, body.document_id)
    except ValueError as exc:
        raise HTTPException(422, "Invalid tenant/document workspace") from exc
    if body.parameters.get("mode", "mix") not in {"mix", "naive", "local", "global", "hybrid"}:
        raise HTTPException(422, "Invalid retrieval mode")
    for key in ("top_k", "chunk_top_k"):
        if key in body.parameters and not 1 <= int(body.parameters[key]) <= 100:
            raise HTTPException(422, "Invalid retrieval budget")
    try:
        await asyncio.wait_for(app.state.slots.acquire(), 30)
    except TimeoutError as exc:
        raise HTTPException(429, "RAG runtime capacity reached", headers={"Retry-After": "5"}) from exc
    try:
        return await run_process(body.model_dump(mode="json"))
    except (RuntimeError, TimeoutError, ValueError, KeyError) as exc:
        raise HTTPException(503, "RAG runtime operation failed") from exc
    finally:
        app.state.slots.release()

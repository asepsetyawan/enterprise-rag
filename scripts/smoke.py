"""Live Ollama/LightRAG smoke test. Keeps the example source for UI exploration."""
import json
import argparse
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
TENANT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["naive", "mix", "local", "global", "hybrid"], default="naive")
    parser.add_argument("--name", default="kebijakan-perjalanan.md")
    args = parser.parse_args()
    password = (ROOT / ".secrets/dev_password").read_text().strip()
    with httpx.Client(base_url="http://localhost:8088", timeout=900, trust_env=False) as client:
        response = client.post("/auth/dev-login", json={"username": "alice", "password": password})
        response.raise_for_status()
        me = client.get("/api/me").json()
        client.headers.update({"X-Tenant-ID": TENANT, "X-CSRF-Token": me["csrf"]})
        source = ROOT / "examples/kebijakan-perjalanan.md"
        response = client.post("/api/documents", files={"file": (args.name, source.read_bytes(), "text/markdown")})
        response.raise_for_status()
        identifier = response.json()["id"]
        print("Uploaded example:", identifier, flush=True)
        deadline = time.monotonic() + 1800
        previous = None
        while time.monotonic() < deadline:
            doc = next(d for d in client.get("/api/documents").json() if d["id"] == identifier)
            if doc["status"] != previous:
                print("Index status:", doc["status"], flush=True)
                previous = doc["status"]
            if doc["status"] == "ready":
                break
            if doc["status"] == "failed":
                raise RuntimeError("Ingestion failed; inspect jobs and runtime diagnostics")
            time.sleep(3)
        else:
            raise TimeoutError("Ingestion timeout")
        response = client.post("/api/chat", json={"question": "Berapa plafon hotel untuk perjalanan dinas domestik?", "document_ids": [identifier], "mode": args.mode})
        response.raise_for_status()
        answer = response.json()
        if answer["abstained"] or not answer["citations"] or "750" not in answer["answer"]:
            raise AssertionError("Expected grounded Rp750.000 answer")
        assert all(s["document_id"] == identifier for s in answer["sources"])
        print("Grounded answer and citations OK", flush=True)
        client.post("/auth/logout").raise_for_status()
        client.post("/auth/dev-login", json={"username": "bob", "password": password}).raise_for_status()
        me = client.get("/api/me").json()
        client.headers["X-CSRF-Token"] = me["csrf"]
        assert client.post("/api/chat", json={"question": "Berapa plafon hotel?", "document_ids": [identifier]}).status_code == 404
        print("Unauthorized user denied OK", flush=True)
        report = {"document_id": identifier, "mode": args.mode, "ingestion": "passed", "answer": answer, "unauthorized_user": "denied"}
        (ROOT / "reports").mkdir(exist_ok=True)
        (ROOT / "reports/live-smoke.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

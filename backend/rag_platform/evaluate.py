"""Deterministic regression and access-control gate; human review still measures faithfulness."""
import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path

import httpx


def evaluate(client, cases):
    results = []
    for case in cases:
        start = time.monotonic()
        response = client.post("/api/chat", json={"question": case["question"], "document_ids": case.get("document_ids", []), "mode": case.get("mode", "mix")})
        expected_status = case.get("expected_status", 200)
        checks = {"status": response.status_code == expected_status}
        if response.status_code == 200 and expected_status == 200:
            result = response.json()
            expected_abstain = case.get("expected_abstain", False)
            checks["abstention"] = result["abstained"] == expected_abstain
            checks["answer_terms"] = all(str(term).casefold() in result["answer"].casefold() for term in case.get("must_contain", []))
            checks["no_forbidden_terms"] = all(str(term).casefold() not in result["answer"].casefold() for term in case.get("must_not_contain", []))
            checks["citations_present"] = expected_abstain or bool(result["citations"])
            actual_sources = {s["document_id"] for s in result["sources"]}
            checks["source_allowlist"] = actual_sources <= set(case.get("allowed_source_ids", case.get("document_ids", [])))
            checks["required_sources"] = set(case.get("required_source_ids", [])) <= actual_sources
        results.append({"id": case["id"], "passed": all(checks.values()), "checks": checks,
                        "latency_ms": int((time.monotonic() - start) * 1000)})
    passed = sum(case["passed"] for case in results)
    return results, {"cases": len(results), "pass_rate": passed / len(results) if results else 0,
                     "failed": len(results) - passed,
                     "p95_latency_ms": sorted(r["latency_ms"] for r in results)[max(0, math.ceil(len(results) * .95) - 1)] if results else 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--base-url", default="http://localhost:8088")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--output", type=Path, default=Path("../reports/evaluation.json"))
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.min_pass_rate <= 1:
        parser.error("min-pass-rate must be between 0 and 1")
    content = args.dataset.read_bytes()
    cases = [json.loads(line) for line in content.decode().splitlines() if line.strip()]
    with httpx.Client(base_url=args.base_url, timeout=900, trust_env=False) as client:
        client.headers["X-Tenant-ID"] = args.tenant
        if os.getenv("EVAL_TOKEN_FILE"):
            client.headers["Authorization"] = "Bearer " + Path(os.environ["EVAL_TOKEN_FILE"]).read_text().strip()
        elif os.getenv("EVAL_DEV_PASSWORD_FILE"):
            client.post("/auth/dev-login", json={"username": os.getenv("EVAL_USER", "alice"), "password": Path(os.environ["EVAL_DEV_PASSWORD_FILE"]).read_text().strip()}).raise_for_status()
            client.headers["X-CSRF-Token"] = client.get("/api/me").json()["csrf"]
        else:
            parser.error("Supply EVAL_TOKEN_FILE (SSO access token) or EVAL_DEV_PASSWORD_FILE for local testing")
        results, metrics = evaluate(client, cases)
        # Security cases are zero tolerance even if the overall quality threshold is lower.
        security_ok = all(r["passed"] for c, r in zip(cases, results, strict=True) if c.get("security_case"))
        report = {"dataset_revision": hashlib.sha256(content).hexdigest(), "release_revision": args.release,
                  "metrics": metrics, "cases": results, "passed": bool(cases) and security_ok and metrics["pass_rate"] >= args.min_pass_rate}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2))
        if args.record:
            client.post("/api/evaluations", json=report).raise_for_status()
    print(json.dumps({"passed": report["passed"], **metrics}))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

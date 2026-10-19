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
        retrieval_metrics = {}
        timings = {}
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
            selected = result.get("routing", {}).get("document_ids", [])
            timings = result.get("timings", {})
            if "expected_document_ids" in case:
                expected = set(case["expected_document_ids"])
                found = set(selected) & expected
                checks["routing_recall"] = (len(found) / len(expected) if expected else float(not selected)) >= case.get("min_routing_recall", 1.0)
                retrieval_metrics = {"recall": len(found) / len(expected) if expected else float(not selected),
                                     "precision": len(found) / len(selected) if selected else float(not expected),
                                     "mrr": next((1 / rank for rank, item in enumerate(selected, 1) if item in expected), 0.0)}
            checks["routing_excludes_forbidden"] = not set(selected) & set(case.get("forbidden_document_ids", []))
            if "allowed_document_ids" in case:
                checks["routing_allowlist"] = set(selected) <= set(case["allowed_document_ids"])
            if case.get("require_provenance"):
                checks["source_provenance"] = bool(result["sources"]) and all(s.get("headings") or s.get("pages") for s in result["sources"])
        results.append({"id": case["id"], "passed": all(checks.values()), "checks": checks,
                        "retrieval": retrieval_metrics, "timings": timings,
                        "latency_ms": int((time.monotonic() - start) * 1000)})
    passed = sum(case["passed"] for case in results)
    metrics = {"cases": len(results), "pass_rate": passed / len(results) if results else 0, "failed": len(results) - passed}
    for percentile in (50, 95):
        metrics[f"p{percentile}_latency_ms"] = quantile([r["latency_ms"] for r in results], percentile)
        for stage in ("routing_ms", "retrieval_ms", "generation_ms"):
            metrics[f"p{percentile}_{stage}"] = quantile([r["timings"][stage] for r in results if stage in r["timings"]], percentile)
    scored = [r["retrieval"] for r in results if r["retrieval"]]
    for name in ("recall", "precision", "mrr"):
        metrics[f"routing_{name}"] = sum(r[name] for r in scored) / len(scored) if scored else None
    return results, metrics


def quantile(values, percentile):
    return sorted(values)[max(0, math.ceil(len(values) * percentile / 100) - 1)] if values else None


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

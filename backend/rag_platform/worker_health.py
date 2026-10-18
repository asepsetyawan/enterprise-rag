"""Container health probe; it never logs the metrics credential."""
import time

import httpx

from .config import settings


def main():
    cfg = settings()
    with httpx.Client(timeout=3, trust_env=False) as client:
        response = client.get("http://127.0.0.1:8093/metrics", headers={"Authorization": "Bearer " + cfg.metrics_token.get_secret_value()})
        response.raise_for_status()
        metrics = response.text
    timestamp = next(float(line.split()[1]) for line in metrics.splitlines() if line.startswith("rag_worker_heartbeat_timestamp_seconds "))
    raise SystemExit(0 if time.time() - timestamp < cfg.runtime_timeout + 600 else 1)


if __name__ == "__main__":
    main()

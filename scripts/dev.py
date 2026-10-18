"""Supervise native services against Docker PostgreSQL and local Ollama."""
import argparse
from pathlib import Path
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    pidfile = ROOT / "data/dev-pids"
    if args.stop:
        if pidfile.exists():
            for pid in pidfile.read_text().splitlines():
                try:
                    __import__('os').kill(int(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            pidfile.unlink()
        return
    if pidfile.exists():
        parser.error("A supervisor pidfile exists; run --stop first")
    binary = ROOT / "backend/.venv/bin"
    commands = [([str(binary / "uvicorn"), f"rag_platform.{module}:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--no-proxy-headers"], module) for module, port in [("api",8088),("gateway",8091),("runtime",8092)]]
    commands.append(([str(binary / "rag-worker")], "worker"))
    logs = ROOT / "data/logs"
    logs.mkdir(exist_ok=True)
    processes = []
    try:
        for command, name in commands:
            with (logs / f"{name}.log").open("ab", buffering=0) as output:
                processes.append(subprocess.Popen(command, cwd=ROOT / "backend", stdout=output, stderr=output))
        pidfile.write_text("\n".join(str(p.pid) for p in processes))
        print("Portal: http://localhost:8088 | logs: data/logs | stop: Ctrl+C", flush=True)
        while all(p.poll() is None for p in processes):
            time.sleep(1)
        raise RuntimeError("A service stopped; inspect data/logs")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        pidfile.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

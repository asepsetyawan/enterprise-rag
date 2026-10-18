"""Restore into an isolated disposable cluster, verify every source checksum, then remove it."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

import psycopg

ROOT = Path(__file__).resolve().parents[1]
COMMAND = ["docker", "compose", "-f", str(ROOT / "infra/postgres/restore.compose.yaml"), "-p", "cnf-rag-restore-probe"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    backup = args.backup.resolve()
    manifest = json.loads((backup / "manifest.json").read_text())
    for name, expected in manifest.items():
        if name not in {"cluster.sql", "files-secrets.tar.gz"}:
            raise ValueError("Unexpected backup manifest file")
        if hashlib.sha256((backup / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Backup checksum mismatch")
    subprocess.run(COMMAND + ["up", "-d", "--wait"], check=True)
    try:
        # pg_dumpall includes CREATE ROLE postgres, which already exists in the target cluster.
        sql = (backup / "cluster.sql").read_text()
        sql = "\n".join(line for line in sql.splitlines() if line != "CREATE ROLE postgres;") + "\n"
        subprocess.run(COMMAND + ["exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1"], input=sql.encode(), check=True, stdout=subprocess.DEVNULL)
        password = (ROOT / ".secrets/pg_password").read_text().strip()
        with tempfile.TemporaryDirectory(prefix="rag-restore-") as directory:
            target = Path(directory).resolve()
            with tarfile.open(backup / "files-secrets.tar.gz") as archive:
                archive.extractall(target, filter="data")
            with psycopg.connect(host="localhost", port=15433, user="postgres", password=password, dbname="enterprise") as connection:
                rows = connection.execute("SELECT relative_path,checksum FROM app.document_versions WHERE status <> 'purged'").fetchall()
                audit_count = connection.execute("SELECT count(*) FROM app.audit_events").fetchone()[0]
                rag_databases = connection.execute("SELECT datname FROM pg_database WHERE datname LIKE 'rag_%'").fetchall()
            for relative, expected in rows:
                file = (target / "data/files" / relative).resolve()
                if not file.is_relative_to(target / "data/files") or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                    raise ValueError("Restored document checksum mismatch")
            rag_tables = 0
            for (database,) in rag_databases:
                with psycopg.connect(host="localhost", port=15433, user="postgres", password=password, dbname=database) as connection:
                    rag_tables += connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'lightrag_%'").fetchone()[0]
            report = {"passed": True, "documents_verified": len(rows), "audit_events_restored": audit_count,
                      "tenant_databases_restored": len(rag_databases), "rag_tables_restored": rag_tables,
                      "scope": "isolated development logical restore + source checksums; not a production PITR/failover drill"}
            (ROOT / "reports").mkdir(exist_ok=True)
            (ROOT / "reports/restore-drill.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report))
    finally:
        subprocess.run(COMMAND + ["down", "-v"], check=True)


if __name__ == "__main__":
    main()

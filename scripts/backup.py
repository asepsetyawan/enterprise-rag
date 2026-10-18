"""Coordinated development backup. Production uses pgBackRest + NAS snapshots."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--writes-quiesced", action="store_true", required=True, help="Stop API and worker before taking this backup")
    args = parser.parse_args()
    target = args.destination.resolve() / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target.mkdir(parents=True, mode=0o700)
    with (target / "cluster.sql").open("wb") as output:
        subprocess.run(["docker", "compose", "exec", "-T", "postgres", "pg_dumpall", "-U", "postgres"], cwd=ROOT, check=True, stdout=output)
    (target / "cluster.sql").chmod(0o600)
    with tarfile.open(target / "files-secrets.tar.gz", "w:gz") as archive:
        archive.add(ROOT / "data/files", arcname="data/files")
        archive.add(ROOT / ".secrets", arcname=".secrets")
        archive.add(ROOT / "data/audit-export", arcname="data/audit-export")
    (target / "files-secrets.tar.gz").chmod(0o600)
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir() if p.is_file()}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"Backup created: {target}. Encrypt and move to restricted off-host storage.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Create private local-development configuration; never overwrite existing secrets."""
import json
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]
private = ROOT / ".secrets"
private.mkdir(mode=0o700, exist_ok=True)


def write_private(name, content):
    path = private / name
    if not path.exists():
        path.write_text(content)
        path.chmod(0o600)
    return path.read_text().strip()


names = ["pg_password", "app_password", "worker_password", "keycloak_password", "session_secret",
         "internal_token", "audit_hmac_key", "dev_password", "oidc_client_secret", "metrics_token"]
values = {name: write_private(name, secrets.token_hex(32)) for name in names}
for role, database, password in [("app", "enterprise", values["app_password"]),
                                 ("worker", "enterprise", values["worker_password"])]:
    write_private(f"{role}_dsn", f"postgresql+psycopg://rag_{role}:{password}@postgres:5432/{database}")
write_private("migration_dsn", f"postgresql+psycopg://postgres:{values['pg_password']}@postgres:5432/enterprise")
sql = f"""
CREATE ROLE app_owner NOLOGIN;
CREATE ROLE rag_maintenance NOLOGIN BYPASSRLS;
CREATE ROLE rag_app LOGIN PASSWORD '{values['app_password']}';
CREATE ROLE rag_worker LOGIN PASSWORD '{values['worker_password']}';
CREATE ROLE keycloak LOGIN PASSWORD '{values['keycloak_password']}';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA app AUTHORIZATION app_owner;
REVOKE CONNECT ON DATABASE enterprise FROM PUBLIC;
GRANT CONNECT ON DATABASE enterprise TO rag_app,rag_worker;
CREATE DATABASE keycloak OWNER keycloak;
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
GRANT CONNECT ON DATABASE keycloak TO keycloak;
"""
write_private("postgres-init.sql", sql)
users = []
for name, identifier in [("alice", "11111111-1111-4111-8111-111111111111"),
                         ("bob", "22222222-2222-4222-8222-222222222222"),
                         ("mallory", "33333333-3333-4333-8333-333333333333")]:
    users.append({"id": identifier, "username": name, "enabled": True,
                  "emailVerified": True, "firstName": name.title(), "lastName": "Demo",
                  "email": f"{name}@example.test", "requiredActions": [],
                  "credentials": [{"type": "password", "value": values["dev_password"], "temporary": False}]})
realm = {"realm": "enterprise-rag", "enabled": True, "registrationAllowed": False,
         "sslRequired": "none", "users": users,
         "clients": [{"clientId": "rag-portal", "enabled": True, "publicClient": False,
                      "secret": values["oidc_client_secret"], "standardFlowEnabled": True,
                      "directAccessGrantsEnabled": False,
                      "redirectUris": ["http://localhost:8088/auth/callback"],
                      "webOrigins": ["http://localhost:8088"],
                      "attributes": {"pkce.code.challenge.method": "S256"},
                      "protocolMappers": [{"name": "api-audience", "protocol": "openid-connect",
                                           "protocolMapper": "oidc-audience-mapper",
                                           "config": {"included.client.audience": "rag-portal",
                                                      "access.token.claim": "true", "id.token.claim": "false"}}]}]}
write_private("keycloak-realm.json", json.dumps(realm, indent=2))
env = ROOT / ".env"
if not env.exists():
    config = {
        "APP_ENV": "development", "PUBLIC_ORIGIN": "http://localhost:8088",
        "RAG_UID": str(os.getuid()), "RAG_GID": str(os.getgid()),
        "DATABASE_URL": f"postgresql+psycopg://rag_app:{values['app_password']}@localhost:15432/enterprise",
        "WORKER_DATABASE_URL": f"postgresql+psycopg://rag_worker:{values['worker_password']}@localhost:15432/enterprise",
        "MIGRATION_DATABASE_URL": f"postgresql+psycopg://postgres:{values['pg_password']}@localhost:15432/enterprise",
        "SESSION_SECRET": values["session_secret"], "INTERNAL_TOKEN": values["internal_token"],
        "AUDIT_HMAC_KEY": values["audit_hmac_key"], "METRICS_TOKEN": values["metrics_token"],
        "DEV_AUTH_ENABLED": "true", "DEV_PASSWORD": values["dev_password"],
        "OIDC_ISSUER": "http://localhost:8089/realms/enterprise-rag",
        "OIDC_INTERNAL_BASE": "http://localhost:8089", "OIDC_CLIENT_ID": "rag-portal",
        "OIDC_CLIENT_SECRET": values["oidc_client_secret"],
        "STORAGE_ROOT": str(ROOT / "data/files"), "RUNTIME_ROOT": str(ROOT / "data/runtime"),
        "TENANT_STORES_FILE": str(private / "tenant-stores.json"),
        "FRONTEND_DIR": str(ROOT / "frontend/dist"),
        "AUDIT_EXPORT_DIR": str(ROOT / "data/audit-export"),
        "OLLAMA_HOSTS": "http://127.0.0.1:11434",
        "GATEWAY_URL": "http://127.0.0.1:8091", "RUNTIME_URL": "http://127.0.0.1:8092",
    }
    env.write_text("\n".join(f"{key}={value}" for key, value in config.items()) + "\n")
    env.chmod(0o600)
existing_env = env.read_text()
with env.open("a") as output:
    for key, value in {"RAG_UID": os.getuid(), "RAG_GID": os.getgid()}.items():
        if not any(line.startswith(key + "=") for line in existing_env.splitlines()):
            output.write(f"{key}={value}\n")
for name in ["files", "runtime", "audit-export", "prometheus", "grafana", "tempo"]:
    (ROOT / "data" / name).mkdir(parents=True, mode=0o700, exist_ok=True)
print("Local configuration prepared. Private demo password: .secrets/dev_password")
print("Existing secrets and configuration were preserved.")

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_env: Literal["development", "testing", "production"] = "development"
    service_role: Literal["api", "worker", "runtime", "gateway", "admin"] = "api"
    public_origin: str = "http://localhost:8088"
    database_url: SecretStr = SecretStr("postgresql+psycopg://rag_app@localhost:15432/enterprise")
    worker_database_url: SecretStr | None = None
    migration_database_url: SecretStr | None = None
    session_secret: SecretStr = SecretStr("")
    internal_token: SecretStr = SecretStr("")
    audit_hmac_key: SecretStr = SecretStr("")
    session_hours: int = 8
    dev_auth_enabled: bool = False
    dev_password: SecretStr = SecretStr("")
    cookie_name: str = "rag_session"
    oidc_issuer: str = "http://localhost:8089/realms/enterprise-rag"
    oidc_internal_base: str = "http://localhost:8089"
    oidc_client_id: str = "rag-portal"
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_required_acr: str = ""
    oidc_service_clients: str = "rag-eval"
    storage_root: Path = Path("../data/files")
    runtime_root: Path = Path("../data/runtime")
    tenant_stores_file: Path = Path("../.secrets/tenant-stores.json")
    tenant_db_host: str = ""
    tenant_db_port: int = 0
    runtime_url: str = "http://127.0.0.1:8092"
    gateway_url: str = "http://127.0.0.1:8091"
    ollama_hosts: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3.6:latest"
    embedding_model: str = "qwen3-embedding:8b"
    embedding_dim: int = 4096
    llm_digest: str = ""
    embedding_digest: str = ""
    llm_context: int = 32768
    llm_output_tokens: int = 4096
    model_timeout: int = 600
    generation_concurrency: int = 1
    embedding_concurrency: int = 1
    gateway_queue_timeout: int = 30
    runtime_concurrency: int = 2
    runtime_timeout: int = 1800
    max_upload_bytes: int = 25 * 1024 * 1024
    max_text_chars: int = 2_000_000
    max_query_documents: int = 20
    max_question_chars: int = 8000
    query_timeout: int = 600
    chunk_size: int = 1000
    chunk_overlap: int = 100
    top_k: int = 20
    chunk_top_k: int = 8
    context_tokens: int = 8000
    retrieval_profile: str = "docling-structure-catalog-v2"
    parser_revision: str = "docling-hybrid-v1"
    parser_timeout: int = 600
    parser_artifacts_path: Path = Path("../data/parser-models")
    parser_ocr_languages: str = "eng"
    routing_top_k: int = 5
    prompt_revision: str = "grounded-json-v1"
    job_lease_seconds: int = 90
    job_max_attempts: int = 5
    job_poll_seconds: float = 2
    metrics_token: SecretStr = SecretStr("")
    audit_export_dir: Path = Path("../data/audit-export")
    audit_sink_url: str = ""
    audit_sink_token: SecretStr = SecretStr("")
    otel_exporter_otlp_endpoint: str = ""
    frontend_dir: Path = Path("../frontend/dist")
    antivirus_host: str = ""
    antivirus_port: int = 3310
    default_retention_days: int = 365

    @model_validator(mode="after")
    def validate_security(self):
        required = {"api": ["session_secret", "internal_token", "audit_hmac_key"],
                    "worker": ["internal_token", "audit_hmac_key"], "runtime": ["internal_token"],
                    "gateway": ["internal_token"], "admin": []}[self.service_role]
        for field in required:
            if len(getattr(self, field).get_secret_value()) < 32:
                raise ValueError(f"{field} must contain at least 32 random characters; run scripts/configure.py")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if not 64 <= self.chunk_size <= 8000:
            raise ValueError("Chunk size must be between 64 and 8000 tokens")
        for field in ("chunk_size", "context_tokens", "runtime_concurrency", "generation_concurrency", "embedding_concurrency", "model_timeout", "runtime_timeout", "max_upload_bytes", "job_lease_seconds"):
            if getattr(self, field) <= 0:
                raise ValueError(f"{field} must be positive")
        if self.chunk_overlap < 0 or not 1 <= self.job_max_attempts <= 20:
            raise ValueError("Invalid chunk overlap or job retry limit")
        if not 1 <= self.routing_top_k <= self.max_query_documents <= 20 or self.parser_timeout <= 0:
            raise ValueError("Invalid routing limit or parser timeout")
        if self.context_tokens + self.llm_output_tokens + 2000 > self.llm_context:
            raise ValueError("Context + output + instruction budget exceeds model context")
        if self.app_env == "production":
            if self.dev_auth_enabled:
                raise ValueError("Development login is forbidden in production")
            if self.service_role == "api" and (not self.public_origin.startswith("https://") or not self.oidc_issuer.startswith("https://")):
                raise ValueError("Production requires HTTPS public origin and OIDC issuer")
            if self.service_role == "api" and (not self.oidc_client_secret.get_secret_value() or not self.metrics_token.get_secret_value()):
                raise ValueError("Production requires OIDC client and metrics secrets")
            if self.service_role == "api" and not self.oidc_required_acr:
                raise ValueError("Production requires an OIDC assurance level (OIDC_REQUIRED_ACR)")
            if self.service_role != "admin" and (len(self.llm_digest) != 64 or len(self.embedding_digest) != 64):
                raise ValueError("Production requires exact Ollama model SHA256 digests")
            if self.service_role == "worker" and not self.antivirus_host:
                raise ValueError("Production requires a fail-closed ClamAV ingestion scanner")
        if any("cloud" in model.lower() for model in (self.llm_model, self.embedding_model)):
            raise ValueError("Only local Ollama models are allowed")
        return self


@lru_cache
def settings() -> Settings:
    values = {}
    for field in Settings.model_fields:
        file_path = os.getenv(f"{field.upper()}_FILE")
        if file_path:
            values[field] = Path(file_path).read_text().strip()
    return Settings(**values)

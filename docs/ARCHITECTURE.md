# Implementation architecture

Browser React → HAProxy TLS → FastAPI BFF/API. The API authenticates the caller, checks tenant membership, role, and ACL, then reads metadata through the `rag_app` role. The cookie holds an opaque token; only the token hash is stored in the database. Cookie-authenticated mutations require CSRF and an Origin check. The OIDC authorization code uses PKCE S256, a signed state cookie, a nonce, issuer, audience, RS256, and an assurance level. The API does not trust tenant roles or groups from the token. Membership is provisioned locally and checked on every request. There is no tenant self-registration.

API upload → immutable file → metadata, version, and job in one PostgreSQL transaction → worker claim with SKIP LOCKED → antivirus and checksum → parser subprocess → private LightRAG runtime → Ollama gateway → tenant PostgreSQL. The worker heartbeat extends the lease. Each attempt gets a new workspace. Publication uses the lease token and active-version CAS. A late attempt cannot replace the active index. Leftover attempt data is purged after the runtime hard timeout. Retries use backoff, then a dead letter and an explicit admin retry.

Query → select permitted documents → retrieve in each document workspace → merge permitted chunks within the token budget → check membership, ACL, and version → model → validate the JSON schema, exact quotes, and inline source IDs → check permissions again → answer. Output that fails validation is tried once more after permissions are revalidated, then the system abstains. There is no token streaming and no shared answer cache. The graph is used inside a document scope. Graph relationships are not merged across documents that have different ACLs. That choice gives up some global-graph capability so the access boundary stays testable.

## PostgreSQL

`app_owner` is NOLOGIN and owns the schema. The API and worker are not the owner, a superuser, or BYPASSRLS. FORCE RLS requires a transaction-local tenant GUC. Cross-tenant claim functions use SECURITY DEFINER, owned by the NOLOGIN role `rag_maintenance`, with a fixed search_path, input validation, and EXECUTE granted only to the worker. That role has BYPASSRLS for queue functions. It is not a runtime login credential. Document-level access is checked by the API. Application-database RLS limits the tenant. It does not replace the application ACL.

Each tenant has its own LightRAG database and role, so the system does not rely on an upstream adapter to add RLS across every graph and vector table. The runtime launches one SDK process per operation so the SDK environment and global connection pool are not mixed across tenants. The API does not accept a workspace or database name from the browser. The private runtime must be reachable only by authorized services. A shared internal token is not a substitute for network segmentation.

## Stack and services

| Service | Technology | Local port |
|---|---|---|
| Portal/API | React 19, TypeScript, Vite; FastAPI/Uvicorn, Pydantic, SQLAlchemy | 8088 |
| Ingestion | Python asyncio, PostgreSQL leases/outbox, bounded parser | worker metrics 8093 |
| Retrieval runtime | LightRAG 1.5.7, per-operation subprocess | 8092 private |
| Inference gateway | HTTPX, bounded queues, local-only Ollama standby list | 8091 private |
| Models | Qwen 3.6; qwen3-embedding:8b, 4096 | Ollama 11434 private |
| Storage | PostgreSQL 18 + pgvector; shared NAS source files | 15432 dev / 5432 prod |
| Identity | Keycloak in development; corporate OIDC or Keycloak HA in production | 8089 dev |
| Edge | HAProxy TLS, host checks, request rate and size limits | 443 prod |
| Database HA | Patroni + etcd quorum, synchronous standby, PostgreSQL router | infra-only |
| Backup | pgBackRest WAL/PITR, encrypted off-host archive, NAS snapshots | infra-only |
| Telemetry | Prometheus, Alertmanager, Grafana, OpenTelemetry, Tempo; JSON stdout → company log collector | infra-only |
| CI/release | GitLab CI, Trivy/SBOM, image digests, Ansible serial rollout | runners |

## Network policy to apply

Only the edge may reach API:8088. In the production host-network compose, the API and worker reach the runtime and gateway on loopback. Application roles reach the PostgreSQL router with verify-full TLS. GPU inference ports are reachable only from gateway hosts. Cross-host traffic needs an encrypted private network or a TLS proxy. The runtime accesses tenant stores only. File and NAS mounts use least privilege and encryption (for example NFSv4 with krb5p where it is supported). The audit archive is separate from source storage and immutable at the storage-policy level. Secrets, PostgreSQL superuser credentials, and Patroni/etcd admin endpoints are unavailable to browsers and API containers.

Two API/worker/runtime nodes, two TLS edges behind a company VIP or load balancer, three PostgreSQL nodes, three etcd nodes, a GPU primary and standby, and redundant storage form the production topology. Development Compose is intentionally a single-node topology. HA templates need host provisioning, certificates, fencing, and a failure-drill validation before they are used.

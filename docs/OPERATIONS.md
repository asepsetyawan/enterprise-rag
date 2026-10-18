# Operations and recovery

## Telemetry and alerts

The API emits a request ID, route template, status, and latency. The gateway measures inference. The worker exposes backlog, dead-letter, audit outbox, and heartbeat. Scraping `/metrics` requires a bearer secret. The metrics token is not the internal model token. OpenTelemetry sends request metadata only. It does not send questions, prompts, results, documents, or SQL statements. JSON stdout is forwarded by the company collector to log storage under the retention policy. Uvicorn access logs are disabled so OAuth callback codes do not enter the logs. Do not enable LightRAG or provider debug in production: the SDK can record document contents.

Prometheus, Grafana, Tempo, and Alertmanager are in the monitoring Compose file. Import and operate them on the internal network with company Grafana SSO. Configure a paging receiver before go-live, fire a deliberate alert, and record the runbook URL. The alert templates cover service down, a stalled worker, dead letter, API errors, and audit backlog. Add PostgreSQL, GPU/DCGM, node, storage, backup-age, WAL-archive, and etcd-quorum exporters to company monitoring. The application does not treat those exporters as already installed.

Dead letter: check whether the cause is file format or OCR, an unavailable scanner, an unavailable model or digest mismatch, a context that is too large, the database or TLS, or storage. Normal logs store only the error class so sensitive content is not copied. Use a staging copy or a controlled diagnostic process for a deeper diagnosis. Fix the root cause, then retry from the portal as tenant_admin. Avoid a retry loop that does not fix configuration or capacity. Changing the embedding requires a reindex of every related document.

## Local backup and restore drill

Stop the API and worker, wait until active requests and runtime work finish, then:

```sh
backend/.venv/bin/python scripts/backup.py /secure/backup-destination --writes-quiesced
backend/.venv/bin/python scripts/restore_drill.py /secure/backup-destination/<timestamp>
```

The backup includes the SQL cluster (roles, app, auth, and tenant RAG databases), source files, secrets, and the audit archive. The contents are sensitive: encrypt them and move them to controlled off-host storage. The quiesced flag is an operator acknowledgement, not an automatic mechanism that blocks writers. The restore drill creates a separate disposable cluster on localhost:15433, restores with stop-on-error, verifies source checksums and tenant tables, then deletes the probe cluster. The drill does not replace the active database. Do not run two probes at once on the same project name.

## Production backup and PITR

Use a weekly pgBackRest full backup, a daily differential, continuous WAL archive, repository encryption, off-host redundancy and immutability, and backup-age monitoring. The template `infra/ha/pgbackrest.conf.j2` needs archive credentials from Vault. Run stanza-create, check, and info, take a backup, and test a restore onto a DR host. File, NAS, and secret backups must have a recovery point that matches the database. Pause ingestion while taking a coordinated snapshot, or use storage snapshot orchestration. Restoring the database without the matching sources can break reindex and download. The audit archive sits in a separate failure domain and can be forwarded to SIEM with an idempotency key.

Disaster recovery: isolate traffic → choose the recovery target (RPO) → restore pgBackRest onto a new cluster with PITR → restore the NAS snapshot and secret key versions → verify source checksums, migrations, and tenant roles → replay the latest deletion ledger from the audit archive with `SERVICE_ROLE=admin rag-admin replay-deletions <archive-directory>` (the `--keys-file` option accepts a JSON array of old audit keys for rotation) → canary auth, ACL, retrieval, and citations → open traffic in stages. Tombstone replay matters: restoring an old snapshot must not bring back a document that was already deleted. Confirm that legal hold and the deletion decision match the current policy. A deleted source can remain on an encrypted backup until the backup lifecycle ends. Do not promise a physical secure erase of an SSD or NAS from an application unlink.

## Failover

Three Patroni and etcd nodes, with a strict synchronous standby and company-approved quorum and fencing. Strict sync chooses durability and can stop writes when no healthy standby exists. The router selects `/primary` only. Read and write traffic is not sent to a random replica. Database TLS verify-full must include the router name on the certificate SAN. Test a primary kill, a network partition, etcd member loss, and split-brain recovery before go-live. The initial targets to test are database RPO 0 for committed synchronous transactions and RTO under 15 minutes. Source, NAS, and backup RPO are set separately. Those numbers are not an SLA this local project has already proved.

Two API, worker, and runtime nodes share the NAS and PostgreSQL. An expired lease can be claimed by another worker. CAS prevents a stale attempt from publishing. Another API node accepts the cookie when it has the same secret. The GPU standby is tried only when a local request fails. There is no cloud fallback. The Ollama queue, VRAM, and load shedding must be tested at real concurrency. Edge nodes need a company VIP or load balancer, or platform-managed VRRP and fencing. An HA template is not a substitute for an infrastructure drill.

## Incident ownership

P1: a permission leak, data loss, an unavailable database, or a stopped audit trail. Cut off the affected network or requests, preserve the audit, and escalate to Security, Platform, and the knowledge owner. Do not copy sensitive documents or prompts into a general ticket or log. P2: poor answer quality, a backlog, or an ingestion format failure. Roll back the relevant prompt, model, or retrieval change after checking compatibility. Every incident has a timeline, the actual RPO and RTO, an evaluation-dataset correction, and a preventive action.

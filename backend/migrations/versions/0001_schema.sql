-- Immutable initial schema; later changes belong in new migrations.
CREATE TABLE app.audit_events (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	actor_id VARCHAR(200) NOT NULL, 
	action VARCHAR(80) NOT NULL, 
	resource VARCHAR(200) NOT NULL, 
	detail JSONB NOT NULL, 
	request_id VARCHAR(80) NOT NULL, 
	signature VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_app_audit_events_tenant_id ON app.audit_events (tenant_id);

CREATE TABLE app.evaluation_runs (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	actor_id VARCHAR(200) NOT NULL, 
	dataset_revision VARCHAR(160) NOT NULL, 
	release_revision VARCHAR(160) NOT NULL, 
	metrics JSONB NOT NULL, 
	cases JSONB NOT NULL, 
	passed BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_app_evaluation_runs_tenant_id ON app.evaluation_runs (tenant_id);

CREATE TABLE app.query_records (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	user_id VARCHAR(200) NOT NULL, 
	question_hash VARCHAR(64) NOT NULL, 
	source_ids JSONB NOT NULL, 
	profile VARCHAR(160) NOT NULL, 
	prompt_revision VARCHAR(160) NOT NULL, 
	latency_ms INTEGER NOT NULL, 
	abstained BOOLEAN NOT NULL, 
	feedback VARCHAR(30), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_app_query_records_tenant_id ON app.query_records (tenant_id);

CREATE TABLE app.tenants (
	id UUID NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE app.users (
	id VARCHAR(200) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	email VARCHAR(320) NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE app.audit_deliveries (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	delivered_at TIMESTAMP WITH TIME ZONE, 
	attempts INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(id) REFERENCES app.audit_events (id)
);

CREATE INDEX ix_app_audit_deliveries_tenant_id ON app.audit_deliveries (tenant_id);

CREATE TABLE app.documents (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	title VARCHAR(240) NOT NULL, 
	filename VARCHAR(240) NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	active_version_id UUID, 
	acl_users JSONB NOT NULL, 
	acl_groups JSONB NOT NULL, 
	acl_revision INTEGER NOT NULL, 
	retention_until TIMESTAMP WITH TIME ZONE, 
	legal_hold BOOLEAN NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (tenant_id, id), 
	FOREIGN KEY(tenant_id) REFERENCES app.tenants (id), 
	FOREIGN KEY(owner_id) REFERENCES app.users (id)
);

CREATE INDEX ix_app_documents_tenant_id ON app.documents (tenant_id);

CREATE TABLE app.memberships (
	tenant_id UUID NOT NULL, 
	user_id VARCHAR(200) NOT NULL, 
	role VARCHAR(30) NOT NULL, 
	groups JSONB NOT NULL, 
	active BOOLEAN NOT NULL, 
	revision INTEGER NOT NULL, 
	PRIMARY KEY (tenant_id, user_id), 
	FOREIGN KEY(tenant_id) REFERENCES app.tenants (id), 
	FOREIGN KEY(user_id) REFERENCES app.users (id)
);

CREATE TABLE app.sessions (
	token_hash VARCHAR(64) NOT NULL, 
	user_id VARCHAR(200) NOT NULL, 
	csrf VARCHAR(80) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(user_id) REFERENCES app.users (id)
);

CREATE TABLE app.document_versions (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	checksum VARCHAR(64) NOT NULL, 
	relative_path VARCHAR(300) NOT NULL, 
	filename VARCHAR(240) NOT NULL, 
	bytes INTEGER NOT NULL, 
	workspace VARCHAR(160), 
	profile VARCHAR(160) NOT NULL, 
	configuration JSONB NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id, document_id) REFERENCES app.documents (tenant_id, id), 
	UNIQUE (tenant_id, id)
);

CREATE INDEX ix_app_document_versions_tenant_id ON app.document_versions (tenant_id);

CREATE INDEX ix_app_document_versions_document_id ON app.document_versions (document_id);

CREATE TABLE app.jobs (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	version_id UUID, 
	kind VARCHAR(30) NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	attempts INTEGER NOT NULL, 
	lease_token UUID, 
	lease_until TIMESTAMP WITH TIME ZONE, 
	available_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id, document_id) REFERENCES app.documents (tenant_id, id)
);

CREATE INDEX ix_app_jobs_tenant_id ON app.jobs (tenant_id);

CREATE INDEX jobs_claim_idx ON app.jobs (status, available_at);

CREATE TABLE app.index_builds (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	version_id UUID NOT NULL, 
	workspace VARCHAR(160) NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id, version_id) REFERENCES app.document_versions (tenant_id, id), 
	UNIQUE (workspace)
);

CREATE INDEX ix_app_index_builds_version_id ON app.index_builds (version_id);

CREATE INDEX ix_app_index_builds_tenant_id ON app.index_builds (tenant_id);

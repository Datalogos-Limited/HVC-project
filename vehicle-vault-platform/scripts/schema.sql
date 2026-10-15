-- Run separately in each vault database as its schema owner/migration operator.
-- Supply psql -v runtime_user='service-account@project.iam' -v scanner_user='scanner@project.iam'
-- IAM users must already exist (Terraform); runtime identities never own tables.
CREATE TABLE records (
 id uuid PRIMARY KEY,
 tenant_id uuid NOT NULL,
 client_id uuid NOT NULL,
 kind text NOT NULL CHECK (kind IN ('clients','assets','kyc-cases','transactions','documents')),
 data jsonb NOT NULL,
 version integer NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX records_scope ON records(tenant_id,kind,id);
CREATE INDEX documents_parent ON records(tenant_id,(data->>'parent_id'),id) WHERE kind='documents';
CREATE TABLE idempotency (
 tenant_id uuid NOT NULL, actor_id text NOT NULL, kind text NOT NULL, key text NOT NULL,
 request_hash text NOT NULL, record_id uuid NOT NULL REFERENCES records(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,actor_id,kind,key)
);
CREATE TABLE audit_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 tenant_id uuid NOT NULL, actor_id text NOT NULL, action text NOT NULL, resource_id uuid,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE records ENABLE ROW LEVEL SECURITY;
ALTER TABLE records FORCE ROW LEVEL SECURITY;
CREATE POLICY records_scope ON records USING (
 tenant_id = nullif(current_setting('app.tenant',true),'')::uuid AND
 (current_setting('app.staff',true)='true' OR client_id=nullif(current_setting('app.client',true),'')::uuid)
) WITH CHECK (
 tenant_id = nullif(current_setting('app.tenant',true),'')::uuid AND
 (current_setting('app.staff',true)='true' OR client_id=nullif(current_setting('app.client',true),'')::uuid)
);
ALTER TABLE idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency FORCE ROW LEVEL SECURITY;
CREATE POLICY idempotency_scope ON idempotency USING (
 tenant_id=nullif(current_setting('app.tenant',true),'')::uuid
 AND actor_id=current_setting('app.actor',true)
) WITH CHECK (
 tenant_id=nullif(current_setting('app.tenant',true),'')::uuid
 AND actor_id=current_setting('app.actor',true)
);
ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_insert ON audit_events FOR INSERT WITH CHECK (
 tenant_id=nullif(current_setting('app.tenant',true),'')::uuid
 AND actor_id=current_setting('app.actor',true)
);
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"runtime_user", :"scanner_user";
GRANT SELECT, INSERT, UPDATE ON records TO :"runtime_user", :"scanner_user";
GRANT SELECT, INSERT ON idempotency TO :"runtime_user";
GRANT INSERT ON audit_events TO :"runtime_user", :"scanner_user";
GRANT USAGE ON SEQUENCE audit_events_id_seq TO :"runtime_user", :"scanner_user";
-- Deliberately no DELETE, table ownership, DDL, audit UPDATE or audit DELETE grant.
-- For stronger audit immutability, export to separately administered locked log storage.

-- YojanaSaathi tables (Phase 3). Applied at every agent start (idempotent).
-- The LangGraph checkpointer creates its own tables (checkpoints, checkpoint_blobs,
-- checkpoint_writes, checkpoint_migrations): that is the case memory, one thread per case.
-- Privacy: no table here holds document contents or a full Aadhaar number.

CREATE TABLE IF NOT EXISTS citizens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    phone_hash  text UNIQUE,  -- Phase 7: HMAC of the caller's number, never the number itself
    phone_last4 text
);

-- The reusable citizen profile. Saved only with consent: the CHECK makes the database
-- itself refuse profile data without consent_profile.
CREATE TABLE IF NOT EXISTS profiles (
    citizen_id        uuid PRIMARY KEY REFERENCES citizens(id) ON DELETE CASCADE,
    consent_profile   boolean NOT NULL DEFAULT false,
    consent_documents boolean NOT NULL DEFAULT false,
    consent_at        timestamptz,
    data              jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT profile_data_needs_consent CHECK (consent_profile OR data = '{}'::jsonb)
);

-- One row per case: a readable projection, updated after every /turn. The graph state in
-- the checkpointer is the source of truth (idempotency reads it from there).
CREATE TABLE IF NOT EXISTS cases (
    case_id         text PRIMARY KEY,
    citizen_id      uuid NOT NULL REFERENCES citizens(id) ON DELETE CASCADE,
    lang            text,
    status          text,
    selected_scheme text,
    paused          text,  -- confirm | otp | safe_stop | null
    applications    jsonb NOT NULL DEFAULT '{}'::jsonb,  -- scheme_id -> {app_id, status}
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- The case timeline (turns, submissions; Phase 6: status changes). No message text.
CREATE TABLE IF NOT EXISTS case_events (
    id        bigserial PRIMARY KEY,
    case_id   text NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    at        timestamptz NOT NULL DEFAULT now(),
    kind      text NOT NULL,
    scheme_id text,
    detail    jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS case_events_case_at ON case_events (case_id, at);

-- Document METADATA only. The encrypted file is on disk (agent/vault.py); its own key is
-- wrapped with MASTER_KEY inside the file, so nothing here can decrypt it.
CREATE TABLE IF NOT EXISTS documents (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id       text NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    citizen_id    uuid NOT NULL REFERENCES citizens(id) ON DELETE CASCADE,
    doc_type      text NOT NULL,
    content_type  text NOT NULL,
    size_bytes    integer NOT NULL,
    sha256        text NOT NULL,  -- of the plaintext, checked after decryption
    storage_key   text NOT NULL UNIQUE,
    aadhaar_last4 text CHECK (aadhaar_last4 ~ '^[0-9]{4}$'),
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz,  -- set when the case submits; purged after
    UNIQUE (case_id, doc_type)
);

-- Append-only: one row per consequential action. No foreign key, so the trail outlives
-- deleted cases; it holds no personal values (field names, IDs, last 4 digits only).
CREATE TABLE IF NOT EXISTS audit_log (
    id        bigserial PRIMARY KEY,
    at        timestamptz NOT NULL DEFAULT now(),
    actor     text NOT NULL,  -- citizen | agent | system (Phase 4: browser_agent)
    action    text NOT NULL,
    case_id   text,
    scheme_id text,
    detail    jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS audit_log_case_at ON audit_log (case_id, at);

CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP;
END
$$;
CREATE OR REPLACE TRIGGER audit_log_no_change
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();

-- Additive authentication only. No owner-key backfill or automatic legacy claim.
BEGIN;
ALTER TABLE users
 ADD COLUMN email VARCHAR(320),
 ADD COLUMN normalized_email VARCHAR(320),
 ADD COLUMN password_hash TEXT,
 ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT FALSE,
 ADD COLUMN avatar_url TEXT,
 ADD COLUMN account_status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' CHECK(account_status IN ('ACTIVE','DISABLED')),
 ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 ADD COLUMN last_login_at TIMESTAMPTZ,
 ADD CONSTRAINT users_email_pair CHECK ((email IS NULL AND normalized_email IS NULL) OR
   (email IS NOT NULL AND normalized_email IS NOT NULL AND normalized_email=lower(btrim(normalized_email))));
CREATE UNIQUE INDEX users_normalized_email_key ON users(normalized_email) WHERE normalized_email IS NOT NULL;
CREATE TABLE auth_identities (
 identity_id BIGSERIAL PRIMARY KEY,
 user_id BIGINT NOT NULL REFERENCES users(user_id),
 provider VARCHAR(16) NOT NULL CHECK(provider='google'),
 subject VARCHAR(255) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 UNIQUE(provider,subject), UNIQUE(user_id,provider)
);
CREATE TABLE auth_sessions (
 token_digest CHAR(64) PRIMARY KEY,
 user_id BIGINT NOT NULL REFERENCES users(user_id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 expires_at TIMESTAMPTZ NOT NULL,
 revoked_at TIMESTAMPTZ,
 CHECK(expires_at>created_at)
);
CREATE INDEX auth_sessions_user_expiry ON auth_sessions(user_id,expires_at);
CREATE TABLE auth_challenges (
 challenge_id UUID PRIMARY KEY,
 user_id BIGINT REFERENCES users(user_id),
 normalized_email VARCHAR(320) NOT NULL,
 purpose VARCHAR(16) NOT NULL CHECK(purpose IN ('VERIFY_EMAIL','RESET_PASSWORD')),
 code_digest CHAR(64) NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 expires_at TIMESTAMPTZ NOT NULL,
 consumed_at TIMESTAMPTZ,
 completed_at TIMESTAMPTZ,
 grant_digest CHAR(64) UNIQUE,
 grant_expires_at TIMESTAMPTZ,
 pending_password_hash TEXT,
 pending_display_name VARCHAR(255),
 CHECK(expires_at>created_at),
 CHECK((grant_digest IS NULL)=(grant_expires_at IS NULL)),
 CHECK(pending_password_hash IS NULL OR purpose='VERIFY_EMAIL')
);
CREATE INDEX auth_challenges_email_recent ON auth_challenges(normalized_email,purpose,created_at DESC);
CREATE TABLE auth_rate_limits (
 bucket_key CHAR(64) PRIMARY KEY,
 window_start BIGINT NOT NULL,
 hits INTEGER NOT NULL CHECK(hits>0)
);
CREATE TABLE auth_oauth_states (
 state_digest CHAR(64) PRIMARY KEY,
 return_to VARCHAR(2048) NOT NULL,
 link_user_id BIGINT REFERENCES users(user_id),
 expires_at TIMESTAMPTZ NOT NULL,
 consumed_at TIMESTAMPTZ
);
CREATE INDEX auth_oauth_states_expiry ON auth_oauth_states(expires_at);
CREATE TABLE auth_security_events (
 event_id BIGSERIAL PRIMARY KEY,
 user_id BIGINT REFERENCES users(user_id),
 event_type VARCHAR(40) NOT NULL CHECK(event_type IN
 ('SIGNUP','LOGIN_SUCCESS','LOGIN_FAILURE','LOGOUT','RESET_REQUESTED','RESET_COMPLETED','EMAIL_VERIFIED','GOOGLE_LINKED','PASSWORD_CHANGED','EMAIL_DELIVERY_FAILURE')),
 category VARCHAR(40),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX auth_events_user_time ON auth_security_events(user_id,created_at DESC);
CREATE FUNCTION auth_user_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at=NOW(); RETURN NEW; END $$;
CREATE TRIGGER auth_user_updated_at BEFORE UPDATE OF email,normalized_email,password_hash,email_verified,display_name,avatar_url,account_status,last_login_at
 ON users FOR EACH ROW EXECUTE FUNCTION auth_user_updated_at();
COMMIT;

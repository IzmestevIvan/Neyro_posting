SCHEMA = """
CREATE TABLE IF NOT EXISTS billing_accounts (
 user_id BIGINT PRIMARY KEY REFERENCES users(tg_id) ON DELETE RESTRICT,
 version INTEGER NOT NULL DEFAULT 0,
 entitlement_active BOOLEAN NOT NULL DEFAULT FALSE,
 legacy_until TIMESTAMPTZ,
 legacy_plan TEXT,
 legacy_daily_limit INTEGER,
 needs_review BOOLEAN NOT NULL DEFAULT FALSE,
 scheduled_plan TEXT,
 scheduled_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS billing_quotes (
 id UUID PRIMARY KEY,
 user_id BIGINT NOT NULL REFERENCES users(tg_id),
 plan TEXT NOT NULL,
 kind TEXT NOT NULL,
 amount INTEGER NOT NULL CHECK (amount>=0),
 account_version INTEGER NOT NULL,
 period_id UUID,
 period_start TIMESTAMPTZ NOT NULL,
 period_end TIMESTAMPTZ NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS billing_orders (
 id UUID PRIMARY KEY,
 user_id BIGINT NOT NULL REFERENCES users(tg_id),
 quote_id UUID NOT NULL UNIQUE REFERENCES billing_quotes(id),
 request_id UUID NOT NULL,
 provider_id TEXT UNIQUE,
 plan TEXT NOT NULL,
 kind TEXT NOT NULL,
 amount INTEGER NOT NULL CHECK (amount>0),
 account_version INTEGER NOT NULL,
 period_id UUID,
 period_start TIMESTAMPTZ NOT NULL,
 period_end TIMESTAMPTZ NOT NULL,
 environment TEXT NOT NULL CHECK(environment IN ('test','live')),
 status TEXT NOT NULL DEFAULT 'creating',
 request_body JSONB NOT NULL,
 confirmation_url TEXT,
 receipt_status TEXT,
 applied_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 checked_at TIMESTAMPTZ,
 UNIQUE(user_id,request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS billing_one_unresolved ON billing_orders(user_id)
 WHERE status IN ('creating','pending','review');
CREATE TABLE IF NOT EXISTS billing_periods (
 id UUID PRIMARY KEY,
 user_id BIGINT NOT NULL REFERENCES users(tg_id),
 plan TEXT NOT NULL,
 starts_at TIMESTAMPTZ NOT NULL,
 ends_at TIMESTAMPTZ NOT NULL,
 order_id UUID NOT NULL UNIQUE REFERENCES billing_orders(id),
 revoked BOOLEAN NOT NULL DEFAULT FALSE,
 CHECK(ends_at>starts_at)
);
CREATE INDEX IF NOT EXISTS billing_periods_owner_time ON billing_periods(user_id,starts_at,ends_at);
CREATE TABLE IF NOT EXISTS billing_refunds (
 provider_id TEXT PRIMARY KEY,
 order_id UUID NOT NULL REFERENCES billing_orders(id),
 amount INTEGER NOT NULL,
 status TEXT NOT NULL,
 checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS billing_events (
 id BIGSERIAL PRIMARY KEY,
 order_id UUID REFERENCES billing_orders(id),
 event TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE billing_accounts ADD COLUMN IF NOT EXISTS entitlement_active BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE billing_accounts ADD COLUMN IF NOT EXISTS legacy_until TIMESTAMPTZ;
ALTER TABLE billing_accounts ADD COLUMN IF NOT EXISTS legacy_plan TEXT;
ALTER TABLE billing_accounts ADD COLUMN IF NOT EXISTS legacy_daily_limit INTEGER;
ALTER TABLE billing_accounts ADD COLUMN IF NOT EXISTS needs_review BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS receipt_mode TEXT NOT NULL DEFAULT 'yookassa_54fz';
ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS receipt_email TEXT;
ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS npd_receipt_url TEXT;
ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS npd_receipt_attached_at TIMESTAMPTZ;
"""

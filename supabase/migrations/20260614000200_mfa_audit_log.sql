-- ═══════════════════════════════════════════════════════════════════════════
-- MFA Audit Log Migration (2026-06-14)
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Adds algochains_mfa_audit_log for operator visibility into MFA lifecycle
-- events (enrollment, verification, removal, login step-up).
-- Service-role only — never exposed to users or subscribers.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS algochains_mfa_audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    event           TEXT NOT NULL
                    CHECK (event IN ('enroll', 'verify', 'remove', 'login_stepup', 'failed_verify')),
    factor_type     TEXT NOT NULL DEFAULT 'totp'
                    CHECK (factor_type IN ('totp', 'phone', 'webauthn')),
    factor_id       TEXT,                           -- Supabase MFA factor UUID
    ip              TEXT,
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mfa_audit_user_id   ON algochains_mfa_audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_mfa_audit_event     ON algochains_mfa_audit_log(event);
CREATE INDEX IF NOT EXISTS idx_mfa_audit_created   ON algochains_mfa_audit_log(created_at DESC);

ALTER TABLE algochains_mfa_audit_log ENABLE ROW LEVEL SECURITY;

-- Service role only — MFA audit is operator-only visibility
CREATE POLICY "Service role manages MFA audit" ON algochains_mfa_audit_log
    USING (auth.role() = 'service_role');


-- ─────────────────────────────────────────────────────────────────────────────
-- RPC: log_mfa_event (called by MCP auth module after MFA operations)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION log_mfa_event(
    p_user_id    UUID,
    p_event      TEXT,
    p_type       TEXT DEFAULT 'totp',
    p_factor_id  TEXT DEFAULT NULL,
    p_ip         TEXT DEFAULT NULL,
    p_user_agent TEXT DEFAULT NULL
)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
AS $$
    INSERT INTO algochains_mfa_audit_log (user_id, event, factor_type, factor_id, ip, user_agent)
    VALUES (p_user_id, p_event, p_type, p_factor_id, p_ip, p_user_agent);
$$;

COMMENT ON TABLE algochains_mfa_audit_log IS
    'Operator audit log for MFA lifecycle events. Service-role only. '
    'Written by MCP auth module after enroll/verify/remove/login_stepup operations.';

COMMIT;

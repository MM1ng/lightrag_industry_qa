CREATE TABLE conversation_sessions (
    conversation_id TEXT PRIMARY KEY,
    selected_cycle_id INTEGER CHECK (
        selected_cycle_id IS NULL OR selected_cycle_id BETWEEN 1 AND 2205
    ),
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE request_summaries (
    request_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversation_sessions(conversation_id)
        ON DELETE CASCADE,
    intent TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_request_summaries_conversation
    ON request_summaries(conversation_id, created_at);

CREATE TABLE trace_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    conversation_id TEXT REFERENCES conversation_sessions(conversation_id)
        ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_trace_events_request ON trace_events(request_id, event_id);

CREATE TABLE diagnoses (
    diagnosis_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversation_sessions(conversation_id)
        ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_diagnoses_conversation
    ON diagnoses(conversation_id, created_at DESC, diagnosis_id DESC);

CREATE TABLE work_orders (
    work_order_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    diagnosis_id TEXT NOT NULL REFERENCES diagnoses(diagnosis_id),
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status = 'DRAFT'),
    approval_status TEXT NOT NULL DEFAULT 'PENDING_REVIEW' CHECK (
        approval_status IN ('PENDING_REVIEW', 'REVIEWED', 'REJECTED')
    ),
    executed INTEGER NOT NULL DEFAULT 0 CHECK (executed = 0),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_work_orders_conversation
    ON work_orders(conversation_id, created_at DESC);

CREATE TABLE risk_reviews (
    review_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversation_sessions(conversation_id)
        ON DELETE CASCADE,
    risk_category TEXT NOT NULL,
    restricted_answer_hash TEXT NOT NULL CHECK (
        substr(restricted_answer_hash, 1, 7) = 'sha256:'
        AND length(restricted_answer_hash) = 71
        AND substr(restricted_answer_hash, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'PENDING_REVIEW' CHECK (
        status IN ('PENDING_REVIEW', 'REVIEWED', 'REJECTED')
    ),
    decision TEXT,
    reviewer_id TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    CHECK (
        (status = 'PENDING_REVIEW' AND decision IS NULL
            AND reviewer_id IS NULL AND reviewed_at IS NULL)
        OR
        (status IN ('REVIEWED', 'REJECTED') AND decision IS NOT NULL
            AND reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL)
    )
);

CREATE TABLE work_order_reviews (
    review_id TEXT PRIMARY KEY,
    work_order_id TEXT NOT NULL REFERENCES work_orders(work_order_id),
    request_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'PENDING_REVIEW' CHECK (
        status IN ('PENDING_REVIEW', 'REVIEWED', 'REJECTED')
    ),
    decision TEXT,
    reviewer_id TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    CHECK (
        (status = 'PENDING_REVIEW' AND decision IS NULL
            AND reviewer_id IS NULL AND reviewed_at IS NULL)
        OR
        (status IN ('REVIEWED', 'REJECTED') AND decision IS NOT NULL
            AND reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL)
    )
);

CREATE TABLE ingest_jobs (
    job_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'RECONCILE_REQUIRED')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    lease_owner TEXT,
    lease_expires_at TEXT,
    remote_call_started INTEGER NOT NULL DEFAULT 0 CHECK (remote_call_started IN (0, 1)),
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (status = 'RUNNING' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR
        (status != 'RUNNING' AND lease_owner IS NULL AND lease_expires_at IS NULL)
    )
);
CREATE INDEX idx_ingest_jobs_claim
    ON ingest_jobs(status, lease_expires_at, created_at);

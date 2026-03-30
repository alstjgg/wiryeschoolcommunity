-- 위례인생학교 DB 테이블 생성 스크립트
-- 사용법: psql $DATABASE_URL -f tests/data/create_tables.sql
--
-- 이 스크립트는 두 영역의 테이블을 생성합니다:
--   1. Chainlit 채팅 기록 (users, threads, steps, elements, feedbacks)
--   2. 비즈니스 데이터 (members, member_records, course_records, applications, deposits, attendance)
--
-- 모든 테이블은 IF NOT EXISTS이므로 재실행 안전합니다.

BEGIN;

-- ========================================
-- 1. Chainlit 채팅 기록 테이블
-- ========================================

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    identifier  TEXT UNIQUE NOT NULL,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS threads (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    user_id     TEXT REFERENCES users(id),
    metadata    JSONB DEFAULT '{}',
    tags        TEXT[] DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS steps (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT REFERENCES threads(id) ON DELETE CASCADE,
    parent_id   TEXT,
    name        TEXT,
    type        TEXT,
    input       TEXT,
    output      TEXT,
    metadata    JSONB DEFAULT '{}',
    start_time  TIMESTAMPTZ,
    end_time    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS elements (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT,
    type        TEXT,
    name        TEXT,
    url         TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ========================================
-- 2. 비즈니스 데이터 테이블
-- ========================================

CREATE TABLE IF NOT EXISTS members (
    name_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    phone           TEXT,
    address         TEXT,
    grade           TEXT DEFAULT '회원',
    is_exception    BOOLEAN DEFAULT FALSE,
    course_count    INTEGER DEFAULT 0,
    avg_attendance  REAL DEFAULT 0.0,
    last_term       TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS member_records (
    id          SERIAL PRIMARY KEY,
    name_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    changed_at  TIMESTAMPTZ DEFAULT NOW(),
    grade_from  TEXT,
    grade_to    TEXT,
    reason      TEXT,
    term_id     TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS course_records (
    id          SERIAL PRIMARY KEY,
    name_id     TEXT NOT NULL,
    term_id     TEXT NOT NULL,
    course_name TEXT NOT NULL,
    attendance  REAL DEFAULT 0.0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS applications (
    id                  SERIAL PRIMARY KEY,
    term_id             TEXT NOT NULL,
    name_id             TEXT NOT NULL,
    name                TEXT NOT NULL,
    type                TEXT NOT NULL,
    course_name         TEXT,
    expected_amount     INTEGER,
    paid_amount         INTEGER,
    payment_status      TEXT DEFAULT 'not_paid',
    review_reason       TEXT,
    processing_status   TEXT,
    payment_time        TEXT,
    payer_name          TEXT,
    memo                TEXT,
    phone               TEXT,
    address             TEXT,
    processed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_applications
ON applications (term_id, name_id, type, COALESCE(course_name, ''));

CREATE TABLE IF NOT EXISTS deposits (
    id                  SERIAL PRIMARY KEY,
    term_id             TEXT NOT NULL,
    transaction_time    TEXT,
    amount              INTEGER,
    payer_name          TEXT,
    memo                TEXT,
    match_status        TEXT DEFAULT 'unmatched',
    matched_name_ids    TEXT[],
    processing_status   TEXT,
    review_reason       TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_deposits
ON deposits (term_id, COALESCE(transaction_time, ''), amount, COALESCE(payer_name, ''), COALESCE(memo, ''));

CREATE TABLE IF NOT EXISTS attendance (
    id              SERIAL PRIMARY KEY,
    term_id         TEXT NOT NULL,
    course_name     TEXT NOT NULL,
    student_name    TEXT NOT NULL,
    session_data    JSONB DEFAULT '{}',
    attendance_rate REAL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance
ON attendance (term_id, course_name, student_name);

CREATE TABLE IF NOT EXISTS feedbacks (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT,
    step_id     TEXT,
    value       INTEGER,
    comment     TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

COMMIT;

"""PostgreSQL 비즈니스 데이터 레이어 — DB SoT

USE_DB_SOT=true 환경변수가 설정되고 DATABASE_URL이 있을 때 활성화.
chat_data_layer.py(채팅 기록)와 별도의 asyncpg 풀 사용.

DB에는 상태 코드(confirmed, not_paid 등)를 저장하고,
앱 코드는 이모지(✅정상, ❌미입금 등)를 사용한다.
변환은 이 모듈의 경계에서 수행.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

# ================================================================ Schema ====

_BUSINESS_SCHEMA_SQL = """
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
    payment_status      TEXT DEFAULT 'not_paid',
    review_reason       TEXT,
    processing_status   TEXT,
    payment_time        TEXT,
    payer_name          TEXT,
    phone               TEXT,
    address             TEXT,
    applied_at          TEXT,
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
"""

# ======================================================== Status Mapping ====

STATUS_TO_EMOJI = {
    "confirmed": "✅정상",
    "needs_review": "🔶확인필요",
    "not_paid": "❌미입금",
    "name_mismatch": "⚠️이름불일치",
    "duplicate": "🔄중복",
    "exempted": "💎면제",
}
EMOJI_TO_STATUS = {v: k for k, v in STATUS_TO_EMOJI.items()}


def _emoji_to_code(emoji: str) -> str:
    """이모지 상태 → DB 코드. 알 수 없는 값은 그대로 반환."""
    return EMOJI_TO_STATUS.get(emoji, emoji)


def _code_to_emoji(code: str) -> str:
    """DB 코드 → 이모지 상태. 알 수 없는 값은 그대로 반환."""
    return STATUS_TO_EMOJI.get(code, code)


def _to_datetime(val) -> datetime | None:
    """processed_at 등 TIMESTAMPTZ 컬럼 값을 datetime으로 변환.

    str이면 fromisoformat 파싱, datetime이면 그대로, None/빈값이면 None.
    asyncpg는 TIMESTAMPTZ에 str을 받지 않으므로 이 변환이 필요.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str) and val:
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            return None
    return None


# ======================================================== Connection Pool ====

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """비즈니스 테이블용 asyncpg 풀 (싱글턴). 첫 호출 시 스키마 자동 생성."""
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            raise RuntimeError("DATABASE_URL 환경변수가 설정되지 않았습니다.")
        _pool = await asyncpg.create_pool(dsn)
        async with _pool.acquire() as conn:
            await conn.execute(_BUSINESS_SCHEMA_SQL)
        logger.info("Business DB pool created, schema ensured.")
    return _pool


async def close_pool() -> None:
    """풀 종료 (테스트/셧다운용)."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# ============================================================== Members ====

async def load_members() -> list[dict]:
    """회원목록 전체 로드. Sheets 호환 dict 형식 반환."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM members ORDER BY name_id"
        )
    return [
        {
            "이름ID": r["name_id"],
            "이름": r["name"],
            "전화번호": r["phone"] or "",
            "주소": r["address"] or "",
            "등급": r["grade"] or "회원",
            "예외여부": "TRUE" if r["is_exception"] else "",
            "수강count": str(r["course_count"] or 0),
            "출석률(누적)": str(r["avg_attendance"] or ""),
            "마지막수강회차": r["last_term"] or "",
        }
        for r in rows
    ]


async def upsert_members(members: list[dict]) -> None:
    """회원목록 upsert (전체 덮어쓰기 대응)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for m in members:
                is_exc = str(m.get("예외여부", "")).upper() == "TRUE"
                await conn.execute(
                    """
                    INSERT INTO members (name_id, name, phone, address, grade,
                                         is_exception, course_count, avg_attendance, last_term)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (name_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        phone = EXCLUDED.phone,
                        address = EXCLUDED.address,
                        grade = EXCLUDED.grade,
                        is_exception = EXCLUDED.is_exception,
                        course_count = EXCLUDED.course_count,
                        avg_attendance = EXCLUDED.avg_attendance,
                        last_term = EXCLUDED.last_term,
                        updated_at = NOW()
                    """,
                    m.get("이름ID", ""),
                    m.get("이름", ""),
                    m.get("전화번호", ""),
                    m.get("주소", ""),
                    m.get("등급", "회원"),
                    is_exc,
                    int(m.get("수강count", 0) or 0),
                    float(m.get("출석률(누적)", 0) or 0),
                    m.get("마지막수강회차", "") or None,
                )


async def update_member_grade(name_id: str, new_grade: str) -> None:
    """단일 회원 등급 변경."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE members SET grade = $1, updated_at = NOW() WHERE name_id = $2",
            new_grade, name_id,
        )


# ======================================================= Member Records ====

async def insert_member_records(records: list[dict]) -> None:
    """회원기록 (등급 변경 이력) 일괄 INSERT."""
    if not records:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for r in records:
                await conn.execute(
                    """
                    INSERT INTO member_records (name_id, name, changed_at, grade_from, grade_to, reason, term_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    r.get("이름ID", ""),
                    r.get("이름", ""),
                    r.get("변경일시", ""),
                    r.get("변경전등급", ""),
                    r.get("변경후등급", ""),
                    r.get("사유", ""),
                    r.get("관련회차", ""),
                )


# ======================================================= Course Records ====

async def insert_course_records(records: list[dict]) -> None:
    """수강기록 일괄 INSERT."""
    if not records:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for r in records:
                rate = 0.0
                try:
                    rate = float(r.get("출석률", 0))
                except (ValueError, TypeError):
                    pass
                await conn.execute(
                    """
                    INSERT INTO course_records (name_id, term_id, course_name, attendance)
                    VALUES ($1, $2, $3, $4)
                    """,
                    r.get("이름ID", ""),
                    r.get("회차", ""),
                    r.get("과목명", ""),
                    rate,
                )


async def load_course_records(name_id: str | None = None) -> list[dict]:
    """수강기록 로드. name_id 지정 시 해당 회원만, None이면 전체."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if name_id:
            rows = await conn.fetch(
                "SELECT * FROM course_records WHERE name_id = $1 ORDER BY created_at",
                name_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM course_records ORDER BY created_at"
            )
    return [
        {
            "이름ID": r["name_id"],
            "회차": r["term_id"],
            "과목명": r["course_name"],
            "출석률": str(r["attendance"] or 0),
        }
        for r in rows
    ]


# =========================================================== Applications ====

async def upsert_applications(term_id: str, applications: list[dict]) -> None:
    """통합 신청서 upsert. Key: (term_id, name_id, type, course_name).

    이모지 → 코드 변환 포함.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for a in applications:
                amount = 0
                try:
                    amount = int(a.get("예상금액", 0) or 0)
                except (ValueError, TypeError):
                    pass

                await conn.execute(
                    """
                    INSERT INTO applications (
                        term_id, name_id, name, type, course_name,
                        expected_amount, payment_status, review_reason,
                        processing_status, payment_time, payer_name,
                        phone, address, applied_at, processed_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                    ON CONFLICT (term_id, name_id, type, COALESCE(course_name, ''))
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        expected_amount = EXCLUDED.expected_amount,
                        payment_status = EXCLUDED.payment_status,
                        review_reason = EXCLUDED.review_reason,
                        processing_status = EXCLUDED.processing_status,
                        payment_time = EXCLUDED.payment_time,
                        payer_name = EXCLUDED.payer_name,
                        phone = EXCLUDED.phone,
                        address = EXCLUDED.address,
                        applied_at = EXCLUDED.applied_at,
                        processed_at = COALESCE(EXCLUDED.processed_at, applications.processed_at),
                        updated_at = NOW()
                    """,
                    term_id,
                    a.get("이름ID", ""),
                    a.get("이름", ""),
                    a.get("유형", ""),
                    a.get("과목명", "") or None,
                    amount,
                    _emoji_to_code(a.get("입금현황", "❌미입금")),
                    a.get("확인사유", "") or None,
                    a.get("처리상태", "") or None,
                    a.get("입금시간", "") or None,
                    a.get("입금자명(적요)", "") or None,
                    a.get("전화번호", "") or None,
                    a.get("주소", "") or None,
                    a.get("신청일", "") or None,
                    _to_datetime(a.get("processed_at")),
                )


async def load_applications(term_id: str) -> list[dict]:
    """통합 신청서 로드. 코드 → 이모지 변환 포함. Sheets 호환 dict 형식."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM applications WHERE term_id = $1 ORDER BY id",
            term_id,
        )
    return [
        {
            "이름ID": r["name_id"],
            "이름": r["name"],
            "유형": r["type"],
            "과목명": r["course_name"] or "",
            "예상금액": str(r["expected_amount"] or ""),
            "입금현황": _code_to_emoji(r["payment_status"] or "not_paid"),
            "확인사유": r["review_reason"] or "",
            "처리상태": r["processing_status"] or "",
            "입금시간": r["payment_time"] or "",
            "입금자명(적요)": r["payer_name"] or "",
            "전화번호": r["phone"] or "",
            "주소": r["address"] or "",
            "신청일": r["applied_at"] or "",
        }
        for r in rows
    ]


# ============================================================== Deposits ====

async def insert_deposits(term_id: str, deposits: list[dict]) -> None:
    """입금내역 전건 INSERT."""
    if not deposits:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for d in deposits:
                amount = 0
                try:
                    amount = int(d.get("입금", 0) or d.get("amount", 0) or 0)
                except (ValueError, TypeError):
                    pass
                await conn.execute(
                    """
                    INSERT INTO deposits (term_id, transaction_time, amount, payer_name, memo)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (term_id, COALESCE(transaction_time, ''), amount,
                                 COALESCE(payer_name, ''), COALESCE(memo, ''))
                    DO NOTHING
                    """,
                    term_id,
                    d.get("거래일시", "") or d.get("transaction_time", "") or None,
                    amount,
                    d.get("의뢰인", "") or d.get("payer_name", "") or None,
                    d.get("적요", "") or d.get("memo", "") or None,
                )


async def load_deposits(
    term_id: str,
    unmatched_only: bool = False,
) -> list[dict]:
    """입금내역 로드. unmatched_only=True이면 미매칭 건만."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if unmatched_only:
            rows = await conn.fetch(
                "SELECT * FROM deposits WHERE term_id = $1 AND match_status = 'unmatched' ORDER BY id",
                term_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM deposits WHERE term_id = $1 ORDER BY id",
                term_id,
            )
    return [
        {
            "id": r["id"],
            "거래일시": r["transaction_time"] or "",
            "입금": r["amount"] or 0,
            "입금자명": r["payer_name"] or "",
            "적요": r["memo"] or "",
            "match_status": r["match_status"] or "unmatched",
            "matched_name_ids": list(r["matched_name_ids"] or []),
            "처리상태": r["processing_status"] or "",
            "확인사유": r["review_reason"] or "",
        }
        for r in rows
    ]


async def update_deposit_match(
    deposit_id: int,
    match_status: str,
    matched_name_ids: list[str] | None = None,
) -> None:
    """입금내역 매칭 결과 업데이트."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE deposits SET match_status = $1, matched_name_ids = $2
            WHERE id = $3
            """,
            match_status,
            matched_name_ids or [],
            deposit_id,
        )


# ============================================================= Attendance ====

async def upsert_attendance(
    term_id: str,
    course_name: str,
    student_name: str,
    session_data: dict,
    attendance_rate: float | None = None,
) -> None:
    """출석 데이터 upsert. session_data: {"1": "O", "2": "", ...}"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO attendance (term_id, course_name, student_name, session_data, attendance_rate)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (term_id, course_name, student_name)
            DO UPDATE SET
                session_data = EXCLUDED.session_data,
                attendance_rate = EXCLUDED.attendance_rate
            """,
            term_id,
            course_name,
            student_name,
            json.dumps(session_data),
            attendance_rate,
        )


async def load_attendance(
    term_id: str,
    course_name: str | None = None,
) -> list[dict]:
    """출석 데이터 로드."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if course_name:
            rows = await conn.fetch(
                "SELECT * FROM attendance WHERE term_id = $1 AND course_name = $2 ORDER BY student_name",
                term_id, course_name,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM attendance WHERE term_id = $1 ORDER BY course_name, student_name",
                term_id,
            )
    return [
        {
            "과목명": r["course_name"],
            "이름": r["student_name"],
            "session_data": json.loads(r["session_data"]) if r["session_data"] else {},
            "출석률": r["attendance_rate"],
        }
        for r in rows
    ]


async def update_attendance_rate(
    term_id: str,
    course_name: str,
    student_name: str,
    rate: float,
) -> None:
    """출석률 단건 업데이트."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE attendance SET attendance_rate = $1
            WHERE term_id = $2 AND course_name = $3 AND student_name = $4
            """,
            rate, term_id, course_name, student_name,
        )


# ============================================================== Feedbacks ====

async def upsert_feedback(feedback: dict) -> str:
    """피드백 upsert. Chainlit thumbs up/down 저장."""
    import uuid
    pool = await get_pool()
    feedback_id = feedback.get("id") or str(uuid.uuid4())
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO feedbacks (id, thread_id, step_id, value, comment)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE
                SET value = EXCLUDED.value, comment = EXCLUDED.comment
            """,
            feedback_id,
            feedback.get("thread_id", ""),
            feedback.get("step_id", ""),
            feedback.get("value", 0),
            feedback.get("comment", ""),
        )
    return feedback_id

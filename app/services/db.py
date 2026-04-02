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
    """비즈니스 테이블용 asyncpg 풀 (싱글턴). 테이블 미존재 시 에러 발생."""
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            raise RuntimeError("DATABASE_URL 환경변수가 설정되지 않았습니다.")
        _pool = await asyncpg.create_pool(dsn)
        async with _pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'members')"
            )
            if not exists:
                raise RuntimeError(
                    "DB 테이블이 존재하지 않습니다. "
                    "psql $DATABASE_URL -f tests/data/create_tables.sql 을 먼저 실행하세요."
                )
        logger.info("Business DB pool created, tables verified.")
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
    """수강기록 일괄 UPSERT. 동일 (name_id, term_id, course_name) 재실행 시 출석률만 업데이트."""
    if not records:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for r in records:
                rate = 0.0
                try:
                    rate = float(r.get("출석률", 0) or r.get("attendance", 0) or 0)
                except (ValueError, TypeError):
                    pass
                await conn.execute(
                    """
                    INSERT INTO course_records (name_id, term_id, course_name, attendance)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (name_id, term_id, course_name)
                    DO UPDATE SET
                        attendance = EXCLUDED.attendance,
                        created_at = NOW()
                    """,
                    r.get("이름ID", "") or r.get("name_id", ""),
                    r.get("회차", "") or r.get("term_id", ""),
                    r.get("과목명", "") or r.get("course_name", ""),
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
                expected = 0
                try:
                    expected = int(a.get("예상금액", 0) or 0)
                except (ValueError, TypeError):
                    pass
                paid = None
                try:
                    v = a.get("입금액", "") or ""
                    if v:
                        paid = int(v)
                except (ValueError, TypeError):
                    pass

                await conn.execute(
                    """
                    INSERT INTO applications (
                        term_id, name_id, name, type, course_name,
                        expected_amount, paid_amount, payment_status, review_reason,
                        processing_status, payment_time, payer_name, memo,
                        phone, address, processed_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                    ON CONFLICT (term_id, name_id, type, COALESCE(course_name, ''))
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        expected_amount = EXCLUDED.expected_amount,
                        paid_amount = CASE
                            WHEN applications.processing_status IN ('등록완료','환불완료','취소완료','보류')
                              OR applications.payment_status IN ('exempted','confirmed')
                            THEN applications.paid_amount
                            ELSE EXCLUDED.paid_amount END,
                        payment_status = CASE
                            WHEN applications.processing_status IN ('등록완료','환불완료','취소완료','보류')
                              OR applications.payment_status IN ('exempted','confirmed')
                            THEN applications.payment_status
                            ELSE EXCLUDED.payment_status END,
                        review_reason = CASE
                            WHEN applications.processing_status IN ('등록완료','환불완료','취소완료','보류')
                              OR applications.payment_status IN ('exempted','confirmed')
                            THEN applications.review_reason
                            ELSE EXCLUDED.review_reason END,
                        processing_status = COALESCE(NULLIF(EXCLUDED.processing_status, ''), applications.processing_status),
                        payment_time = CASE
                            WHEN applications.processing_status IN ('등록완료','환불완료','취소완료','보류')
                              OR applications.payment_status IN ('exempted','confirmed')
                            THEN applications.payment_time
                            ELSE EXCLUDED.payment_time END,
                        payer_name = CASE
                            WHEN applications.processing_status IN ('등록완료','환불완료','취소완료','보류')
                              OR applications.payment_status IN ('exempted','confirmed')
                            THEN applications.payer_name
                            ELSE EXCLUDED.payer_name END,
                        memo = CASE
                            WHEN applications.processing_status IN ('등록완료','환불완료','취소완료','보류')
                              OR applications.payment_status IN ('exempted','confirmed')
                            THEN applications.memo
                            ELSE EXCLUDED.memo END,
                        phone = EXCLUDED.phone,
                        address = EXCLUDED.address,
                        processed_at = COALESCE(EXCLUDED.processed_at, applications.processed_at),
                        updated_at = NOW()
                    """,
                    term_id,
                    a.get("이름ID", ""),
                    a.get("이름", ""),
                    a.get("유형", ""),
                    a.get("과목명", "") or None,
                    expected,
                    paid,
                    _emoji_to_code(a.get("입금현황", "❌미입금")),
                    a.get("확인사유", "") or None,
                    a.get("처리상태", "") or None,
                    a.get("입금시간", "") or None,
                    a.get("의뢰인", "") or None,
                    a.get("적요", "") or None,
                    a.get("전화번호", "") or None,
                    a.get("주소", "") or None,
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
            "회차": r["term_id"],
            "이름ID": r["name_id"],
            "이름": r["name"],
            "유형": r["type"],
            "과목명": r["course_name"] or "",
            "예상금액": str(r["expected_amount"] or ""),
            "입금액": str(r["paid_amount"] or ""),
            "입금현황": _code_to_emoji(r["payment_status"] or "not_paid"),
            "확인사유": r["review_reason"] or "",
            "처리상태": r["processing_status"] or "",
            "입금시간": r["payment_time"] or "",
            "의뢰인": r["payer_name"] or "",
            "적요": r["memo"] or "",
            "전화번호": r["phone"] or "",
            "주소": r["address"] or "",
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
            "의뢰인": r["payer_name"] or "",
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


async def sync_deposit_processing_status(term_id: str, rows: list[dict]) -> int:
    """Sheets 미확인입금의 처리상태를 DB에 역동기화.

    각 row는 {"거래일시": ..., "입금액": ..., "의뢰인": ..., "처리상태": ...} 형태.
    거래일시+금액+의뢰인으로 매칭하여 processing_status 업데이트.
    Returns: 업데이트된 행 수.
    """
    if not rows:
        return 0
    pool = await get_pool()
    updated = 0
    async with pool.acquire() as conn:
        for r in rows:
            ps = (r.get("처리상태") or "").strip()
            if not ps:
                continue
            amount = 0
            try:
                amount = int(r.get("입금액", 0) or 0)
            except (ValueError, TypeError):
                pass
            result = await conn.execute(
                """
                UPDATE deposits SET processing_status = $1
                WHERE term_id = $2
                  AND COALESCE(transaction_time, '') = $3
                  AND amount = $4
                  AND COALESCE(payer_name, '') = $5
                  AND (processing_status IS NULL OR processing_status != $1)
                """,
                ps,
                term_id,
                r.get("거래일시", "") or "",
                amount,
                r.get("의뢰인", "") or "",
            )
            if result and result.split()[-1] != "0":
                updated += 1
    return updated


async def sync_application_processing_status(term_id: str, rows: list[dict]) -> int:
    """Sheets 신청기록의 처리상태를 DB에 역동기화.

    각 row는 {"이름ID": ..., "유형": ..., "과목명": ..., "처리상태": ...} 형태.
    (term_id, name_id, type, course_name) 복합키로 매칭하여 processing_status 업데이트.
    Returns: 업데이트된 행 수.
    """
    if not rows:
        return 0
    pool = await get_pool()
    updated = 0
    async with pool.acquire() as conn:
        for r in rows:
            ps = (r.get("처리상태") or "").strip()
            if not ps:
                continue
            result = await conn.execute(
                """
                UPDATE applications SET processing_status = $1
                WHERE term_id = $2
                  AND name_id = $3
                  AND type = $4
                  AND COALESCE(course_name, '') = $5
                  AND (processing_status IS NULL OR processing_status = '' OR processing_status != $1)
                """,
                ps,
                term_id,
                r.get("이름ID", ""),
                r.get("유형", ""),
                r.get("과목명", "") or "",
            )
            if result and result.split()[-1] != "0":
                updated += 1
    return updated


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

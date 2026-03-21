"""PostgreSQL 비즈니스 데이터 레이어 (asyncpg 기반)

chat_data_layer.py 와 같은 DB 인스턴스를 공유하지만,
비즈니스 테이블(members, students, enrollment_records 등)을 담당한다.

DATABASE_URL 환경변수 필수.
"""

import os
from typing import Optional

import asyncpg

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS members (
    name_id             TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    gender              TEXT,
    phone               TEXT,
    address             TEXT,
    age                 INTEGER,
    grade               TEXT DEFAULT '회원',
    enrollment_count    INTEGER DEFAULT 0,
    attendance_rate     NUMERIC(5,2),
    last_term           TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS enrollment_records (
    id              SERIAL PRIMARY KEY,
    name_id         TEXT NOT NULL,
    term_id         TEXT NOT NULL,
    course_name     TEXT NOT NULL,
    attendance_rate NUMERIC(5,2),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (name_id, term_id, course_name)
);

CREATE TABLE IF NOT EXISTS member_signups (
    id          SERIAL PRIMARY KEY,
    name_id     TEXT NOT NULL,
    name        TEXT,
    phone       TEXT,
    address     TEXT,
    birth_date  TEXT,
    gender      TEXT,
    signup_date DATE,
    join_term   TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fullmember_signups (
    id          SERIAL PRIMARY KEY,
    name_id     TEXT NOT NULL,
    name        TEXT,
    phone       TEXT,
    address     TEXT,
    birth_date  TEXT,
    signup_date DATE,
    join_term   TEXT,
    start_term  TEXT,
    end_term    TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS students (
    term_id             TEXT NOT NULL,
    name_id             TEXT NOT NULL,
    course_name         TEXT NOT NULL,
    payment_time        TEXT,
    payment_memo        TEXT,
    note                TEXT,
    payment_status      TEXT DEFAULT '❌미입금',
    registration_status TEXT DEFAULT '',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (term_id, name_id, course_name)
);

CREATE TABLE IF NOT EXISTS attendance (
    term_id         TEXT NOT NULL,
    name_id         TEXT NOT NULL,
    course_name     TEXT NOT NULL,
    session_1       TEXT DEFAULT '',
    session_2       TEXT DEFAULT '',
    session_3       TEXT DEFAULT '',
    session_4       TEXT DEFAULT '',
    session_5       TEXT DEFAULT '',
    session_6       TEXT DEFAULT '',
    session_7       TEXT DEFAULT '',
    session_8       TEXT DEFAULT '',
    session_9       TEXT DEFAULT '',
    session_10      TEXT DEFAULT '',
    session_11      TEXT DEFAULT '',
    session_12      TEXT DEFAULT '',
    attendance_rate NUMERIC(5,2),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (term_id, name_id, course_name)
);
"""

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL 환경변수가 설정되지 않았습니다.")
        _pool = await asyncpg.create_pool(dsn)
        async with _pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)
    return _pool


# ---------------------------------------------------------------- Members ----

async def load_members() -> list[dict]:
    """회원관리 전체 로드"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM members ORDER BY name_id")
    return [dict(r) for r in rows]


async def upsert_member(data: dict) -> None:
    """단건 회원 upsert"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO members
                (name_id, name, gender, phone, address, age, grade,
                 enrollment_count, attendance_rate, last_term)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (name_id) DO UPDATE
                SET name             = EXCLUDED.name,
                    gender           = COALESCE(EXCLUDED.gender, members.gender),
                    phone            = COALESCE(EXCLUDED.phone, members.phone),
                    address          = COALESCE(EXCLUDED.address, members.address),
                    age              = COALESCE(EXCLUDED.age, members.age),
                    grade            = EXCLUDED.grade,
                    enrollment_count = EXCLUDED.enrollment_count,
                    attendance_rate  = COALESCE(EXCLUDED.attendance_rate, members.attendance_rate),
                    last_term        = COALESCE(EXCLUDED.last_term, members.last_term),
                    updated_at       = NOW()
            """,
            data.get("name_id"),
            data.get("name"),
            data.get("gender"),
            data.get("phone"),
            data.get("address"),
            data.get("age"),
            data.get("grade", "회원"),
            data.get("enrollment_count", 0),
            data.get("attendance_rate"),
            data.get("last_term"),
        )


async def bulk_upgrade_members(name_ids: list[str], term_id: str) -> int:
    """확정 수강생: 회원→준회원 승격, 수강count+1, 마지막수강학기 갱신"""
    if not name_ids:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE members
            SET grade            = CASE WHEN grade = '회원' THEN '준회원' ELSE grade END,
                enrollment_count = enrollment_count + 1,
                last_term        = $2,
                updated_at       = NOW()
            WHERE name_id = ANY($1::text[])
            """,
            name_ids,
            term_id,
        )
    # result 형식: "UPDATE N"
    return int(result.split()[-1]) if result else 0


# --------------------------------------------------------------- Students ----

async def upsert_students(term_id: str, applicants: list[dict]) -> int:
    """신청자 목록을 students 테이블에 upsert.

    이미 존재하는 행은 건드리지 않음 (입금현황·등록상태 보존).
    """
    if not applicants:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO students (term_id, name_id, course_name, payment_status)
            VALUES ($1, $2, $3, '❌미입금')
            ON CONFLICT (term_id, name_id, course_name) DO NOTHING
            """,
            [(term_id, a["이름ID"], a["강좌명"]) for a in applicants],
        )
    return len(applicants)


async def load_students(term_id: str) -> list[dict]:
    """해당 회차 수강생 전체 로드"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM students WHERE term_id = $1 ORDER BY name_id, course_name",
            term_id,
        )
    result = []
    for r in rows:
        d = dict(r)
        d["이름"] = d["name_id"].rstrip("0123456789")
        # payment.py 기존 코드와 호환되는 키 추가
        d["이름ID"] = d["name_id"]
        d["강좌명"] = d["course_name"]
        result.append(d)
    return result


async def update_student_payments(
    term_id: str,
    results: list[dict],
    exempted: list[dict],
) -> int:
    """매칭 결과와 면제 목록을 students 테이블에 반영"""
    WRITABLE = ("✅정상", "🔶확인필요", "⚠️이름불일치")
    pool = await get_pool()
    updated = 0
    async with pool.acquire() as conn:
        # 면제(정회원) 처리
        for e in exempted:
            await conn.execute(
                """
                UPDATE students
                SET payment_status      = '💎면제',
                    registration_status = '정상등록',
                    updated_at          = NOW()
                WHERE term_id = $1 AND name_id = $2 AND course_name = $3
                """,
                term_id, e["이름ID"], e["강좌명"],
            )
            updated += 1

        # 매칭 결과 반영
        for r in results:
            if not r.get("매칭ID") or r["상태"] not in WRITABLE:
                continue
            reg_status = "정상등록" if r["상태"] == "✅정상" else ""
            await conn.execute(
                """
                UPDATE students
                SET payment_time        = $4,
                    payment_memo        = $5,
                    payment_status      = $6,
                    registration_status = $7,
                    updated_at          = NOW()
                WHERE term_id = $1 AND name_id = $2 AND course_name = $3
                """,
                term_id,
                r["매칭ID"],
                r.get("매칭강좌", ""),
                r.get("거래일시", ""),
                r.get("적요", ""),
                r["상태"],
                reg_status,
            )
            updated += 1

    return updated


async def load_registered_students(term_id: str) -> list[dict]:
    """등록상태='정상등록' 수강생만 로드 (출석부 생성용)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM students
            WHERE term_id = $1 AND registration_status = '정상등록'
            ORDER BY course_name, name_id
            """,
            term_id,
        )
    result = []
    for r in rows:
        d = dict(r)
        d["이름"] = d["name_id"].rstrip("0123456789")
        d["이름ID"] = d["name_id"]
        d["과목명"] = d["course_name"]
        result.append(d)
    return result


# -------------------------------------------------------- Enrollment Records ----

async def add_enrollment_records(matched_results: list[dict], term_id: str) -> int:
    """확정된 수강 이력을 enrollment_records에 추가"""
    new_records = [
        (r["매칭ID"], term_id, r.get("매칭강좌", ""))
        for r in matched_results
        if r.get("매칭ID") and r["상태"] == "✅정상"
    ]
    if not new_records:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO enrollment_records (name_id, term_id, course_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (name_id, term_id, course_name) DO NOTHING
            """,
            new_records,
        )
    return len(new_records)


# ------------------------------------------------ Member / Fullmember Signups ----

async def load_member_signups() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM member_signups ORDER BY signup_date")
    return [dict(r) for r in rows]


async def load_fullmember_signups() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM fullmember_signups ORDER BY signup_date")
    return [dict(r) for r in rows]

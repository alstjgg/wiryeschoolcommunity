"""v4.0 스키마 마이그레이션 — 기존 테이블 ALTER + deposits 생성

Usage:
    DATABASE_URL="..." python scripts/migrate_v4.py

변경 사항:
  - applications: registration_status → processing_status, +processed_at, -start_term, -end_term
  - deposits: 신규 테이블 생성
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import asyncpg


_MIGRATION_SQL = """
-- applications: registration_status → processing_status
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'applications' AND column_name = 'registration_status'
    ) THEN
        ALTER TABLE applications DROP COLUMN registration_status;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'applications' AND column_name = 'processing_status'
    ) THEN
        ALTER TABLE applications ADD COLUMN processing_status TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'applications' AND column_name = 'processed_at'
    ) THEN
        ALTER TABLE applications ADD COLUMN processed_at TIMESTAMPTZ;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'applications' AND column_name = 'start_term'
    ) THEN
        ALTER TABLE applications DROP COLUMN start_term;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'applications' AND column_name = 'end_term'
    ) THEN
        ALTER TABLE applications DROP COLUMN end_term;
    END IF;
END $$;

-- deposits: 신규 테이블
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
"""


async def main():
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("ERROR: DATABASE_URL 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    print(f"Connecting to: {dsn[:30]}...")
    conn = await asyncpg.connect(dsn)

    try:
        await conn.execute(_MIGRATION_SQL)
        print("✅ Migration complete.")

        # 결과 확인
        cols = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'applications'
            ORDER BY ordinal_position
            """
        )
        print(f"\napplications columns: {[c['column_name'] for c in cols]}")

        tables = await conn.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public' AND tablename = 'deposits'
            """
        )
        print(f"deposits table exists: {len(tables) > 0}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

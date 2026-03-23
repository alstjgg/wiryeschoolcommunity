"""비즈니스 테이블 스키마 생성 — 한 번만 실행하면 됨.

Usage:
    python scripts/init_db_schema.py

DATABASE_URL 환경변수 필요 (.env 또는 직접 설정).
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.services.db import get_pool, close_pool


async def main():
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("ERROR: DATABASE_URL 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    print(f"Connecting to: {dsn[:30]}...")
    pool = await get_pool()

    # 테이블 목록 확인
    async with pool.acquire() as conn:
        tables = await conn.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            AND tablename IN ('members', 'member_records', 'course_records',
                              'applications', 'attendance', 'feedbacks')
            ORDER BY tablename
            """
        )

    print(f"\n✅ {len(tables)}개 비즈니스 테이블 생성 완료:")
    for t in tables:
        print(f"   - {t['tablename']}")

    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())

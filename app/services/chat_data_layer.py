"""PostgreSQL 기반 Chainlit BaseDataLayer — 채팅 기록 영속성

DATABASE_URL 환경변수가 있을 때만 활성화된다.
Railway PostgreSQL 추가 시 자동으로 DATABASE_URL이 주입된다.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import asyncpg
import chainlit.data as cl_data
from chainlit.data.base import BaseDataLayer
from chainlit.element import ElementDict
from chainlit.step import StepDict
from chainlit.types import (
    Feedback,
    PageInfo,
    PaginatedResponse,
    Pagination,
    ThreadDict,
    ThreadFilter,
)
from chainlit.user import PersistedUser, User

_SCHEMA_SQL = """
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
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PostgresDataLayer(BaseDataLayer):
    """asyncpg 기반 PostgreSQL 데이터 레이어"""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.dsn)
            async with self._pool.acquire() as conn:
                await conn.execute(_SCHEMA_SQL)
        return self._pool

    # ------------------------------------------------------------------ User --

    async def get_user(self, identifier: str) -> Optional[PersistedUser]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, identifier, metadata, created_at FROM users WHERE identifier = $1",
                identifier,
            )
        if not row:
            return None
        return PersistedUser(
            id=row["id"],
            identifier=row["identifier"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            createdAt=row["created_at"].isoformat(),
        )

    async def create_user(self, user: User) -> Optional[PersistedUser]:
        import uuid

        pool = await self._get_pool()
        user_id = str(uuid.uuid4())
        metadata = json.dumps(user.metadata or {})
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (id, identifier, metadata)
                VALUES ($1, $2, $3)
                ON CONFLICT (identifier) DO UPDATE
                    SET metadata = EXCLUDED.metadata
                RETURNING id, identifier, metadata, created_at
                """,
                user_id,
                user.identifier,
                metadata,
            )
        return PersistedUser(
            id=row["id"],
            identifier=row["identifier"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            createdAt=row["created_at"].isoformat(),
        )

    async def delete_user_session(self, id: str) -> bool:
        return True

    # --------------------------------------------------------------- Thread --

    async def get_thread_author(self, thread_id: str) -> str:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT u.identifier FROM threads t
                JOIN users u ON u.id = t.user_id
                WHERE t.id = $1
                """,
                thread_id,
            )
        return row["identifier"] if row else ""

    async def get_thread(self, thread_id: str) -> Optional[ThreadDict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            t_row = await conn.fetchrow(
                "SELECT id, name, user_id, metadata, tags, created_at, updated_at FROM threads WHERE id = $1",
                thread_id,
            )
            if not t_row:
                return None
            step_rows = await conn.fetch(
                "SELECT id, parent_id, name, type, input, output, metadata, start_time, end_time FROM steps WHERE thread_id = $1 ORDER BY created_at",
                thread_id,
            )

        steps: List[StepDict] = [
            StepDict(
                id=r["id"],
                threadId=thread_id,
                parentId=r["parent_id"],
                name=r["name"] or "",
                type=r["type"] or "assistant_message",
                input=r["input"] or "",
                output=r["output"] or "",
                metadata=json.loads(r["metadata"]) if r["metadata"] else {},
                startTime=r["start_time"].isoformat() if r["start_time"] else None,
                endTime=r["end_time"].isoformat() if r["end_time"] else None,
                createdAt=None,
            )
            for r in step_rows
        ]

        return ThreadDict(
            id=t_row["id"],
            name=t_row["name"] or "",
            userId=t_row["user_id"],
            metadata=json.loads(t_row["metadata"]) if t_row["metadata"] else {},
            tags=list(t_row["tags"] or []),
            steps=steps,
            elements=[],
            createdAt=t_row["created_at"].isoformat(),
        )

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse[ThreadDict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # 사용자 필터
            user_id = None
            if filters.userId:
                row = await conn.fetchrow(
                    "SELECT id FROM users WHERE identifier = $1", filters.userId
                )
                user_id = row["id"] if row else None

            cursor_condition = ""
            params: list = []

            if user_id:
                params.append(user_id)
                cursor_condition = f"WHERE t.user_id = ${len(params)}"

            if pagination.cursor:
                params.append(pagination.cursor)
                join_kw = "AND" if params else "WHERE"
                cursor_condition += f" {join_kw} t.updated_at < (SELECT updated_at FROM threads WHERE id = ${len(params)})"

            params.append(pagination.first + 1)
            limit_idx = len(params)

            rows = await conn.fetch(
                f"""
                SELECT t.id, t.name, t.user_id, t.metadata, t.tags, t.created_at, t.updated_at
                FROM threads t
                {cursor_condition}
                ORDER BY t.updated_at DESC
                LIMIT ${limit_idx}
                """,
                *params,
            )

        has_next = len(rows) > pagination.first
        threads = rows[: pagination.first]

        thread_dicts = [
            ThreadDict(
                id=r["id"],
                name=r["name"] or "",
                userId=r["user_id"],
                metadata=json.loads(r["metadata"]) if r["metadata"] else {},
                tags=list(r["tags"] or []),
                steps=[],
                elements=[],
                createdAt=r["created_at"].isoformat(),
            )
            for r in threads
        ]

        return PaginatedResponse(
            data=thread_dicts,
            pageInfo=PageInfo(
                hasNextPage=has_next,
                startCursor=threads[0]["id"] if threads else None,
                endCursor=threads[-1]["id"] if threads else None,
            ),
        )

    async def update_thread(
        self,
        thread_id: str,
        name: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
    ):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # 없으면 생성
            await conn.execute(
                """
                INSERT INTO threads (id, name, user_id, metadata, tags)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (id) DO UPDATE
                    SET name       = COALESCE(EXCLUDED.name, threads.name),
                        user_id    = COALESCE(EXCLUDED.user_id, threads.user_id),
                        metadata   = COALESCE(EXCLUDED.metadata, threads.metadata),
                        tags       = COALESCE(EXCLUDED.tags, threads.tags),
                        updated_at = NOW()
                """,
                thread_id,
                name,
                user_id,
                json.dumps(metadata or {}),
                tags or [],
            )

    async def delete_thread(self, thread_id: str) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM threads WHERE id = $1", thread_id)
        return True

    # ----------------------------------------------------------------- Step --

    async def create_step(self, step_dict: StepDict):
        pool = await self._get_pool()
        # thread가 먼저 존재해야 FK 제약 통과 — 없으면 최소 행 삽입
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO threads (id) VALUES ($1)
                ON CONFLICT (id) DO NOTHING
                """,
                step_dict.get("threadId"),
            )
            await conn.execute(
                """
                INSERT INTO steps (id, thread_id, parent_id, name, type, input, output, metadata, start_time, end_time)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (id) DO NOTHING
                """,
                step_dict.get("id"),
                step_dict.get("threadId"),
                step_dict.get("parentId"),
                step_dict.get("name"),
                step_dict.get("type"),
                step_dict.get("input"),
                step_dict.get("output"),
                json.dumps(step_dict.get("metadata") or {}),
                _parse_dt(step_dict.get("startTime")),
                _parse_dt(step_dict.get("endTime")),
            )

    async def update_step(self, step_dict: StepDict):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE steps
                SET output   = COALESCE($2, output),
                    end_time = COALESCE($3, end_time)
                WHERE id = $1
                """,
                step_dict.get("id"),
                step_dict.get("output"),
                _parse_dt(step_dict.get("endTime")),
            )

    async def delete_step(self, step_id: str) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM steps WHERE id = $1", step_id)
        return True

    # ------------------------------------------- Feedback / Element (빈 구현) --

    async def upsert_feedback(self, feedback: Feedback) -> str:
        return ""

    async def delete_feedback(self, feedback_id: str) -> bool:
        return True

    async def create_element(self, element: ElementDict):
        pass

    async def get_element(
        self, thread_id: str, element_id: str
    ) -> Optional[ElementDict]:
        return None

    async def delete_element(self, element_id: str, thread_id: Optional[str] = None) -> bool:
        return True

    async def build_debug_url(self) -> str:
        return ""

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def get_favorite_steps(self, user_id: str) -> List[StepDict]:
        return []


# ------------------------------------------------------------------ helpers --

def _parse_dt(value) -> Optional[datetime]:
    """ISO 문자열 또는 None → datetime"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


# ---------------------------------------------------------------- 등록 시점 --

_database_url = os.environ.get("DATABASE_URL")
if _database_url:
    cl_data._data_layer = PostgresDataLayer(_database_url)

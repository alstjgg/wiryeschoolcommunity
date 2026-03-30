"""데이터 조회 tool — 자연어 → 구조화된 쿼리 → DB 조회

LLM이 자연어를 QueryIntent(Pydantic)로 파싱하고,
미리 정의된 조회 함수가 안전하게 SQL을 실행한다.
"""

import logging
from typing import Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config import ANTHROPIC_API_KEY, LLM_MODEL
from app.services.db import get_pool

logger = logging.getLogger(__name__)


# ---- 1. 쿼리 의도 파싱용 Pydantic 스키마 ----


class QueryIntent(BaseModel):
    """사용자 자연어 조회 요청의 구조화된 의도."""

    query_type: str = Field(
        description=(
            "조회 유형: members, applications, course_records, "
            "member_info, deposits, course_summary, payment_summary, "
            "grade_distribution"
        ),
    )
    term_id: Optional[str] = Field(
        default=None,
        description="회차 ID (예: 2026-1)",
    )
    name: Optional[str] = Field(
        default=None,
        description="검색할 사람 이름",
    )
    course_name: Optional[str] = Field(
        default=None,
        description="강좌명",
    )
    grade: Optional[str] = Field(
        default=None,
        description="회원 등급 필터 (회원, 준회원, 정회원)",
    )


PARSE_SYSTEM_PROMPT = """사용자의 데이터 조회 요청을 분석하여 구조화된 쿼리 의도로 변환하세요.

조회 유형:
- members: 회원 목록 조회 (등급별 필터 가능)
- applications: 특정 회차의 수강 신청/등록 목록
- course_records: 수강 기록 (출석률 포함)
- member_info: 특정 회원의 상세 정보
- deposits: 입금 내역 조회 (미확인 건 포함)
- course_summary: 강좌별 수강생 수 집계 ("강좌별 인원", "과목별 수강생 수")
- payment_summary: 입금 현황 요약 ("입금률", "입금 현황", "미입금 현황")
- grade_distribution: 회원 등급별 분포 ("등급별 인원", "회원 분포")

회차 ID 형식: "연도-번호" (예: 2026-1 = 2026년 겨울학기)
계절 매핑: 1=겨울(1~3월), 2=봄(4~6월), 3=여름(7~9월), 4=가을(10~12월)

"이번학기" = 현재 회차, "지난학기" = 직전 회차로 매핑하세요.
"""


# ---- 2. 미리 정의된 조회 함수들 ----


async def _query_members(grade: str | None = None) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if grade:
            rows = await conn.fetch(
                "SELECT name_id, name, grade, phone FROM members "
                "WHERE grade = $1 ORDER BY name",
                grade,
            )
        else:
            rows = await conn.fetch(
                "SELECT name_id, name, grade, phone FROM members ORDER BY name"
            )
    if not rows:
        return "조건에 맞는 회원이 없습니다."
    lines = [f"총 **{len(rows)}명**:"]
    for r in rows[:50]:
        phone = r["phone"] or ""
        lines.append(f"- {r['name']} ({r['grade']}) {phone}")
    if len(rows) > 50:
        lines.append(f"... 외 {len(rows) - 50}명")
    return "\n".join(lines)


async def _query_applications(
    term_id: str, course_name: str | None = None,
) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if course_name:
            rows = await conn.fetch(
                "SELECT name_id, name, type, course_name, "
                "payment_status, processing_status "
                "FROM applications "
                "WHERE term_id = $1 AND course_name ILIKE $2 "
                "ORDER BY name",
                term_id,
                f"%{course_name}%",
            )
        else:
            rows = await conn.fetch(
                "SELECT name_id, name, type, course_name, "
                "payment_status, processing_status "
                "FROM applications WHERE term_id = $1 "
                "ORDER BY type, name",
                term_id,
            )
    if not rows:
        return f"{term_id} 회차의 신청 기록이 없습니다."
    lines = [f"**{term_id}** 신청 목록 ({len(rows)}건):"]
    for r in rows[:50]:
        course = r["course_name"] or ""
        ps = r["processing_status"] or "미처리"
        lines.append(f"- {r['name']} | {r['type']} | {course} | {ps}")
    if len(rows) > 50:
        lines.append(f"... 외 {len(rows) - 50}건")
    return "\n".join(lines)


async def _query_course_records(
    name: str | None = None, term_id: str | None = None,
) -> str:
    pool = await get_pool()
    conditions: list[str] = []
    params: list = []
    if name:
        params.append(f"%{name}%")
        conditions.append(f"name_id ILIKE ${len(params)}")
    if term_id:
        params.append(term_id)
        conditions.append(f"term_id = ${len(params)}")
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT name_id, term_id, course_name, attendance "
            f"FROM course_records {where} ORDER BY term_id DESC, name_id",
            *params,
        )
    if not rows:
        return "조건에 맞는 수강 기록이 없습니다."
    lines = [f"수강 기록 ({len(rows)}건):"]
    for r in rows[:50]:
        att = f"{r['attendance']:.1f}%" if r["attendance"] else "-"
        lines.append(
            f"- {r['name_id']} | {r['term_id']} | {r['course_name']} | 출석률 {att}"
        )
    if len(rows) > 50:
        lines.append(f"... 외 {len(rows) - 50}건")
    return "\n".join(lines)


async def _query_member_info(name: str) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM members WHERE name ILIKE $1 OR name_id ILIKE $1",
            f"%{name}%",
        )
    if not rows:
        return f"'{name}'에 해당하는 회원을 찾을 수 없습니다."
    parts = []
    for row in rows[:5]:
        avg_att = f"{row['avg_attendance']:.1f}%" if row["avg_attendance"] else "-"
        parts.append(
            f"**{row['name']}** ({row['name_id']})\n"
            f"- 등급: {row['grade']}\n"
            f"- 연락처: {row['phone'] or '미등록'}\n"
            f"- 수강횟수: {row['course_count']}회\n"
            f"- 누적출석률: {avg_att}\n"
            f"- 마지막수강: {row['last_term'] or '-'}"
        )
    if len(rows) > 5:
        parts.append(f"... 외 {len(rows) - 5}명")
    return "\n\n".join(parts)


async def _query_deposits(term_id: str) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT transaction_time, amount, payer_name, memo, "
            "match_status, processing_status "
            "FROM deposits WHERE term_id = $1 "
            "ORDER BY transaction_time DESC",
            term_id,
        )
    if not rows:
        return f"{term_id} 회차의 입금 내역이 없습니다."
    unmatched = sum(1 for r in rows if r["match_status"] == "unmatched")
    lines = [f"**{term_id}** 입금 내역 ({len(rows)}건, 미확인 {unmatched}건):"]
    for r in rows[:30]:
        status = "미확인" if r["match_status"] == "unmatched" else "매칭"
        lines.append(
            f"- {r['transaction_time'] or '-'} | "
            f"{r['amount']:,}원 | {r['payer_name'] or '-'} | {status}"
        )
    if len(rows) > 30:
        lines.append(f"... 외 {len(rows) - 30}건")
    return "\n".join(lines)


async def _query_course_summary(term_id: str) -> str:
    """강좌별 수강생 수 집계."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT course_name, COUNT(*) as cnt "
            "FROM applications "
            "WHERE term_id = $1 AND type = '수강' "
            "GROUP BY course_name ORDER BY cnt DESC",
            term_id,
        )
    if not rows:
        return f"{term_id} 회차의 강좌 데이터가 없습니다."
    lines = [f"**{term_id}** 강좌별 수강생 수:"]
    total = 0
    for r in rows:
        lines.append(f"- {r['course_name']}: **{r['cnt']}명**")
        total += r["cnt"]
    lines.append(f"\n총 **{total}명**")
    return "\n".join(lines)


async def _query_payment_summary(term_id: str) -> str:
    """입금 현황 요약 집계."""
    from app.services.db import STATUS_TO_EMOJI
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT payment_status, COUNT(*) as cnt "
            "FROM applications "
            "WHERE term_id = $1 "
            "GROUP BY payment_status ORDER BY cnt DESC",
            term_id,
        )
    if not rows:
        return f"{term_id} 회차의 신청 데이터가 없습니다."
    lines = [f"**{term_id}** 입금 현황:"]
    total = 0
    for r in rows:
        status = STATUS_TO_EMOJI.get(r["payment_status"], r["payment_status"] or "미설정")
        lines.append(f"- {status}: **{r['cnt']}건**")
        total += r["cnt"]
    lines.append(f"\n총 **{total}건**")
    return "\n".join(lines)


async def _query_grade_distribution() -> str:
    """회원 등급별 분포."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT grade, COUNT(*) as cnt "
            "FROM members "
            "GROUP BY grade ORDER BY cnt DESC"
        )
    if not rows:
        return "회원 데이터가 없습니다."
    lines = ["**회원 등급별 분포:**"]
    total = 0
    for r in rows:
        lines.append(f"- {r['grade']}: **{r['cnt']}명**")
        total += r["cnt"]
    lines.append(f"\n총 **{total}명**")
    return "\n".join(lines)


# ---- 3. tool 함수 ----

_QUERY_DISPATCH = {
    "members": lambda intent: _query_members(grade=intent.grade),
    "applications": lambda intent: _query_applications(
        term_id=intent.term_id or "", course_name=intent.course_name,
    ),
    "course_records": lambda intent: _query_course_records(
        name=intent.name, term_id=intent.term_id,
    ),
    "member_info": lambda intent: _query_member_info(name=intent.name or ""),
    "deposits": lambda intent: _query_deposits(term_id=intent.term_id or ""),
    "course_summary": lambda intent: _query_course_summary(
        term_id=intent.term_id or "",
    ),
    "payment_summary": lambda intent: _query_payment_summary(
        term_id=intent.term_id or "",
    ),
    "grade_distribution": lambda intent: _query_grade_distribution(),
}


@tool
async def query_data(query_description: str) -> str:
    """위례인생학교 데이터를 조회합니다.

    수강생 목록, 회원 정보, 강좌 목록, 입금 내역 등을 검색합니다.

    조회 가능한 데이터:
    - 회원 목록 (등급별 필터: 정회원, 준회원, 회원)
    - 특정 회차의 수강생/신청자 목록
    - 특정 강좌의 수강생 목록
    - 특정 회원의 상세 정보 (수강 이력, 출석률)
    - 입금 내역 (미확인 건 포함)
    - 강좌별 수강생 수 집계
    - 입금 현황 요약 (상태별 건수)
    - 회원 등급별 분포

    Args:
        query_description: 조회 내용을 자연어로 설명.
            예: "2026-1 겨울학기 수강생 목록", "정회원 목록", "박민서의 수강 기록"
    """
    try:
        llm = ChatAnthropic(
            model=LLM_MODEL, api_key=ANTHROPIC_API_KEY, max_tokens=256,
        )
        structured_llm = llm.with_structured_output(QueryIntent)
        intent: QueryIntent = await structured_llm.ainvoke(
            f"{PARSE_SYSTEM_PROMPT}\n\n사용자 요청: {query_description}"
        )

        handler = _QUERY_DISPATCH.get(intent.query_type)
        if not handler:
            return (
                f"'{query_description}'에 해당하는 조회 방법을 찾지 못했습니다.\n"
                "회원 목록, 수강생 목록, 회원 정보, 수강 기록, 입금 내역을 조회할 수 있습니다."
            )

        # 필수 파라미터 검증
        if intent.query_type == "applications" and not intent.term_id:
            return "어떤 회차의 목록을 조회할지 알려주세요. (예: 2026-1 겨울학기)"
        if intent.query_type == "member_info" and not intent.name:
            return "어떤 회원의 정보를 조회할지 이름을 알려주세요."
        if intent.query_type == "deposits" and not intent.term_id:
            return "어떤 회차의 입금 내역을 조회할지 알려주세요. (예: 2026-1)"
        if intent.query_type == "course_summary" and not intent.term_id:
            return "어떤 회차의 강좌별 현황을 조회할지 알려주세요. (예: 2026-1 겨울학기)"
        if intent.query_type == "payment_summary" and not intent.term_id:
            return "어떤 회차의 입금 현황을 조회할지 알려주세요. (예: 2026-1)"

        return await handler(intent)

    except Exception as e:
        logger.error("query_data failed: %s", e, exc_info=True)
        return f"데이터 조회 중 오류가 발생했습니다: {e}"

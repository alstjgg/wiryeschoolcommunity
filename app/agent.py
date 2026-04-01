"""LangChain Agent 생성 — 위례인생학교 업무 도우미

create_agent()로 ReAct 에이전트를 생성하고,
7개 tool 중 적합한 것을 선택하여 관리자 요청을 처리한다.

Checkpointer:
  - DATABASE_URL 있으면 AsyncPostgresSaver (세션 영속성)
  - 없으면 MemorySaver (로컬 개발용)
"""

import logging
import os

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import MemorySaver

from app.config import ANTHROPIC_API_KEY, LLM_MODEL
from app.context.business import get_system_prompt
from app.tools import ALL_TOOLS

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPT = """당신은 위례인생학교의 업무 도우미 AI입니다.
관리자의 요청을 이해하고 적합한 도구를 선택하여 처리합니다.

## 행동 지침
- 관리자가 요청한 작업에 적합한 도구를 선택하여 처리하세요.
- 파일이 필요한 작업에서 파일이 없으면 도구를 호출하여 파일을 요청하세요.
- [FILE:경로] 형태가 메시지에 있으면 업로드된 파일 경로입니다.
- 작업 완료 후 결과를 간결하게 전달하세요. 불필요한 부연 설명은 생략합니다.
- 한국어로 답변하세요.
- 모르는 질문은 솔직하게 모른다고 답하세요.
- 도구가 "__SILENT__"을 반환하면, 이미 사용자에게 직접 메시지를 보낸 것이므로 추가 응답을 하지 마세요.

## 도구 선택 가이드
- "입금 대조", "입금 확인", "입금 처리", "입금 매칭" → process_payment
- "출석부 생성", "출석부 만들기" → create_attendance
- "출석 체크", "출석 확인", "OCR", "사진으로 출석" → check_attendance_ocr
- "종강 처리", "종강", "학기 마무리", "출석률 계산", "등급 강등" → process_graduation
- "계획서 검토", "강의 계획서" → review_plan
- "보고서 생성", "보고서" → generate_report
- 수강생/회원/강좌 데이터 조회 → query_data
- 업무 관련 질문, 정보 요청, "~이 뭐야?", "~가 뭔가요?" → answer_question

## 회차 확인 규칙
- 입금 대조, 출석부 생성, 출석 체크, 종강 처리를 시작할 때, 관리자가 회차를 명시하지 않으면 process_payment/create_attendance 등을 term_id 없이 호출하세요. 도구가 회차 확인 메시지를 반환합니다.
- 관리자가 "맞아", "네", "진행해" 등 확인하면 현재 회차의 term_id(예: "{current_term_id}")를 전달하여 다시 호출하세요.
- 관리자가 다른 회차를 지정하면 해당 term_id를 전달하세요.

## 현재 회차 정보
{term_context}

## 비즈니스 컨텍스트
{business_context}
"""

# 싱글턴 checkpointer (서버 수명 동안 재사용)
_checkpointer = None


async def _get_checkpointer():
    """DATABASE_URL이 있으면 AsyncPostgresSaver, 없으면 MemorySaver."""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            checkpointer = AsyncPostgresSaver.from_conn_string(dsn)
            await checkpointer.setup()
            _checkpointer = checkpointer
            logger.info("PostgresSaver checkpointer initialized")
            return _checkpointer
        except Exception as e:
            logger.warning("PostgresSaver init failed, falling back to MemorySaver: %s", e)

    _checkpointer = MemorySaver()
    logger.info("MemorySaver checkpointer initialized (no DATABASE_URL)")
    return _checkpointer


async def create_wirye_agent():
    """세션별 Agent 인스턴스 생성 (async — checkpointer 초기화 포함)."""
    from app.config import TERM_SEASONS
    from app.context.term import get_current_term

    llm = ChatAnthropic(
        model=LLM_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=4096,
    )

    # 현재/직전 회차 컨텍스트
    current_term = get_current_term()
    curr_abs = (current_term["year"] - 2020) * 4 + current_term["term"]
    prev_abs = curr_abs - 1
    prev_year = 2020 + (prev_abs - 1) // 4
    prev_num = ((prev_abs - 1) % 4) + 1
    prev_season = TERM_SEASONS.get(prev_num, "")

    term_context = (
        f"현재 회차: {current_term['term_id']} {current_term['season']}학기\n"
        f"직전 회차: {prev_year}-{prev_num} {prev_season}학기\n"
        f"'지난 학기' = {prev_year}-{prev_num}, '이번 학기' = {current_term['term_id']}"
    )

    system_prompt = AGENT_SYSTEM_PROMPT.format(
        business_context=get_system_prompt(),
        term_context=term_context,
        current_term_id=current_term["term_id"],
    )

    checkpointer = await _get_checkpointer()

    return create_agent(
        model=llm,
        tools=ALL_TOOLS,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )

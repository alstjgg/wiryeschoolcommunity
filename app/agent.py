"""LangChain Agent 생성 — 위례인생학교 업무 도우미

create_agent()로 ReAct 에이전트를 생성하고,
7개 tool 중 적합한 것을 선택하여 관리자 요청을 처리한다.
"""

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import MemorySaver

from app.config import ANTHROPIC_API_KEY, LLM_MODEL
from app.context.business import get_system_prompt
from app.tools import ALL_TOOLS

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
- 업무 관련 질문, 정보 요청, "~이 뭐야?", "~가 뭔가요?" → answer_question

## 비즈니스 컨텍스트
{business_context}
"""


def create_wirye_agent():
    """세션별 Agent 인스턴스 생성."""
    llm = ChatAnthropic(
        model=LLM_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=4096,
    )

    system_prompt = AGENT_SYSTEM_PROMPT.format(
        business_context=get_system_prompt(),
    )

    return create_agent(
        model=llm,
        tools=ALL_TOOLS,
        system_prompt=system_prompt,
        checkpointer=MemorySaver(),
    )

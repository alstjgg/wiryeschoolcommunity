"""질의 응답 체인 — 비즈니스 컨텍스트 기반 Q&A (prompt caching 적용)"""

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import ANTHROPIC_API_KEY, LLM_MODEL
from app.context.business import get_system_prompt

ANSWER_STYLE_INSTRUCTION = """
## 답변 스타일
- 핵심만 간결하게 답변하세요. 2~3문장 이내가 이상적입니다.
- 부가 설명은 사용자가 추가 질문을 했을 때만 제공하세요.
- 불필요한 서론("안녕하세요", "네, 답변드리겠습니다" 등)은 생략하세요.
- 관련 시트 링크가 있으면 함께 제공하세요.
"""


def get_llm():
    return ChatAnthropic(
        model=LLM_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=1024,
    )


async def answer_question(question: str) -> str:
    """비즈니스 컨텍스트 기반으로 질문에 답변 (prompt caching 적용)"""
    llm = get_llm()
    messages = [
        SystemMessage(content=[
            {
                "type": "text",
                "text": get_system_prompt() + ANSWER_STYLE_INSTRUCTION,
                "cache_control": {"type": "ephemeral"},
            }
        ]),
        HumanMessage(content=question),
    ]
    response = await llm.ainvoke(messages)
    return response.content

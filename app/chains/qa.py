"""질의 응답 체인 — 비즈니스 컨텍스트 기반 Q&A"""

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import ANTHROPIC_API_KEY, LLM_MODEL
from app.context.business import get_system_prompt


def get_llm():
    return ChatAnthropic(
        model=LLM_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=2048,
    )


async def answer_question(question: str) -> str:
    """비즈니스 컨텍스트 기반으로 질문에 답변"""
    llm = get_llm()
    messages = [
        SystemMessage(content=get_system_prompt()),
        HumanMessage(content=question),
    ]
    response = await llm.ainvoke(messages)
    return response.content

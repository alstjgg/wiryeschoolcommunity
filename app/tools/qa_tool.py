"""Q&A tool — 업무 관련 질문 답변"""

from langchain_core.tools import tool

from app.chains.qa import answer_question as _answer_question


@tool
async def answer_question(question: str) -> str:
    """위례인생학교 업무 관련 질문에 답변합니다.

    회원제도, 수강료, 입금 절차, 환불 규정, 업무 일정, 출석 관리,
    종강 처리, 강사/사무처 면제, 시트 구조 등의 질문에 답합니다.
    데이터 조회가 필요 없는 일반적인 업무 질문에 사용합니다.

    Args:
        question: 관리자의 질문 내용
    """
    try:
        return await _answer_question(question)
    except Exception as e:
        return f"답변 생성 중 오류가 발생했습니다: {e}"

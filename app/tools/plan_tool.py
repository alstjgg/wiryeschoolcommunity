"""계획서 검토 tool — placeholder"""

from langchain_core.tools import tool


@tool
async def review_plan() -> str:
    """강의 계획서를 검토합니다.

    강사가 제출한 강의 계획서의 오탈자, 말투를 교정하고
    배움숲 포탈 업로드용 멘트를 생성합니다.
    현재 준비 중인 기능입니다.
    """
    return "계획서 검토 기능은 준비 중입니다."

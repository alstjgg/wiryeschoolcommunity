"""보고서 생성 tool — placeholder"""

from langchain_core.tools import tool


@tool
async def generate_report() -> str:
    """보고서를 생성합니다.

    DB 데이터를 집계하여 회차별 활동 보고서(PDF)를 생성합니다.
    현재 준비 중인 기능입니다.
    """
    return "보고서 기능은 아직 준비 중입니다."

"""동적 학기 데이터 로더 — Google Sheets에서 런타임 로드"""

from datetime import date
from app.config import SEMESTERS


def get_current_semester() -> dict:
    """현재 날짜 기준으로 연도와 학기를 반환"""
    today = date.today()
    year = today.year
    month = today.month

    if month <= 3:
        semester = 1
    elif month <= 6:
        semester = 2
    elif month <= 9:
        semester = 3
    else:
        semester = 4

    return {
        "year": year,
        "semester": semester,
        "label": f"{year}_{semester}학기",
        "period": SEMESTERS[semester],
    }

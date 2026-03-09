"""현재 회차 자동 판별"""

from datetime import date
from app.config import TERMS, TERM_SEASONS


def get_current_term() -> dict:
    """현재 날짜 기준으로 연도와 회차를 반환"""
    today = date.today()
    year = today.year
    month = today.month

    if month <= 3:
        term = 1
    elif month <= 6:
        term = 2
    elif month <= 9:
        term = 3
    else:
        term = 4

    season = TERM_SEASONS[term]

    return {
        "year": year,
        "term": term,
        "term_id": f"{year}-{term}",
        "term_name": f"{year}-{term} {season}학기",
        "season": season,
    }

"""현재 회차 자동 판별 + 자유 텍스트 회차 파싱"""

import re
from datetime import date
from app.config import TERMS, TERM_SEASONS

# 계절 → 회차 번호 매핑
_SEASON_MAP = {
    "겨울": 1, "봄": 2, "여름": 3, "가을": 4,
}


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


def _build_term_dict(year: int, term_num: int) -> dict | None:
    """연도와 회차 번호로 term dict 생성. 유효하지 않으면 None."""
    if term_num not in TERM_SEASONS:
        return None
    if year < 2020 or year > 2099:
        return None
    season = TERM_SEASONS[term_num]
    return {
        "year": year,
        "term": term_num,
        "term_id": f"{year}-{term_num}",
        "term_name": f"{year}-{term_num} {season}학기",
        "season": season,
    }


def parse_term_input(text: str) -> dict | None:
    """관리자 자유 텍스트에서 회차 정보를 파싱.

    지원 패턴:
      2026-2, 2026-2 봄, 2026년 2학기, 2026년 봄, 봄, 봄학기,
      26-2, 26봄, 2학기, 2회차,
      지난학기, 이번학기, 작년, 저번학기

    Returns: get_current_term()과 동일한 구조의 dict, 실패 시 None.
    """
    text = text.strip()
    current_year = date.today().year

    # ── 패턴 0: 상대적 시간 표현 ──
    current = get_current_term()
    curr_abs = (current["year"] - 2020) * 4 + current["term"]

    # "작년" 특수 처리
    if "작년" in text:
        # 작년 + 계절 지정이 있으면 해당 계절로
        for season, num in _SEASON_MAP.items():
            if season in text:
                return _build_term_dict(current_year - 1, num)
        # 계절 미지정: 작년의 같은 학기
        return _build_term_dict(current_year - 1, current["term"])

    relative_keywords = {
        -1: ["지난", "저번", "이전", "전학기", "지난학기", "저번학기", "직전"],
        0: ["이번", "현재", "이번학기", "현재학기", "금학기"],
    }
    for offset, keywords in relative_keywords.items():
        if any(kw in text for kw in keywords):
            target = curr_abs + offset
            if target < 1:
                return None
            t_year = 2020 + (target - 1) // 4
            t_num = ((target - 1) % 4) + 1
            return _build_term_dict(t_year, t_num)

    # ── 패턴 1: YYYY-N (e.g. 2026-2, 2026-1)
    m = re.search(r"(20\d{2})-([1-4])", text)
    if m:
        return _build_term_dict(int(m.group(1)), int(m.group(2)))

    # 패턴 2: YY-N (e.g. 26-2)
    m = re.search(r"(\d{2})-([1-4])", text)
    if m:
        return _build_term_dict(2000 + int(m.group(1)), int(m.group(2)))

    # 패턴 3: YYYY년 N학기/회차 (e.g. 2026년 2학기)
    m = re.search(r"(20\d{2})\s*년?\s*([1-4])\s*(?:학기|회차)", text)
    if m:
        return _build_term_dict(int(m.group(1)), int(m.group(2)))

    # 패턴 4: YYYY년 계절 (e.g. 2026년 봄)
    for season, num in _SEASON_MAP.items():
        m = re.search(rf"(20\d{{2}})\s*년?\s*{season}", text)
        if m:
            return _build_term_dict(int(m.group(1)), num)

    # 패턴 5: YY+계절 (e.g. 26봄)
    for season, num in _SEASON_MAP.items():
        m = re.search(rf"(\d{{2}})\s*{season}", text)
        if m:
            return _build_term_dict(2000 + int(m.group(1)), num)

    # 패턴 6: 계절 only (e.g. 봄, 봄학기) — 현재 연도
    for season, num in _SEASON_MAP.items():
        if season in text:
            return _build_term_dict(current_year, num)

    # 패턴 7: N학기/회차 only (e.g. 2학기, 2회차)
    m = re.search(r"([1-4])\s*(?:학기|회차)", text)
    if m:
        return _build_term_dict(current_year, int(m.group(1)))

    return None

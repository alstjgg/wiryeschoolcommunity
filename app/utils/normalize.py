"""이름·전화번호 정규화 유틸리티.

모든 데이터 소스(배움숲 엑셀, Drive 가입 신청서, DB 쓰기)에서
일관된 형태로 이름/전화번호/name_id를 생성하기 위한 공통 함수.
"""

import re


def normalize_name(name: str) -> str:
    """이름 정규화 — 모든 종류의 공백 제거.

    Examples:
        "박 현주" → "박현주"
        " 김철수 " → "김철수"
        "노　민의" → "노민의"  (전각 공백 포함)
    """
    return re.sub(r'\s+', '', name.strip())


def normalize_phone(phone: str) -> str:
    """전화번호 정규화 — 숫자만 추출, 01012345678 형태.

    다양한 사용자 입력 포맷을 처리:
        "010-1234-5678" → "01012345678"
        "010.5333.8691" → "01053338691"
        "010ㅡ8258ㅡ4056" → "01082584056"
        "010,9339-8420" → "01093398420"
        "010 2323. 0167" → "01023230167"
    """
    return re.sub(r'[^0-9]', '', phone.strip())


def make_name_id(name: str, phone: str) -> str:
    """정규화된 이름 + 전화번호 뒤 4자리로 name_id 생성.

    Examples:
        ("박현주", "01082608408") → "박현주8408"
        ("노 민의", "010-8850-2708") → "노민의2708"
    """
    n = normalize_name(name)
    p = normalize_phone(phone)
    suffix = p[-4:] if len(p) >= 4 else ""
    return f"{n}{suffix}"

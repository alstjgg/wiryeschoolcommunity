"""
Unit tests for app/utils/matching.py

Google API 없음. 인라인 픽스처 사용.
실행: pytest tests/test_matching.py -v
"""

import pytest
from app.utils.matching import (
    extract_name_from_sender,
    is_third_party,
    extract_name_from_memo,
    extract_course_hint,
    detect_special_type,
    classify_amount,
    match_transaction,
)

# ── 공통 픽스처 ────────────────────────────────────────────────────────────────

STUDENTS = [
    {"이름ID": "김기춘1234", "이름": "김기춘", "강좌명": "경제뉴스로 배우는 경제해설(기초)"},
    {"이름ID": "박민서6804", "이름": "박민서", "강좌명": "나, 마음챙김 명상"},
    {"이름ID": "이현수9900", "이름": "이현수", "강좌명": "우쿨렐레 중급"},
    {"이름ID": "김기춘5678", "이름": "김기춘", "강좌명": "생활교양법률"},  # 동명이인
]

COURSE_NAMES = list({s["강좌명"] for s in STUDENTS})


# ── extract_name_from_sender ──────────────────────────────────────────────────

def test_extract_name_strips_bank_suffix():
    assert extract_name_from_sender("홍길동(국민)") == "홍길동"

def test_extract_name_no_suffix():
    assert extract_name_from_sender("박민서") == "박민서"

def test_extract_name_strips_whitespace():
    assert extract_name_from_sender("  김기춘  ") == "김기춘"


# ── is_third_party ────────────────────────────────────────────────────────────

def test_kakao_pay_detected():
    assert is_third_party("(주)카카오페이") is True

def test_toss_detected():
    assert is_third_party("(주)비바리퍼블리카") is True

def test_normal_sender_not_third_party():
    assert is_third_party("김기춘") is False


# ── extract_name_from_memo ────────────────────────────────────────────────────

def test_name_found_in_memo():
    result = extract_name_from_memo("김기춘경제기초", ["김기춘", "박민서"])
    assert result == "김기춘"

def test_longer_name_wins_over_shorter_substring():
    # "김기"와 "김기춘" 모두 후보일 때 긴 이름이 우선
    result = extract_name_from_memo("김기춘수강", ["김기", "김기춘"])
    assert result == "김기춘"

def test_name_not_in_memo_returns_none():
    result = extract_name_from_memo("입금합니다", ["김기춘"])
    assert result is None

def test_empty_memo_returns_none():
    result = extract_name_from_memo("", ["김기춘"])
    assert result is None


# ── extract_course_hint ───────────────────────────────────────────────────────

def test_keyword_maps_to_course():
    result = extract_course_hint("경제기초수강", COURSE_NAMES)
    assert result == "경제뉴스로 배우는 경제해설(기초)"

def test_exact_course_name_in_memo():
    result = extract_course_hint("우쿨렐레 중급", COURSE_NAMES)
    assert result == "우쿨렐레 중급"

def test_unrecognized_keyword_returns_none():
    result = extract_course_hint("xyz알수없는강좌", COURSE_NAMES)
    assert result is None

def test_empty_memo_returns_none_for_course():
    result = extract_course_hint("", COURSE_NAMES)
    assert result is None


# ── detect_special_type ───────────────────────────────────────────────────────

def test_small_amount_detected():
    result = detect_special_type("입금", 500)
    assert result == "소액"

def test_cancellation_keyword_detected():
    result = detect_special_type("취소됨", 20000)
    assert result == "취소"

def test_waiting_keyword_detected():
    result = detect_special_type("결제대기", 20000)
    assert result == "취소"

def test_full_membership_fee_detected():
    # FULL_MEMBERSHIP_FEE = 120000
    result = detect_special_type("정회원비납부", 120000)
    assert result == "정회원"

def test_normal_transaction_returns_none():
    result = detect_special_type("경제기초김기춘", 20000)
    assert result is None


# ── classify_amount ───────────────────────────────────────────────────────────

def test_classify_membership_fee():
    assert classify_amount(10000) == "가입비(1만)"

def test_classify_tuition():
    assert classify_amount(20000) == "수강료(2만)"

def test_classify_combined():
    assert classify_amount(30000) == "수강료+가입비(3만)"

def test_classify_two_courses():
    assert classify_amount(40000) == "2과목(4만)"

def test_classify_multi_course():
    result = classify_amount(60000)
    assert "6만" in result


# ── match_transaction — 정상 매칭 ─────────────────────────────────────────────

def test_match_exact_sender_name():
    tx = {"거래일시": "2026-01-10 10:00", "적요": "", "의뢰인": "박민서", "입금": 20000}
    result = match_transaction(tx, STUDENTS, COURSE_NAMES)
    assert result["상태"] == "✅정상"
    assert result["매칭이름"] == "박민서"
    assert result["매칭ID"] == "박민서6804"

def test_match_sender_with_bank_suffix():
    tx = {"거래일시": "2026-01-10", "적요": "", "의뢰인": "이현수(신한)", "입금": 20000}
    result = match_transaction(tx, STUDENTS, COURSE_NAMES)
    assert result["상태"] == "✅정상"
    assert result["매칭이름"] == "이현수"

def test_match_kakao_pay_via_memo():
    tx = {
        "거래일시": "2026-01-10",
        "적요": "박민서 명상 수강료",
        "의뢰인": "(주)카카오페이",
        "입금": 20000,
    }
    result = match_transaction(tx, STUDENTS, COURSE_NAMES)
    assert result["상태"] == "✅정상"
    assert result["매칭이름"] == "박민서"


# ── match_transaction — 스킵 ──────────────────────────────────────────────────

def test_skip_small_amount():
    tx = {"거래일시": "2026-01-10", "적요": "예금이자", "의뢰인": "", "입금": 100}
    result = match_transaction(tx, STUDENTS, COURSE_NAMES)
    assert result["상태"] == "⏭️스킵"

def test_skip_cancellation():
    tx = {"거래일시": "2026-01-10", "적요": "취소됨", "의뢰인": "박민서", "입금": 20000}
    result = match_transaction(tx, STUDENTS, COURSE_NAMES)
    assert result["상태"] == "⏭️스킵"


# ── match_transaction — 동명이인 ──────────────────────────────────────────────

def test_homonym_resolved_by_course():
    # 김기춘1234(경제기초), 김기춘5678(법률) 두 명 존재
    tx = {
        "거래일시": "2026-01-10",
        "적요": "김기춘경제기초",
        "의뢰인": "김기춘",
        "입금": 20000,
    }
    result = match_transaction(tx, STUDENTS, COURSE_NAMES)
    assert result["상태"] == "✅정상"
    assert result["매칭ID"] == "김기춘1234"

def test_homonym_unresolved_flagged():
    # 강좌 힌트 없음 → 확인필요
    tx = {"거래일시": "2026-01-10", "적요": "", "의뢰인": "김기춘", "입금": 20000}
    result = match_transaction(tx, STUDENTS, COURSE_NAMES)
    assert result["상태"] == "🔶확인필요"
    assert "동명이인" in result["메모"]


# ── match_transaction — 미등록 수강생 ─────────────────────────────────────────

def test_unknown_sender_flagged():
    tx = {"거래일시": "2026-01-10", "적요": "", "의뢰인": "홍길동", "입금": 20000}
    result = match_transaction(tx, STUDENTS, COURSE_NAMES)
    assert result["상태"] == "🔶확인필요"

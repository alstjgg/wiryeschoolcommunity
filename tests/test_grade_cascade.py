"""
Unit tests for apply_grade_cascade() — 등급 전환 cascade 로직 검증

improvement_plan.md §3의 경우의 수 테이블 기반.
실행: pytest tests/test_grade_cascade.py -v
"""

import pytest
from app.chains.payment import apply_grade_cascade


# ── 헬퍼 ──────────────────────────────────────────────────────────────────

def _make_app(name_id, name, 유형, 과목명="", 입금현황="❌미입금"):
    return {
        "이름ID": name_id, "이름": name, "유형": 유형,
        "과목명": 과목명, "예상금액": "20000",
        "입금현황": 입금현황, "확인사유": "", "처리상태": "",
        "입금시간": "", "입금자명(적요)": "",
        "전화번호": "", "주소": "", "신청일": "", "회차": "2026-1",
    }


def _make_member(name_id, name, grade="회원"):
    return {
        "이름ID": name_id, "이름": name, "등급": grade,
        "예외여부": "", "전화번호": "", "주소": "",
        "수강count": "0", "출석률(누적)": "", "마지막수강회차": "",
    }


# ══════════════════════════════════════════ 비회원 시작 ══════════════════

class TestNewMemberCascade:
    def test_signup_paid_tuition_paid(self):
        """비회원: 가입비O + 수강비O → 회원→준회원"""
        apps = [
            _make_app("김신규1234", "김신규", "신규가입", 입금현황="✅정상"),
            _make_app("김신규1234", "김신규", "수강", "오카리나", 입금현황="✅정상"),
        ]
        members = []
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 2
        assert changes[0]["변경후등급"] == "회원"
        assert changes[0]["사유"] == "신규가입"
        assert changes[1]["변경후등급"] == "준회원"
        assert changes[1]["사유"] == "수강료입금"

    def test_signup_paid_tuition_unpaid(self):
        """비회원: 가입비O + 수강비X → 회원 (수강보류)"""
        apps = [
            _make_app("김신규1234", "김신규", "신규가입", 입금현황="✅정상"),
            _make_app("김신규1234", "김신규", "수강", "오카리나", 입금현황="❌미입금"),
        ]
        members = []
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 1  # 신규가입만
        assert changes[0]["변경후등급"] == "회원"
        # 수강 건은 미입금이므로 변경 없음

    def test_signup_paid_fullmember_paid_tuition(self):
        """비회원: 가입비O + 정회원비O + 수강O → 회원→정회원, 수강면제"""
        apps = [
            _make_app("김신규1234", "김신규", "신규가입", 입금현황="✅정상"),
            _make_app("김신규1234", "김신규", "정회원", 입금현황="✅정상"),
            _make_app("김신규1234", "김신규", "수강", "오카리나", 입금현황="✅정상"),
        ]
        members = []
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 2  # 신규가입→회원, 정회원비→정회원
        assert changes[0]["변경후등급"] == "회원"
        assert changes[1]["변경후등급"] == "정회원"
        # 수강 건은 정회원이므로 면제
        assert apps[2]["입금현황"] == "💎면제"

    def test_signup_paid_fullmember_unpaid_tuition_paid(self):
        """비회원: 가입비O + 정회원비X + 수강비O → 관리자검토"""
        apps = [
            _make_app("김신규1234", "김신규", "신규가입", 입금현황="✅정상"),
            _make_app("김신규1234", "김신규", "정회원", 입금현황="❌미입금"),
            _make_app("김신규1234", "김신규", "수강", "오카리나", 입금현황="✅정상"),
        ]
        members = []
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 1  # 신규가입→회원만
        # 수강 건은 정회원 미입금 보류
        assert apps[2]["입금현황"] == "🔶확인필요"
        assert "정회원 신청 중" in apps[2]["확인사유"]

    def test_signup_unpaid(self):
        """비회원: 가입비X → 비회원 유지, 등급변경 없음"""
        apps = [
            _make_app("김신규1234", "김신규", "신규가입", 입금현황="❌미입금"),
            _make_app("김신규1234", "김신규", "수강", "오카리나", 입금현황="✅정상"),
        ]
        members = []
        changes = apply_grade_cascade(apps, members, "2026-1")

        # 비회원이므로 수강 등급변경 불가 (member가 없음)
        assert len(changes) == 0


# ══════════════════════════════════════════ 회원 시작 ══════════════════

class TestExistingMemberCascade:
    def test_tuition_paid(self):
        """회원: 수강비O → 준회원"""
        apps = [
            _make_app("박회원5678", "박회원", "수강", "명상", 입금현황="✅정상"),
        ]
        members = [_make_member("박회원5678", "박회원", "회원")]
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 1
        assert changes[0]["변경후등급"] == "준회원"

    def test_tuition_unpaid(self):
        """회원: 수강비X → 변경없음"""
        apps = [
            _make_app("박회원5678", "박회원", "수강", "명상", 입금현황="❌미입금"),
        ]
        members = [_make_member("박회원5678", "박회원", "회원")]
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 0

    def test_fullmember_paid_tuition(self):
        """회원: 정회원비O + 수강O → 정회원, 수강면제"""
        apps = [
            _make_app("박회원5678", "박회원", "정회원", 입금현황="✅정상"),
            _make_app("박회원5678", "박회원", "수강", "명상", 입금현황="✅정상"),
        ]
        members = [_make_member("박회원5678", "박회원", "회원")]
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 1
        assert changes[0]["변경후등급"] == "정회원"
        assert apps[1]["입금현황"] == "💎면제"

    def test_fullmember_unpaid_tuition_paid(self):
        """회원: 정회원비X + 수강비O → 관리자검토"""
        apps = [
            _make_app("박회원5678", "박회원", "정회원", 입금현황="❌미입금"),
            _make_app("박회원5678", "박회원", "수강", "명상", 입금현황="✅정상"),
        ]
        members = [_make_member("박회원5678", "박회원", "회원")]
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 0
        assert apps[1]["입금현황"] == "🔶확인필요"

    def test_fullmember_no_member_record(self):
        """정회원비 확정인데 회원이 아닌 경우 → 확인필요"""
        apps = [
            _make_app("미등록0001", "미등록", "정회원", 입금현황="✅정상"),
        ]
        members = []
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 0
        assert apps[0]["입금현황"] == "🔶확인필요"
        assert "회원 아님" in apps[0]["확인사유"]


# ══════════════════════════════════════════ 정회원 시작 ══════════════════

class TestFullMemberCascade:
    def test_tuition_exempted(self):
        """정회원: 수강O → 면제"""
        apps = [
            _make_app("이정회1111", "이정회", "수강", "영어", 입금현황="❌미입금"),
        ]
        members = [_make_member("이정회1111", "이정회", "정회원")]
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 0
        assert apps[0]["입금현황"] == "💎면제"


# ══════════════════════════════════════════ Idempotent ══════════════════

class TestIdempotent:
    def test_already_promoted_skipped(self):
        """이미 준회원인 회원은 다시 승급하지 않음"""
        apps = [
            _make_app("박준회9999", "박준회", "수강", "명상", 입금현황="✅정상"),
        ]
        members = [_make_member("박준회9999", "박준회", "준회원")]
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 0  # 이미 준회원

    def test_already_fullmember_skipped(self):
        """이미 정회원인 회원은 정회원 승급 건너뜀"""
        apps = [
            _make_app("최정회0000", "최정회", "정회원", 입금현황="✅정상"),
        ]
        members = [_make_member("최정회0000", "최정회", "정회원")]
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 0

    def test_double_run_same_result(self):
        """두 번 실행해도 결과 동일 (idempotent)"""
        apps = [
            _make_app("김신규1234", "김신규", "신규가입", 입금현황="✅정상"),
            _make_app("김신규1234", "김신규", "수강", "오카리나", 입금현황="✅정상"),
        ]
        members = []

        changes1 = apply_grade_cascade(apps, members, "2026-1")
        changes2 = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes1) == 2  # 첫 실행: 회원+준회원
        assert len(changes2) == 0  # 두 번째: 이미 처리됨


# ══════════════════════════════════════════ 다중 수강 ══════════════════

class TestMultipleCourses:
    def test_multiple_courses_single_promotion(self):
        """같은 회원이 2과목 수강 → 준회원 승급 1회만"""
        apps = [
            _make_app("다과목0001", "다과목", "수강", "명상", 입금현황="✅정상"),
            _make_app("다과목0001", "다과목", "수강", "영어", 입금현황="✅정상"),
        ]
        members = [_make_member("다과목0001", "다과목", "회원")]
        changes = apply_grade_cascade(apps, members, "2026-1")

        assert len(changes) == 1  # 준회원 승급 1회만
        assert changes[0]["변경후등급"] == "준회원"

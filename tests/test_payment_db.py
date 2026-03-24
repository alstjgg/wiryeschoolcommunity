"""
Unit tests for app/chains/payment.py — dual-write 로직 검증

Google API / DB 접속 없음. USE_DB_SOT 플래그에 따른 분기 + 순수 로직 테스트.
실행: pytest tests/test_payment_db.py -v
"""

import pytest
from unittest.mock import patch, AsyncMock

from app.chains.payment import (
    build_applications,
    apply_exemptions,
    applications_to_students,
    apply_matching_results,
    find_unpaid,
    format_results,
    get_cycle_year,
    _parse_ym_to_cycle_year,
    apply_grade_cascade,
)


# ── 픽스처 ──────────────────────────────────────────────────────────────────

APPLICANTS = [
    {"이름ID": "김기춘1234", "이름": "김기춘", "강좌명": "경제뉴스로 배우는 경제해설(기초)",
     "전화번호": "010-1234-5678", "주소": "위례", "신청일": "2026-01-15"},
    {"이름ID": "박민서6804", "이름": "박민서", "강좌명": "나, 마음챙김 명상",
     "전화번호": "010-6804-1234", "주소": "위례", "신청일": "2026-01-16"},
]

MEMBER_SIGNUPS = [
    {"이름ID": "신규자0001", "이름": "신규자", "유형": "신규가입", "과목명": "",
     "전화번호": "010-0001-0001", "주소": "위례", "신청일": "2026-01-10",
     },
]

FULLMEMBER_SIGNUPS = [
    {"이름ID": "정회원0001", "이름": "정회원", "유형": "정회원", "과목명": "",
     "전화번호": "010-0002-0002", "주소": "위례", "신청일": "2026-01-10",
     },
]


# ── build_applications ──────────────────────────────────────────────────────

class TestBuildApplications:
    def test_basic_merge(self):
        """수강 + 신규가입 + 정회원이 합쳐지는지"""
        apps = build_applications(APPLICANTS, MEMBER_SIGNUPS, FULLMEMBER_SIGNUPS)
        assert len(apps) == 4  # 2 수강 + 1 신규가입 + 1 정회원

    def test_type_distribution(self):
        """유형별 정확한 분류"""
        apps = build_applications(APPLICANTS, MEMBER_SIGNUPS, FULLMEMBER_SIGNUPS)
        types = [a["유형"] for a in apps]
        assert types.count("수강") == 2
        assert types.count("신규가입") == 1
        assert types.count("정회원") == 1

    def test_default_payment_status(self):
        """초기 입금현황은 모두 ❌미입금"""
        apps = build_applications(APPLICANTS, MEMBER_SIGNUPS, FULLMEMBER_SIGNUPS)
        for a in apps:
            assert a["입금현황"] == "❌미입금"

    def test_expected_amounts(self):
        """유형별 예상금액"""
        apps = build_applications(APPLICANTS, MEMBER_SIGNUPS, FULLMEMBER_SIGNUPS)
        amounts_by_type = {a["유형"]: a["예상금액"] for a in apps}
        assert amounts_by_type["수강"] == "20000"
        assert amounts_by_type["신규가입"] == "10000"
        assert amounts_by_type["정회원"] == "120000"

    def test_duplicate_removal(self):
        """동일 key 중복 제거"""
        apps = build_applications(
            APPLICANTS + APPLICANTS, MEMBER_SIGNUPS, FULLMEMBER_SIGNUPS
        )
        assert len(apps) == 4  # 중복된 수강 2건은 제거


# ── apply_exemptions ────────────────────────────────────────────────────────

class TestApplyExemptions:
    def test_fullmember_exempted(self):
        """정회원은 수강료 면제"""
        apps = [
            {"이름ID": "김기춘1234", "유형": "수강", "입금현황": "❌미입금"},
        ]
        members = [
            {"이름ID": "김기춘1234", "등급": "정회원"},
        ]
        exempted = apply_exemptions(apps, members)
        assert len(exempted) == 1
        assert apps[0]["입금현황"] == "💎면제"

    def test_regular_member_not_exempted(self):
        """일반 회원은 면제 안 됨"""
        apps = [
            {"이름ID": "박민서6804", "유형": "수강", "입금현황": "❌미입금"},
        ]
        members = [
            {"이름ID": "박민서6804", "등급": "회원"},
        ]
        exempted = apply_exemptions(apps, members)
        assert len(exempted) == 0
        assert apps[0]["입금현황"] == "❌미입금"

    def test_non_tuition_ignored(self):
        """신규가입/정회원 유형은 면제 대상 아님"""
        apps = [
            {"이름ID": "정회원0001", "유형": "정회원", "입금현황": "❌미입금"},
        ]
        members = [
            {"이름ID": "정회원0001", "등급": "정회원"},
        ]
        exempted = apply_exemptions(apps, members)
        assert len(exempted) == 0


# ── applications_to_students ────────────────────────────────────────────────

class TestApplicationsToStudents:
    def test_filters_tuition_only(self):
        """수강 유형만 추출"""
        apps = build_applications(APPLICANTS, MEMBER_SIGNUPS, FULLMEMBER_SIGNUPS)
        students = applications_to_students(apps)
        assert len(students) == 2
        for s in students:
            assert "이름ID" in s
            assert "이름" in s
            assert "강좌명" in s


# ── apply_matching_results ──────────────────────────────────────────────────

class TestApplyMatchingResults:
    def test_confirmed_match_applied(self):
        """✅정상 매칭이 applications에 반영"""
        apps = [
            {"이름ID": "김기춘1234", "유형": "수강", "과목명": "경제뉴스로 배우는 경제해설(기초)",
             "입금현황": "❌미입금", "입금시간": "", "입금자명(적요)": ""},
        ]
        results = [
            {"매칭ID": "김기춘1234", "매칭강좌": "경제뉴스로 배우는 경제해설(기초)",
             "상태": "✅정상", "거래일시": "2026-01-20 14:30", "적요": "김기춘경제기초"},
        ]
        apply_matching_results(apps, results)
        assert apps[0]["입금현황"] == "✅정상"
        assert apps[0]["입금시간"] == "2026-01-20 14:30"

    def test_skip_ignored(self):
        """⏭️스킵은 무시"""
        apps = [
            {"이름ID": "김기춘1234", "유형": "수강", "과목명": "과목",
             "입금현황": "❌미입금", "입금시간": "", "입금자명(적요)": ""},
        ]
        results = [
            {"매칭ID": "김기춘1234", "매칭강좌": "과목", "상태": "⏭️스킵"},
        ]
        apply_matching_results(apps, results)
        assert apps[0]["입금현황"] == "❌미입금"


# ── find_unpaid / format_results ────────────────────────────────────────────

class TestFindUnpaid:
    def test_finds_unpaid_tuitions(self):
        """미입금 수강 건만 필터"""
        apps = [
            {"유형": "수강", "입금현황": "❌미입금"},
            {"유형": "수강", "입금현황": "✅정상"},
            {"유형": "신규가입", "입금현황": "❌미입금"},
        ]
        unpaid = find_unpaid(apps)
        assert len(unpaid) == 1


class TestFormatResults:
    def test_basic_format(self):
        """기본 요약 포맷"""
        matched = [
            {"상태": "✅정상"},
            {"상태": "✅정상"},
            {"상태": "🔶확인필요"},
        ]
        apps = [
            {"유형": "수강", "입금현황": "✅정상"},
            {"유형": "수강", "입금현황": "✅정상"},
            {"유형": "수강", "입금현황": "❌미입금"},
        ]
        result = format_results(matched, apps, exempted=[{"유형": "수강"}])
        assert "✅ 2건" in result
        assert "🔶 1건" in result
        assert "❌ 1건" in result
        assert "💎 1건" in result


# ── dual-write 분기 테스트 (USE_DB_SOT 플래그) ──────────────────────────────

class TestDBOnlyAPI:
    @pytest.mark.asyncio
    async def test_load_members_from_db(self):
        """load_members_from_sheet() → DB에서 읽기"""
        mock_db = AsyncMock(return_value=[
            {"이름ID": "테스트0001", "이름": "테스트", "전화번호": "", "주소": "",
             "등급": "회원", "예외여부": "", "수강count": "0",
             "출석률(누적)": "", "마지막수강회차": ""},
        ])
        with patch("app.services.db.load_members", mock_db):
            from app.chains.payment import load_members_from_sheet
            result = await load_members_from_sheet()
            assert len(result) == 1
            assert result[0]["이름ID"] == "테스트0001"
            mock_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_append_member_records_db_and_sync(self):
        """append_member_records() → DB insert + Sheets sync"""
        mock_db_insert = AsyncMock()
        mock_sync = AsyncMock()
        records = [
            {"이름ID": "테스트0001", "이름": "테스트", "변경일시": "2026-01-20",
             "변경전등급": "회원", "변경후등급": "준회원", "사유": "수강료입금",
             "관련회차": "2026-1"},
        ]
        with patch("app.services.db.insert_member_records", mock_db_insert), \
             patch("app.services.sheets_sync.sync_to_sheets", mock_sync):
            from app.chains.payment import append_member_records
            await append_member_records(records)
            mock_db_insert.assert_called_once_with(records)
            mock_sync.assert_called_once_with("member_records", data=records)

    @pytest.mark.asyncio
    async def test_db_failure_propagates(self):
        """DB 실패 시 에러 전파 (폴백 없음)"""
        mock_db = AsyncMock(side_effect=Exception("DB connection error"))
        with patch("app.services.db.load_members", mock_db):
            from app.chains.payment import load_members_from_sheet
            with pytest.raises(Exception, match="DB connection error"):
                await load_members_from_sheet()


# ── get_cycle_year / _parse_ym_to_cycle_year ──────────────────────────────

class TestCycleYear:
    def test_winter_term(self):
        """겨울학기(1)는 전년 사이클"""
        assert get_cycle_year("2026-1") == 2025

    def test_spring_term(self):
        """봄학기(2)는 당년 사이클"""
        assert get_cycle_year("2026-2") == 2026

    def test_autumn_term(self):
        assert get_cycle_year("2025-4") == 2025

    def test_parse_ym_short(self):
        """YY.MM 형식 파싱"""
        assert _parse_ym_to_cycle_year("25.03") == 2024  # 3월 → 1학기 → 전년
        assert _parse_ym_to_cycle_year("25.05") == 2025  # 5월 → 2학기 → 당년
        assert _parse_ym_to_cycle_year("26.01") == 2025  # 1월 → 1학기 → 전년

    def test_parse_ym_full(self):
        """YYYY.MM 형식 파싱"""
        assert _parse_ym_to_cycle_year("2025.10") == 2025  # 10월 → 4학기

    def test_parse_ym_invalid(self):
        assert _parse_ym_to_cycle_year("invalid") is None
        assert _parse_ym_to_cycle_year("") is None


# ── apply_exemptions with exception_ids ───────────────────────────────────

class TestApplyExemptionsWithExceptionIds:
    def test_exception_all_types_exempted(self):
        """강사/사무처 면제 대상은 모든 유형(수강/신규가입/정회원) 면제"""
        apps = [
            {"이름ID": "강사0001", "유형": "신규가입", "입금현황": "❌미입금"},
            {"이름ID": "강사0001", "유형": "정회원", "입금현황": "❌미입금"},
            {"이름ID": "강사0001", "유형": "수강", "입금현황": "❌미입금"},
        ]
        members = []
        exc_ids = {"강사0001"}
        exempted = apply_exemptions(apps, members, exc_ids)
        assert len(exempted) == 3
        for app in apps:
            assert app["입금현황"] == "💎면제"
            assert app["확인사유"] == "강사/사무처 면제"

    def test_non_exception_not_affected(self):
        """면제 대상 아닌 사람은 기존 로직 유지"""
        apps = [
            {"이름ID": "일반0001", "유형": "수강", "입금현황": "❌미입금"},
        ]
        members = [{"이름ID": "일반0001", "등급": "회원"}]
        exc_ids = {"강사0001"}
        exempted = apply_exemptions(apps, members, exc_ids)
        assert len(exempted) == 0
        assert apps[0]["입금현황"] == "❌미입금"


# ── apply_grade_cascade with exception_ids ────────────────────────────────

class TestGradeCascadeWithExceptionIds:
    def test_exception_new_member_registered(self):
        """면제 대상 신규가입 → 비회원이 회원으로 등록 + is_exception=TRUE"""
        apps = [
            {"이름ID": "강사0001", "이름": "강사A", "유형": "신규가입",
             "입금현황": "💎면제", "확인사유": "강사/사무처 면제",
             "전화번호": "", "주소": ""},
        ]
        members = []
        changes = apply_grade_cascade(apps, members, "2026-1", {"강사0001"})
        assert len(changes) == 1
        assert changes[0]["사유"] == "신규가입(강사/사무처면제)"
        assert members[0]["예외여부"] == "TRUE"

    def test_exception_promoted_to_fullmember(self):
        """면제 대상 정회원 신청 → 정회원 승급 + is_exception=TRUE"""
        apps = [
            {"이름ID": "강사0001", "이름": "강사A", "유형": "정회원",
             "입금현황": "💎면제", "확인사유": "강사/사무처 면제"},
        ]
        members = [
            {"이름ID": "강사0001", "이름": "강사A", "등급": "회원", "예외여부": ""},
        ]
        changes = apply_grade_cascade(apps, members, "2026-1", {"강사0001"})
        assert len(changes) == 1
        assert changes[0]["사유"] == "정회원비면제(강사/사무처)"
        assert members[0]["등급"] == "정회원"
        assert members[0]["예외여부"] == "TRUE"

    def test_exception_tuition_exempted_after_fullmember(self):
        """면제 대상 정회원 승급 후 수강비도 면제"""
        apps = [
            {"이름ID": "강사0001", "이름": "강사A", "유형": "정회원",
             "입금현황": "💎면제", "확인사유": "강사/사무처 면제"},
            {"이름ID": "강사0001", "이름": "강사A", "유형": "수강", "과목명": "오카리나",
             "입금현황": "💎면제", "확인사유": "강사/사무처 면제"},
        ]
        members = [
            {"이름ID": "강사0001", "이름": "강사A", "등급": "회원", "예외여부": ""},
        ]
        changes = apply_grade_cascade(apps, members, "2026-1", {"강사0001"})
        # 정회원 승급 1건, 수강 면제는 changes에 미포함 (Pass 3에서 정회원 → 💎면제)
        assert len(changes) == 1
        assert apps[1]["입금현황"] == "💎면제"

    def test_no_exception_ids_normal_flow(self):
        """exception_ids 없으면 기존 로직과 동일"""
        apps = [
            {"이름ID": "김기춘1234", "이름": "김기춘", "유형": "수강", "과목명": "오카리나",
             "입금현황": "✅정상"},
        ]
        members = [
            {"이름ID": "김기춘1234", "이름": "김기춘", "등급": "회원", "예외여부": ""},
        ]
        changes = apply_grade_cascade(apps, members, "2026-1")
        assert len(changes) == 1
        assert changes[0]["사유"] == "수강료입금"
        assert members[0]["등급"] == "준회원"

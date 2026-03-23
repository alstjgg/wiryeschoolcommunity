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

class TestDualWriteBranching:
    @pytest.mark.asyncio
    @patch("app.chains.payment.USE_DB_SOT", True)
    @patch("app.chains.payment._load_members_from_sheets")
    async def test_load_members_uses_db_when_flag_true(self, mock_sheets):
        """USE_DB_SOT=true → DB에서 읽기"""
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
            mock_sheets.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.chains.payment.USE_DB_SOT", False)
    @patch("app.chains.payment._load_members_from_sheets")
    async def test_load_members_uses_sheets_when_flag_false(self, mock_sheets):
        """USE_DB_SOT=false → Sheets에서 읽기"""
        mock_sheets.return_value = [
            {"이름ID": "시트0001", "이름": "시트", "등급": "회원"},
        ]
        from app.chains.payment import load_members_from_sheet
        result = await load_members_from_sheet()
        assert len(result) == 1
        assert result[0]["이름ID"] == "시트0001"
        mock_sheets.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.chains.payment.USE_DB_SOT", True)
    @patch("app.chains.payment._append_member_records_to_sheets")
    async def test_append_member_records_db_mode(self, mock_sheets):
        """USE_DB_SOT=true → DB insert + n8n webhook (Sheets 직접 쓰기 없음)"""
        mock_db_insert = AsyncMock()
        mock_n8n = AsyncMock()
        records = [
            {"이름ID": "테스트0001", "이름": "테스트", "변경일시": "2026-01-20",
             "변경전등급": "회원", "변경후등급": "준회원", "사유": "수강료입금",
             "관련회차": "2026-1"},
        ]
        with patch("app.services.db.insert_member_records", mock_db_insert), \
             patch("app.services.n8n.trigger_sheets_sync", mock_n8n):
            from app.chains.payment import append_member_records
            await append_member_records(records)
            mock_db_insert.assert_called_once_with(records)
            mock_n8n.assert_called_once_with("members")
            mock_sheets.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.chains.payment.USE_DB_SOT", True)
    @patch("app.chains.payment._load_members_from_sheets")
    async def test_db_failure_falls_back_to_sheets(self, mock_sheets):
        """DB 실패 시 Sheets로 폴백"""
        mock_db = AsyncMock(side_effect=Exception("DB connection error"))
        mock_sheets.return_value = [{"이름ID": "폴백0001", "이름": "폴백", "등급": "회원"}]
        with patch("app.services.db.load_members", mock_db):
            from app.chains.payment import load_members_from_sheet
            result = await load_members_from_sheet()
            assert len(result) == 1
            assert result[0]["이름ID"] == "폴백0001"
            mock_sheets.assert_called_once()

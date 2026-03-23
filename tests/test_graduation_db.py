"""
Unit tests for app/chains/graduation.py — 순수 로직 + dual-write 분기 테스트

Google API / DB 접속 없음.
실행: pytest tests/test_graduation_db.py -v
"""

import pytest
from unittest.mock import patch, AsyncMock

from app.chains.graduation import (
    get_members_to_demote,
    _apply_stats,
)


# ── 픽스처 ──────────────────────────────────────────────────────────────────

MEMBERS = [
    {"이름ID": "준회원01", "이름": "준회원A", "등급": "준회원", "예외여부": ""},
    {"이름ID": "준회원02", "이름": "준회원B", "등급": "준회원", "예외여부": ""},
    {"이름ID": "정회원01", "이름": "정회원A", "등급": "정회원", "예외여부": ""},
    {"이름ID": "정회원02", "이름": "정회원B", "등급": "정회원", "예외여부": "TRUE"},
    {"이름ID": "회원01", "이름": "회원A", "등급": "회원", "예외여부": ""},
]

COURSE_RECORDS = [
    {"이름ID": "회원01", "회차": "2025-4", "과목명": "오카리나", "출석률": "90.0"},
    {"이름ID": "회원01", "회차": "2026-1", "과목명": "오카리나", "출석률": "85.0"},
    {"이름ID": "준회원01", "회차": "2026-1", "과목명": "명상", "출석률": "100.0"},
]


# ── get_members_to_demote ───────────────────────────────────────────────────

class TestGetMembersToDemote:
    def test_junior_demoted_every_term(self):
        """준회원은 매 종강 시 강등 대상"""
        junior, full = get_members_to_demote(MEMBERS, is_winter_term=False)
        assert len(junior) == 2
        assert len(full) == 0

    def test_fullmember_demoted_only_winter(self):
        """정회원은 겨울학기(1학기)에만 강등 — 활동 중 사무처 직원은 제외"""
        active_staff = {"정회원02"}
        junior, full = get_members_to_demote(MEMBERS, is_winter_term=True, active_staff_ids=active_staff)
        assert len(junior) == 2
        assert len(full) == 1  # 정회원A만 (정회원B는 활동 중 직원)

    def test_active_staff_excluded(self):
        """활동 중 사무처 직원은 겨울학기에도 강등 면제"""
        active_staff = {"정회원02"}
        junior, full = get_members_to_demote(MEMBERS, is_winter_term=True, active_staff_ids=active_staff)
        demoted_ids = [m["이름ID"] for m in full]
        assert "정회원02" not in demoted_ids

    def test_no_staff_ids_demotes_all(self):
        """active_staff_ids 없으면 정회원 전원 강등"""
        junior, full = get_members_to_demote(MEMBERS, is_winter_term=True)
        assert len(full) == 2

    def test_regular_member_not_demoted(self):
        """일반 회원은 강등 대상 아님"""
        junior, full = get_members_to_demote(MEMBERS, is_winter_term=True)
        all_demoted_ids = [m["이름ID"] for m in junior + full]
        assert "회원01" not in all_demoted_ids


# ── _apply_stats ────────────────────────────────────────────────────────────

class TestApplyStats:
    def test_course_count(self):
        """수강기록에서 수강count 재집계"""
        members = [
            {"이름ID": "회원01", "수강count": "0", "출석률(누적)": "", "마지막수강회차": ""},
        ]
        result = _apply_stats(members, COURSE_RECORDS)
        assert result[0]["수강count"] == "2"

    def test_avg_attendance(self):
        """수강기록에서 출석률(누적) 재집계"""
        members = [
            {"이름ID": "회원01", "수강count": "0", "출석률(누적)": "", "마지막수강회차": ""},
        ]
        result = _apply_stats(members, COURSE_RECORDS)
        # (90.0 + 85.0) / 2 = 87.5
        assert result[0]["출석률(누적)"] == "87.5"

    def test_last_term(self):
        """수강기록에서 마지막수강회차 재집계"""
        members = [
            {"이름ID": "회원01", "수강count": "0", "출석률(누적)": "", "마지막수강회차": ""},
        ]
        result = _apply_stats(members, COURSE_RECORDS)
        assert result[0]["마지막수강회차"] == "2026-1"

    def test_unknown_member_unchanged(self):
        """수강기록에 없는 회원은 변경 없음"""
        members = [
            {"이름ID": "미수강01", "수강count": "0", "출석률(누적)": "", "마지막수강회차": ""},
        ]
        result = _apply_stats(members, COURSE_RECORDS)
        assert result[0]["수강count"] == "0"
        assert result[0]["마지막수강회차"] == ""

    def test_empty_records(self):
        """수강기록이 비어있으면 회원 그대로"""
        members = [
            {"이름ID": "회원01", "수강count": "5", "출석률(누적)": "80", "마지막수강회차": "2025-3"},
        ]
        result = _apply_stats(members, [])
        assert result[0]["수강count"] == "5"  # 수강기록에 없으므로 갱신 안 됨

    def test_single_record_avg(self):
        """수강기록 1건이면 출석률 = 그 값 그대로"""
        members = [
            {"이름ID": "준회원01", "수강count": "0", "출석률(누적)": "", "마지막수강회차": ""},
        ]
        result = _apply_stats(members, COURSE_RECORDS)
        assert result[0]["출석률(누적)"] == "100.0"


# ── dual-write 분기 테스트 ──────────────────────────────────────────────────

class TestGraduationDualWrite:
    @pytest.mark.asyncio
    @patch("app.chains.graduation.USE_DB_SOT", True)
    @patch("app.chains.graduation._recalculate_from_sheets")
    async def test_recalculate_uses_db_when_flag_true(self, mock_sheets):
        """USE_DB_SOT=true → DB에서 수강기록 읽기"""
        mock_db_records = AsyncMock(return_value=COURSE_RECORDS)
        members_input = [
            {"이름ID": "회원01", "수강count": "0", "출석률(누적)": "", "마지막수강회차": ""},
        ]
        with patch("app.services.db.load_course_records", mock_db_records):
            from app.chains.graduation import recalculate_member_stats
            result = await recalculate_member_stats(members_input)
            assert result[0]["수강count"] == "2"
            mock_db_records.assert_called_once()
            mock_sheets.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.chains.graduation.USE_DB_SOT", False)
    @patch("app.chains.graduation._recalculate_from_sheets")
    async def test_recalculate_uses_sheets_when_flag_false(self, mock_sheets):
        """USE_DB_SOT=false → Sheets에서 수강기록 읽기"""
        members_input = [
            {"이름ID": "회원01", "수강count": "0", "출석률(누적)": "", "마지막수강회차": ""},
        ]
        mock_sheets.return_value = members_input
        from app.chains.graduation import recalculate_member_stats
        result = await recalculate_member_stats(members_input)
        mock_sheets.assert_called_once()

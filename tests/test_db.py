"""
Unit tests for app/services/db.py

DB 접속 없음. 이모지↔코드 변환, dict 매핑 로직만 테스트.
실행: pytest tests/test_db.py -v
"""

import pytest
from app.services.db import (
    _emoji_to_code,
    _code_to_emoji,
    STATUS_TO_EMOJI,
    EMOJI_TO_STATUS,
)


# ── 이모지 ↔ 코드 변환 ──────────────────────────────────────────────────────

class TestEmojiCodeConversion:
    def test_all_emoji_to_code(self):
        """모든 이모지 상태가 코드로 변환되는지 확인"""
        assert _emoji_to_code("✅정상") == "confirmed"
        assert _emoji_to_code("🔶확인필요") == "needs_review"
        assert _emoji_to_code("❌미입금") == "not_paid"
        assert _emoji_to_code("⚠️이름불일치") == "name_mismatch"
        assert _emoji_to_code("🔄중복") == "duplicate"
        assert _emoji_to_code("💎면제") == "exempted"

    def test_all_code_to_emoji(self):
        """모든 코드가 이모지로 변환되는지 확인"""
        assert _code_to_emoji("confirmed") == "✅정상"
        assert _code_to_emoji("needs_review") == "🔶확인필요"
        assert _code_to_emoji("not_paid") == "❌미입금"
        assert _code_to_emoji("name_mismatch") == "⚠️이름불일치"
        assert _code_to_emoji("duplicate") == "🔄중복"
        assert _code_to_emoji("exempted") == "💎면제"

    def test_unknown_emoji_passthrough(self):
        """알 수 없는 이모지는 그대로 반환"""
        assert _emoji_to_code("🆕새로운상태") == "🆕새로운상태"

    def test_unknown_code_passthrough(self):
        """알 수 없는 코드는 그대로 반환"""
        assert _code_to_emoji("unknown_status") == "unknown_status"

    def test_empty_string_passthrough(self):
        """빈 문자열은 그대로 반환"""
        assert _emoji_to_code("") == ""
        assert _code_to_emoji("") == ""

    def test_roundtrip_emoji_code_emoji(self):
        """이모지 → 코드 → 이모지 라운드트립"""
        for emoji, code in EMOJI_TO_STATUS.items():
            assert _code_to_emoji(_emoji_to_code(emoji)) == emoji

    def test_roundtrip_code_emoji_code(self):
        """코드 → 이모지 → 코드 라운드트립"""
        for code, emoji in STATUS_TO_EMOJI.items():
            assert _emoji_to_code(_code_to_emoji(code)) == code

    def test_mapping_completeness(self):
        """매핑 테이블이 6가지 상태를 모두 포함하는지"""
        assert len(STATUS_TO_EMOJI) == 6
        assert len(EMOJI_TO_STATUS) == 6
        assert set(STATUS_TO_EMOJI.keys()) == {
            "confirmed", "needs_review", "not_paid",
            "name_mismatch", "duplicate", "exempted",
        }

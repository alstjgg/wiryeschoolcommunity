"""
Unit tests for app/context/term.py

date.today()를 unittest.mock으로 패치.
모킹 대상: "app.context.term.date" (모듈 내 직접 바인딩된 이름)
실행: pytest tests/test_term.py -v
"""

import pytest
from datetime import date
from unittest.mock import patch

from app.context.term import get_current_term


def test_january_is_term_1():
    with patch("app.context.term.date") as mock_date:
        mock_date.today.return_value = date(2026, 1, 15)
        result = get_current_term()
    assert result["term"] == 1
    assert result["season"] == "겨울"
    assert result["term_id"] == "2026-1"
    assert result["term_name"] == "2026-1 겨울학기"
    assert result["year"] == 2026

def test_march_boundary_is_term_1():
    with patch("app.context.term.date") as mock_date:
        mock_date.today.return_value = date(2026, 3, 31)
        result = get_current_term()
    assert result["term"] == 1

def test_april_is_term_2():
    with patch("app.context.term.date") as mock_date:
        mock_date.today.return_value = date(2026, 4, 1)
        result = get_current_term()
    assert result["term"] == 2
    assert result["season"] == "봄"
    assert result["term_id"] == "2026-2"

def test_july_is_term_3():
    with patch("app.context.term.date") as mock_date:
        mock_date.today.return_value = date(2026, 7, 1)
        result = get_current_term()
    assert result["term"] == 3
    assert result["season"] == "여름"
    assert result["term_id"] == "2026-3"

def test_october_is_term_4():
    with patch("app.context.term.date") as mock_date:
        mock_date.today.return_value = date(2026, 10, 1)
        result = get_current_term()
    assert result["term"] == 4
    assert result["season"] == "가을"

def test_december_boundary_is_term_4():
    with patch("app.context.term.date") as mock_date:
        mock_date.today.return_value = date(2026, 12, 31)
        result = get_current_term()
    assert result["term"] == 4
    assert result["term_id"] == "2026-4"

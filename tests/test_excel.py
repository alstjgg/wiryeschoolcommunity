"""
Unit tests for app/services/excel.py

파일 없이 인메모리 바이트로 테스트.
실행: pytest tests/test_excel.py -v
"""

import io
import pytest
import openpyxl

from app.services.excel import parse_bank_statement, parse_applicant_list


# ── 헬퍼 함수 ─────────────────────────────────────────────────────────────────

def make_bank_xlsx(data_rows: list[list]) -> bytes:
    """
    은행 입금내역 .xlsx 인메모리 빌드.
    구조: 메타 6행 + 헤더 1행 + 데이터 행 + 합계 1행 (마지막).

    컬럼: 거래일시(0) | 적요B(1) | 적요C(2) | 의뢰인/수취인(3) | 입금(4) | 출금(5)
    '적요' 헤더가 B+C 두 컬럼을 차지하는 실제 파일 구조 반영.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    for i in range(6):
        ws.append([f"메타{i+1}", "", "", "", "", ""])
    ws.append(["거래일시", "적요", "", "의뢰인/수취인", "입금", "출금"])
    for row in data_rows:
        ws.append(row)
    # 합계 행 (마지막 — 파서가 제외해야 함)
    total = sum(r[4] for r in data_rows if isinstance(r[4], (int, float)) and r[4] > 0)
    ws.append(["합계", "", "", "", total, ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_applicant_html(rows: list[dict]) -> bytes:
    """
    배움숲 신청자 목록 HTML .xls 인메모리 빌드.
    헤더 23개, 데이터 22개 셀 (실제결제금액 누락 재현).
    """
    headers = [
        "번호", "회차", "강좌명", "감면정보", "수강료", "실제결제금액",
        "신청자(아이디)", "성별", "생년월일", "나이", "연락처",
        "주소", "행정동", "이메일", "분류", "교육기간",
        "신청상태", "진행상태", "환불신청일", "환불은행", "환불계좌번호",
        "환불예금주", "환불사유",
    ]
    header_html = "".join(f"<th>{h}</th>" for h in headers)

    data_rows_html = ""
    for idx, r in enumerate(rows, start=1):
        # 22개 셀 — 실제결제금액 데이터 없음 (헤더에만 존재)
        cells = [
            str(r.get("번호", idx)),
            r.get("회차", "2026-1"),
            r.get("강좌명", "경제뉴스로 배우는 경제해설(기초)"),
            r.get("감면정보", ""),
            r.get("수강료", "20000"),
            # 실제결제금액 셀 없음
            r.get("신청자", f"테스트{idx}(user{idx})"),
            r.get("성별", "남"),
            r.get("생년월일", "1960-01-01"),
            r.get("나이", "66"),
            r.get("연락처", f"010-0000-{idx:04d}"),
            r.get("주소", "서울시 송파구"),
            r.get("행정동", "위례동"),
            r.get("이메일", f"user{idx}@test.com"),
            r.get("분류", ""),
            r.get("교육기간", ""),
            r.get("신청상태", "결제완료"),
            r.get("진행상태", ""),
            "", "", "", "", "",
        ]
        data_rows_html += "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

    html = f"""<html><body><table>
<tr>{header_html}</tr>
{data_rows_html}
</table></body></html>"""
    return html.encode("utf-8")


# ── parse_bank_statement ──────────────────────────────────────────────────────

def test_parse_bank_normal_row():
    # 컬럼: 거래일시, 적요B, 적요C, 의뢰인, 입금, 출금
    data = make_bank_xlsx([
        ["2026-01-10 10:00", "박민서명상", "나, 마음챙김 명상", "박민서", 20000, 0],
    ])
    result = parse_bank_statement(data)
    assert len(result) == 1
    assert result[0]["입금"] == 20000
    assert result[0]["의뢰인"] == "박민서"
    assert result[0]["적요"] == "박민서명상 나, 마음챙김 명상"
    assert result[0]["거래일시"] == "2026-01-10 10:00"

def test_parse_bank_memo_c_question_mark_ignored():
    """적요C가 '?' 또는 공백만이면 적요B만 사용"""
    data = make_bank_xlsx([
        ["2026-01-10", "최정숙", "?", "최정숙", 20000, 0],
    ])
    result = parse_bank_statement(data)
    assert result[0]["적요"] == "최정숙"

def test_parse_bank_skips_zero_amount():
    data = make_bank_xlsx([
        ["2026-01-10", "수강료", "", "박민서", 20000, 0],
        ["2026-01-10", "출금건", "", "은행", 0, 5000],  # 입금=0 → 스킵
    ])
    result = parse_bank_statement(data)
    assert len(result) == 1
    assert result[0]["입금"] == 20000

def test_parse_bank_skips_negative_amount():
    data = make_bank_xlsx([
        ["2026-01-10", "수강료", "", "김기춘", 20000, 0],
        ["2026-01-10", "이자환수", "", "은행", -100, 0],  # 음수 → 스킵
    ])
    result = parse_bank_statement(data)
    assert len(result) == 1

def test_parse_bank_excludes_last_totals_row():
    # 1개 데이터 행만 있어야 함 (합계 행 제외 확인)
    data = make_bank_xlsx([
        ["2026-01-10", "수강료", "", "이현수", 20000, 0],
    ])
    result = parse_bank_statement(data)
    assert len(result) == 1  # 합계 행이 포함됐다면 2가 됨

def test_parse_bank_comma_in_amount():
    data = make_bank_xlsx([
        ["2026-01-10", "수강료", "", "이현수", "20,000", 0],
    ])
    result = parse_bank_statement(data)
    assert len(result) == 1
    assert result[0]["입금"] == 20000

def test_parse_bank_too_short_returns_empty():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["행 하나만"])
    buf = io.BytesIO()
    wb.save(buf)
    result = parse_bank_statement(buf.getvalue())
    assert result == []

def test_parse_bank_multiple_rows():
    data = make_bank_xlsx([
        ["2026-01-10", "수강료", "", "박민서", 20000, 0],
        ["2026-01-11", "수강료", "", "이현수", 30000, 0],
        ["2026-01-12", "수강료", "", "김기춘", 20000, 0],
    ])
    result = parse_bank_statement(data)
    assert len(result) == 3


# ── parse_applicant_list ──────────────────────────────────────────────────────

def test_parse_applicant_basic():
    data = make_applicant_html([{
        "신청자": "홍길동(hong123)",
        "연락처": "010-1234-5678",
        "강좌명": "경제뉴스로 배우는 경제해설(기초)",
        "신청상태": "결제완료",
    }])
    result = parse_applicant_list(data)
    assert len(result) == 1
    assert result[0]["이름"] == "홍길동"
    assert result[0]["이름ID"] == "홍길동5678"  # 전화번호 뒤 4자리
    assert result[0]["강좌명"] == "경제뉴스로 배우는 경제해설(기초)"
    assert result[0]["신청상태"] == "결제완료"

def test_parse_applicant_column_offset_correction():
    """실제결제금액 누락 보정 → 신청상태가 올바른 컬럼에서 읽혀야 함"""
    data = make_applicant_html([{
        "신청자": "박민서(park99)",
        "연락처": "010-9999-6804",
        "강좌명": "나, 마음챙김 명상",
        "신청상태": "결제완료",
    }])
    result = parse_applicant_list(data)
    # 오프셋 보정 실패 시 신청상태 컬럼이 밀려 "결제완료"가 아닌 다른 값이 됨
    assert result[0]["신청상태"] == "결제완료"

def test_parse_applicant_all_statuses_included():
    """신청상태와 무관하게 모든 신청자 포함 (관리자가 별도 확인)"""
    rows = [
        {"신청자": "A(a1)", "연락처": "010-0000-0001", "신청상태": "결제완료"},
        {"신청자": "B(b1)", "연락처": "010-0000-0002", "신청상태": "취소"},
        {"신청자": "C(c1)", "연락처": "010-0000-0003", "신청상태": "환불완료"},
    ]
    data = make_applicant_html(rows)
    result = parse_applicant_list(data)
    assert len(result) == 3

def test_parse_applicant_empty_table():
    html = b"<html><body><table><tr><th>empty</th></tr></table></body></html>"
    result = parse_applicant_list(html)
    assert result == []

def test_parse_applicant_name_id_uses_phone_suffix():
    """이름ID = 이름 + 전화번호 뒤 4자리"""
    data = make_applicant_html([{
        "신청자": "김테스트(kt99)",
        "연락처": "010-5555-1234",
        "신청상태": "결제완료",
    }])
    result = parse_applicant_list(data)
    assert result[0]["이름ID"] == "김테스트1234"

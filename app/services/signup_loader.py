"""Drive에서 신규가입/정회원가입 신청서를 로드하여 파싱 결과를 반환하는 서비스.

Drive 폴더 탐색 → Sheets 읽기 → 이름ID 생성 → dict 리스트 반환.
DB에는 쓰지 않음 — 호출자(payment flow)가 통합 신청서 Sheets에 기록.
"""

import re

from app.config import MEMBER_SIGNUP_FOLDER_ID, FULLMEMBER_SIGNUP_FOLDER_ID
from app.services.google_drive import list_files
from app.services.google_sheets import read_sheet


def _find_signup_sheet(folder_id: str, term_id: str) -> dict | None:
    """폴더 내에서 파일명에 회차 문자열(예: "2026-2")이 포함된 Spreadsheet를 찾는다."""
    files = list_files(
        folder_id,
        mime_type="application/vnd.google-apps.spreadsheet",
    )
    for f in files:
        if term_id in f["name"]:
            return f
    return None


def _extract_phone_digits(raw: str) -> str:
    """전화번호에서 숫자만 추출"""
    return re.sub(r"[^0-9]", "", raw)


def _calc_term(year: int, month: int) -> tuple[int, str]:
    """날짜 기반 회차 번호 + term_id 계산"""
    if month <= 3:
        term_num = 1
    elif month <= 6:
        term_num = 2
    elif month <= 9:
        term_num = 3
    else:
        term_num = 4
    return term_num, f"{year}-{term_num}"


def _read_sheet_flexible(sheet_id: str) -> list[list[str]]:
    """시트 읽기 — '시트1' 탭명 시도, 실패 시 범위 없이 재시도"""
    try:
        return read_sheet(sheet_id, "시트1!A1:Z5000")
    except Exception:
        try:
            return read_sheet(sheet_id, "A1:Z5000")
        except Exception:
            return []


def load_member_signups_from_drive(term_id: str) -> dict:
    """Drive에서 회원 가입 신청서를 찾아 파싱 결과를 반환.

    Returns: {"found": bool, "file_name": str|None, "count": int,
              "records": list[dict], "error": str|None}
    """
    sheet_file = _find_signup_sheet(MEMBER_SIGNUP_FOLDER_ID, term_id)
    if not sheet_file:
        return {
            "found": False,
            "file_name": None,
            "count": 0,
            "records": [],
            "error": f"신규가입 신청서 폴더에서 '{term_id}' 파일을 찾을 수 없습니다.",
        }

    rows = _read_sheet_flexible(sheet_file["id"])
    if not rows or len(rows) < 2:
        return {
            "found": True,
            "file_name": sheet_file["name"],
            "count": 0,
            "records": [],
            "error": "시트에 데이터가 없습니다.",
        }

    header = rows[0]
    records = []
    for row in rows[1:]:
        data = dict(zip(header, row + [""] * (len(header) - len(row))))

        # ⚠️ 아래 키 이름은 구글 설문 응답 시트의 실제 열 헤더로 변경하세요
        name = (data.get("이름") or "").strip()
        phone = _extract_phone_digits(
            data.get("전화번호") or data.get("연락처") or ""
        )
        if not name or len(phone) < 4:
            continue

        name_id = name + phone[-4:]
        signup_date = (data.get("타임스탬프") or "")[:10] or ""

        from datetime import date
        today = date.today()
        _, join_term = _calc_term(today.year, today.month)

        records.append({
            "이름ID": name_id,
            "이름": name,
            "유형": "신규가입",
            "과목명": "",
            "전화번호": phone,
            "주소": (data.get("주소") or "").strip(),
            "생년월일": (data.get("생년월일") or "").strip(),
            "성별": (data.get("성별") or "").strip(),
            "신청일": signup_date,
            "시작회차": "",
            "종료회차": "",
        })

    return {
        "found": True,
        "file_name": sheet_file["name"],
        "count": len(records),
        "records": records,
        "error": None,
    }


def load_fullmember_signups_from_drive(term_id: str) -> dict:
    """Drive에서 정회원 가입 신청서를 찾아 파싱 결과를 반환.

    Returns: {"found": bool, "file_name": str|None, "count": int,
              "records": list[dict], "error": str|None}
    """
    sheet_file = _find_signup_sheet(FULLMEMBER_SIGNUP_FOLDER_ID, term_id)
    if not sheet_file:
        return {
            "found": False,
            "file_name": None,
            "count": 0,
            "records": [],
            "error": f"정회원가입 신청서 폴더에서 '{term_id}' 파일을 찾을 수 없습니다.",
        }

    rows = _read_sheet_flexible(sheet_file["id"])
    if not rows or len(rows) < 2:
        return {
            "found": True,
            "file_name": sheet_file["name"],
            "count": 0,
            "records": [],
            "error": "시트에 데이터가 없습니다.",
        }

    header = rows[0]
    records = []
    for row in rows[1:]:
        data = dict(zip(header, row + [""] * (len(header) - len(row))))

        name = (data.get("이름") or "").strip()
        phone = _extract_phone_digits(
            data.get("전화번호") or data.get("연락처") or ""
        )
        if not name or len(phone) < 4:
            continue

        name_id = name + phone[-4:]
        signup_date = (data.get("타임스탬프") or "")[:10] or ""

        from datetime import date
        today = date.today()
        year = today.year
        term_num, join_term = _calc_term(year, today.month)

        start_term = join_term
        # 정회원 사이클: yy-2(봄) ~ (yy+1)-1(겨울)
        if term_num == 1:
            end_term = f"{year}-1"
        else:
            end_term = f"{year + 1}-1"

        records.append({
            "이름ID": name_id,
            "이름": name,
            "유형": "정회원",
            "과목명": "",
            "전화번호": phone,
            "주소": (data.get("주소") or "").strip(),
            "생년월일": (data.get("생년월일") or "").strip(),
            "성별": "",
            "신청일": signup_date,
            "시작회차": start_term,
            "종료회차": end_term,
        })

    return {
        "found": True,
        "file_name": sheet_file["name"],
        "count": len(records),
        "records": records,
        "error": None,
    }

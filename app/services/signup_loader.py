"""Drive에서 신규가입/정회원가입 신청서를 로드하여 파싱 결과를 반환하는 서비스.

Drive 폴더 탐색 → Sheets 읽기 → 이름ID 생성 → dict 리스트 반환.
DB에는 쓰지 않음 — 호출자(payment flow)가 통합 신청서 Sheets에 기록.

헤더 매핑: 구글폼 응답 시트의 헤더가 매년 달라질 수 있으므로
키워드 부분 일치(_get_value_by_keyword)로 필수 컬럼을 추출한다.
"""

from app.utils.normalize import normalize_name, normalize_phone, make_name_id
from app.config import (
    MEMBER_SIGNUP_FOLDER_ID,
    FULLMEMBER_SIGNUP_FOLDER_ID,
    SIGNUP_SHEET_TAB,
)
from app.services.google_drive import list_files
from app.services.google_sheets import read_sheet


def _find_signup_sheet(folder_id: str, year: str) -> dict | None:
    """폴더 내에서 파일명에 연도 문자열(예: "2026")이 포함된 Spreadsheet를 찾는다.

    신규가입/정회원 신청서는 연도별로 관리되므로 연도 문자열로 탐색.
    """
    files = list_files(
        folder_id,
        mime_type="application/vnd.google-apps.spreadsheet",
    )
    for f in files:
        if year in f["name"]:
            return f
    return None


def _get_value_by_keyword(data: dict, keywords: list[str]) -> str:
    """헤더 키에 keyword가 포함된 컬럼 값을 반환.

    헤더가 "1.이름", "3. 연락처 (01023456789)" 처럼 번호+설명 형태이거나
    매년 변경될 수 있으므로 정확한 키 매칭 대신 부분 일치를 사용한다.
    여러 keyword 중 하나라도 포함되면 해당 컬럼의 첫 번째 매칭 값을 반환.
    """
    for key in data:
        for kw in keywords:
            if kw in key:
                return (data[key] or "").strip()
    return ""


def _read_sheet_flexible(sheet_id: str) -> list[list[str]]:
    """시트 읽기 — 구글폼 기본 탭명 우선 시도"""
    for tab in [SIGNUP_SHEET_TAB, "시트1", "Sheet1"]:
        try:
            rows = read_sheet(sheet_id, f"{tab}!A1:Z5000")
            if rows:
                return rows
        except Exception:
            continue
    return []


def load_member_signups_from_drive(year: str) -> dict:
    """Drive에서 신규가입 신청서를 찾아 파싱 결과를 반환.

    탐색 기준: 폴더 내 파일명에 year (예: "2026") 포함 여부.
    탭명: "Form Responses 1" 우선 시도.
    헤더 매핑: 키워드 부분 일치 (헤더가 매년 바뀔 수 있음).
    필수 컬럼: 이름, 연락처, 주소, Timestamp.

    Returns: {"found": bool, "file_name": str|None, "count": int,
              "records": list[dict], "error": str|None}
    """
    sheet_file = _find_signup_sheet(MEMBER_SIGNUP_FOLDER_ID, year)
    if not sheet_file:
        return {
            "found": False,
            "file_name": None,
            "count": 0,
            "records": [],
            "error": f"신규가입 신청서 폴더에서 '{year}년' 파일을 찾을 수 없습니다.",
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

        name_raw = _get_value_by_keyword(data, ["이름", "성함"])
        phone = normalize_phone(
            _get_value_by_keyword(data, ["연락처", "전화번호"])
        )
        name = normalize_name(name_raw)
        address = _get_value_by_keyword(data, ["주소", "거주"])
        signup_date = _get_value_by_keyword(data, ["Timestamp", "타임스탬프"])[:10]

        if not name or len(phone) < 4:
            continue

        name_id = make_name_id(name_raw, phone)

        records.append({
            "이름ID": name_id,
            "이름": name,
            "유형": "신규가입",
            "과목명": "",
            "전화번호": phone,
            "주소": address,
            "신청일": signup_date,
        })

    return {
        "found": True,
        "file_name": sheet_file["name"],
        "count": len(records),
        "records": records,
        "error": None,
    }


def load_fullmember_signups_from_drive(year: str) -> dict:
    """Drive에서 정회원가입 신청서를 찾아 파싱 결과를 반환.

    탐색 기준: 폴더 내 파일명에 year (예: "2026") 포함 여부.
    정회원 가입은 2~4학기(3월 중순~9월 말)에만 가능.

    Returns: {"found": bool, "file_name": str|None, "count": int,
              "records": list[dict], "error": str|None}
    """
    sheet_file = _find_signup_sheet(FULLMEMBER_SIGNUP_FOLDER_ID, year)
    if not sheet_file:
        return {
            "found": False,
            "file_name": None,
            "count": 0,
            "records": [],
            "error": f"정회원가입 신청서 폴더에서 '{year}년' 파일을 찾을 수 없습니다.",
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

        name_raw = _get_value_by_keyword(data, ["이름", "성함"])
        phone = normalize_phone(
            _get_value_by_keyword(data, ["연락처", "전화번호"])
        )
        name = normalize_name(name_raw)
        address = _get_value_by_keyword(data, ["주소", "거주"])
        signup_date = _get_value_by_keyword(data, ["Timestamp", "타임스탬프"])[:10]

        if not name or len(phone) < 4:
            continue

        name_id = make_name_id(name_raw, phone)

        records.append({
            "이름ID": name_id,
            "이름": name,
            "유형": "정회원",
            "과목명": "",
            "전화번호": phone,
            "주소": address,
            "신청일": signup_date,
        })

    return {
        "found": True,
        "file_name": sheet_file["name"],
        "count": len(records),
        "records": records,
        "error": None,
    }

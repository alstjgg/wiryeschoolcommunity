"""입금 대조 파이프라인 — DB SoT + n8n Sheets 동기화

USE_DB_SOT=true: PostgreSQL이 SoT. Sheets는 n8n webhook으로 동기화 (직접 쓰기 없음).
USE_DB_SOT=false: 기존 Sheets SoT 동작 유지 (폴백).
"""

import json
import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import (
    ANTHROPIC_API_KEY, LLM_MODEL, USE_DB_SOT,
    MEMBERS_SHEET_ID, COURSE_KEYWORDS,
    TUITION_FEE, MEMBERSHIP_FEE, FULL_MEMBERSHIP_FEE,
    MEMBERS_TAB, MEMBER_RECORDS_TAB, COURSE_RECORDS_TAB, APPLICATIONS_TAB,
    MEMBER_RECORD_HEADER, COURSE_RECORD_HEADER,
    INSTRUCTOR_SHEET_ID, STAFF_SHEET_ID,
)
from app.services.google_auth import get_drive_service, get_sheets_service
from app.services.google_drive import find_spreadsheet_by_name, find_or_create_folder
from app.services.google_sheets import read_sheet, write_sheet, append_sheet

logger = logging.getLogger(__name__)


# =========================================== 통합 신청서 시트 관리 ====

APPLICATION_HEADER = [
    "신청일", "회차", "이름ID", "이름", "유형", "과목명",
    "예상금액", "입금시간", "입금자명(적요)", "입금현황",
    "확인사유", "처리상태",
]

# 컬럼 인덱스 (0-based)
_COL = {name: i for i, name in enumerate(APPLICATION_HEADER)}


def _app_to_row(app: dict) -> list[str]:
    """신청서 dict → Sheets 행"""
    return [str(app.get(col, "") or "") for col in APPLICATION_HEADER]


def build_applications(
    applicants: list[dict],
    member_signups: list[dict],
    fullmember_signups: list[dict],
    term_id: str = "",
) -> list[dict]:
    """수강 신청 + 신규가입 + 정회원가입을 통합 신청서 리스트로 합친다.

    중복 제거: 이름ID + 유형 + 과목명 기준.
    """
    seen = set()
    apps = []

    for a in applicants:
        key = (a["이름ID"], "수강", a["강좌명"])
        if key in seen:
            continue
        seen.add(key)
        apps.append({
            "신청일": a.get("신청일", ""),
            "회차": term_id,
            "이름ID": a["이름ID"],
            "이름": a["이름"],
            "유형": "수강",
            "과목명": a["강좌명"],
            "예상금액": str(TUITION_FEE),
            "입금시간": "",
            "입금자명(적요)": "",
            "입금현황": "❌미입금",
            "확인사유": "",
            "처리상태": "",
            "전화번호": a.get("전화번호", ""),
            "주소": a.get("주소", ""),
        })

    for s in member_signups:
        key = (s["이름ID"], "신규가입", "")
        if key in seen:
            continue
        seen.add(key)
        apps.append({
            **s,
            "회차": term_id,
            "예상금액": str(MEMBERSHIP_FEE),
            "입금현황": "❌미입금",
            "확인사유": "",
            "처리상태": "",
            "입금시간": "",
            "입금자명(적요)": "",
        })

    for f in fullmember_signups:
        key = (f["이름ID"], "정회원", "")
        if key in seen:
            continue
        seen.add(key)
        apps.append({
            **f,
            "회차": term_id,
            "예상금액": str(FULL_MEMBERSHIP_FEE),
            "입금현황": "❌미입금",
            "확인사유": "",
            "처리상태": "",
            "입금시간": "",
            "입금자명(적요)": "",
        })

    return apps


# ============================================ Sheets 전용 헬퍼 (내부) ====

def _write_applications_to_sheets(
    term_folder_id: str,
    applications: list[dict],
) -> str:
    """Sheets에 통합 신청서 upsert. Returns spreadsheet_id."""
    subfolder = find_or_create_folder(term_folder_id, "신청서")
    folder_id = subfolder["id"]
    existing_file = find_spreadsheet_by_name(folder_id, "신청서")

    if existing_file:
        spreadsheet_id = existing_file["id"]
        existing_apps = _read_applications_from_sheets(spreadsheet_id)
        existing_keys = {
            (a["이름ID"], a["유형"], a.get("과목명", ""))
            for a in existing_apps
        }
        new_apps = [
            a for a in applications
            if (a["이름ID"], a["유형"], a.get("과목명", "")) not in existing_keys
        ]
        merged = existing_apps + new_apps
        rows = [APPLICATION_HEADER] + [_app_to_row(a) for a in merged]
        write_sheet(spreadsheet_id, "신청서!A1", rows)
    else:
        drive = get_drive_service()
        file_metadata = {
            "name": "신청서",
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "parents": [folder_id],
        }
        file = drive.files().create(
            body=file_metadata, fields="id", supportsAllDrives=True
        ).execute()
        spreadsheet_id = file["id"]

        sheets_svc = get_sheets_service()
        meta = sheets_svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        default_sheet_id = meta["sheets"][0]["properties"]["sheetId"]
        sheets_svc.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [{
                    "updateSheetProperties": {
                        "properties": {"sheetId": default_sheet_id, "title": "신청서"},
                        "fields": "title",
                    }
                }]
            },
        ).execute()

        rows = [APPLICATION_HEADER] + [_app_to_row(a) for a in applications]
        write_sheet(spreadsheet_id, "신청서!A1", rows)
        merged = applications

    # 필터 + 처리상태 드롭다운 설정
    sheets_svc = get_sheets_service()
    meta = sheets_svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_id = next(
        s["properties"]["sheetId"]
        for s in meta["sheets"]
        if s["properties"]["title"] == "신청서"
    )

    requests = [
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(APPLICATION_HEADER),
                    }
                }
            }
        },
    ]

    # 처리상태 드롭다운 (등록완료/환불완료/취소완료/보류)
    if "처리상태" in _COL:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": 1 + len(merged),
                    "startColumnIndex": _COL["처리상태"],
                    "endColumnIndex": _COL["처리상태"] + 1,
                },
                "cell": {
                    "dataValidation": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [
                                {"userEnteredValue": "등록완료"},
                                {"userEnteredValue": "환불완료"},
                                {"userEnteredValue": "취소완료"},
                                {"userEnteredValue": "보류"},
                            ],
                        },
                        "showCustomUi": True,
                        "strict": False,
                    }
                },
                "fields": "dataValidation",
            }
        })

    sheets_svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()

    return spreadsheet_id


def _read_applications_from_sheets(spreadsheet_id: str, tab_name: str = "신청서") -> list[dict]:
    """Sheets에서 신청서 읽기."""
    rows = read_sheet(spreadsheet_id, f"{tab_name}!A1:L5000")
    if not rows or len(rows) < 2:
        return []
    header = rows[0]
    return [
        dict(zip(header, row + [""] * (len(header) - len(row))))
        for row in rows[1:]
    ]


def _update_applications_in_sheets(
    spreadsheet_id: str,
    applications: list[dict],
    tab_name: str = "신청서",
) -> None:
    """Sheets에 매칭 결과 덮어쓰기."""
    rows = [APPLICATION_HEADER] + [_app_to_row(a) for a in applications]
    write_sheet(spreadsheet_id, f"{tab_name}!A1", rows)


def _load_members_from_sheets() -> list[dict]:
    """Sheets에서 회원목록 로드."""
    rows = read_sheet(MEMBERS_SHEET_ID, f"{MEMBERS_TAB}!A1:I2000")
    if not rows or len(rows) < 2:
        return []
    header = rows[0]
    return [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in rows[1:]]


def _update_members_in_sheets(members: list[dict]) -> None:
    """Sheets에 회원목록 덮어쓰기."""
    header = [
        "이름ID", "이름", "전화번호", "주소", "등급", "예외여부",
        "수강count", "출석률(누적)", "마지막수강회차",
    ]
    rows = [header]
    for m in members:
        rows.append([
            m.get("이름ID", ""),
            m.get("이름", ""),
            m.get("전화번호", ""),
            m.get("주소", ""),
            m.get("등급", "회원"),
            m.get("예외여부", ""),
            m.get("수강count", "0"),
            m.get("출석률(누적)", ""),
            m.get("마지막수강회차", ""),
        ])
    write_sheet(MEMBERS_SHEET_ID, f"{MEMBERS_TAB}!A1", rows)


def _append_member_records_to_sheets(records: list[dict]) -> None:
    """Sheets에 회원기록 append."""
    if not records:
        return
    rows = [
        [r.get(col, "") for col in MEMBER_RECORD_HEADER]
        for r in records
    ]
    append_sheet(MEMBERS_SHEET_ID, f"{MEMBER_RECORDS_TAB}!A1", rows)


def _append_course_records_to_sheets(records: list[dict]) -> None:
    """Sheets에 수강기록 append."""
    if not records:
        return
    rows = [
        [r.get(col, "") for col in COURSE_RECORD_HEADER]
        for r in records
    ]
    append_sheet(MEMBERS_SHEET_ID, f"{COURSE_RECORDS_TAB}!A1", rows)


# ======================================= 강사/사무처 면제 대상 판별 ====

def get_cycle_year(term_id: str) -> int:
    """회차 → 사이클 연도. 사이클 = YY-2(봄) ~ YY+1-1(다음해 겨울)."""
    year, num = int(term_id.split("-")[0]), int(term_id.split("-")[1])
    return year - 1 if num == 1 else year


def _parse_ym_to_cycle_year(ym: str) -> int | None:
    """'YY.MM' 또는 'YYYY.MM' → 소속 사이클 연도.

    예: '25.03' → 2025-1 → 사이클 2024
        '25.05' → 2025-2 → 사이클 2025
    """
    try:
        parts = ym.split(".")
        year = int(parts[0])
        month = int(parts[1])
        if year < 100:
            year += 2000
        term_num = (month - 1) // 3 + 1
        return year - 1 if term_num == 1 else year
    except (ValueError, IndexError):
        return None


def get_exception_ids(term_id: str) -> set[str]:
    """강사관리 + 사무처관리 시트에서 현재 사이클 면제 대상 이름ID 추출.

    입금 대조 시 1회만 호출. SoT가 Sheet이므로 DB에 저장하지 않음.
    """
    exception_ids: set[str] = set()
    cycle_year = get_cycle_year(term_id)
    cycle_terms = {
        f"{cycle_year}-2", f"{cycle_year}-3",
        f"{cycle_year}-4", f"{cycle_year + 1}-1",
    }

    # 강사: 현재 사이클에 강의 row가 있는 강사
    try:
        rows = read_sheet(INSTRUCTOR_SHEET_ID, "Sheet1!A1:F1000")
        if rows and len(rows) >= 2:
            header = rows[0]
            for row in rows[1:]:
                data = dict(zip(header, row + [""] * (len(header) - len(row))))
                if data.get("강의회차", "").strip() in cycle_terms:
                    name_id = data.get("이름ID", "").strip()
                    if name_id:
                        exception_ids.add(name_id)
    except Exception as e:
        logger.warning("강사관리 시트 읽기 실패: %s", e)

    # 직원: 활동종료가 비어있거나 종료 시점이 현재 사이클 이후
    try:
        rows = read_sheet(STAFF_SHEET_ID, "Sheet1!A1:G100")
        if rows and len(rows) >= 2:
            header = rows[0]
            for row in rows[1:]:
                data = dict(zip(header, row + [""] * (len(header) - len(row))))
                name_id = data.get("이름ID", "").strip()
                if not name_id:
                    continue
                end = data.get("활동종료", "").strip()
                if not end:
                    exception_ids.add(name_id)
                else:
                    end_cycle = _parse_ym_to_cycle_year(end)
                    if end_cycle is not None and end_cycle >= cycle_year:
                        exception_ids.add(name_id)
    except Exception as e:
        logger.warning("사무처관리 시트 읽기 실패: %s", e)

    return exception_ids


def get_active_staff_ids() -> set[str]:
    """사무처관리 시트에서 활동 중인 직원 이름ID 추출 (종강 처리용)."""
    active: set[str] = set()
    try:
        rows = read_sheet(STAFF_SHEET_ID, "Sheet1!A1:G100")
        if rows and len(rows) >= 2:
            header = rows[0]
            for row in rows[1:]:
                data = dict(zip(header, row + [""] * (len(header) - len(row))))
                if not data.get("활동종료", "").strip():
                    name_id = data.get("이름ID", "").strip()
                    if name_id:
                        active.add(name_id)
    except Exception as e:
        logger.warning("사무처관리 시트 읽기 실패: %s", e)
    return active


# ============================================ Grade Cascade (등급 전환) ====

def apply_grade_cascade(
    applications: list[dict],
    members: list[dict],
    term_id: str = "",
    exception_ids: set[str] | None = None,
) -> list[dict]:
    """확정된 입금 건에 대해 등급 전환을 순서대로 적용. Idempotent.

    호출할 때마다 전체를 재평가. 이미 처리된 건은 skip.

    3-Pass 순서:
      Pass 1: 신규가입 confirmed/면제 → 비회원을 회원으로 등록
      Pass 2: 정회원 confirmed/면제 → 회원을 정회원으로 승급
      Pass 3: 수강 → 정회원이면 면제, 아니면 준회원 승급

    exception_ids: 강사/사무처 면제 대상 이름ID set. 해당 대상은 가입비+정회원비+수강비 전부 면제.

    Returns: 등급 변경 기록 리스트 (회원기록 탭에 append할 데이터)
    """
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    if exception_ids is None:
        exception_ids = set()

    # 이름ID → member dict 매핑
    member_map: dict[str, dict] = {m["이름ID"]: m for m in members}
    changes: list[dict] = []

    # Pass 1: 신규가입 → 회원 등록
    for app in applications:
        if app.get("유형") != "신규가입":
            continue
        name_id = app["이름ID"]
        is_exc = name_id in exception_ids

        if is_exc:
            app["입금현황"] = "💎면제"
            app["확인사유"] = "강사/사무처 면제"
        elif app.get("입금현황") != "✅정상":
            continue

        if name_id in member_map:
            if is_exc:
                member_map[name_id]["예외여부"] = "TRUE"
            continue  # 이미 회원

        # 비회원 → 회원 등록
        new_member = {
            "이름ID": name_id,
            "이름": app["이름"],
            "전화번호": app.get("전화번호", ""),
            "주소": app.get("주소", ""),
            "등급": "회원",
            "예외여부": "TRUE" if is_exc else "",
            "수강count": "0",
            "출석률(누적)": "",
            "마지막수강회차": "",
        }
        member_map[name_id] = new_member
        members.append(new_member)
        changes.append({
            "이름ID": name_id, "이름": app["이름"],
            "변경일시": now_str,
            "변경전등급": "(신규)", "변경후등급": "회원",
            "사유": "신규가입(강사/사무처면제)" if is_exc else "신규가입",
            "관련회차": term_id,
        })

    # Pass 2: 정회원 → 정회원 승급
    for app in applications:
        if app.get("유형") != "정회원":
            continue
        name_id = app["이름ID"]
        is_exc = name_id in exception_ids

        if is_exc:
            app["입금현황"] = "💎면제"
            app["확인사유"] = "강사/사무처 면제"
        elif app.get("입금현황") != "✅정상":
            continue

        member = member_map.get(name_id)

        if not member:
            # 회원이 아님 — 신규가입 먼저 필요
            if not is_exc:
                app["입금현황"] = "🔶확인필요"
                app["확인사유"] = "회원 아님 — 신규가입 먼저 필요"
            continue

        if member.get("등급") == "정회원":
            if is_exc:
                member["예외여부"] = "TRUE"
            continue  # 이미 정회원

        prev_grade = member.get("등급", "회원")
        member["등급"] = "정회원"
        if is_exc:
            member["예외여부"] = "TRUE"
        changes.append({
            "이름ID": name_id, "이름": app["이름"],
            "변경일시": now_str,
            "변경전등급": prev_grade, "변경후등급": "정회원",
            "사유": "정회원비면제(강사/사무처)" if is_exc else "정회원비입금",
            "관련회차": term_id,
        })

    # Pass 3: 수강 → 면제 or 준회원 승급
    for app in applications:
        if app.get("유형") != "수강":
            continue
        name_id = app["이름ID"]
        member = member_map.get(name_id)

        # 정회원이면 수강료 면제 (기존 ✅정상도 💎면제로 변환)
        if member and member.get("등급") == "정회원":
            if app.get("입금현황") != "💎면제":
                app["입금현황"] = "💎면제"
            continue

        # 정회원 신청 중 + 정회원비 미입금 → 보류
        has_pending_full = any(
            a.get("유형") == "정회원"
            and a.get("이름ID") == name_id
            and a.get("입금현황") not in ("✅정상", "💎면제")
            for a in applications
        )
        if has_pending_full and app.get("입금현황") == "✅정상":
            app["입금현황"] = "🔶확인필요"
            app["확인사유"] = "정회원 신청 중 — 수강비만 입금됨"
            continue

        # 일반 수강료 매칭 확정 → 준회원 승급
        if app.get("입금현황") == "✅정상" and member:
            current_grade = member.get("등급", "")
            if current_grade not in ("준회원", "정회원"):
                member["등급"] = "준회원"
                changes.append({
                    "이름ID": name_id, "이름": app["이름"],
                    "변경일시": now_str,
                    "변경전등급": current_grade or "회원",
                    "변경후등급": "준회원",
                    "사유": "수강료입금", "관련회차": term_id,
                })

    return changes


# ==================================== 공개 API (DB/Sheets dual-write) ====

async def write_applications_sheet(
    term_folder_id: str,
    applications: list[dict],
    term_id: str = "",
) -> str:
    """통합 신청서 upsert.

    DB 모드: DB에 upsert + n8n webhook → 회원관리 파일 신청기록 탭에 동기화. Returns MEMBERS_SHEET_ID.
    Sheets 모드: 회차별 신청서 파일에 write. Returns spreadsheet_id.
    """
    if USE_DB_SOT and term_id:
        from app.services import db
        from app.services.n8n import trigger_sheets_sync
        try:
            await db.upsert_applications(term_id, applications)
            await trigger_sheets_sync("applications", {"term_id": term_id})
        except Exception as e:
            logger.error("DB write failed, falling back to Sheets: %s", e)
            return _write_applications_to_sheets(term_folder_id, applications)
        return MEMBERS_SHEET_ID

    return _write_applications_to_sheets(term_folder_id, applications)


async def read_applications_sheet(
    spreadsheet_id: str,
    term_id: str = "",
) -> list[dict]:
    """신청서 읽기.

    DB 모드: DB에서 읽기 (빠름).
    Sheets 모드: Sheets에서 읽기.
    """
    if USE_DB_SOT and term_id:
        from app.services import db
        try:
            return await db.load_applications(term_id)
        except Exception as e:
            logger.error("DB read failed, falling back to Sheets: %s", e)
            return _read_applications_from_sheets(spreadsheet_id, tab_name=APPLICATIONS_TAB)

    return _read_applications_from_sheets(spreadsheet_id)


async def update_applications_sheet(
    spreadsheet_id: str,
    applications: list[dict],
    term_id: str = "",
) -> None:
    """매칭 결과 반영.

    DB 모드: DB에 upsert + n8n webhook (Sheets 직접 쓰기 없음).
    Sheets 모드: Sheets에 직접 write.
    """
    if USE_DB_SOT and term_id:
        from app.services import db
        from app.services.n8n import trigger_sheets_sync
        try:
            await db.upsert_applications(term_id, applications)
            await trigger_sheets_sync("applications", {"term_id": term_id})
            return
        except Exception as e:
            logger.error("DB write failed, falling back to Sheets: %s", e)
            _update_applications_in_sheets(spreadsheet_id, applications, tab_name=APPLICATIONS_TAB)
            return

    _update_applications_in_sheets(spreadsheet_id, applications)


async def load_members_from_sheet() -> list[dict]:
    """회원목록 로드.

    DB 모드: DB에서 읽기.
    Sheets 모드: Sheets에서 읽기.
    """
    if USE_DB_SOT:
        from app.services import db
        try:
            return await db.load_members()
        except Exception as e:
            logger.error("DB read failed, falling back to Sheets: %s", e)

    return _load_members_from_sheets()


async def update_members_sheet(members: list[dict]) -> None:
    """회원목록 덮어쓰기.

    DB 모드: DB에 upsert + n8n webhook (Sheets 직접 쓰기 없음).
    Sheets 모드: Sheets에 직접 write.
    """
    if USE_DB_SOT:
        from app.services import db
        from app.services.n8n import trigger_sheets_sync
        try:
            await db.upsert_members(members)
            await trigger_sheets_sync("members")
            return
        except Exception as e:
            logger.error("DB write failed, falling back to Sheets: %s", e)

    _update_members_in_sheets(members)


async def append_member_records(records: list[dict]) -> None:
    """회원기록 등급 변경 이력 append.

    DB 모드: DB에 insert + n8n webhook (Sheets 직접 쓰기 없음).
    Sheets 모드: Sheets에 직접 append.
    """
    if not records:
        return

    if USE_DB_SOT:
        from app.services import db
        from app.services.n8n import trigger_sheets_sync
        try:
            await db.insert_member_records(records)
            await trigger_sheets_sync("members")
            return
        except Exception as e:
            logger.error("DB write failed, falling back to Sheets: %s", e)

    _append_member_records_to_sheets(records)


async def append_course_records(records: list[dict]) -> None:
    """수강기록 수강 이력 append.

    DB 모드: DB에 insert + n8n webhook (Sheets 직접 쓰기 없음).
    Sheets 모드: Sheets에 직접 append.
    """
    if not records:
        return

    if USE_DB_SOT:
        from app.services import db
        from app.services.n8n import trigger_sheets_sync
        try:
            await db.insert_course_records(records)
            await trigger_sheets_sync("graduation")
            return
        except Exception as e:
            logger.error("DB write failed, falling back to Sheets: %s", e)

    _append_course_records_to_sheets(records)


# ======================================= 입금 매칭 관련 함수 ====

def apply_exemptions(
    applications: list[dict],
    members: list[dict],
    exception_ids: set[str] | None = None,
) -> list[dict]:
    """면제 선처리.

    1. 기존 정회원 → 수강 행 💎면제
    2. 강사/사무처 면제 대상 → 모든 유형(신규가입/정회원/수강) 💎면제
       (cascade에서 등급 승급은 별도 처리)
    """
    if exception_ids is None:
        exception_ids = set()
    member_grades = {m.get("이름ID", ""): m.get("등급", "") for m in members}
    exempted = []
    for app in applications:
        name_id = app["이름ID"]
        is_exc = name_id in exception_ids

        if is_exc:
            # 강사/사무처 면제 — 모든 유형 (매칭 전에 💎면제 설정)
            app["입금현황"] = "💎면제"
            app["확인사유"] = "강사/사무처 면제"
            exempted.append(app)
        elif app["유형"] == "수강" and member_grades.get(name_id) == "정회원":
            # 기존 정회원 수강료 면제
            app["입금현황"] = "💎면제"
            exempted.append(app)
    return exempted


def applications_to_students(applications: list[dict]) -> list[dict]:
    """통합 신청서에서 수강 유형만 추출하여 matching.py 호환 형식으로 변환"""
    return [
        {
            "이름ID": a["이름ID"],
            "이름": a["이름"],
            "강좌명": a["과목명"],
        }
        for a in applications
        if a["유형"] == "수강"
    ]


def apply_matching_results(
    applications: list[dict],
    matched_results: list[dict],
) -> int:
    """매칭 결과를 applications에 반영 (입금현황, 입금시간, 입금자명).

    각 matched_result에 deposit 추적 메타데이터를 태그:
    - _matched = True/False (매칭 성공 여부)
    - _matched_name_ids = [name_id] (매칭된 이름ID 목록)

    Returns: 매칭되지 않은 deposit 수 (스킵 제외)
    """
    # 이름ID + 과목명 → application 인덱스 매핑
    app_index = {}
    for i, app in enumerate(applications):
        if app["유형"] == "수강":
            app_index[(app["이름ID"], app["과목명"])] = i

    unmatched_count = 0
    for r in matched_results:
        if r["상태"] == "⏭️스킵":
            continue
        matched_id = r.get("매칭ID")
        matched_course = r.get("매칭강좌", "")
        if not matched_id:
            r["_matched"] = False
            unmatched_count += 1
            continue

        key = (matched_id, matched_course)
        if key not in app_index:
            # 강좌 없이 이름ID만으로 시도
            for k, idx in app_index.items():
                if k[0] == matched_id and applications[idx]["입금현황"] == "❌미입금":
                    key = k
                    break

        if key in app_index:
            idx = app_index[key]
            applications[idx]["입금현황"] = r["상태"]
            applications[idx]["입금시간"] = r.get("거래일시", "")
            applications[idx]["입금자명(적요)"] = r.get("적요", "")
            r["_matched"] = True
            r["_matched_name_ids"] = [applications[idx].get("이름ID", "")]
        else:
            r["_matched"] = False
            unmatched_count += 1

    return unmatched_count


async def run_llm_matching(unmatched: list[dict], students: list[dict]) -> list[dict]:
    """LLM으로 미매칭 건 처리 — 비정형 적요 텍스트 해석"""
    if not unmatched:
        return []

    llm = ChatAnthropic(
        model=LLM_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=4096,
    )

    student_info = [
        f"- {s['이름']} / {s['강좌명']} (ID: {s['이름ID']})"
        for s in students
    ]
    student_list_text = "\n".join(student_info)

    tx_list = [
        f"{i+1}. 적요: \"{tx['적요']}\" / 의뢰인: \"{tx['의뢰인']}\" / "
        f"금액: {tx['입금']:,}원"
        for i, tx in enumerate(unmatched)
    ]
    tx_text = "\n".join(tx_list)

    keyword_text = "\n".join(f"  {k} → {v}" for k, v in COURSE_KEYWORDS.items())

    system_prompt = f"""당신은 위례인생학교의 입금 대조 보조 AI입니다.
아래 미매칭 거래들을 수강생 목록과 대조하여 매칭해주세요.

## 수강생 목록
{student_list_text}

## 강좌 키워드 매핑
{keyword_text}

## 매칭 규칙
1. 적요나 의뢰인에서 학생 이름을 찾으세요.
2. 이름만으로 특정이 안 되면 강좌 힌트를 활용하세요.
3. 대리입금 패턴: "A(B강좌)" → B가 수강생, A는 대리인
4. 잘린 텍스트: "경제심" → "경제심화" 또는 "경제해설(심화)"
5. 매칭 확신이 없으면 상태를 "🔶확인필요"로 설정하세요.

## 상태 코드
- ✅정상: 확실한 매칭
- 🔶확인필요: LLM 추정, 동명이인, 금액 불일치 등
- ⚠️이름불일치: 대리 입금 추정
- 🔄중복: 중복 입금 감지

## 응답 형식
JSON 배열로 응답하세요. 각 항목:
```json
[
  {{
    "index": 1,
    "매칭이름": "학생이름" 또는 null,
    "매칭ID": "학생ID" 또는 null,
    "매칭강좌": "강좌명" 또는 null,
    "상태": "✅정상" 또는 "🔶확인필요" 또는 "⚠️이름불일치" 또는 "🔄중복",
    "메모": "판단 근거"
  }}
]
```
JSON만 응답하세요. 설명은 메모 필드에 넣어주세요."""

    user_prompt = f"다음 미매칭 거래들을 매칭해주세요:\n\n{tx_text}"

    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])

    content = response.content.strip()
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    try:
        llm_results = json.loads(content)
    except json.JSONDecodeError:
        return unmatched

    for llm_item in llm_results:
        idx = llm_item.get("index", 0) - 1
        if 0 <= idx < len(unmatched):
            tx = unmatched[idx]
            tx["매칭이름"] = llm_item.get("매칭이름") or tx.get("매칭이름")
            tx["매칭ID"] = llm_item.get("매칭ID") or tx.get("매칭ID")
            tx["매칭강좌"] = llm_item.get("매칭강좌") or tx.get("매칭강좌")
            tx["상태"] = llm_item.get("상태", "🔶확인필요")
            tx["메모"] = llm_item.get("메모", tx.get("메모", ""))

    return unmatched


def find_unpaid(applications: list[dict]) -> list[dict]:
    """입금현황이 ❌미입금인 수강 행"""
    return [
        a for a in applications
        if a["유형"] == "수강" and a["입금현황"] == "❌미입금"
    ]


def format_results(
    matched: list[dict],
    applications: list[dict],
    exempted: list[dict] | None = None,
    unmatched_deposits: int = 0,
) -> str:
    """매칭 결과를 한 줄 숫자 요약으로 포맷"""
    if exempted is None:
        exempted = []

    success = sum(1 for r in matched if r["상태"] == "✅정상")
    needs_check = sum(1 for r in matched if r["상태"] == "🔶확인필요")
    name_mismatch = sum(1 for r in matched if r["상태"] == "⚠️이름불일치")
    unpaid = find_unpaid(applications)

    parts = [f"✅ {success}건"]
    if needs_check:
        parts.append(f"🔶 {needs_check}건")
    if name_mismatch:
        parts.append(f"⚠️ {name_mismatch}건")
    if unpaid:
        parts.append(f"❌ {len(unpaid)}건")
    if exempted:
        parts.append(f"💎 {len(exempted)}건")

    summary = "  ".join(parts)
    if unmatched_deposits:
        summary += f"  | 미확인입금: {unmatched_deposits}건"
    return summary

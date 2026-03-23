"""종강 처리 파이프라인 — 출석률 집계 → 수강기록 → 회원목록 재집계 → 등급 강등

전제: 출석부 시트의 과목별 탭에 출석 체크(OCR)가 완료된 상태.

출석부 시트 구조:
  탭 수강생: 이름ID(A) | 이름(B) | 과목명(C) | 출석률(D)  ← 종강 처리 시 D열 채움
  탭 {과목}: 이름(A) | 1~12회차(B~M)
"""

import logging
from datetime import datetime

from app.config import (
    MEMBERS_SHEET_ID, MAX_SESSIONS, USE_DB_SOT,
    MEMBERS_TAB, MEMBER_RECORDS_TAB, COURSE_RECORDS_TAB,
    MEMBER_RECORD_HEADER, COURSE_RECORD_HEADER,
)
from app.services.google_sheets import read_sheet, write_sheet, append_sheet
from app.chains.payment import (
    load_members_from_sheet,
    update_members_sheet,
    append_member_records,
    append_course_records,
)

logger = logging.getLogger(__name__)


# =========================================== 출석률 집계 =====

def _load_attendance_from_sheets(spreadsheet_id: str) -> list[dict]:
    """출석부 Sheets에서 과목별 수강생 출석률을 집계."""
    from app.services.google_auth import get_sheets_service
    svc = get_sheets_service()
    meta = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()

    results = []
    col_end = chr(ord("B") + MAX_SESSIONS - 1)  # "M"

    for sheet in meta.get("sheets", []):
        course_name = sheet["properties"]["title"]
        if course_name == "수강생":
            continue

        rows = read_sheet(spreadsheet_id, f"{course_name}!A1:{col_end}500")
        if not rows or len(rows) < 2:
            continue

        for row in rows[1:]:
            padded = row + [""] * (1 + MAX_SESSIONS - len(row))
            name = padded[0]
            if not name:
                continue

            attended = sum(
                1 for i in range(1, 1 + MAX_SESSIONS) if padded[i] == "O"
            )
            total = sum(
                1 for i in range(1, 1 + MAX_SESSIONS) if padded[i] != ""
            )
            rate = round(attended / total * 100, 1) if total > 0 else 0.0

            results.append({
                "이름": name,
                "과목명": course_name,
                "출석률": str(rate),
            })

    return results


async def _load_attendance_from_db(term_id: str) -> list[dict]:
    """DB attendance 테이블에서 출석률 집계."""
    from app.services import db

    rows = await db.load_attendance(term_id)
    results = []
    for r in rows:
        session_data = r.get("session_data", {})
        if not session_data:
            rate = r.get("출석률") or 0.0
        else:
            attended = sum(1 for v in session_data.values() if v == "O")
            total = sum(1 for v in session_data.values() if v != "")
            rate = round(attended / total * 100, 1) if total > 0 else 0.0

        results.append({
            "이름": r["이름"],
            "과목명": r["과목명"],
            "출석률": str(rate),
        })
    return results


async def load_attendance_results(
    spreadsheet_id: str,
    term_id: str = "",
) -> list[dict]:
    """출석부에서 과목별 수강생 출석률을 집계.

    DB 모드: attendance 테이블의 session_data에서 O/빈칸 카운트.
    Sheets 모드: 과목별 탭에서 직접 읽기.
    """
    if USE_DB_SOT and term_id:
        try:
            return await _load_attendance_from_db(term_id)
        except Exception as e:
            logger.error("DB read failed, falling back to Sheets: %s", e)

    return _load_attendance_from_sheets(spreadsheet_id)


def _update_attendance_rates_in_sheets(
    spreadsheet_id: str,
    attendance_results: list[dict],
) -> None:
    """Sheets 수강생 탭의 출석률(D열) 업데이트."""
    rows = read_sheet(spreadsheet_id, "수강생!A1:D5000")
    if not rows or len(rows) < 2:
        return

    key_to_row: dict[tuple, int] = {}
    for i, row in enumerate(rows[1:], start=2):
        padded = row + [""] * (4 - len(row))
        key_to_row[(padded[1], padded[2])] = i

    for r in attendance_results:
        key = (r["이름"], r["과목명"])
        if key in key_to_row:
            row_idx = key_to_row[key]
            write_sheet(spreadsheet_id, f"수강생!D{row_idx}", [[r["출석률"]]])


async def update_attendance_rates_in_sheet(
    spreadsheet_id: str,
    attendance_results: list[dict],
    term_id: str,
) -> None:
    """수강생 탭의 출석률(D열)을 집계 결과로 업데이트.

    DB 모드: attendance 테이블의 attendance_rate 업데이트 + Sheets도 업데이트.
    Sheets 모드: Sheets에만 업데이트.
    """
    if USE_DB_SOT and term_id:
        from app.services import db
        try:
            for r in attendance_results:
                await db.update_attendance_rate(
                    term_id, r["과목명"], r["이름"], float(r["출석률"]),
                )
        except Exception as e:
            logger.error("DB write failed, falling back to Sheets only: %s", e)

    _update_attendance_rates_in_sheets(spreadsheet_id, attendance_results)


# ============================================= 회원목록 재집계 =====

def _recalculate_from_sheets(members: list[dict]) -> list[dict]:
    """Sheets 수강기록 탭에서 재집계."""
    rows = read_sheet(MEMBERS_SHEET_ID, f"{COURSE_RECORDS_TAB}!A1:D10000")
    if not rows or len(rows) < 2:
        return members

    header = rows[0]
    records = [
        dict(zip(header, row + [""] * (len(header) - len(row))))
        for row in rows[1:]
    ]
    return _apply_stats(members, records)


async def _recalculate_from_db(members: list[dict]) -> list[dict]:
    """DB course_records 테이블에서 재집계."""
    from app.services import db
    records = await db.load_course_records()
    return _apply_stats(members, records)


def _apply_stats(members: list[dict], records: list[dict]) -> list[dict]:
    """수강기록 리스트로 회원목록 통계를 재집계. 공통 로직."""
    stats: dict[str, dict] = {}

    for data in records:
        name_id = data.get("이름ID", "")
        if not name_id:
            continue
        if name_id not in stats:
            stats[name_id] = {"count": 0, "total_rate": 0.0, "last_term": ""}

        stats[name_id]["count"] += 1
        try:
            stats[name_id]["total_rate"] += float(data.get("출석률", 0))
        except (ValueError, TypeError):
            pass
        term = data.get("회차", "")
        if term > stats[name_id]["last_term"]:
            stats[name_id]["last_term"] = term

    for m in members:
        name_id = m.get("이름ID", "")
        if name_id in stats:
            s = stats[name_id]
            m["수강count"] = str(s["count"])
            avg = (
                round(s["total_rate"] / s["count"], 1) if s["count"] > 0 else 0.0
            )
            m["출석률(누적)"] = str(avg)
            m["마지막수강회차"] = s["last_term"]

    return members


async def recalculate_member_stats(members: list[dict]) -> list[dict]:
    """수강기록에서 회원목록의 누적 통계를 재집계.

    DB 모드: course_records 테이블에서 읽기.
    Sheets 모드: 수강기록 탭에서 읽기.
    """
    if USE_DB_SOT:
        try:
            return await _recalculate_from_db(members)
        except Exception as e:
            logger.error("DB read failed, falling back to Sheets: %s", e)

    return _recalculate_from_sheets(members)


# ================================================= 등급 강등 =====

def get_members_to_demote(
    members: list[dict],
    is_winter_term: bool,
    active_staff_ids: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """강등 대상 분류.

    준회원 → 회원: 매 종강 시
    정회원 → 회원: 1학기(겨울) 종강 시만.
      - 활동 중인 사무처 직원(active_staff_ids)은 제외
      - 강사 포함 나머지 정회원은 전원 강등

    Returns: (준회원_강등대상, 정회원_강등대상)
    """
    if active_staff_ids is None:
        active_staff_ids = set()
    junior_demote = []
    full_demote = []

    for m in members:
        grade = m.get("등급", "")
        if grade == "준회원":
            junior_demote.append(m)
        elif grade == "정회원" and is_winter_term:
            if m.get("이름ID", "") not in active_staff_ids:
                full_demote.append(m)

    return junior_demote, full_demote


# ================================================= 종강 처리 =====

async def run_graduation(
    term_id: str,
    attendance_sheet_id: str,
) -> dict:
    """종강 처리 전체 실행.

    1. 과목별 탭에서 출석률 집계
    2. 수강생 탭 출석률 업데이트
    3. 수강기록 탭에 append
    4. 회원목록 재집계
    5. 등급 강등 + 회원기록 append

    Returns: {
        "course_records_added": int,
        "junior_demoted": int,
        "full_demoted": int,
    }
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    is_winter = term_id.endswith("-1")

    # 1. 출석률 집계 (과목별 탭 O/빈칸 직접 카운트)
    attendance_results = await load_attendance_results(
        attendance_sheet_id, term_id=term_id,
    )

    # 2. 수강생 탭 출석률 컬럼 업데이트
    await update_attendance_rates_in_sheet(
        attendance_sheet_id, attendance_results, term_id,
    )

    # 3. 이름ID 조회 (수강생 탭에서 — Sheets에만 있는 매핑)
    student_rows = read_sheet(attendance_sheet_id, "수강생!A1:D5000")
    name_to_id: dict[tuple, str] = {}
    if student_rows and len(student_rows) >= 2:
        header = student_rows[0]
        for row in student_rows[1:]:
            data = dict(zip(header, row + [""] * (len(header) - len(row))))
            key = (data.get("이름", ""), data.get("과목명", ""))
            name_to_id[key] = data.get("이름ID", "")

    # 4. 수강기록 append
    course_records = []
    for r in attendance_results:
        name_id = name_to_id.get((r["이름"], r["과목명"]), "")
        course_records.append({
            "이름ID": name_id,
            "회차": term_id,
            "과목명": r["과목명"],
            "출석률": r["출석률"],
        })
    if course_records:
        await append_course_records(course_records)

    # 5. 회원목록 재집계
    members = await load_members_from_sheet()
    members = await recalculate_member_stats(members)

    # 6. 등급 강등 (활동 중 사무처 직원은 제외)
    from app.chains.payment import get_active_staff_ids
    active_staff_ids = get_active_staff_ids()
    junior_demote, full_demote = get_members_to_demote(
        members, is_winter, active_staff_ids,
    )
    change_records = []

    for m in junior_demote:
        m["등급"] = "회원"
        change_records.append({
            "이름ID": m["이름ID"], "이름": m["이름"],
            "변경일시": now_str,
            "변경전등급": "준회원", "변경후등급": "회원",
            "사유": "종강강등", "관련회차": term_id,
        })

    for m in full_demote:
        m["등급"] = "회원"
        m["예외여부"] = ""  # 강등 시 리셋 (다음 사이클 강의하면 재설정)
        change_records.append({
            "이름ID": m["이름ID"], "이름": m["이름"],
            "변경일시": now_str,
            "변경전등급": "정회원", "변경후등급": "회원",
            "사유": "겨울학기강등", "관련회차": term_id,
        })

    # 7. 회원목록 + 회원기록 저장
    await update_members_sheet(members)
    if change_records:
        await append_member_records(change_records)

    return {
        "course_records_added": len(course_records),
        "junior_demoted": len(junior_demote),
        "full_demoted": len(full_demote),
    }

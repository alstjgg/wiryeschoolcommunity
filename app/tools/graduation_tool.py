"""종강 처리 tool — 출석률 집계 → 수강기록 → 회원목록 재집계 → 등급 강등"""

import logging

import chainlit as cl
from langchain_core.tools import tool

from app.chains.graduation import (
    load_attendance_results,
    update_attendance_rates_in_sheet,
    load_student_name_id_map,
    build_course_records,
    recalculate_member_stats,
    apply_demotion,
)
from app.chains.payment import (
    load_members_from_sheet,
    update_members_sheet,
    append_member_records,
    append_course_records,
    get_active_staff_ids,
)
from app.config import MEMBERS_SHEET_ID
from app.context.term import get_current_term
from app.services.google_drive import find_term_folder, find_or_create_folder, find_spreadsheet_by_name

logger = logging.getLogger(__name__)


def _resolve_attendance_sheet_id(term: dict) -> str | None:
    """세션에서 attendance_sheet_id 조회하거나 Drive에서 탐색."""
    sheet_id = cl.user_session.get("attendance_sheet_id")
    if sheet_id:
        return sheet_id

    term_folder_id = cl.user_session.get("term_folder_id")
    if not term_folder_id:
        term_folder = find_term_folder(term["term_id"])
        if term_folder:
            term_folder_id = term_folder["id"]
            cl.user_session.set("term_folder_id", term_folder_id)

    if term_folder_id:
        att_folder = find_or_create_folder(term_folder_id, "출석부")
        att_file = find_spreadsheet_by_name(att_folder["id"], "출석부")
        if att_file:
            sheet_id = att_file["id"]
            cl.user_session.set("attendance_sheet_id", sheet_id)
            return sheet_id

    return None


@tool
async def process_graduation(term_id: str = "") -> str:
    """종강 처리를 실행합니다.

    출석부의 출석 데이터를 집계하여 출석률을 계산하고,
    수강기록을 저장하고, 회원목록 통계를 재집계하고,
    준회원/정회원 등급을 강등합니다.

    출석 체크(OCR)가 모든 과목에 대해 완료된 후에 진행해야 합니다.

    Args:
        term_id: 회차 ID (예: "2026-1"). 비어있으면 현재 회차 자동 판별.
    """
    term = cl.user_session.get("term")
    if not term:
        term = get_current_term()
    if term_id:
        term["term_id"] = term_id
    cl.user_session.set("term", term)

    term_id = term["term_id"]

    attendance_sheet_id = _resolve_attendance_sheet_id(term)
    if not attendance_sheet_id:
        return (
            "출석부 시트를 찾을 수 없습니다. "
            "출석부 생성 및 출석 체크가 완료되었는지 확인해주세요."
        )

    cl.user_session.set("state", "running_graduation")
    progress = cl.Message(content=f"🎓 **{term['term_name']}** 종강 처리를 시작합니다...")
    await progress.send()

    try:
        # 1. 출석률 집계
        progress.content = "📊 출석률을 집계하고 있습니다..."
        await progress.update()
        results = await load_attendance_results(attendance_sheet_id, term_id=term_id)

        if not results:
            cl.user_session.set("state", "idle")
            progress.content = "출석 데이터가 없습니다. 출석 체크(OCR)가 완료되었는지 확인해주세요."
            await progress.update()
            return "출석 데이터가 없습니다."

        # 2. 출석률 기록
        progress.content = f"📝 출석률 **{len(results)}건** 집계 완료. 출석부에 기록 중..."
        await progress.update()
        await update_attendance_rates_in_sheet(attendance_sheet_id, results, term_id)

        # 3. 수강기록 저장
        progress.content = "📋 수강기록을 저장하고 있습니다..."
        await progress.update()
        name_to_id = load_student_name_id_map(attendance_sheet_id)
        course_records = build_course_records(results, name_to_id, term_id)
        if course_records:
            await append_course_records(course_records)

        # 4. 회원목록 재집계
        progress.content = f"📊 수강기록 **{len(course_records)}건** 저장 완료. 회원 통계 재집계 중..."
        await progress.update()
        members = await load_members_from_sheet()
        members = await recalculate_member_stats(members)

        # 5. 등급 강등
        progress.content = f"🔄 회원 **{len(members)}명** 재집계 완료. 등급 강등 처리 중..."
        await progress.update()
        active_staff_ids = get_active_staff_ids()
        change_records = apply_demotion(members, term_id, active_staff_ids)

        junior_count = sum(1 for r in change_records if r["사유"] == "종강강등")
        full_count = sum(1 for r in change_records if r["사유"] == "겨울학기강등")

        # 6. 저장
        progress.content = "💾 저장 중..."
        await progress.update()
        await update_members_sheet(members)
        if change_records:
            await append_member_records(change_records)

        members_link = f"https://docs.google.com/spreadsheets/d/{MEMBERS_SHEET_ID}"

        result_msg = (
            f"✅ **{term['term_name']}** 종강 처리가 완료되었습니다!\n\n"
            f"- 수강기록 추가: **{len(course_records)}건**\n"
            f"- 준회원 → 회원 강등: **{junior_count}명**\n"
        )
        if full_count:
            result_msg += f"- 정회원 → 회원 강등: **{full_count}명**\n"
        result_msg += f"\n[회원관리 시트 열기]({members_link})"

        progress.content = result_msg
        await progress.update()
        return "__SILENT__"

    except Exception as e:
        return f"종강 처리 중 오류: {e}"
    finally:
        cl.user_session.set("state", "idle")

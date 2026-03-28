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
    # 회차 결정
    term = cl.user_session.get("term")
    if not term:
        term = get_current_term()
    if term_id:
        term["term_id"] = term_id
    cl.user_session.set("term", term)

    term_id = term["term_id"]

    # 출석부 시트 찾기
    attendance_sheet_id = _resolve_attendance_sheet_id(term)
    if not attendance_sheet_id:
        return (
            "출석부 시트를 찾을 수 없습니다. "
            "출석부 생성 및 출석 체크가 완료되었는지 확인해주세요."
        )

    cl.user_session.set("state", "running_graduation")

    try:
        # Step 1: 출석률 집계
        async with cl.Step(name="📊 출석률 집계", type="tool") as step:
            results = await load_attendance_results(
                attendance_sheet_id, term_id=term_id,
            )
            step.output = f"총 **{len(results)}건** (수강생 × 과목) 집계 완료"

        if not results:
            cl.user_session.set("state", "idle")
            return "출석 데이터가 없습니다. 출석 체크(OCR)가 완료되었는지 확인해주세요."

        # Step 2: 출석률 기록
        async with cl.Step(name="📝 출석률 기록", type="tool") as step:
            await update_attendance_rates_in_sheet(
                attendance_sheet_id, results, term_id,
            )
            step.output = f"출석부 탭 출석률 **{len(results)}건** 업데이트"

        # Step 3: 수강기록 저장
        async with cl.Step(name="📋 수강기록 저장", type="tool") as step:
            name_to_id = load_student_name_id_map(attendance_sheet_id)
            course_records = build_course_records(results, name_to_id, term_id)
            if course_records:
                await append_course_records(course_records)
            step.output = f"수강기록 **{len(course_records)}건** 추가"

        # Step 4: 회원목록 재집계
        async with cl.Step(name="📊 회원 통계 재집계", type="tool") as step:
            members = await load_members_from_sheet()
            members = await recalculate_member_stats(members)
            step.output = f"회원 **{len(members)}명** 통계 재집계 완료"

        # Step 5: 등급 강등
        async with cl.Step(name="🔄 등급 강등", type="tool") as step:
            active_staff_ids = get_active_staff_ids()
            change_records = apply_demotion(members, term_id, active_staff_ids)

            junior_count = sum(1 for r in change_records if r["사유"] == "종강강등")
            full_count = sum(1 for r in change_records if r["사유"] == "겨울학기강등")

            parts = []
            if junior_count:
                parts.append(f"준회원 → 회원 **{junior_count}명**")
            if full_count:
                parts.append(f"정회원 → 회원 **{full_count}명**")
            step.output = ", ".join(parts) if parts else "등급 변경 없음"

        # Step 6: 저장
        async with cl.Step(name="💾 저장", type="tool") as step:
            await update_members_sheet(members)
            if change_records:
                await append_member_records(change_records)
            step.output = "회원목록 + 등급변경 이력 저장 완료"

        members_link = f"https://docs.google.com/spreadsheets/d/{MEMBERS_SHEET_ID}"

        result_msg = (
            f"✅ **{term['term_name']}** 종강 처리가 완료되었습니다!\n\n"
            f"- 수강기록 추가: **{len(course_records)}건**\n"
            f"- 준회원 → 회원 강등: **{junior_count}명**\n"
        )
        if full_count:
            result_msg += f"- 정회원 → 회원 강등: **{full_count}명**\n"
        result_msg += f"\n[회원관리 시트 열기]({members_link})"

        return result_msg

    except Exception as e:
        return f"종강 처리 중 오류: {e}"
    finally:
        cl.user_session.set("state", "idle")

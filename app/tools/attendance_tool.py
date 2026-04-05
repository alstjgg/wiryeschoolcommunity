"""출석부 생성 tool — 처리상태 gate check → 출석부 시트 + PDF 생성"""

import asyncio
import logging

import chainlit as cl
from langchain_core.tools import tool

from app.chains.attendance import (
    load_registered_students,
    group_by_course,
    create_attendance_spreadsheet,
    generate_attendance_pdf,
    upload_pdf_to_drive,
)
from app.config import USE_DB_SOT, MEMBERS_SHEET_ID, APPLICATIONS_TAB, UNMATCHED_DEPOSITS_TAB
from app.context.term import get_current_term, build_term_from_id
from app.services.google_drive import find_term_folder
from app.services.google_sheets import read_sheet

logger = logging.getLogger(__name__)


def _get_app_sheet_id() -> str | None:
    sheet_id = cl.user_session.get("applications_sheet_id")
    if not sheet_id and USE_DB_SOT:
        sheet_id = MEMBERS_SHEET_ID
    return sheet_id


def _check_processing_gate(term: dict, app_sheet_id: str | None) -> str | None:
    """Check for unprocessed items. Returns None if clear, or a message describing blockers."""
    term_id = term.get("term_id", "")

    def _read_unprocessed(sid: str, tab: str, col_range: str) -> list[dict]:
        result = []
        if not sid:
            return result
        rows = read_sheet(sid, f"{tab}!{col_range}")
        if not rows or len(rows) < 2:
            return result
        header = rows[0]
        for row in rows[1:]:
            data = dict(zip(header, row + [""] * (len(header) - len(row))))
            if term_id and data.get("회차", "").strip() != term_id:
                continue
            status = data.get("처리상태", "").strip()
            if not status or status == "보류":
                result.append(data)
        return result

    if USE_DB_SOT:
        apps_sheet_id = MEMBERS_SHEET_ID
        apps_tab = APPLICATIONS_TAB
    else:
        apps_sheet_id = app_sheet_id
        apps_tab = "신청서"

    unprocessed_apps = _read_unprocessed(apps_sheet_id, apps_tab, "A1:M5000")
    unprocessed_deposits = (
        _read_unprocessed(MEMBERS_SHEET_ID, UNMATCHED_DEPOSITS_TAB, "A1:H5000")
        if USE_DB_SOT else []
    )

    if not unprocessed_apps and not unprocessed_deposits:
        return None

    sections = []
    if unprocessed_apps:
        lines = []
        for a in unprocessed_apps[:5]:
            name = a.get("이름", "?")
            course = a.get("과목명", "")
            label = f"{name}({course})" if course else name
            ps = a.get("처리상태", "").strip() or "미입력"
            lines.append(f"  - {label} — 입금현황: {a.get('입금현황', '')}, 처리상태: {ps}")
        if len(unprocessed_apps) > 5:
            lines.append(f"  - ... 외 {len(unprocessed_apps) - 5}건")
        sections.append(
            f"**신청기록 미처리: {len(unprocessed_apps)}건**\n"
            + "\n".join(lines)
            + "\n  → 신청기록 시트에서 처리상태를 입력해주세요"
        )

    if unprocessed_deposits:
        lines = []
        for d in unprocessed_deposits[:5]:
            ps = d.get("처리상태", "").strip() or "미입력"
            lines.append(f"  - 의뢰인 {d.get('의뢰인', '?')} / {d.get('입금액', '')}원 — 처리상태: {ps}")
        if len(unprocessed_deposits) > 5:
            lines.append(f"  - ... 외 {len(unprocessed_deposits) - 5}건")
        sections.append(
            f"**미확인입금 미처리: {len(unprocessed_deposits)}건**\n"
            + "\n".join(lines)
            + "\n  → 미확인입금 시트에서 처리상태를 입력해주세요"
        )

    link_id = MEMBERS_SHEET_ID if USE_DB_SOT else apps_sheet_id
    sheet_link = (
        f"\n\n[회원관리 시트 열기](https://docs.google.com/spreadsheets/d/{link_id})"
        if link_id else ""
    )

    return (
        f"**{term.get('term_name', '')}** 출석부 생성을 위해 처리가 필요한 건이 있습니다.\n\n"
        + "\n\n".join(sections)
        + f"\n\n모든 건의 처리상태를 입력해주세요 (등록완료/환불완료/취소완료).{sheet_link}"
        + "\n\n처리 완료 후 다시 '출석부 생성'을 요청해주세요."
    )


@tool
async def create_attendance(term_id: str = "") -> str:
    """출석부를 생성합니다.

    입금 대조에서 처리상태가 '등록완료'인 수강생만 포함하여
    출석부 시트(Google Sheets)와 과목별 인쇄용 PDF를 생성합니다.

    사전 조건: 입금 대조 완료 + 처리상태 입력 완료.
    처리상태가 미입력이거나 보류인 건이 있으면 출석부 생성을 차단합니다.

    Args:
        term_id: 회차 ID (예: "2026-1"). 비어있으면 현재 회차 자동 판별.
    """
    term = cl.user_session.get("term")
    if not term:
        if not term_id:
            current = get_current_term()
            cl.user_session.set("term_confirm_tool", "attendance")
            cl.user_session.set("term", current)
            await cl.Message(
                content=(
                    f"현재 회차는 **{current['term_name']}**입니다.\n\n"
                    f"이 회차의 출석부를 생성할까요?\n"
                    f"다른 회차를 원하시면 **'2025-4 가을학기'**처럼 말씀해주세요."
                ),
                actions=[
                    cl.Action(name="term_confirm", label="✅ 맞습니다", payload={"value": "confirm"}),
                    cl.Action(name="term_other", label="📅 다른 회차", payload={"value": "other"}),
                ],
            ).send()
            return "__SILENT__"
        term = get_current_term()
    if term_id:
        rebuilt = build_term_from_id(term_id)
        if rebuilt:
            term = rebuilt
    cl.user_session.set("term", term)

    att_term_id = term["term_id"]

    term_folder_id = cl.user_session.get("term_folder_id")
    if not term_folder_id:
        term_folder = find_term_folder(att_term_id)
        if not term_folder:
            return (
                f"Drive에서 **{term['term_name']}** 폴더를 찾을 수 없습니다.\n"
                f"학사운영 → {term['year']} → 회차 폴더가 있는지 확인해주세요."
            )
        term_folder_id = term_folder["id"]
        cl.user_session.set("term_folder_id", term_folder_id)

    app_sheet_id = _get_app_sheet_id()
    gate_msg = _check_processing_gate(term, app_sheet_id)
    if gate_msg:
        return gate_msg

    cl.user_session.set("state", "creating_attendance")
    progress = cl.Message(content=f"📋 **{term['term_name']}** 출석부 생성을 시작합니다...")
    await progress.send()

    try:
        # 1. 등록 수강생 확인
        progress.content = "📊 등록 수강생을 확인하고 있습니다..."
        await progress.update()

        registered = await load_registered_students(app_sheet_id, term_id=att_term_id)
        if not registered:
            cl.user_session.set("state", "idle")
            progress.content = (
                "처리상태가 '등록완료'인 수강생이 없습니다.\n"
                "배움숲 등록 처리 후 신청기록 시트에서 처리상태를 '등록완료'로 설정해주세요."
            )
            await progress.update()
            return progress.content

        courses = group_by_course(registered)
        course_names = sorted(courses.keys())
        total_students = sum(len(v) for v in courses.values())

        # 2. 출석부 시트 생성
        progress.content = f"📋 수강생 **{total_students}명** ({len(course_names)}개 과목) 확인. 출석부 시트 생성 중..."
        await progress.update()

        sheet_result = await asyncio.to_thread(
            create_attendance_spreadsheet, att_term_id, term_folder_id, courses
        )
        cl.user_session.set("attendance_sheet_id", sheet_result["spreadsheet_id"])

        # 3. 과목별 PDF 생성 + 업로드
        pdf_urls: dict[str, str | None] = {}
        for idx, course_name in enumerate(course_names):
            try:
                pdf_bytes = await asyncio.to_thread(
                    generate_attendance_pdf, att_term_id, course_name, courses[course_name]
                )
                pdf_url = await asyncio.to_thread(
                    upload_pdf_to_drive,
                    pdf_bytes, att_term_id, course_name, sheet_result["attendance_folder_id"],
                )
                pdf_urls[course_name] = pdf_url
            except Exception:
                pdf_urls[course_name] = None

            progress.content = f"PDF 생성 중... ({idx + 1}/{len(course_names)})"
            await progress.update()

        pdf_count = sum(1 for v in pdf_urls.values() if v)

        pdf_lines = []
        for course in course_names:
            url = pdf_urls.get(course)
            if url:
                pdf_lines.append(f"  - [{course}]({url})")
            else:
                pdf_lines.append(f"  - {course} (PDF 생성 실패)")
        pdf_section = "\n\n**과목별 인쇄용 PDF**:\n" + "\n".join(pdf_lines)

        final = (
            f"✅ 출석부 생성이 완료되었습니다!\n\n"
            f"- 과목 수: **{len(course_names)}개**\n"
            f"- 총 수강생: **{total_students}명**\n"
            f"- PDF: **{pdf_count}개** 생성\n\n"
            f"[{term['term_name']} 출석부 폴더]({sheet_result['attendance_folder_url']})\n"
            f"[출석부 시트 열기]({sheet_result['spreadsheet_url']})"
            f"{pdf_section}\n\n"
            "과목별 PDF를 출력하여 강사에게 전달해주세요.\n"
            "종강 후 출석 체크(사진 촬영 → OCR)를 진행해주세요."
        )

        progress.content = final
        await progress.update()
        return "__SILENT__"

    except Exception as e:
        return f"출석부 생성 중 오류: {e}"
    finally:
        cl.user_session.set("state", "idle")

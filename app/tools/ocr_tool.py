"""출석 체크 OCR tool — 출석부 사진 → Claude Vision → 시트 반영"""

import logging

import chainlit as cl
from langchain_core.tools import tool

from app.chains.ocr import (
    load_course_students,
    process_attendance_image,
    write_attendance_to_sheet,
)
from app.context.term import get_current_term
from app.services.google_drive import find_term_folder, find_or_create_folder, find_spreadsheet_by_name
from app.services.google_sheets import read_sheet
from app.utils.matching import fuzzy_course_match

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


def _extract_course_name(text: str, attendance_sheet_id: str) -> str:
    """메시지 텍스트에서 출석부 과목명을 추출."""
    if not text:
        return ""

    rows = read_sheet(attendance_sheet_id, "출석부!C1:C5000")
    if not rows or len(rows) < 2:
        return ""
    course_list = list(dict.fromkeys(
        row[0] for row in rows[1:] if row and row[0]
    ))

    for course in course_list:
        if course in text:
            return course

    matched = fuzzy_course_match(text, course_list)
    return matched or ""


@tool
async def check_attendance_ocr(
    file_path: str = "",
    course_name: str = "",
    term_id: str = "",
) -> str:
    """출석부 사진을 OCR로 분석하여 출석 체크를 수행합니다.

    종이 출석부의 사진을 Claude Vision으로 분석하여
    수강생별 회차별 출석 여부를 인식하고 출석부 시트에 반영합니다.

    사전 조건: 출석부 생성 완료 + 해당 회차 종강 완료.

    사용 방법:
    1. 관리자가 출석부 사진과 과목명을 함께 전달
    2. AI가 사진을 분석하여 출석 인식
    3. 결과를 시트에 반영

    Args:
        file_path: 출석부 사진 파일 경로. 비어있으면 사진 업로드를 요청합니다.
        course_name: 과목명. 비어있으면 메시지에서 추출을 시도합니다.
        term_id: 회차 ID (예: "2026-1"). 비어있으면 현재 회차.
    """
    term = cl.user_session.get("term")
    if not term:
        term = get_current_term()
    if term_id:
        term["term_id"] = term_id
    cl.user_session.set("term", term)

    ocr_term_id = term["term_id"]

    attendance_sheet_id = _resolve_attendance_sheet_id(term)
    if not attendance_sheet_id:
        return (
            "출석부 시트를 찾을 수 없습니다.\n"
            "출석부 생성이 먼저 완료되어야 합니다."
        )

    if not file_path:
        cl.user_session.set("state", "awaiting_ocr_image")
        return (
            f"**{term['term_name']}** 출석부 사진을 업로드해주세요.\n\n"
            "**촬영 방법**:\n"
            "- 한 과목의 출석부 전체가 나오도록 촬영해주세요.\n"
            "- 이름과 회차 칸이 모두 선명하게 보여야 합니다.\n\n"
            "사진과 함께 **과목명**을 입력해주세요.\n"
            "예) `경제뉴스 기초 출석부입니다` + 사진 첨부"
        )

    if not course_name:
        course_name = _extract_course_name(course_name, attendance_sheet_id)
    if not course_name:
        return (
            "과목명을 인식하지 못했습니다.\n"
            "예) `경제뉴스 기초 출석부입니다` 처럼 과목명을 함께 입력해주세요."
        )

    try:
        # 1. 수강생 목록 로드 + 이미지 읽기
        progress = cl.Message(content=f"📸 **{course_name}** 출석부 이미지를 분석하고 있습니다...")
        await progress.send()

        with open(file_path, "rb") as f:
            image_bytes = f.read()
        students = await load_course_students(
            attendance_sheet_id, course_name, term_id=ocr_term_id,
        )
        if not students:
            progress.content = f"출석부 시트에서 **{course_name}** 과목을 찾을 수 없습니다.\n과목명을 정확히 입력해주세요."
            await progress.update()
            return progress.content

        # 2. OCR 실행
        progress.content = f"🤖 **{course_name}** 수강생 **{len(students)}명** 확인. 출석 인식 중..."
        await progress.update()

        ocr_result = await process_attendance_image(image_bytes, course_name, students)
        recognized = len(ocr_result["results"])
        unrecognized = len(ocr_result["unrecognized"])

        # 3. 시트 반영
        progress.content = f"💾 인식 **{recognized}명** 완료. 출석부 시트에 반영 중..."
        await progress.update()

        updated = await write_attendance_to_sheet(
            attendance_sheet_id, course_name,
            ocr_result["results"], students,
            term_id=ocr_term_id,
        )

        # 결과 요약
        preview_lines = [f"**{course_name}** 출석 인식 결과:\n"]
        for r in ocr_result["results"][:10]:
            attended = sum(1 for v in r["출석"].values() if v == "O")
            preview_lines.append(f"- {r['이름']}: {attended}회 출석")
        if len(ocr_result["results"]) > 10:
            preview_lines.append(f"... 외 {len(ocr_result['results']) - 10}명")
        if ocr_result["unrecognized"]:
            preview_lines.append(
                f"\n⚠️ 이미지에서 찾지 못한 수강생: "
                f"{', '.join(ocr_result['unrecognized'])}"
            )

        preview_lines.append(f"\n✅ **{updated}명** 출석부 시트에 반영 완료")
        preview_lines.append("\n다른 과목도 처리하시려면 사진과 과목명을 보내주세요.")

        final = "\n".join(preview_lines)
        progress.content = final
        await progress.update()

        cl.user_session.set("state", "idle")
        return "__SILENT__"

    except Exception as e:
        cl.user_session.set("state", "idle")
        return f"출석 체크 중 오류: {e}"

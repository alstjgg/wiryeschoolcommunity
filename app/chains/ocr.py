"""출석 체크 OCR 파이프라인 — Claude Vision으로 종이 출석부 디지털화

종이 출석부의 (이름 × 회차) 셀에 수강생 사인 여부를 인식.
출석 = "O", 결석 = "" (빈칸). 지각 처리 없음.

출석부 시트 구조 (단일 탭 "출석부"):
  A열: 이름ID
  B열: 이름
  C열: 과목명
  D~O열: 1회차~12회차
  P열: 출석률
"""

import base64
import json
import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage

from app.config import ANTHROPIC_API_KEY, LLM_MODEL, MAX_SESSIONS, USE_DB_SOT
from app.services.google_sheets import read_sheet, write_sheet

logger = logging.getLogger(__name__)


def _load_course_students_from_sheets(
    spreadsheet_id: str,
    course_name: str,
) -> list[dict]:
    """Sheets 출석부 탭에서 특정 과목의 수강생 목록과 출석 데이터 로드."""
    # 컬럼: 이름ID(A) | 이름(B) | 전화번호(C) | 과목명(D) | 1회차(E) | ... | 12회차(P) | 출석률(Q)
    col_end = chr(ord("A") + 4 + MAX_SESSIONS)  # "Q"
    rows = read_sheet(spreadsheet_id, f"출석부!A1:{col_end}5000")
    if not rows or len(rows) < 2:
        return []

    header = rows[0]
    students = []
    for i, row in enumerate(rows[1:], start=2):
        padded = row + [""] * (len(header) - len(row))
        if padded[3] != course_name:  # D열: 과목명 필터
            continue
        name = padded[1]  # B열: 이름
        if not name:
            continue
        attendance = {
            str(j + 1): padded[4 + j]  # E열부터 시작
            for j in range(MAX_SESSIONS)
        }
        students.append({
            "row_index": i,
            "이름": name,
            "출석": attendance,
        })
    return students


async def _load_course_students_from_db(
    term_id: str,
    course_name: str,
) -> list[dict]:
    """DB attendance 테이블에서 수강생 목록 로드."""
    from app.services import db

    rows = await db.load_attendance(term_id, course_name)
    students = []
    for i, r in enumerate(rows, start=2):
        session_data = r.get("session_data", {})
        attendance = {
            str(j + 1): session_data.get(str(j + 1), "")
            for j in range(MAX_SESSIONS)
        }
        students.append({
            "row_index": i,
            "이름": r["이름"],
            "출석": attendance,
        })
    return students


async def load_course_students(
    spreadsheet_id: str,
    course_name: str,
    term_id: str = "",
) -> list[dict]:
    """출석부에서 특정 과목 탭의 수강생 목록과 현재 출석 데이터를 로드.

    DB 모드: attendance 테이블에서 읽기.
    Sheets 모드: 과목별 탭에서 직접 읽기.

    Returns: [
        {
            "row_index": int,   # 시트 행 번호 (1-based, 헤더=1, 데이터 시작=2)
            "이름": str,
            "출석": {"1": "O", "2": "", ...}
        }, ...
    ]
    """
    if USE_DB_SOT and term_id:
        try:
            result = await _load_course_students_from_db(term_id, course_name)
            if result:
                return result
            # DB에 데이터가 없으면 (첫 OCR 전) Sheets로 폴백
        except Exception as e:
            logger.error("DB read failed, falling back to Sheets: %s", e)

    return _load_course_students_from_sheets(spreadsheet_id, course_name)


async def process_attendance_image(
    image_bytes: bytes,
    course_name: str,
    students: list[dict],
) -> dict:
    """Claude Vision으로 출석부 이미지를 파싱.

    출석부는 Google Sheets에서 인쇄된 표 형태:
      - 1열: 수강생 이름 (세로로 나열)
      - 1행: 강의 회차 번호 (1회차, 2회차, ... 가로로 나열)
      - 각 셀: 수강생이 해당 회차 출석 시 사인 또는 체크 표시를 직접 기입

    판단 기준:
      - 해당 셀에 어떤 표시든 있으면 → 출석 ("O")
      - 해당 셀이 비어있으면 → 결석 ("")
      - 지각 구분 없음

    Returns: {
        "results": [
            {"이름": str, "출석": {"1": "O", "2": "", ...}}
        ],
        "unrecognized": [str],  # 시트에 있으나 이미지에서 못 찾은 이름
        "raw_response": str,
    }
    """
    llm = ChatAnthropic(
        model=LLM_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=2048,
    ).with_config({"run_name": "ocr_vision_llm"})

    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    student_list = "\n".join(f"- {s['이름']}" for s in students)

    prompt = f"""이것은 위례인생학교 강의 출석부 사진입니다.
출석부는 Google Sheets에서 인쇄된 표 형태로, 구조는 다음과 같습니다:
- 1열: 수강생 이름 (세로로 나열)
- 1행: 강의 회차 번호 (1회차, 2회차, ... 가로로 나열)
- 각 셀: 수강생이 해당 회차 출석 시 사인 또는 체크 표시를 직접 기입

판단 기준:
- 해당 셀에 어떤 표시든(사인, 체크, 낙서 등) 있으면 → 출석 ("O")
- 해당 셀이 비어있으면 → 결석 ("")
- 지각 구분 없음 (표시가 있으면 무조건 출석)

수강생 목록 ({len(students)}명):
{student_list}

응답은 반드시 JSON만 출력하세요. 설명 금지:
{{
    "results": [
        {{
            "이름": "수강생 이름",
            "출석": {{"1": "O", "2": "", "3": "O", ...}}
        }}
    ],
    "unrecognized": ["이미지에서 찾지 못한 이름1", ...]
}}

최대 {MAX_SESSIONS}회차까지 인식. 이미지에서 보이는 회차만 포함해도 됩니다."""

    try:
        response = await llm.ainvoke([
            HumanMessage(content=[
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ])
        ])

        content = response.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        parsed = json.loads(content)
        return {
            "results": parsed.get("results", []),
            "unrecognized": parsed.get("unrecognized", []),
            "raw_response": response.content,
        }

    except Exception as e:
        return {
            "results": [],
            "unrecognized": [s["이름"] for s in students],
            "raw_response": str(e),
        }


async def write_attendance_to_sheet(
    spreadsheet_id: str,
    course_name: str,
    ocr_results: list[dict],
    students: list[dict],
    term_id: str = "",
) -> int:
    """OCR 결과를 출석부 시트에 반영.

    DB 모드: DB attendance 테이블에 upsert + Sheets에도 반영.
    Sheets 모드: Sheets에만 반영.

    쓰기 범위: E{row}:P{row} (1회차~12회차, E열부터 시작)
    Returns: 업데이트된 수강생 수
    """
    name_to_row = {s["이름"]: s["row_index"] for s in students}

    # E열(1회차) ~ P열(12회차)
    col_start = "E"
    col_end = chr(ord("E") + MAX_SESSIONS - 1)  # "P"
    updated = 0

    for result in ocr_results:
        name = result.get("이름", "")
        if not name or name not in name_to_row:
            continue

        row_index = name_to_row[name]
        attendance = result.get("출석", {})

        # DB 모드: attendance 테이블에 upsert
        if USE_DB_SOT and term_id:
            try:
                from app.services import db
                await db.upsert_attendance(
                    term_id, course_name, name, attendance,
                )
            except Exception as e:
                logger.error("DB write failed for %s: %s", name, e)

        # Sheets에 항상 반영 (관리자 view)
        values = [attendance.get(str(i), "") for i in range(1, MAX_SESSIONS + 1)]
        range_notation = f"출석부!{col_start}{row_index}:{col_end}{row_index}"
        write_sheet(spreadsheet_id, range_notation, [values])
        updated += 1

    return updated

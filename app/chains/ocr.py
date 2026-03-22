"""출석 체크 OCR 파이프라인 — Claude Vision으로 종이 출석부 디지털화

종이 출석부의 (이름 × 회차) 셀에 수강생 사인 여부를 인식.
출석 = "O", 결석 = "" (빈칸). 지각 처리 없음.

출석부 시트 구조 (과목별 탭):
  A열: 이름 (1행: 헤더 "이름", 2행~: 수강생 이름)
  B~M열: 1회차~12회차
"""

import base64
import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage

from app.config import ANTHROPIC_API_KEY, LLM_MODEL, MAX_SESSIONS
from app.services.google_sheets import read_sheet, write_sheet


def load_course_students(
    spreadsheet_id: str,
    course_name: str,
) -> list[dict]:
    """출석부 시트에서 특정 과목 탭의 수강생 목록과 현재 출석 데이터를 로드.

    과목별 탭 구조:
      A열: 이름, B~M열: 1~12회차

    Returns: [
        {
            "row_index": int,   # 시트 행 번호 (1-based, 헤더=1, 데이터 시작=2)
            "이름": str,
            "출석": {"1": "O", "2": "", ...}
        }, ...
    ]
    """
    col_count = 1 + MAX_SESSIONS  # A~M = 13컬럼
    col_end = chr(ord("A") + col_count - 1)  # "M"
    rows = read_sheet(spreadsheet_id, f"{course_name}!A1:{col_end}500")
    if not rows or len(rows) < 2:
        return []

    students = []
    for i, row in enumerate(rows[1:], start=2):
        padded = row + [""] * (col_count - len(row))
        name = padded[0]
        if not name:
            continue
        attendance = {
            str(j + 1): padded[1 + j]
            for j in range(MAX_SESSIONS)
        }
        students.append({
            "row_index": i,
            "이름": name,
            "출석": attendance,
        })
    return students


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
    )

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


def write_attendance_to_sheet(
    spreadsheet_id: str,
    course_name: str,
    ocr_results: list[dict],
    students: list[dict],
) -> int:
    """OCR 결과를 출석부 시트의 해당 과목 탭에 반영.

    쓰기 범위: B{row}:M{row} (1회차~12회차, B열부터 시작)
    Returns: 업데이트된 수강생 수
    """
    # 이름 → row_index 매핑
    name_to_row = {s["이름"]: s["row_index"] for s in students}

    col_end = chr(ord("B") + MAX_SESSIONS - 1)  # "M"
    updated = 0

    for result in ocr_results:
        name = result.get("이름", "")
        if not name or name not in name_to_row:
            continue

        row_index = name_to_row[name]
        attendance = result.get("출석", {})

        # 1회차~12회차 값 리스트 (B열부터)
        values = [attendance.get(str(i), "") for i in range(1, MAX_SESSIONS + 1)]
        range_notation = f"{course_name}!B{row_index}:{col_end}{row_index}"
        write_sheet(spreadsheet_id, range_notation, [values])
        updated += 1

    return updated

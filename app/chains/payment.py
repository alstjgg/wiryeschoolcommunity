"""입금 대조 파이프라인 — 수강생 로드 + 코드 매칭 + LLM 폴백 + 시트 기록"""

import io
import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import (
    ANTHROPIC_API_KEY, LLM_MODEL, STUDENTS_SHEET_ID,
    ATTENDANCE_FOLDER_ID, COURSE_KEYWORDS,
)
from app.services.google_auth import get_drive_service
from app.services.google_drive import list_files
from app.services.google_sheets import read_sheet, write_sheet
from app.services.excel import parse_bank_statement, parse_student_list
from app.utils.matching import run_code_matching


def load_students_from_drive() -> list[dict]:
    """출석부 폴더의 모든 .xlsx 파일을 다운로드하여 수강생 목록 생성"""
    drive = get_drive_service()
    files = list_files(ATTENDANCE_FOLDER_ID)

    # xlsx/spreadsheet 파일만 필터
    xlsx_files = [
        f for f in files
        if f["name"].endswith(".xlsx")
        or f["mimeType"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ]

    all_students = []
    for file_info in xlsx_files:
        file_id = file_info["id"]
        # Drive에서 파일 다운로드
        request = drive.files().get_media(fileId=file_id)
        file_bytes = request.execute()
        students = parse_student_list(file_bytes, filename=file_info["name"])
        all_students.extend(students)

    return all_students


def load_students_from_sheet() -> list[dict]:
    """수강생 Google Spreadsheet에서 학생 목록 로드"""
    rows = read_sheet(STUDENTS_SHEET_ID, "수강생!A1:G500")
    if not rows or len(rows) < 2:
        return []

    header = rows[0]
    students = []
    for row in rows[1:]:
        if len(row) < 3:
            continue
        # 수강생 시트 컬럼: 이름ID | 과목명 | 입금시간 | 입금자명(적요) | 비고 | 입금현황 | 등록상태
        student = {
            "이름ID": row[0] if len(row) > 0 else "",
            "강좌명": row[1] if len(row) > 1 else "",
            "이름": row[0].rstrip("0123456789") if len(row) > 0 else "",  # ID에서 이름 추출
        }
        if student["이름ID"]:
            students.append(student)
    return students


def populate_students_sheet(students: list[dict]) -> int:
    """수강생 목록을 수강생 Google Spreadsheet에 기록"""
    header = ["이름ID", "과목명", "입금시간", "입금자명(적요)", "비고", "입금현황", "등록상태"]
    rows = [header]
    for s in students:
        rows.append([
            s.get("이름ID", ""),
            s.get("강좌명", ""),
            "",  # 입금시간
            "",  # 입금자명(적요)
            "",  # 비고
            "❌미입금",  # 입금현황 기본값
            "",  # 등록상태
        ])
    write_sheet(STUDENTS_SHEET_ID, "수강생!A1", rows)
    return len(rows) - 1


async def run_llm_matching(unmatched: list[dict], students: list[dict]) -> list[dict]:
    """LLM으로 미매칭 건 처리 — 비정형 적요 텍스트 해석"""
    if not unmatched:
        return []

    llm = ChatAnthropic(
        model=LLM_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=4096,
    )

    # 학생 목록 (이름 + 강좌) 정리
    student_info = []
    for s in students:
        student_info.append(f"- {s['이름']} / {s['강좌명']} (ID: {s['이름ID']})")
    student_list_text = "\n".join(student_info)

    # 미매칭 거래 정리
    tx_list = []
    for i, tx in enumerate(unmatched):
        tx_list.append(
            f"{i+1}. 적요: \"{tx['적요']}\" / 의뢰인: \"{tx['의뢰인']}\" / "
            f"금액: {tx['입금']:,}원 / 현재비고: {tx.get('비고', '')}"
        )
    tx_text = "\n".join(tx_list)

    # 강좌 키워드 매핑 정보
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

## 응답 형식
JSON 배열로 응답하세요. 각 항목:
```json
[
  {{
    "index": 1,
    "매칭이름": "학생이름" 또는 null,
    "매칭ID": "학생ID" 또는 null,
    "매칭강좌": "강좌명" 또는 null,
    "상태": "✅정상" 또는 "🔶확인필요",
    "비고": "판단 근거"
  }}
]
```
JSON만 응답하세요. 설명은 비고 필드에 넣어주세요."""

    user_prompt = f"다음 미매칭 거래들을 매칭해주세요:\n\n{tx_text}"

    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])

    # 응답 파싱
    content = response.content.strip()
    # JSON 블록 추출
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    try:
        llm_results = json.loads(content)
    except json.JSONDecodeError:
        # 파싱 실패 → 모두 확인필요로
        return unmatched

    # LLM 결과를 미매칭 건에 반영
    for llm_item in llm_results:
        idx = llm_item.get("index", 0) - 1
        if 0 <= idx < len(unmatched):
            tx = unmatched[idx]
            tx["매칭이름"] = llm_item.get("매칭이름") or tx.get("매칭이름")
            tx["매칭ID"] = llm_item.get("매칭ID") or tx.get("매칭ID")
            tx["매칭강좌"] = llm_item.get("매칭강좌") or tx.get("매칭강좌")
            tx["상태"] = llm_item.get("상태", "🔶확인필요")
            tx["비고"] = llm_item.get("비고", tx.get("비고", ""))

    return unmatched


def find_unpaid_students(students: list[dict], matched_results: list[dict]) -> list[dict]:
    """매칭된 결과에 없는 수강생 → 미입금 목록"""
    matched_ids = set()
    for r in matched_results:
        if r.get("매칭ID") and r["상태"] == "✅정상":
            matched_ids.add(r["매칭ID"])

    unpaid = []
    for s in students:
        if s["이름ID"] not in matched_ids:
            unpaid.append({
                "이름ID": s["이름ID"],
                "이름": s["이름"],
                "강좌명": s["강좌명"],
                "상태": "❌미입금",
            })
    return unpaid


def format_results(matched: list[dict], unpaid: list[dict]) -> str:
    """매칭 결과를 한국어 요약 텍스트로 포맷"""
    # 통계
    total = len(matched)
    success = sum(1 for r in matched if r["상태"] == "✅정상")
    needs_check = sum(1 for r in matched if r["상태"] == "🔶확인필요")
    skipped = sum(1 for r in matched if r["상태"] == "⏭️스킵")
    unmatched = sum(1 for r in matched if r["상태"] == "❌미매칭")

    lines = [
        "## 📊 입금 대조 결과\n",
        f"**총 거래**: {total}건",
        f"- ✅ 정상 매칭: {success}건",
        f"- 🔶 확인 필요: {needs_check}건",
        f"- ❌ 미매칭: {unmatched}건",
        f"- ⏭️ 스킵: {skipped}건",
        f"- ❌ 미입금 수강생: {len(unpaid)}명\n",
    ]

    # 정상 매칭 목록
    if success > 0:
        lines.append("### ✅ 정상 매칭")
        for r in matched:
            if r["상태"] == "✅정상":
                lines.append(
                    f"- {r['매칭이름']} → {r.get('매칭강좌', '?')} "
                    f"({r['금액분류']})"
                )
        lines.append("")

    # 확인 필요
    if needs_check > 0:
        lines.append("### 🔶 확인 필요")
        for r in matched:
            if r["상태"] == "🔶확인필요":
                lines.append(
                    f"- 적요: \"{r['적요']}\" / 의뢰인: \"{r['의뢰인']}\" / "
                    f"{r['금액분류']} → {r.get('비고', '')}"
                )
        lines.append("")

    # 미입금
    if unpaid:
        lines.append("### ❌ 미입금 수강생")
        for u in unpaid:
            lines.append(f"- {u['이름']} ({u['강좌명']})")
        lines.append("")

    lines.append("---")
    lines.append("결과를 수강생 시트에 반영하시겠습니까? **'예'** 또는 **'아니오'**로 답해주세요.")

    return "\n".join(lines)


def write_results_to_sheet(matched_results: list[dict]) -> int:
    """매칭 결과를 수강생 시트에 반영 (입금시간, 적요, 입금현황, 등록상태 업데이트)"""
    # 현재 시트 읽기
    rows = read_sheet(STUDENTS_SHEET_ID, "수강생!A1:G500")
    if not rows or len(rows) < 2:
        return 0

    header = rows[0]
    data_rows = rows[1:]

    # 매칭 결과를 ID 기준으로 인덱싱
    result_by_id = {}
    for r in matched_results:
        if r.get("매칭ID") and r["상태"] == "✅정상":
            result_by_id[r["매칭ID"]] = r

    updated_count = 0
    updated_rows = []
    for row in data_rows:
        student_id = row[0] if len(row) > 0 else ""
        # 기존 값 유지하면서 업데이트할 부분만 변경
        new_row = list(row) + [""] * (7 - len(row))  # 7컬럼 보장

        if student_id in result_by_id:
            r = result_by_id[student_id]
            new_row[2] = r.get("거래일시", "")  # 입금시간
            new_row[3] = r.get("적요", "")  # 입금자명(적요)
            new_row[4] = r.get("비고", "")  # 비고
            new_row[5] = "✅정상"  # 입금현황
            new_row[6] = "정상등록"  # 등록상태
            updated_count += 1
        else:
            # 미입금 상태 유지
            if not new_row[5]:
                new_row[5] = "❌미입금"

        updated_rows.append(new_row)

    # 시트에 쓰기
    write_sheet(STUDENTS_SHEET_ID, "수강생!A2", updated_rows)
    return updated_count

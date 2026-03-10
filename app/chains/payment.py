"""입금 대조 파이프라인 — 신청자 로드 + 수강생 시트 생성 + 코드 매칭 + LLM 폴백 + 시트 기록"""

import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import (
    ANTHROPIC_API_KEY, LLM_MODEL,
    MEMBERS_SHEET_ID, RECORDS_SHEET_ID, COURSE_KEYWORDS,
)
from app.services.google_auth import get_drive_service
from app.services.google_drive import (
    list_files, find_spreadsheet_by_name, find_or_create_folder,
)
from app.services.google_sheets import read_sheet, write_sheet, append_sheet


def load_applicants_from_drive(term_folder_id: str) -> list[dict]:
    """회차 폴더에서 LEARNING_APPLY*.xls 파일을 찾아 파싱 → 신청자 목록 반환

    새 구조: 회차 폴더 내 '수강생/' 서브폴더에서 먼저 탐색.
    서브폴더가 없으면 term_folder_id 직접 탐색 (구버전 폴백).
    """
    from app.services.excel import parse_applicant_list
    from app.services.google_drive import find_file as _find_file

    # 수강생/ 서브폴더 탐색
    students_subfolder = _find_file("수강생", parent_id=term_folder_id)
    if students_subfolder and "folder" in students_subfolder.get("mimeType", ""):
        search_folder_id = students_subfolder["id"]
    else:
        search_folder_id = term_folder_id

    drive = get_drive_service()
    files = list_files(search_folder_id)

    # LEARNING_APPLY*.xls 파일 찾기
    apply_files = [
        f for f in files
        if f["name"].startswith("LEARNING_APPLY") and f["name"].endswith(".xls")
    ]

    if not apply_files:
        return []

    # 가장 최근 파일 사용 (이름 역순 정렬 → datetime 포함이므로 마지막이 최신)
    apply_files.sort(key=lambda f: f["name"], reverse=True)
    file_info = apply_files[0]

    # Drive에서 파일 다운로드
    request = drive.files().get_media(fileId=file_info["id"], supportsAllDrives=True)
    file_bytes = request.execute()

    return parse_applicant_list(file_bytes)


def create_students_sheet(applicants: list[dict], term_id: str, term_folder_id: str) -> tuple[str, int]:
    """신청자 목록으로 수강생 Google Sheets 생성/갱신

    회차 폴더에 "수강생" 시트가 이미 있으면 초기화 후 재작성, 없으면 새로 생성.

    컬럼: 이름ID, 과목명, 입금시간, 입금자명(적요), 비고, 입금현황, 등록상태

    Returns:
        (spreadsheet_id, count) 튜플
    """
    header = ["이름ID", "과목명", "입금시간", "입금자명(적요)", "비고", "입금현황", "등록상태"]
    rows = [header]
    for a in applicants:
        rows.append([
            a.get("이름ID", ""),
            a.get("강좌명", ""),
            "",  # 입금시간
            "",  # 입금자명(적요)
            "",  # 비고
            "❌미입금",  # 입금현황 기본값
            "",  # 등록상태
        ])

    # 수강생/ 서브폴더 확보 (없으면 생성)
    subfolder = find_or_create_folder(term_folder_id, "수강생")
    students_folder_id = subfolder["id"]

    # 기존 "수강생" 시트 찾기 — 서브폴더 기준
    existing = find_spreadsheet_by_name(students_folder_id, "수강생")

    if existing:
        spreadsheet_id = existing["id"]
        # 기존 데이터 초기화 후 재작성
        write_sheet(spreadsheet_id, "수강생!A1", rows)
    else:
        # 새 Google Sheets 생성
        drive = get_drive_service()
        file_metadata = {
            "name": "수강생",
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "parents": [students_folder_id],
        }
        file = drive.files().create(
            body=file_metadata, fields="id", supportsAllDrives=True
        ).execute()
        spreadsheet_id = file["id"]

        # 기본 시트탭 이름을 "수강생"으로 변경
        from app.services.google_auth import get_sheets_service
        sheets_service = get_sheets_service()
        sheet_metadata = sheets_service.spreadsheets().get(
            spreadsheetId=spreadsheet_id
        ).execute()
        default_sheet_id = sheet_metadata["sheets"][0]["properties"]["sheetId"]
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [{
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": default_sheet_id,
                            "title": "수강생",
                        },
                        "fields": "title",
                    }
                }]
            },
        ).execute()

        write_sheet(spreadsheet_id, "수강생!A1", rows)

    return spreadsheet_id, len(rows) - 1


def load_students_from_sheet(students_sheet_id: str) -> list[dict]:
    """수강생 Google Spreadsheet에서 학생 목록 로드"""
    rows = read_sheet(students_sheet_id, "수강생!A1:G500")
    if not rows or len(rows) < 2:
        return []

    students = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        student_id = row[0] if len(row) > 0 else ""
        course = row[1] if len(row) > 1 else ""
        # ID에서 이름 추출 (뒤의 숫자 4자리 제거)
        이름 = student_id.rstrip("0123456789") if student_id else ""
        if student_id:
            students.append({
                "이름ID": student_id,
                "강좌명": course,
                "이름": 이름,
            })
    return students


def load_members() -> list[dict]:
    """회원관리 시트에서 회원 목록 로드 (등급 확인용)

    컬럼: 이름ID, 이름, 성별, 전화번호, 주소, 나이, 등급, 가입날짜, 수강count, 출석률(누적), 마지막수강학기
    """
    rows = read_sheet(MEMBERS_SHEET_ID, "회원관리!A1:K500")
    if not rows or len(rows) < 2:
        return []

    members = []
    for row in rows[1:]:
        if len(row) < 7:
            continue
        members.append({
            "이름ID": row[0] if len(row) > 0 else "",
            "이름": row[1] if len(row) > 1 else "",
            "성별": row[2] if len(row) > 2 else "",
            "전화번호": row[3] if len(row) > 3 else "",
            "주소": row[4] if len(row) > 4 else "",
            "나이": row[5] if len(row) > 5 else "",
            "등급": row[6] if len(row) > 6 else "",
            "가입날짜": row[7] if len(row) > 7 else "",
            "수강count": row[8] if len(row) > 8 else "",
            "출석률(누적)": row[9] if len(row) > 9 else "",
            "마지막수강학기": row[10] if len(row) > 10 else "",
        })
    return members


def apply_exemptions(students: list[dict], members: list[dict]) -> list[dict]:
    """정회원 선처리: 정회원은 입금현황=💎면제, 등록상태=정상등록"""
    member_grades = {m["이름ID"]: m["등급"] for m in members}
    exempted = []
    for s in students:
        grade = member_grades.get(s["이름ID"], "")
        if grade == "정회원":
            exempted.append({
                "이름ID": s["이름ID"],
                "이름": s["이름"],
                "강좌명": s["강좌명"],
                "상태": "💎면제",
            })
    return exempted


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
            f"금액: {tx['입금']:,}원"
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
            tx["메모"] = llm_item.get("메모", tx.get("메모", ""))

    return unmatched


def find_unpaid_students(students: list[dict], matched_results: list[dict], exempted_ids: set[str] | None = None) -> list[dict]:
    """매칭된 결과에 없는 수강생 → 미입금 목록 (정회원 제외)"""
    if exempted_ids is None:
        exempted_ids = set()

    matched_ids = set()
    for r in matched_results:
        if r.get("매칭ID") and r["상태"] == "✅정상":
            matched_ids.add(r["매칭ID"])

    unpaid = []
    for s in students:
        if s["이름ID"] in matched_ids or s["이름ID"] in exempted_ids:
            continue
        unpaid.append({
            "이름ID": s["이름ID"],
            "이름": s["이름"],
            "강좌명": s["강좌명"],
            "상태": "❌미입금",
        })
    return unpaid


def format_results(matched: list[dict], unpaid: list[dict], exempted: list[dict] | None = None) -> str:
    """매칭 결과를 한국어 요약 텍스트로 포맷"""
    if exempted is None:
        exempted = []

    # 통계
    total = len(matched)
    success = sum(1 for r in matched if r["상태"] == "✅정상")
    needs_check = sum(1 for r in matched if r["상태"] == "🔶확인필요")
    name_mismatch = sum(1 for r in matched if r["상태"] == "⚠️이름불일치")
    duplicate = sum(1 for r in matched if r["상태"] == "🔄중복")
    skipped = sum(1 for r in matched if r["상태"] == "⏭️스킵")
    unmatched_count = sum(1 for r in matched if r["상태"] == "❌미매칭")

    lines = [
        "## 📊 입금 대조 결과\n",
        f"**총 거래**: {total}건",
        f"- ✅ 정상 매칭: {success}건",
        f"- 🔶 확인 필요: {needs_check}건",
        f"- ⚠️ 이름 불일치: {name_mismatch}건",
        f"- 🔄 중복: {duplicate}건",
        f"- ❌ 미매칭: {unmatched_count}건",
        f"- ⏭️ 스킵: {skipped}건",
        f"- 💎 면제(정회원): {len(exempted)}명",
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
                    f"{r['금액분류']} → {r.get('메모', '')}"
                )
        lines.append("")

    # 이름 불일치
    if name_mismatch > 0:
        lines.append("### ⚠️ 이름 불일치 (대리입금 추정)")
        for r in matched:
            if r["상태"] == "⚠️이름불일치":
                lines.append(
                    f"- 적요: \"{r['적요']}\" / 의뢰인: \"{r['의뢰인']}\" / "
                    f"{r['금액분류']} → {r.get('메모', '')}"
                )
        lines.append("")

    # 면제
    if exempted:
        lines.append("### 💎 면제 (정회원)")
        for e in exempted:
            lines.append(f"- {e['이름']} ({e['강좌명']})")
        lines.append("")

    # 미입금
    if unpaid:
        lines.append("### ❌ 미입금 수강생")
        for u in unpaid:
            lines.append(f"- {u['이름']} ({u['강좌명']})")
        lines.append("")

    return "\n".join(lines)


def write_results_to_sheet(students_sheet_id: str, matched_results: list[dict], exempted: list[dict] | None = None) -> int:
    """매칭 결과를 수강생 시트에 반영 (입금시간, 적요, 입금현황, 등록상태 업데이트)"""
    if exempted is None:
        exempted = []

    # 현재 시트 읽기
    rows = read_sheet(students_sheet_id, "수강생!A1:G500")
    if not rows or len(rows) < 2:
        return 0

    data_rows = rows[1:]

    # 매칭 결과를 ID 기준으로 인덱싱
    result_by_id = {}
    for r in matched_results:
        if r.get("매칭ID") and r["상태"] == "✅정상":
            result_by_id[r["매칭ID"]] = r

    # 정회원 면제 ID
    exempted_ids = {e["이름ID"] for e in exempted}

    updated_count = 0
    updated_rows = []
    for row in data_rows:
        student_id = row[0] if len(row) > 0 else ""
        # 기존 값 유지하면서 업데이트할 부분만 변경
        new_row = list(row) + [""] * (7 - len(row))  # 7컬럼 보장

        if student_id in exempted_ids:
            new_row[5] = "💎면제"  # 입금현황
            new_row[6] = "정상등록"  # 등록상태
            updated_count += 1
        elif student_id in result_by_id:
            r = result_by_id[student_id]
            new_row[2] = r.get("거래일시", "")  # 입금시간
            new_row[3] = r.get("적요", "")  # 입금자명(적요)
            new_row[5] = "✅정상"  # 입금현황
            new_row[6] = "정상등록"  # 등록상태
            updated_count += 1
        else:
            # 미입금 상태 유지
            if not new_row[5]:
                new_row[5] = "❌미입금"

        updated_rows.append(new_row)

    # 시트에 쓰기
    write_sheet(students_sheet_id, "수강생!A2", updated_rows)
    return updated_count


def update_members_after_registration(matched_results: list[dict], term_id: str) -> int:
    """회원관리 시트 업데이트: 확인된 수강생 회원→준회원 승격, 마지막수강학기/수강count 갱신"""
    confirmed_ids = set()
    for r in matched_results:
        if r.get("매칭ID") and r["상태"] in ("✅정상",):
            confirmed_ids.add(r["매칭ID"])

    rows = read_sheet(MEMBERS_SHEET_ID, "회원관리!A1:K500")
    if not rows or len(rows) < 2:
        return 0

    data_rows = rows[1:]
    updated_count = 0
    updated_rows = []

    for row in data_rows:
        new_row = list(row) + [""] * (11 - len(row))  # 11컬럼 보장
        member_id = new_row[0]

        if member_id in confirmed_ids:
            # 등급 승격: 회원 → 준회원
            if new_row[6] == "회원":
                new_row[6] = "준회원"

            # 수강count +1
            try:
                count = int(new_row[8]) if new_row[8] else 0
            except (ValueError, TypeError):
                count = 0
            new_row[8] = str(count + 1)

            # 마지막수강학기
            new_row[10] = term_id

            updated_count += 1

        updated_rows.append(new_row)

    write_sheet(MEMBERS_SHEET_ID, "회원관리!A2", updated_rows)
    return updated_count


def add_enrollment_records(matched_results: list[dict], term_id: str) -> int:
    """수강기록 시트에 확정된 수강 이력 추가"""
    new_records = []
    for r in matched_results:
        if r.get("매칭ID") and r["상태"] == "✅정상":
            new_records.append([
                r["매칭ID"],    # 이름ID
                term_id,        # 회차
                r.get("매칭강좌", ""),  # 과목명
                "",             # 출석률 (종강 시 확정)
            ])

    if not new_records:
        return 0

    append_sheet(RECORDS_SHEET_ID, "수강기록!A1", new_records)
    return len(new_records)

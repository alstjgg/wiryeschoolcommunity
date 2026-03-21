"""입금 대조 파이프라인 — DB SoT + Sheets 뷰 동기화

SoT: PostgreSQL (app/services/db.py)
관리자 뷰: Google Sheets (동기화 방향: DB → Sheets)

신청자 목록 로드: 챗봇 직접 업로드 (Drive 탐색 제거)
"""

import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import (
    ANTHROPIC_API_KEY, LLM_MODEL,
    MEMBERS_SHEET_ID, RECORDS_SHEET_ID, COURSE_KEYWORDS,
)
from app.services import db
from app.services.google_auth import get_drive_service
from app.services.google_drive import find_spreadsheet_by_name, find_or_create_folder
from app.services.google_sheets import read_sheet, write_sheet, append_sheet


# -------------------------------------------------- 수강생 시트 Sheets 동기화 ----

def _students_to_rows(students: list[dict]) -> list[list]:
    """students DB 행 목록 → Sheets 2D 배열 (헤더 포함)"""
    header = ["이름ID", "과목명", "입금시간", "입금자명(적요)", "비고", "입금현황", "등록상태"]
    rows = [header]
    for s in students:
        rows.append([
            s.get("name_id") or s.get("이름ID", ""),
            s.get("course_name") or s.get("강좌명", ""),
            s.get("payment_time") or "",
            s.get("payment_memo") or "",
            s.get("note") or "",
            s.get("payment_status") or "❌미입금",
            s.get("registration_status") or "",
        ])
    return rows


def sync_students_to_sheet(
    term_id: str,
    students: list[dict],
    term_folder_id: str,
) -> str:
    """DB students → Google Sheets 동기화.

    수강생/ 서브폴더에 "수강생" 시트가 없으면 생성.
    Returns: spreadsheet_id
    """
    subfolder = find_or_create_folder(term_folder_id, "수강생")
    students_folder_id = subfolder["id"]

    existing = find_spreadsheet_by_name(students_folder_id, "수강생")

    rows = _students_to_rows(students)

    if existing:
        spreadsheet_id = existing["id"]
        write_sheet(spreadsheet_id, "수강생!A1", rows)
        return spreadsheet_id

    # 새 시트 생성
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

    # 기본 탭 이름 "수강생"으로 변경
    from app.services.google_auth import get_sheets_service
    sheets_svc = get_sheets_service()
    meta = sheets_svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    default_sheet_id = meta["sheets"][0]["properties"]["sheetId"]
    sheets_svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [{
                "updateSheetProperties": {
                    "properties": {"sheetId": default_sheet_id, "title": "수강생"},
                    "fields": "title",
                }
            }]
        },
    ).execute()

    write_sheet(spreadsheet_id, "수강생!A1", rows)
    return spreadsheet_id


def sync_members_to_sheet(members: list[dict]) -> None:
    """DB members → 회원관리 Sheets 동기화 (헤더 포함 전체 덮어쓰기)"""
    header = ["이름ID", "이름", "성별", "전화번호", "주소", "나이", "등급",
              "수강count", "출석률(누적)", "마지막수강회차"]
    rows = [header]
    for m in members:
        rows.append([
            m.get("name_id", ""),
            m.get("name", ""),
            m.get("gender") or "",
            m.get("phone") or "",
            m.get("address") or "",
            str(m.get("age") or ""),
            m.get("grade", "회원"),
            str(m.get("enrollment_count") or 0),
            str(m.get("attendance_rate") or ""),
            m.get("last_term") or "",
        ])
    write_sheet(MEMBERS_SHEET_ID, "회원관리!A1", rows)


def sync_enrollment_records_to_sheet(records: list[dict]) -> None:
    """DB enrollment_records → 수강기록 Sheets 동기화"""
    header = ["이름ID", "회차", "과목명", "출석률"]
    rows = [header]
    for r in records:
        rows.append([
            r.get("name_id", ""),
            r.get("term_id", ""),
            r.get("course_name", ""),
            str(r.get("attendance_rate") or ""),
        ])
    write_sheet(RECORDS_SHEET_ID, "수강기록!A1", rows)


# -------------------------------------------- Sheets → DB 역방향 동기화 ----

def sync_registration_status_from_sheet(
    term_id: str,
    students_sheet_id: str,
) -> list[tuple[str, str, str]]:
    """수강생 시트 등록상태 컬럼 → DB students 반영.

    관리자가 시트에서 직접 체크한 등록상태를 DB로 가져온다.
    Returns: 업데이트된 (term_id, name_id, course_name) 목록
    """
    import asyncio

    rows = read_sheet(students_sheet_id, "수강생!A1:G500")
    if not rows or len(rows) < 2:
        return []

    updates = []
    for row in rows[1:]:
        if len(row) < 7:
            continue
        name_id = row[0]
        course_name = row[1]
        reg_status = row[6] if len(row) > 6 else ""
        if name_id and course_name and reg_status:
            updates.append((term_id, name_id, course_name, reg_status))

    if not updates:
        return []

    async def _apply():
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                UPDATE students
                SET registration_status = $4, updated_at = NOW()
                WHERE term_id = $1 AND name_id = $2 AND course_name = $3
                """,
                updates,
            )

    asyncio.get_event_loop().run_until_complete(_apply())
    return [(u[0], u[1], u[2]) for u in updates]


# ---------------------------------------------- 매칭 로직 (변경 없음) ----

def apply_exemptions(students: list[dict], members: list[dict]) -> list[dict]:
    """정회원 선처리: 정회원은 입금현황=💎면제"""
    member_grades = {m["name_id"]: m["grade"] for m in members}
    exempted = []
    for s in students:
        name_id = s.get("name_id") or s.get("이름ID", "")
        grade = member_grades.get(name_id, "")
        if grade == "정회원":
            exempted.append({
                "이름ID": name_id,
                "이름": s.get("이름", ""),
                "강좌명": s.get("course_name") or s.get("강좌명", ""),
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

    student_info = [
        f"- {s.get('이름', '')} / {s.get('course_name') or s.get('강좌명', '')} "
        f"(ID: {s.get('name_id') or s.get('이름ID', '')})"
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


def find_unpaid_students(
    students: list[dict],
    matched_results: list[dict],
    exempted_ids: set[str] | None = None,
) -> list[dict]:
    """매칭된 결과에 없는 수강생 → 미입금 목록 (정회원 제외)"""
    if exempted_ids is None:
        exempted_ids = set()

    matched_ids = {
        r["매칭ID"]
        for r in matched_results
        if r.get("매칭ID") and r["상태"] == "✅정상"
    }

    unpaid = []
    for s in students:
        name_id = s.get("name_id") or s.get("이름ID", "")
        if name_id in matched_ids or name_id in exempted_ids:
            continue
        unpaid.append({
            "이름ID": name_id,
            "이름": s.get("이름", ""),
            "강좌명": s.get("course_name") or s.get("강좌명", ""),
            "상태": "❌미입금",
        })
    return unpaid


def format_results(
    matched: list[dict],
    unpaid: list[dict],
    exempted: list[dict] | None = None,
) -> str:
    """매칭 결과를 한국어 요약 텍스트로 포맷"""
    if exempted is None:
        exempted = []

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

    if success > 0:
        lines.append("### ✅ 정상 매칭")
        for r in matched:
            if r["상태"] == "✅정상":
                lines.append(
                    f"- {r['매칭이름']} → {r.get('매칭강좌', '?')} ({r['금액분류']})"
                )
        lines.append("")

    if needs_check > 0:
        lines.append("### 🔶 확인 필요")
        for r in matched:
            if r["상태"] == "🔶확인필요":
                lines.append(
                    f"- 적요: \"{r['적요']}\" / 의뢰인: \"{r['의뢰인']}\" / "
                    f"{r['금액분류']} → {r.get('메모', '')}"
                )
        lines.append("")

    if name_mismatch > 0:
        lines.append("### ⚠️ 이름 불일치 (대리입금 추정)")
        for r in matched:
            if r["상태"] == "⚠️이름불일치":
                lines.append(
                    f"- 적요: \"{r['적요']}\" / 의뢰인: \"{r['의뢰인']}\" / "
                    f"{r['금액분류']} → {r.get('메모', '')}"
                )
        lines.append("")

    if exempted:
        lines.append("### 💎 면제 (정회원)")
        for e in exempted:
            lines.append(f"- {e['이름']} ({e['강좌명']})")
        lines.append("")

    if unpaid:
        lines.append("### ❌ 미입금 수강생")
        for u in unpaid:
            lines.append(f"- {u['이름']} ({u['강좌명']})")
        lines.append("")

    unmatched_txs = [r for r in matched if r["상태"] == "❌미매칭"]
    if unmatched_txs:
        lines.append("### ❌ 미매칭 입금 거래 (수동 확인 필요)")
        for r in unmatched_txs:
            lines.append(
                f"- 적요: \"{r['적요']}\" / 의뢰인: \"{r['의뢰인']}\" / "
                f"{r['금액분류']} / {r.get('메모', '')}"
            )
        lines.append("")

    return "\n".join(lines)

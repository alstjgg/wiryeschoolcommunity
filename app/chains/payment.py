"""입금 대조 파이프라인 — Google Sheets SoT

SoT: Google Sheets (통합 신청서, 회원관리)
PostgreSQL은 채팅 기록 전용 (chat_data_layer.py).
"""

import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import (
    ANTHROPIC_API_KEY, LLM_MODEL,
    MEMBERS_SHEET_ID, COURSE_KEYWORDS,
    TUITION_FEE, MEMBERSHIP_FEE, FULL_MEMBERSHIP_FEE,
)
from app.services.google_auth import get_drive_service, get_sheets_service
from app.services.google_drive import find_spreadsheet_by_name, find_or_create_folder
from app.services.google_sheets import read_sheet, write_sheet


# =========================================== 통합 신청서 시트 관리 ====

APPLICATION_HEADER = [
    "이름ID", "이름", "유형", "과목명", "예상금액",
    "입금현황", "등록상태", "입금시간", "입금자명(적요)",
    "전화번호", "주소", "생년월일", "성별", "신청일",
    "시작회차", "종료회차",
]

# 컬럼 인덱스 (0-based)
_COL = {name: i for i, name in enumerate(APPLICATION_HEADER)}


def _app_to_row(app: dict) -> list[str]:
    """신청서 dict → Sheets 행"""
    return [str(app.get(col, "") or "") for col in APPLICATION_HEADER]


def build_applications(
    applicants: list[dict],
    member_signups: list[dict],
    fullmember_signups: list[dict],
) -> list[dict]:
    """수강 신청 + 신규가입 + 정회원가입을 통합 신청서 리스트로 합친다.

    중복 제거: 이름ID + 유형 + 과목명 기준.
    """
    seen = set()
    apps = []

    for a in applicants:
        key = (a["이름ID"], "수강", a["강좌명"])
        if key in seen:
            continue
        seen.add(key)
        apps.append({
            "이름ID": a["이름ID"],
            "이름": a["이름"],
            "유형": "수강",
            "과목명": a["강좌명"],
            "예상금액": str(TUITION_FEE),
            "입금현황": "❌미입금",
            "등록상태": "",
            "입금시간": "",
            "입금자명(적요)": "",
            "전화번호": a.get("전화번호", ""),
            "주소": a.get("주소", ""),
            "생년월일": a.get("생년월일", ""),
            "성별": a.get("성별", ""),
            "신청일": a.get("신청일", ""),
            "시작회차": "",
            "종료회차": "",
        })

    for s in member_signups:
        key = (s["이름ID"], "신규가입", "")
        if key in seen:
            continue
        seen.add(key)
        apps.append({
            **s,
            "예상금액": str(MEMBERSHIP_FEE),
            "입금현황": "❌미입금",
            "등록상태": "",
            "입금시간": "",
            "입금자명(적요)": "",
        })

    for f in fullmember_signups:
        key = (f["이름ID"], "정회원", "")
        if key in seen:
            continue
        seen.add(key)
        apps.append({
            **f,
            "예상금액": str(FULL_MEMBERSHIP_FEE),
            "입금현황": "❌미입금",
            "등록상태": "",
            "입금시간": "",
            "입금자명(적요)": "",
        })

    return apps


def write_applications_sheet(
    term_folder_id: str,
    applications: list[dict],
) -> str:
    """통합 신청서를 Google Sheets에 저장.

    회차 폴더 → '신청서' 서브폴더 → '신청서' 시트.
    기존 시트가 있으면 덮어쓰기, 없으면 생성.
    Returns: spreadsheet_id
    """
    subfolder = find_or_create_folder(term_folder_id, "신청서")
    folder_id = subfolder["id"]

    existing = find_spreadsheet_by_name(folder_id, "신청서")
    rows = [APPLICATION_HEADER] + [_app_to_row(a) for a in applications]

    if existing:
        spreadsheet_id = existing["id"]
        write_sheet(spreadsheet_id, "신청서!A1", rows)
        return spreadsheet_id

    # 새 시트 생성
    drive = get_drive_service()
    file_metadata = {
        "name": "신청서",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [folder_id],
    }
    file = drive.files().create(
        body=file_metadata, fields="id", supportsAllDrives=True
    ).execute()
    spreadsheet_id = file["id"]

    # 기본 탭 이름 변경
    sheets_svc = get_sheets_service()
    meta = sheets_svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    default_sheet_id = meta["sheets"][0]["properties"]["sheetId"]
    sheets_svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [{
                "updateSheetProperties": {
                    "properties": {"sheetId": default_sheet_id, "title": "신청서"},
                    "fields": "title",
                }
            }]
        },
    ).execute()

    write_sheet(spreadsheet_id, "신청서!A1", rows)
    return spreadsheet_id


def read_applications_sheet(spreadsheet_id: str) -> list[dict]:
    """신청서 시트에서 전체 행을 dict 리스트로 읽기"""
    rows = read_sheet(spreadsheet_id, "신청서!A1:P5000")
    if not rows or len(rows) < 2:
        return []
    header = rows[0]
    result = []
    for row in rows[1:]:
        data = dict(zip(header, row + [""] * (len(header) - len(row))))
        result.append(data)
    return result


def update_applications_sheet(
    spreadsheet_id: str,
    applications: list[dict],
) -> None:
    """매칭 결과가 반영된 applications를 시트에 다시 쓴다."""
    rows = [APPLICATION_HEADER] + [_app_to_row(a) for a in applications]
    write_sheet(spreadsheet_id, "신청서!A1", rows)


# ================================================= 회원관리 시트 ====

def load_members_from_sheet() -> list[dict]:
    """회원관리 시트에서 전체 회원 로드"""
    rows = read_sheet(MEMBERS_SHEET_ID, "회원관리!A1:J2000")
    if not rows or len(rows) < 2:
        return []
    header = rows[0]
    return [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in rows[1:]]


def update_members_sheet(members: list[dict]) -> None:
    """회원관리 시트 전체 덮어쓰기"""
    header = ["이름ID", "이름", "성별", "전화번호", "주소", "나이", "등급",
              "수강count", "출석률(누적)", "마지막수강회차"]
    rows = [header]
    for m in members:
        rows.append([
            m.get("이름ID", ""),
            m.get("이름", ""),
            m.get("성별", ""),
            m.get("전화번호", ""),
            m.get("주소", ""),
            m.get("나이", ""),
            m.get("등급", "회원"),
            m.get("수강count", "0"),
            m.get("출석률(누적)", ""),
            m.get("마지막수강회차", ""),
        ])
    write_sheet(MEMBERS_SHEET_ID, "회원관리!A1", rows)


# ======================================= 입금 매칭 관련 함수 ====

def apply_exemptions(
    applications: list[dict],
    members: list[dict],
) -> list[dict]:
    """정회원 선처리: 회원관리에서 등급='정회원' → 해당 수강 행의 입금현황=💎면제"""
    member_grades = {m.get("이름ID", ""): m.get("등급", "") for m in members}
    exempted = []
    for app in applications:
        if app["유형"] != "수강":
            continue
        name_id = app["이름ID"]
        if member_grades.get(name_id) == "정회원":
            app["입금현황"] = "💎면제"
            exempted.append(app)
    return exempted


def applications_to_students(applications: list[dict]) -> list[dict]:
    """통합 신청서에서 수강 유형만 추출하여 matching.py 호환 형식으로 변환"""
    return [
        {
            "이름ID": a["이름ID"],
            "이름": a["이름"],
            "강좌명": a["과목명"],
        }
        for a in applications
        if a["유형"] == "수강"
    ]


def apply_matching_results(
    applications: list[dict],
    matched_results: list[dict],
) -> None:
    """매칭 결과를 applications에 반영 (입금현황, 입금시간, 입금자명)"""
    # 이름ID + 과목명 → application 인덱스 매핑
    app_index = {}
    for i, app in enumerate(applications):
        if app["유형"] == "수강":
            app_index[(app["이름ID"], app["과목명"])] = i

    for r in matched_results:
        if r["상태"] == "⏭️스킵":
            continue
        matched_id = r.get("매칭ID")
        matched_course = r.get("매칭강좌", "")
        if not matched_id:
            continue

        key = (matched_id, matched_course)
        if key not in app_index:
            # 강좌 없이 이름ID만으로 시도
            for k, idx in app_index.items():
                if k[0] == matched_id and applications[idx]["입금현황"] == "❌미입금":
                    key = k
                    break

        if key in app_index:
            idx = app_index[key]
            applications[idx]["입금현황"] = r["상태"]
            applications[idx]["입금시간"] = r.get("거래일시", "")
            applications[idx]["입금자명(적요)"] = r.get("적요", "")


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
        f"- {s['이름']} / {s['강좌명']} (ID: {s['이름ID']})"
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


def find_unpaid(applications: list[dict]) -> list[dict]:
    """입금현황이 ❌미입금인 수강 행"""
    return [
        a for a in applications
        if a["유형"] == "수강" and a["입금현황"] == "❌미입금"
    ]


def format_results(
    matched: list[dict],
    applications: list[dict],
    exempted: list[dict] | None = None,
) -> str:
    """매칭 결과를 한 줄 숫자 요약으로 포맷"""
    if exempted is None:
        exempted = []

    success = sum(1 for r in matched if r["상태"] == "✅정상")
    needs_check = sum(1 for r in matched if r["상태"] == "🔶확인필요")
    name_mismatch = sum(1 for r in matched if r["상태"] == "⚠️이름불일치")
    unpaid = find_unpaid(applications)

    parts = [f"✅ {success}건"]
    if needs_check:
        parts.append(f"🔶 {needs_check}건")
    if name_mismatch:
        parts.append(f"⚠️ {name_mismatch}건")
    if unpaid:
        parts.append(f"❌ {len(unpaid)}건")
    if exempted:
        parts.append(f"💎 {len(exempted)}건")

    return "  ".join(parts)

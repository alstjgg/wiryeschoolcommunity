"""입금 대조 파이프라인 — DB SoT + 백그라운드 Sheets 동기화

PostgreSQL이 SoT. DB 쓰기 후 sheets_sync.py로 백그라운드 Sheets 동기화.
"""

import json
import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import (
    ANTHROPIC_API_KEY, LLM_MODEL,
    MEMBERS_SHEET_ID,
    TUITION_FEE, MEMBERSHIP_FEE, FULL_MEMBERSHIP_FEE,
    INSTRUCTOR_SHEET_ID, STAFF_SHEET_ID,
)
from app.services.google_sheets import read_sheet

logger = logging.getLogger(__name__)


# =========================================== 통합 신청서 시트 관리 ====

APPLICATION_HEADER = [
    "신청일", "회차", "이름ID", "이름", "유형", "과목명",
    "예상금액", "입금시간", "입금자명(적요)", "입금현황",
    "확인사유", "처리상태",
]

def _app_to_row(app: dict) -> list[str]:
    """신청서 dict → Sheets 행"""
    return [str(app.get(col, "") or "") for col in APPLICATION_HEADER]


def build_applications(
    applicants: list[dict],
    member_signups: list[dict],
    fullmember_signups: list[dict],
    term_id: str = "",
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
            "신청일": a.get("신청일", ""),
            "회차": term_id,
            "이름ID": a["이름ID"],
            "이름": a["이름"],
            "유형": "수강",
            "과목명": a["강좌명"],
            "예상금액": str(TUITION_FEE),
            "입금시간": "",
            "입금자명(적요)": "",
            "입금현황": "❌미입금",
            "확인사유": "",
            "처리상태": "",
            "전화번호": a.get("전화번호", ""),
            "주소": a.get("주소", ""),
        })

    for s in member_signups:
        key = (s["이름ID"], "신규가입", "")
        if key in seen:
            continue
        seen.add(key)
        apps.append({
            **s,
            "회차": term_id,
            "예상금액": str(MEMBERSHIP_FEE),
            "입금현황": "❌미입금",
            "확인사유": "",
            "처리상태": "",
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
            "회차": term_id,
            "예상금액": str(FULL_MEMBERSHIP_FEE),
            "입금현황": "❌미입금",
            "확인사유": "",
            "처리상태": "",
            "입금시간": "",
            "입금자명(적요)": "",
        })

    return apps


# ======================================= 강사/사무처 면제 대상 판별 ====

def get_cycle_year(term_id: str) -> int:
    """회차 → 사이클 연도. 사이클 = YY-2(봄) ~ YY+1-1(다음해 겨울)."""
    year, num = int(term_id.split("-")[0]), int(term_id.split("-")[1])
    return year - 1 if num == 1 else year


def _parse_ym_to_cycle_year(ym: str) -> int | None:
    """'YY.MM' 또는 'YYYY.MM' → 소속 사이클 연도.

    예: '25.03' → 2025-1 → 사이클 2024
        '25.05' → 2025-2 → 사이클 2025
    """
    try:
        parts = ym.split(".")
        year = int(parts[0])
        month = int(parts[1])
        if year < 100:
            year += 2000
        term_num = (month - 1) // 3 + 1
        return year - 1 if term_num == 1 else year
    except (ValueError, IndexError):
        return None


def get_exception_ids(term_id: str) -> set[str]:
    """강사관리 + 사무처관리 시트에서 현재 사이클 면제 대상 이름ID 추출.

    입금 대조 시 1회만 호출. SoT가 Sheet이므로 DB에 저장하지 않음.
    """
    exception_ids: set[str] = set()
    cycle_year = get_cycle_year(term_id)
    cycle_terms = {
        f"{cycle_year}-2", f"{cycle_year}-3",
        f"{cycle_year}-4", f"{cycle_year + 1}-1",
    }

    # 강사: 현재 사이클에 강의 row가 있는 강사
    try:
        rows = read_sheet(INSTRUCTOR_SHEET_ID, "Sheet1!A1:F1000")
        if rows and len(rows) >= 2:
            header = rows[0]
            for row in rows[1:]:
                data = dict(zip(header, row + [""] * (len(header) - len(row))))
                if data.get("강의회차", "").strip() in cycle_terms:
                    name_id = data.get("이름ID", "").strip()
                    if name_id:
                        exception_ids.add(name_id)
    except Exception as e:
        logger.warning("강사관리 시트 읽기 실패: %s", e)

    # 직원: 활동종료가 비어있거나 종료 시점이 현재 사이클 이후
    try:
        rows = read_sheet(STAFF_SHEET_ID, "Sheet1!A1:G100")
        if rows and len(rows) >= 2:
            header = rows[0]
            for row in rows[1:]:
                data = dict(zip(header, row + [""] * (len(header) - len(row))))
                name_id = data.get("이름ID", "").strip()
                if not name_id:
                    continue
                end = data.get("활동종료", "").strip()
                if not end:
                    exception_ids.add(name_id)
                else:
                    end_cycle = _parse_ym_to_cycle_year(end)
                    if end_cycle is not None and end_cycle >= cycle_year:
                        exception_ids.add(name_id)
    except Exception as e:
        logger.warning("사무처관리 시트 읽기 실패: %s", e)

    return exception_ids


def get_active_staff_ids() -> set[str]:
    """사무처관리 시트에서 활동 중인 직원 이름ID 추출 (종강 처리용)."""
    active: set[str] = set()
    try:
        rows = read_sheet(STAFF_SHEET_ID, "Sheet1!A1:G100")
        if rows and len(rows) >= 2:
            header = rows[0]
            for row in rows[1:]:
                data = dict(zip(header, row + [""] * (len(header) - len(row))))
                if not data.get("활동종료", "").strip():
                    name_id = data.get("이름ID", "").strip()
                    if name_id:
                        active.add(name_id)
    except Exception as e:
        logger.warning("사무처관리 시트 읽기 실패: %s", e)
    return active


# ============================================ Grade Cascade (등급 전환) ====

def apply_grade_cascade(
    applications: list[dict],
    members: list[dict],
    term_id: str = "",
    exception_ids: set[str] | None = None,
) -> list[dict]:
    """확정된 입금 건에 대해 등급 전환을 순서대로 적용. Idempotent.

    호출할 때마다 전체를 재평가. 이미 처리된 건은 skip.

    3-Pass 순서:
      Pass 1: 신규가입 confirmed/면제 → 비회원을 회원으로 등록
      Pass 2: 정회원 confirmed/면제 → 회원을 정회원으로 승급
      Pass 3: 수강 → 정회원이면 면제, 아니면 준회원 승급

    exception_ids: 강사/사무처 면제 대상 이름ID set. 해당 대상은 가입비+정회원비+수강비 전부 면제.

    Returns: 등급 변경 기록 리스트 (회원기록 탭에 append할 데이터)
    """
    from datetime import datetime, timezone
    now_dt = datetime.now(timezone.utc)

    if exception_ids is None:
        exception_ids = set()

    # 이름ID → member dict 매핑
    member_map: dict[str, dict] = {m["이름ID"]: m for m in members}
    changes: list[dict] = []

    # Pass 1: 신규가입 → 회원 등록
    for app in applications:
        if app.get("유형") != "신규가입":
            continue
        name_id = app["이름ID"]
        is_exc = name_id in exception_ids

        if is_exc:
            app["입금현황"] = "💎면제"
            app["확인사유"] = "강사/사무처 면제"
        elif app.get("입금현황") != "✅정상":
            continue

        if name_id in member_map:
            if is_exc:
                member_map[name_id]["예외여부"] = "TRUE"
            continue  # 이미 회원

        # 비회원 → 회원 등록
        new_member = {
            "이름ID": name_id,
            "이름": app["이름"],
            "전화번호": app.get("전화번호", ""),
            "주소": app.get("주소", ""),
            "등급": "회원",
            "예외여부": "TRUE" if is_exc else "",
            "수강count": "0",
            "출석률(누적)": "",
            "마지막수강회차": "",
        }
        member_map[name_id] = new_member
        members.append(new_member)
        changes.append({
            "이름ID": name_id, "이름": app["이름"],
            "변경일시": now_dt,
            "변경전등급": "(신규)", "변경후등급": "회원",
            "사유": "신규가입(강사/사무처면제)" if is_exc else "신규가입",
            "관련회차": term_id,
        })

    # Pass 2: 정회원 → 정회원 승급
    for app in applications:
        if app.get("유형") != "정회원":
            continue
        name_id = app["이름ID"]
        is_exc = name_id in exception_ids

        if is_exc:
            app["입금현황"] = "💎면제"
            app["확인사유"] = "강사/사무처 면제"
        elif app.get("입금현황") != "✅정상":
            continue

        member = member_map.get(name_id)

        if not member:
            # 회원이 아님 — 신규가입 먼저 필요
            if not is_exc:
                app["입금현황"] = "🔶확인필요"
                app["확인사유"] = "회원 아님 — 신규가입 먼저 필요"
            continue

        if member.get("등급") == "정회원":
            if is_exc:
                member["예외여부"] = "TRUE"
            continue  # 이미 정회원

        prev_grade = member.get("등급", "회원")
        member["등급"] = "정회원"
        if is_exc:
            member["예외여부"] = "TRUE"
        changes.append({
            "이름ID": name_id, "이름": app["이름"],
            "변경일시": now_dt,
            "변경전등급": prev_grade, "변경후등급": "정회원",
            "사유": "정회원비면제(강사/사무처)" if is_exc else "정회원비입금",
            "관련회차": term_id,
        })

    # Pass 3: 수강 → 면제 or 준회원 승급
    for app in applications:
        if app.get("유형") != "수강":
            continue
        name_id = app["이름ID"]
        member = member_map.get(name_id)

        # 정회원이면 수강료 면제 (기존 ✅정상도 💎면제로 변환)
        if member and member.get("등급") == "정회원":
            if app.get("입금현황") != "💎면제":
                app["입금현황"] = "💎면제"
            continue

        # 정회원 신청 중 + 정회원비 미입금 → 보류
        has_pending_full = any(
            a.get("유형") == "정회원"
            and a.get("이름ID") == name_id
            and a.get("입금현황") not in ("✅정상", "💎면제")
            for a in applications
        )
        if has_pending_full and app.get("입금현황") == "✅정상":
            app["입금현황"] = "🔶확인필요"
            app["확인사유"] = "정회원 신청 중 — 수강비만 입금됨"
            continue

        # 일반 수강료 매칭 확정 → 준회원 승급
        if app.get("입금현황") == "✅정상" and member:
            current_grade = member.get("등급", "")
            if current_grade not in ("준회원", "정회원"):
                member["등급"] = "준회원"
                changes.append({
                    "이름ID": name_id, "이름": app["이름"],
                    "변경일시": now_dt,
                    "변경전등급": current_grade or "회원",
                    "변경후등급": "준회원",
                    "사유": "수강료입금", "관련회차": term_id,
                })

    return changes


# ==================================== 공개 API (DB SoT + Sheets sync) ====

async def write_applications_sheet(
    term_folder_id: str,
    applications: list[dict],
    term_id: str = "",
) -> str:
    """통합 신청서 upsert → DB + Sheets sync. Returns MEMBERS_SHEET_ID."""
    from app.services import db
    from app.services.sheets_sync import sync_to_sheets

    await db.upsert_applications(term_id, applications)
    await sync_to_sheets("applications", data=applications, term_id=term_id)
    return MEMBERS_SHEET_ID


async def read_applications_sheet(
    spreadsheet_id: str,
    term_id: str = "",
) -> list[dict]:
    """신청서 읽기 — DB에서."""
    from app.services import db
    return await db.load_applications(term_id)


async def update_applications_sheet(
    spreadsheet_id: str,
    applications: list[dict],
    term_id: str = "",
) -> None:
    """매칭 결과 반영 → DB upsert + Sheets sync."""
    from app.services import db
    from app.services.sheets_sync import sync_to_sheets

    await db.upsert_applications(term_id, applications)
    await sync_to_sheets("applications", data=applications, term_id=term_id)


async def load_members_from_sheet() -> list[dict]:
    """회원목록 로드 — DB에서."""
    from app.services import db
    return await db.load_members()


async def update_members_sheet(members: list[dict]) -> None:
    """회원목록 덮어쓰기 → DB upsert + Sheets sync."""
    from app.services import db
    from app.services.sheets_sync import sync_to_sheets

    await db.upsert_members(members)
    await sync_to_sheets("members", data=members)


async def append_member_records(records: list[dict]) -> None:
    """회원기록 등급 변경 이력 → DB insert + Sheets sync."""
    if not records:
        return
    from app.services import db
    from app.services.sheets_sync import sync_to_sheets

    await db.insert_member_records(records)
    await sync_to_sheets("member_records", data=records)


async def append_course_records(records: list[dict]) -> None:
    """수강기록 이력 → DB insert + Sheets sync."""
    if not records:
        return
    from app.services import db
    from app.services.sheets_sync import sync_to_sheets

    await db.insert_course_records(records)
    await sync_to_sheets("course_records", data=records)


# ======================================= 입금 매칭 관련 함수 ====

def apply_exemptions(
    applications: list[dict],
    members: list[dict],
    exception_ids: set[str] | None = None,
) -> list[dict]:
    """면제 선처리.

    1. 기존 정회원 → 수강 행 💎면제
    2. 강사/사무처 면제 대상 → 모든 유형(신규가입/정회원/수강) 💎면제
       (cascade에서 등급 승급은 별도 처리)
    """
    if exception_ids is None:
        exception_ids = set()
    member_grades = {m.get("이름ID", ""): m.get("등급", "") for m in members}
    exempted = []
    for app in applications:
        name_id = app["이름ID"]
        is_exc = name_id in exception_ids

        if is_exc:
            # 강사/사무처 면제 — 모든 유형 (매칭 전에 💎면제 설정)
            app["입금현황"] = "💎면제"
            app["확인사유"] = "강사/사무처 면제"
            exempted.append(app)
        elif app["유형"] == "수강" and member_grades.get(name_id) == "정회원":
            # 기존 정회원 수강료 면제
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
) -> int:
    """매칭 결과를 applications에 반영 (입금현황, 입금시간, 입금자명).

    강좌 지정 건을 먼저 처리하고, 이름만 있는 건은 남은 미입금 슬롯에 할당.

    각 matched_result에 deposit 추적 메타데이터를 태그:
    - _matched = True/False (매칭 성공 여부)
    - _matched_name_ids = [name_id] (매칭된 이름ID 목록)

    Returns: 매칭되지 않은 deposit 수 (스킵 제외)
    """
    # 이름ID + 과목명 → application 인덱스 매핑
    app_index: dict[tuple[str, str], int] = {}
    for i, app in enumerate(applications):
        if app["유형"] == "수강":
            app_index[(app["이름ID"], app["과목명"])] = i

    def _apply(r: dict) -> bool:
        """단일 결과를 application에 반영. 성공 시 True."""
        matched_id = r.get("매칭ID")
        matched_course = r.get("매칭강좌", "")
        if not matched_id:
            return False

        key = (matched_id, matched_course)
        if key not in app_index:
            # 강좌 없이 이름ID만으로 남은 미입금 슬롯 찾기
            for k, idx in app_index.items():
                if k[0] == matched_id and applications[idx]["입금현황"] == "❌미입금":
                    key = k
                    break

        if key in app_index:
            idx = app_index[key]
            if applications[idx]["입금현황"] != "❌미입금":
                # 이미 처리된 슬롯 → 다른 미입금 슬롯 탐색
                for k, i2 in app_index.items():
                    if k[0] == matched_id and applications[i2]["입금현황"] == "❌미입금":
                        idx = i2
                        break
                else:
                    return False
            applications[idx]["입금현황"] = r["상태"]
            applications[idx]["입금시간"] = r.get("거래일시", "")
            applications[idx]["입금자명(적요)"] = r.get("적요", "")
            r["_matched"] = True
            r["_matched_name_ids"] = [applications[idx].get("이름ID", "")]
            return True
        return False

    # 1차: 강좌 지정 건 먼저 (정확한 슬롯 대상)
    # 2차: 이름만 있는 건 (남은 미입금 슬롯에 순서대로)
    with_course = [r for r in matched_results if r.get("매칭강좌") and r["상태"] != "⏭️스킵"]
    without_course = [r for r in matched_results if not r.get("매칭강좌") and r["상태"] != "⏭️스킵"]

    unmatched_count = 0
    for r in with_course + without_course:
        if not r.get("매칭ID"):
            r["_matched"] = False
            unmatched_count += 1
            continue
        if not _apply(r):
            r["_matched"] = False
            unmatched_count += 1

    return unmatched_count


_LLM_BATCH_SIZE = 20  # 한 번에 LLM에 보내는 최대 건수


async def run_llm_matching(needs_llm: list[dict], students: list[dict]) -> list[dict]:
    """LLM으로 강좌 특정 — 배치 분할 호출.

    룰베이스가 이름 추출까지 완료한 건의 과목만 매칭.
    20건씩 배치로 나눠서 호출하여 max_tokens 초과 및 JSON 문법 오류를 방지.
    한 배치가 실패해도 다음 배치는 계속 진행.
    """
    if not needs_llm:
        return []

    llm = ChatAnthropic(
        model=LLM_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=4096,
    )

    system_prompt = """당신은 위례인생학교의 입금 대조 보조 AI입니다.
각 거래에 대해 수강생의 과목 목록과 적요 힌트를 제공합니다.
적요에서 추출된 강좌 힌트를 해당 수강생의 과목 목록과 대조하여 어느 과목인지 특정해주세요.

## 적요 패턴
- "이름+강좌약어" 형태가 많음 (예: "경제뉴스로기초" → "경제뉴스로 배우는 경제해설(기초)")
- 역순("마음챙김명상" → "나, 마음챙김 명상"), 줄임말("경제심" → 심화), 오타 가능
- 힌트가 비어있으면 금액/맥락으로 추정하되, 확신 없으면 "🔶확인필요"

## 응답 형식
JSON 배열. 각 항목:
[
  {
    "index": 1,
    "매칭강좌": "정확한 과목명" 또는 null,
    "상태": "✅정상" 또는 "🔶확인필요",
    "메모": "판단 근거"
  }
]
JSON만 응답하세요."""

    num_batches = -(-len(needs_llm) // _LLM_BATCH_SIZE)
    logger.info("LLM matching input: %d items, %d batches", len(needs_llm), num_batches)

    for batch_start in range(0, len(needs_llm), _LLM_BATCH_SIZE):
        batch = needs_llm[batch_start:batch_start + _LLM_BATCH_SIZE]
        batch_end = batch_start + len(batch)

        tx_lines = []
        for i, tx in enumerate(batch):
            ctx = tx.get("_llm_context", {})
            name = ctx.get("matched_name", tx.get("매칭이름", "?"))
            hint = ctx.get("course_hint", "")
            courses = ctx.get("candidate_courses", [])
            amount_info = ctx.get("amount_info", {})
            paid = amount_info.get("paid_courses", 1)
            tx_lines.append(
                f"{i+1}. 수강생: {name} / 힌트: '{hint}' / "
                f"과목: {courses} / 금액: {tx['입금']:,}원({paid}과목)"
            )
        tx_text = "\n".join(tx_lines)
        user_prompt = f"다음 거래들의 과목을 특정해주세요:\n\n{tx_text}"

        try:
            response = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
        except Exception as e:
            logger.error("LLM API call failed (batch %d~%d): %s", batch_start, batch_end, e)
            continue

        content = response.content.strip()
        logger.info("LLM batch %d~%d response: %s", batch_start, batch_end, content[:500])

        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            llm_results = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error("LLM JSON parse failed (batch %d~%d): %s / response: %s",
                         batch_start, batch_end, e, content[:500])
            continue

        for llm_item in llm_results:
            idx = llm_item.get("index", 0) - 1
            if 0 <= idx < len(batch):
                tx = batch[idx]
                course = llm_item.get("매칭강좌")
                if course:
                    tx["매칭강좌"] = course
                    for s in students:
                        if s["이름"] == tx.get("매칭이름") and s["강좌명"] == course:
                            tx["매칭ID"] = s["이름ID"]
                            break
                tx["상태"] = llm_item.get("상태", "🔶확인필요")
                tx["메모"] = llm_item.get("메모", tx.get("메모", ""))
                tx.pop("_llm_context", None)

    # 실패한 배치의 잔여 _llm_context 정리
    for tx in needs_llm:
        tx.pop("_llm_context", None)

    return needs_llm


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
    unmatched_deposits: int = 0,
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

    summary = "  ".join(parts)
    if unmatched_deposits:
        summary += f"  | 미확인입금: {unmatched_deposits}건"
    return summary

"""입금 대조 파이프라인 — DB SoT + 백그라운드 Sheets 동기화

PostgreSQL이 SoT. DB 쓰기 후 sheets_sync.py로 백그라운드 Sheets 동기화.
"""

import asyncio
import json
import logging
from typing import Literal, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

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
    "회차", "이름ID", "이름", "유형", "과목명",
    "예상금액", "입금액", "입금시간", "의뢰인", "적요",
    "입금현황", "확인사유", "처리상태",
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
            "회차": term_id,
            "이름ID": a["이름ID"],
            "이름": a["이름"],
            "유형": "수강",
            "과목명": a["강좌명"],
            "예상금액": str(TUITION_FEE),
            "입금액": "",
            "입금시간": "",
            "의뢰인": "",
            "적요": "",
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
            "입금액": "",
            "입금시간": "",
            "의뢰인": "",
            "적요": "",
            "입금현황": "❌미입금",
            "확인사유": "",
            "처리상태": "",
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
            "입금액": "",
            "입금시간": "",
            "의뢰인": "",
            "적요": "",
            "입금현황": "❌미입금",
            "확인사유": "",
            "처리상태": "",
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

# 재실행 시 보존 + 매칭 제외 기준
FINALIZED_PROCESSING = {"등록완료", "환불완료", "취소완료", "보류"}
PRESERVED_PAYMENT = {"✅정상", "💎면제"}


def _is_preserved(app: dict) -> bool:
    """이 신청 건이 재실행 시 보존 대상인지 판단."""
    ps = (app.get("처리상태") or "").strip()
    payment = app.get("입금현황", "❌미입금")
    return ps in FINALIZED_PROCESSING or payment in PRESERVED_PAYMENT


async def merge_with_existing_applications(
    term_id: str,
    applications: list[dict],
) -> list[dict]:
    """새로 생성한 신청서 리스트에 기존 DB 데이터를 병합.

    보존 기준 (_is_preserved):
    - 처리상태가 확정/보류(등록완료/환불완료/취소완료/보류) → 결제 필드 보존
    - 입금현황이 ✅정상 또는 💎면제 → 결제 필드 보존
    - 그 외 → 결제 필드 리셋(❌미입금)하여 재매칭 대상으로 전환
    - 처리상태는 위 조건과 무관하게 항상 보존 (관리자 직접 편집 값)
    """
    from app.services import db

    existing = await db.load_applications(term_id)
    if not existing:
        return applications

    # 기존 데이터를 key로 인덱싱
    existing_map: dict[tuple, dict] = {}
    for e in existing:
        key = (e["이름ID"], e["유형"], e.get("과목명", ""))
        existing_map[key] = e

    PAYMENT_FIELDS = [
        "입금액", "입금시간", "의뢰인", "적요",
        "입금현황", "확인사유",
    ]

    for app in applications:
        key = (app["이름ID"], app["유형"], app.get("과목명", ""))
        prev = existing_map.get(key)
        if not prev:
            continue

        prev_ps = (prev.get("처리상태") or "").strip()

        # 처리상태는 항상 보존
        if prev_ps:
            app["처리상태"] = prev_ps

        # 보존 대상 → 결제 필드 전체 보존
        if _is_preserved(prev):
            for field in PAYMENT_FIELDS:
                if prev.get(field):
                    app[field] = prev[field]

        # 그 외 → ❌미입금으로 리셋 (재매칭 대상)

    return applications


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


def all_application_names(applications: list[dict]) -> list[str]:
    """전체 신청서에서 이름 목록 추출 (수강+신규가입+정회원 모두 포함).

    이름 추출(extract_name)에 사용. 긴 이름부터 정렬.
    신규가입/정회원 이름이 포함되어야 가입비/정회원비 deposit이 매칭됨.
    """
    return sorted(
        {a["이름"] for a in applications if a.get("이름")},
        key=len, reverse=True,
    )


def apply_matching_results(
    applications: list[dict],
    matched_results: list[dict],
) -> int:
    """매칭 결과를 applications에 반영 — type별 인덱스로 정확한 슬롯 배정.

    3개 인덱스: 수강(이름ID+과목명), 신규가입(이름ID), 정회원(이름ID).
    강좌 지정 건 먼저 처리, 이름만 있는 건은 남은 미입금 슬롯에 할당.

    각 matched_result에 deposit 추적 메타데이터를 태그:
    - _matched = True/False (매칭 성공 여부)
    - _matched_name_ids = [name_id] (매칭된 이름ID 목록)

    Returns: 매칭되지 않은 deposit 수 (스킵 제외)
    """
    # 수강: (이름ID, 과목명) → index
    tuition_index: dict[tuple[str, str], int] = {}
    for i, app in enumerate(applications):
        if app["유형"] == "수강":
            tuition_index[(app["이름ID"], app["과목명"])] = i

    # 신규가입: 이름ID → index
    membership_index: dict[str, int] = {}
    for i, app in enumerate(applications):
        if app["유형"] == "신규가입":
            membership_index[app["이름ID"]] = i

    # 정회원: 이름ID → index
    fullmember_index: dict[str, int] = {}
    for i, app in enumerate(applications):
        if app["유형"] == "정회원":
            fullmember_index[app["이름ID"]] = i

    def _fill_app(idx: int, r: dict) -> None:
        """application 슬롯에 매칭 결과 반영."""
        applications[idx]["입금현황"] = r["상태"]
        applications[idx]["입금시간"] = r.get("거래일시", "")
        applications[idx]["입금액"] = str(r.get("입금", ""))
        applications[idx]["의뢰인"] = r.get("의뢰인", "")
        applications[idx]["적요"] = r.get("적요", "")
        applications[idx]["확인사유"] = r.get("메모", "")

    def _apply(r: dict) -> bool:
        """단일 결과를 application에 반영. 성공 시 True."""
        matched_id = r.get("매칭ID")
        matched_course = r.get("매칭강좌", "")
        match_type = r.get("_match_type", "수강")
        if not matched_id:
            return False

        # 신규가입 → membership_index
        if match_type == "신규가입":
            idx = membership_index.get(matched_id)
            if idx is not None and applications[idx]["입금현황"] == "❌미입금":
                _fill_app(idx, r)
                r["_matched"] = True
                r["_matched_name_ids"] = [matched_id]
                # 합산 입금(가입비+정회원비) → 정회원 슬롯도 동시 매칭
                if r.get("_amount_type") == "membership_plus_fullmember":
                    fm_idx = fullmember_index.get(matched_id)
                    if fm_idx is not None and applications[fm_idx]["입금현황"] == "❌미입금":
                        _fill_app(fm_idx, r)
                return True
            return False

        # 정회원 → fullmember_index
        if match_type == "정회원":
            idx = fullmember_index.get(matched_id)
            if idx is not None and applications[idx]["입금현황"] == "❌미입금":
                _fill_app(idx, r)
                r["_matched"] = True
                r["_matched_name_ids"] = [matched_id]
                return True
            return False

        # 수강 — tuition_index

        # 전과목 합산: 해당 이름ID의 모든 미입금 수강 슬롯에 한번에 배정
        if r.get("상태") == "✅정상" and "전과목합산" in r.get("메모", ""):
            applied_any = False
            for k, idx in tuition_index.items():
                if k[0] == matched_id and applications[idx]["입금현황"] == "❌미입금":
                    _fill_app(idx, r)
                    applied_any = True
            if applied_any:
                r["_matched"] = True
                r["_matched_name_ids"] = [matched_id]
                return True
            return False

        # 정확한 (이름ID, 강좌) 키로 슬롯 찾기
        key = (matched_id, matched_course)
        if key not in tuition_index:
            # 매칭강좌 우선 탐색 → 이름 fallback 2단계
            found = False
            if matched_course:
                for k, idx in tuition_index.items():
                    if k[0] == matched_id and k[1] == matched_course and applications[idx]["입금현황"] == "❌미입금":
                        key = k
                        found = True
                        break
            if not found:
                for k, idx in tuition_index.items():
                    if k[0] == matched_id and applications[idx]["입금현황"] == "❌미입금":
                        key = k
                        break

        if key in tuition_index:
            idx = tuition_index[key]
            if applications[idx]["입금현황"] != "❌미입금":
                # 이미 처리된 슬롯 → 다른 미입금 슬롯 탐색
                for k, i2 in tuition_index.items():
                    if k[0] == matched_id and applications[i2]["입금현황"] == "❌미입금":
                        idx = i2
                        break
                else:
                    return False
            _fill_app(idx, r)
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


class TransactionMatch(BaseModel):
    """LLM이 적요에서 추출한 과목 매칭 결과"""
    matched_course: Optional[str] = Field(
        None,
        description="수강생의 과목 목록 중 적요에 해당하는 정확한 과목명. 특정 불가하면 null.",
    )
    status: Literal["confirmed", "needs_review"] = Field(
        description="'confirmed' = 확실한 매칭, 'needs_review' = 모호하거나 확신 없음",
    )
    memo: str = Field(
        default="",
        description="판단 근거를 간단히 설명",
    )


_LLM_CONCURRENCY = 1  # Tier 1 RPM 50 제한 대응 (Agent 호출 + 매칭 합산)


async def run_llm_matching(needs_llm: list[dict], students: list[dict]) -> list[dict]:
    """LLM으로 과목 추출 — with_structured_output + 병렬 호출.

    LLM은 추출만 담당: 적요에서 과목 약칭을 찾아 과목 목록과 매핑.
    매칭 판정(슬롯 배정)은 apply_matching_results가 담당.
    """
    if not needs_llm:
        return []

    llm = ChatAnthropic(
        model=LLM_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=1024,
    )
    structured_llm = llm.with_structured_output(TransactionMatch)

    system_prompt = """당신은 입금 적요에서 과목 정보를 추출하는 AI입니다.

## 입력
각 거래에 대해 적요, 의뢰인, 금액, 수강생의 과목 목록을 제공합니다.

## 추출 규칙
1. 적요에서 과목 약칭을 찾고, 과목 목록의 정확한 과목명으로 매핑하세요.
   - "경제뉴스로기초" → "경제뉴스로 배우는 경제해설(기초)"
   - "올인원영어" → "다시 시작하는 All In One 영어"
   - "어반스캐치" (오타) → "어반스케치"
   - "경제심" → "경제뉴스로 배우는 경제해설(심화)"
2. 과목 약칭이 과목 목록의 여러 과목에 해당할 수 있으면 → status="needs_review"
   예: "경제"만으로는 "금융과 경제" vs "경제뉴스로 배우는 경제해설" 구분 불가
3. matched_course는 반드시 과목 목록에 있는 정확한 과목명이어야 합니다. 없으면 null.
4. 적요에 과목 힌트가 전혀 없으면 matched_course=null, status="needs_review"."""

    logger.info("LLM matching input: %d items (structured output, concurrency=%d)",
                len(needs_llm), _LLM_CONCURRENCY)

    semaphore = asyncio.Semaphore(_LLM_CONCURRENCY)

    async def process_one(i: int, tx: dict) -> tuple[int, TransactionMatch | None]:
        ctx = tx.get("_llm_context", {})
        name = ctx.get("matched_name", tx.get("매칭이름", "?"))
        hint = ctx.get("course_hint", "")
        courses = ctx.get("candidate_courses", [])

        user_prompt = (
            f"수강생: {name}\n"
            f"적요: '{tx.get('적요', '')}'\n"
            f"의뢰인: '{tx.get('의뢰인', '')}'\n"
            f"금액: {tx['입금']:,}원\n"
            f"과목 목록: {courses}"
        )

        async with semaphore:
            await asyncio.sleep(1.5)  # Tier 1 RPM 50 안전 마진
            try:
                result = await structured_llm.ainvoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ])
                return (i, result)
            except Exception as e:
                logger.error("LLM structured call failed (item %d, %s): %s", i, name, e)
                return (i, None)

    tasks = [process_one(i, tx) for i, tx in enumerate(needs_llm)]
    results = await asyncio.gather(*tasks)

    resolved = 0
    for i, result in results:
        tx = needs_llm[i]
        if result is None:
            # LLM 실패 → 슬롯 배정 방지 (잘못된 슬롯보다 미확인입금이 나음)
            tx["매칭ID"] = None
            tx.pop("_llm_context", None)
            continue

        course = result.matched_course
        if course:
            tx["매칭강좌"] = course
            for s in students:
                if s["이름"] == tx.get("매칭이름") and s["강좌명"] == course:
                    tx["매칭ID"] = s["이름ID"]
                    break

        tx["상태"] = "✅정상" if result.status == "confirmed" else "🔶확인필요"
        tx["메모"] = result.memo
        tx.pop("_llm_context", None)
        if result.status == "confirmed":
            resolved += 1

    logger.info("LLM matching completed: %d/%d resolved", resolved, len(needs_llm))

    # LLM 호출 실패 건의 잔여 _llm_context 정리
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

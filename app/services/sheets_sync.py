"""Background Sheets sync — DB SoT 모드에서 DB 쓰기 후 Sheets를 백그라운드로 동기화.

n8n 웹훅 대신 chatbot에서 직접 Sheets API를 호출한다.
asyncio.to_thread()로 동기 Sheets API를 백그라운드 스레드에서 실행 (fire-and-forget).
"""

import asyncio
import logging

from app.config import (
    USE_DB_SOT,
    MEMBERS_SHEET_ID,
    MEMBERS_TAB,
    MEMBER_RECORDS_TAB,
    COURSE_RECORDS_TAB,
    APPLICATIONS_TAB,
    UNMATCHED_DEPOSITS_TAB,
    MEMBER_RECORD_HEADER,
    COURSE_RECORD_HEADER,
)
from app.services.google_sheets import clear_range, write_sheet, append_sheet

logger = logging.getLogger(__name__)

# 신청서 헤더 (payment.py APPLICATION_HEADER와 동일)
_APP_HEADER = [
    "신청일", "회차", "이름ID", "이름", "유형", "과목명",
    "예상금액", "입금시간", "입금자명(적요)", "입금현황",
    "확인사유", "처리상태",
]

_MEMBERS_HEADER = [
    "이름ID", "이름", "전화번호", "주소", "등급", "예외여부",
    "수강count", "출석률(누적)", "마지막수강회차",
]

_DEPOSITS_HEADER = [
    "입금일시", "회차", "입금액", "입금자명", "적요", "확인사유", "처리상태",
]

# DB column → Sheets column mapping for deposits
_DEPOSIT_COLS = [
    "transaction_time", "term_id", "amount", "payer_name",
    "memo", "review_reason", "processing_status",
]


# ── Sync functions (synchronous, run in background thread) ────────────────


def _sync_applications(applications: list[dict]) -> None:
    """신청기록 탭 전체 덮어쓰기 (clear A2:L + write A2)."""
    rows = [
        [str(a.get(col, "") or "") for col in _APP_HEADER]
        for a in applications
    ]
    clear_range(MEMBERS_SHEET_ID, f"{APPLICATIONS_TAB}!A2:L")
    if rows:
        write_sheet(MEMBERS_SHEET_ID, f"{APPLICATIONS_TAB}!A2", rows)


def _sync_members(members: list[dict]) -> None:
    """회원목록 탭 전체 덮어쓰기 (clear A2:I + write A2)."""
    rows = [
        [
            m.get("이름ID", ""),
            m.get("이름", ""),
            m.get("전화번호", ""),
            m.get("주소", ""),
            m.get("등급", "회원"),
            m.get("예외여부", ""),
            m.get("수강count", "0"),
            m.get("출석률(누적)", ""),
            m.get("마지막수강회차", ""),
        ]
        for m in members
    ]
    clear_range(MEMBERS_SHEET_ID, f"{MEMBERS_TAB}!A2:I")
    if rows:
        write_sheet(MEMBERS_SHEET_ID, f"{MEMBERS_TAB}!A2", rows)


def _sync_member_records(records: list[dict]) -> None:
    """회원기록 탭 append."""
    if not records:
        return
    rows = [
        [str(r.get(col, "") or "") for col in MEMBER_RECORD_HEADER]
        for r in records
    ]
    append_sheet(MEMBERS_SHEET_ID, f"{MEMBER_RECORDS_TAB}!A1", rows)


def _sync_course_records(records: list[dict]) -> None:
    """수강기록 탭 append."""
    if not records:
        return
    rows = [
        [str(r.get(col, "") or "") for col in COURSE_RECORD_HEADER]
        for r in records
    ]
    append_sheet(MEMBERS_SHEET_ID, f"{COURSE_RECORDS_TAB}!A1", rows)


def _sync_deposits(deposits: list[dict]) -> None:
    """미확인입금 탭 전체 덮어쓰기 (clear A2:G + write A2)."""
    rows = [
        [str(d.get(col, "") or "") for col in _DEPOSIT_COLS]
        for d in deposits
    ]
    clear_range(MEMBERS_SHEET_ID, f"{UNMATCHED_DEPOSITS_TAB}!A2:G")
    if rows:
        write_sheet(MEMBERS_SHEET_ID, f"{UNMATCHED_DEPOSITS_TAB}!A2", rows)


# ── Public API ────────────────────────────────────────────────────────────


def _safe_sync(sync_type: str, data: list[dict], term_id: str) -> None:
    """Background thread entry point with error handling."""
    try:
        if sync_type == "applications":
            _sync_applications(data)
        elif sync_type == "members":
            _sync_members(data)
        elif sync_type == "member_records":
            _sync_member_records(data)
        elif sync_type == "course_records":
            _sync_course_records(data)
        elif sync_type == "deposits":
            _sync_deposits(data)
        else:
            logger.warning("Unknown sync type: %s", sync_type)
            return
        logger.info("Sheets sync completed: type=%s rows=%d", sync_type, len(data))
    except Exception as e:
        logger.warning("Sheets sync failed (non-critical): type=%s error=%s", sync_type, e)


async def sync_to_sheets(
    sync_type: str,
    *,
    data: list[dict],
    term_id: str = "",
) -> None:
    """Fire-and-forget background Sheets write.

    Args:
        sync_type: "applications", "members", "member_records",
                   "course_records", "deposits"
        data: 실제 쓸 데이터 (한국어 키 dict 리스트). 호출 시점에 이미 메모리에 있음.
        term_id: 회차 ID (로깅용)
    """
    if not USE_DB_SOT:
        return
    asyncio.create_task(asyncio.to_thread(_safe_sync, sync_type, data, term_id))

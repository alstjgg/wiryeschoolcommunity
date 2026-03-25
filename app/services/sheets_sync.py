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

# ── Sync functions (synchronous, run in background thread) ────────────────


def _sync_applications(applications: list[dict]) -> None:
    """신청기록 탭 전체 덮어쓰기 (clear A2:L + write A2)."""
    rows = [
        [str(a.get(col, "") or "") for col in _APP_HEADER]
        for a in applications
    ]
    if not rows:
        logger.warning("_sync_applications: no rows to write, skipping clear+write")
        return
    clear_range(MEMBERS_SHEET_ID, f"{APPLICATIONS_TAB}!A2:L")
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
    if not rows:
        logger.warning("_sync_members: no rows to write, skipping clear+write")
        return
    clear_range(MEMBERS_SHEET_ID, f"{MEMBERS_TAB}!A2:I")
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
    """미확인입금 탭 전체 덮어쓰기 (clear A2:G + write A2).

    _DEPOSITS_HEADER 순서: 입금일시, 회차, 입금액, 입금자명, 적요, 확인사유, 처리상태
    load_deposits() dict 키: 거래일시, 입금, 입금자명, 적요, 확인사유, 처리상태
    term_id는 호출부에서 각 dict에 주입.
    """
    rows = []
    for d in deposits:
        rows.append([
            str(d.get("거래일시", "") or ""),
            str(d.get("term_id", "") or d.get("회차", "") or ""),
            str(d.get("입금", "") or d.get("amount", "") or ""),
            str(d.get("입금자명", "") or ""),
            str(d.get("적요", "") or ""),
            str(d.get("확인사유", "") or ""),
            str(d.get("처리상태", "") or ""),
        ])
    if not rows:
        logger.warning("_sync_deposits: no rows to write, skipping clear+write")
        return
    clear_range(MEMBERS_SHEET_ID, f"{UNMATCHED_DEPOSITS_TAB}!A2:G")
    write_sheet(MEMBERS_SHEET_ID, f"{UNMATCHED_DEPOSITS_TAB}!A2", rows)


# ── Public API ────────────────────────────────────────────────────────────


def _safe_sync(sync_type: str, data: list[dict], term_id: str) -> None:
    """Background thread entry point with error handling."""
    logger.info("Sheets sync starting: type=%s rows=%d term=%s", sync_type, len(data), term_id)

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
        logger.error("Sheets sync FAILED: type=%s error=%s", sync_type, e, exc_info=True)


async def sync_to_sheets(
    sync_type: str,
    *,
    data: list[dict],
    term_id: str = "",
) -> None:
    """Sheets 동기화 — await으로 직렬 실행.

    에러 발생 시 호출부의 try/except에서 잡힘.

    Args:
        sync_type: "applications", "members", "member_records",
                   "course_records", "deposits"
        data: 실제 쓸 데이터 (한국어 키 dict 리스트). 호출 시점에 이미 메모리에 있음.
        term_id: 회차 ID (로깅용)
    """
    if not USE_DB_SOT:
        return
    await asyncio.to_thread(_safe_sync, sync_type, data, term_id)

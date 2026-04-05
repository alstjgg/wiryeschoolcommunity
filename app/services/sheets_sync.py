"""Background Sheets sync — DB SoT 모드에서 DB 쓰기 후 Sheets를 동기화.

chatbot에서 직접 Sheets API를 호출한다.
asyncio.to_thread()로 동기 Sheets API를 별도 스레드에서 await 직렬 실행.
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
    "회차", "이름ID", "이름", "유형", "과목명",
    "예상금액", "입금액", "입금시간", "의뢰인", "적요",
    "입금현황", "확인사유", "처리상태", "메모장",
]

_MEMBERS_HEADER = [
    "이름ID", "이름", "전화번호", "주소", "등급", "예외여부",
    "수강count", "출석률(누적)", "마지막수강회차",
]

_DEPOSITS_HEADER = [
    "입금일시", "회차", "입금액", "의뢰인", "적요", "입금자명", "처리상태", "확인한이름", "확인한강좌",
]

# ── Sync functions (synchronous, run in background thread) ────────────────


def _sync_applications(applications: list[dict]) -> None:
    """신청기록 탭 전체 덮어쓰기 (clear A2:N + write A2)."""
    rows = [
        [str(a.get(col, "") or "") for col in _APP_HEADER]
        for a in applications
    ]
    if not rows:
        logger.warning("_sync_applications: no rows to write, skipping clear+write")
        return
    clear_range(MEMBERS_SHEET_ID, f"{APPLICATIONS_TAB}!A2:N")
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
    rows = []
    for r in records:
        row = []
        for col in MEMBER_RECORD_HEADER:
            val = r.get(col, "") or ""
            # 변경일시는 YYYY-MM-DD 텍스트로 강제 (apostrophe prefix)
            # USER_ENTERED 모드에서 날짜 문자열이 serial로 변환되는 것을 방지
            if col == "변경일시" and val:
                val_str = str(val)
                if len(val_str) > 10:
                    val_str = val_str[:10]
                row.append(f"'{val_str}")
            elif col == "관련회차" and val:
                # "2026-2" → "'2026-2" — Sheets가 날짜로 해석하는 것 방지
                row.append(f"'{val}")
            else:
                row.append(str(val))
        rows.append(row)
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
    """미확인입금 탭 전체 덮어쓰기 (clear A2:I + write A2).

    9컬럼: 입금일시, 회차, 입금액, 의뢰인, 적요, 입금자명, 처리상태, 확인한이름, 확인한강좌
    관리자가 편집하는 G/H/I열은 clear 전에 읽어서 보존.
    """
    if not deposits:
        logger.warning("_sync_deposits: no rows to write, skipping clear+write")
        return

    # 관리자가 편집한 G/H/I열(처리상태, 확인한이름, 확인한강좌) 보존
    # deposit 키 = (입금일시, 입금액, 의뢰인)
    from app.services.google_sheets import read_sheet
    admin_data: dict[tuple, tuple] = {}
    try:
        existing = read_sheet(MEMBERS_SHEET_ID, f"{UNMATCHED_DEPOSITS_TAB}!A2:I")
        for row in (existing or []):
            if len(row) >= 3:
                key = (row[0], row[2], row[3] if len(row) > 3 else "")
                ps = row[6] if len(row) > 6 else ""
                cn = row[7] if len(row) > 7 else ""
                cc = row[8] if len(row) > 8 else ""
                if (ps or "").strip() or (cn or "").strip() or (cc or "").strip():
                    admin_data[key] = (ps.strip(), cn.strip(), cc.strip())
    except Exception:
        pass

    rows = []
    for d in deposits:
        tx_time = str(d.get("거래일시", "") or "")
        amount = str(d.get("입금", "") or d.get("amount", "") or "")
        payer = str(d.get("의뢰인", "") or d.get("입금자명", "") or "")

        # 관리자 편집값 복원
        key = (tx_time, amount, payer)
        saved = admin_data.get(key, ("", "", ""))
        ps = saved[0] or str(d.get("처리상태", "") or "")
        cn = saved[1]  # 확인한이름
        cc = saved[2]  # 확인한강좌

        rows.append([
            tx_time,
            str(d.get("term_id", "") or d.get("회차", "") or ""),
            amount,
            payer,
            str(d.get("적요", "") or ""),
            ", ".join(d.get("matched_name_ids", [])) or str(d.get("의뢰인", "") or ""),
            ps,
            cn,
            cc,
        ])
    clear_range(MEMBERS_SHEET_ID, f"{UNMATCHED_DEPOSITS_TAB}!A2:I")
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

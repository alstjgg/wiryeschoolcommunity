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
    """신청기록 탭 전체 덮어쓰기 (clear A2:N + write A2).

    관리자가 Sheets에서 직접 편집하는 처리상태(M열)/메모장(N열)은
    clear 전에 읽어서 in-memory 데이터에 없는 경우 복원.
    """
    if not applications:
        logger.warning("_sync_applications: no rows to write, skipping clear+write")
        return

    # 처리상태/메모장을 Sheets에서 먼저 읽어 보존 (key = 이름ID, 유형, 과목명)
    from app.services.google_sheets import read_sheet
    sheets_admin: dict[tuple, tuple] = {}  # key → (처리상태, 메모장)
    try:
        existing = read_sheet(MEMBERS_SHEET_ID, f"{APPLICATIONS_TAB}!A2:N")
        for row in (existing or []):
            if len(row) < 5:
                continue
            key = (str(row[1]), str(row[3]), str(row[4]) if len(row) > 4 else "")
            ps = str(row[12]) if len(row) > 12 else ""
            memo = str(row[13]) if len(row) > 13 else ""
            if (ps or "").strip() or (memo or "").strip():
                sheets_admin[key] = (ps.strip(), memo.strip())
        if sheets_admin:
            logger.info("_sync_applications: preserved %d admin-edited rows", len(sheets_admin))
    except Exception as e:
        logger.warning("_sync_applications: failed to read existing admin data: %s", e)

    # in-memory 데이터에 Sheets 편집값이 없으면 복원
    for app in applications:
        key = (app.get("이름ID", ""), app.get("유형", ""), app.get("과목명", ""))
        saved = sheets_admin.get(key)
        if saved:
            if not (app.get("처리상태") or "").strip() and saved[0]:
                app["처리상태"] = saved[0]
            if not (app.get("메모장") or "").strip() and saved[1]:
                app["메모장"] = saved[1]

    rows = [
        [str(a.get(col, "") or "") for col in _APP_HEADER]
        for a in applications
    ]
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


def _deposit_key(tx_time, amount, payer) -> tuple:
    """미확인입금 행 매칭용 정규화 키.

    Sheets API는 숫자를 int/float, 날짜를 serial로 반환할 수 있으므로
    양쪽 모두 동일한 정규화를 거쳐 비교해야 한다.
    - 입금액: int로 변환 (float→int 포함)
    - 거래일시/의뢰인: 문자열 strip
    """
    try:
        norm_amount = int(float(amount))
    except (ValueError, TypeError):
        norm_amount = str(amount).strip()
    return (str(tx_time).strip(), norm_amount, str(payer).strip())


def _sync_deposits(deposits: list[dict]) -> None:
    """미확인입금 탭 전체 덮어쓰기 (clear A2:I + write A2).

    9컬럼: 입금일시, 회차, 입금액, 의뢰인, 적요, 입금자명, 처리상태, 확인한이름, 확인한강좌
    관리자가 편집하는 G/H/I열은 clear 전에 읽어서 보존.
    거래일시는 apostrophe prefix로 Sheets의 날짜 자동변환을 방지.
    """
    if not deposits:
        logger.warning("_sync_deposits: no rows to write, skipping clear+write")
        return

    # 관리자가 편집한 G/H/I열(처리상태, 확인한이름, 확인한강좌) 보존
    # primary key = (거래일시, 입금액, 의뢰인), fallback key = (입금액, 의뢰인)
    # fallback은 거래일시가 Sheets에서 date serial로 변환된 경우를 위한 전환기 대책
    from app.services.google_sheets import read_sheet
    admin_data: dict[tuple, tuple] = {}
    admin_fallback: dict[tuple, tuple] = {}
    try:
        existing = read_sheet(MEMBERS_SHEET_ID, f"{UNMATCHED_DEPOSITS_TAB}!A2:I")
        for row in (existing or []):
            if len(row) >= 3:
                row_amount = row[2] if len(row) > 2 else ""
                row_payer = row[3] if len(row) > 3 else ""
                key = _deposit_key(row[0], row_amount, row_payer)
                ps = str(row[6]).strip() if len(row) > 6 else ""
                cn = str(row[7]).strip() if len(row) > 7 else ""
                cc = str(row[8]).strip() if len(row) > 8 else ""
                if ps or cn or cc:
                    admin_vals = (ps, cn, cc)
                    admin_data[key] = admin_vals
                    # fallback: (입금액, 의뢰인) — 거래일시가 serial로 변환된 경우 대비
                    fb_key = _deposit_key("", row_amount, row_payer)
                    admin_fallback[fb_key] = admin_vals
        if admin_data:
            logger.info("_sync_deposits: preserved %d admin-edited rows", len(admin_data))
    except Exception as e:
        logger.warning("_sync_deposits: failed to read existing admin data: %s", e)

    rows = []
    for d in deposits:
        tx_time = str(d.get("거래일시") if d.get("거래일시") is not None else "")
        raw_amount = d.get("입금") if d.get("입금") is not None else d.get("amount", "")
        amount = str(raw_amount) if raw_amount is not None else ""
        payer = str(d.get("의뢰인") or d.get("입금자명") or "")

        # 관리자 편집값 복원 (정규화된 키로 매칭, fallback 포함)
        key = _deposit_key(tx_time, raw_amount, payer)
        saved = admin_data.get(key)
        if not saved:
            # fallback: 거래일시가 date serial로 변환된 기존 데이터와 매칭
            fb_key = _deposit_key("", raw_amount, payer)
            saved = admin_fallback.get(fb_key, ("", "", ""))
        ps = saved[0] or str(d.get("처리상태", "") or "")
        cn = saved[1]  # 확인한이름
        cc = saved[2]  # 확인한강좌

        rows.append([
            # apostrophe prefix로 Sheets 날짜 자동변환 방지
            f"'{tx_time}" if tx_time else "",
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

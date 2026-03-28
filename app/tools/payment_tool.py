"""입금 대조 tool — 신청자 목록 + 입금내역 → 자동 매칭 + 등급 전환"""

import logging
from datetime import datetime, timezone

import chainlit as cl
from langchain_core.tools import tool

from app.chains.payment import (
    build_applications,
    write_applications_sheet,
    apply_exemptions,
    applications_to_students,
    apply_matching_results,
    apply_grade_cascade,
    get_exception_ids,
    update_applications_sheet,
    load_members_from_sheet,
    update_members_sheet,
    run_llm_matching,
    format_results,
    append_member_records,
)
from app.config import (
    USE_DB_SOT, MEMBERS_SHEET_ID, APPLICATIONS_TAB, UNMATCHED_DEPOSITS_TAB,
)
from app.context.term import get_current_term
from app.services.excel import parse_bank_statement, parse_applicant_list
from app.services.google_drive import find_term_folder
from app.services.google_sheets import get_tab_gids
from app.services.signup_loader import (
    load_member_signups_from_drive,
    load_fullmember_signups_from_drive,
)
from app.utils.matching import run_code_matching

logger = logging.getLogger(__name__)


def _get_app_sheet_id() -> str | None:
    sheet_id = cl.user_session.get("applications_sheet_id")
    if not sheet_id and USE_DB_SOT:
        sheet_id = MEMBERS_SHEET_ID
    return sheet_id


async def _load_signup_data(year: str) -> tuple[list[dict], list[dict]]:
    """Drive에서 신규가입·정회원가입 신청서를 자동 로드."""
    member_records = []
    fullmember_records = []

    async with cl.Step(name="📋 신규가입 신청서 로드", type="tool") as step:
        result = load_member_signups_from_drive(year)
        if result["found"] and not result["error"]:
            member_records = result["records"]
            step.output = (
                f"신규가입 신청서 로드 완료: **{result['count']}건** "
                f"({result['file_name']})"
            )
        else:
            step.output = f"⚠️ {result['error']} (입금 대조는 계속 진행합니다)"

    async with cl.Step(name="📋 정회원가입 신청서 로드", type="tool") as step:
        result = load_fullmember_signups_from_drive(year)
        if result["found"] and not result["error"]:
            fullmember_records = result["records"]
            step.output = (
                f"정회원가입 신청서 로드 완료: **{result['count']}건** "
                f"({result['file_name']})"
            )
        else:
            step.output = f"⚠️ {result['error']} (입금 대조는 계속 진행합니다)"

    return member_records, fullmember_records


async def _do_applicants_step(file_path: str, term: dict) -> str | None:
    """신청자 목록 파일 파싱 + 통합 신청서 생성. 성공 시 None, 에러 시 메시지."""
    term_id = term["term_id"]

    # Step 1: 신청자 목록 파싱
    async with cl.Step(name="📊 신청자 목록 분석", type="tool") as step:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        applicants = parse_applicant_list(file_bytes)
        if not applicants:
            step.output = "파일에서 신청자 데이터를 찾을 수 없습니다."
            return "파일에서 신청자 데이터를 찾을 수 없습니다. 파일 형식을 확인해주세요."
        courses = set(a.get("강좌명", "") for a in applicants if a.get("강좌명"))
        step.output = f"수강 신청자 **{len(applicants)}명** 확인 ({len(courses)}개 과목)"

    # Step 2: Drive에서 신규가입/정회원가입 신청서 로드
    member_records, fullmember_records = await _load_signup_data(str(term["year"]))

    # Step 3: 통합 신청서 생성 + Sheets 저장
    async with cl.Step(name="📝 통합 신청서 생성", type="tool") as step:
        applications = build_applications(
            applicants, member_records, fullmember_records, term_id=term_id,
        )

        term_folder_id = cl.user_session.get("term_folder_id")
        if not term_folder_id:
            term_folder = find_term_folder(term_id)
            if term_folder:
                term_folder_id = term_folder["id"]
                cl.user_session.set("term_folder_id", term_folder_id)

        if term_folder_id:
            try:
                app_sheet_id = await write_applications_sheet(
                    term_folder_id, applications, term_id=term_id,
                )
                cl.user_session.set("applications_sheet_id", app_sheet_id)
            except Exception as e:
                logger.error("write_applications_sheet failed: %s", e)

        수강_count = sum(1 for a in applications if a["유형"] == "수강")
        신규_count = sum(1 for a in applications if a["유형"] == "신규가입")
        정회원_count = sum(1 for a in applications if a["유형"] == "정회원")
        step.output = (
            f"수강 {수강_count}건, 신규가입 {신규_count}건, "
            f"정회원 {정회원_count}건 → 시트 저장 완료"
        )

    cl.user_session.set("applications", applications)
    cl.user_session.set("payment_step", "awaiting_payment")

    await cl.Message(
        content=(
            f"**{term['term_name']}** 통합 신청서 생성 완료:\n\n"
            f"- 수강 신청: **{수강_count}건**\n"
            f"- 신규가입: **{신규_count}건**\n"
            f"- 정회원: **{정회원_count}건**\n\n"
            "입금내역 파일(.xls 또는 .xlsx)을 업로드해주세요."
        )
    ).send()
    return None


async def _do_payment_step(file_path: str, term: dict) -> str:
    """입금내역 파일 파싱 + 매칭 + cascade → 결과 반환."""
    term_id = term["term_id"]

    # Step 1: 입금내역 파싱
    async with cl.Step(name="💰 입금내역 분석", type="tool") as step:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        transactions = parse_bank_statement(file_bytes)
        if not transactions:
            step.output = "거래 데이터를 찾을 수 없습니다."
            return "입금내역에서 거래 데이터를 찾을 수 없습니다. 파일 형식을 확인해주세요."
        step.output = f"입금 거래 **{len(transactions)}건** 확인"

    applications = cl.user_session.get("applications", [])
    if not applications:
        return "신청서 데이터가 없습니다. 입금 대조를 처음부터 다시 시작해주세요."

    # Step 1.5: 입금내역 DB 저장 + deposit ID 추적
    if USE_DB_SOT and term_id:
        try:
            from app.services import db
            existing = await db.load_deposits(term_id)
            max_existing_id = max((d["id"] for d in existing), default=0)
            await db.insert_deposits(term_id, transactions)
            all_deposits = await db.load_deposits(term_id)
            new_deposits = [d for d in all_deposits if d["id"] > max_existing_id]
            for i, tx in enumerate(transactions):
                if i < len(new_deposits):
                    tx["_deposit_id"] = new_deposits[i]["id"]
        except Exception as e:
            logger.warning("deposits INSERT failed (non-critical): %s", e)

    # Step 2: 회원 정보 로드 + 면제 처리
    async with cl.Step(name="👥 회원 정보 로드", type="tool") as step:
        members = await load_members_from_sheet()
        exception_ids = get_exception_ids(term_id) if term_id else set()
        exempted = apply_exemptions(applications, members, exception_ids)
        students = applications_to_students(applications)
        exc_count = sum(1 for e in exempted if e.get("확인사유") == "강사/사무처 면제")
        regular_count = len(exempted) - exc_count
        parts = [f"회원 **{len(members)}명** 로드"]
        if regular_count:
            parts.append(f"정회원 면제 **{regular_count}건**")
        if exc_count:
            parts.append(f"강사/사무처 면제 **{exc_count}건**")
        step.output = ", ".join(parts)

    # Step 3: 규칙 기반 매칭
    async with cl.Step(name="🔍 규칙 기반 매칭", type="tool") as step:
        all_results, needs_llm = run_code_matching(transactions, students)
        code_matched = sum(1 for r in all_results if r["상태"] == "✅정상")
        step.output = f"✅ {code_matched}건 매칭 / 🔶 {len(needs_llm)}건 강좌특정필요"

    # Step 4: LLM 매칭
    if needs_llm:
        async with cl.Step(name="🤖 AI 매칭", type="tool") as step:
            await run_llm_matching(needs_llm, students)
            llm_resolved = sum(1 for r in needs_llm if r["상태"] != "🔶확인필요")
            step.output = f"AI 분석 완료: **{llm_resolved}건** 추가 매칭"

    # deposit ID 전파
    for r in all_results:
        tx_memo = r.get("적요", "")
        tx_time = r.get("거래일시", "")
        for tx in transactions:
            if tx.get("적요") == tx_memo and tx.get("거래일시") == tx_time:
                if "_deposit_id" in tx:
                    r["_deposit_id"] = tx["_deposit_id"]
                break

    unmatched_deposits = apply_matching_results(applications, all_results)

    # DB: deposit match status 업데이트
    if USE_DB_SOT and term_id:
        try:
            from app.services import db as _db
            for r in all_results:
                dep_id = r.get("_deposit_id")
                if not dep_id:
                    continue
                if r.get("_matched"):
                    await _db.update_deposit_match(
                        dep_id, "matched", r.get("_matched_name_ids", []),
                    )
        except Exception as e:
            logger.warning("deposit match update failed (non-critical): %s", e)

    # processed_at 설정
    now = datetime.now(timezone.utc)
    for app in applications:
        if app.get("입금현황") not in ("❌미입금", ""):
            app.setdefault("processed_at", now)

    # Step 5: 등급 전환 cascade
    async with cl.Step(name="🔄 등급 전환", type="tool") as step:
        grade_changes = apply_grade_cascade(
            applications, members, term_id, exception_ids,
        )
        if grade_changes:
            step.output = f"등급 변경 **{len(grade_changes)}건** 처리"
        else:
            step.output = "등급 변경 없음"

    # 시트에 자동 반영
    return await _write_payment_results(
        applications, members, all_results, exempted,
        grade_changes, unmatched_deposits, term,
    )


async def _write_payment_results(
    applications: list[dict],
    members: list[dict],
    matched_results: list[dict],
    exempted: list[dict],
    grade_changes: list[dict],
    unmatched_deposits: int,
    term: dict,
) -> str:
    """매칭 결과를 DB/Sheets에 반영 → 요약 반환."""
    app_sheet_id = _get_app_sheet_id()
    term_id = term.get("term_id", "")

    async with cl.Step(name="💾 신청서 업데이트", type="tool") as step:
        errors = []
        if applications:
            try:
                await update_applications_sheet(
                    app_sheet_id, applications, term_id=term_id,
                )
                step.output = f"입금현황 **{len(applications)}건** 반영 완료"
            except Exception as e:
                logger.error("update_applications_sheet failed: %s", e)
                errors.append(f"입금현황 반영 실패: {e}")
                step.output = "입금현황 반영 중 오류 발생"

            if grade_changes:
                try:
                    await append_member_records(grade_changes)
                    step.output += f", 등급변경 {len(grade_changes)}건 기록"
                except Exception as e:
                    logger.error("append_member_records failed: %s", e)
                    errors.append(f"등급변경 기록 실패: {e}")
                try:
                    await update_members_sheet(members)
                except Exception as e:
                    logger.error("update_members_sheet failed: %s", e)
                    errors.append(f"회원목록 업데이트 실패: {e}")

            if errors:
                step.output += f"\n⚠️ 일부 오류: {'; '.join(errors)}"
        else:
            step.output = "신청서 데이터가 없어 반영하지 못했습니다."

    # 숫자 요약
    summary_line = format_results(
        matched_results, applications, exempted,
        unmatched_deposits=unmatched_deposits,
    )

    needs_check = sum(1 for r in matched_results if r["상태"] == "🔶확인필요")

    notes = []
    if needs_check:
        notes.append("🔶 확인이 필요한 건이 있습니다. 신청기록 시트에서 직접 확인해주세요.")
    if unmatched_deposits:
        notes.append(f"💳 미확인입금 **{unmatched_deposits}건**이 있습니다. 미확인입금 시트에서 확인해주세요.")
    check_note = "\n\n" + "\n".join(notes) if notes else ""

    sheet_links = ""
    link_sheet_id = _get_app_sheet_id()
    if link_sheet_id:
        try:
            gids = get_tab_gids(link_sheet_id)
            base = f"https://docs.google.com/spreadsheets/d/{link_sheet_id}"
            app_gid = gids.get(APPLICATIONS_TAB)
            dep_gid = gids.get(UNMATCHED_DEPOSITS_TAB)
            app_link = f"{base}#gid={app_gid}" if app_gid is not None else base
            dep_link = f"{base}#gid={dep_gid}" if dep_gid is not None else base
            sheet_links = f"\n\n[신청기록 시트 열기]({app_link})"
            if unmatched_deposits:
                sheet_links += f"\n[미확인입금 시트 열기]({dep_link})"
        except Exception:
            sheet_links = f"\n\n[회원관리 시트 열기](https://docs.google.com/spreadsheets/d/{link_sheet_id})"

    # 미확인입금 Sheets 동기화
    if USE_DB_SOT and term_id:
        try:
            from app.services import db as _db
            from app.services.sheets_sync import sync_to_sheets
            all_deposits = await _db.load_deposits(term_id)
            unmatched = [d for d in all_deposits if d.get("match_status") == "unmatched"]
            for d in unmatched:
                d["term_id"] = term_id
            await sync_to_sheets("deposits", data=unmatched, term_id=term_id)
        except Exception as e:
            logger.warning("deposits sync failed (non-critical): %s", e)

    # 세션 클리어
    cl.user_session.set("state", "idle")
    cl.user_session.set("payment_step", None)
    cl.user_session.set("matched_results", None)
    cl.user_session.set("applications", None)

    return (
        f"입금 대조가 완료되었습니다.\n\n"
        f"{summary_line}{check_note}\n\n"
        f"신청기록 시트에서 입금현황을 확인하시고, "
        f"배움숲 포탈에서 수강 등록을 처리한 뒤\n"
        f"처리상태를 입력해주세요.{sheet_links}"
    )


@tool
async def process_payment(
    file_path: str = "",
    term_id: str = "",
) -> str:
    """입금 대조를 수행합니다.

    수강 신청자 목록(배움숲 엑셀)과 입금내역(은행 엑셀)을 순서대로 받아
    자동 매칭 + 등급 전환을 처리합니다.

    2단계 파일 업로드 플로우:
    1단계: 수강 신청자 목록 (.xls) 업로드
    2단계: 입금내역 (.xls/.xlsx) 업로드

    Args:
        file_path: 업로드된 파일 경로. 비어있으면 파일 업로드를 요청합니다.
        term_id: 회차 ID (예: "2026-1"). 비어있으면 현재 회차 자동 판별.
    """
    # 회차 결정
    term = cl.user_session.get("term")
    if not term:
        term = get_current_term()
    if term_id:
        term["term_id"] = term_id
    cl.user_session.set("term", term)

    payment_step = cl.user_session.get("payment_step", "init")

    try:
        # Init: 파일 없으면 신청자 목록 요청
        if payment_step == "init" or payment_step is None:
            if not file_path:
                cl.user_session.set("payment_step", "awaiting_applicants")
                cl.user_session.set("state", "awaiting_applicants_file")
                return (
                    f"**{term['term_name']}** 수강 신청자 목록 파일을 업로드해주세요.\n\n"
                    "배움숲 포탈 → 수강신청관리 → 수강신청조회 → 엑셀 다운로드\n"
                    "파일명 형식: `LEARNING_APPLY*.xls`"
                )
            # 파일이 있으면 바로 처리
            cl.user_session.set("payment_step", "awaiting_applicants")

        # 신청자 목록 처리
        if payment_step == "awaiting_applicants":
            if not file_path:
                cl.user_session.set("state", "awaiting_applicants_file")
                return (
                    f"**{term['term_name']}** 수강 신청자 목록 파일을 업로드해주세요.\n\n"
                    "배움숲 포탈 → 수강신청관리 → 수강신청조회 → 엑셀 다운로드\n"
                    "파일명 형식: `LEARNING_APPLY*.xls`"
                )
            error = await _do_applicants_step(file_path, term)
            if error:
                cl.user_session.set("state", "idle")
                cl.user_session.set("payment_step", None)
                return error
            cl.user_session.set("state", "awaiting_payment_file")
            return "__SILENT__"  # 메시지는 _do_applicants_step에서 직접 발송

        # 입금내역 처리
        if payment_step == "awaiting_payment":
            if not file_path:
                cl.user_session.set("state", "awaiting_payment_file")
                return "입금내역 파일(.xls 또는 .xlsx)을 업로드해주세요."
            return await _do_payment_step(file_path, term)

    except Exception as e:
        cl.user_session.set("state", "idle")
        cl.user_session.set("payment_step", None)
        return f"입금 대조 중 오류가 발생했습니다: {e}"

    return "알 수 없는 상태입니다. 입금 대조를 처음부터 다시 시작해주세요."

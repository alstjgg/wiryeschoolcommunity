"""Chainlit 엔트리포인트 — 위례인생학교 업무 도우미"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 데이터 레이어 등록 (DATABASE_URL 있을 때만 활성화 — 다른 import보다 먼저)
import app.services.chat_data_layer  # noqa: F401

import chainlit as cl
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.chains.qa import answer_question
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
    find_unpaid,
    format_results,
    append_member_records,
)
from app.chains.attendance import (
    load_registered_students,
    group_by_course,
    create_attendance_spreadsheet,
    generate_attendance_pdf,
    upload_pdf_to_drive,
)
from app.chains.ocr import (
    load_course_students,
    process_attendance_image,
    write_attendance_to_sheet,
)
from app.chains.graduation import (
    load_attendance_results,
    update_attendance_rates_in_sheet,
    load_student_name_id_map,
    build_course_records,
    recalculate_member_stats,
    apply_demotion,
)
from app.config import ANTHROPIC_API_KEY, LLM_MODEL, MEMBERS_SHEET_ID
from app.services.excel import parse_bank_statement, parse_applicant_list
from app.services.google_drive import find_term_folder
from app.services.google_sheets import read_sheet
from app.services.signup_loader import (
    load_member_signups_from_drive,
    load_fullmember_signups_from_drive,
)
from app.utils.matching import run_code_matching
from app.context.term import get_current_term, parse_term_input

logger = logging.getLogger(__name__)

# 워크플로우 중 취소 의도 감지 키워드
CANCEL_KEYWORDS = ["취소", "중단", "그만", "멈춰", "stop", "cancel", "안 할게", "안할게", "나가기"]


def _get_app_sheet_id() -> str | None:
    """applications_sheet_id 조회 — DB SoT 모드에서는 항상 MEMBERS_SHEET_ID."""
    from app.config import USE_DB_SOT, MEMBERS_SHEET_ID
    sheet_id = cl.user_session.get("applications_sheet_id")
    if not sheet_id and USE_DB_SOT:
        sheet_id = MEMBERS_SHEET_ID
    return sheet_id


@cl.on_chat_start
async def on_chat_start():
    """새 대화 시작 — 세션 초기화"""
    cl.user_session.set("state", "idle")


@cl.on_chat_resume
async def on_chat_resume(thread: dict):
    """과거 대화를 열었을 때 메시지 히스토리 복원"""
    for step in thread.get("steps", []):
        step_type = step.get("type", "")
        output = step.get("output") or ""
        if not output:
            continue
        if step_type == "user_message":
            await cl.Message(
                author=step.get("name") or "관리자",
                content=output,
                type="user_message",
            ).send()
        elif step_type in ("assistant_message", "llm"):
            await cl.Message(content=output).send()
    cl.user_session.set("state", "idle")


@cl.on_stop
async def on_stop():
    """User clicked the stop button — reset to idle."""
    cl.user_session.set("state", "idle")


@cl.oauth_callback
def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: dict,
    default_user: cl.User,
) -> cl.User | None:
    """Google OAuth 콜백 — wiryeschoolcomunity.com 도메인만 허용"""
    if provider_id == "google":
        email = raw_user_data.get("email", "")
        if email.endswith("@wiryeschoolcomunity.com"):
            return default_user
    return None


@cl.set_starters
async def set_starters():
    return [
        cl.Starter(label="📝 계획서 검토", message="강의 계획서를 검토합니다."),
        cl.Starter(label="💰 입금 대조", message="입금 대조를 시작합니다."),
        cl.Starter(label="📋 출석부 생성", message="출석부를 생성합니다."),
        cl.Starter(label="✅ 출석 체크", message="출석 체크를 시작합니다."),
        cl.Starter(label="🎓 종강 처리", message="종강 처리를 시작합니다."),
        cl.Starter(label="📊 보고서 생성", message="보고서를 생성합니다."),
        cl.Starter(label="❓ 질문하기", message="업무 관련 질문이 있습니다."),
    ]


@cl.on_message
async def on_message(message: cl.Message):
    session_state = cl.user_session.get("state", "idle")

    # 세션 상태 기반 라우팅 (진행 중인 플로우)
    if session_state == "awaiting_applicants_file":
        if message.elements:
            await handle_applicants_file(message)
        else:
            await handle_mid_flow_text(message, "awaiting_applicants_file")
        return
    if session_state == "awaiting_payment_file":
        if message.elements:
            await handle_payment_file(message)
        else:
            await handle_mid_flow_text(message, "awaiting_payment_file")
        return
    if session_state == "awaiting_term_input":
        await handle_term_input(message)
        return
    if session_state == "awaiting_ocr_image":
        if message.elements:
            await handle_ocr_image(message)
        else:
            await handle_mid_flow_text(message, "awaiting_ocr_image")
        return
    if session_state == "creating_attendance":
        await cl.Message("출석부를 생성하고 있습니다. 잠시만 기다려주세요...").send()
        return
    if session_state == "running_graduation":
        await cl.Message("종강 처리가 진행 중입니다. 잠시만 기다려주세요...").send()
        return

    # Starter 버튼 메시지 라우팅
    if message.content == "입금 대조를 시작합니다.":
        await start_payment_flow(message)
    elif message.content == "출석부를 생성합니다.":
        await start_attendance_flow(message)
    elif message.content == "출석 체크를 시작합니다.":
        await start_ocr_flow(message)
    elif message.content == "종강 처리를 시작합니다.":
        await start_graduation_flow(message)
    elif message.content == "강의 계획서를 검토합니다.":
        await cl.Message("계획서 검토 기능은 준비 중입니다.").send()
        await send_default_actions()
    elif message.content == "보고서를 생성합니다.":
        await start_report_flow()
    else:
        # LLM 의도 분류 → 워크플로우 or Q&A
        intent_result = await classify_intent_llm(message.content)
        if intent_result["intent"] == "question":
            msg = cl.Message(content="")
            await msg.send()
            try:
                response = await answer_question(message.content)
                msg.content = response
                await msg.update()
                await send_default_actions()
            except Exception as e:
                msg.content = (
                    f"오류가 발생했습니다: {str(e)}\n\n"
                    "환경 변수(ANTHROPIC_API_KEY)가 올바르게 설정되어 있는지 확인해주세요."
                )
                await msg.update()
                await send_default_actions()
        else:
            await ask_intent_confirm(intent_result)


# ===================================================== 입금 대조 플로우 =====

async def start_payment_flow(message: cl.Message):
    """입금 대조 시작 — 회차 추측 → 확인"""
    term = get_current_term()
    cl.user_session.set("term", term)
    await _show_payment_term_confirm(term)


async def _show_payment_term_confirm(term: dict):
    """Show term confirmation with action buttons — non-blocking."""
    cl.user_session.set("term", term)
    await cl.Message(
        content=f"**{term['term_name']}** 입금 대조를 시작할까요?",
        actions=[
            cl.Action(name="payment_confirm_term", label="✅ 맞습니다",
                      payload={"value": "confirm"}),
            cl.Action(name="payment_other_term", label="📅 다른 회차에요",
                      payload={"value": "other"}),
            cl.Action(name="payment_cancel", label="❌ 취소",
                      payload={"value": "cancel"}),
        ],
    ).send()


@cl.action_callback("payment_confirm_term")
async def on_payment_confirm_term(action: cl.Action):
    term = cl.user_session.get("term")
    await _load_signup_and_ask_applicants(term)


@cl.action_callback("payment_other_term")
async def on_payment_other_term(action: cl.Action):
    cl.user_session.set("state", "awaiting_term_input")
    cl.user_session.set("term_input_next", "payment")
    await cl.Message("어떤 회차인지 알려주세요.\n예) 2026-2, 2026년 봄, 봄학기").send()


@cl.action_callback("payment_cancel")
async def on_payment_cancel(action: cl.Action):
    cl.user_session.set("state", "idle")
    await cl.Message("입금 대조가 취소되었습니다.").send()
    await send_default_actions()


async def handle_mid_flow_text(message: cl.Message, current_state: str):
    """워크플로우 진행 중 텍스트 입력 처리 (파일 없음).

    취소 → 플로우 종료
    질문 → Q&A 답변 후 상태 유지 + 재안내
    그 외 → 재안내
    """
    # 1) 취소 감지
    if any(k in message.content for k in CANCEL_KEYWORDS):
        await cl.Message("작업이 취소되었습니다.").send()
        cl.user_session.set("state", "idle")
        await send_default_actions()
        return

    # 2) LLM 의도 분류 — question이면 Q&A
    intent_result = await classify_intent_llm(message.content)
    if intent_result["intent"] == "question":
        msg = cl.Message(content="")
        await msg.send()
        try:
            response = await answer_question(message.content)
            msg.content = response
            await msg.update()
        except Exception as e:
            msg.content = f"오류가 발생했습니다: {str(e)}"
            await msg.update()
        # state 유지 + 재안내
        resume = _get_resume_prompt(current_state)
        await cl.Message(resume).send()
        return

    # 3) 그 외 — 재안내
    resume = _get_resume_prompt(current_state)
    await cl.Message(resume).send()


def _get_resume_prompt(state: str) -> str:
    """상태별 재안내 문구 반환"""
    term = cl.user_session.get("term") or {}
    term_name = term.get("term_name", "")
    prompts = {
        "awaiting_applicants_file": (
            f"계속 진행하려면 **{term_name}** 수강 신청자 목록 파일(.xls)을 업로드해주세요.\n"
            "취소하려면 '취소'라고 입력하세요."
        ),
        "awaiting_payment_file": (
            "계속 진행하려면 입금내역 파일(.xls 또는 .xlsx)을 업로드해주세요.\n"
            "취소하려면 '취소'라고 입력하세요."
        ),
        "awaiting_ocr_image": (
            "계속 진행하려면 출석부 사진을 업로드해주세요.\n"
            "취소하려면 '취소'라고 입력하세요."
        ),
    }
    return prompts.get(state, "계속 진행하려면 파일을 업로드해주세요.\n취소하려면 '취소'라고 입력하세요.")


async def _load_signup_and_ask_applicants(term: dict):
    """Auto-load signup data from Drive, then ask for applicants file."""
    member_records, fullmember_records = await _load_signup_data(str(term["year"]))
    cl.user_session.set("member_records", member_records)
    cl.user_session.set("fullmember_records", fullmember_records)
    await _ask_for_applicants_file()


async def _ask_for_applicants_file():
    """신청자 목록 파일 업로드 요청"""
    term = cl.user_session.get("term")
    await cl.Message(
        content=(
            f"**{term['term_name']}** 수강 신청자 목록 파일을 업로드해주세요.\n\n"
            "배움숲 포탈 → 수강신청관리 → 수강신청조회 → 엑셀 다운로드\n"
            "파일명 형식: `LEARNING_APPLY*.xls`"
        )
    ).send()
    cl.user_session.set("state", "awaiting_applicants_file")


async def handle_applicants_file(message: cl.Message):
    """신청자 목록 파일 수신 → 파싱 → Drive 신청서 로드 → 통합 신청서 Sheets 생성"""
    file_element = message.elements[0]
    term = cl.user_session.get("term")
    term_id = term["term_id"]

    try:
        # Step 1: 신청자 목록 파싱
        async with cl.Step(name="📊 신청자 목록 분석", type="tool") as step:
            with open(file_element.path, "rb") as f:
                file_bytes = f.read()
            applicants = parse_applicant_list(file_bytes)
            if not applicants:
                step.output = "파일에서 신청자 데이터를 찾을 수 없습니다."
                await cl.Message(
                    "파일에서 신청자 데이터를 찾을 수 없습니다. 파일 형식을 확인해주세요."
                ).send()
                cl.user_session.set("state", "idle")
                return
            courses = set(a.get("강좌명", "") for a in applicants if a.get("강좌명"))
            step.output = f"수강 신청자 **{len(applicants)}명** 확인 ({len(courses)}개 과목)"

        # Step 2: 세션에서 신규가입/정회원가입 신청서 로드 (이미 _load_signup_and_ask_applicants에서 저장됨)
        member_records = cl.user_session.get("member_records", [])
        fullmember_records = cl.user_session.get("fullmember_records", [])

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

            app_sheet_id = None
            if term_folder_id:
                try:
                    app_sheet_id = await write_applications_sheet(
                        term_folder_id, applications, term_id=term_id,
                    )
                    cl.user_session.set("applications_sheet_id", app_sheet_id)
                except Exception as e:
                    await cl.Message(f"신청서 시트 생성 오류: {e}").send()

            수강_count = sum(1 for a in applications if a["유형"] == "수강")
            신규_count = sum(1 for a in applications if a["유형"] == "신규가입")
            정회원_count = sum(1 for a in applications if a["유형"] == "정회원")
            step.output = (
                f"수강 {수강_count}건, 신규가입 {신규_count}건, "
                f"정회원 {정회원_count}건 → 시트 저장 완료"
            )

        await cl.Message(
            content=(
                f"**{term['term_name']}** 통합 신청서 생성 완료:\n\n"
                f"- 수강 신청: **{수강_count}건**\n"
                f"- 신규가입: **{신규_count}건**\n"
                f"- 정회원: **{정회원_count}건**\n\n"
                "입금내역 파일(.xls 또는 .xlsx)을 업로드해주세요."
            )
        ).send()

        cl.user_session.set("state", "awaiting_payment_file")
        cl.user_session.set("applications", applications)

    except Exception as e:
        await cl.Message(f"신청자 데이터 로드 중 오류: {str(e)}").send()
        cl.user_session.set("state", "idle")


async def _load_signup_data(year: str) -> tuple[list[dict], list[dict]]:
    """Drive에서 신규가입·정회원가입 신청서를 자동 로드.

    파일을 못 찾으면 경고만 표시하고 빈 리스트 반환.
    Returns: (member_records, fullmember_records)
    """
    member_records = []
    fullmember_records = []

    # 신규가입 신청서
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

    # 정회원가입 신청서
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


async def handle_payment_file(message: cl.Message):
    """입금내역 파일 수신 → 파싱 → 매칭 → 결과 표시"""
    file_element = message.elements[0]

    try:
        # Step 1: 입금내역 파싱
        async with cl.Step(name="💰 입금내역 분석", type="tool") as step:
            with open(file_element.path, "rb") as f:
                file_bytes = f.read()
            transactions = parse_bank_statement(file_bytes)
            if not transactions:
                step.output = "거래 데이터를 찾을 수 없습니다."
                await cl.Message(
                    "입금내역에서 거래 데이터를 찾을 수 없습니다. 파일 형식을 확인해주세요."
                ).send()
                cl.user_session.set("state", "idle")
                return
            step.output = f"입금 거래 **{len(transactions)}건** 확인"

        applications = cl.user_session.get("applications", [])
        if not applications:
            await cl.Message(
                "신청서 데이터가 없습니다. 입금 대조를 처음부터 다시 시작해주세요."
            ).send()
            cl.user_session.set("state", "idle")
            return

        # Step 1.5: 입금내역 DB 저장 + deposit ID 추적 (USE_DB_SOT 모드)
        term_id = (cl.user_session.get("term") or {}).get("term_id", "")
        from app.config import USE_DB_SOT
        if USE_DB_SOT and term_id:
            try:
                from app.services import db
                # 기존 최대 ID 기록 (재실행 시 새 건만 추적)
                existing = await db.load_deposits(term_id)
                max_existing_id = max((d["id"] for d in existing), default=0)

                await db.insert_deposits(term_id, transactions)

                # 새로 삽입된 deposits 로드 (id > max_existing_id)
                all_deposits = await db.load_deposits(term_id)
                new_deposits = [d for d in all_deposits if d["id"] > max_existing_id]

                # transactions[i] ↔ new_deposits[i] 매핑 (삽입 순서 = id 순서)
                for i, tx in enumerate(transactions):
                    if i < len(new_deposits):
                        tx["_deposit_id"] = new_deposits[i]["id"]
            except Exception as e:
                logger.warning("deposits INSERT failed (non-critical): %s", e)

        # Step 2: 회원 정보 로드 + 면제 처리 (정회원 + 강사/사무처)
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

        # Step 4: LLM 매칭 (강좌 특정이 필요한 건만)
        if needs_llm:
            async with cl.Step(name="🤖 AI 매칭", type="tool") as step:
                await run_llm_matching(needs_llm, students)
                llm_resolved = sum(
                    1 for r in needs_llm if r["상태"] != "🔶확인필요"
                )
                step.output = f"AI 분석 완료: **{llm_resolved}건** 추가 매칭"

        # 매칭 결과를 applications에 반영 + deposit 추적
        # _deposit_id가 transactions에 있으면 all_results로 전파
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

        # processed_at 설정 (매칭 처리된 건)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for app in applications:
            if app.get("입금현황") not in ("❌미입금", ""):
                app.setdefault("processed_at", now)

        # Step 5: 등급 전환 cascade (신규가입 → 정회원 → 수강)
        async with cl.Step(name="🔄 등급 전환", type="tool") as step:
            grade_changes = apply_grade_cascade(
                applications, members, term_id, exception_ids,
            )
            if grade_changes:
                step.output = f"등급 변경 **{len(grade_changes)}건** 처리"
            else:
                step.output = "등급 변경 없음"

        cl.user_session.set("applications", applications)
        cl.user_session.set("matched_results", all_results)
        cl.user_session.set("members", members)
        cl.user_session.set("grade_changes", grade_changes)

        # 즉시 시트에 자동 반영
        await write_payment_results(
            all_results, exempted, grade_changes, unmatched_deposits,
        )

    except Exception as e:
        await cl.Message(f"입금 대조 중 오류가 발생했습니다: {str(e)}").send()
        cl.user_session.set("state", "idle")


async def handle_term_input(message: cl.Message):
    """Unified term text input handler — routes based on term_input_next."""
    # Check cancel first
    if any(k in message.content for k in CANCEL_KEYWORDS):
        await cl.Message("작업이 취소되었습니다.").send()
        cl.user_session.set("state", "idle")
        await send_default_actions()
        return

    term = parse_term_input(message.content)
    if not term:
        await cl.Message(
            "회차를 인식하지 못했어요. 다시 입력해주세요.\n예) 2026-2, 봄학기, 2026년 여름"
        ).send()
        return  # state stays awaiting_term_input

    cl.user_session.set("term", term)
    cl.user_session.set("state", "idle")

    next_flow = cl.user_session.get("term_input_next", "payment")
    if next_flow == "payment":
        await _show_payment_term_confirm(term)
    elif next_flow == "attendance":
        await _do_attendance_preflight(term)
    elif next_flow == "ocr":
        await _show_ocr_start_confirm(term)
    elif next_flow == "graduation":
        await _confirm_ocr_done_before_graduation(term)


async def write_payment_results(
    matched_results: list[dict] | None = None,
    exempted: list[dict] | None = None,
    grade_changes: list[dict] | None = None,
    unmatched_deposits: int = 0,
):
    """매칭 결과를 DB/Sheets에 반영 → 요약 + 다음 단계 Action 제공"""
    try:
        app_sheet_id = _get_app_sheet_id()
        applications = cl.user_session.get("applications", [])
        members = cl.user_session.get("members", [])

        async with cl.Step(name="💾 신청서 업데이트", type="tool") as step:
            errors = []
            if applications:
                term_id = (cl.user_session.get("term") or {}).get("term_id", "")
                try:
                    await update_applications_sheet(
                        app_sheet_id, applications, term_id=term_id,
                    )
                    step.output = f"입금현황 **{len(applications)}건** 반영 완료"
                except Exception as e:
                    logger.error("update_applications_sheet failed: %s", e)
                    errors.append(f"입금현황 반영 실패: {e}")
                    step.output = "입금현황 반영 중 오류 발생"

                # 회원기록 + 회원목록 저장 (cascade 결과)
                if grade_changes is None:
                    grade_changes = cl.user_session.get("grade_changes", [])
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
        if matched_results is None:
            matched_results = cl.user_session.get("matched_results", [])
        summary_line = format_results(
            matched_results, applications, exempted,
            unmatched_deposits=unmatched_deposits,
        )

        needs_check = sum(
            1 for r in matched_results if r["상태"] == "🔶확인필요"
        )

        notes = []
        if needs_check:
            notes.append("🔶 확인이 필요한 건이 있습니다. 신청기록 시트에서 직접 확인해주세요.")
        if unmatched_deposits:
            notes.append(f"💳 미확인입금 **{unmatched_deposits}건**이 있습니다. 미확인입금 시트에서 확인해주세요.")
        check_note = "\n\n" + "\n".join(notes) if notes else ""

        from app.config import APPLICATIONS_TAB, UNMATCHED_DEPOSITS_TAB
        from app.services.google_sheets import get_tab_gids
        link_sheet_id = _get_app_sheet_id()
        sheet_links = ""
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

        await cl.Message(
            content=(
                f"입금 대조가 완료되었습니다.\n\n"
                f"{summary_line}{check_note}\n\n"
                f"신청기록 시트에서 입금현황을 확인하시고, "
                f"배움숲 포탈에서 수강 등록을 처리한 뒤\n"
                f"처리상태를 입력해주세요.{sheet_links}"
            ),
        ).send()

        await send_default_actions("payment")

        # 미확인입금 Sheets 동기화 (fire-and-forget background)
        if USE_DB_SOT:
            from app.services import db as _db
            from app.services.sheets_sync import sync_to_sheets
            term_id = (cl.user_session.get("term") or {}).get("term_id", "")
            if term_id:
                all_deposits = await _db.load_deposits(term_id)
                unmatched = [d for d in all_deposits if d.get("match_status") == "unmatched"]
                for d in unmatched:
                    d["term_id"] = term_id
                await sync_to_sheets("deposits", data=unmatched, term_id=term_id)

    except Exception as e:
        await cl.Message(f"저장 중 오류: {str(e)}").send()
        await send_default_actions()

    _clear_payment_session()


# ===================================================== 출석부 생성 플로우 =====

async def start_attendance_flow(message: cl.Message | None):
    """출석부 생성 진입 — 세션에 term이 없으면 회차 먼저 확인"""
    term = cl.user_session.get("term")

    if not term:
        term = get_current_term()
        cl.user_session.set("term", term)

    if cl.user_session.get("applications"):
        # Same session as payment — skip term confirm, go to preflight
        await _do_attendance_preflight(term)
    else:
        await cl.Message(
            content=f"**{term['term_name']}** 출석부를 생성할까요?",
            actions=[
                cl.Action(name="attendance_confirm_term", label="✅ 맞습니다",
                          payload={"value": "confirm"}),
                cl.Action(name="attendance_other_term", label="📅 다른 회차에요",
                          payload={"value": "other"}),
                cl.Action(name="attendance_cancel", label="❌ 취소",
                          payload={"value": "cancel"}),
            ],
        ).send()


@cl.action_callback("attendance_confirm_term")
async def on_attendance_confirm_term(action: cl.Action):
    term = cl.user_session.get("term")
    await _do_attendance_preflight(term)


@cl.action_callback("attendance_other_term")
async def on_attendance_other_term(action: cl.Action):
    cl.user_session.set("state", "awaiting_term_input")
    cl.user_session.set("term_input_next", "attendance")
    await cl.Message("어떤 회차인지 알려주세요.\n예) 2026-1, 2026년 겨울, 겨울학기").send()


@cl.action_callback("attendance_cancel")
async def on_attendance_cancel(action: cl.Action):
    cl.user_session.set("state", "idle")
    await cl.Message("출석부 생성이 취소되었습니다.").send()
    await send_default_actions()


async def _do_attendance_preflight(term: dict):
    """출석부 생성 전 사전 확인 — 처리상태 gate → 최종 승인"""
    msg = cl.Message(content=f"**{term['term_name']}** 출석부 생성을 준비하는 중...")
    await msg.send()

    try:
        term_folder_id = cl.user_session.get("term_folder_id")
        if not term_folder_id:
            term_folder = find_term_folder(term["term_id"])
            if not term_folder:
                msg.content = (
                    f"Drive에서 **{term['term_name']}** 폴더를 찾을 수 없습니다.\n"
                    f"학사운영 → {term['year']} → 회차 폴더가 있는지 확인해주세요."
                )
                await msg.update()
                await send_default_actions()
                return
            term_folder_id = term_folder["id"]
            cl.user_session.set("term_folder_id", term_folder_id)

        app_sheet_id = _get_app_sheet_id()
        await _check_processing_gate(term, app_sheet_id)

    except Exception as e:
        msg.content = f"출석부 생성 준비 중 오류: {str(e)}"
        await msg.update()
        await send_default_actions()


async def _check_processing_gate(term: dict, app_sheet_id: str | None):
    """Check for unprocessed items. If clear → show final confirm. If not → show recheck/cancel."""
    from app.config import (
        USE_DB_SOT, MEMBERS_SHEET_ID, APPLICATIONS_TAB, UNMATCHED_DEPOSITS_TAB,
    )
    term_id = term.get("term_id", "")

    def _read_unprocessed(sid: str, tab: str, col_range: str) -> list[dict]:
        """시트 탭에서 처리상태가 미입력/보류인 행만 반환."""
        result = []
        if not sid:
            return result
        rows = read_sheet(sid, f"{tab}!{col_range}")
        if not rows or len(rows) < 2:
            return result
        header = rows[0]
        for row in rows[1:]:
            data = dict(zip(header, row + [""] * (len(header) - len(row))))
            if term_id and data.get("회차", "").strip() != term_id:
                continue
            status = data.get("처리상태", "").strip()
            if not status or status == "보류":
                result.append(data)
        return result

    if USE_DB_SOT:
        apps_sheet_id = MEMBERS_SHEET_ID
        apps_tab = APPLICATIONS_TAB
    else:
        apps_sheet_id = app_sheet_id
        apps_tab = "신청서"

    unprocessed_apps = _read_unprocessed(apps_sheet_id, apps_tab, "A1:M5000")
    unprocessed_deposits = (
        _read_unprocessed(MEMBERS_SHEET_ID, UNMATCHED_DEPOSITS_TAB, "A1:H5000")
        if USE_DB_SOT else []
    )

    if not unprocessed_apps and not unprocessed_deposits:
        # Gate passed — show final attendance confirm
        await _show_attendance_final_confirm()
        return

    # Gate blocked — build message
    sections = []
    if unprocessed_apps:
        lines = []
        for a in unprocessed_apps[:5]:
            name = a.get("이름", "?")
            course = a.get("과목명", "")
            label = f"{name}({course})" if course else name
            ps = a.get("처리상태", "").strip() or "미입력"
            lines.append(f"  - {label} — 입금현황: {a.get('입금현황', '')}, 처리상태: {ps}")
        if len(unprocessed_apps) > 5:
            lines.append(f"  - ... 외 {len(unprocessed_apps) - 5}건")
        sections.append(
            f"**신청기록 미처리: {len(unprocessed_apps)}건**\n"
            + "\n".join(lines)
            + "\n  → 신청기록 시트에서 처리상태를 입력해주세요"
        )

    if unprocessed_deposits:
        lines = []
        for d in unprocessed_deposits[:5]:
            ps = d.get("처리상태", "").strip() or "미입력"
            lines.append(f"  - 의뢰인 {d.get('의뢰인', '?')} / {d.get('입금액', '')}원 — 처리상태: {ps}")
        if len(unprocessed_deposits) > 5:
            lines.append(f"  - ... 외 {len(unprocessed_deposits) - 5}건")
        sections.append(
            f"**미확인입금 미처리: {len(unprocessed_deposits)}건**\n"
            + "\n".join(lines)
            + "\n  → 미확인입금 시트에서 처리상태를 입력해주세요"
        )

    link_id = MEMBERS_SHEET_ID if USE_DB_SOT else apps_sheet_id
    sheet_link = (
        f"\n\n[회원관리 시트 열기](https://docs.google.com/spreadsheets/d/{link_id})"
        if link_id else ""
    )

    msg_content = (
        f"**{term.get('term_name', '')}** 출석부 생성을 위해 처리가 필요한 건이 있습니다.\n\n"
        + "\n\n".join(sections)
        + f"\n\n모든 건의 처리상태를 입력해주세요 (등록완료/환불완료/취소완료).{sheet_link}"
    )

    await cl.Message(
        content=msg_content,
        actions=[
            cl.Action(name="gate_recheck", label="🔄 다시 확인",
                      payload={"value": "recheck"}),
            cl.Action(name="gate_cancel", label="❌ 취소",
                      payload={"value": "cancel"}),
        ],
    ).send()


async def _show_attendance_final_confirm():
    """Show final confirm before creating attendance sheet."""
    await cl.Message(
        content=(
            "배움숲 등록, 환불/취소 처리를 모두 완료하셨나요?\n\n"
            "처리상태가 '등록완료'인 수강생만 출석부에 포함됩니다."
        ),
        actions=[
            cl.Action(name="attendance_confirm_create", label="✅ 확인, 출석부 생성",
                      payload={"value": "confirm"}),
            cl.Action(name="attendance_cancel_create", label="❌ 취소",
                      payload={"value": "cancel"}),
        ],
    ).send()


@cl.action_callback("gate_recheck")
async def on_gate_recheck(action: cl.Action):
    term = cl.user_session.get("term")
    app_sheet_id = _get_app_sheet_id()
    await _check_processing_gate(term, app_sheet_id)


@cl.action_callback("gate_cancel")
async def on_gate_cancel(action: cl.Action):
    cl.user_session.set("state", "idle")
    await send_default_actions()


@cl.action_callback("attendance_confirm_create")
async def on_attendance_confirm_create(action: cl.Action):
    await do_create_attendance()


@cl.action_callback("attendance_cancel_create")
async def on_attendance_cancel_create(action: cl.Action):
    cl.user_session.set("state", "idle")
    await cl.Message("출석부 생성이 취소되었습니다.").send()
    await send_default_actions()


async def do_create_attendance():
    """출석부 생성 실행 — 단계별 직렬 처리 + 중간 진행 표시"""
    cl.user_session.set("state", "creating_attendance")
    term = cl.user_session.get("term") or get_current_term()
    term_folder_id = cl.user_session.get("term_folder_id")
    app_sheet_id = _get_app_sheet_id()

    if not term_folder_id:
        await cl.Message(
            "회차 폴더 정보가 없습니다. 입금 대조를 먼저 완료해주세요."
        ).send()
        cl.user_session.set("state", "idle")
        await send_default_actions()
        return

    try:
        # Step 1: 등록 수강생 확인
        async with cl.Step(name="📊 등록 수강생 확인", type="tool") as step:
            att_term_id = term.get("term_id", "")
            registered = await load_registered_students(
                app_sheet_id, term_id=att_term_id,
            )
            if not registered:
                step.output = "등록완료 수강생 없음"
                await cl.Message(
                    "처리상태가 '등록완료'인 수강생이 없습니다.\n"
                    "배움숲 등록 처리 후 신청기록 시트에서 처리상태를 '등록완료'로 설정해주세요."
                ).send()
                cl.user_session.set("state", "idle")
                await send_default_actions()
                return

            courses = group_by_course(registered)
            course_names = sorted(courses.keys())
            total_students = sum(len(v) for v in courses.values())
            step.output = f"등록완료 수강생 **{total_students}명** ({len(course_names)}개 과목)"

        # 중간 안내
        await cl.Message(
            f"**{term['term_name']}**에 총 **{len(course_names)}개** 강의를 확인했습니다. "
            f"수강생은 총 **{total_students}명**입니다.\n\n"
            "출석부 시트를 생성합니다..."
        ).send()

        # Step 2: 출석부 시트 생성 (폴더 + 스프레드시트 + 탭 + 데이터)
        async with cl.Step(name="📋 출석부 시트 생성", type="tool") as step:
            sheet_result = create_attendance_spreadsheet(
                term["term_id"], term_folder_id, courses,
            )
            cl.user_session.set("attendance_sheet_id", sheet_result["spreadsheet_id"])
            step.output = (
                f"수강생 탭 + 과목별 탭 **{len(course_names)}개** 생성 완료"
            )

        await cl.Message(
            f"출석부 시트 생성 완료. 과목별 PDF를 생성합니다... (0/{len(course_names)})"
        ).send()

        # Step 3: 과목별 PDF 생성 + 업로드
        pdf_urls: dict[str, str | None] = {}
        progress_msg = await cl.Message(
            content=f"PDF 생성 중... (0/{len(course_names)})"
        ).send()

        for idx, course_name in enumerate(course_names):
            try:
                pdf_bytes = generate_attendance_pdf(
                    term["term_id"], course_name, courses[course_name],
                )
                pdf_url = upload_pdf_to_drive(
                    pdf_bytes, term["term_id"], course_name,
                    sheet_result["attendance_folder_id"],
                )
                pdf_urls[course_name] = pdf_url
            except Exception:
                pdf_urls[course_name] = None

            # 4~5과목마다 진행 메시지 업데이트
            if (idx + 1) % 4 == 0 or idx + 1 == len(course_names):
                progress_msg.content = (
                    f"PDF 생성 중... ({idx + 1}/{len(course_names)})"
                )
                await progress_msg.update()

        pdf_count = sum(1 for v in pdf_urls.values() if v)

        # 최종 안내 메시지
        pdf_lines = []
        for course in course_names:
            url = pdf_urls.get(course)
            if url:
                pdf_lines.append(f"  - [{course}]({url})")
            else:
                pdf_lines.append(f"  - {course} (PDF 생성 실패)")

        pdf_section = "\n\n**과목별 인쇄용 PDF**:\n" + "\n".join(pdf_lines)

        await cl.Message(
            content=(
                f"✅ 출석부 생성이 완료되었습니다!\n\n"
                f"- 과목 수: **{len(course_names)}개**\n"
                f"- 총 수강생: **{total_students}명**\n"
                f"- PDF: **{pdf_count}개** 생성\n\n"
                f"[{term['term_name']} 출석부 폴더]({sheet_result['attendance_folder_url']})\n"
                f"[출석부 시트 열기]({sheet_result['spreadsheet_url']})"
                f"{pdf_section}\n\n"
                "과목별 PDF를 출력하여 강사에게 전달해주세요.\n"
                "종강 후 출석 체크(사진 촬영 → OCR)를 진행해주세요."
            )
        ).send()

        await send_default_actions("attendance")

    except Exception as e:
        await cl.Message(f"출석부 생성 중 오류: {str(e)}").send()
        await send_default_actions()

    cl.user_session.set("state", "idle")


# ===================================================== 출석 체크(OCR) 플로우 =====

async def start_ocr_flow(message):
    """출석 체크 시작 — 회차 확인 → 종강 여부 확인 → 사진 업로드"""
    term = cl.user_session.get("term") or get_current_term()
    cl.user_session.set("term", term)
    await _show_ocr_start_confirm(term)


async def _show_ocr_start_confirm(term: dict):
    """Show OCR start confirmation — non-blocking."""
    await cl.Message(
        content=(
            f"**{term['term_name']}** 출석 체크를 시작합니다.\n\n"
            "출석 체크는 **종강 후** 진행하는 작업입니다.\n"
            "해당 회차의 강좌가 종강되었는지 확인해주세요."
        ),
        actions=[
            cl.Action(name="ocr_confirm_start", label="✅ 종강 완료, 시작합니다",
                      payload={"value": "confirm"}),
            cl.Action(name="ocr_cancel_start", label="❌ 취소",
                      payload={"value": "cancel"}),
        ],
    ).send()


@cl.action_callback("ocr_confirm_start")
async def on_ocr_confirm_start(action: cl.Action):
    term = cl.user_session.get("term")
    await _resolve_attendance_sheet_id(term)
    await _ask_for_ocr_image(term)


@cl.action_callback("ocr_cancel_start")
async def on_ocr_cancel_start(action: cl.Action):
    cl.user_session.set("state", "idle")
    await cl.Message("출석 체크가 취소되었습니다.").send()
    await send_default_actions()


async def _resolve_attendance_sheet_id(term: dict):
    """세션에 attendance_sheet_id가 없으면 Drive에서 탐색하여 세션에 저장"""
    if cl.user_session.get("attendance_sheet_id"):
        return

    term_folder_id = cl.user_session.get("term_folder_id")
    if not term_folder_id:
        term_folder = find_term_folder(term["term_id"])
        if term_folder:
            term_folder_id = term_folder["id"]
            cl.user_session.set("term_folder_id", term_folder_id)

    if term_folder_id:
        from app.services.google_drive import (
            find_or_create_folder, find_spreadsheet_by_name,
        )
        att_folder = find_or_create_folder(term_folder_id, "출석부")
        att_file = find_spreadsheet_by_name(att_folder["id"], "출석부")
        if att_file:
            cl.user_session.set("attendance_sheet_id", att_file["id"])


async def _ask_for_ocr_image(term: dict):
    """출석부 사진 업로드 요청"""
    await cl.Message(
        content=(
            f"**{term['term_name']}** 출석부 사진을 업로드해주세요.\n\n"
            "**촬영 방법**:\n"
            "- 한 과목의 출석부 전체가 나오도록 촬영해주세요.\n"
            "- 이름과 회차 칸이 모두 선명하게 보여야 합니다.\n\n"
            "사진과 함께 **과목명**을 입력해주세요.\n"
            "예) `경제뉴스 기초 출석부입니다` + 사진 첨부\n\n"
            "취소하려면 '취소'라고 입력하세요."
        )
    ).send()
    cl.user_session.set("state", "awaiting_ocr_image")


async def handle_ocr_image(message: cl.Message):
    """출석부 사진 수신 → OCR → 결과 확인 → 시트 반영"""
    file_element = message.elements[0]
    term = cl.user_session.get("term") or get_current_term()
    attendance_sheet_id = cl.user_session.get("attendance_sheet_id")

    if not attendance_sheet_id:
        await cl.Message(
            "출석부 시트를 찾을 수 없습니다.\n"
            "출석부 생성이 먼저 완료되어야 합니다."
        ).send()
        cl.user_session.set("state", "idle")
        await send_default_actions()
        return

    # 메시지 텍스트에서 과목명 추출
    course_name = _extract_course_name(message.content, attendance_sheet_id)
    if not course_name:
        await cl.Message(
            "과목명을 인식하지 못했습니다.\n"
            "예) `경제뉴스 기초 출석부입니다` 처럼 과목명을 함께 입력해주세요."
        ).send()
        return  # 상태 유지 — 다시 사진 업로드 요청

    try:
        async with cl.Step(name="📸 출석부 이미지 분석", type="tool") as step:
            with open(file_element.path, "rb") as f:
                image_bytes = f.read()
            ocr_term_id = (cl.user_session.get("term") or {}).get("term_id", "")
            students = await load_course_students(
                attendance_sheet_id, course_name, term_id=ocr_term_id,
            )
            if not students:
                step.output = f"'{course_name}' 탭을 찾을 수 없습니다."
                await cl.Message(
                    f"출석부 시트에서 **{course_name}** 과목을 찾을 수 없습니다.\n"
                    "과목명을 정확히 입력해주세요."
                ).send()
                return
            step.output = f"**{course_name}** 수강생 **{len(students)}명** 확인"

        async with cl.Step(name="🤖 출석 인식", type="tool") as step:
            ocr_result = await process_attendance_image(
                image_bytes, course_name, students
            )
            recognized = len(ocr_result["results"])
            unrecognized = len(ocr_result["unrecognized"])
            step.output = f"인식 **{recognized}명** / 미인식 **{unrecognized}명**"

        # 결과 미리보기 (최대 10명)
        preview_lines = [f"**{course_name}** 출석 인식 결과 (일부):\n"]
        for r in ocr_result["results"][:10]:
            attended = sum(1 for v in r["출석"].values() if v == "O")
            preview_lines.append(f"- {r['이름']}: {attended}회 출석")
        if len(ocr_result["results"]) > 10:
            preview_lines.append(
                f"... 외 {len(ocr_result['results']) - 10}명"
            )
        if ocr_result["unrecognized"]:
            preview_lines.append(
                f"\n⚠️ 이미지에서 찾지 못한 수강생: "
                f"{', '.join(ocr_result['unrecognized'])}"
            )

        # Store OCR result in session for the callback to use
        cl.user_session.set("pending_ocr_result", ocr_result)
        cl.user_session.set("pending_ocr_students", students)

        await cl.Message(
            content="\n".join(preview_lines) + "\n\n출석부 시트에 반영할까요?",
            actions=[
                cl.Action(name="ocr_apply", label="✅ 반영하기",
                          payload={"value": "apply", "course_name": course_name}),
                cl.Action(name="ocr_retry", label="🔄 이 과목 다시 찍기",
                          payload={"value": "retry", "course_name": course_name}),
                cl.Action(name="ocr_finish", label="✅ 출석 체크 완료",
                          payload={"value": "finish"}),
            ],
        ).send()

    except Exception as e:
        await cl.Message(f"출석 체크 중 오류: {str(e)}").send()
        await send_default_actions()


@cl.action_callback("ocr_apply")
async def on_ocr_apply(action: cl.Action):
    course_name = action.payload.get("course_name", "")
    ocr_result = cl.user_session.get("pending_ocr_result")
    students = cl.user_session.get("pending_ocr_students")
    attendance_sheet_id = cl.user_session.get("attendance_sheet_id")
    term = cl.user_session.get("term") or get_current_term()
    ocr_term_id = term.get("term_id", "")

    if not ocr_result or not students:
        await cl.Message("OCR 결과를 찾을 수 없습니다. 다시 시도해주세요.").send()
        await _ask_for_ocr_image(term)
        return

    async with cl.Step(name="💾 출석부 시트 반영", type="tool") as step:
        updated = await write_attendance_to_sheet(
            attendance_sheet_id, course_name,
            ocr_result["results"], students,
            term_id=ocr_term_id,
        )
        step.output = f"**{updated}명** 반영 완료"

    await cl.Message(f"**{course_name}** 출석 체크가 반영되었습니다.").send()

    # Sheets 반영은 write_attendance_to_sheet()에서 직접 처리됨 — 별도 동기화 불필요

    cl.user_session.set("current_ocr_course", "")
    cl.user_session.set("pending_ocr_result", None)
    cl.user_session.set("pending_ocr_students", None)
    await _show_continue_ocr(term)


@cl.action_callback("ocr_retry")
async def on_ocr_retry(action: cl.Action):
    course_name = action.payload.get("course_name", "")
    cl.user_session.set("current_ocr_course", course_name)
    await cl.Message(f"**{course_name}** 출석부를 다시 촬영하여 업로드해주세요.").send()
    # state stays awaiting_ocr_image


@cl.action_callback("ocr_finish")
async def on_ocr_finish(action: cl.Action):
    cl.user_session.set("state", "idle")
    cl.user_session.set("pending_ocr_result", None)
    cl.user_session.set("pending_ocr_students", None)
    await cl.Message(
        "출석 체크를 완료합니다.\n"
        "모든 과목의 출석 체크가 완료되면 종강 처리를 진행하세요."
    ).send()
    await send_default_actions("ocr")


async def _show_continue_ocr(term: dict):
    """다른 과목 출석 체크 계속 여부 — non-blocking."""
    await cl.Message(
        content="다른 과목의 출석부도 처리하시겠어요?",
        actions=[
            cl.Action(name="ocr_next", label="📸 다른 과목 처리",
                      payload={"value": "next"}),
            cl.Action(name="ocr_done", label="✅ 모두 완료",
                      payload={"value": "done"}),
        ],
    ).send()


@cl.action_callback("ocr_next")
async def on_ocr_next(action: cl.Action):
    term = cl.user_session.get("term") or get_current_term()
    await _ask_for_ocr_image(term)


@cl.action_callback("ocr_done")
async def on_ocr_done(action: cl.Action):
    cl.user_session.set("state", "idle")
    await cl.Message(
        "출석 체크가 완료되었습니다.\n"
        "모든 과목의 출석 체크가 끝났으면 종강 처리를 진행하세요."
    ).send()
    await send_default_actions("ocr")


def _extract_course_name(text: str, attendance_sheet_id: str) -> str:
    """메시지 텍스트에서 출석부 과목 탭명을 추출.

    세션에 저장된 current_ocr_course가 있으면 우선 사용.
    없으면 출석부 시트 탭명과 fuzzy 매칭.
    """
    saved = cl.user_session.get("current_ocr_course", "")
    if saved:
        return saved

    if not text:
        return ""

    from app.services.google_auth import get_sheets_service
    from app.utils.matching import fuzzy_course_match

    svc = get_sheets_service()
    meta = svc.spreadsheets().get(spreadsheetId=attendance_sheet_id).execute()
    course_tabs = [
        s["properties"]["title"]
        for s in meta.get("sheets", [])
        if s["properties"]["title"] != "수강생"
    ]

    # 정확 매칭 우선
    for tab in course_tabs:
        if tab in text:
            return tab

    # fuzzy 매칭 (텍스트 전체를 힌트로 사용)
    matched = fuzzy_course_match(text, course_tabs)
    return matched or ""


# ===================================================== 종강 처리 플로우 =====

async def start_report_flow():
    """보고서 생성 — 미구현 placeholder"""
    await cl.Message("보고서 기능은 아직 준비 중입니다.").send()
    await send_default_actions()


async def start_graduation_flow(message):
    """종강 처리 시작 — 회차 확인 (non-blocking)"""
    term = cl.user_session.get("term") or get_current_term()
    cl.user_session.set("term", term)
    await cl.Message(
        content=f"**{term['term_name']}** 종강 처리를 시작할까요?",
        actions=[
            cl.Action(name="graduation_confirm_term", label="✅ 맞습니다",
                      payload={"value": "confirm"}),
            cl.Action(name="graduation_other_term", label="📅 다른 회차에요",
                      payload={"value": "other"}),
            cl.Action(name="graduation_cancel", label="❌ 취소",
                      payload={"value": "cancel"}),
        ],
    ).send()


@cl.action_callback("graduation_confirm_term")
async def on_graduation_confirm_term(action: cl.Action):
    term = cl.user_session.get("term")
    await _confirm_ocr_done_before_graduation(term)


@cl.action_callback("graduation_other_term")
async def on_graduation_other_term(action: cl.Action):
    cl.user_session.set("state", "awaiting_term_input")
    cl.user_session.set("term_input_next", "graduation")
    await cl.Message("어떤 회차인지 알려주세요.\n예) 2026-1, 겨울학기, 2026년 겨울").send()


@cl.action_callback("graduation_cancel")
async def on_graduation_cancel(action: cl.Action):
    cl.user_session.set("state", "idle")
    await cl.Message("종강 처리가 취소되었습니다.").send()
    await send_default_actions()


async def _confirm_ocr_done_before_graduation(term: dict):
    """종강 처리 전 출석 체크 완료 확인 — non-blocking."""
    await _resolve_attendance_sheet_id(term)
    attendance_sheet_id = cl.user_session.get("attendance_sheet_id")

    sheet_link = (
        f"[출석부 시트 열기](https://docs.google.com/spreadsheets/d/"
        f"{attendance_sheet_id})"
        if attendance_sheet_id else "출석부 시트"
    )

    await cl.Message(
        content=(
            f"종강 처리 전 아래 사항을 확인해주세요.\n\n"
            f"1. ✅ 모든 강좌의 출석 체크(사진 → OCR)가 완료되었습니다.\n"
            f"2. ✅ {sheet_link}에서 과목별 탭의 출석 데이터가 올바르게 "
            f"입력되었습니다.\n\n"
            "확인 후 종강 처리를 시작합니다."
        ),
        actions=[
            cl.Action(name="graduation_confirm_start", label="✅ 확인했습니다, 시작합니다",
                      payload={"value": "start"}),
            cl.Action(name="graduation_not_ready", label="❌ 아직 출석 체크가 안 됐어요",
                      payload={"value": "not_ready"}),
        ],
    ).send()


@cl.action_callback("graduation_confirm_start")
async def on_graduation_confirm_start(action: cl.Action):
    term = cl.user_session.get("term")
    await _run_graduation_process(term)


@cl.action_callback("graduation_not_ready")
async def on_graduation_not_ready(action: cl.Action):
    cl.user_session.set("state", "idle")
    await cl.Message(
        "출석 체크를 먼저 완료해주세요.\n"
        "'✅ 출석 체크' 버튼으로 과목별 출석부 사진을 처리한 뒤 "
        "종강 처리를 진행하세요."
    ).send()
    await send_default_actions()


async def _run_graduation_process(term: dict):
    """종강 처리 실행 — 단계별 직렬 처리 + 중간 진행 표시"""
    cl.user_session.set("state", "running_graduation")
    term_id = term["term_id"]
    attendance_sheet_id = cl.user_session.get("attendance_sheet_id")

    if not attendance_sheet_id:
        await cl.Message(
            "출석부 시트를 찾을 수 없습니다.\n"
            "출석부 생성 및 출석 체크가 완료되었는지 확인해주세요."
        ).send()
        cl.user_session.set("state", "idle")
        await send_default_actions()
        return

    try:
        # Step 1: 출석률 집계
        async with cl.Step(name="📊 출석률 집계", type="tool") as step:
            results = await load_attendance_results(
                attendance_sheet_id, term_id=term_id,
            )
            step.output = f"총 **{len(results)}건** (수강생 × 과목) 집계 완료"

        if not results:
            await cl.Message(
                "출석 데이터가 없습니다. "
                "출석 체크(OCR)가 완료되었는지 확인해주세요."
            ).send()
            cl.user_session.set("state", "idle")
            await send_default_actions()
            return

        # Step 2: 수강생 탭 출석률 업데이트
        async with cl.Step(name="📝 출석률 기록", type="tool") as step:
            await update_attendance_rates_in_sheet(
                attendance_sheet_id, results, term_id,
            )
            step.output = f"수강생 탭 출석률 **{len(results)}건** 업데이트"

        await cl.Message("출석률 집계 완료. 수강기록을 저장합니다...").send()

        # Step 3: 수강기록 저장
        async with cl.Step(name="📋 수강기록 저장", type="tool") as step:
            name_to_id = load_student_name_id_map(attendance_sheet_id)
            course_records = build_course_records(results, name_to_id, term_id)
            if course_records:
                await append_course_records(course_records)
            step.output = f"수강기록 **{len(course_records)}건** 추가"

        # Step 4: 회원목록 재집계
        async with cl.Step(name="📊 회원 통계 재집계", type="tool") as step:
            members = await load_members_from_sheet()
            members = await recalculate_member_stats(members)
            step.output = f"회원 **{len(members)}명** 통계 재집계 완료"

        await cl.Message("회원 통계 재집계 완료. 등급 강등을 처리합니다...").send()

        # Step 5: 등급 강등
        async with cl.Step(name="🔄 등급 강등", type="tool") as step:
            from app.chains.payment import get_active_staff_ids
            active_staff_ids = get_active_staff_ids()
            change_records = apply_demotion(members, term_id, active_staff_ids)

            junior_count = sum(1 for r in change_records if r["사유"] == "종강강등")
            full_count = sum(1 for r in change_records if r["사유"] == "겨울학기강등")

            parts = []
            if junior_count:
                parts.append(f"준회원 → 회원 **{junior_count}명**")
            if full_count:
                parts.append(f"정회원 → 회원 **{full_count}명**")
            step.output = ", ".join(parts) if parts else "등급 변경 없음"

        # Step 6: 저장
        async with cl.Step(name="💾 저장", type="tool") as step:
            await update_members_sheet(members)
            if change_records:
                await append_member_records(change_records)
            step.output = "회원목록 + 등급변경 이력 저장 완료"

        # 최종 안내
        members_link = (
            f"https://docs.google.com/spreadsheets/d/{MEMBERS_SHEET_ID}"
        )

        result_msg = (
            f"✅ **{term['term_name']}** 종강 처리가 완료되었습니다!\n\n"
            f"- 수강기록 추가: **{len(course_records)}건**\n"
            f"- 준회원 → 회원 강등: **{junior_count}명**\n"
        )
        if full_count:
            result_msg += f"- 정회원 → 회원 강등: **{full_count}명**\n"
        result_msg += f"\n[회원관리 시트 열기]({members_link})"

        await cl.Message(content=result_msg).send()
        await send_default_actions("graduation")

    except Exception as e:
        await cl.Message(f"종강 처리 중 오류: {str(e)}").send()
        await send_default_actions()

    cl.user_session.set("state", "idle")


# ===================================================== LLM 의도 분류 =====

INTENT_LABELS = {
    "payment": "입금 대조",
    "attendance": "출석부 생성",
    "ocr": "출석 체크",
    "graduation": "종강 처리",
    "plan": "계획서 검토",
}


async def classify_intent_llm(text: str) -> dict:
    """자유 텍스트에서 LLM으로 의도를 분류. max_tokens=150."""
    llm = ChatAnthropic(
        model=LLM_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=150,
    )

    system_prompt = """당신은 위례인생학교 관리 시스템의 의도 분류기입니다.
관리자의 메시지를 보고 아래 중 하나로 분류하세요.

작업 목록:
- payment: 입금 대조, 입금 확인, 입금 처리, 입금 매칭
- attendance: 출석부 생성, 출석부 만들기, 출석부
- ocr: 출석 체크, 출석 확인, OCR, 사진으로 출석
- graduation: 종강 처리, 종강, 학기 마무리, 출석률 계산, 등급 강등
- plan: 계획서 검토, 강의 계획서, 강의계획서
- question: 위 작업에 대한 질문이나 설명 요청, 또는 위 어디에도 해당 안 되는 내용. "~이 뭐야?", "~가 뭔가요?", "~는 어떻게 해?", "~를 설명해줘" 처럼 정보를 얻으려는 의도이면 무조건 question으로 분류. 작업을 직접 실행하려는 의도가 명확할 때만 payment/attendance/ocr/plan으로 분류.

응답은 반드시 JSON만 출력하세요. 설명 금지.
{"intent": "payment", "term": "2026-2", "confidence": 0.95}

term: 메시지에 회차 정보가 있으면 추출 (예: "2026-1", "봄학기"). 없으면 null.
confidence: 0.0~1.0 (0.8 이상이면 확신, 미만이면 불확실)."""

    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=text),
        ])
        content = response.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        result = json.loads(content)
        return {
            "intent": result.get("intent", "question"),
            "term": result.get("term"),
            "confidence": float(result.get("confidence", 0.0)),
        }
    except Exception:
        return {"intent": "question", "term": None, "confidence": 0.0}


async def ask_intent_confirm(intent_result: dict):
    """의도 분류 결과를 관리자에게 확인 — non-blocking."""
    intent = intent_result["intent"]
    confidence = intent_result["confidence"]
    label = INTENT_LABELS.get(intent, intent)

    if confidence >= 0.8:
        prompt = f"**{label}**을(를) 시작할까요?"
        confirm_label = "✅ 네, 시작해주세요"
    else:
        prompt = f"혹시 **{label}**을(를) 원하시는 건가요?"
        confirm_label = "✅ 맞아요"

    await cl.Message(
        content=prompt,
        actions=[
            cl.Action(
                name="intent_confirm",
                label=confirm_label,
                payload={"value": "confirm", "intent": intent, "term": intent_result.get("term")},
            ),
            cl.Action(
                name="intent_deny",
                label="❌ 아니요, 다른 작업이에요",
                payload={"value": "deny"},
            ),
        ],
    ).send()


@cl.action_callback("intent_confirm")
async def on_intent_confirm(action: cl.Action):
    intent = action.payload.get("intent")
    term_text = action.payload.get("term")
    await _route_to_workflow(intent, term_text)


@cl.action_callback("intent_deny")
async def on_intent_deny(action: cl.Action):
    await send_default_actions()


async def _route_to_workflow(intent: str, term_text: str | None):
    """의도에 맞는 워크플로우로 진입"""
    if intent == "payment":
        if term_text:
            term = parse_term_input(term_text)
            if term:
                await _show_payment_term_confirm(term)
                return
        term = get_current_term()
        await _show_payment_term_confirm(term)
    elif intent == "attendance":
        await start_attendance_flow(None)
    elif intent == "ocr":
        await start_ocr_flow(None)
    elif intent == "graduation":
        await start_graduation_flow(None)
    elif intent == "plan":
        await cl.Message("계획서 검토 기능은 준비 중입니다.").send()


# ===================================================== 공통 액션 버튼 =====

async def send_default_actions(completed: str | None = None):
    """모든 작업 완료/종료 후 공통으로 호출하는 기본 액션 버튼.

    completed: 방금 완료한 작업 키 — 해당 작업은 "다시하기" 레이블로 표시.
    """
    definitions = [
        ("plan", "📝 계획서 검토"),
        ("payment", "💰 입금 대조"),
        ("attendance", "📋 출석부 생성"),
        ("ocr", "✅ 출석 체크"),
        ("graduation", "🎓 종강 처리"),
        ("report", "📊 보고서 생성"),
        ("question", "❓ 질문하기"),
    ]
    actions = []
    for key, label in definitions:
        display = label + " 다시하기" if key == completed else label
        actions.append(cl.Action(
            name=f"default_{key}",
            label=display,
            payload={"value": key},
        ))

    await cl.Message(content="무엇을 도와드릴까요?", actions=actions).send()


@cl.action_callback("default_payment")
async def on_default_payment(action: cl.Action):
    term = get_current_term()
    await _show_payment_term_confirm(term)


@cl.action_callback("default_attendance")
async def on_default_attendance(action: cl.Action):
    await start_attendance_flow(None)


@cl.action_callback("default_ocr")
async def on_default_ocr(action: cl.Action):
    await start_ocr_flow(None)


@cl.action_callback("default_graduation")
async def on_default_graduation(action: cl.Action):
    await start_graduation_flow(None)


@cl.action_callback("default_plan")
async def on_default_plan(action: cl.Action):
    await cl.Message("계획서 검토 기능은 준비 중입니다.").send()
    await send_default_actions()


@cl.action_callback("default_report")
async def on_default_report(action: cl.Action):
    await start_report_flow()


@cl.action_callback("default_question")
async def on_default_question(action: cl.Action):
    cl.user_session.set("state", "idle")
    await cl.Message("궁금한 점을 자유롭게 질문해주세요.").send()


# ===================================================== 유틸 =====

def _clear_payment_session():
    cl.user_session.set("state", "idle")
    cl.user_session.set("matched_results", None)
    cl.user_session.set("applications", None)

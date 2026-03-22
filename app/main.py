"""Chainlit 엔트리포인트 — 위례인생학교 업무 도우미"""

import json
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
    update_applications_sheet,
    load_members_from_sheet,
    run_llm_matching,
    find_unpaid,
    format_results,
    append_member_records,
)
from app.chains.attendance import create_attendance_sheet
from app.config import ANTHROPIC_API_KEY, LLM_MODEL
from app.services.excel import parse_bank_statement, parse_applicant_list
from app.services.google_drive import find_term_folder
from app.services.signup_loader import (
    load_member_signups_from_drive,
    load_fullmember_signups_from_drive,
)
from app.utils.matching import run_code_matching
from app.context.term import get_current_term, parse_term_input

# 워크플로우 중 취소 의도 감지 키워드
CANCEL_KEYWORDS = ["취소", "중단", "그만", "멈춰", "stop", "cancel", "안 할게", "안할게", "나가기"]


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
        cl.Starter(
            label="💰 입금 대조",
            message="입금 대조를 시작합니다.",
        ),
        cl.Starter(
            label="📋 출석부 생성",
            message="출석부를 생성합니다.",
        ),
        cl.Starter(
            label="✅ 출석 체크",
            message="출석 체크를 시작합니다.",
        ),
        cl.Starter(
            label="📝 계획서 검토",
            message="강의 계획서를 검토합니다.",
        ),
        cl.Starter(
            label="❓ 업무 관련 질문",
            message="업무 관련 질문이 있습니다.",
        ),
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

    # Starter 버튼 메시지 라우팅
    if message.content == "입금 대조를 시작합니다.":
        await start_payment_flow(message)
    elif message.content == "출석부를 생성합니다.":
        await start_attendance_flow(message)
    elif message.content == "출석 체크를 시작합니다.":
        await cl.Message("출석 체크 기능은 준비 중입니다.").send()
    elif message.content == "강의 계획서를 검토합니다.":
        await cl.Message("계획서 검토 기능은 준비 중입니다.").send()
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
    await start_payment_flow_with_term(term)


async def start_payment_flow_with_term(term: dict):
    """회차 확인 AskActionMessage — 루프 재진입점"""
    cl.user_session.set("term", term)

    res = await cl.AskActionMessage(
        content=f"**{term['term_name']}** 입금 대조를 시작할까요?",
        actions=[
            cl.Action(name="confirm_term", label="✅ 맞습니다", payload={"value": "confirm"}),
            cl.Action(name="other_term", label="📅 다른 회차에요", payload={"value": "other"}),
            cl.Action(name="cancel_term", label="❌ 취소", payload={"value": "cancel"}),
        ],
    ).send()

    value = (res or {}).get("payload", {}).get("value")
    if value == "confirm":
        await _ask_to_confirm_signup_files(term)
    elif value == "other":
        cl.user_session.set("state", "awaiting_term_input")
        await cl.Message(
            "어떤 회차인지 알려주세요.\n예) 2026-2, 2026년 봄, 봄학기"
        ).send()
    else:
        await cl.Message("입금 대조가 취소되었습니다.").send()
        cl.user_session.set("state", "idle")


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
    }
    return prompts.get(state, "계속 진행하려면 파일을 업로드해주세요.\n취소하려면 '취소'라고 입력하세요.")


async def _ask_to_confirm_signup_files(term: dict):
    """신청서 파일이 올바른 위치에 있는지 관리자에게 확인"""
    year = term["year"]
    res = await cl.AskActionMessage(
        content=(
            f"입금 대조 전, **{year}년** 가입 신청서 응답 파일 위치를 확인해주세요.\n\n"
            f"**확인 위치**:\n"
            f"- 신규가입 신청서: `03 회원과 강사 > 회원 > 신규가입 신청서/`\n"
            f"- 정회원가입 신청서: `03 회원과 강사 > 회원 > 정회원가입 신청서/`\n\n"
            f"신청서가 없거나 이번 회차에 해당 없으면 건너뛰기를 선택하세요."
        ),
        actions=[
            cl.Action(
                name="signup_ready",
                label="✅ 확인했습니다",
                payload={"value": "ready"},
            ),
            cl.Action(
                name="signup_skip",
                label="⏭️ 해당 없음 / 건너뛰기",
                payload={"value": "skip"},
            ),
            cl.Action(
                name="signup_cancel",
                label="❌ 취소",
                payload={"value": "cancel"},
            ),
        ],
    ).send()

    value = (res or {}).get("payload", {}).get("value")
    if value in ("ready", "skip"):
        await _ask_for_applicants_file()
    else:
        await cl.Message("입금 대조가 취소되었습니다.").send()
        cl.user_session.set("state", "idle")
        await send_default_actions()


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
        async with cl.Step(name="📊 신청자 목록 분석") as step:
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
            courses = set(a.get("과목명", "") for a in applicants if a.get("과목명"))
            step.output = f"수강 신청자 **{len(applicants)}명** 확인 ({len(courses)}개 과목)"

        # Step 2-3: Drive에서 신규가입/정회원가입 신청서 자동 로드 (기존 cl.Step 사용)
        member_records, fullmember_records = await _load_signup_data(str(term["year"]))

        # Step 4: 통합 신청서 생성 + Sheets 저장
        async with cl.Step(name="📝 통합 신청서 생성") as step:
            applications = build_applications(applicants, member_records, fullmember_records)

            term_folder_id = cl.user_session.get("term_folder_id")
            if not term_folder_id:
                term_folder = find_term_folder(term_id)
                if term_folder:
                    term_folder_id = term_folder["id"]
                    cl.user_session.set("term_folder_id", term_folder_id)

            app_sheet_id = None
            if term_folder_id:
                try:
                    app_sheet_id = write_applications_sheet(term_folder_id, applications)
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

        sheet_note = (
            f"\n[신청서 시트 열기](https://docs.google.com/spreadsheets/d/{app_sheet_id})"
            if app_sheet_id else ""
        )

        await cl.Message(
            content=(
                f"**{term['term_name']}** 통합 신청서 생성 완료:{sheet_note}\n\n"
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
    async with cl.Step(name="📋 신규가입 신청서 로드") as step:
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
    async with cl.Step(name="📋 정회원가입 신청서 로드") as step:
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
        async with cl.Step(name="💰 입금내역 분석") as step:
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

        # Step 2: 회원 정보 로드 + 정회원 면제 처리
        async with cl.Step(name="👥 회원 정보 로드") as step:
            members = load_members_from_sheet()
            exempted = apply_exemptions(applications, members)
            students = applications_to_students(applications)
            step.output = (
                f"회원 **{len(members)}명** 로드, "
                f"정회원 면제 **{len(exempted)}건** 처리"
            )

        # Step 3: 규칙 기반 매칭
        async with cl.Step(name="🔍 규칙 기반 매칭") as step:
            all_results, unmatched = run_code_matching(transactions, students)
            code_matched = sum(1 for r in all_results if r["상태"] == "✅정상")
            step.output = f"✅ {code_matched}건 매칭 / 🔶 {len(unmatched)}건 미매칭"

        # Step 4: LLM 매칭 (미매칭 건이 있을 때만)
        llm_unmatched = [r for r in unmatched if r["상태"] != "⏭️스킵"]
        if llm_unmatched:
            async with cl.Step(name="🤖 AI 매칭") as step:
                await run_llm_matching(llm_unmatched, students)
                llm_resolved = sum(
                    1 for r in llm_unmatched if r["상태"] != "🔶확인필요"
                )
                step.output = f"AI 분석 완료: **{llm_resolved}건** 추가 매칭"

        # 매칭 결과를 applications에 반영
        apply_matching_results(applications, all_results)

        cl.user_session.set("applications", applications)
        cl.user_session.set("matched_results", all_results)

        # 즉시 시트에 자동 반영
        await write_payment_results(all_results, exempted)

    except Exception as e:
        await cl.Message(f"입금 대조 중 오류가 발생했습니다: {str(e)}").send()
        cl.user_session.set("state", "idle")


async def handle_term_input(message: cl.Message):
    """회차 자유 텍스트 입력 파싱 → 확인 루프"""
    term = parse_term_input(message.content)
    if term:
        await start_payment_flow_with_term(term)
    else:
        await cl.Message(
            "회차를 인식하지 못했어요. 다시 입력해주세요.\n"
            "예) 2026-2, 봄학기, 2026년 여름"
        ).send()
        # 상태 유지 — awaiting_term_input


async def write_payment_results(
    matched_results: list[dict] | None = None,
    exempted: list[dict] | None = None,
):
    """매칭 결과를 신청서 Sheets에 자동 반영 → 요약 + 다음 단계 Action 제공"""
    try:
        app_sheet_id = cl.user_session.get("applications_sheet_id")
        applications = cl.user_session.get("applications", [])

        async with cl.Step(name="💾 신청서 시트 업데이트") as step:
            if app_sheet_id and applications:
                update_applications_sheet(app_sheet_id, applications)
                step.output = f"입금현황 **{len(applications)}건** 반영 완료"

                # 회원기록 자동 기록
                term_id = (cl.user_session.get("term") or {}).get("term_id", "")
                records_to_log = []
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                for app in applications:
                    if app.get("입금현황") != "✅정상":
                        continue
                    if app["유형"] == "신규가입":
                        records_to_log.append({
                            "이름ID": app["이름ID"], "이름": app["이름"],
                            "변경일시": now_str,
                            "변경전등급": "(신규)", "변경후등급": "회원",
                            "사유": "신규가입", "관련회차": term_id,
                        })
                    elif app["유형"] == "수강":
                        records_to_log.append({
                            "이름ID": app["이름ID"], "이름": app["이름"],
                            "변경일시": now_str,
                            "변경전등급": "회원", "변경후등급": "준회원",
                            "사유": "수강료입금", "관련회차": term_id,
                        })
                    elif app["유형"] == "정회원":
                        records_to_log.append({
                            "이름ID": app["이름ID"], "이름": app["이름"],
                            "변경일시": now_str,
                            "변경전등급": "회원", "변경후등급": "정회원",
                            "사유": "정회원비입금", "관련회차": term_id,
                        })
                if records_to_log:
                    append_member_records(records_to_log)
                    step.output += f", 회원기록 {len(records_to_log)}건 기록"
            else:
                step.output = "시트 정보가 없어 반영하지 못했습니다."

        # 숫자 요약
        if matched_results is None:
            matched_results = cl.user_session.get("matched_results", [])
        summary_line = format_results(matched_results, applications, exempted)

        needs_check = sum(
            1 for r in matched_results if r["상태"] == "🔶확인필요"
        )
        check_note = (
            "\n\n🔶 확인이 필요한 건이 있습니다. 신청서 시트에서 직접 확인해주세요."
            if needs_check else ""
        )

        sheet_link = (
            f"\n\n[신청서 시트 열기](https://docs.google.com/spreadsheets/d/{app_sheet_id})"
            if app_sheet_id else ""
        )

        await cl.Message(
            content=(
                f"입금 대조가 완료되었습니다.\n\n"
                f"{summary_line}{check_note}\n\n"
                f"신청서 시트에서 입금현황을 확인하시고, "
                f"배움숲 포탈에서 수강 등록을 처리한 뒤\n"
                f"등록상태 체크박스를 클릭해주세요.{sheet_link}"
            ),
        ).send()

        await send_default_actions("payment")

    except Exception as e:
        await cl.Message(f"저장 중 오류: {str(e)}").send()
        await send_default_actions()

    _clear_payment_session()


# ===================================================== 출석부 생성 플로우 =====

async def start_attendance_flow(message: cl.Message | None):
    """출석부 생성 — Starter 버튼 또는 워크플로우 라우팅에서 진입"""
    term = cl.user_session.get("term") or get_current_term()
    cl.user_session.set("term", term)

    msg = cl.Message(content=f"**{term['term_name']}** 출석부 생성을 준비하는 중...")
    await msg.send()

    try:
        # 회차 폴더 탐색
        term_folder_id = cl.user_session.get("term_folder_id")
        if not term_folder_id:
            term_folder = find_term_folder(term["term_id"])
            if not term_folder:
                msg.content = (
                    f"Drive에서 **{term['term_name']}** 폴더를 찾을 수 없습니다.\n"
                    f"학사운영(연도별) → {term['year']} → {term['term_id']}... 폴더가 있는지 확인해주세요."
                )
                await msg.update()
                return
            term_folder_id = term_folder["id"]
            cl.user_session.set("term_folder_id", term_folder_id)

        # 신청서 시트 확인
        app_sheet_id = cl.user_session.get("applications_sheet_id")

        if app_sheet_id:
            confirm_content = (
                "출석부 생성 전 아래 3단계가 완료되었는지 확인해주세요.\n\n"
                "1. ✅ 입금 대조 완료\n"
                "2. ✅ 배움숲 포탈에서 수강 등록 처리 완료\n"
                f"3. ✅ [신청서 시트](https://docs.google.com/spreadsheets/d/{app_sheet_id})의 "
                "등록상태 열 체크박스 클릭 완료\n\n"
                "모두 완료되셨으면 출석부를 생성합니다."
            )
        else:
            confirm_content = (
                "출석부 생성 전 아래 3단계가 완료되었는지 확인해주세요.\n\n"
                "1. ✅ 입금 대조 완료\n"
                "2. ✅ 배움숲 포탈에서 수강 등록 처리 완료\n"
                "3. ✅ 신청서 시트의 등록상태 열 체크박스 클릭 완료\n\n"
                "모두 완료되셨으면 출석부를 생성합니다."
            )

        res = await cl.AskActionMessage(
            content=confirm_content,
            actions=[
                cl.Action(
                    name="confirm_attendance",
                    label="✅ 등록 완료, 출석부 생성",
                    payload={"value": "confirm"},
                ),
                cl.Action(
                    name="cancel_attendance",
                    label="❌ 아직 안 했어요",
                    payload={"value": "cancel"},
                ),
            ],
        ).send()

        if res and res.get("payload", {}).get("value") == "confirm":
            await do_create_attendance()
        else:
            if app_sheet_id:
                guide = (
                    "아직 완료되지 않은 단계가 있다면 아래 순서로 진행해주세요.\n\n"
                    "**1단계** — 신청서 시트에서 입금현황 확인\n"
                    f"→ [신청서 시트 열기](https://docs.google.com/spreadsheets/d/{app_sheet_id})\n\n"
                    "**2단계** — 배움숲 포탈에서 수강 등록 처리\n"
                    "→ 배움숲 포탈 접속 → 수강신청관리 → 등록 처리\n\n"
                    "**3단계** — 신청서 시트로 돌아와 등록상태 열의 체크박스 클릭\n\n"
                    "3단계까지 완료되면 '📋 출석부 생성' 버튼을 다시 눌러주세요."
                )
            else:
                guide = (
                    "아직 완료되지 않은 단계가 있다면 아래 순서로 진행해주세요.\n\n"
                    "**1단계** — 입금 대조를 먼저 진행해주세요.\n"
                    "**2단계** — 배움숲 포탈에서 수강 등록 처리\n"
                    "**3단계** — 신청서 시트의 등록상태 열 체크박스 클릭\n\n"
                    "완료 후 '📋 출석부 생성' 버튼을 다시 눌러주세요."
                )
            await cl.Message(guide).send()
            await send_default_actions()

    except Exception as e:
        msg.content = f"출석부 생성 준비 중 오류: {str(e)}"
        await msg.update()
        await send_default_actions()


async def do_create_attendance():
    """출석부 생성 실행"""
    term = cl.user_session.get("term") or get_current_term()
    term_folder_id = cl.user_session.get("term_folder_id")
    app_sheet_id = cl.user_session.get("applications_sheet_id")

    if not term_folder_id:
        await cl.Message(
            "회차 폴더 정보를 찾을 수 없습니다. 입금 대조를 먼저 완료해주세요."
        ).send()
        return

    if not app_sheet_id:
        await cl.Message(
            "신청서 시트 정보를 찾을 수 없습니다. 입금 대조를 먼저 완료해주세요."
        ).send()
        return

    try:
        async with cl.Step(name="📊 등록 수강생 확인") as step:
            from app.chains.attendance import load_registered_students
            registered = load_registered_students(app_sheet_id)
            courses = set(s.get("과목명", "") for s in registered if s.get("과목명"))
            step.output = (
                f"등록상태 체크된 수강생 **{len(registered)}명** ({len(courses)}개 과목)"
            )

        if not registered:
            await cl.Message(
                "등록상태가 체크된 수강생이 없습니다. "
                "배움숲에서 등록 처리 후 신청서 시트에 등록상태를 체크해주세요."
            ).send()
            cl.user_session.set("state", "idle")
            return

        async with cl.Step(name="📋 출석부 시트 생성") as step:
            result = await create_attendance_sheet(
                term["term_id"], term_folder_id, app_sheet_id
            )
            step.output = (
                f"과목별 시트탭 **{len(result['courses'])}개** 생성, "
                f"수강생 **{result['total_students']}명**, 출석률 수식 삽입"
            )

        await cl.Message(
            content=(
                f"## 출석부 생성 완료\n\n"
                f"- 과목 수: {len(result['courses'])}개\n"
                f"- 총 수강생: {result['total_students']}명\n"
                f"- 과목: {', '.join(result['courses'])}\n\n"
                f"[출석부 열기]({result['spreadsheet_url']})"
            )
        ).send()
        await send_default_actions("attendance")

    except Exception as e:
        await cl.Message(f"출석부 생성 중 오류: {str(e)}").send()
        await send_default_actions()

    cl.user_session.set("state", "idle")


# ===================================================== LLM 의도 분류 =====

INTENT_LABELS = {
    "payment": "입금 대조",
    "attendance": "출석부 생성",
    "ocr": "출석 체크",
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
    """의도 분류 결과를 관리자에게 확인"""
    intent = intent_result["intent"]
    confidence = intent_result["confidence"]
    label = INTENT_LABELS.get(intent, intent)

    if confidence >= 0.8:
        prompt = f"**{label}**을(를) 시작할까요?"
        confirm_label = "✅ 네, 시작해주세요"
    else:
        prompt = f"혹시 **{label}**을(를) 원하시는 건가요?"
        confirm_label = "✅ 맞아요"

    res = await cl.AskActionMessage(
        content=prompt,
        actions=[
            cl.Action(
                name="intent_confirm",
                label=confirm_label,
                payload={"value": "confirm", "intent": intent, "term": intent_result["term"]},
            ),
            cl.Action(
                name="intent_deny",
                label="❌ 아니요, 다른 작업이에요",
                payload={"value": "deny"},
            ),
        ],
    ).send()

    value = (res or {}).get("payload", {}).get("value")
    if value == "confirm":
        await _route_to_workflow(intent, intent_result.get("term"))
    elif value == "deny":
        await send_default_actions()
    # res=None (타임아웃) 시 아무것도 하지 않음


async def _route_to_workflow(intent: str, term_text: str | None):
    """의도에 맞는 워크플로우로 진입"""
    if intent == "payment":
        if term_text:
            term = parse_term_input(term_text)
            if term:
                await start_payment_flow_with_term(term)
                return
        term = get_current_term()
        await start_payment_flow_with_term(term)
    elif intent == "attendance":
        await start_attendance_flow(None)
    elif intent == "ocr":
        await cl.Message("출석 체크 기능은 준비 중입니다.").send()
    elif intent == "plan":
        await cl.Message("계획서 검토 기능은 준비 중입니다.").send()


# ===================================================== 공통 액션 버튼 =====

async def send_default_actions(completed: str | None = None):
    """모든 작업 완료/종료 후 공통으로 호출하는 기본 액션 버튼.

    completed: 방금 완료한 작업 키 — 해당 작업은 "다시하기" 레이블로 표시.
    """
    definitions = [
        ("payment", "💰 입금 대조"),
        ("attendance", "📋 출석부 생성"),
        ("ocr", "✅ 출석 체크"),
        ("plan", "📝 계획서 검토"),
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
    await start_payment_flow_with_term(term)


@cl.action_callback("default_attendance")
async def on_default_attendance(action: cl.Action):
    await start_attendance_flow(None)


@cl.action_callback("default_ocr")
async def on_default_ocr(action: cl.Action):
    await cl.Message("출석 체크 기능은 준비 중입니다.").send()
    await send_default_actions()


@cl.action_callback("default_plan")
async def on_default_plan(action: cl.Action):
    await cl.Message("계획서 검토 기능은 준비 중입니다.").send()
    await send_default_actions()


@cl.action_callback("default_question")
async def on_default_question(action: cl.Action):
    cl.user_session.set("state", "idle")
    await cl.Message("궁금한 점을 자유롭게 질문해주세요.").send()


# ===================================================== 유틸 =====

def _clear_payment_session():
    cl.user_session.set("state", "idle")
    cl.user_session.set("matched_results", None)
    cl.user_session.set("applications", None)

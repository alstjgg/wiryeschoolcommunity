"""Chainlit 엔트리포인트 — 위례인생학교 업무 도우미"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 데이터 레이어 등록 (DATABASE_URL 있을 때만 활성화 — 다른 import보다 먼저)
import app.services.chat_data_layer  # noqa: F401

import chainlit as cl
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
)
from app.chains.attendance import create_attendance_sheet
from app.services.excel import parse_bank_statement, parse_applicant_list
from app.services.google_drive import find_term_folder
from app.services.signup_loader import (
    load_member_signups_from_drive,
    load_fullmember_signups_from_drive,
)
from app.utils.matching import run_code_matching
from app.context.term import get_current_term


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
        await handle_applicants_file(message)
        return
    if session_state == "awaiting_payment_file":
        await handle_payment_file(message)
        return
    if session_state == "awaiting_payment_confirm":
        await handle_payment_confirm(message)
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
        # Q&A 폴백
        msg = cl.Message(content="")
        await msg.send()
        try:
            response = await answer_question(message.content)
            msg.content = response
            await msg.update()
        except Exception as e:
            msg.content = (
                f"오류가 발생했습니다: {str(e)}\n\n"
                "환경 변수(ANTHROPIC_API_KEY)가 올바르게 설정되어 있는지 확인해주세요."
            )
            await msg.update()


# ===================================================== 입금 대조 플로우 =====

async def start_payment_flow(message: cl.Message):
    """입금 대조 시작 — 회차 추측 → 확인 → 신청자 파일 업로드 요청"""
    term = get_current_term()
    cl.user_session.set("term", term)

    res = await cl.AskActionMessage(
        content=f"**{term['term_name']}** 입금 대조를 시작할까요?",
        actions=[
            cl.Action(name="confirm_term", label="✅ 맞습니다", payload={"value": "confirm"}),
            cl.Action(name="cancel_term", label="❌ 취소", payload={"value": "cancel"}),
        ],
    ).send()

    if res and res.get("payload", {}).get("value") == "confirm":
        await _ask_for_applicants_file()
    else:
        await cl.Message("입금 대조가 취소되었습니다.").send()
        cl.user_session.set("state", "idle")


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
    if not message.elements:
        await cl.Message(
            "배움숲에서 다운로드한 신청자 목록 파일(.xls)을 업로드해주세요."
        ).send()
        return

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
        member_records, fullmember_records = await _load_signup_data(term_id)

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


async def _load_signup_data(term_id: str) -> tuple[list[dict], list[dict]]:
    """Drive에서 신규가입·정회원가입 신청서를 자동 로드.

    파일을 못 찾으면 경고만 표시하고 빈 리스트 반환.
    Returns: (member_records, fullmember_records)
    """
    member_records = []
    fullmember_records = []

    # 신규가입 신청서
    async with cl.Step(name="📋 신규가입 신청서 로드") as step:
        result = load_member_signups_from_drive(term_id)
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
        result = load_fullmember_signups_from_drive(term_id)
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
    if not message.elements:
        await cl.Message("입금내역 파일을 업로드해주세요. (.xls 또는 .xlsx)").send()
        return

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

        summary = format_results(all_results, applications, exempted)

        actions = [
            cl.Action(
                name="write_results",
                label="✅ 신청서에 반영하기",
                payload={"value": "write"},
            ),
            cl.Action(
                name="cancel_results",
                label="❌ 취소",
                payload={"value": "cancel"},
            ),
        ]
        await cl.Message(content=summary, actions=actions).send()

        cl.user_session.set("state", "awaiting_payment_confirm")
        cl.user_session.set("matched_results", all_results)

    except Exception as e:
        await cl.Message(f"입금 대조 중 오류가 발생했습니다: {str(e)}").send()
        cl.user_session.set("state", "idle")


async def handle_payment_confirm(message: cl.Message):
    """텍스트 입력으로 시트 반영 확인 (AskActionMessage 폴백)"""
    text = message.content.strip()
    if any(word in text for word in ["예", "네", "응", "확인", "반영"]):
        await write_payment_results()
    else:
        await cl.Message("입금 대조 결과가 반영되지 않았습니다.").send()
        _clear_payment_session()


@cl.action_callback("write_results")
async def on_write_results(action: cl.Action):
    await write_payment_results()


@cl.action_callback("cancel_results")
async def on_cancel_results(action: cl.Action):
    await cl.Message("입금 대조 결과가 반영되지 않았습니다.").send()
    _clear_payment_session()


async def write_payment_results():
    """매칭 결과를 신청서 Sheets에 반영 → 다음 단계 Action 제공"""
    try:
        app_sheet_id = cl.user_session.get("applications_sheet_id")
        applications = cl.user_session.get("applications", [])

        async with cl.Step(name="💾 신청서 시트 업데이트") as step:
            if app_sheet_id and applications:
                update_applications_sheet(app_sheet_id, applications)
                step.output = f"입금현황 **{len(applications)}건** 반영 완료"
            else:
                step.output = "시트 정보가 없어 반영하지 못했습니다."

        sheet_note = (
            f"\n[신청서 시트 열기](https://docs.google.com/spreadsheets/d/{app_sheet_id})"
            if app_sheet_id else ""
        )
        await cl.Message(
            content=(
                f"입금 대조 결과가 신청서에 반영되었습니다.{sheet_note}\n\n"
                "신청서 시트에서 입금현황을 확인하시고, "
                "배움숲 포탈에서 수강 등록을 처리한 뒤 등록상태를 체크해주세요."
            )
        ).send()

        actions = [
            cl.Action(
                name="create_attendance",
                label="📋 출석부 생성하기",
                payload={"value": "attendance"},
            ),
            cl.Action(
                name="redo_payment",
                label="🔄 입금대조 다시하기",
                payload={"value": "redo"},
            ),
            cl.Action(
                name="free_question",
                label="❓ 다른 질문하기",
                payload={"value": "question"},
            ),
        ]
        await cl.Message(content="다음 작업을 선택해주세요.", actions=actions).send()

    except Exception as e:
        await cl.Message(f"저장 중 오류: {str(e)}").send()

    _clear_payment_session()


@cl.action_callback("create_attendance")
async def on_create_attendance(action: cl.Action):
    await do_create_attendance()


@cl.action_callback("redo_payment")
async def on_redo_payment(action: cl.Action):
    cl.user_session.set("state", "awaiting_payment_file")
    await cl.Message("입금내역 파일(.xls 또는 .xlsx)을 다시 업로드해주세요.").send()


@cl.action_callback("free_question")
async def on_free_question(action: cl.Action):
    cl.user_session.set("state", "idle")
    await cl.Message("궁금한 점을 자유롭게 질문해주세요.").send()


# ===================================================== 출석부 생성 플로우 =====

async def start_attendance_flow(message: cl.Message):
    """출석부 생성 — Starter 버튼에서 독립 진입"""
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
        sheet_note = (
            f"[신청서 시트](https://docs.google.com/spreadsheets/d/{app_sheet_id})에서 "
            "배움숲 수강 등록을 완료하셨나요?"
            if app_sheet_id
            else "신청서 시트에서 배움숲 수강 등록을 완료하셨나요?"
        )

        res = await cl.AskActionMessage(
            content=f"{sheet_note}\n\n등록상태를 기준으로 출석부를 생성합니다.",
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
            await cl.Message(
                "배움숲 포탈에서 수강 등록을 완료한 뒤 신청서 시트의 등록상태를 체크해주세요.\n"
                "완료 후 '📋 출석부 생성' 버튼을 다시 눌러주세요."
            ).send()

    except Exception as e:
        msg.content = f"출석부 생성 준비 중 오류: {str(e)}"
        await msg.update()


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

    except Exception as e:
        await cl.Message(f"출석부 생성 중 오류: {str(e)}").send()

    cl.user_session.set("state", "idle")


def _clear_payment_session():
    cl.user_session.set("state", "idle")
    cl.user_session.set("matched_results", None)
    cl.user_session.set("applications", None)

"""Chainlit 엔트리포인트 — 위례인생학교 업무 도우미"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chainlit as cl
from app.chains.qa import answer_question
from app.chains.payment import (
    load_applicants_from_drive,
    create_students_sheet,
    load_students_from_sheet,
    load_members,
    apply_exemptions,
    run_llm_matching,
    find_unpaid_students,
    format_results,
    write_results_to_sheet,
    update_members_after_registration,
    add_enrollment_records,
)
from app.chains.attendance import create_attendance_sheet
from app.services.excel import parse_bank_statement
from app.services.google_drive import find_term_folder
from app.utils.matching import run_code_matching
from app.context.term import get_current_term


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
    if session_state == "awaiting_term_confirm":
        await handle_term_confirm(message)
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


async def start_payment_flow(message: cl.Message):
    """입금 대조 시작 — 회차 추측 → 확인 요청"""
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
        await process_term_confirmed()
    else:
        await cl.Message("입금 대조가 취소되었습니다.").send()
        cl.user_session.set("state", "idle")


async def handle_term_confirm(message: cl.Message):
    """회차 확인 응답 처리 (텍스트 입력 시 폴백)"""
    text = message.content.strip()
    if any(word in text for word in ["예", "네", "응", "확인", "맞"]):
        await process_term_confirmed()
    else:
        await cl.Message("입금 대조가 취소되었습니다.").send()
        cl.user_session.set("state", "idle")


async def process_term_confirmed():
    """회차 확정 후 — Drive에서 회차 폴더 탐색 → 신청자 로드 → 수강생 시트 생성"""
    term = cl.user_session.get("term")

    msg = cl.Message(content="회차 폴더를 찾는 중...")
    await msg.send()

    try:
        # 1. Drive에서 회차 폴더 동적 탐색
        term_folder = find_term_folder(term["term_id"])
        if not term_folder:
            msg.content = (
                f"Drive에서 **{term['term_name']}** 폴더를 찾을 수 없습니다.\n"
                f"학사운영(연도별) → {term['year']} → {term['term_id']}... 폴더가 있는지 확인해주세요."
            )
            await msg.update()
            cl.user_session.set("state", "idle")
            return

        term_folder_id = term_folder["id"]
        cl.user_session.set("term_folder_id", term_folder_id)

        msg.content = f"**{term_folder['name']}** 폴더에서 신청자 목록을 불러오는 중..."
        await msg.update()

        # 2. 신청자 목록 로드
        applicants = load_applicants_from_drive(term_folder_id)

        if not applicants:
            msg.content = (
                "회차 폴더에서 신청자 목록(LEARNING_APPLY*.xls)을 찾을 수 없습니다.\n"
                "배움숲에서 다운로드한 파일을 Drive 회차 폴더에 업로드해주세요."
            )
            await msg.update()
            cl.user_session.set("state", "idle")
            return

        # 3. 수강생 시트 생성 (회차 폴더에 동적 생성)
        students_sheet_id, count = create_students_sheet(
            applicants, term["term_id"], term_folder_id
        )
        cl.user_session.set("students_sheet_id", students_sheet_id)

        # 4. 수강생 목록 로드
        students = load_students_from_sheet(students_sheet_id)

        msg.content = (
            f"**{term['term_name']}** 수강 신청자(총 **{count}명**)를 확인했습니다.\n\n"
            "입금내역 파일(.xls 또는 .xlsx)을 업로드해주세요."
        )
        await msg.update()

        cl.user_session.set("state", "awaiting_payment_file")
        cl.user_session.set("students", students)

    except Exception as e:
        msg.content = f"신청자 데이터 로드 중 오류: {str(e)}"
        await msg.update()
        cl.user_session.set("state", "idle")


async def handle_payment_file(message: cl.Message):
    """입금내역 파일 수신 → 파싱 → 매칭 → 결과 표시"""
    # 파일 확인
    if not message.elements:
        await cl.Message("입금내역 파일을 업로드해주세요. (.xls 또는 .xlsx)").send()
        return

    file_element = message.elements[0]

    msg = cl.Message(content="입금내역 파일을 분석하는 중...")
    await msg.send()

    try:
        # 1. 파일 읽기
        with open(file_element.path, "rb") as f:
            file_bytes = f.read()

        # 2. 입금내역 파싱
        transactions = parse_bank_statement(file_bytes)
        if not transactions:
            msg.content = "입금내역에서 거래 데이터를 찾을 수 없습니다. 파일 형식을 확인해주세요."
            await msg.update()
            cl.user_session.set("state", "idle")
            return

        # 3. 수강생 목록 가져오기
        students = cl.user_session.get("students", [])
        students_sheet_id = cl.user_session.get("students_sheet_id")
        if not students and students_sheet_id:
            students = load_students_from_sheet(students_sheet_id)

        # 4. 회원 등급 로드 (정회원 면제 처리용)
        members = load_members()
        exempted = apply_exemptions(students, members)
        exempted_ids = {e["이름ID"] for e in exempted}

        msg.content = f"입금 거래 **{len(transactions)}건**을 수강생 **{len(students)}명**과 대조하는 중..."
        await msg.update()

        # 5. 규칙 기반 매칭
        all_results, unmatched = run_code_matching(transactions, students)

        code_matched = sum(1 for r in all_results if r["상태"] == "✅정상")
        msg.content = (
            f"코드 매칭 완료: ✅ {code_matched}건 매칭 / 🔶 {len(unmatched)}건 미매칭\n\n"
            "미매칭 건을 AI로 분석하는 중..."
        )
        await msg.update()

        # 6. LLM 매칭 (미매칭 건이 있을 때만)
        llm_unmatched = [r for r in unmatched if r["상태"] != "⏭️스킵"]
        if llm_unmatched:
            await run_llm_matching(llm_unmatched, students)

        # 7. 미입금 수강생 찾기
        unpaid = find_unpaid_students(students, all_results, exempted_ids)

        # 8. 결과 포맷 및 표시
        summary = format_results(all_results, unpaid, exempted)

        # Action 버튼으로 다음 단계 안내
        actions = [
            cl.Action(name="write_results", label="✅ 시트에 반영하기", payload={"value": "write"}),
            cl.Action(name="cancel_results", label="❌ 취소", payload={"value": "cancel"}),
        ]
        await cl.Message(content=summary, actions=actions).send()

        # 세션에 결과 저장
        cl.user_session.set("state", "awaiting_payment_confirm")
        cl.user_session.set("matched_results", all_results)
        cl.user_session.set("exempted", exempted)

    except Exception as e:
        msg.content = f"입금 대조 중 오류가 발생했습니다: {str(e)}"
        await msg.update()
        cl.user_session.set("state", "idle")


async def handle_payment_confirm(message: cl.Message):
    """사용자 확인 → 시트 반영 → 다음 단계 Action 버튼 제공"""
    text = message.content.strip()

    if any(word in text for word in ["예", "네", "응", "확인", "반영"]):
        await write_payment_results()
    else:
        await cl.Message("입금 대조 결과가 반영되지 않았습니다.").send()
        _clear_payment_session()


@cl.action_callback("write_results")
async def on_write_results(action: cl.Action):
    """시트에 반영하기 Action 핸들러"""
    await write_payment_results()


@cl.action_callback("cancel_results")
async def on_cancel_results(action: cl.Action):
    """취소 Action 핸들러"""
    await cl.Message("입금 대조 결과가 반영되지 않았습니다.").send()
    _clear_payment_session()


async def write_payment_results():
    """매칭 결과를 시트에 반영하고 다음 단계 Action 제공"""
    msg = cl.Message(content="수강생 시트에 결과를 반영하는 중...")
    await msg.send()

    try:
        results = cl.user_session.get("matched_results", [])
        exempted = cl.user_session.get("exempted", [])
        students_sheet_id = cl.user_session.get("students_sheet_id")

        if not students_sheet_id:
            msg.content = "수강생 시트 정보를 찾을 수 없습니다. 입금 대조를 다시 시작해주세요."
            await msg.update()
            _clear_payment_session()
            return

        updated = write_results_to_sheet(students_sheet_id, results, exempted)

        msg.content = (
            f"수강생 시트에 **{updated}건**의 입금 정보가 반영되었습니다.\n\n"
            "배움숲에서 결제완료 회원의 등록상태를 변경해주세요."
        )
        await msg.update()

        # 다음 단계 Action 버튼
        actions = [
            cl.Action(name="create_attendance", label="📋 출석부 생성하기", payload={"value": "attendance"}),
            cl.Action(name="redo_payment", label="🔄 입금대조 다시하기", payload={"value": "redo"}),
            cl.Action(name="free_question", label="❓ 다른 질문하기", payload={"value": "question"}),
        ]
        await cl.Message(content="다음 작업을 선택해주세요.", actions=actions).send()

    except Exception as e:
        msg.content = f"시트 반영 중 오류: {str(e)}"
        await msg.update()

    # 매칭 결과만 초기화 (term, term_folder_id, students_sheet_id는 유지)
    cl.user_session.set("state", "idle")
    cl.user_session.set("matched_results", None)
    cl.user_session.set("students", None)
    cl.user_session.set("exempted", None)


@cl.action_callback("create_attendance")
async def on_create_attendance(action: cl.Action):
    """출석부 생성 Action 핸들러"""
    await do_create_attendance()


@cl.action_callback("redo_payment")
async def on_redo_payment(action: cl.Action):
    """입금대조 다시하기 Action 핸들러"""
    cl.user_session.set("state", "awaiting_payment_file")
    await cl.Message("입금내역 파일(.xls 또는 .xlsx)을 다시 업로드해주세요.").send()


@cl.action_callback("free_question")
async def on_free_question(action: cl.Action):
    """다른 질문하기 Action 핸들러"""
    cl.user_session.set("state", "idle")
    await cl.Message("궁금한 점을 자유롭게 질문해주세요.").send()


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

        # 수강생 시트 찾기 — 수강생/ 서브폴더 우선, 없으면 회차 폴더 직접 탐색
        students_sheet_id = cl.user_session.get("students_sheet_id")
        if not students_sheet_id:
            from app.services.google_drive import find_spreadsheet_by_name, find_file as _find_file
            students_subfolder = _find_file("수강생", parent_id=term_folder_id)
            search_folder = (
                students_subfolder["id"]
                if students_subfolder and "folder" in students_subfolder.get("mimeType", "")
                else term_folder_id
            )
            existing = find_spreadsheet_by_name(search_folder, "수강생")
            if not existing:
                msg.content = "수강생 시트를 찾을 수 없습니다. 입금 대조를 먼저 완료해주세요."
                await msg.update()
                return
            students_sheet_id = existing["id"]
            cl.user_session.set("students_sheet_id", students_sheet_id)

        await do_create_attendance()

    except Exception as e:
        msg.content = f"출석부 생성 준비 중 오류: {str(e)}"
        await msg.update()


async def do_create_attendance():
    """출석부 생성 실행"""
    term = cl.user_session.get("term") or get_current_term()
    term_folder_id = cl.user_session.get("term_folder_id")
    students_sheet_id = cl.user_session.get("students_sheet_id")

    if not term_folder_id or not students_sheet_id:
        await cl.Message(
            "회차 폴더 또는 수강생 시트 정보를 찾을 수 없습니다. 입금 대조를 먼저 완료해주세요."
        ).send()
        return

    msg = cl.Message(content=f"**{term['term_name']}** 출석부를 생성하는 중...")
    await msg.send()

    try:
        # 출석부 생성
        result = create_attendance_sheet(
            term["term_id"], term_folder_id, students_sheet_id
        )

        # 회원관리 업데이트 + 수강기록 추가
        matched_results = cl.user_session.get("matched_results")
        if matched_results:
            member_count = update_members_after_registration(matched_results, term["term_id"])
            record_count = add_enrollment_records(matched_results, term["term_id"])
            extra = f"\n- 회원 승급: {member_count}명\n- 수강기록 추가: {record_count}건"
        else:
            extra = ""

        msg.content = (
            f"## 📋 출석부 생성 완료\n\n"
            f"- 과목 수: {len(result['courses'])}개\n"
            f"- 총 수강생: {result['total_students']}명\n"
            f"- 과목: {', '.join(result['courses'])}"
            f"{extra}\n\n"
            f"[출석부 열기]({result['spreadsheet_url']})"
        )
        await msg.update()

    except Exception as e:
        msg.content = f"출석부 생성 중 오류: {str(e)}"
        await msg.update()

    cl.user_session.set("state", "idle")


def _clear_payment_session():
    """입금 대조 세션 상태 초기화"""
    cl.user_session.set("state", "idle")
    cl.user_session.set("matched_results", None)
    cl.user_session.set("students", None)
    cl.user_session.set("exempted", None)

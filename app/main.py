"""Chainlit 엔트리포인트 — 위례인생학교 업무 도우미"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chainlit as cl
from app.chains.qa import answer_question
from app.chains.payment import (
    load_students_from_drive,
    load_students_from_sheet,
    populate_students_sheet,
    run_llm_matching,
    find_unpaid_students,
    format_results,
    write_results_to_sheet,
)
from app.services.excel import parse_bank_statement
from app.utils.matching import run_code_matching


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
        await cl.Message("출석부 생성 기능은 준비 중입니다.").send()
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
    """입금 대조 시작 — 수강생 로드 + 파일 업로드 요청"""
    msg = cl.Message(content="수강생 데이터를 불러오는 중...")
    await msg.send()

    try:
        # 수강생 시트에서 먼저 로드 시도
        students = load_students_from_sheet()

        if not students:
            # 시트가 비어있으면 출석부 폴더에서 로드 후 시트에 기록
            msg.content = "출석부 폴더에서 수강생 명단을 불러오는 중..."
            await msg.update()

            students = load_students_from_drive()
            if not students:
                msg.content = "출석부 폴더에서 수강생 명단을 찾을 수 없습니다. 출석부 폴더를 확인해주세요."
                await msg.update()
                return

            count = populate_students_sheet(students)
            msg.content = (
                f"출석부에서 **{count}명**의 수강생을 불러와 수강생 시트에 등록했습니다.\n\n"
                "입금내역 파일(.xls 또는 .xlsx)을 업로드해주세요."
            )
        else:
            msg.content = (
                f"수강생 **{len(students)}명**이 로드되었습니다.\n\n"
                "입금내역 파일(.xls 또는 .xlsx)을 업로드해주세요."
            )
        await msg.update()

        cl.user_session.set("state", "awaiting_payment_file")
        cl.user_session.set("students", students)

    except Exception as e:
        msg.content = f"수강생 데이터 로드 중 오류: {str(e)}"
        await msg.update()


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
        if not students:
            students = load_students_from_sheet()

        msg.content = f"입금 거래 **{len(transactions)}건**을 수강생 **{len(students)}명**과 대조하는 중..."
        await msg.update()

        # 4. 규칙 기반 매칭
        all_results, unmatched = run_code_matching(transactions, students)

        code_matched = sum(1 for r in all_results if r["상태"] == "✅정상")
        msg.content = (
            f"코드 매칭 완료: ✅ {code_matched}건 매칭 / 🔶 {len(unmatched)}건 미매칭\n\n"
            "미매칭 건을 AI로 분석하는 중..."
        )
        await msg.update()

        # 5. LLM 매칭 (미매칭 건이 있을 때만)
        llm_unmatched = [r for r in unmatched if r["상태"] != "⏭️스킵"]
        if llm_unmatched:
            await run_llm_matching(llm_unmatched, students)

        # 6. 미입금 수강생 찾기
        unpaid = find_unpaid_students(students, all_results)

        # 7. 결과 포맷 및 표시
        summary = format_results(all_results, unpaid)
        msg.content = summary
        await msg.update()

        # 세션에 결과 저장
        cl.user_session.set("state", "awaiting_payment_confirm")
        cl.user_session.set("matched_results", all_results)

    except Exception as e:
        msg.content = f"입금 대조 중 오류가 발생했습니다: {str(e)}"
        await msg.update()
        cl.user_session.set("state", "idle")


async def handle_payment_confirm(message: cl.Message):
    """사용자 확인 → 시트 반영"""
    text = message.content.strip()

    if any(word in text for word in ["예", "네", "응", "확인", "반영"]):
        msg = cl.Message(content="수강생 시트에 결과를 반영하는 중...")
        await msg.send()

        try:
            results = cl.user_session.get("matched_results", [])
            updated = write_results_to_sheet(results)
            msg.content = f"수강생 시트에 **{updated}건**의 입금 정보가 반영되었습니다."
            await msg.update()
        except Exception as e:
            msg.content = f"시트 반영 중 오류: {str(e)}"
            await msg.update()
    else:
        await cl.Message("입금 대조 결과가 반영되지 않았습니다.").send()

    # 상태 초기화
    cl.user_session.set("state", "idle")
    cl.user_session.set("matched_results", None)
    cl.user_session.set("students", None)

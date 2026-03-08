"""Chainlit 엔트리포인트 — 위례인생학교 업무 도우미"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chainlit as cl
from app.chains.qa import answer_question


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
    # Phase 0: 모든 메시지를 Q&A로 처리
    # Phase 1에서 의도 분류 라우터(router.py) 추가 예정
    msg = cl.Message(content="")
    await msg.send()

    try:
        response = await answer_question(message.content)
        msg.content = response
        await msg.update()
    except Exception as e:
        msg.content = f"오류가 발생했습니다: {str(e)}\n\n환경 변수(ANTHROPIC_API_KEY)가 올바르게 설정되어 있는지 확인해주세요."
        await msg.update()

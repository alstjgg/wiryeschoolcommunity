"""Chainlit 엔트리포인트 — LangChain Agent 기반 위례인생학교 업무 도우미"""

import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 데이터 레이어 등록 (DATABASE_URL 있을 때만 활성화 — 다른 import보다 먼저)
import app.services.chat_data_layer  # noqa: F401

import chainlit as cl

from app.agent import create_wirye_agent
from app.context.term import get_current_term

logger = logging.getLogger(__name__)

# 워크플로우 중 취소 의도 감지 키워드
CANCEL_KEYWORDS = ["취소", "중단", "그만", "멈춰", "stop", "cancel", "안 할게", "안할게", "나가기"]

# 신청자 목록 건너뛰기 키워드
SKIP_KEYWORDS = ["건너뛰기", "스킵", "skip", "이전 데이터", "패스"]


# ===================================================== Lifecycle =====


@cl.on_chat_start
async def on_chat_start():
    """새 대화 시작 — Agent 초기화"""
    agent = await create_wirye_agent()
    # Chainlit이 부여하는 thread_id 사용 → PostgresSaver와 동기화
    thread_id = cl.context.session.thread_id or str(uuid.uuid4())
    cl.user_session.set("agent", agent)
    cl.user_session.set("thread_id", thread_id)
    cl.user_session.set("state", "idle")


@cl.on_chat_resume
async def on_chat_resume(thread: dict):
    """과거 대화 복원 — Agent 재초기화, 메시지 replay 없음.

    Chainlit이 자체적으로 steps를 렌더링하고,
    PostgresSaver가 thread_id 기반으로 Agent state를 복원한다.
    """
    agent = await create_wirye_agent()
    # 같은 thread_id → PostgresSaver에서 이전 checkpoint 자동 로드
    thread_id = thread.get("id", str(uuid.uuid4()))
    cl.user_session.set("agent", agent)
    cl.user_session.set("thread_id", thread_id)
    cl.user_session.set("state", "idle")


@cl.on_stop
async def on_stop():
    """User clicked the stop button — reset to idle."""
    cl.user_session.set("state", "idle")
    cl.user_session.set("payment_step", None)


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


# ===================================================== Starters =====


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


# ===================================================== Message =====


@cl.on_message
async def on_message(message: cl.Message):
    session_state = cl.user_session.get("state", "idle")

    # 작업 진행 중 busy 상태 → 대기 요청
    if session_state in ("creating_attendance", "running_graduation"):
        await cl.Message("작업이 진행 중입니다. 잠시만 기다려주세요...").send()
        return

    # 파일 대기 중 텍스트만 입력 → 취소 감지 / 건너뛰기 / Agent에게 질문 전달 후 재안내
    if session_state in ("awaiting_applicants_file", "awaiting_payment_file", "awaiting_ocr_image"):
        if not message.elements:
            if any(k in message.content for k in CANCEL_KEYWORDS):
                cl.user_session.set("state", "idle")
                cl.user_session.set("payment_step", None)
                await cl.Message("작업이 취소되었습니다.").send()
                await send_default_actions()
                return
            # 신청자 목록 건너뛰기 (기존 DB 데이터 사용)
            if session_state == "awaiting_applicants_file" and any(k in message.content for k in SKIP_KEYWORDS):
                await _skip_applicants_step()
                return
            # Agent에게 질문 전달 (Q&A 등)
            await _invoke_agent(message.content)
            # Agent 응답 후에도 여전히 파일 대기 중이면 재안내
            current_state = cl.user_session.get("state", "idle")
            if current_state in ("awaiting_applicants_file", "awaiting_payment_file", "awaiting_ocr_image"):
                resume = _get_resume_prompt(current_state)
                await cl.Message(resume).send()
            return

    # 파일 태깅: message.elements → file_path 추출
    content = message.content
    file_paths = []
    if message.elements:
        for elem in message.elements:
            if hasattr(elem, "path") and elem.path:
                file_paths.append(elem.path)
                content += f"\n[FILE:{elem.path}]"

    # Agent invoke
    await _invoke_agent(content, file_paths)


async def _invoke_agent(content: str, file_paths: list[str] | None = None):
    """Agent에 메시지를 전달하고 응답을 표시."""
    agent = cl.user_session.get("agent")
    thread_id = cl.user_session.get("thread_id")

    if not agent or not thread_id:
        # Agent가 없으면 재초기화
        agent = await create_wirye_agent()
        thread_id = cl.context.session.thread_id or str(uuid.uuid4())
        cl.user_session.set("agent", agent)
        cl.user_session.set("thread_id", thread_id)

    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 15,
    }

    msg = cl.Message(content="")
    await msg.send()

    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": content}]},
            config=config,
        )

        # 마지막 AI 메시지 추출
        last_message = result["messages"][-1]
        response_text = last_message.content if hasattr(last_message, "content") else str(last_message)

        # __SILENT__ 응답은 tool이 직접 메시지를 보낸 경우
        if response_text.strip() == "__SILENT__":
            # 빈 메시지 제거
            msg.content = ""
            await msg.update()
            return

        msg.content = response_text
        await msg.update()

    except Exception as e:
        logger.error("Agent invoke failed: %s", e, exc_info=True)
        msg.content = f"오류가 발생했습니다: {e}\n\n환경 변수(ANTHROPIC_API_KEY)가 올바르게 설정되어 있는지 확인해주세요."
        await msg.update()

    # idle 상태이면 기본 액션 버튼 표시
    state = cl.user_session.get("state", "idle")
    if state == "idle":
        await send_default_actions()


# ===================================================== 공통 액션 버튼 =====


async def send_default_actions(completed: str | None = None):
    """모든 작업 완료/종료 후 공통으로 호출하는 기본 액션 버튼."""
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


# Action callbacks — 버튼 클릭 시 해당 Starter 메시지를 Agent에 전달

BUTTON_MESSAGES = {
    "plan": "강의 계획서를 검토합니다.",
    "payment": "입금 대조를 시작합니다.",
    "attendance": "출석부를 생성합니다.",
    "ocr": "출석 체크를 시작합니다.",
    "graduation": "종강 처리를 시작합니다.",
    "report": "보고서를 생성합니다.",
    "question": "업무 관련 질문이 있습니다.",
}


@cl.action_callback("default_plan")
async def on_default_plan(action: cl.Action):
    await _invoke_agent(BUTTON_MESSAGES["plan"])


@cl.action_callback("default_payment")
async def on_default_payment(action: cl.Action):
    await _invoke_agent(BUTTON_MESSAGES["payment"])


@cl.action_callback("default_attendance")
async def on_default_attendance(action: cl.Action):
    await _invoke_agent(BUTTON_MESSAGES["attendance"])


@cl.action_callback("default_ocr")
async def on_default_ocr(action: cl.Action):
    await _invoke_agent(BUTTON_MESSAGES["ocr"])


@cl.action_callback("default_graduation")
async def on_default_graduation(action: cl.Action):
    await _invoke_agent(BUTTON_MESSAGES["graduation"])


@cl.action_callback("default_report")
async def on_default_report(action: cl.Action):
    await _invoke_agent(BUTTON_MESSAGES["report"])


@cl.action_callback("default_question")
async def on_default_question(action: cl.Action):
    cl.user_session.set("state", "idle")
    await cl.Message("궁금한 점을 자유롭게 질문해주세요.").send()


# ===================================================== 건너뛰기 =====


async def _skip_applicants_step():
    """기존 DB 신청 데이터를 사용하여 신청자 목록 단계를 건너뛴다."""
    term = cl.user_session.get("term")
    if not term:
        await cl.Message("회차 정보가 없습니다. 입금 대조를 처음부터 다시 시작해주세요.").send()
        cl.user_session.set("state", "idle")
        cl.user_session.set("payment_step", None)
        return

    try:
        from app.services import db
        existing = await db.load_applications(term["term_id"])
        if not existing:
            await cl.Message("이전 신청 데이터가 없습니다. 신청자 목록 파일을 업로드해주세요.").send()
            return

        cl.user_session.set("applications", existing)
        cl.user_session.set("payment_step", "awaiting_payment")
        cl.user_session.set("state", "awaiting_payment_file")

        수강 = sum(1 for a in existing if a["유형"] == "수강")
        await cl.Message(
            f"이전 신청 데이터(**{수강}건**)를 사용합니다.\n\n"
            "입금내역 파일(.xls 또는 .xlsx)을 업로드해주세요."
        ).send()
    except Exception as e:
        logger.error("skip applicants failed: %s", e)
        await cl.Message(f"이전 데이터 로드 중 오류: {e}\n신청자 목록 파일을 업로드해주세요.").send()


# ===================================================== 유틸 =====


def _get_resume_prompt(state: str) -> str:
    """상태별 재안내 문구 반환."""
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

# LangChain Agent 전환 제안서

> 현재 고정 파이프라인의 라우팅 레이어를 LangChain Agent의 tool selection으로 대체한다.
> 비즈니스 파이프라인 코드는 그대로 유지하고, Agent는 "어떤 작업을 할지"만 결정한다.

---

## 1. 현재 아키텍처

```
Starter 버튼 → 고정 메시지 매칭 → Python 함수 파이프라인 → cl.Step 진행 표시
자유 텍스트 → LLM 의도 분류 → 관리자 확인 → Python 함수 파이프라인
```

- 7개 작업이 각각 고정 실행 순서의 Python 함수 파이프라인으로 구현
- LLM은 비정형 텍스트 해석에만 사용 (입금자명 파싱, OCR, Q&A)
- `main.py` 1500줄의 세션 상태 머신 + 33개 action callback으로 라우팅
- 새 작업 추가 시 `on_message` 라우팅, 상태 머신, action callback, Starter 버튼 등 4-5곳 수정 필요

**문제점:**
- 라우팅 레이어의 복잡도가 비즈니스 로직보다 높아짐
- 키워드 매칭 기반 의도 분류로 자연어 입력 처리가 제한적
- 비즈니스 컨텍스트를 매 LLM 호출에 전체 주입하여 비용과 지연 발생
- 외부 시스템 연동 시 수동 API 래퍼 작성 필요

---

## 2. 전환 범위: Agent를 라우터로만 사용

```
사용자 입력 → LangChain Agent (tool selection) → 기존 Python 파이프라인 실행 → 결과 + Action 버튼
```

**변경하는 것:**
- `on_message`의 1500줄 상태 기반 라우팅 → Agent의 tool selection (~200줄)
- `classify_intent_llm` + 고정 메시지 매칭 → LLM이 자연스럽게 적합한 tool 선택
- 세션 상태 머신 → Agent 내부 state + 메시지 히스토리

**변경하지 않는 것:**
- 7개 비즈니스 파이프라인 코드 (payment.py, attendance.py, ocr.py, graduation.py, qa.py)
- DB/Sheets 쓰기 로직 (db.py, sheets_sync.py, google_sheets.py)
- Chainlit UI 패턴 (Action 버튼, Step 진행 표시)
- 단위 테스트 121개

```python
from langchain.agents import create_agent
from langchain.tools import tool

@tool
async def process_payment(term_id: str) -> str:
    """입금 대조를 실행합니다. 수강 신청자 목록과 은행 입금 내역을 매칭합니다."""
    # 기존 payment pipeline 함수 그대로 호출
    ...

@tool
async def create_attendance_sheet(term_id: str) -> str:
    """출석부를 생성합니다. 처리상태가 '등록완료'인 수강생으로 출석부를 만듭니다."""
    ...

agent = create_agent(
    model="anthropic:claude-sonnet-4-5",
    tools=[process_payment, create_attendance_sheet, ...],  # 7개 tool
    system_prompt=BUSINESS_CONTEXT,
    middleware=[error_handler, action_button_suggester],
)
```

---

## 3. 전환 근거

### 3.1 라우팅 복잡도 해소

`main.py`의 33개 action callback과 상태 머신이 Agent 하나로 대체된다. 새 작업 추가는 `@tool` 함수 하나만 정의하면 된다.

### 3.2 자연어 입력 처리

55세+ 사용자의 예측 불가능한 입력을 LLM이 맥락으로 이해한다:

- "아 잠깐, 이번 학기가 아니라 지난 학기 거예요" → Agent가 term_id 변경을 이해
- "입금 대조 중인데 회원 명단도 볼 수 있어요?" → Agent가 Q&A tool을 병행 호출
- 오타, 불완전한 문장, 비표준 표현 → LLM 자연어 이해로 처리

Action 버튼을 매 응답마다 제안하면 사용자가 길을 잃지 않으면서도, 자유 입력이 정확하게 처리된다.

### 3.3 MCP 서버 통합

`langchain-mcp-adapters`로 외부 MCP 서버를 Agent tool로 바로 연결:

```python
client = MultiServerMCPClient({
    "google-sheets": {"transport": "stdio", "command": "...", "args": [...]},
    "google-drive": {"transport": "stdio", "command": "...", "args": [...]},
})
```

현재 `google_sheets.py`, `google_drive.py`의 수동 API 래퍼 중 읽기 작업(시트 조회, 드라이브 파일 탐색)을 MCP로 대체할 수 있다. 쓰기 작업은 트랜잭션 안전성을 위해 기존 Python 함수 유지.

### 3.4 비즈니스 로직 확장성

7개 이상의 작업이 추가될 때 (보고서 생성, 계획서 검토 등 백로그 포함), 라우팅 레이어를 건드리지 않고 `@tool` 함수만 추가하면 된다.

---

## 4. 비즈니스 컨텍스트 관리

현재 `BUSINESS_CONTEXT.md`를 매 LLM 호출의 시스템 프롬프트에 전체 주입한다. 컨텍스트가 성장하면 비용과 지연이 비례 증가한다.

### 4.1 Prompt Caching (즉시 적용, 아키텍처 변경 없음)

Anthropic의 prompt caching으로 자주 재사용되는 prefix 토큰을 메모리에 유지. **비용 90% 감소, 지연 ~50% 감소** (캐시 적중 시).

```python
SystemMessage(content=[
    {"type": "text", "text": business_context, "cache_control": {"type": "ephemeral"}}
])
```

LangChain `SystemMessage`에서 네이티브 지원. 비즈니스 컨텍스트는 동일한 텍스트가 반복 사용되므로 캐시 적중률이 매우 높다. **Agent 전환과 무관하게 즉시 적용 가능.**

### 4.2 Selective Context Injection (Agent 전환 시 자연스럽게 적용)

Agent가 tool을 선택한 후, 해당 tool의 LLM 호출에만 관련 컨텍스트를 주입:

| Tool | 필요한 컨텍스트 |
|------|----------------|
| 입금 대조 | 입금 매칭 로직, 상태 코드, 금액 분류 |
| 출석부 생성 | 처리상태 gate, 출석부 구조 |
| Q&A | 전체 컨텍스트 (유일하게 전체가 필요한 경우) |

Agent의 tool selection은 짧은 프롬프트로 수행 (tool 이름 + 설명만). Tool 내부의 LLM 호출에만 작업별 컨텍스트를 주입하면 대부분의 호출에서 컨텍스트 크기가 **70-80% 감소**.

Agent 아키텍처에서는 각 `@tool` 함수가 자체 LLM 호출을 제어하므로 이 패턴이 자연스럽다.

### 4.3 RAG (컨텍스트가 ~100K 토큰을 초과할 때)

회의록, 과거 보고서, 규정 문서 등이 추가되어 컨텍스트가 prompt caching으로 감당할 수 없는 규모가 되면 RAG 도입:

- 문서를 벡터 스토어에 임베딩
- 쿼리별 관련 청크만 검색
- Q&A tool에만 적용 (비즈니스 파이프라인 tool은 고정 컨텍스트로 충분)

**현재 규모 (`BUSINESS_CONTEXT.md` + `DEV_DOCUMENT.md`)에서는 불필요.** Prompt caching + selective injection으로 충분하며, RAG 인프라는 실제로 필요해질 때 구축한다.

---

## 5. 설계 시 주의사항

### 5.1 파일 업로드 처리

현재 상태 머신의 `awaiting_*_file` 패턴은 Agent의 ReAct loop에서 직접 대응되지 않는다. **Multi-turn 패턴**으로 해결:

1. Tool이 "파일이 필요합니다" 메시지를 반환하고 Agent 턴 종료
2. 사용자가 파일을 업로드하면 새로운 Agent invoke 시작
3. 대화 히스토리에 이전 맥락이 있으므로 Agent가 이어서 처리

`interrupt()`/`resume`보다 단순하며, Chainlit의 파일 업로드 UI와 자연스럽게 연동된다.

### 5.2 Coarse-Grained Tool 설계

7개 작업을 7개 tool로 1:1 매핑한다. 하나의 tool을 여러 sub-tool로 분리하지 않는다:

- Agent가 "무엇을 할지"만 결정 → tool selection은 단순
- Tool 내부는 기존 Python 함수가 고정 순서로 실행 → 비즈니스 안전성 유지
- Agent가 순서를 바꾸거나 단계를 건너뛸 위험 없음

### 5.3 LLM 비용

모든 사용자 입력에 LLM이 호출되므로 현재 대비 3-5배 비용 증가 예상. 관리자 2-4명, 하루 수십 건이므로 절대 비용은 크지 않다. Prompt caching 적용 시 추가 비용은 더 줄어든다.

### 5.4 에러 복구

Middleware `@wrap_tool_call`로 재시도 횟수를 제한한다. Agent가 같은 실패를 반복 시도하는 루프를 방지.

### 5.5 Chainlit 통합

`@cl.on_message`에서 Agent의 `astream`을 호출하고, Chainlit의 Action 버튼/Step 표시를 유지한다:

```python
@cl.on_message
async def on_message(message: cl.Message):
    async for chunk in agent.astream(
        {"messages": [{"role": "user", "content": message.content}]},
        config={"configurable": {"thread_id": cl.user_session.get("id")}},
    ):
        ...
```

### 5.6 테스트

기존 121개 단위 테스트는 그대로 유지 (tool 내부 = 기존 함수). 추가로 Agent의 tool selection 정확도를 LangSmith evaluation으로 검증한다.

---

## 6. 향후 확장 (현재 범위 밖)

아래 단계는 Phase A 안정화 후 필요에 따라 검토한다:

- **Fine-grained tool 분리**: 하나의 coarse tool을 여러 tool로 분리 (예: `process_payment` → `load_applicants` + `match_deposits` + `write_results`). Agent 자율성 증가, 비즈니스 안전성 감소.
- **LangGraph `interrupt()`/`resume`**: 파일 업로드 대기를 명시적 중단/재개로 구현. Multi-turn 패턴으로 충분하지 않을 때.
- **RAG 인프라**: 컨텍스트가 ~100K 토큰을 초과할 때 벡터 스토어 도입.

---

## 7. 장단점 요약

| 관점 | 현재 (고정 파이프라인) | 전환 후 (Agent 라우터) |
|------|----------------------|----------------------|
| **자연어 이해** | 키워드 매칭 + LLM 의도 분류 | LLM이 모든 입력에서 적합한 tool 선택 |
| **확장성** | 새 작업 = 4-5곳 수정 | 새 작업 = `@tool` 함수 1개 |
| **비즈니스 안전성** | 고정 파이프라인 (결정적) | 동일 (파이프라인 코드 변경 없음) |
| **비용** | LLM 호출 최소 | 3-5배 증가 (prompt caching으로 완화) |
| **파일 업로드** | 상태 머신 (자연스러움) | Multi-turn 패턴 (설계 필요) |
| **MCP 통합** | 수동 API 래퍼 | 네이티브 MCP 어댑터 |
| **코드량** | main.py 1500줄 | main.py ~200줄 + tool 함수들 |
| **디버깅** | 스택 트레이스 | LangSmith 추적 |

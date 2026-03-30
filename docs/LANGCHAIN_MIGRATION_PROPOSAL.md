# LangChain Agent 전환 제안서

> 현재 고정 파이프라인의 라우팅 레이어를 LangChain Agent의 tool selection으로 대체한다.
> 비즈니스 파이프라인 코드는 그대로 유지하고, Agent는 "어떤 작업을 할지"만 결정한다.

---

## 결정 사항 (2026-03-28 논의 결과)

### 확정된 방향

1. **릴리즈 전략**: 현재 코드 그대로 prod 릴리즈(`main` 브랜치) → `to-langchain` 브랜치에서 Agent 전환 개발 → Railway dev 환경 연결 → 검증 후 prod 패치
2. **전환 범위**: Phase A (Agent를 라우터로만 사용) — 기존 파이프라인 코드 유지
3. **의존성**: LangChain 1.0 + LangGraph 1.0으로 업그레이드 (`create_agent` API 활용)
4. **UX 유지**: Conversation Starter 버튼 + Action 버튼 유지. 버튼은 고정 메시지를 발송하고, Agent가 해석하여 tool 선택
5. **세 가지 입력 채널이 Agent로 수렴**:
   - Starter/Action 버튼 → 고정 메시지 발송 → Agent 해석 → tool 선택
   - 자유 텍스트 → Agent 해석 → tool 선택
   - 파일 업로드 → 세션 상태 + MIME 타입 + Agent 맥락 3단계 판단 → tool 선택

### Phase 구분

| Phase | 목표 | 범위 |
|-------|------|------|
| **A** | Agent 전환 + 기존 기능 유지 | 의존성 업그레이드, @tool 래핑, Agent routing, Starter/Action 버튼 연결, 파일 업로드 multi-turn |
| **B** | 기능 확장 + UX 개선 | 데이터 조회 tool, Q&A 고도화, cl.Step→cl.Message 전환, 세션 영속성(PostgresSaver) |

### 향후 결정 보류

- **"시스템 vs 챗봇"**: Agent 전환 후 실무자 사용 패턴을 보고 결정
- **범용 템플릿화**: 첫 번째 고객(위례인생학교) 성공 후 결정

---

## 프레임워크 선택: LangChain vs LangGraph vs Deep Agents

### 계층 구조

```
┌─────────────────────────────────────────┐
│              Deep Agents                │  ← 최고수준: 기획/메모리/스킬/파일 관리 내장
│   (planning, memory, skills, files)     │
├─────────────────────────────────────────┤
│               LangGraph                 │  ← 오케스트레이션: 그래프/루프/상태
│    (nodes, edges, state, persistence)   │
├─────────────────────────────────────────┤
│               LangChain                 │  ← 기반: 모델/도구/프롬프트/RAG
│      (models, tools, prompts, RAG)      │
└─────────────────────────────────────────┘
```

### 비교 테이블

| | LangChain | LangGraph | Deep Agents |
|---|---|---|---|
| **핵심 API** | `create_agent()` | `StateGraph` | `DeepAgent` |
| **제어 흐름** | 고정 ReAct 루프 | 커스텀 그래프 (분기/루프/병렬) | 내부 관리 (middleware) |
| **상태 관리** | 메시지 히스토리만 | 커스텀 상태 스키마 | 내장 메모리 |
| **Human-in-the-loop** | middleware 기반 | `interrupt()`/`Command(resume=...)` | 내장 middleware |
| **계획 수립** | 없음 | 수동 구현 | ✅ TodoListMiddleware |
| **파일 관리** | 없음 | 수동 구현 | ✅ FilesystemMiddleware |
| **서브에이전트** | 없음 | 수동 구현 | ✅ SubAgentMiddleware |
| **설정 복잡도** | 낮음 | 중간 | 낮음 |
| **유연성** | 중간 | 높음 | 중간 |

### 선택 가이드

| 질문 | Yes → | No → |
|------|-------|------|
| 서브태스크 분할, 파일 관리, 영속 메모리, 온디맨드 스킬이 필요한가? | **Deep Agents** | ↓ |
| 복잡한 제어 흐름 — 루프, 분기, 병렬, HITL, 커스텀 상태가 필요한가? | **LangGraph** | ↓ |
| 입력 → tool 호출 → 결과 반환하는 단일 목적 Agent인가? | **LangChain** (`create_agent`) | ↓ |
| 순수 모델 호출, 체인, RAG 파이프라인인가? | **LangChain** (LCEL/chain) | — |

### 우리 프로젝트: **LangChain `create_agent`** (Phase A)

7개 coarse-grained tool을 라우팅하는 단순 ReAct 루프로 충분. `create_agent`가 내부적으로 LangGraph StateGraph를 사용하므로, 나중에 LangGraph 직접 제어가 필요하면 자연스럽게 확장 가능.

> **Claude Code 팁**: `.agents/skills/framework-selection/SKILL.md`를 로드하면 프레임워크 선택을 자동으로 가이드받을 수 있음.

---

## 1. 현재 아키텍처 및 전환 동기

(기존 제안서 내용 유지 — `main.py` ~70KB, 33개 callback, 키워드 매칭 한계, 확장성 문제, 실무자 피드백)

---

## 2. Phase A 상세

### 전환 후 구조

```
사용자 입력 (버튼/자유텍스트/파일) → LangChain Agent (tool selection) → 기존 Python 파이프라인 실행 → 결과 + Action 버튼
```

### Tool 설계, Agent 생성, Chainlit 통합, 파일 업로드 multi-turn 패턴

→ 상세: `CLAUDE_CODE_WORK_REQUEST_PHASE_A.md`

### 점진적 전환 순서

1. **Step 0**: 의존성 업그레이드 (langchain 1.0, langgraph 1.0) + 기존 테스트 121개 통과 확인
2. **Step 1**: 파일 업로드 없는 tool 먼저 (Q&A, 계획서, 보고서, 종강 처리, 출석부 생성)
3. **Step 2**: 파일 업로드 있는 tool (출석 체크 → 입금 대조 순)
4. **Step 3**: Agent 생성 + main.py 축소
5. **Step 4**: Starter/Action 버튼 연결
6. **Step 5**: E2E 테스트

---

## 3. Phase B 상세

### B-1. 데이터 조회 tool
자연어로 수강생/강사/강좌 조회. 예: "지난 학기 수강생 누구야?"

### B-2. Q&A tool 고도화
응답 스타일 개선 (간결하게 핵심만), Prompt caching 적용, 정관/규정 Q&A

### B-3. 중간 과정 공유 전환
cl.Step → cl.Message로 직접 진행 상태 메시지 표시 (55+ 사용자에게 더 직관적)

### B-4. 세션 영속성
MemorySaver → AsyncPostgresSaver (PostgreSQL), 서버 재시작 후 대화 유지

### B-5. 추가 tool 후보
카카오톡 연동, 강사 실적 보고서, 월간 활동 보고서

---

## 4. Claude Code LangChain Skills 활용

| Skill | 내용 | 사용 시점 |
|---|---|---|
| `framework-selection` | 프레임워크 선택 가이드 | ✅ 완료 |
| `langchain-fundamentals` | `create_agent`, `@tool`, middleware | ✅ Phase A |
| `langchain-dependencies` | 패키지 버전, 호환성 | ✅ Phase A |
| `langgraph-fundamentals` | StateGraph, streaming, 에러 처리 | 커스텀 그래프 필요 시 |
| `langchain-middleware` | HITL, 커스텀 미들웨어 | Phase B |
| `langgraph-persistence` | 체크포인터, PostgreSQL | Phase B |
| `langchain-rag` | RAG 파이프라인 | 컨텍스트 100K 초과 시 |

---

## 5. 장단점 요약

| 관점 | 현재 (고정 파이프라인) | 전환 후 (Agent 라우터) |
|------|----------------------|----------------------|
| **자연어 이해** | 키워드 매칭 + LLM 의도 분류 | LLM이 모든 입력에서 tool 선택 |
| **확장성** | 새 작업 = 4-5곳 수정 | 새 작업 = `@tool` 함수 1개 |
| **비용** | LLM 호출 최소 | 3-5배 증가 (prompt caching 완화) |
| **코드량** | main.py ~70KB | main.py ~200줄 + agent.py + tools/ |
| **UX** | 버튼 가이드 (제한적 자유 입력) | 버튼 가이드 + 완전한 자유 입력 |

# CLAUDE.md

## 프로젝트 요약

위례인생학교(성인 평생교육, 연 4회차) 관리자용 AI 업무 도우미 챗봇. LangChain + Chainlit + Google Sheets API 기반. 핵심 업무: 입금 대조, 출석부 생성, 출석 체크(OCR), 계획서 검토, 보고서 생성, 질의응답.

**설계 원칙**: AI가 90% 처리 → 관리자가 10% 검증. 코드로 될 건 코드로, LLM은 비정형 데이터 해석에만 사용.

## 기술 스택

- **언어**: Python 3.12 (`.python-version`으로 고정)
- **LLM 프레임워크**: LangChain 1.0 `create_agent` + LangGraph 1.0 (Agent 라우터 + 8개 @tool, AsyncPostgresSaver checkpointer)
- **채팅 UI**: Chainlit (WebSocket 기반, Conversation Starter 버튼 지원)
- **LLM**: Claude API (Anthropic) — 한국어 + Vision
- **데이터 SoT**: `USE_DB_SOT` 플래그로 전환
  - `USE_DB_SOT=true`: **PostgreSQL이 SoT**. 챗봇은 DB에 쓰고, 백그라운드 스레드로 Sheets 동기화 (`sheets_sync.py`). DB 실패 시 Sheets 폴백.
  - `USE_DB_SOT=false` (기본): Google Sheets가 SoT (기존 동작)
- **데이터베이스**: PostgreSQL (Railway) — 채팅 기록(chat_data_layer.py) + 비즈니스 데이터(db.py)
- **Google 인증**: Service Account + Domain-wide Delegation
- **배포**: Railway (Git push 자동 배포)
- **RAG 없음**: Context Injection (시스템 프롬프트에 비즈니스 컨텍스트 직접 주입)

## 아키텍처 방침

**고정 파이프라인 + LangChain Agent 라우터**

- 각 작업(입금 대조, 출석부 생성 등)은 실행 순서가 고정된 Python 함수 파이프라인으로 구현한다 — 이 파이프라인 코드는 변경하지 않는다
- LLM은 비정형 텍스트 해석이 필요한 특정 단계에서만 호출한다 (예: 입금자명 파싱)
- LangChain `create_agent` + 7개 coarse-grained `@tool`로 라우팅. Agent가 "어떤 작업을 할지"만 결정, 내부 로직은 기존 Python 파이프라인 유지.
- Starter/Action 버튼은 유지 — 55세+ 사용자를 위한 가이드 UX. 버튼 클릭 → 고정 메시지 발송 → Agent가 해석하여 tool 선택.
- `.agents/skills/`에 LangChain Skills(langchain-ai/langchain-skills)이 설치되어 있음. Claude Code가 LangChain 관련 코드 작성 시 참조하는 코딩 가이드이며, 런타임 동작에는 영향 없음.

**이유**: 대상 사용자가 55세 이상 비개발자 관리자 2~4명. Action 버튼 중심의 가이드 UX로 충분히 안내 가능하면서, Agent의 자연어 이해로 예측 불가능한 입력도 처리.

```python
# 라우팅 패턴 — 세 가지 입력 채널이 Agent로 수렴
# 1) Starter/Action 버튼 → 고정 메시지 → Agent 해석 → tool 선택
# 2) 자유 텍스트 → Agent 해석 → tool 선택
# 3) 파일 업로드 → [FILE:path] 태깅 → Agent 해석 → tool 선택
#
# 파일 대기 중 텍스트만 입력 → 취소 감지 / 재안내 (main.py에서 처리)

@cl.on_message
async def on_message(message: cl.Message):
    content = message.content
    if message.elements:
        for elem in message.elements:
            content += f"\n[FILE:{elem.path}]"
    await _invoke_agent(content)
```

## 용어 정의

- **회차**: 연 4회 운영 단위. 코드 식별자: `2026-1` (연도-번호)
- **회차명**: 표시용 이름. `2026-1 겨울학기` (연도-번호 계절학기, 공백 구분). Drive 폴더명과 동일.
- **계절 매핑**: 1=겨울(1~3월), 2=봄(4~6월), 3=여름(7~9월), 4=가을(10~12월)
- **이름ID**: 사용자 식별자. `이름+핸드폰뒤4자리` (예: 박민서6804)

## Chainlit UX 가이드

대상 사용자(55세+, 비개발자)를 위한 Chainlit 기능 활용 방침.

### 진행 상태 메시지 — cl.Message send/update 패턴
복잡한 작업에서 각 단계를 관리자에게 직접 메시지로 보여줌. "지금 뭘 하고 있는지" 피드백이 신뢰 구축에 핵심. `cl.Message`를 보낸 뒤 `.update()`로 내용을 갱신하여 단계별 진행 표시.
```python
progress = cl.Message(content="📊 데이터를 읽고 있습니다...")
await progress.send()
# ... 작업 수행 ...
progress.content = "📊 수강생 **85명** 로드 완료. 입금 매칭 중..."
await progress.update()
# ... 다음 작업 ...
progress.content = "✅ 78건 매칭 / 🔶 7건 미매칭"
await progress.update()
```

### Action — 사용자 선택지 제공 (non-blocking)
모든 작업 완료/취소/에러 후 `send_default_actions(completed)` 호출로 7개 기본 버튼 제공. 방금 완료한 작업은 "다시하기" 레이블로 표시. 자유 텍스트 입력 없이 클릭만으로 다음 업무 진행.

워크플로우 중간의 확인/선택도 `cl.Message(actions=[...]).send()` + `@cl.action_callback`으로 구현. **`AskActionMessage` 사용 금지** — blocking + timeout으로 UI 멈춤, 파일 업로드 비활성화, state 꼬임 유발.

```python
# ✅ 올바른 패턴: non-blocking (메시지 보내고 즉시 return)
await cl.Message(
    content="**2026-1 겨울학기** 입금 대조를 시작할까요?",
    actions=[
        cl.Action(name="payment_confirm_term", label="✅ 맞습니다", payload={"value": "confirm"}),
        cl.Action(name="payment_other_term", label="📅 다른 회차에요", payload={"value": "other"}),
        cl.Action(name="payment_cancel", label="❌ 취소", payload={"value": "cancel"}),
    ]
).send()
# 함수 끝. 코루틴 종료. timeout 없음.

@cl.action_callback("payment_confirm_term")
async def on_payment_confirm_term(action: cl.Action):
    term = cl.user_session.get("term")
    await _load_signup_and_ask_applicants(term)

# ❌ 금지 패턴: blocking (코루틴이 응답까지 멈춤 + timeout 위험)
# res = await cl.AskActionMessage(content="...", actions=[...]).send()
```

### State Machine — 파일 대기 + busy 상태 관리
Agent가 대화 라우팅을 담당하지만, 파일 업로드 대기와 작업 중 busy 상태는 `user_session`의 state로 관리한다.

```python
# 상태 목록
idle                     # 기본. 모든 입력이 Agent에 전달됨.
awaiting_applicants_file # 수강 신청자 목록 파일 대기 (텍스트만 입력 시 취소 감지 / 재안내)
awaiting_payment_file    # 입금내역 파일 대기
awaiting_ocr_image       # 출석부 사진 대기
creating_attendance      # 출석부 생성 중 (텍스트 입력 시 "잠시만 기다려주세요" 응답)
running_graduation       # 종강 처리 중 (텍스트 입력 시 "잠시만 기다려주세요" 응답)
```

파일 대기 상태에서 텍스트만 입력되면 main.py에서 직접 처리: 취소 키워드 감지 → 취소 / 파일 재요청. 파일이 첨부되면 `[FILE:path]` 태깅하여 Agent에 전달.

`@cl.on_stop` 훅으로 사용자가 멈춤 버튼을 누르면 state를 `idle`로 리셋.

## Chainlit 테마 & 디자인 시스템

### 색상 팔레트 — Palette C: 마을회관

따뜻한 테라코타/황토 톤. "사람이 모이는 커뮤니티" 정체성. 55세+ 사용자를 위한 WCAG AA 이상 접근성.

**Chainlit CSS 변수 → UI 요소 매핑** (실측 기반, 수정 시 주의):

| Variable | Chainlit 실제 사용처 | 성격 |
|----------|---------------------|------|
| `--primary` | 유저 아바타, 체크박스, 라디오, 프로그레스 바, 링크, Step 세로선, 로딩 스피너 | 진한 색 + 흰 텍스트 |
| `--accent` | **입력창 배경, 유저 메시지 버블** | 밝은 색 + 어두운 텍스트 |
| `--secondary` | 보조 버튼, 비활성 영역 | 밝은 중성 색 |
| `--card` | 어시스턴트 메시지 카드 배경 | 거의 흰색 |
| `--muted` | 비활성 배경 영역 | 매우 밝은 색 |

**주의**: `--accent`에 진한 색을 넣으면 입력창과 유저 메시지 버블이 어두워져 텍스트를 읽을 수 없음. 반드시 밝은 색 유지.

주요 색상 (HEX):
- Primary: `#7A5234` (따뜻한 테라코타) — 버튼/강조, 유저 메시지 버블 배경 (CSS 오버라이드)
- Accent: `#E8D8C4` (라이트 웜 피치) — 입력창 배경 (theme.json `--accent`)
- Background: `#FAF6EF` (따뜻한 오프화이트)
- Foreground: `#2C2520` (따뜻한 차콜)
- Border: `#DED7CA` — Starter/Action 버튼 테두리

**CSS 오버라이드** (`stylesheet.css`에서 theme.json 위에 강제 적용):
- 유저 메시지 버블: `--accent` 대신 `#7A5234` 배경 + 흰 텍스트 (`div[data-step-type="user_message"] .bg-accent`)
- Starter 버튼: `#DED7CA` 테두리 + `#FEFCF8` 배경 + 호버 시 `#EAE2D4` 배경 (`#starters button`)
- Action 버튼: `#DED7CA` 테두리 (`div[data-step-type="assistant_message"] button.border-input`)
- 다크모드 토글: 숨김 (`#theme-toggle`)
- Chainlit 기본 로고: 숨김 (`img.logo`), 추후 `public/logo_light.png`로 교체 예정

설정 파일:
- `public/theme.json` — 전체 색상 팔레트 (HSL), 폰트 설정, Google Fonts URL
- `public/stylesheet.css` — 타이포그래피 + UI 오버라이드 (버튼 테두리, 유저 메시지, 로고, 다크모드 토글)
- `.chainlit/config.toml` — `custom_css = "/public/stylesheet.css"`, `default_theme = "light"`, `cot = "tool_call"` (Step 표시)

### 타이포그래피

- **폰트**: Noto Sans KR (400/500/700), `theme.json`의 `custom_fonts`로 로딩
- **본문**: 18px (`1.125rem`), line-height 1.7, letter-spacing 0.01em
- **버튼**: 16px 최소, font-weight 500, min-height 48px (55세+ 클릭 타겟)
- **사이드바**: 16px, line-height 1.6
- **입력창**: 18px, line-height 1.7 (본문과 동일)
- **16px 미만 텍스트 금지** (타임스탬프 14px만 예외)
- **빨간 텍스트 금지** — 색상 대신 아이콘 + Bold로 강조

## 프로젝트 구조

```
wiryeschoolcommunity/
├── CLAUDE.md                    # 이 파일
├── chainlit.md                  # Chainlit 웰컴 화면
├── public/
│   ├── theme.json               # Chainlit 테마 (색상 팔레트 + 폰트, shadcn HSL 형식)
│   └── stylesheet.css           # 커스텀 CSS (타이포그래피, 접근성)
├── .chainlit/
│   └── config.toml              # Chainlit UI 설정 (이름, 테마, CSS 경로 등)
├── .agents/
│   └── skills/                  # LangChain Skills (Claude Code 코딩 가이드)
├── docs/
│   ├── DEV_DOCUMENT.md          # 상세 기획서 (비즈니스 컨텍스트, 데이터 구조, 입금 패턴 등)
│   ├── BUSINESS_CONTEXT.md      # Context Injection 소스 텍스트
│   ├── PLANNED_DRIVE_STRUCTURE.md  # Google Drive 확정 구조 + 폴더 ID 참조
│   └── LANGCHAIN_MIGRATION_PROPOSAL.md  # LangChain Agent 전환 제안서 (Phase A: 라우터 전환)
├── app/
│   ├── main.py                  # Chainlit 엔트리포인트 — Agent invoke + 파일 태깅 + Starter/Action 버튼 (~285줄)
│   ├── agent.py                 # [신규] LangChain create_agent — 시스템 프롬프트 + 7개 tool 바인딩
│   ├── config.py                # 환경 변수, 상수, 영속 Google IDs, COURSE_KEYWORDS, USE_DB_SOT, INSTRUCTOR/STAFF_SHEET_ID
│   ├── tools/                   # @tool 함수 모듈 — Agent가 호출하는 8개 tool
│   │   ├── __init__.py          # ALL_TOOLS 리스트 export
│   │   ├── qa_tool.py           # 업무 Q&A
│   │   ├── payment_tool.py      # 입금 대조 (2-file multi-turn)
│   │   ├── attendance_tool.py   # 출석부 생성 (처리상태 gate + PDF)
│   │   ├── ocr_tool.py          # 출석 체크 OCR (이미지 → Claude Vision)
│   │   ├── graduation_tool.py   # 종강 처리 (6-step pipeline)
│   │   ├── query_tool.py        # 데이터 조회 (자연어 → 구조화 쿼리 → DB)
│   │   ├── plan_tool.py         # 계획서 검토 (placeholder)
│   │   └── report_tool.py       # 보고서 생성 (placeholder)
│   ├── context/
│   │   ├── business.py          # 정적 비즈니스 컨텍스트 dict + 시스템 프롬프트
│   │   └── term.py              # 현재 회차 자동 판별 + 자유 텍스트 회차 파싱 (parse_term_input)
│   ├── chains/
│   │   ├── qa.py                # 질의 응답 체인
│   │   ├── payment.py           # 입금 대조 파이프라인 (DB, 매칭, deposits 추적, 등급 cascade, 강사/사무처 면제)
│   │   ├── attendance.py        # 출석부 생성 — 단계별 함수 분리
│   │   ├── ocr.py               # 출석 체크 OCR (dual-write: DB+Sheets, Claude Vision)
│   │   └── graduation.py        # 종강 처리 — 단계별 함수 분리
│   ├── services/
│   │   ├── google_auth.py       # Google API 인증 (SA 파일 + JSON 환경변수 이중 지원)
│   │   ├── google_drive.py      # Drive API 래퍼 + 동적 폴더 탐색 (find_term_folder 등)
│   │   ├── google_sheets.py     # Sheets API 래퍼 (읽기/쓰기)
│   │   ├── excel.py             # Excel 파싱 (입금내역 .xls/.xlsx + 신청자 목록 HTML .xls)
│   │   ├── signup_loader.py     # Drive에서 신규가입/정회원가입 신청서 로드 → 파싱 결과 반환
│   │   ├── chat_data_layer.py   # Chainlit 채팅 기록 PostgreSQL 영속성 (BaseDataLayer 구현)
│   │   ├── db.py                # 비즈니스 데이터 PostgreSQL CRUD (asyncpg, 7 테이블)
│   │   └── sheets_sync.py       # Sheets 동기화 (asyncio.to_thread, await 직렬)
│   └── utils/
│       ├── __init__.py
│       └── matching.py          # 이름/강좌 추출, 규칙 기반 입금 매칭
├── assets/
│   └── fonts/                   # NanumGothic TTF (git 커밋됨)
├── .python-version              # Python 3.12 고정 (Railway mise 빌드용)
├── nixpacks.toml                # Railway Nixpacks 빌드 설정
├── Procfile                     # PaaS 실행 명령
├── requirements.txt
├── .env.example                 # 환경 변수 템플릿
└── .gitignore
```

## 환경 변수

```
# web 서비스 (Chainlit 챗봇)
ANTHROPIC_API_KEY=              # Claude API 키
GOOGLE_SA_KEY_PATH=sa-key.json  # Service Account JSON 키 파일 경로 (로컬)
GOOGLE_SA_KEY_JSON=             # Service Account JSON 문자열 (PaaS 배포용)
GOOGLE_DELEGATED_USER=wirye@wiryeschoolcomunity.com  # Delegation 대상 (오타 아님, 실제 도메인)
DATABASE_URL=                   # Railway 자동 주입 (PostgreSQL)
USE_DB_SOT=false                # true=PostgreSQL SoT, false=Sheets SoT (기본)
```

## Google API 인증 패턴

```python
from google.oauth2 import service_account
from googleapiclient.discovery import build

# PaaS: GOOGLE_SA_KEY_JSON 환경변수 → from_service_account_info()
# 로컬: GOOGLE_SA_KEY_PATH 파일 → from_service_account_file()
credentials = service_account.Credentials.from_service_account_file(
    os.environ['GOOGLE_SA_KEY_PATH'],
    scopes=['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets'],
    subject=os.environ['GOOGLE_DELEGATED_USER']  # Domain-wide Delegation
)

sheets_service = build('sheets', 'v4', credentials=credentials)
drive_service = build('drive', 'v3', credentials=credentials)
```

## 데이터 아키텍처

### 설계 원칙

- **DB SoT + 백그라운드 Sheets 동기화**: **PostgreSQL이 유일한 SoT**. 챗봇은 DB에 쓰고, 백그라운드 스레드로 Sheets 동기화 (`sheets_sync.py`). DB 실패 시 에러를 호출자에 전파 (Sheets 폴백 없음).
  - `USE_DB_SOT` 플래그는 `main.py`, `ocr.py`, `graduation.py`에 아직 남아 있으나, `payment.py`는 DB-only로 전환 완료.
- **처리상태 예외**: 신청기록/미확인입금의 `처리상태` 컬럼만 관리자가 Sheets에서 직접 편집 (드롭다운: 등록완료/환불완료/취소완료/보류). DB 모드에서도 이 컬럼은 Sheets에서 읽음. 입금 대조 재실행 시 미확인입금의 처리상태를 Sheets→DB 역동기화 (`sync_deposit_processing_status`). 신청기록의 처리상태는 `upsert_applications`의 COALESCE로 보호.
- **신청기록 통합**: 회차별 "신청서" 파일을 생성하지 않음. 회원관리 파일(`MEMBERS_SHEET_ID`)의 `신청기록` 탭에 전 회차 데이터 통합. `sheets_sync.py`가 백그라운드로 push.
- **시트 서식**: 필터, 드롭다운, 보호 설정은 관리자가 Google Sheets UI에서 직접 관리. 코드는 값만 읽고 쓴다 (`values().get/update/clear/append`). 출석부 탭 생성(`addSheet`)만 코드에서 수행.
- **PostgreSQL**: 비즈니스 데이터 (`db.py`, 7 테이블) + 채팅 기록 (`chat_data_layer.py`).
- **Google Drive는 파일 저장소**. Raw 엑셀, PDF, 출석부 등 파일 단위 자료 관리.
- **이모지↔코드 변환**: DB에는 상태 코드(confirmed, not_paid 등) 저장. 앱 코드는 이모지(✅정상, ❌미입금 등) 사용. 변환은 `db.py` 경계에서 수행.
- **타입 변환**: TIMESTAMPTZ 컬럼(`processed_at` 등)은 `db.py`의 `_to_datetime()`으로 str/datetime 모두 안전하게 처리. asyncpg는 str을 받지 않으므로 이 경계 변환이 필수.
- **Deposit 매칭 추적**: 입금내역은 `deposits` 테이블에 전건 저장. 매칭 후 `match_status`(matched/unmatched) + `matched_name_ids` 업데이트. 미확인입금 시트에는 unmatched 건만 표시.

### DB 스키마 (7 테이블)

| 테이블 | PK / UK | 성격 |
|--------|---------|------|
| `members` | `name_id` (TEXT PK) | 회원 현재 상태 |
| `member_records` | `id` (SERIAL) | 등급 변경 이력 |
| `course_records` | `id` (SERIAL), UK `(name_id, term_id, course_name)` | 수강 이력. 재실행 시 출석률만 업데이트 (`ON CONFLICT DO UPDATE`). |
| `applications` | `(term_id, name_id, type, course_name)` UK | 통합 신청서 (`processing_status`, `processed_at`). upsert 시 `processing_status`는 COALESCE 보호 (빈값이면 기존값 유지). |
| `deposits` | `id` (SERIAL), UK `(term_id, transaction_time, amount, payer_name, memo)` | 입금내역 원본 (`match_status`, `matched_name_ids`). 중복 INSERT 방지 (`ON CONFLICT DO NOTHING`). |
| `attendance` | `(term_id, course_name, student_name)` UK | 출석 데이터 |
| `feedbacks` | `id` (TEXT PK) | Chainlit thumbs up/down |

스키마 DDL은 `tests/data/create_tables.sql`에 정의. 배포 전 `psql $DATABASE_URL -f tests/data/create_tables.sql`로 수동 생성. 앱 시작 시 테이블 미존재 시 에러 발생.

### Sheets 동기화

DB SoT 모드에서 DB 쓰기 후 Sheets를 백그라운드로 동기화. `sheets_sync.py`의 `sync_to_sheets()` 사용.

**구조**: `await asyncio.to_thread(_safe_sync, ...)` — 동기 Sheets API를 별도 스레드에서 await 직렬 실행.

| sync_type | 대상 탭 | 패턴 | 호출 시점 |
|-----------|---------|------|----------|
| `applications` | 신청기록 | clear A2:M + write | 입금 대조 시 신청서 upsert 후 |
| `members` | 회원목록 | clear A2:I + write | 등급 cascade 후 |
| `member_records` | 회원기록 | append | 등급 변경 이력 추가 시 |
| `course_records` | 수강기록 | append | 종강 처리 시 |
| `deposits` | 미확인입금 | clear A2:H + write | 입금 매칭 후 unmatched 건만 |

**시트 보호**: 회원관리 5탭 전체 보호 + SA 이메일만 쓰기 허용. 처리상태 컬럼만 관리자 편집 가능 (신청기록 L열, 미확인입금 G열). `values.clear`는 data validation/서식/보호 설정을 유지하므로 드롭다운은 1회 설정 후 영속.

### 데이터 구조

| 시트/탭 | 성격 | DB 테이블 | Sheets 탭 | 설명 |
|---------|------|-----------|-----------|------|
| **회원목록** | Master (영속) | `members` | 회원관리 → 회원목록 | 전체 회원 현재 상태 스냅샷 |
| **회원기록** | History (영속) | `member_records` | 회원관리 → 회원기록 | 등급 변경 이력 |
| **수강기록** | History (영속) | `course_records` | 회원관리 → 수강기록 | 전체 수강 이력 |
| **신청기록** | Working (전 회차 누적) | `applications` | 회원관리 → 신청기록 | 통합 신청서 — 수강+신규가입+정회원, 회차 필터로 열람 |
| **미확인입금** | Working (전 회차 누적) | `deposits` (unmatched) | 회원관리 → 미확인입금 | 자동 매칭 안 된 입금 건만 표시 |
| **출석부** | Working (회차별) | `attendance` | 회차폴더 → 출석부 | 단일 "출석부" 탭 (이름ID/이름/과목명/1~12회차/출석률) |

회원관리 시트(`MEMBERS_SHEET_ID`)는 5탭 구조: `회원목록`, `회원기록`, `수강기록`, `신청기록`, `미확인입금`.
탭명 상수: `MEMBERS_TAB`, `MEMBER_RECORDS_TAB`, `COURSE_RECORDS_TAB` (`config.py`).

### 회원목록 (Master) — 현재 상태 스냅샷

| 이름ID | 이름 | 전화번호 | 주소 | 등급 | 예외여부 | 수강count | 출석률(누적) | 마지막수강회차 |
|--------|------|---------|------|------|---------|----------|------------|--------------|

- **PK**: 이름ID
- **등급**: 회원 / 준회원 / 정회원
- **예외여부**: 입금 대조 시 강사관리/사무처관리 시트에서 자동 판별하여 설정. 종강 강등 시 리셋됨

### 회원기록 (History) — 등급 변경 이력

| 이름ID | 이름 | 변경일시 | 변경전등급 | 변경후등급 | 사유 | 관련회차 |
|--------|------|---------|----------|----------|------|---------|

입금 대조 시 자동 기록 (신규가입→회원, 수강→준회원, 정회원비→정회원), 종강 강등 시 자동 기록.

### 수강기록 (History) — 전체 수강 이력

| 이름ID | 회차 | 과목명 | 출석률 |
|--------|------|--------|--------|

종강 시: 출석부 → 출석률 확정 → 수강기록에 행 추가 → 회원목록 재집계

### 신청기록 (Working, 전 회차 누적) — 수강+가입+정회원 통합 (12컬럼)

| 신청일 | 회차 | 이름ID | 이름 | 유형 | 과목명 | 예상금액 | 입금시간 | 입금자명(적요) | 입금현황 | 확인사유 | 처리상태 |
|--------|------|--------|------|------|--------|---------|---------|-------------|---------|---------|---------|

- **유형**: `수강`(수강료 2만), `신규가입`(가입비 1만), `정회원`(정회원비 12만)
- **과목명**: 수강 유형만 값 있음. 신규가입/정회원은 빈칸.
- **입금현황**: Agent가 자동 채움 (✅정상 / 🔶확인필요 / ⚠️이름불일치 / ❌미입금 / 🔄중복 / 💎면제). DB에는 코드(confirmed 등) 저장, `db.py` 경계에서 이모지로 변환.
- **처리상태**: 드롭다운 (등록완료/환불완료/취소완료/보류). 관리자가 Sheets에서 직접 편집. 출석부 생성 시 필터 기준 (`등록완료`만 포함).
- **DB 전용 컬럼** (Sheets 비노출): `phone`, `address`, `processed_at`
- **데이터 소스**:
  - 수강: 배움숲 다운로드 엑셀 (관리자가 챗봇에 업로드)
  - 신규가입: Drive 신규가입 신청서 폴더 (signup_loader.py가 자동 탐색)
  - 정회원: Drive 정회원가입 신청서 폴더 (signup_loader.py가 자동 탐색)

### 강사/사무처 면제 (자동 판별)

입금 대조 시 강사관리/사무처관리 시트에서 면제 대상을 자동 판별. `get_exception_ids(term_id)` → set[str].

- **강사**: 강사관리 시트(`INSTRUCTOR_SHEET_ID`)에서 현재 사이클에 강의 row가 있는 강사. 1학기 종강 시 강등됨 (다음 사이클 강의하면 재승급).
- **사무처 직원**: 사무처관리 시트(`STAFF_SHEET_ID`)에서 활동종료가 비어있는(=활동 중) 직원. 1학기 종강 시 활동 중이면 강등 제외.
- **사이클**: YY-2(봄) ~ YY+1-1(다음해 겨울). 예: 2025 사이클 = 2025-2, 2025-3, 2025-4, 2026-1.
- **면제 범위**: 가입비 + 정회원비 + 수강비 전부 면제 (입금현황 = 💎면제).
- **SoT**: 강사관리/사무처관리 시트 (Sheet). DB에 복제하지 않음. 입금 대조 시 1회 조회.
- **`is_exception` 자동 설정**: cascade에서 면제 대상의 `members.예외여부 = TRUE` 자동 설정. 종강 강등 시 리셋.

### 등급 전환 (Grade Cascade)

입금 대조 시 `apply_grade_cascade()` 함수가 자동 실행. Idempotent — 재실행 가능.

3-Pass 순서:
1. **신규가입 confirmed/면제** → 비회원을 회원으로 등록
2. **정회원 confirmed/면제** → 회원을 정회원으로 승급 (회원 아니면 🔶확인필요)
3. **수강** → 정회원이면 💎면제, 정회원비 미입금이면 🔶확인필요, 확정이면 준회원 승급

강사/사무처 면제 대상은 Pass 1~2에서 입금현황=💎면제로 처리, 자동 등급 승급 + `is_exception=TRUE` 설정.

### 출석부 (Working, 회차별, 단일 탭)

Google Sheets 파일 1개. 단일 "출석부" 탭 + 과목별 인쇄용 PDF.

```
출석부 시트
└── 탭: 출석부  → 이름ID(A) | 이름(B) | 과목명(C) | 1회차(D) | ... | 12회차(O) | 출석률(P)
```

- **출석부 탭**: 전 과목 수강생 통합. OCR 기록 범위: D열(1회차)~O열(12회차). 출석="O", 결석="". 출석률(P열)은 종강 처리 시 `graduation.py`가 채움 (생성 시 빈칸).
- **PDF**: 과목별 A4 가로 PDF. NanumGothic 12pt, 페이지 분할. Drive 출석부 폴더에 업로드. 페이지번호는 `onPage` 캔버스 콜백으로 렌더링 (빈 페이지 방지).
- 신청기록의 `처리상태`가 `등록완료`인 수강자만 포함

---

## 데이터 파이프라인 설계

### 시스템 아키텍처

```
┌──────────────────────────────────────────────────────┐
│  Railway                                              │
│                                                       │
│  ┌──────────────┐        ┌──────────────────────┐    │
│  │  Chainlit     │───────→│  PostgreSQL           │    │
│  │  + LangChain  │        │  (채팅 기록 +         │    │
│  │  (챗봇)       │        │   비즈니스 데이터)     │    │
│  └──────┬───────┘        └──────────────────────┘    │
│         │                                             │
│         │  sheets_sync.py (백그라운드 스레드)          │
└─────────┼─────────────────────────────────────────────┘
          │
          ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Google Workspace                                         │
   │  Drive (파일) + Sheets (관리자 view, 처리상태 편집)       │
   └──────────────────────────────────────────────────────────┘
          │
          ▼
   ┌─────────────┐
   │ Claude API   │
   │ (LLM 호출)   │
   └─────────────┘
```

### 파이프라인 분류

| 구분 | 트리거 | 실행 엔진 | 설명 |
|------|--------|----------|------|
| **On-demand** | 관리자 버튼 클릭 | 챗봇 (Chainlit) | 입금 대조 (수강+가입+정회원 통합), 출석부 생성, 종강 처리, OCR, 계획서 검토, Q&A |

### 파이프라인 상세

**P3. 종강 처리** (on-demand, ✅ 구현 완료 — `graduation.py` 단계별 함수 + `main.py` cl.Step 직렬 호출)
```
트리거: 관리자가 챗봇에서 "🎓 종강 처리" Starter 버튼 클릭
전제: 출석 체크(OCR)가 모든 과목에 대해 완료된 상태
1. 회차 확인 → 관리자 확정
2. 출석 체크 완료 확인 → 관리자 확정
3. [Step 📊 출석률 집계] load_attendance_results() — 출석부 탭 D~O열 O/빈칸 카운트
4. [Step 📝 출석률 기록] update_attendance_rates_in_sheet() — 출석부 탭 P열 업데이트
5. [Step 📋 수강기록 저장] load_student_name_id_map() + build_course_records() + append
6. [Step 📊 회원 통계 재집계] recalculate_member_stats()
7. [Step 🔄 등급 강등] apply_demotion() — 준회원→회원(매 종강), 정회원→회원(겨울만, 활동 중 사무처 직원 제외)
8. [Step 💾 저장] update_members_sheet() + append_member_records()
```

**DB → Sheets 동기화** (sheets_sync.py, 백그라운드 스레드)
```
챗봇 DB 쓰기 → sync_to_sheets() → asyncio.to_thread(_safe_sync) → clear_range + write_sheet
  applications: 신청기록 (clear A2:M + write)
  members: 회원목록 (clear A2:I + write)
  member_records: 회원기록 (append)
  course_records: 수강기록 (append)
  deposits: 미확인입금 (clear A2:H + write, unmatched만)
```

### 데이터 흐름 정리

| 방향 | 시점 | 내용 |
|------|------|------|
| Raw → DB → Sheets | 입금 대조 시 | 배움숲 엑셀 + Drive 신청서 → `applications` DB upsert → sheets_sync가 신청기록 탭에 push |
| Raw → DB → Sheets | 입금 대조 시 | 은행 입금내역 → `deposits` DB INSERT → 매칭 → `applications` 입금현황 갱신 → sheets_sync push |
| DB + Sheets → Sheets | 출석부 생성 시 | 처리상태='등록완료' 필터 (DB apps + Sheets 처리상태 머지) → 출석부 시트 생성 + PDF |
| Image → Sheets | 출석 체크 시 | 종이 출석부 사진 → Claude Vision OCR → 출석부 탭 D~O열 O/빈칸 |
| Sheets → DB → Sheets | 종강 처리 시 | 출석부 탭 출석률 집계 → 수강기록 append → 회원목록 재집계 → 등급 강등 |

### 신청자 목록 (배움숲 다운로드 원본, SoT)

파일명 패턴: `LEARNING_APPLY{datetime}.xls` (HTML 형식 .xls)

실제 컬럼 (23개 헤더, 22개 데이터 — `실제결제금액` 컬럼 누락으로 1칸 밀림 보정 필요):
번호, 회차, 강좌명, 감면정보, 수강료, **[실제결제금액 — 헤더만 존재, 데이터 없음]**, 신청자(아이디), 성별, 생년월일, 나이, 연락처, 주소, 행정동, 이메일, 분류, 교육기간, 신청상태, 진행상태, 환불신청일, 환불은행, 환불계좌번호, 환불예금주, 환불사유

신청상태 값: 결제완료, 결제대기, 접수완료, 취소, 환불완료, 환불신청, 기간만료, 결제취소

수강생 시트 생성 시: 신청상태에 관계없이 **전체 신청자**를 포함 (취소/환불은 관리자가 별도 확인)

### 입금내역 (은행 엑셀) — 파싱 주의사항

관리자가 챗봇에 직접 업로드 (Drive에 별도 저장하지 않음).

- **1~6행**: 계좌 메타정보 (예금주, 계좌번호, 조회기간) → 스킵
- **7행**: 헤더 (`거래일시, 적요, 의뢰인/수취인, 입금, 출금, ...`)
- **마지막 행**: 합계 → 제외
- **비고 컬럼 없음**: 매칭은 적요와 의뢰인 컬럼만 사용
- **의뢰인/수취인**: 실제 송금자 이름 → 이름 매칭에 핵심
- **적요**: 입금자가 자유 입력한 텍스트 → 이름/강좌 추출에 핵심
- 카카오페이/토스 경유 시 의뢰인이 `(주)카카오페이`, `(주)비바리퍼블리카` → 적요에서 이름 추출 필요

## 입금 대조 작업 플로우 (핵심)

관리자 관점의 전체 플로우:

1. **관리자**: 챗봇에서 "💰 입금 대조" Starter 버튼 클릭 (또는 자유 텍스트 → LLM 의도 분류 → 확인)
2. **Agent**: 현재 날짜 기반으로 회차 추측 → "2026-1 겨울학기 입금 대조를 시작할까요?" → ✅ 맞습니다 / 📅 다른 회차에요 / ❌ 취소
3. **관리자**: "다른 회차에요" 선택 시 → 자유 텍스트로 회차 입력 (`parse_term_input`으로 파싱) → 재확인
4. **Agent**: (자동) Drive에서 신규가입 신청서 로드 → 파싱 (cl.Step 진행 표시)
5. **Agent**: (자동) Drive에서 정회원가입 신청서 로드 → 파싱 (cl.Step 진행 표시)
6. **Agent**: "신청자 목록 엑셀을 업로드해주세요" (배움숲에서 다운로드한 `LEARNING_APPLY*.xls`)
7. **관리자**: 신청자 목록 엑셀을 챗봇에 직접 업로드 (파일 대기 중 텍스트 입력 시: 취소 감지 / Q&A 답변 후 재안내 / 파일 재요청)
8. **Agent**: 신청자 목록 파싱 + 4+5의 신청서와 합쳐 통합 신청서 생성 → Google Sheets에 저장 (필터 + 처리상태 드롭다운 자동 설정)
9. **Agent**: "입금 내역을 업로드해주세요"
10. **관리자**: 입금 내역 엑셀을 챗봇에 직접 업로드
11. **Agent**: 회원 정보 로드 + 강사/사무처 면제 판별 → 입금내역 전건 → DB deposits INSERT → 자동 매칭 (코드 80~90% → LLM 10~20%) → 등급 전환 cascade 실행 → **즉시** 신청서 시트에 자동 반영
12. **Agent**: 숫자 요약 (✅ 78건 🔶 5건 ...) + 미확인입금 안내 + 시트 링크 + "처리상태를 입력해주세요" + 기본 Action 버튼 7개 (`send_default_actions`)
13. **관리자**: 신청기록/미확인입금 시트에서 입금현황 확인 → 배움숲 포탈에서 수강 등록 → 처리상태 '등록완료' 입력
14. **관리자**: '출석부 생성' 클릭
15. **Agent**: 처리상태 gate check (신청기록 + 미확인입금 양쪽에서 NULL/보류 건이 있으면 차단 + 상세 안내) → 관리자 확인
16. **Agent**: 신청기록에서 처리상태='등록완료'인 수강생만 → 출석부 생성 (과목별 필터 자동 설정) → 기본 Action 버튼

### 입금 매칭 로직

2단계 구조: 룰베이스 매칭 → LLM 과목 추출 (fallback)

**면제 선처리** (`apply_exemptions`): (1) 기존 정회원 → 수강 행 💎면제. (2) 강사/사무처 면제 대상(`exception_ids`) → 모든 유형 💎면제. 매칭 대상에서 제외됨.

**입금 유형별 처리**:

| 입금 유형 | 금액 | 매칭 대상 | 등급 전환 |
|----------|------|----------|----------|
| 가입비 | 1만원 | 통합 신청서 유형='신규가입' | 비회원 → 회원 |
| 수강료 | 2만원 | 통합 신청서 유형='수강' | 회원 → 준회원 |
| 수강료+가입비 합산 | 3만원 | 통합 신청서 (수강+신규가입) | 비회원 → 회원 → 준회원 |
| 다과목 합산 | 4만원+ | 통합 신청서 (다과목) | 회원 → 준회원 |
| 정회원비 | 12만원 | 통합 신청서 유형='정회원' | 회원 → 정회원 |
| 가입비+정회원비 | 13만원 | 통합 신청서 (신규가입+정회원) | 비회원 → 회원 → 정회원 |

**룰베이스 매칭** (`matching.py`):
1. 소액 필터링: 금액 < 1만원(예금이자 등) 스킵, "취소됨"/"대기" 키워드 감지
2. 이름 추출: **적요 우선** → 의뢰인 fallback. 카카오페이/토스면 적요에서만 추출
3. 금액 판별: `classify_by_amount()` — 금액 + 과목 수 + 키워드로 유형 분류
4. 가입비/정회원 분류: `_match_type` 태그 → `apply_matching_results`에서 해당 유형 슬롯에 배정
5. 강좌 힌트 추출: 적요에서 이름 제거 → 나머지를 `fuzzy_course_match()`로 과목 매칭
6. 힌트 매칭 + 1과목 이상 금액 → ✅정상 (다과목 개별 입금도 각각 확정)
7. 전과목 합산: 금액 = N×2만원, N = 과목수 → 전 슬롯 한번에 ✅정상

**LLM 추출** (`run_llm_matching`, `TransactionMatch` structured output):
- 룰베이스 fallback 건만 LLM에 전달 (`_llm_context` 태깅)
- LLM은 **추출만 담당**: 적요에서 과목 약칭 추출 → 수강생 과목 목록과 매핑
- 가입비/정회원 키워드 감지 → `match_type`으로 분류 (수강 슬롯 침범 방지)
- `with_structured_output(TransactionMatch)` — Pydantic 스키마 강제, JSON 파싱 오류 없음
- 건당 개별 호출 + `asyncio.gather` 병렬 (`_LLM_CONCURRENCY=5`)

**슬롯 배정** (`apply_matching_results`):
- 3개 인덱스: `tuition_index`(이름ID+과목명), `membership_index`(이름ID), `fullmember_index`(이름ID)
- `_match_type` 태그로 올바른 인덱스에 라우팅 — 가입비가 수강 슬롯에 배정되는 문제 방지
- 강좌 지정 건 → 정확한 슬롯, 이름만 있는 건 → 남은 미입금 슬롯에 fallback
- 전과목 합산 → 해당 이름ID의 모든 미입금 수강 슬롯 한번에 배정

### 입금 상태 코드 (6가지)

| 코드 | 이모지 | 의미 |
|-----|:-----:|------|
| confirmed | ✅ | 정상 매칭 |
| needs_review | 🔶 | 확인 필요 (LLM 추정, 동명이인, 금액 불일치 등) |
| name_mismatch | ⚠️ | 이름 불일치 (대리 입금 추정) |
| not_paid | ❌ | 미입금 |
| duplicate | 🔄 | 중복 입금 |
| exempted | 💎 | 정회원 — 수강료 면제 |

### 입금자명 패턴 (형식 통일 안 됨)

- `이름강좌`: 김기춘경제뉴스로기초
- `이름_강좌`, `이름 강좌`, `이름-강좌`, `이름/강좌`, `이름.강좌`
- `강좌이름` (역순): 경제기초조윤정
- `강좌(이름)`: 황용섭(경제)
- 이름만, 가입비, 연회원, 합산, 대리입금 등 다양

## 비즈니스 상수

```python
TUITION_FEE = 20000            # 수강료 (변동 가능)
MEMBERSHIP_FEE = 10000         # 가입비 (변동 가능)
FULL_MEMBERSHIP_FEE = 120000   # 정회원비 (매년 초 결정, 변동)
REFUND_DEADLINE_DAYS = 7       # 개강 후 환불 마감일
MAX_SESSIONS = 12              # 강좌당 최대 회차
TERMS = {1: "겨울(1~3월)", 2: "봄(4~6월)", 3: "여름(7~9월)", 4: "가을(10~12월)"}
TERM_SEASONS = {1: "겨울", 2: "봄", 3: "여름", 4: "가을"}
```

## Google Sheets/Drive IDs

### 영속 리소스 (config.py에 상수로 정의)

| Resource | 상수명 | Type | ID | 탭명 |
|----------|--------|------|----|------|
| 회원관리 (5탭) | `MEMBERS_SHEET_ID` | Spreadsheet | `193r34mtLHd0-oX7MKJOWq1Ane9iBfbBZB5yYf78R3Bo` | `회원목록`, `회원기록`, `수강기록`, `신청기록`, `미확인입금` |
| Root folder | `ROOT_FOLDER_ID` | Shared Drive root | `0AANInBeWsB7dUk9PVA` | — |
| 회원 폴더 | `MEMBERS_FOLDER_ID` | Drive folder | `12xm3vG4w5nOPTwoWgmyGCpz939KvJ93e` | — |
| 학사운영 folder | `OPERATIONS_FOLDER_ID` | Drive folder | `1WuqNFt-g5qhnY1nMk0a8dsowZHKQVRMm` | — |
| 신규가입 신청서 폴더 | `MEMBER_SIGNUP_FOLDER_ID` | Drive folder | `10ZL8rD9j7OyyZOihfyJ6GRTzmBrTBgWe` | — |
| 정회원가입 신청서 폴더 | `FULLMEMBER_SIGNUP_FOLDER_ID` | Drive folder | `17tsWfYwIRgHHcT1DQEj8Sqa4ys6pe0Vy` | — |
| 강사관리 | `INSTRUCTOR_SHEET_ID` | Spreadsheet | `1GPwpyHU4vzOtDW3eFlvDKh13maR-yq-qaUzJ73HaR2U` | `Sheet1` (강의회차/이름ID/이름/전화번호/주소/과목) |
| 사무처관리 | `STAFF_SHEET_ID` | Spreadsheet | `1hvuXv0NZmEhTW6QDFJ4SYArP51rrPJoWS9BRramtTMY` | `Sheet1` (이름ID/이름/전화번호/주소/역할/활동시작/활동종료) |

회원관리 시트는 `03 회원과 강사/회원(회원명단/가입서/정회원)/` 폴더에 위치한다 (Shared Drive 루트가 아님).
신청서 폴더는 공유 드라이브에 위치.
강사관리/사무처관리 시트는 면제 대상 판별에만 사용 (SoT = Sheet, DB 복제 없음).

### 회원 폴더 내부 구조 (03 회원과 강사/회원/)

```
회원(회원명단/가입서/정회원)/
├── 회원관리 (Google Sheets)          ← 5탭: 회원목록, 회원기록, 수강기록, 신청기록, 미확인입금
├── 신규가입 신청서/                   ← 연도별 Google Forms 응답 xlsx
├── 정회원가입 신청서/                 ← 연도별 Google Forms 응답 xlsx
├── 연회비/                           ← 연회비 기록
└── 운영자료/                         ← 홍보물, 양식, 과거 작업 파일
```

**신청서 파일 탐색**: 신규가입/정회원 신청서는 연도별로 새 Google Form을 생성하므로 파일 ID가 고정이 아님. 챗봇 `signup_loader.py`에서 Drive 폴더 탐색 → Spreadsheet mimeType 필터 → 파일명에 **연도 문자열**(예: "2026") 매칭으로 자동화. 탭명 `Form Responses 1` 우선 시도. 헤더는 키워드 부분 일치(`_get_value_by_keyword`)로 매핑.

### 회차별 리소스 (런타임에 동적 탐색 — config.py에 없음)

| Resource | 탐색 방법 | 참고 ID (2026-1) |
|----------|----------|-----------------|
| 회차 폴더 | `find_term_folder(term_id)` → OPERATIONS_FOLDER_ID → 연도 폴더 → "2026-1 겨울학기" | `1rqb06_MdfaXHGmqbtS6kpb2Y6PdZbk9P` |
| 신청서 폴더 | `find_or_create_folder(term_folder, "신청서")` — Sheets 모드에서만 사용 | — |
| 신청서 시트 | DB 모드: `MEMBERS_SHEET_ID`의 `신청기록` 탭. Sheets 모드: 회차별 `신청서` 파일 | — |
| 출석부 폴더 | `find_or_create_folder(term_folder, "출석부")` | `1i-sixwrwPU_XxYhOwhDvaIqfvCxICWB8` |

**주의**: DB 모드에서 신청기록은 회원관리 파일(`MEMBERS_SHEET_ID`)의 `신청기록` 탭에 전 회차 통합 저장. 회차별 "신청서" 파일은 생성하지 않음. 처리상태 gate와 출석부 생성 시 `신청기록!A1:L5000` + term_id 필터로 읽음.

**주의**: Google Sheets/Docs 내용 읽기·쓰기는 `google-docs` MCP(`readSpreadsheet`, `getSpreadsheetInfo`, `readDocument` 등)를 사용해야 한다. `google_drive_search`는 파일/폴더 이름·메타데이터 탐색 전용이며 파일 내용에는 접근 불가. ID를 아는 경우 `google-docs` MCP로 직접 접근할 것.

## 강좌명 매핑

배움숲 신청자 목록의 강좌명과 Drive/은행 적요에서 사용되는 약칭이 다를 수 있음.

배움숲 기준 강좌명 (2026-1 겨울학기):
경제뉴스로 배우는 경제해설(기초), 경제뉴스로 배우는 경제해설(심화), 금융과 경제, 기초탄탄 오카리나, 나, 마음챙김 명상, 다시 시작하는 All In One 영어, 라이프코칭상담, 미술관투어, 사진촬영기초(스마트폰 활용), 생활교양법률, 스마트폰으로 배우는 여행영어, 심리상담교실(TA), 어반스케치, 요들송배우기, 우쿨렐레 중급, 책, 가끔은 낭독

## 인프라 구성

### Railway 프로젝트 구조

```
Railway 프로젝트
├── web (Chainlit 챗봇)
│   └── ai-wiryeschoolcommunity.up.railway.app
├── Postgres (DB)
│   └── yamanote.proxy.rlwy.net:26189
│   └── postgres-volume
```

### Railway 환경 변수 (web 서비스)

```
ANTHROPIC_API_KEY=              # Claude API 키
GOOGLE_SA_KEY_JSON=             # Service Account JSON 문자열 (PaaS 배포용)
GOOGLE_DELEGATED_USER=wirye@wiryeschoolcomunity.com  # Delegation 대상 (오타 아님, 실제 도메인)
DATABASE_URL=                   # Railway가 자동 주입 (PostgreSQL 연결 문자열)
```

**주의**: Railway Variables에서 반드시 `GOOGLE_SA_KEY_JSON`을 사용할 것 (`GOOGLE_SA_KEY_PATH`에 JSON을 넣으면 "File name too long" 에러 발생)

---

## 현재 상태 및 로드맵

### Phase 0 — 프로토타입 ✅ 완료

기획서, 데이터 구조 설계, Python 프로젝트 초기 셋업, Chainlit 기본 앱 (채팅 UI + Starter 버튼 + Q&A), Google API 인증 (Domain-wide Delegation).

### Phase 1 — 핵심 기능 (Sheets 기반) ✅ 완료

- 입금 대조 파이프라인: 신청자 목록 SoT, 적요+의뢰인 기반 매칭, 6가지 상태 코드
- Excel 파싱 (`app/services/excel.py`): 입금내역 + 신청자 목록(HTML .xls, BeautifulSoup)
- 규칙 기반 매칭 (`app/utils/matching.py`)
- 출석부 생성 (`app/chains/attendance.py`): 단계별 함수 분리 — `group_by_course`, `create_attendance_spreadsheet`, `generate_attendance_pdf`, `upload_pdf_to_drive`. `main.py`에서 cl.Step으로 직렬 호출 + PDF 루프 progress 표시.
- 출석 체크 OCR (`app/chains/ocr.py`): Claude Vision, 출석부 탭 D:O 쓰기
- 종강 처리 (`app/chains/graduation.py`): 단계별 함수 분리 — `load_student_name_id_map`, `build_course_records`, `apply_demotion`. `main.py`에서 6-Step 직렬 호출.
- 회원관리 3탭 구조 (회원목록/회원기록/수강기록), 동적 Drive 폴더 탐색
- 신청서 upsert (기존 행 보존, 새 key만 추가), 입금 시 회원기록 자동 기록
- Non-blocking Action 패턴 (`cl.Message(actions=...) + @cl.action_callback`), 세션 상태 머신
- `@cl.on_stop` 훅으로 사용자 멈춤 시 state 리셋
- cl.Step 진행 상황 표시 (각 파이프라인 단계별 expandable indicator)
- 입금 대조 결과 즉시 자동 반영 (확인 단계 제거, 숫자 요약 한 줄)
- 회차 불일치 시 자유 텍스트 재입력 루프 (`parse_term_input`)
- 자유 텍스트 → LLM 의도 분류 (`classify_intent_llm`) → 관리자 확인 후 워크플로우 진입
- 워크플로우 중 인터럽트 처리 (`handle_mid_flow_text`): 취소 감지, Q&A 답변 후 상태 유지
- 모든 작업 종료 후 공통 기본 Action 버튼 (`send_default_actions`)
- 신청서 시트: 필터 + 처리상태 드롭다운 자동 설정
- 출석부 시트: 단일 "출석부" 탭 구조
- Railway 배포, 단위 테스트 121개 통과
- 커스텀 테마 (Palette C 마을회관): `public/theme.json` + `public/stylesheet.css`
- Noto Sans KR 폰트, 본문 18px, WCAG AA 접근성
- `config.toml`: `cot = "tool_call"` (Step 진행 표시), `description` 추가
- **남은 작업**: E2E 기능 테스트, Context Injection 고도화

### Phase 2 — 데이터 파이프라인 ✅ 완료

인프라 셋업 + 통합 신청서 설계 + Sheets SoT 확정.

**2-1. PostgreSQL 비즈니스 스키마** ✅ 완료
- `app/services/db.py`: 7 테이블 (members, member_records, course_records, applications, deposits, attendance, feedbacks)
- DB가 SoT, Sheets는 `sheets_sync.py`로 동기화
- 스키마 DDL은 `tests/data/create_tables.sql`에 정의, 배포 전 psql로 수동 생성 (앱 시작 시 미존재 에러)

**2-2. DB SoT + Sheets 동기화** ✅ 완료
- `payment.py`: 5개 write 함수 DB+Sheets sync 패턴 (DB 쓰기 → `sync_to_sheets()`)
- `payment.py`: `apply_matching_results()` deposit 매칭 추적 (match_status/matched_name_ids), unmatched count 반환
- `payment.py`: `format_results()` 미확인입금 카운트 표시 (`| 미확인입금: N건`)
- `payment.py`: `apply_grade_cascade()` 등급 전환 cascade (idempotent 3-pass)
- `db.py`: `upsert_applications()`에 `processed_at` 컬럼 추가
- `main.py`: deposit ID 추적 (insert 전후 load), `db.update_deposit_match()` 호출, `processed_at` 설정
- `main.py`: `_check_processing_gate()` DB 모드에서 `MEMBERS_SHEET_ID`/`신청기록` 탭 + term_id 필터
- `main.py`: `write_payment_results()` DB 모드에서 `MEMBERS_SHEET_ID`로 관리자 링크
- `attendance.py`: DB 모드에서 `MEMBERS_SHEET_ID`/`신청기록` 탭에서 처리상태 읽기 + term_id 필터
- `graduation.py`: 3개 함수 async dual-write
- `ocr.py`: `load_course_students` + `write_attendance_to_sheet` dual-write
- `app/services/sheets_sync.py`: Sheets 동기화 (asyncio.to_thread, await 직렬)
- `registration_status` → `processing_status` 전환 완료

### Phase 3 — 기능 확장 + UX 개선

**✅ 완료:**
- 종강 처리 — 출석률 집계, 수강기록 추가, 등급 강등, 회원목록 재집계, 회원기록 기록
- 출석 체크 (OCR) — Claude Vision으로 종이 출석부 디지털화 → 출석부 탭 D~O열 O/빈칸
- 과목별 출석부 PDF 생성 — A4 가로, NanumGothic 12pt, Drive 업로드
- Theme/CSS 커스터마이징 — Palette C 마을회관 + Noto Sans KR 타이포그래피
- 신청서 upsert — 기존 행 보존, 새 key만 추가
- 등급 전환 cascade (`apply_grade_cascade`) — 입금 대조 시 자동 실행, idempotent
- 입금내역 원본 저장 + 매칭 추적 (`deposits` 테이블) — `match_status`/`matched_name_ids` 업데이트, 미확인입금 시트 연동
- 출석부 생성 처리상태 gate — 신청기록 + 미확인입금 양쪽 미처리 건 차단 + 상세 안내 (DB 모드: `MEMBERS_SHEET_ID`의 `신청기록`·`미확인입금` 탭)
- 신청기록 통합 — DB 모드에서 회차별 "신청서" 파일 제거, 회원관리 파일 `신청기록` 탭에 전 회차 통합
- 피드백 수집 — Chainlit thumbs up/down → PostgreSQL feedbacks 테이블
- Starter 버튼 정비 — 작업 순서 정렬 (7개), CSS min-width/flex 레이아웃
- 신청서 폴더 통합 — ASIS(개인 드라이브) → TOBE(공유 드라이브) 전환 완료
- FAQ/Context Injection 보강 — 18개 토픽 (`BUSINESS_CONTEXT` dict): 회차구조, 회원제도, 수강료, 입금패턴, 입금대조절차, 처리상태, 등급전환, 시트구조, 주요링크, 업무일정, 환불규정, 강사사무처면제, 외부시스템, 출석관리, 종강처리, 강의계획서, 드라이브구조, 용어정리
- 합산 입금 분류 — 12만(정회원비), 13만(가입비+정회원비)
- 강사/사무처 면제 자동 판별 — 강사관리/사무처관리 시트에서 면제 대상 자동 추출, 가입비+정회원비+수강비 전부 면제, 등급 자동 승급, 종강 시 활동 중 사무처 직원만 강등 제외
- AskActionMessage → non-blocking 전환 — 전체 12개 blocking AskActionMessage를 `cl.Message(actions=...) + @cl.action_callback` 패턴으로 전환. 26개 새 action callback 추가 (총 33개). `@cl.on_stop` 훅 추가. 회차 입력 상태 통합 (`term_input_next`). 신청서 위치 확인 단계 제거 (Drive 자동 탐색). 처리상태 gate의 `while True` 루프를 recheck callback으로 전환.
- 출석부 생성 + 종강 처리 리팩토링 — 모놀리식 함수를 단계별 함수로 분리. `main.py`에서 cl.Step으로 직렬 호출하여 중간 진행 상태 표시. `creating_attendance`/`running_graduation` state로 작업 중 race condition 방지. PDF 생성 루프에 `progress_msg.update()` 적용.

**✅ Phase A 완료 — LangChain Agent 전환:**
- LangChain 1.0 + LangGraph 1.0 + langchain-anthropic 1.0 업그레이드
- `main.py` 1719줄 → 285줄 (상태 머신 + 33개 callback → Agent invoke + 7개 action callback)
- `app/agent.py` 신규 — `create_agent()` + MemorySaver + 비즈니스 컨텍스트 시스템 프롬프트
- `app/tools/` 신규 — 7개 @tool (qa, payment, attendance, ocr, graduation, plan, report)
- 파일 업로드: `[FILE:path]` 태깅 → Agent가 tool에 전달
- Starter/Action 버튼 동일 유지 (버튼 클릭 → Agent invoke)
- `classify_intent_llm` 제거 — Agent의 tool selection이 의도 분류를 대체

**✅ Phase B 완료 — Agent 기능 확장:**
- PostgresSaver checkpointer (MemorySaver → AsyncPostgresSaver, 대화 state 영속화)
- on_chat_resume 개선 (메시지 replay 루프 제거 → checkpointer 기반 복원)
- 데이터 조회 tool (`query_data`) — 자연어 → LLM 의도 파싱 → 미리 정의된 DB 조회 함수 (NL-to-SQL 아님)
- Q&A 품질 개선 — prompt caching (`cache_control: ephemeral`), 간결한 답변 스타일, max_tokens 1024
- cl.Step → cl.Message 전환 — 모든 tool에서 진행 상태를 일반 메시지로 직접 표시

**✅ 데이터 무결성 + UX 수정 (테스트 중 발견):**
- 미확인입금 처리상태 보호 — 입금 대조 재실행 전 Sheets→DB 역동기화 (`sync_deposit_processing_status`)
- 미확인입금 시트 입금자명 빈값 수정 — `_sync_deposits`에서 `matched_name_ids` 또는 `의뢰인` 표시
- 회원기록 변경일시 YYYY-MM-DD 포맷 — `_sync_member_records`에서 날짜 잘라내기
- "새 채팅" 다이얼로그 문구 수정 — "기록이 지워진다" → "사이드바에서 다시 열 수 있다" (ko.json)
- 파일 대기 중 자연어 질문 대응 — 취소 아닌 텍스트는 Agent에게 전달 후 재안내
- 신청자 목록 건너뛰기 — DB에 기존 데이터 있으면 "건너뛰기" 텍스트로 스킵 가능

**✅ Agent E2E 테스트 이슈 일괄 수정:**
- AIMessage.content list 파싱 — tool use 후 text 블록 추출, tool_use 블록 필터링
- 에러 메시지 사용자 친화적 변경 — 기술적 세부사항 제거, 한국어 안내
- 회차 확인 안내 — 입금 대조 시작 시 다른 회차 처리 방법 안내 문구 추가
- 상대적 시간 표현 — `parse_term_input()`에 지난학기/이번학기/작년 등 파싱 추가
- Agent 시스템 프롬프트에 현재/직전 회차 컨텍스트 동적 주입
- LLM rate limit 완화 — concurrency 1, sleep 1.5s (Tier 1 RPM 50 대응)
- 처리완료 건 건너뛰기 — 재실행 시 ✅정상/💎면제 건은 매칭 대상에서 제외
- 집계 쿼리 3종 추가 — course_summary, payment_summary, grade_distribution

**📋 백로그:**
- **비즈니스 컨텍스트 최적화**: selective context injection (tool별 관련 컨텍스트만 주입), 컨텍스트 ~100K 토큰 초과 시 RAG 도입
- 보고서 생성: DB SQL 집계 → PDF (placeholder 버튼 배치 완료)
- 계획서 검토: PDF 파싱 → 오탈자/말투 수정 → 배움숲 멘트 생성 (placeholder 배치 완료)
- 첫 화면 로고+타이틀 PNG 이미지 제작 (`public/logo_light.png` → CSS 워크어라운드 제거)
- Accent 색상(#2B7A6E 틸) 적용 위치 결정 (현재 미사용)

## 코딩 규칙

- 한국어 주석 OK, 변수명/함수명은 영문
- **데이터 읽기/쓰기는 DB SoT**. `payment.py` 공개 API는 DB-only (폴백 없음). `db.py`의 CRUD 함수 + `sheets_sync.py`의 `sync_to_sheets()` 사용. `main.py`/`ocr.py`/`graduation.py`는 아직 `USE_DB_SOT` 분기 잔존.
- LLM 호출은 최소화 — 코드로 처리 가능하면 코드로
- 에러 시 사용자에게 한국어로 안내 메시지 반환
- Docker 사용 안 함. Railway는 Procfile 기반 배포.
- 상세 기획은 `docs/DEV_DOCUMENT.md` 참조
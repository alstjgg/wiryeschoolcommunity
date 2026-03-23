# CLAUDE.md

## 프로젝트 요약

위례인생학교(성인 평생교육, 연 4회차) 관리자용 AI 업무 도우미 챗봇. LangChain + Chainlit + Google Sheets API 기반. 핵심 업무: 입금 대조, 출석부 생성, 출석 체크(OCR), 계획서 검토, 보고서 생성, 질의응답.

**설계 원칙**: AI가 90% 처리 → 관리자가 10% 검증. 코드로 될 건 코드로, LLM은 비정형 데이터 해석에만 사용.

## 기술 스택

- **언어**: Python 3.12 (`.python-version`으로 고정)
- **LLM 프레임워크**: LangChain (LLM 호출 래퍼로만 사용)
- **채팅 UI**: Chainlit (WebSocket 기반, Conversation Starter 버튼 지원)
- **LLM**: Claude API (Anthropic) — 한국어 + Vision
- **데이터 SoT**: `USE_DB_SOT` 플래그로 전환
  - `USE_DB_SOT=true`: **PostgreSQL이 SoT**. 챗봇은 DB에 쓰고, n8n webhook으로 Sheets 동기화 (직접 Sheets 쓰기 없음). DB 실패 시 Sheets 폴백.
  - `USE_DB_SOT=false` (기본): Google Sheets가 SoT (기존 동작)
- **데이터베이스**: PostgreSQL (Railway) — 채팅 기록(chat_data_layer.py) + 비즈니스 데이터(db.py)
- **배치 파이프라인**: n8n (Railway) — DB→Sheets 동기화 (Sync-1 일일 전체, Sync-2 웹훅 즉시)
- **Google 인증**: Service Account + Domain-wide Delegation
- **배포**: Railway (Git push 자동 배포)
- **RAG 없음**: Context Injection (시스템 프롬프트에 비즈니스 컨텍스트 직접 주입)

## 아키텍처 방침

**고정 파이프라인 + LLM은 특정 단계에서만 (Agent 패턴 사용 금지)**

- LangChain Agent나 tool-calling agent 패턴을 사용하지 않는다
- 각 작업(입금 대조, 출석부 생성 등)은 실행 순서가 고정된 Python 함수 파이프라인으로 구현한다
- LLM은 비정형 텍스트 해석이 필요한 특정 단계에서만 호출한다 (예: 입금자명 파싱)
- LangChain은 LLM 호출 래퍼(ChatAnthropic)로만 사용, 오케스트레이션 프레임워크로는 사용하지 않는다
- 의도 분류는 Conversation Starter 버튼의 고정 메시지로 판별. 버튼이 아닌 자유 텍스트 입력에 한해 LLM 기반 intent 분류를 사용하며 (`classify_intent_llm`), 분류 결과는 반드시 관리자 확인 단계(`AskActionMessage`)를 거친다.
- 워크플로우 진행 중 파일 대신 텍스트가 입력되면 `handle_mid_flow_text()`로 처리: 취소 키워드 감지 → Q&A 답변 후 상태 유지 → 파일 재요청. 워크플로우를 이탈하지 않는다.
- `.agents/skills/`에 LangChain Skills(langchain-ai/langchain-skills)이 설치되어 있음. Claude Code가 LangChain 관련 코드 작성 시 참조하는 코딩 가이드이며, 런타임 동작에는 영향 없음.

**이유**: 대상 사용자가 55세 이상 비개발자 관리자 2~4명. 대화형 AI에 익숙하지 않음. 예측 가능하고 가이드된 UX가 필수. 자유도가 높으면 오히려 혼란.

```python
# 라우팅 패턴 — 세션 상태 → 버튼 메시지 → 자유 텍스트 LLM 의도 분류
# 1) 워크플로우 진행 중: 파일 있으면 핸들러, 없으면 mid-flow 텍스트 처리
if session_state == "awaiting_applicants_file":
    if message.elements:
        await handle_applicants_file(message)
    else:
        await handle_mid_flow_text(message, session_state)  # 취소/질문/재안내
    return
# 2) Starter 버튼 메시지로 분기
if message.content == "입금 대조를 시작합니다.":
    await start_payment_flow(message)
# 3) 자유 텍스트 → LLM 의도 분류 → 확인 후 워크플로우 진입
else:
    intent = await classify_intent_llm(message.content)
    if intent["intent"] == "question":
        await qa_flow(message)
    else:
        await ask_intent_confirm(intent)
```

## 용어 정의

- **회차**: 연 4회 운영 단위. 코드 식별자: `2026-1` (연도-번호)
- **회차명**: 표시용 이름. `2026-1 겨울학기` (연도-번호 계절학기, 공백 구분). Drive 폴더명과 동일.
- **계절 매핑**: 1=겨울(1~3월), 2=봄(4~6월), 3=여름(7~9월), 4=가을(10~12월)
- **이름ID**: 사용자 식별자. `이름+핸드폰뒤4자리` (예: 박민서6804)

## Chainlit UX 가이드

대상 사용자(55세+, 비개발자)를 위한 Chainlit 기능 활용 방침.

### Step — 중간 진행 상황 공유
복잡한 작업에서 각 단계를 관리자에게 시각적으로 보여줌. "지금 뭘 하고 있는지" 피드백이 신뢰 구축에 핵심.
```python
@cl.step(name="📊 데이터 읽기")
async def read_data():
    ...  # 관리자에게 "수강생 시트를 읽고 있어요..." 표시

@cl.step(name="🔍 입금 매칭 중")
async def match_payments():
    ...  # "85건 중 78건 매칭 완료..." 중간 결과 표시
```

### Action — 다음 작업 추천 버튼
모든 작업 완료/취소/에러 후 `send_default_actions(completed)` 호출로 6개 기본 버튼 제공. 방금 완료한 작업은 "다시하기" 레이블로 표시. 자유 텍스트 입력 없이 클릭만으로 다음 업무 진행.
```python
await send_default_actions("payment")  # 입금 대조 완료 후 → "💰 입금 대조 다시하기" 레이블
await send_default_actions()            # 에러/취소 후 → 기본 레이블
```

### AskActionMessage — 사용자 확인 대기
파이프라인 중간에 관리자 확인이 필요한 지점에서 사용. 응답을 기다렸다가 다음 단계 진행.
```python
res = await cl.AskActionMessage(
    content="매칭 결과를 시트에 반영할까요?",
    actions=[
        cl.Action(name="confirm", label="✅ 반영하기", value="confirm"),
        cl.Action(name="cancel", label="❌ 취소", value="cancel"),
    ]
).send()
if res and res.get("value") == "confirm":
    await write_back_to_sheets()
```

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
- `.chainlit/config.toml` — `custom_css = "/public/stylesheet.css"`, `default_theme = "light"`

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
│   └── PLANNED_DRIVE_STRUCTURE.md  # Google Drive 확정 구조 + 폴더 ID 참조
├── n8n/                         # n8n 워크플로우 JSON (n8n UI에서 import 용)
│   ├── sync_daily.json          # Sync-1: DB → Sheets 일일 전체 push (매일 06:00)
│   └── sync_webhook.json        # Sync-2: DB → Sheets 웹훅 즉시 push
├── app/
│   ├── main.py                  # Chainlit 엔트리포인트 + 세션 상태 라우터 + LLM 의도 분류 + mid-flow 인터럽트 처리
│   ├── config.py                # 환경 변수, 상수, 영속 Google IDs, COURSE_KEYWORDS, USE_DB_SOT
│   ├── context/
│   │   ├── business.py          # 정적 비즈니스 컨텍스트 dict + 시스템 프롬프트
│   │   └── term.py              # 현재 회차 자동 판별 + 자유 텍스트 회차 파싱 (parse_term_input)
│   ├── chains/
│   │   ├── qa.py                # 질의 응답 체인
│   │   ├── payment.py           # 입금 대조 파이프라인 (DB+n8n, 매칭, deposits 추적, 등급 cascade)
│   │   ├── attendance.py        # 출석부 생성 (처리상태는 Sheets에서 읽기, DB모드: 신청기록 탭)
│   │   ├── ocr.py               # 출석 체크 OCR (dual-write: DB+Sheets, Claude Vision)
│   │   └── graduation.py        # 종강 처리 (dual-write, 출석률 집계, 등급 강등)
│   ├── services/
│   │   ├── google_auth.py       # Google API 인증 (SA 파일 + JSON 환경변수 이중 지원)
│   │   ├── google_drive.py      # Drive API 래퍼 + 동적 폴더 탐색 (find_term_folder 등)
│   │   ├── google_sheets.py     # Sheets API 래퍼 (읽기/쓰기)
│   │   ├── excel.py             # Excel 파싱 (입금내역 .xls/.xlsx + 신청자 목록 HTML .xls)
│   │   ├── signup_loader.py     # Drive에서 신규가입/정회원가입 신청서 로드 → 파싱 결과 반환
│   │   ├── chat_data_layer.py   # Chainlit 채팅 기록 PostgreSQL 영속성 (BaseDataLayer 구현)
│   │   ├── db.py                # 비즈니스 데이터 PostgreSQL CRUD (asyncpg, 7 테이블)
│   │   └── n8n.py               # n8n 웹훅 트리거 (DB→Sheets 동기화 fire-and-forget)
│   └── utils/
│       ├── __init__.py
│       └── matching.py          # 이름/강좌 추출, 규칙 기반 입금 매칭
├── assets/
│   └── fonts/                   # NanumGothic TTF (빌드 시 다운로드, .gitignore)
├── scripts/
│   ├── download_fonts.py        # NanumGothic 폰트 다운로드 (빌드 시 자동 실행)
│   ├── init_db_schema.py        # DB 비즈니스 테이블 생성 (최초 1회)
│   ├── migrate_v4.py            # v4.0 스키마 마이그레이션 (processing_status, deposits)
│   ├── populate_members.py      # 회원관리 시트 초기 데이터 생성
│   └── populate_students.py     # 수강생 시트 초기 데이터 생성
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
N8N_WEBHOOK_URL=                # n8n 웹훅 URL (DB→Sheets 동기화, 예: https://n8n-production-81b4.up.railway.app)
```

n8n 환경 변수는 "인프라 구성 > n8n 배포 방법" 섹션 참조.

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

- **DB SoT + n8n Sheets 동기화** (`USE_DB_SOT` 환경변수로 전환):
  - `USE_DB_SOT=true`: **PostgreSQL이 SoT**. 챗봇은 DB에 쓰고, n8n webhook으로 Sheets 동기화. 챗봇이 Sheets에 직접 쓰지 않음.
  - `USE_DB_SOT=false` (기본): **Google Sheets가 SoT**. 기존 동작 유지. DB 쓰기 안 함.
- **DB 실패 시 자동 폴백**: DB 읽기/쓰기 실패하면 Sheets로 폴백 + 로그 기록. 서비스 중단 없음.
- **처리상태 예외**: 신청기록/미확인입금의 `처리상태` 컬럼만 관리자가 Sheets에서 직접 편집 (드롭다운: 등록완료/환불완료/취소완료/보류). DB 모드에서도 이 컬럼은 Sheets에서 읽음.
- **신청기록 통합**: DB 모드에서는 회차별 "신청서" 파일을 생성하지 않음. 회원관리 파일(`MEMBERS_SHEET_ID`)의 `신청기록` 탭에 전 회차 데이터 통합. n8n이 DB→Sheets push.
- **PostgreSQL**: 비즈니스 데이터 (`db.py`, 7 테이블) + 채팅 기록 (`chat_data_layer.py`).
- **Google Drive는 파일 저장소**. Raw 엑셀, PDF, 출석부 등 파일 단위 자료 관리.
- **이모지↔코드 변환**: DB에는 상태 코드(confirmed, not_paid 등) 저장. 앱 코드는 이모지(✅정상, ❌미입금 등) 사용. 변환은 `db.py` 경계에서 수행.
- **Deposit 매칭 추적**: 입금내역은 `deposits` 테이블에 전건 저장. 매칭 후 `match_status`(matched/unmatched) + `matched_name_ids` 업데이트. 미확인입금 시트에는 unmatched 건만 표시.

### DB 스키마 (7 테이블)

| 테이블 | PK / UK | 성격 |
|--------|---------|------|
| `members` | `name_id` (TEXT PK) | 회원 현재 상태 |
| `member_records` | `id` (SERIAL) | 등급 변경 이력 |
| `course_records` | `id` (SERIAL) | 수강 이력 |
| `applications` | `(term_id, name_id, type, course_name)` UK | 통합 신청서 (`processing_status`, `processed_at`) |
| `deposits` | `id` (SERIAL) | 입금내역 원본 (`match_status`, `matched_name_ids`) |
| `attendance` | `(term_id, course_name, student_name)` UK | 출석 데이터 |
| `feedbacks` | `id` (TEXT PK) | Chainlit thumbs up/down |

스키마 DDL은 `db.py`의 `_BUSINESS_SCHEMA_SQL`에 정의. `get_pool()` 첫 호출 시 자동 생성.

### n8n 동기화

| 워크플로우 | 트리거 | 동작 |
|-----------|--------|------|
| Sync-1 (`sync_daily.json`) | 매일 06:00 | 전체 테이블 → Sheets 덮어쓰기 |
| Sync-2 (`sync_webhook.json`) | 챗봇 웹훅 호출 | 변경된 시트만 즉시 업데이트 |

웹훅 엔드포인트: `N8N_WEBHOOK_URL/webhook/sheets-sync` (POST, body: `{type, term_id, ...}`)

### 데이터 구조

| 시트/탭 | 성격 | DB 테이블 | Sheets 탭 | 설명 |
|---------|------|-----------|-----------|------|
| **회원목록** | Master (영속) | `members` | 회원관리 → 회원목록 | 전체 회원 현재 상태 스냅샷 |
| **회원기록** | History (영속) | `member_records` | 회원관리 → 회원기록 | 등급 변경 이력 |
| **수강기록** | History (영속) | `course_records` | 회원관리 → 수강기록 | 전체 수강 이력 |
| **신청기록** | Working (전 회차 누적) | `applications` | 회원관리 → 신청기록 | 통합 신청서 — 수강+신규가입+정회원, 회차 필터로 열람 |
| **미확인입금** | Working (전 회차 누적) | `deposits` (unmatched) | 회원관리 → 미확인입금 | 자동 매칭 안 된 입금 건만 표시 |
| **출석부** | Working (회차별) | `attendance` | 회차폴더 → 출석부 | 수강생 탭 + 과목별 탭, 12회차 출석 |

회원관리 시트(`MEMBERS_SHEET_ID`)는 5탭 구조: `회원목록`, `회원기록`, `수강기록`, `신청기록`, `미확인입금`.
탭명 상수: `MEMBERS_TAB`, `MEMBER_RECORDS_TAB`, `COURSE_RECORDS_TAB` (`config.py`).

### 회원목록 (Master) — 현재 상태 스냅샷

| 이름ID | 이름 | 전화번호 | 주소 | 등급 | 예외여부 | 수강count | 출석률(누적) | 마지막수강회차 |
|--------|------|---------|------|------|---------|----------|------------|--------------|

- **PK**: 이름ID
- **등급**: 회원 / 준회원 / 정회원
- **예외여부**: TRUE이면 종강 시 정회원 강등 면제 (강사/사무처)

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
- **입금현황**: Agent가 자동 채움 (✅정상 / 🔶확인필요 / ⚠️이름불일치 / ❌미입금 / 🔄중복 / 💎면제). DB에는 코드(confirmed 등) 저장, n8n Code 노드에서 이모지로 변환.
- **처리상태**: 드롭다운 (등록완료/환불완료/취소완료/보류). 관리자가 Sheets에서 직접 편집. 출석부 생성 시 필터 기준 (`등록완료`만 포함).
- **DB 전용 컬럼** (Sheets 비노출): `phone`, `address`, `processed_at`
- **데이터 소스**:
  - 수강: 배움숲 다운로드 엑셀 (관리자가 챗봇에 업로드)
  - 신규가입: Drive 신규가입 신청서 폴더 (signup_loader.py가 자동 탐색)
  - 정회원: Drive 정회원가입 신청서 폴더 (signup_loader.py가 자동 탐색)

### 등급 전환 (Grade Cascade)

입금 대조 시 `apply_grade_cascade()` 함수가 자동 실행. Idempotent — 재실행 가능.

3-Pass 순서:
1. **신규가입 confirmed** → 비회원을 회원으로 등록
2. **정회원 confirmed** → 회원을 정회원으로 승급 (회원 아니면 🔶확인필요)
3. **수강** → 정회원이면 💎면제, 정회원비 미입금이면 🔶확인필요, 확정이면 준회원 승급

### 출석부 (Working, 회차별, 1파일 다중시트)

Google Sheets 파일 1개. 수강생 탭 + 과목별 탭 + 과목별 인쇄용 PDF.

```
출석부 시트
├── 탭: 수강생       → 이름ID(A) | 이름(B) | 과목명(C) | 출석률(D)
├── 탭: {과목명1}    → 이름(A) | 1회차(B) | ... | 12회차(M)
└── 탭: {과목명2}    → 동일 구조
```

- **수강생 탭**: 전체 수강생 현황. 출석률(D열)은 종강 처리 시 `graduation.py`가 채움 (생성 시 빈칸).
- **과목별 탭**: 이름ID·출석률 없음. OCR 기록 범위: B열(1회차)~M열(12회차). 출석="O", 결석="".
- **PDF**: 과목별 A4 가로 PDF. NanumGothic 12pt, 페이지 분할. Drive 출석부 폴더에 업로드.
- 신청기록의 `처리상태`가 `등록완료`인 수강자만 포함

---

## 데이터 파이프라인 설계

### 시스템 아키텍처

```
┌──────────────────────────────────────────────────────┐
│  Railway                                              │
│                                                       │
│  ┌──────────────┐        ┌──────────────────────┐    │
│  │  Chainlit     │        │  PostgreSQL           │    │
│  │  + LangChain  │───────→│  (채팅 기록 +         │    │
│  │  (챗봇)       │        │   비즈니스 데이터)     │    │
│  └──────┬───────┘        └──────────┬───────────┘    │
│         │                           │                 │
│  ┌──────┴───────┐          ┌────────┴──────────┐     │
│  │  n8n          │←─webhook─│  DB→Sheets Sync   │     │
│  │  (동기화)     │          └──────────────────-┘     │
│  └──────────────┘                                     │
└───────────────────────────────────────────────────────┘
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

### n8n 배치 파이프라인 상세

**P3. 종강 처리** (on-demand, ✅ 구현 완료 — `graduation.py`)
```
트리거: 관리자가 챗봇에서 "🎓 종강 처리" Starter 버튼 클릭
전제: 출석 체크(OCR)가 모든 과목에 대해 완료된 상태
1. 회차 확인 → 관리자 확정
2. 출석 체크 완료 확인 → 관리자 확정
3. 과목별 탭에서 O/빈칸 직접 카운트 → 출석률 집계
4. 수강생 탭 출석률(D열) 업데이트
5. 수강기록 탭에 append (수강생 × 과목)
6. 회원목록 재집계 (수강count, 출석률(누적), 마지막수강회차)
7. 준회원 → 회원 일괄 강등 (매 종강 시)
8. (1학기 종강 시) 정회원 → 회원 강등 (예외여부=TRUE 제외)
9. 회원기록 탭에 강등 이력 append
```

**DB → Sheets 동기화** (n8n)
```
Sync-1 (n8n/sync_daily.json): 매일 06:00 전체 push — members, member_records, course_records → Sheets 덮어쓰기
Sync-2 (n8n/sync_webhook.json): 웹훅 즉시 push — 챗봇이 DB 쓰기 후 n8n 웹훅 호출 → 변경 시트만 업데이트
```

### 데이터 흐름 정리

| 방향 | 시점 | 내용 |
|------|------|------|
| Raw → DB → n8n → Sheets | 입금 대조 시 | 배움숲 엑셀 + Drive 신청서 → `applications` DB upsert → n8n이 신청기록 탭에 push |
| Raw → DB → n8n → Sheets | 입금 대조 시 | 은행 입금내역 → `deposits` DB INSERT → 매칭 → `applications` 입금현황 갱신 → n8n push |
| DB + Sheets → Sheets | 출석부 생성 시 | 처리상태='등록완료' 필터 (DB apps + Sheets 처리상태 머지) → 출석부 시트 생성 + PDF |
| Image → Sheets | 출석 체크 시 | 종이 출석부 사진 → Claude Vision OCR → 과목별 탭 O/빈칸 |
| Sheets → DB → n8n → Sheets | 종강 처리 시 | 과목별 탭 출석률 집계 → 수강기록 append → 회원목록 재집계 → 등급 강등 |

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
3. **관리자**: "다른 회차에요" 선택 시 → 자유 텍스트로 회차 입력 (`parse_term_input`으로 파싱) → 재확인 루프
4. **Agent**: "신청자 목록 엑셀을 업로드해주세요" (배움숲에서 다운로드한 `LEARNING_APPLY*.xls`)
5. **관리자**: 신청자 목록 엑셀을 챗봇에 직접 업로드 (파일 대기 중 텍스트 입력 시: 취소 감지 / Q&A 답변 후 재안내 / 파일 재요청)
6. **Agent**: 신청자 목록 파싱 (수강 유형)
7. **Agent**: (자동) Drive에서 신규가입 신청서 로드 → 파싱 (cl.Step 진행 표시)
8. **Agent**: (자동) Drive에서 정회원가입 신청서 로드 → 파싱 (cl.Step 진행 표시)
9. **Agent**: 6+7+8을 합쳐 통합 신청서 생성 → Google Sheets에 저장 (필터 + 처리상태 드롭다운 자동 설정)
10. **Agent**: "입금 내역을 업로드해주세요"
11. **관리자**: 입금 내역 엑셀을 챗봇에 직접 업로드
12. **Agent**: 입금내역 전건 → DB deposits INSERT → 자동 매칭 (코드 80~90% → LLM 10~20%) → 등급 전환 cascade 실행 → **즉시** 신청서 시트에 자동 반영
13. **Agent**: 숫자 요약 (✅ 78건 🔶 5건 ...) + 미확인입금 안내 + 시트 링크 + "처리상태를 입력해주세요" + 기본 Action 버튼 7개 (`send_default_actions`)
14. **관리자**: 신청기록/미확인입금 시트에서 입금현황 확인 → 배움숲 포탈에서 수강 등록 → 처리상태 '등록완료' 입력
15. **관리자**: '출석부 생성' 클릭
16. **Agent**: 처리상태 gate check (신청기록 + 미확인입금 양쪽에서 NULL/보류 건이 있으면 차단 + 상세 안내) → 관리자 확인
17. **Agent**: 신청기록에서 처리상태='등록완료'인 수강생만 → 출석부 생성 (과목별 필터 자동 설정) → 기본 Action 버튼

### 입금 매칭 로직

2단계 구조: 코드 매칭(80~90%) → LLM 예외 처리(10~20%)

**정회원 선처리**: 회원관리 등급 "정회원" → 입금현황 = "💎면제" (매칭 대상 제외). 등록상태는 관리자가 포탈 처리 후 직접 체크.

**입금 유형별 처리**:

| 입금 유형 | 금액 | 매칭 대상 | 등급 전환 |
|----------|------|----------|----------|
| 가입비 | 1만원 | 통합 신청서 유형='신규가입' | 비회원 → 회원 |
| 수강료 | 2만원 | 통합 신청서 유형='수강' | 회원 → 준회원 |
| 수강료+가입비 합산 | 3만원 | 통합 신청서 (수강+신규가입) | 비회원 → 회원 → 준회원 |
| 다과목 합산 | 4만원+ | 통합 신청서 (다과목) | 회원 → 준회원 |
| 정회원비 | 12만원 | 통합 신청서 유형='정회원' | 회원 → 정회원 |
| 가입비+정회원비 | 13만원 | 통합 신청서 (신규가입+정회원) | 비회원 → 회원 → 정회원 |

**코드 매칭 순서**:
1. 비수강료 필터링: 금액 < 1만원(예금이자 등) 스킵, "취소됨"/"대기" 키워드 감지
2. 금액 분류: 1만(가입비), 2만(수강료), 3만(합산), 4만+(다과목), 12만(정회원), 13만(가입비+정회원)
3. 이름 매칭: 의뢰인 컬럼 → 통합 신청서의 이름 (정확 일치). 카카오페이/토스면 적요에서 추출
4. 강좌 매칭: 적요 컬럼에서 강좌 키워드 추출 → 신청서의 과목명과 대조
5. 동명이인: 이름 매칭 2명+ → 강좌명으로 2차 구분, 안 되면 🔶확인필요

**LLM 처리**: 코드로 매칭 실패한 건만. 비정형 적요 텍스트 해석, 후보 목록과 비교.

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

회원관리 시트는 `03 회원과 강사/회원(회원명단/가입서/정회원)/` 폴더에 위치한다 (Shared Drive 루트가 아님).
신청서 폴더는 공유 드라이브에 위치.

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
Railway 프로젝트 1 (기존)              Railway 프로젝트 2 (n8n)
├── web (Chainlit 챗봇)               └── n8n (Docker: n8nio/n8n)
│   └── ai-wiryeschoolcommunity.          └── n8n-production-81b4.up.railway.app
│       up.railway.app                    └── 기존 Postgres에 public URL로 연결
├── Postgres (공유 DB)
│   └── yamanote.proxy.rlwy.net:26189
│   └── postgres-volume
```

n8n은 Railway 템플릿("n8n w/ postgres")으로 별도 프로젝트에 배포. 템플릿이 생성한 Postgres-us3E는 삭제 완료. n8n은 기존 Postgres의 public URL로 연결.

### n8n 배포 방법

Railway 템플릿 "n8n (w/ postgres)"으로 배포 후 기존 PostgreSQL로 연결 전환:

```
1. Railway 대시보드 → "+ New" → "Template" → "n8n (w/ postgres)" 선택 → Deploy
2. 새 프로젝트로 n8n + Postgres가 생성됨
3. n8n 서비스 Variables에서 DB 연결 정보를 기존 Postgres의 public URL로 변경
4. 템플릿이 생성한 새 Postgres 서비스 삭제
5. n8n 웹 UI 접속 → admin 계정 생성
```

**주의**: n8n과 기존 Postgres가 다른 프로젝트에 있으므로 `*.railway.internal` (internal host)은 사용 불가. 반드시 `DATABASE_PUBLIC_URL`에서 추출한 public host + port를 사용.

n8n 환경 변수 (실제 배포 설정):

| 변수명 | 값 | 설명 |
|--------|-----|------|
| `DB_TYPE` | `postgresdb` | DB 종류 |
| `DB_POSTGRESDB_HOST` | `yamanote.proxy.rlwy.net` | 기존 Postgres public host |
| `DB_POSTGRESDB_PORT` | `26189` | 기존 Postgres public port |
| `DB_POSTGRESDB_DATABASE` | `railway` | |
| `DB_POSTGRESDB_USER` | `postgres` | |
| `DB_POSTGRESDB_PASSWORD` | (기존 Postgres PGPASSWORD) | |
| `N8N_PORT` | `5678` | n8n 기본 포트 |
| `PORT` | `5678` | Railway 포트 매핑 |
| `WEBHOOK_URL` | `https://n8n-production-81b4.up.railway.app` | 외부 webhook 수신 URL |
| `N8N_ENCRYPTION_KEY` | (자동 생성) | credential 암호화 키 |

n8n은 기존 PostgreSQL에 자체 테이블(`execution_entity`, `workflow_entity`, `credentials_entity` 등)을 자동 생성. 챗봇의 채팅기록/비즈니스 테이블과 같은 DB에 공존.

### n8n Credential 참조 (P4 워크플로우 JSON에서 사용, 현재 비활성)

| 노드 타입 | credential key | credential name | credential ID |
|----------|----------------|-----------------|---------------|
| `n8n-nodes-base.postgres` | `postgres` | `Postgres account` | `j1PLbiwu8dCOVpl5` |
| `n8n-nodes-base.googleSheets` | `googleApi` | `Google Service Account account` | `JtDg23azfbja0yi3` |

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
- 출석부 생성 (`app/chains/attendance.py`): 수강생 탭 + 과목별 탭 + 인쇄용 PDF (NanumGothic)
- 출석 체크 OCR (`app/chains/ocr.py`): Claude Vision, 과목별 탭 B:M 쓰기
- 종강 처리 (`app/chains/graduation.py`): 출석률 집계, 수강기록 append, 회원목록 재집계, 등급 강등
- 회원관리 3탭 구조 (회원목록/회원기록/수강기록), 동적 Drive 폴더 탐색
- 신청서 upsert (기존 행 보존, 새 key만 추가), 입금 시 회원기록 자동 기록
- Action 버튼, AskActionMessage, 세션 상태 머신
- cl.Step 진행 상황 표시 (각 파이프라인 단계별 expandable indicator)
- 입금 대조 결과 즉시 자동 반영 (확인 단계 제거, 숫자 요약 한 줄)
- 회차 불일치 시 자유 텍스트 재입력 루프 (`parse_term_input`)
- 자유 텍스트 → LLM 의도 분류 (`classify_intent_llm`) → 관리자 확인 후 워크플로우 진입
- 워크플로우 중 인터럽트 처리 (`handle_mid_flow_text`): 취소 감지, Q&A 답변 후 상태 유지
- 모든 작업 종료 후 공통 기본 Action 버튼 (`send_default_actions`)
- 신청서 시트: 필터 + 처리상태 드롭다운 자동 설정
- 출석부 시트: 과목별 탭 BasicFilter 자동 설정
- Railway 배포, 단위 테스트 108개 통과
- 커스텀 테마 (Palette C 마을회관): `public/theme.json` + `public/stylesheet.css`
- Noto Sans KR 폰트, 본문 18px, WCAG AA 접근성
- `config.toml`: `cot = "hidden"`, `description` 추가
- **남은 작업**: E2E 기능 테스트, Context Injection 고도화

### Phase 2 — 데이터 파이프라인 ✅ 완료

인프라 셋업 + 통합 신청서 설계 + Sheets SoT 확정.

**2-0. n8n 배포 + PostgreSQL 연결** ✅ 완료
- Railway 템플릿 "n8n (w/ postgres)"로 별도 프로젝트에 배포
- 기존 Postgres public URL로 연결 전환 (yamanote.proxy.rlwy.net:26189)
- n8n 웹 UI 접속 확인, admin 계정 생성 완료
- Credential 등록 완료:
  - PostgreSQL: `Postgres account` (id: `j1PLbiwu8dCOVpl5`)
  - Google SA: `Google Service Account account` (id: `JtDg23azfbja0yi3`, key: `googleApi`)
- P4 워크플로우로 정상 동작 검증 완료

**2-1. PostgreSQL 비즈니스 스키마** ✅ 완료 (Phase 2.5)
- `app/services/db.py`: 7 테이블 (members, member_records, course_records, applications, deposits, attendance, feedbacks)
- Dual-Write 모드: `USE_DB_SOT=true`이면 DB가 SoT, Sheets는 secondary write
- `scripts/init_db_schema.py` + `scripts/migrate_v4.py`: Railway PostgreSQL에 테이블 생성/마이그레이션 완료

**2-2. DB SoT + n8n Sheets 동기화** ✅ 완료
- `payment.py`: 5개 write 함수 DB+n8n 패턴으로 전환 (DB 쓰기 → `trigger_sheets_sync()`, Sheets 직접 쓰기 제거)
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
- `app/services/n8n.py`: fire-and-forget 웹훅 트리거 (applications/members/deposits/graduation)
- n8n 워크플로우: Sync-1 (일일 06:00, 5탭) + Sync-2 (웹훅 즉시) — 설정 완료
- `registration_status` → `processing_status` 전환 완료
- 108개 테스트 통과

### Phase 3 — 기능 확장 + UX 개선

**✅ 완료:**
- 종강 처리 — 출석률 집계, 수강기록 추가, 등급 강등, 회원목록 재집계, 회원기록 기록
- 출석 체크 (OCR) — Claude Vision으로 종이 출석부 디지털화 → 과목별 탭 O/빈칸
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
- FAQ/Context Injection 보강 — 입금대조절차, 처리상태, 등급전환, 시트구조 등 5개 토픽 추가
- 합산 입금 분류 — 12만(정회원비), 13만(가입비+정회원비)

**📋 백로그:**
- 보고서 생성: DB SQL 집계 → PDF (placeholder 버튼 배치 완료)
- 계획서 검토: PDF 파싱 → 오탈자/말투 수정 → 배움숲 멘트 생성 (placeholder 배치 완료)
- 첫 화면 로고+타이틀 PNG 이미지 제작 (`public/logo_light.png` → CSS 워크어라운드 제거)
- Accent 색상(#2B7A6E 틸) 적용 위치 결정 (현재 미사용)

## 코딩 규칙

- 한국어 주석 OK, 변수명/함수명은 영문
- **데이터 읽기/쓰기는 `USE_DB_SOT` 플래그로 결정**. `true`=DB SoT (DB 쓰기 + n8n webhook, Sheets 직접 쓰기 없음), `false`=Sheets SoT (기본). `db.py`의 CRUD 함수 + `n8n.py`의 `trigger_sheets_sync()` 사용.
- LLM 호출은 최소화 — 코드로 처리 가능하면 코드로
- 에러 시 사용자에게 한국어로 안내 메시지 반환
- Docker 사용 안 함 (챗봇). n8n만 Docker 배포. Railway는 Procfile 기반 배포.
- 상세 기획은 `docs/DEV_DOCUMENT.md` 참조
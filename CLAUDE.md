# CLAUDE.md

## 프로젝트 요약

위례인생학교(성인 평생교육, 연 4학기) 관리자용 AI 업무 도우미 챗봇. LangChain + Chainlit + Google Sheets API 기반. 핵심 업무: 입금 대조, 출석부 생성, 출석 체크(OCR), 계획서 검토, 보고서 생성, 질의응답.

**설계 원칙**: AI가 90% 처리 → 관리자가 10% 검증. 코드로 될 건 코드로, LLM은 비정형 데이터 해석에만 사용.

## 기술 스택

- **언어**: Python 3.10+
- **LLM 프레임워크**: LangChain
- **채팅 UI**: Chainlit (WebSocket 기반, Conversation Starter 버튼 지원)
- **LLM**: Claude API (Anthropic) — 한국어 + Vision
- **데이터 저장**: Google Sheets API (DB 없음, Sheets가 저장소)
- **Google 인증**: Service Account + Domain-wide Delegation
- **배포**: Phase 0은 Fly.io, Phase 1~은 Railway (Git push 자동 배포)
- **RAG 없음**: Context Injection (시스템 프롬프트에 비즈니스 컨텍스트 직접 주입)

## 아키텍처 방침

**고정 파이프라인 + LLM은 특정 단계에서만 (Agent 패턴 사용 금지)**

- LangChain Agent나 tool-calling agent 패턴을 사용하지 않는다
- 각 작업(입금 대조, 출석부 생성 등)은 실행 순서가 고정된 Python 함수 파이프라인으로 구현한다
- LLM은 비정형 텍스트 해석이 필요한 특정 단계에서만 호출한다 (예: 입금자명 파싱)
- LangChain은 LLM 호출 래퍼(ChatAnthropic)로만 사용, 오케스트레이션 프레임워크로는 사용하지 않는다
- 의도 분류는 Conversation Starter 버튼의 고정 메시지로 판별 (LLM 기반 intent classifier 불필요)

**이유**: 대상 사용자가 55세 이상 비개발자 관리자 2~4명. 대화형 AI에 익숙하지 않음. 예측 가능하고 가이드된 UX가 필수. 자유도가 높으면 오히려 혼란.

```python
# 라우팅 패턴 — 버튼 메시지로 단순 분기
if message.content == "입금 대조를 시작합니다.":
    await payment_flow(message)
elif message.content == "출석부를 생성합니다.":
    await attendance_flow(message)
else:
    await qa_flow(message)  # 자유 Q&A만 LLM 자유 사용
```

## Chainlit UX 가이드

대상 사용자(55세+, 비개발자)를 위한 Chainlit 기능 활용 방침.

### Step — 중간 진행 상황 공유 (Phase 1)
복잡한 작업에서 각 단계를 관리자에게 시각적으로 보여줌. "지금 뭘 하고 있는지" 피드백이 신뢰 구축에 핵심.
```python
@cl.step(name="📊 데이터 읽기")
async def read_data():
    ...  # 관리자에게 "수강생 시트를 읽고 있어요..." 표시

@cl.step(name="🔍 입금 매칭 중")
async def match_payments():
    ...  # "85건 중 78건 매칭 완료..." 중간 결과 표시
```

### Action — 다음 작업 추천 버튼 (Phase 1)
작업 완료 후 다음 가능한 작업을 버튼으로 제시. 자유 텍스트 입력 없이 클릭만으로 업무 진행.
```python
actions = [
    cl.Action(name="create_attendance", label="📋 출석부 생성하기"),
    cl.Action(name="redo_payment", label="🔄 입금 대조 다시하기"),
    cl.Action(name="free_question", label="❓ 다른 질문하기"),
]
await cl.Message(content="입금 대조가 완료되었습니다.", actions=actions).send()
```

### AskActionMessage — 사용자 확인 대기 (Phase 1)
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

### Authentication — Google OAuth (Phase 1 초반)
관리자만 접근 가능하도록 Google Workspace 도메인 제한.
```python
@cl.oauth_callback
def oauth_callback(provider_id, token, raw_user_data, default_user):
    if raw_user_data.get("hd") != "wiryeschoolcomunity.com":
        return None  # Workspace 외 계정 차단
    return default_user
```
환경변수: `OAUTH_GOOGLE_CLIENT_ID`, `OAUTH_GOOGLE_CLIENT_SECRET`, `CHAINLIT_AUTH_SECRET`

### Data Persistence — 채팅 기록 (Phase 2)
세션 간 대화 기록 유지. LiteralAI 또는 커스텀 데이터 레이어 필요. 당장은 세션 내 유지로 충분.

### Theme/CSS — 접근성 (Phase 2)
- 폰트 크기: 기본보다 크게 (18~20px)
- 색상: 눈에 편한 따뜻한 톤
- 버튼: 크게, 터치 타겟 넓게
- UI 전체 한국어화

## 프로젝트 구조

```
wiryeschoolcommunity/
├── CLAUDE.md                    # 이 파일
├── chainlit.md                  # Chainlit 웰컴 화면 (커스터마이징 필요 — 백로그)
├── .chainlit/
│   └── config.toml              # Chainlit UI 설정 (이름, 테마 등)
├── docs/
│   ├── DEV_DOCUMENT.md          # 상세 기획서 (비즈니스 컨텍스트, 데이터 구조, 입금 패턴 등)
│   └── BUSINESS_CONTEXT.md      # Context Injection 소스 텍스트
├── app/
│   ├── main.py                  # Chainlit 엔트리포인트 + 세션 상태 라우터 + 입금 대조 wizard flow
│   ├── config.py                # 환경 변수, 상수, Google IDs, COURSE_KEYWORDS
│   ├── context/
│   │   ├── business.py          # 정적 비즈니스 컨텍스트 dict + 시스템 프롬프트
│   │   └── semester.py          # 현재 학기 자동 판별
│   ├── chains/
│   │   ├── qa.py                # 질의 응답 체인
│   │   └── payment.py           # 입금 대조 파이프라인 (Drive 로드, 코드 매칭, LLM 폴백, 시트 기록)
│   ├── services/
│   │   ├── google_auth.py       # Google API 인증 (SA 파일 + JSON 환경변수 이중 지원)
│   │   ├── google_drive.py      # Drive API 래퍼
│   │   ├── google_sheets.py     # Sheets API 래퍼
│   │   └── excel.py             # Excel 파싱 (입금내역 .xls/.xlsx + 수강생 명단 .xlsx)
│   └── utils/
│       ├── __init__.py
│       └── matching.py          # 이름/강좌 추출, 규칙 기반 입금 매칭
├── scripts/
│   ├── populate_members.py      # 회원관리 시트 초기 데이터 생성 (출석부 XLSX → 시트)
│   └── populate_students.py     # 수강생 시트 초기 데이터 생성 (출석부 XLSX → 시트)
├── Dockerfile                   # Docker 빌드 (Fly.io 배포용)
├── Procfile                     # PaaS 실행 명령
├── fly.toml                     # Fly.io 배포 설정 (nrt 리전)
├── requirements.txt
├── .env.example                 # 환경 변수 템플릿
└── .gitignore
```

**Phase 1에서 추가 예정**:
- `app/chains/attendance.py`: 출석부 생성 파이프라인

## 환경 변수

```
ANTHROPIC_API_KEY=              # Claude API 키
GOOGLE_SA_KEY_PATH=sa-key.json  # Service Account JSON 키 파일 경로
GOOGLE_DELEGATED_USER=wirye@wiryeschoolcomunity.com  # Delegation 대상 (오타 아님, 실제 도메인)
```

## Google API 인증 패턴

```python
from google.oauth2 import service_account
from googleapiclient.discovery import build

credentials = service_account.Credentials.from_service_account_file(
    os.environ['GOOGLE_SA_KEY_PATH'],
    scopes=[
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/spreadsheets'
    ],
    subject=os.environ['GOOGLE_DELEGATED_USER']  # Domain-wide Delegation
)

sheets_service = build('sheets', 'v4', credentials=credentials)
drive_service = build('drive', 'v3', credentials=credentials)
```

**Sheets API 주요 조작**:
```python
# 특정 시트 범위 읽기
sheets_service.spreadsheets().values().get(
    spreadsheetId=SHEET_ID, range='마스터!A1:N100'
).execute()

# 특정 시트에 쓰기
sheets_service.spreadsheets().values().update(
    spreadsheetId=SHEET_ID, range='마스터!J2:M50',
    valueInputOption='USER_ENTERED', body={'values': data}
).execute()

# 새 시트 탭 추가
sheets_service.spreadsheets().batchUpdate(
    spreadsheetId=SHEET_ID,
    body={'requests': [{'addSheet': {'properties': {'title': '강좌명'}}}]}
).execute()
```

## 데이터 구조

### 3개 테이블 + 출석부

```
[회원관리] ←──집계── [수강기록]       ← 둘 다 루트, 영속
     │                    ▲
     │ 이름ID             │ 학기 종료 시 추가
     ▼                    │
[수강생]              [출석부]          ← 둘 다 학기 폴더
```

### Google Drive 디렉토리

```
위례인생학교 자료실/
├── 회원관리.xlsx                       ← 루트, 영속
├── 수강기록.xlsx                       ← 루트, 영속
└── 학사운영(연도별)/
    └── {연도}/
        └── {YYYY}_{N}학기/
            ├── 수강생.xlsx              ← 학기별
            ├── 출석부/{강좌명}_출석부.xlsx
            ├── 입금내역/{YYYY}_{MM}_{DD}_입금내역.xlsx
            └── 강의계획서/초안/ , 검토완료/
```

### 회원관리 (루트, 영속) — 전체 회원 마스터

| 이름ID | 이름 | 성별 | 전화번호 | 주소 | 나이 | 등급 | 가입날짜 | 시작학기 | 만료학기 | 수강count | 출석률(누적) | 마지막수강학기 | active |
|--------|------|------|---------|------|------|------|---------|---------|---------|----------|------------|-------------|--------|

- **PK**: 이름ID
- **등급**: 회원(영속, 수강료 필요) / 준회원(학기 중, 수강료 납부 완료) / 정회원(수강료 면제, 만료학기까지)
- **만료학기**: 정회원만 해당 (후년 1학기). 회원/준회원은 없음.
- **active**: 매 1학기(1월) 일괄 업데이트. 정회원: 현재학기 > 만료학기 → 등급="회원". 회원: 항상 Y.
- **등급 자동 전환**:
  - 학기 시작: 정회원 만료 → "회원"
  - 입금 대조: 수강료 입금 확인 + 등급 "회원" → "준회원" 승급
  - 종강: 준회원 → "회원" 강등
- **특수**: 강사=자동 정회원(기준 추후 확정), 사무처 직원=입사시 정회원(퇴사 시 해당 학기까지)
- **수강count/출석률/마지막수강학기**: 수강기록에서 집계

### 수강기록 (루트, 영속) — 전체 수강 이력

| 이름ID | 학기 | 과목명 | 출석률 |
|--------|------|--------|--------|

학기 종료 시: 출석부 → 출석률 확정 → 수강기록에 행 추가 → 회원관리 갱신

### 수강생 (학기 폴더) — 해당 학기 수강 신청 + 입금

| 이름ID | 과목명 | 입금시간 | 입금자명(적요) | 비고 | 입금현황 | 등록상태 |
|--------|--------|---------|-------------|------|---------|---------|

- **PK**: 이름ID + 과목명
- **입금시간/입금자명/입금현황**: AI 입금 대조 시 자동 채움
- **입금현황**: ✅정상 / 🔶확인필요 / ❌미입금 / 💎면제 (정회원)
- **등록상태**: 관리자 확인 후 수동. 정회원은 자동으로 "정상등록"

### 출석부 (학기 폴더, 강좌별 파일)

| ID | 이름 | 1회차~12회차 | 출석률 |
|----|------|------------|--------|

- 12회차 일괄 생성, 출석률 = 출석수/회차수×100 (수식)
- 등록상태 "정상등록"인 수강자만 포함

### 입금내역 (은행 엑셀) — 파싱 주의사항

- **1~6행**: 계좌 메타정보 (예금주, 계좌번호, 조회기간) → 스킵
- **7행**: 헤더 (`거래일시, 적요, (비고), 의뢰인/수취인, 입금, 출금, ...`)
- **마지막 행**: 합계 → 제외
- **비고 컬럼** (3번째, 헤더 없음): 정규화된 강좌명 포함 → 강좌 매칭에 핵심
- **의뢰인/수취인**: 실제 송금자 이름 → 이름 매칭에 핵심
- 카카오페이/토스 경유 시 의뢰인이 `(주)카카오페이`, `(주)비바리퍼블리카` → 적요에서 이름 추출 필요

## 입금 매칭 로직 (핵심)

2단계 구조: 코드 매칭(80~90%) → LLM 예외 처리(10~20%)

**정회원 선처리**: 회원관리 등급 "정회원" → 입금현황 = "💎면제", 등록상태 = "정상등록" (매칭 대상 제외)

**코드 매칭 순서**:
1. 비수강료 필터링: 금액 < 1만원(예금이자 등) 스킵, "취소됨"/"대기" 키워드 감지
2. 이름 매칭: 의뢰인 컬럼 → 수강생 시트의 이름 (정확 일치). 카카오페이/토스면 적요에서 추출
3. 강좌 매칭: 비고 컬럼 → 수강생 시트의 과목명 (문자열 포함/유사도)
4. 금액 분류: 1만(가입비), 2만(수강료), 3만(수강료+가입비), 4만+(다과목 합산), 10만+(연회원)
5. 동명이인: 이름 매칭 2명+ → 강좌명으로 2차 구분, 안 되면 🔶

**LLM 처리**: 코드로 매칭 실패한 건만. 비정형 적요 텍스트 해석, 후보 목록과 비교.

**입금 상태 코드**: ✅정상 / 🔶확인필요 / ⚠️이름불일치 / ❌미입금 / 🔄중복 / 💎면제(정회원)

**입금자명 패턴** (형식 통일 안 됨):
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
SEMESTERS = {1: "1~3월", 2: "4~6월", 3: "7~9월", 4: "10~12월"}
# 비용은 변동 가능 → 설정 파일 또는 시트에서 읽어오는 구조 권장
```

## Google Sheets/Drive IDs

| Resource | Type | ID | 시트 탭명 |
|----------|------|----|----------|
| 수강생 | Spreadsheet | `16vRPnHWX7AEd66xsxVSYAZrKNRtTdz_5DbjSYDo5RBY` | `수강생` |
| 회원관리 | Spreadsheet | `193r34mtLHd0-oX7MKJOWq1Ane9iBfbBZB5yYf78R3Bo` | `회원관리` |
| 수강기록 | Spreadsheet | `1cKolq6Mr-5u65nQDeMq8z4DsFWpHTLVthkAvyt4Rb6s` | — |
| 출석부 folder | Drive folder | `1i-sixwrwPU_XxYhOwhDvaIqfvCxICWB8` | — |
| Root folder | Drive folder | `1N24ROJ12BEDRrSmtezXdV70Pi3Np7sRm` | — |

**주의**: 시트 탭명이 "시트1"이 아님. API 호출 시 정확한 탭명 사용 필요 (예: `"수강생!A1:G500"`).

## 현재 상태

- **Phase**: Phase 1 진행 중 (입금 대조 구현 완료)
- **배포 URL**: https://wirye-school-assistant.fly.dev/
- **Phase 0 완료**:
  - 기획서(DEV_DOCUMENT v4.1), 데이터 구조 설계, 입금 패턴 분석
  - Python 프로젝트 초기 셋업, Chainlit 기본 앱 (채팅 UI + Starter 버튼 + Q&A)
  - Google API 인증 — Domain-wide Delegation, Sheets/Drive 연동 확인
  - Fly.io 배포 완료 (nrt 리전, shared-cpu-1x, 512MB, auto_stop)
- **Phase 1 완료**:
  - 입금 대조 파이프라인 (`app/chains/payment.py`): 파일 업로드 → 파싱 → 코드 매칭(~85%) → LLM 폴백 → 시트 기록
  - Excel 파싱 (`app/services/excel.py`): .xls(xlrd) + .xlsx(openpyxl) 입금내역/수강생 명단
  - 규칙 기반 매칭 (`app/utils/matching.py`): 이름/강좌 추출, 카카오페이/토스, 동명이인, 특수 유형
  - main.py 세션 상태 라우터: idle → awaiting_payment_file → awaiting_payment_confirm → idle
  - 회원관리 시트 초기 데이터 (138명, 출석부 XLSX에서 추출, `scripts/populate_members.py`)
  - 수강생 시트 초기 데이터 (225건, 15개 강좌, `scripts/populate_students.py`)
- **Fly.io 시크릿**: ANTHROPIC_API_KEY, GOOGLE_SA_KEY_JSON, GOOGLE_DELEGATED_USER
- **PaaS 인증 방식**: SA 키를 GOOGLE_SA_KEY_JSON 환경변수로 전달 (파일 마운트 불가), google_auth.py에서 from_service_account_info() 사용
- **다음 작업 (Phase 1 잔여)**:
  1. 출석부 생성 구현
  2. 질의 응답 — Context Injection 고도화
  3. Railway 이관 (Git push 자동 배포)
- **백로그 (Phase 1)**:
  - Google OAuth 인증 (Workspace 도메인 제한)
  - cl.Step으로 작업 중간 진행 상황 공유
  - cl.Action / AskActionMessage로 작업 간 연결
- **백로그 (Phase 2)**:
  - Data Persistence (채팅 기록 유지)
  - Theme/CSS 커스터마이징 (폰트 크기, 색상, 접근성)
  - Chainlit UI 커스터마이징 (chainlit.md 웰컴 화면, 어시스턴트 이름 표시)

## 코딩 규칙

- 한국어 주석 OK, 변수명/함수명은 영문
- Google Sheets 데이터는 항상 Sheets API로 직접 접근 (파일 다운로드 방식 사용 안 함)
- LLM 호출은 최소화 — 코드로 처리 가능하면 코드로
- 에러 시 사용자에게 한국어로 안내 메시지 반환
- 상세 기획은 `docs/DEV_DOCUMENT.md` 참조

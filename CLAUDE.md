# CLAUDE.md

## 프로젝트 요약

위례인생학교(성인 평생교육, 연 4회차) 관리자용 AI 업무 도우미 챗봇. LangChain + Chainlit + Google Sheets API 기반. 핵심 업무: 입금 대조, 출석부 생성, 출석 체크(OCR), 계획서 검토, 보고서 생성, 질의응답.

**설계 원칙**: AI가 90% 처리 → 관리자가 10% 검증. 코드로 될 건 코드로, LLM은 비정형 데이터 해석에만 사용.

## 기술 스택

- **언어**: Python 3.10+
- **LLM 프레임워크**: LangChain (LLM 호출 래퍼로만 사용)
- **채팅 UI**: Chainlit (WebSocket 기반, Conversation Starter 버튼 지원)
- **LLM**: Claude API (Anthropic) — 한국어 + Vision
- **데이터 저장**: Google Sheets API (DB 없음, Sheets가 저장소)
- **Google 인증**: Service Account + Domain-wide Delegation
- **배포**: Railway (Git push 자동 배포)
- **RAG 없음**: Context Injection (시스템 프롬프트에 비즈니스 컨텍스트 직접 주입)

## 아키텍처 방침

**고정 파이프라인 + LLM은 특정 단계에서만 (Agent 패턴 사용 금지)**

- LangChain Agent나 tool-calling agent 패턴을 사용하지 않는다
- 각 작업(입금 대조, 출석부 생성 등)은 실행 순서가 고정된 Python 함수 파이프라인으로 구현한다
- LLM은 비정형 텍스트 해석이 필요한 특정 단계에서만 호출한다 (예: 입금자명 파싱)
- LangChain은 LLM 호출 래퍼(ChatAnthropic)로만 사용, 오케스트레이션 프레임워크로는 사용하지 않는다
- 의도 분류는 Conversation Starter 버튼의 고정 메시지로 판별 (LLM 기반 intent classifier 불필요)
- `.agents/skills/`에 LangChain Skills(langchain-ai/langchain-skills)이 설치되어 있음. Claude Code가 LangChain 관련 코드 작성 시 참조하는 코딩 가이드이며, 런타임 동작에는 영향 없음.

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

## 용어 정의

- **회차**: 연 4회 운영 단위. 코드 식별자: `2026-1` (연도-번호)
- **회차명**: 표시용 이름. `2026-1 겨울학기` (연도-번호 계절학기)
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
작업 완료 후 다음 가능한 작업을 버튼으로 제시. 자유 텍스트 입력 없이 클릭만으로 업무 진행.
```python
actions = [
    cl.Action(name="create_attendance", label="📋 출석부 생성하기"),
    cl.Action(name="redo_payment", label="🔄 입금 대조 다시하기"),
    cl.Action(name="free_question", label="❓ 다른 질문하기"),
]
await cl.Message(content="입금 대조가 완료되었습니다.", actions=actions).send()
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

## 프로젝트 구조

```
wiryeschoolcommunity/
├── CLAUDE.md                    # 이 파일
├── chainlit.md                  # Chainlit 웰컴 화면
├── .chainlit/
│   └── config.toml              # Chainlit UI 설정 (이름, 테마 등)
├── .agents/
│   └── skills/                  # LangChain Skills (Claude Code 코딩 가이드)
├── docs/
│   ├── DEV_DOCUMENT.md          # 상세 기획서 (비즈니스 컨텍스트, 데이터 구조, 입금 패턴 등)
│   └── BUSINESS_CONTEXT.md      # Context Injection 소스 텍스트
├── app/
│   ├── main.py                  # Chainlit 엔트리포인트 + 세션 상태 라우터 + 입금 대조 wizard flow
│   ├── config.py                # 환경 변수, 상수, 영속 Google IDs, COURSE_KEYWORDS
│   ├── context/
│   │   ├── business.py          # 정적 비즈니스 컨텍스트 dict + 시스템 프롬프트
│   │   └── term.py              # 현재 회차 자동 판별
│   ├── chains/
│   │   ├── qa.py                # 질의 응답 체인
│   │   ├── payment.py           # 입금 대조 파이프라인 (신청자 로드, 코드 매칭, LLM 폴백, 시트 기록)
│   │   └── attendance.py        # 출석부 생성 파이프라인
│   ├── services/
│   │   ├── google_auth.py       # Google API 인증 (SA 파일 + JSON 환경변수 이중 지원)
│   │   ├── google_drive.py      # Drive API 래퍼 + 동적 폴더 탐색 (find_term_folder 등)
│   │   ├── google_sheets.py     # Sheets API 래퍼
│   │   └── excel.py             # Excel 파싱 (입금내역 .xls/.xlsx + 신청자 목록 HTML .xls)
│   └── utils/
│       ├── __init__.py
│       └── matching.py          # 이름/강좌 추출, 규칙 기반 입금 매칭
├── scripts/
│   ├── populate_members.py      # 회원관리 시트 초기 데이터 생성
│   └── populate_students.py     # 수강생 시트 초기 데이터 생성
├── Procfile                     # PaaS 실행 명령
├── requirements.txt
├── .env.example                 # 환경 변수 템플릿
└── .gitignore
```

## 환경 변수

```
ANTHROPIC_API_KEY=              # Claude API 키
GOOGLE_SA_KEY_PATH=sa-key.json  # Service Account JSON 키 파일 경로 (로컬)
GOOGLE_SA_KEY_JSON=             # Service Account JSON 문자열 (PaaS 배포용)
GOOGLE_DELEGATED_USER=wirye@wiryeschoolcomunity.com  # Delegation 대상 (오타 아님, 실제 도메인)
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

## 데이터 구조

### 3개 테이블 + 출석부

```
[회원관리] ←──집계── [수강기록]       ← 둘 다 루트, 영속
     │                    ▲
     │ 이름ID             │ 종강 시 추가
     ▼                    │
[수강생]              [출석부]          ← 둘 다 회차 폴더
```

### Google Drive 디렉토리

```
위례인생학교 자료실/  (Shared Drive root: 0AANInBeWsB7dUk9PVA)
├── 회원관리 (Google Sheets)                  ← 루트, 영속
├── 수강기록 (Google Sheets)                  ← 루트, 영속
└── 02_학사운영/    (1WuqNFt-g5qhnY1nMk0a8dsowZHKQVRMm)
    └── {연도}/
        └── {회차}_{계절}학기/                ← 예: "2026-1_겨울학기"
            ├── 수강생/                        ← 서브폴더 (NEW)
            │   ├── LEARNING_APPLY*.xls       ← 배움숲 신청자 목록 (SoT)
            │   └── 수강생 (Google Sheets)     ← Agent가 신청자 목록 기반으로 생성
            ├── 출석부/
            │   └── 출석부 (Google Sheets)     ← 과목별 시트탭, Agent가 생성
            │   └── {회차}_{과목명}_출석부.pdf  ← A4 프린트용, Agent가 생성
            ├── 강의계획서/
            │   ├── 초안/
            │   └── 검토완료/
            ├── 입금/
            └── 홍보·안내/
```

### 회원관리 (루트, 영속) — 전체 회원 마스터

| 이름ID | 이름 | 성별 | 전화번호 | 주소 | 나이 | 등급 | 가입날짜 | 수강count | 출석률(누적) | 마지막수강학기 |
|--------|------|------|---------|------|------|------|---------|----------|------------|-------------|

- **PK**: 이름ID
- **등급**: 회원(영속, 수강료 필요) / 준회원(회차 중, 수강료 납부 완료) / 정회원(수강료 면제, 후년 1학기까지)
- **등급 자동 전환**:
  - 입금 대조 완료 후: 수강료 입금 확인 + 등급 "회원" → "준회원" 승급
  - 종강: 준회원 → "회원" 강등
  - 1학기(겨울) 종강 시: 정회원 → "회원" 일괄 강등 (강사/사무처 직원 예외)
- **특수**: 강사=자동 정회원(기준 추후 확정), 사무처 직원=입사시 정회원(퇴사 시 해당 회차까지)
- **수강count/출석률/마지막수강학기**: 수강기록에서 집계

### 수강기록 (루트, 영속) — 전체 수강 이력

| 이름ID | 회차 | 과목명 | 출석률 |
|--------|------|--------|--------|

종강 시: 출석부 → 출석률 확정 → 수강기록에 행 추가 → 회원관리 갱신

### 수강생 (회차 폴더) — 해당 회차 수강 신청 + 입금

| 이름ID | 과목명 | 입금시간 | 입금자명(적요) | 비고 | 입금현황 | 등록상태 |
|--------|--------|---------|-------------|------|---------|---------|

- **PK**: 이름ID + 과목명
- **생성 원본**: 배움숲 신청자 목록 (`LEARNING_APPLY*.xls`)
- **입금시간/입금자명/입금현황**: AI 입금 대조 시 자동 채움
- **입금현황**: ✅정상 / 🔶확인필요 / ⚠️이름불일치 / ❌미입금 / 🔄중복 / 💎면제(정회원)
- **등록상태**: 관리자 확인 후 수동. 정회원은 자동으로 "정상등록"

### 출석부 (회차/출석부/ 폴더, 1파일 다중시트)

Google Sheets 파일 1개, 과목별 시트탭.

| ID | 이름 | 1회차~12회차 | 출석률 |
|----|------|------------|--------|

- 12회차 일괄 생성, 출석률 = 출석수/회차수×100 (수식)
- 등록상태 "정상등록"인 수강자만 포함
- 과목별 PDF도 생성 (A4 프린트용)

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

1. **관리자**: 배움숲에서 신청자 목록 엑셀 다운로드 → 회차 폴더에 업로드
2. **관리자**: 챗봇에서 "💰 입금 대조" Starter 버튼 클릭
3. **Agent**: 현재 날짜 기반으로 회차 추측 → "2026-1 겨울학기 입금 대조를 시작할까요?" → 관리자가 확정/수정
4. **Agent**: Drive에서 해당 회차 폴더의 `LEARNING_APPLY*.xls` 파일 찾기 → 파싱 → 회차 폴더에 '수강생' Google Sheets 생성
5. **Agent**: "2026-1 겨울학기 수강 신청자(총 N명)를 확인했습니다. 입금 내역을 업로드해주세요"
6. **관리자**: 입금 내역 엑셀을 챗봇에 직접 업로드
7. **Agent**: 회원관리 + 수강생 + 입금내역을 바탕으로 매칭 (코드 80~90% → LLM 10~20%) → 수강생 시트 업데이트
8. **Agent**: 결과 요약 + 시트 링크 제공 + "배움숲에서 결제완료 회원의 등록상태를 변경해주세요"
9. **Agent**: Action 버튼 제공 — 📋 출석부 생성, 🔄 입금대조 다시하기, ❓ 다른 질문하기
10. **관리자**: 수동 확인 후 '출석부 생성' 클릭 (또는 새 세션에서 Starter 버튼으로 시작 가능)
11. **Agent**: 회차/출석부/ 폴더에 '출석부' Google Sheets 생성 (과목별 시트탭) + 과목별 PDF 생성
12. **Agent**: 회원관리 시트 업데이트 — 수강등록 완료된 회원→준회원 승격, 마지막수강학기 수정, 수강count +1
13. **Agent**: 수강기록 시트에 행 추가

### 입금 매칭 로직

2단계 구조: 코드 매칭(80~90%) → LLM 예외 처리(10~20%)

**정회원 선처리**: 회원관리 등급 "정회원" → 입금현황 = "💎면제", 등록상태 = "정상등록" (매칭 대상 제외)

**코드 매칭 순서**:
1. 비수강료 필터링: 금액 < 1만원(예금이자 등) 스킵, "취소됨"/"대기" 키워드 감지
2. 이름 매칭: 의뢰인 컬럼 → 수강생 시트의 이름 (정확 일치). 카카오페이/토스면 적요에서 추출
3. 강좌 매칭: 적요 컬럼에서 강좌 키워드 추출 → 수강생 시트의 과목명과 대조
4. 금액 분류: 1만(가입비), 2만(수강료), 3만(수강료+가입비), 4만+(다과목 합산), 12만(정회원)
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

| Resource | 상수명 | Type | ID | 시트 탭명 |
|----------|--------|------|----|----------|
| 회원관리 | `MEMBERS_SHEET_ID` | Spreadsheet | `193r34mtLHd0-oX7MKJOWq1Ane9iBfbBZB5yYf78R3Bo` | `회원관리` |
| 수강기록 | `RECORDS_SHEET_ID` | Spreadsheet | `1cKolq6Mr-5u65nQDeMq8z4DsFWpHTLVthkAvyt4Rb6s` | `수강기록` |
| Root folder | `ROOT_FOLDER_ID` | Shared Drive root | `0AANInBeWsB7dUk9PVA` | — |
| 학사운영 folder | `OPERATIONS_FOLDER_ID` | Drive folder | `1WuqNFt-g5qhnY1nMk0a8dsowZHKQVRMm` | — |

### 회차별 리소스 (런타임에 동적 탐색 — config.py에 없음)

| Resource | 탐색 방법 | 참고 ID (2026-1) |
|----------|----------|-----------------|
| 회차 폴더 | `find_term_folder(term_id)` → 02_학사운영 → 연도 → term_id* | `1PD-10tt7o9U8suCjwJmgZt0FCvH6BhtP` |
| 수강생 시트 | `create_students_sheet()` / `find_spreadsheet_by_name(term_folder, "수강생")` | `16vRPnHWX7AEd66xsxVSYAZrKNRtTdz_5DbjSYDo5RBY` |
| 출석부 폴더 | `find_or_create_folder(term_folder, "출석부")` | `1i-sixwrwPU_XxYhOwhDvaIqfvCxICWB8` |

**주의**: 시트 탭명이 "시트1"이 아님. API 호출 시 정확한 탭명 사용 필요 (예: `"수강생!A1:G500"`).

**주의**: Google Sheets/Docs 내용 읽기·쓰기는 `google-docs` MCP(`readSpreadsheet`, `getSpreadsheetInfo`, `readDocument` 등)를 사용해야 한다. `google_drive_search`는 파일/폴더 이름·메타데이터 탐색 전용이며 파일 내용에는 접근 불가. ID를 아는 경우 `google-docs` MCP로 직접 접근할 것.

## 강좌명 매핑

배움숲 신청자 목록의 강좌명과 Drive/은행 적요에서 사용되는 약칭이 다를 수 있음.

배움숲 기준 강좌명 (2026-1 겨울학기):
경제뉴스로 배우는 경제해설(기초), 경제뉴스로 배우는 경제해설(심화), 금융과 경제, 기초탄탄 오카리나, 나, 마음챙김 명상, 다시 시작하는 All In One 영어, 라이프코칭상담, 미술관투어, 사진촬영기초(스마트폰 활용), 생활교양법률, 스마트폰으로 배우는 여행영어, 심리상담교실(TA), 어반스케치, 요들송배우기, 우쿨렐레 중급, 책, 가끔은 낭독

## 현재 상태

- **Phase**: Phase 1 구현 완료 + Railway 배포 완료 (E2E 기능 테스트 필요)
- **Phase 0 완료**:
  - 기획서(DEV_DOCUMENT), 데이터 구조 설계, 입금 패턴 분석
  - Python 프로젝트 초기 셋업, Chainlit 기본 앱 (채팅 UI + Starter 버튼 + Q&A)
  - Google API 인증 — Domain-wide Delegation, Sheets/Drive 연동 확인
- **Phase 1 구현 완료**:
  - 입금 대조 파이프라인 전면 재설계 완료 — 신청자 목록 SoT, 비고 컬럼 제거, 적요+의뢰인 기반 매칭
  - Excel 파싱 (`app/services/excel.py`): 입금내역 + 신청자 목록(HTML .xls, BeautifulSoup) 파서 구현
  - 규칙 기반 매칭 (`app/utils/matching.py`): 적요+의뢰인만 사용, 6가지 상태 코드
  - 출석부 생성 (`app/chains/attendance.py`): 과목별 시트탭, 출석률 수식 자동 생성
  - 회원관리 업데이트: 회원→준회원 승격, 수강count+1, 마지막수강학기 갱신
  - 수강기록 시트 자동 추가
  - 동적 Drive 폴더 탐색: `find_term_folder()` — 회차별 ID를 하드코딩하지 않음
  - 수강생 시트 동적 생성: 회차 폴더에 "수강생" Google Sheets 생성/갱신
  - 출석부 폴더 동적 생성: `find_or_create_folder(term_folder, "출석부")`
  - Action 버튼으로 작업 간 연결 (출석부 생성, 입금대조 다시하기, 다른 질문)
  - AskActionMessage로 회차 확인 대기
  - 세션 상태 머신: idle → awaiting_term_confirm → awaiting_payment_file → awaiting_payment_confirm → idle
- **Railway 배포 완료**:
  - `nixpacks.toml` 추가 (Python 3.10 버전 고정)
  - `cl.Action`의 `value=` → `payload={"value": ...}` 마이그레이션 (Chainlit 2.x API 변경)
  - Railway 환경 변수: `ANTHROPIC_API_KEY`, `GOOGLE_SA_KEY_JSON`, `GOOGLE_DELEGATED_USER`
  - **주의**: Railway Variables에서 반드시 `GOOGLE_SA_KEY_JSON`을 사용할 것 (`GOOGLE_SA_KEY_PATH`에 JSON을 넣으면 "File name too long" 에러 발생)
- **단위 테스트 추가** (`tests/`):
  - `test_matching.py` (32개), `test_excel.py` (12개), `test_term.py` (6개) — 총 50개 통과
  - 실행: `source .venv/bin/activate && pytest tests/ -v`
  - 테스트 의존성: `requirements-dev.txt` (pytest, pytest-asyncio)
- **PaaS 인증 방식**: SA 키를 GOOGLE_SA_KEY_JSON 환경변수로 전달 (파일 마운트 불가), google_auth.py에서 from_service_account_info() 사용
- **다음 작업**:
  1. E2E 기능 테스트 (실제 신청자 목록 + 입금내역으로 전체 플로우 검증)
  2. cl.Step으로 작업 중간 진행 상황 공유 (현재는 msg.update()로 대체)
  3. 질의 응답 — Context Injection 고도화 (docs/BUSINESS_CONTEXT.md 활용)
- **백로그 (Phase 1)**:
  - Google OAuth 인증 (Workspace 도메인 제한)
  - 과목별 출석부 PDF 생성 (A4 프린트용)
- **백로그 (Phase 2)**:
  - 종강 기능: 출석률 계산, 수강기록 추가, 회원관리 갱신, 준회원→회원 강등, 1회차(겨울) 종강 시 정회원→회원 강등(강사/사무처 예외), 보고서 작성
  - 출석 체크 (OCR): Claude Vision으로 종이 출석부 디지털화
  - 계획서 검토: PDF 파싱 → 오탈자/말투 수정 → 배움숲 멘트 생성
  - Data Persistence (채팅 기록 유지)
  - Theme/CSS 커스터마이징 (폰트 크기, 색상, 접근성)
  - Chainlit UI 커스터마이징 (chainlit.md 웰컴 화면, 어시스턴트 이름 표시)

## 코딩 규칙

- 한국어 주석 OK, 변수명/함수명은 영문
- Google Sheets 데이터는 항상 Sheets API로 직접 접근 (파일 다운로드 방식 사용 안 함)
- LLM 호출은 최소화 — 코드로 처리 가능하면 코드로
- 에러 시 사용자에게 한국어로 안내 메시지 반환
- Docker 사용 안 함. Railway는 Procfile 기반 배포.
- 상세 기획은 `docs/DEV_DOCUMENT.md` 참조

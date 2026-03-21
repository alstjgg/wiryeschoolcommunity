# CLAUDE.md

## 프로젝트 요약

위례인생학교(성인 평생교육, 연 4회차) 관리자용 AI 업무 도우미 챗봇. LangChain + Chainlit + Google Sheets API 기반. 핵심 업무: 입금 대조, 출석부 생성, 출석 체크(OCR), 계획서 검토, 보고서 생성, 질의응답.

**설계 원칙**: AI가 90% 처리 → 관리자가 10% 검증. 코드로 될 건 코드로, LLM은 비정형 데이터 해석에만 사용.

## 기술 스택

- **언어**: Python 3.12 (`.python-version`으로 고정)
- **LLM 프레임워크**: LangChain (LLM 호출 래퍼로만 사용)
- **채팅 UI**: Chainlit (WebSocket 기반, Conversation Starter 버튼 지원)
- **LLM**: Claude API (Anthropic) — 한국어 + Vision
- **데이터 SoT**: Google Sheets (비즈니스 데이터) + Google Drive (파일 저장)
- **데이터베이스**: PostgreSQL (Railway) — 채팅 기록(chat_data_layer.py) 전용
- **배치 파이프라인**: n8n (Railway, 당장 비활성 — P4만 유지, 추후 제거 검토)
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
│   ├── BUSINESS_CONTEXT.md      # Context Injection 소스 텍스트
│   └── PLANNED_DRIVE_STRUCTURE.md  # Google Drive 확정 구조 + 폴더 ID 참조
├── n8n/                         # n8n 워크플로우 JSON (n8n UI에서 import 용)
│   └── P4_daily_sync.json       # DB → Sheets 일일 동기화 (P1/P2는 챗봇 코드로 이동)
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
│   │   ├── google_sheets.py     # Sheets API 래퍼 (SoT 읽기/쓰기)
│   │   ├── excel.py             # Excel 파싱 (입금내역 .xls/.xlsx + 신청자 목록 HTML .xls)
│   │   ├── signup_loader.py     # Drive에서 신규가입/정회원가입 신청서 로드 → 파싱 결과 반환
│   │   └── chat_data_layer.py   # Chainlit 채팅 기록 PostgreSQL 영속성 (BaseDataLayer 구현)
│   └── utils/
│       ├── __init__.py
│       └── matching.py          # 이름/강좌 추출, 규칙 기반 입금 매칭
├── scripts/
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

- **Google Sheets가 SoT** (Single Source of Truth). 관리자가 보는 것이 곧 데이터. 별도 동기화 레이어 없음.
- **PostgreSQL은 채팅 기록 전용** (chat_data_layer.py). 비즈니스 데이터는 저장하지 않음.
- **Google Drive는 파일 저장소**. Raw 엑셀, PDF, 출석부 등 파일 단위 자료 관리.

### 데이터 구조

| 시트 | 성격 | 저장소 | 설명 |
|------|------|--------|------|
| **회원관리** | Master (영속) | Google Sheets | 전체 회원 현재 상태 스냅샷 |
| **수강기록** | History (영속) | Google Sheets | 전체 수강 이력 (종강 시 append) |
| **신청서** | Working (회차별) | Google Sheets | 통합 신청서 — 수강+신규가입+정회원 |
| **출석부** | Working (회차별) | Google Sheets | 과목별 시트탭, 12회차 출석 |

### 회원관리 (Master) — 현재 상태 스냅샷

| 이름ID | 이름 | 성별 | 전화번호 | 주소 | 나이 | 등급 | 수강count | 출석률(누적) | 마지막수강회차 |
|--------|------|------|---------|------|------|------|----------|------------|--------------|

- **PK**: 이름ID
- **등급**: 회원 / 준회원 / 정회원

### 수강기록 (History) — 전체 수강 이력

| 이름ID | 회차 | 과목명 | 출석률 |
|--------|------|--------|--------|

종강 시: 출석부 → 출석률 확정 → 수강기록에 행 추가 → 회원관리 재집계

### 통합 신청서 (Working, 회차별) — 수강+가입+정회원 통합

| 이름ID | 이름 | 유형 | 과목명 | 예상금액 | 입금현황 | 등록상태 | 입금시간 | 입금자명(적요) | 전화번호 | 주소 | 생년월일 | 성별 | 신청일 | 시작회차 | 종료회차 |
|--------|------|------|--------|---------|---------|---------|---------|-------------|---------|------|---------|------|--------|---------|---------|

- **유형**: `수강`(수강료 2만), `신규가입`(가입비 1만), `정회원`(정회원비 12만)
- **과목명**: 수강 유형만 값 있음. 신규가입/정회원은 빈칸.
- **시작회차/종료회차**: 정회원 유형만 값 있음.
- **입금현황**: Agent가 자동 채움 (✅정상 / 🔶확인필요 / ⚠️이름불일치 / ❌미입금 / 🔄중복 / 💎면제)
- **등록상태**: Agent는 빈칸. 관리자가 배움숲 포탈에서 등록 완료 후 직접 체크. 출석부 생성 시 필터 기준.
- **데이터 소스**:
  - 수강: 배움숲 다운로드 엑셀 (관리자가 챗봇에 업로드)
  - 신규가입: Drive 신규가입 신청서 폴더 (signup_loader.py가 자동 탐색)
  - 정회원: Drive 정회원가입 신청서 폴더 (signup_loader.py가 자동 탐색)

### 출석부 (Working, 회차별, 1파일 다중시트)

Google Sheets 파일 1개, 과목별 시트탭.

| ID | 이름 | 1회차~12회차 | 출석률 |
|----|------|------------|--------|

- 12회차 일괄 생성, 출석률 = 출석수/회차수×100 (수식)
- 신청서의 `등록상태`가 체크된 수강자만 포함

---

## 데이터 파이프라인 설계

### 시스템 아키텍처

```
┌──────────────────────────────────────────────────────┐
│  Railway                                              │
│                                                       │
│  ┌──────────────┐        ┌──────────────────────┐    │
│  │  Chainlit     │        │  PostgreSQL           │    │
│  │  + LangChain  │───────→│  (채팅 기록 전용)     │    │
│  │  (챗봇)       │        └──────────────────────┘    │
│  └──────┬───────┘                                     │
│         │                                             │
└─────────┼─────────────────────────────────────────────┘
          │
          ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Google Workspace                                         │
   │  Drive (파일) + Sheets (SoT, 비즈니스 데이터) + Forms     │
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

**P3. 종강 처리** (on-demand, 관리자 요청, Phase 3 백로그)
```
트리거: 관리자가 챗봇에서 "🎓 종강 처리" Starter 버튼 클릭
1. Agent: 회차 확인 → 관리자 확정
2. 해당 회차 출석부에서 과목별 출석률 집계
3. 수강기록 시트에 append (수강생 × 과목)
4. 회원관리 시트 재집계 (수강count, 출석률, 마지막수강회차)
5. 준회원 → 회원 일괄 강등
6. (1회차 종강 시) 정회원 만료 대상 → 회원 강등 (강사/사무처 예외)
7. 종강 보고서 생성
```

**P4. DB → Sheets 동기화** (scheduled, 비활성)
```
n8n/P4_daily_sync.json — 비즈니스 테이블 제거로 실질적으로 무용.
당장은 비활성 유지, 추후 제거 검토.
```

### 데이터 흐름 정리

| 방향 | 시점 | 내용 |
|------|------|------|
| Raw → Sheets | 입금 대조 시 | 배움숲 엑셀 → 통합 신청서 (수강 유형) |
| Raw → Sheets | 입금 대조 시 | Drive 신청서 → 통합 신청서 (신규가입/정회원 유형) |
| Raw → Sheets | 입금 대조 시 | 은행 입금내역 → 통합 신청서 입금현황 반영 |
| Sheets → Sheets | 출석부 생성 시 | 신청서 등록상태 체크된 행 → 출석부 시트 생성 |

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

1. **관리자**: 챗봇에서 "💰 입금 대조" Starter 버튼 클릭
2. **Agent**: 현재 날짜 기반으로 회차 추측 → "2026-1 겨울학기 입금 대조를 시작할까요?" → 관리자가 확정/수정
3. **Agent**: "신청자 목록 엑셀을 업로드해주세요" (배움숲에서 다운로드한 `LEARNING_APPLY*.xls`)
4. **관리자**: 신청자 목록 엑셀을 챗봇에 직접 업로드
5. **Agent**: 신청자 목록 파싱 (수강 유형)
6. **Agent**: (자동) Drive에서 신규가입 신청서 로드 → 파싱 (cl.Step 진행 표시)
7. **Agent**: (자동) Drive에서 정회원가입 신청서 로드 → 파싱 (cl.Step 진행 표시)
8. **Agent**: 5+6+7을 합쳐 통합 신청서 생성 → Google Sheets에 저장
9. **Agent**: "입금 내역을 업로드해주세요"
10. **관리자**: 입금 내역 엑셀을 챗봇에 직접 업로드
11. **Agent**: 회원관리(Sheets) + 통합 신청서 + 입금내역을 바탕으로 매칭 (코드 80~90% → LLM 10~20%) → 신청서 시트에 결과 반영
12. **Agent**: 결과 요약 + 시트 링크 제공 + "신청서 시트에서 입금현황을 확인하시고, 배움숲 포탈에서 수강 등록을 처리한 뒤 등록상태를 체크해주세요"
13. **Agent**: Action 버튼 제공 — 📋 출석부 생성, 🔄 입금대조 다시하기, ❓ 다른 질문하기
14. **관리자**: 신청서 시트를 보면서 배움숲 포탈에서 수강 등록 처리 → 등록상태를 시트에서 직접 체크
15. **관리자**: '출석부 생성' 클릭
16. **Agent**: "신청서의 등록상태를 기준으로 출석부를 생성합니다." → 관리자 확인
17. **Agent**: 신청서 시트에서 등록상태 체크된 수강생만 → 출석부 생성

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

**코드 매칭 순서**:
1. 비수강료 필터링: 금액 < 1만원(예금이자 등) 스킵, "취소됨"/"대기" 키워드 감지
2. 금액 분류: 1만(가입비), 2만(수강료), 3만(합산), 4만+(다과목), 12만(정회원)
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

| Resource | 상수명 | Type | ID | 시트 탭명 |
|----------|--------|------|----|----------|
| 회원관리 | `MEMBERS_SHEET_ID` | Spreadsheet | `193r34mtLHd0-oX7MKJOWq1Ane9iBfbBZB5yYf78R3Bo` | `회원관리` |
| 수강기록 | `RECORDS_SHEET_ID` | Spreadsheet | `1cKolq6Mr-5u65nQDeMq8z4DsFWpHTLVthkAvyt4Rb6s` | `수강기록` |
| Root folder | `ROOT_FOLDER_ID` | Shared Drive root | `0AANInBeWsB7dUk9PVA` | — |
| 회원 폴더 | `MEMBERS_FOLDER_ID` | Drive folder | `12xm3vG4w5nOPTwoWgmyGCpz939KvJ93e` | — |
| 학사운영 folder | `OPERATIONS_FOLDER_ID` | Drive folder | `1WuqNFt-g5qhnY1nMk0a8dsowZHKQVRMm` | — |
| 신규가입 신청서 폴더 | `MEMBER_SIGNUP_FOLDER_ID` | Drive folder | `10ZL8rD9j7OyyZOihfyJ6GRTzmBrTBgWe` | — |
| 정회원가입 신청서 폴더 | `FULLMEMBER_SIGNUP_FOLDER_ID` | Drive folder | `17tsWfYwIRgHHcT1DQEj8Sqa4ys6pe0Vy` | — |

회원관리/수강기록 시트는 `03 회원과 강사/회원(회원명단/가입서/정회원)/` 폴더에 위치한다 (Shared Drive 루트가 아님).

### 회원 폴더 내부 구조 (03 회원과 강사/회원/)

```
회원(회원명단/가입서/정회원)/
├── 회원관리 (Google Sheets)          ← 데이터 테이블 (영속)
├── 수강기록 (Google Sheets)          ← 데이터 테이블 (영속)
├── 신규가입 신청서/                   ← 연도별 Google Forms 응답 xlsx
├── 정회원가입 신청서/                 ← 연도별 Google Forms 응답 xlsx
├── 연회비/                           ← 연회비 기록
└── 운영자료/                         ← 홍보물, 양식, 과거 작업 파일
```

**신청서 파일 탐색**: 신규가입/정회원 신청서는 연도별로 새 Google Form을 생성하므로 파일 ID가 고정이 아님. 챗봇 `signup_loader.py`에서 Drive 폴더 탐색 → Spreadsheet mimeType 필터 → 파일명에 회차 문자열(예: "2026-2") 매칭으로 자동화.

### 회차별 리소스 (런타임에 동적 탐색 — config.py에 없음)

| Resource | 탐색 방법 | 참고 ID (2026-1) |
|----------|----------|-----------------|
| 회차 폴더 | `find_term_folder(term_id)` → OPERATIONS_FOLDER_ID → 연도 폴더 → "2026-1 겨울학기" | `1rqb06_MdfaXHGmqbtS6kpb2Y6PdZbk9P` |
| 신청서 폴더 | `find_or_create_folder(term_folder, "신청서")` | — |
| 신청서 시트 | `write_applications_sheet()` / `find_spreadsheet_by_name(folder, "신청서")` | — |
| 출석부 폴더 | `find_or_create_folder(term_folder, "출석부")` | `1i-sixwrwPU_XxYhOwhDvaIqfvCxICWB8` |

**주의**: 시트 탭명이 "시트1"이 아님. API 호출 시 정확한 탭명 사용 필요 (예: `"신청서!A1:P500"`).

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
- 출석부 생성 (`app/chains/attendance.py`): 과목별 시트탭, 출석률 수식
- 회원관리/수강기록 업데이트, 동적 Drive 폴더 탐색
- Action 버튼, AskActionMessage, 세션 상태 머신
- Railway 배포, 단위 테스트 50개 통과
- **남은 작업**: E2E 기능 테스트, cl.Step 진행 상황 공유, Context Injection 고도화

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

**2-1. PostgreSQL 스키마 설계** ❌ 폐기
- 비즈니스 테이블 불필요 판단 → `db.py` 삭제. git 히스토리에 참조용으로 남음.
- `chat_data_layer.py`: Chainlit 채팅 기록 PostgreSQL 영속성은 유지 (DATABASE_URL 자동 활성화)

**2-2. Sheets SoT 복귀 + 통합 신청서** ✅ 완료
- PostgreSQL 비즈니스 테이블 제거 (`db.py` 삭제). DB는 채팅 기록 전용.
- 3개 테이블(students, member_signups, fullmember_signups) → 1개 통합 신청서 시트로 변경
- `payment.py`: Sheets 직접 읽기/쓰기 (DB 동기화 레이어 제거)
- `attendance.py`: 신청서 시트에서 등록상태 체크된 행 필터
- `signup_loader.py`: Drive에서 파싱 결과만 반환 (DB INSERT 제거)
- n8n P4: 비즈니스 테이블 없으므로 비활성 유지, 추후 제거 검토

### Phase 3 — 기능 확장 📋 백로그

- 종강 처리 (on-demand): 출석률 계산, 수강기록 추가, 등급 강등, Master 재집계, 보고서 작성
- 출석 체크 (OCR): Claude Vision으로 종이 출석부 디지털화
- 계획서 검토: PDF 파싱 → 오탈자/말투 수정 → 배움숲 멘트 생성
- Google OAuth 인증 (Workspace 도메인 제한)
- 과목별 출석부 PDF 생성 (A4 프린트용)
- Theme/CSS 커스터마이징 (폰트 크기, 색상, 접근성)
- Chainlit UI 커스터마이징 (chainlit.md 웰컴 화면, 어시스턴트 이름 표시)

## 코딩 규칙

- 한국어 주석 OK, 변수명/함수명은 영문
- **데이터 읽기/쓰기는 Google Sheets가 기본 (SoT)**. PostgreSQL은 채팅 기록 전용.
- LLM 호출은 최소화 — 코드로 처리 가능하면 코드로
- 에러 시 사용자에게 한국어로 안내 메시지 반환
- Docker 사용 안 함 (챗봇). n8n만 Docker 배포. Railway는 Procfile 기반 배포.
- 상세 기획은 `docs/DEV_DOCUMENT.md` 참조
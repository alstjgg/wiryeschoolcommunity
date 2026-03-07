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

## 프로젝트 구조

```
wiryeschoolcommunity/
├── CLAUDE.md                    # 이 파일
├── docs/
│   ├── DEV_DOCUMENT.md          # 상세 기획서 (비즈니스 컨텍스트, 데이터 구조, 입금 패턴 등)
│   └── BUSINESS_CONTEXT.md      # Context Injection 소스 텍스트
├── app/
│   ├── main.py                  # Chainlit 엔트리포인트
│   ├── config.py                # 환경 변수, 상수 (수강료, 가입비 등)
│   ├── context/
│   │   ├── business.py          # 정적 비즈니스 컨텍스트 dict
│   │   └── semester.py          # 동적 학기 데이터 로더 (Sheets에서)
│   ├── chains/
│   │   ├── router.py            # 의도 분류 라우터
│   │   ├── payment.py           # 입금 대조 체인
│   │   ├── attendance.py        # 출석부 생성 체인
│   │   ├── ocr.py               # 출석 체크 (Vision OCR) 체인
│   │   ├── syllabus.py          # 계획서 검토 체인
│   │   └── qa.py                # 질의 응답 체인
│   ├── services/
│   │   ├── google_drive.py      # Drive API 래퍼
│   │   ├── google_sheets.py     # Sheets API 래퍼
│   │   └── excel.py             # openpyxl 유틸리티
│   └── utils/
│       ├── matching.py          # 입금 매칭 규칙 엔진
│       └── formatting.py        # 결과 포맷팅
├── Procfile                     # PaaS 실행 명령
├── requirements.txt
└── .gitignore
```

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

## 현재 상태

- **Phase**: Phase 0 시작 전
- **다음 작업**:
  1. Domain-wide Delegation 설정 (GCP + Workspace Admin)
  2. Python 프로젝트 초기 셋업 (requirements.txt, 프로젝트 구조)
  3. Chainlit 기본 앱 (채팅 + 비즈니스 Q&A)
  4. Google Sheets API 연동 테스트
  5. Fly.io 배포 → 관리자 접속 확인
- **완료된 것**: 기획서(DEV_DOCUMENT v4.1), 데이터 구조 설계(회원관리+수강기록+수강생 3테이블), 입금 패턴 분석, 수강생 시트 생성(Google Sheets에 존재)

## 코딩 규칙

- 한국어 주석 OK, 변수명/함수명은 영문
- Google Sheets 데이터는 항상 Sheets API로 직접 접근 (파일 다운로드 방식 사용 안 함)
- LLM 호출은 최소화 — 코드로 처리 가능하면 코드로
- 에러 시 사용자에게 한국어로 안내 메시지 반환
- 상세 기획은 `docs/DEV_DOCUMENT.md` 참조

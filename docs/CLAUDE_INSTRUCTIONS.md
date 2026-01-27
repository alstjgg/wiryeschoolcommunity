# Claude Code Instructions

> **프로젝트**: 위례인생학교 업무 도우미  
> **최종 수정**: 2026-01-27  
> **현재 Phase**: 1 (기반 구축 + 범용 Agent)

---

## 프로젝트 개요

위례인생학교 관리자의 반복 업무를 지원하는 AI 기반 업무 도우미를 개발합니다.

### 핵심 목표

1. **Phase 1** (현재): 업무 컨텍스트를 이해하고 Google Drive와 상호작용하는 범용 Agent
2. **Phase 2**: 5가지 핵심 기능을 n8n 워크플로우로 자동화
3. **Phase 3**: 일정 기반 능동적 업무 알림

### 마감

- **Phase 1**: 2026년 2월 말 (2학기 시작 전)
- **Phase 2**: 2026년 6월 말 (3학기 시작 전)

---

## 현재 작업 (Phase 1)

### 목표

- Dify 기반 Chatbot Agent 구축
- Google Drive 연동 (파일 읽기/쓰기)
- 비즈니스 컨텍스트 이해 (Knowledge Base)

### 체크리스트

- [x] Dify Cloud 계정 생성
- [x] 비즈니스 컨텍스트 문서 작성
- [x] 데이터 스키마 정의
- [ ] Dify Agent 프롬프트 작성
- [ ] Google Drive 연동 설정
- [ ] Knowledge Base 업로드
- [ ] 기본 대화 테스트
- [ ] Docker 배포 패키지 생성

### 현재 제약사항

- **LLM**: OpenAI (임시) → Claude API 전환 예정
- **Google Drive**: 위례인생학교 Workspace 계정 준비 중

---

## 기술 스택

| 구성요소 | 기술 | 용도 |
|---------|------|------|
| AI Platform | Dify (Cloud → Docker) | Agent 호스팅, 대화 UI |
| LLM | OpenAI (임시) / Claude API | 자연어 처리 |
| 자동화 | n8n (Phase 2) | 워크플로우 |
| 파일 저장 | Google Drive | 데이터 관리 |
| 언어 | Python | 커스텀 코드, n8n Code 노드 |
| 배포 | Docker Compose | 셀프호스팅 |

---

## 프로젝트 구조

```
wiryeschoolcommunity/
├── README.md                    # 프로젝트 소개
├── docs/
│   ├── DEV_DOCUMENT.md          # 개발 명세서 (전체)
│   ├── BUSINESS_CONTEXT.md      # 비즈니스 컨텍스트 (Dify KB용)
│   ├── DATA_SCHEMA.yaml         # 데이터 스키마
│   └── CLAUDE_INSTRUCTIONS.md   # 이 파일
├── dify/
│   └── (Dify 앱 설정 export)
├── n8n/                         # Phase 2
│   └── (워크플로우 JSON)
└── docker/                      # Phase 1 후반
    └── docker-compose.yml
```

---

## 참조 문서

| 문서 | 경로 | 용도 |
|-----|------|------|
| 개발 명세서 | `docs/DEV_DOCUMENT.md` | 전체 설계, Phase별 상세 |
| 비즈니스 컨텍스트 | `docs/BUSINESS_CONTEXT.md` | 업무 규칙, 용어, 프로세스 |
| 데이터 스키마 | `docs/DATA_SCHEMA.yaml` | 테이블 구조, 파일 명명 규칙 |

---

## 개발 환경

### Dify

- **URL**: https://cloud.dify.ai
- **계정**: 13579wkd@naver.com
- **Workspace**: (설정 후 업데이트)

### GitHub

- **Repo**: wiryeschoolcommunity
- **로컬 경로**: `/Users/al03030782/Documents/GitHub/wiryeschoolcommunity`

### Google Drive

- **계정**: wirye@wiryeschoolcomunity.com (준비 중)
- **루트 폴더**: 위례인생학교

---

## 주요 비즈니스 규칙

### 회원 유형

- **일반회원**: 수강료 입금 확인 후 등록
- **연회원**: 입금 확인 없이 등록 가능 (12만원/년, 최대 4학기)

### 수강료

- **기본**: 2만원 (전 과목 동일, 변수로 관리)
- **신규 가입비**: 1만원

### 학기 일정

- 1년 4학기 (1월, 4월, 7월, 10월 시작)
- 수강신청 마감: 전월 말일
- 환불 마감: 개강 1주 후

### 데이터 처리 흐름

```
[입금내역] + [회원정보] + [수강신청현황]
              │
              ▼
         비교/매칭
              │
              ▼
     수강신청현황.입금상태 업데이트
```

---

## 코딩 규칙

### Python

- Python 3.10+
- Type hints 사용
- Docstring 작성 (Google style)
- 변수명: snake_case
- 상수: UPPER_SNAKE_CASE

### 파일

- UTF-8 인코딩
- LF 줄바꿈
- 들여쓰기: 4 spaces (Python), 2 spaces (YAML)

### 커밋 메시지

```
<type>: <subject>

Types:
- feat: 새 기능
- fix: 버그 수정
- docs: 문서 변경
- refactor: 리팩토링
- test: 테스트
- chore: 기타
```

---

## 자주 필요한 작업

### Dify 앱 설정 Export

Dify에서 앱 설정을 YAML로 내보내 `dify/` 폴더에 저장합니다.

### Knowledge Base 업데이트

`docs/BUSINESS_CONTEXT.md` 수정 후 Dify Knowledge Base에 재업로드합니다.

### 데이터 스키마 참조

`docs/DATA_SCHEMA.yaml`에서 테이블 구조, 컬럼 정의, 파일 명명 규칙을 확인합니다.

---

## 문의

이 프로젝트에 대한 추가 컨텍스트가 필요하면 다음을 참조하세요:

1. `docs/DEV_DOCUMENT.md` - 전체 설계 문서
2. `docs/BUSINESS_CONTEXT.md` - 비즈니스 규칙
3. 이전 대화 기록 (Claude Project)

---

## 변경 이력

| 버전 | 날짜 | 변경 내용 |
|:---:|------|----------|
| 1.0 | 2026-01-27 | 초안 작성 |

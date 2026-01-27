# 위례인생학교 업무 도우미

위례인생학교 관리자의 반복 업무를 지원하는 AI 기반 업무 도우미입니다.

## 프로젝트 개요

### 목적

- 강의 계획서 검토 및 게시글 생성
- 입금 내역과 수강신청자 매칭
- 수강자 목록 생성
- 운영 보고서 생성
- 출석부 디지털화

### 기술 스택

| 구성요소 | 기술 |
|---------|------|
| AI Platform | Dify |
| LLM | Claude API / OpenAI |
| 자동화 | n8n |
| 파일 저장 | Google Drive |
| 배포 | Docker Compose |

## 프로젝트 구조

```
wiryeschoolcommunity/
├── README.md                    # 이 파일
├── docs/
│   ├── DEV_DOCUMENT.md          # 개발 명세서
│   ├── BUSINESS_CONTEXT.md      # 비즈니스 컨텍스트
│   ├── DATA_SCHEMA.yaml         # 데이터 스키마
│   └── CLAUDE_INSTRUCTIONS.md   # Claude Code 작업 지시서
├── dify/                        # Dify 앱 설정
├── n8n/                         # n8n 워크플로우
└── docker/                      # Docker 설정
```

## 개발 Phase

| Phase | 목표 | 기간 |
|:-----:|-----|------|
| 1 | 범용 Agent (대화 + Google Drive 연동) | ~2026.02 |
| 2 | 워크플로우 자동화 (n8n) | ~2026.06 |
| 3 | 능동적 업무 알림 | 2026.07~ |

## 문서

- [개발 명세서](docs/DEV_DOCUMENT.md)
- [비즈니스 컨텍스트](docs/BUSINESS_CONTEXT.md)
- [데이터 스키마](docs/DATA_SCHEMA.yaml)
- [Claude Code 지시서](docs/CLAUDE_INSTRUCTIONS.md)

## 시작하기

### 요구사항

- Docker Desktop
- Python 3.10+
- Dify Cloud 계정 또는 셀프호스팅
- Google Drive API 접근

### 설치

```bash
# 저장소 클론
git clone [repository-url]
cd wiryeschoolcommunity

# Docker 실행 (Phase 1 완료 후)
cd docker
docker compose up -d
```

## 라이선스

Private

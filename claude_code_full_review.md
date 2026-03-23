# 전체 코드 리뷰 + 문서 업데이트 + 리팩터링 명령

## 개요

`improvement_plan.md` (v4.0)에 명시된 설계 확정안을 기준으로, 프로젝트 전체 코드와 문서를 리뷰하고 불일치 사항을 수정한다. 코드 품질 개선도 함께 수행한다.

## 반드시 먼저 읽을 파일

1. `improvement_plan.md` — 설계 확정안 (v4.0)
2. `CLAUDE.md` — 개발 가이드라인
3. `docs/BUSINESS_CONTEXT.md` — 비즈니스 규칙
4. `docs/DEV_DOCUMENT.md` — 개발 기획서

## 작업 순서

### Phase 1: 리뷰 — 불일치 리스트업

아래 체크리스트를 기준으로 전체 코드+문서를 리뷰하고, 불일치 사항을 파일별로 리스트업하여 사용자에게 보여줄 것. **수정하기 전에 반드시 리스트업 결과를 확인받을 것.**

### Phase 2: 문서 업데이트

확인받은 후 문서부터 수정 (문서가 코드의 기준이므로):
- `docs/BUSINESS_CONTEXT.md`
- `docs/DEV_DOCUMENT.md`
- `CLAUDE.md`

### Phase 3: 코드 수정

문서 확정 후 코드 수정:
- `app/chains/payment.py`
- `app/chains/attendance.py`
- `app/chains/graduation.py`
- `app/main.py`
- `app/config.py`
- `app/services/db.py`
- `app/services/n8n.py`
- `app/services/signup_loader.py`

### Phase 4: /simplify

모든 수정 완료 후 code-reviewer skill로 최종 품질 점검.

---

## 리뷰 체크리스트

### A. 데이터 구조 변경

- [ ] `registration_status` (Boolean) → `processing_status` (Text: 등록완료/환불완료/취소완료/보류/NULL) 전환 완료
  - applications 테이블, DB CRUD, Sheets column mapping, main.py gate check, attendance.py 수강생 로드 등 모든 참조
- [ ] `start_term`, `end_term` 참조가 코드에서 완전히 제거됨
  - payment.py의 build_applications(), signup_loader.py의 반환값, APPLICATION_HEADER 등
- [ ] `processed_at` 컬럼이 applications 테이블에 존재하고 입금 대조 완료 시 갱신됨
- [ ] `deposits` 테이블 관련 CRUD가 payment flow에 통합됨
  - 입금내역 엑셀 파싱 → db.insert_deposits() 호출
  - 매칭 결과 → db.update_deposit_match() 호출
  - 미매칭 건 → 관리자 안내
- [ ] `phone`, `address`가 DB에는 유지되되 Sheets push에는 포함되지 않음

### B. 회원관리 파일 통합 (핵심)

- [ ] 회차별 '신청서' 스프레드시트 파일 생성 로직이 DB 모드에서 제거됨
  - `_write_applications_to_sheets()`의 회차 폴더 내 파일 생성 → DB 모드에서 실행 안 함
  - `find_or_create_folder(term_folder_id, "신청서")` 등 회차별 로직 → DB 모드 불필요
- [ ] DB 모드에서 applications 관련 Sheets 읽기/쓰기 대상이 회원관리 파일의 '신청기록' 탭
  - 시트 ID: config.py의 `MEMBERS_SHEET_ID` (`193r34mtLHd0-oX7MKJOWq1Ane9iBfbBZB5yYf78R3Bo`)
  - 탭: `신청기록`
  - Range: `신청기록!A1:L5000`
- [ ] `applications_sheet_id` 세션 변수의 의미 변경
  - DB 모드: `MEMBERS_SHEET_ID` (고정) 사용
  - Sheets 모드: 기존 회차별 동적 ID 유지 (폴백)
- [ ] `_check_processing_gate()`가 회원관리 파일의 '신청기록' 탭을 읽음
- [ ] `attendance.py`의 `_load_registered_from_sheets()`와 `_load_registered_from_db()`가 회원관리 파일의 '신청기록' 탭을 읽음

### C. 신청기록 시트 헤더 순서 (확정)

신청일(A) | 회차(B) | 이름ID(C) | 이름(D) | 유형(E) | 과목명(F) | 예상금액(G) | 입금시각(H) | 입금자명(I) | 입금현황(J) | 확인사유(K) | 처리상태(L)

코드 내 `APPLICATION_HEADER` 상수가 이 순서와 일치하는지 확인.

### D. 미확인입금 시트 헤더 (확정)

입금일시(A) | 회차(B) | 입금액(C) | 입금자명(D) | 적요(E) | 확인사유(F) | 처리상태(G)

### E. n8n 연동

- [ ] DB write 후 `trigger_sheets_sync()` 호출이 적절히 배치됨
  - applications 변경 시: `trigger_sheets_sync("applications", {"term_id": term_id})`
  - members 변경 시: `trigger_sheets_sync("members")`
  - deposits 변경 시: `trigger_sheets_sync("deposits", {"term_id": term_id})`
- [ ] Sheets 직접 write 코드는 USE_DB_SOT=false 경로에서만 실행

### F. 입금 대조 플로우 (확정)

```
1.  관리자: "입금 대조" 클릭 → 회차 확인
2.  관리자: 신청자 엑셀 업로드
3.  Agent: 통합 신청서 생성 (수강 + 신규가입 + 정회원 → DB applications INSERT/UPSERT)
4.  관리자: 입금내역 엑셀 업로드
5.  Agent: 입금내역 전건 → DB deposits INSERT
6.  Agent: 자동 매칭 (코드 80~90% → LLM 10~20%)
7.  Agent: grade cascade 실행
8.  Agent: 결과 요약 + n8n webhook + 관리자 안내 (미확인입금 N건 포함)
9.  관리자: 시트 확인 + 처리상태 입력
10. 관리자: "입금 대조 재실행" → idempotent cascade 재실행
11. 관리자: "출석부 생성" → gate check
```

### G. 출석부 생성 플로우 (확정 — 간소화)

```
관리자: "출석부 생성" 클릭 → 회차 확인
Agent: 즉시 gate check 실행 (신청기록 + 미확인입금의 처리상태 확인)
  → 미처리 건 있으면: 상세 안내 + "🔄 다시 확인" / "❌ 취소"
  → 미처리 건 없으면: "배움숲 등록/환불/취소 처리를 모두 완료하셨나요?" + "✅ 확인" / "❌ 취소"
     → 확인 시 출석부 생성 실행
```

기존 3단계 안내 → 확인 → gate check 2단계 구조를 제거하고, 자동 gate check 1단계로 통합.

### H. 비즈니스 규칙 문서 수정 (BUSINESS_CONTEXT.md)

- [ ] 섹션 3.1: 정회원 가입 가능 기간 명시 — "2~4학기(봄~가을)에만 가입 가능, 1학기(겨울)에는 불가"
- [ ] 섹션 3.1/3.2: "후년" 용어 → "다음 해"로 명확화 (2026년 가입 → 2027-1 종강)
- [ ] 섹션 6: 등록 상태 코드 → processing_status (드롭다운: 등록완료/환불완료/취소완료/보류) 반영
- [ ] 섹션 8: 데이터 아키텍처에 deposits 테이블 추가, 회원관리 파일 5탭 구조 반영
- [ ] 섹션 8.6 정회원신청기록: start_term/end_term 불필요 설명 (일괄 종강 처리이므로)
- [ ] 섹션 8.7 수강생: "등록상태" → "처리상태"로 변경, 드롭다운 값 명시

### I. 코드 품질 (code-reviewer skill 활용)

- [ ] 중복 로직 추출 (예: Sheets 읽기 패턴)
- [ ] 데드코드 제거 (DB 모드에서 사용하지 않는 Sheets 전용 함수 — 단, USE_DB_SOT=false 폴백은 유지)
- [ ] 함수 단일 책임 원칙 위반
- [ ] 네이밍 일관성 (한글/영문 혼용 정리)
- [ ] 에러 핸들링 누락

---

## 중요 제약사항

- `app/services/db.py`의 스키마와 CRUD 함수는 이미 improvement_plan v4.0 기준으로 완성됨. 수정 최소화.
- `app/services/n8n.py`도 완성됨. 수정 불필요.
- `USE_DB_SOT` 플래그 분기는 반드시 유지. false 경로 (Sheets 모드)는 폴백으로 남겨둘 것.
- 테스트는 별도 진행. 이 작업에서는 코드 작성에 집중.

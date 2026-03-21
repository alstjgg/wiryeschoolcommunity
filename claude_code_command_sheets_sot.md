# Claude Code 작업 명령 — PostgreSQL 비즈니스 테이블 제거, Google Sheets SoT 복귀

## 배경

논의 결과 PostgreSQL 비즈니스 테이블이 불필요하다는 결론에 도달.

이유:
- 모든 비즈니스 데이터는 결국 관리자가 Google Sheets에서 보고 수정함
- DB는 중간 저장소일 뿐이고, DB→Sheets 동기화 레이어가 추가 복잡도만 증가시킴
- 사용자 2~4명, 동시 쓰기 충돌 없음, 복잡한 JOIN 불필요
- Phase 1에서 Sheets API 성능 문제 없었음

변경 방향:
- **Google Sheets를 다시 SoT(Single Source of Truth)로 사용**
- **PostgreSQL은 채팅 기록(chat_data_layer.py)용으로만 유지**
- **n8n은 P4(DB→Sheets 동기화)만 남아있는데, DB 비즈니스 테이블이 없어지면 P4도 불필요 — 당장은 비활성 유지, 추후 제거**
- **`db.py`의 비즈니스 테이블/함수 제거**
- **`signup_loader.py`가 DB 대신 Sheets에 직접 쓰도록 전환**

추가로, 기존 3개 별도 테이블(students, member_signups, fullmember_signups)을
**1개 통합 신청서 시트(applications)**로 변경.

## 통합 신청서 시트 설계

회차별로 Google Sheets 파일 1개 생성 (기존 "수강생" 시트를 "신청서"로 확장).
시트명: `신청서` (또는 관리자에게 익숙한 이름)

컬럼 구조:
| 이름ID | 이름 | 유형 | 과목명 | 예상금액 | 입금현황 | 등록상태 | 입금시간 | 입금자명(적요) | 전화번호 | 주소 | 생년월일 | 성별 | 신청일 | 시작회차 | 종료회차 |

유형(type) 값:
- `수강` — 수강료 2만 (배움숲 신청자 목록에서 로드)
- `신규가입` — 가입비 1만 (Drive 신규가입 신청서에서 로드)
- `정회원` — 정회원비 12만 (Drive 정회원가입 신청서에서 로드)

과목명: 수강 유형만 값 있음. 신규가입/정회원은 빈칸.
시작회차/종료회차: 정회원 유형만 값 있음.

입금현황: ✅정상 / 🔶확인필요 / ⚠️이름불일치 / ❌미입금 / 🔄중복 / 💎면제
등록상태: 관리자가 배움숲 포탈에서 등록 완료 후 직접 체크

## 수정 대상

### 1. app/services/db.py — 비즈니스 테이블 제거

`_SCHEMA_SQL`에서 비즈니스 테이블 6개 (members, enrollment_records, member_signups,
fullmember_signups, students, attendance) 정의를 제거.
`get_pool()` 함수와 connection pool은 유지 (chat_data_layer.py에서 사용할 수 있음).
단, chat_data_layer.py를 확인해보면 자체 pool을 갖고 있으므로 db.py의 pool도 불필요할 수 있음.

비즈니스 CRUD 함수 전부 제거:
- load_members, upsert_member, bulk_upgrade_members
- upsert_students, load_students, update_student_payments, load_registered_students
- add_enrollment_records, load_member_signups, load_fullmember_signups

db.py 파일 자체를 삭제하거나, 향후 다시 필요할 수 있으니 주석 처리 또는 최소화.
→ **삭제 추천**. git 히스토리에 남으니까.

### 2. app/services/signup_loader.py — DB 대신 Sheets에 직접 쓰도록 전환

현재: Drive에서 읽기 → DB member_signups/fullmember_signups에 INSERT
변경: Drive에서 읽기 → 파싱 결과를 dict 리스트로 반환 (DB 쓰기 제거)

반환값을 payment.py에서 받아서 통합 신청서 데이터에 합치는 구조.

`load_member_signups_from_drive(term_id)` → DB INSERT 제거, 파싱 결과만 반환:
```python
return {
    "found": True,
    "file_name": sheet_file["name"],
    "count": len(records),
    "records": records,  # list[dict] — name_id, name, phone, address 등
    "error": None,
}
```

`load_fullmember_signups_from_drive(term_id)` → 동일하게 DB INSERT 제거, records 반환.

`import db` 관련 코드 제거.

### 3. app/chains/payment.py — DB 대신 Sheets 직접 읽기/쓰기로 전환

현재: db.upsert_students, db.load_members, db.update_student_payments 등 DB 함수 사용
변경: google_sheets.py의 read_sheet, write_sheet 함수로 전환 (Phase 1 방식)

핵심 변경:
- `from app.services import db` → 제거
- 수강생 DB 저장 → 통합 신청서 Sheets에 직접 쓰기
- 회원관리 조회 → MEMBERS_SHEET_ID에서 직접 읽기
- 매칭 결과 저장 → 통합 신청서 Sheets에 직접 쓰기

입금 대조 플로우 (변경 후):
1. 회차 확인
2. 관리자가 신청자 목록(배움숲 엑셀) 업로드 → 파싱
3. [자동] Drive에서 신규가입 신청서 로드 → 파싱 (signup_loader.py)
4. [자동] Drive에서 정회원가입 신청서 로드 → 파싱 (signup_loader.py)
5. 2+3+4를 합쳐서 통합 신청서 데이터 생성:
   - 수강 신청: type='수강', amount=20000 (배움숲에서 로드)
   - 신규가입: type='신규가입', amount=10000 (Drive에서 로드)
   - 정회원: type='정회원', amount=120000 (Drive에서 로드)
6. 통합 신청서를 회차 폴더에 Google Sheets로 저장 (기존 "수강생" 시트 대신 "신청서" 시트)
7. 관리자에게 "입금 내역을 업로드해주세요" 요청
8. 입금 내역 업로드 → 통합 신청서와 매칭
   - 정회원 선처리: 회원관리 Sheets에서 등급="정회원" → 💎면제
   - 입금 유형별 매칭 (1만=가입비, 2만=수강료, 3만=합산, 12만=정회원비)
9. 매칭 결과를 신청서 Sheets에 반영 (입금현황, 입금시간, 입금자명 컬럼 업데이트)
10. 결과 요약 + 신청서 시트 URL 제공
    - 수강 신청 매칭 결과
    - 신규가입 매칭 결과
    - 정회원가입 매칭 결과
    - 검토 필요 건 요약
11. 관리자에게 안내: 배움숲 등록 후 시트에서 '등록상태' 체크

### 4. app/chains/attendance.py — DB 대신 Sheets에서 읽기

현재: db.load_registered_students로 DB에서 등록완료 수강생 로드
변경: 통합 신청서 Sheets에서 type='수강' AND 등록상태='등록완료' 행 필터

### 5. 회원관리 시트 업데이트

입금 대조 완료 시 회원관리 Sheets를 직접 업데이트:
- 신규가입 입금 확인 → 회원관리에 새 회원 추가 (등급: 회원)
- 수강료 입금 확인 → 회원→준회원 승격, 수강count+1, 마지막수강회차 갱신
- 정회원비 입금 확인 → 회원→정회원 전환
(Phase 1에서 이미 구현되어 있던 로직 — DB 경유 대신 Sheets 직접 업데이트로 복귀)

### 6. CLAUDE.md 업데이트

주요 변경:
- 데이터 아키텍처: PostgreSQL SoT → Google Sheets SoT로 변경
  - PostgreSQL은 채팅 기록(chat_data_layer.py) 전용
  - 비즈니스 데이터는 전부 Google Sheets
- 통합 신청서 시트 설계 반영 (기존 students + member_signups + fullmember_signups → applications)
- 파이프라인 분류:
  - Event-driven 행 제거
  - On-demand에 "Drive 신청서 자동 로드" 포함
  - n8n/P4는 당장은 유지하되 비활성 — 비즈니스 테이블이 없으므로 실질적으로 무용
- Phase 2 로드맵 전면 재정리:
  - 2-0 n8n 배포: ✅ 완료 (당장은 유지, 추후 제거 검토)
  - 2-1 스키마 설계: ❌ 폐기 → Sheets SoT
  - 2-2 챗봇 DB 전환: ❌ 폐기 → Sheets 유지
  - 2-3 입금 대조 확장: 🔄 진행 (통합 신청서 + 입금 유형별 처리)
  - 2-4 n8n 워크플로우: P1/P2 챗봇 이동 완료, P4 비활성 유지
- db.py 제거, signup_loader.py 변경 반영
- 프로젝트 구조 업데이트

### 7. 파일 정리
- `app/services/db.py` → 삭제
- `n8n/P1_member_signup.json`, `n8n/P2_fullmember_signup.json` → 이미 삭제됨
- `n8n/P4_daily_sync.json` → 유지 (비활성, 참조용)

## 참고

- `chat_data_layer.py`는 건들지 않음 — PostgreSQL 채팅 기록 전용으로 계속 사용
- google_drive.py의 `list_files()` 함수가 signup_loader.py에서 import됨 — 이 함수가 없으면 추가 필요
- 기존 Phase 1 코드(google_sheets.py의 read_sheet, write_sheet 등)를 최대한 재활용
- `app/utils/matching.py`의 매칭 로직은 유지 — 입력 데이터 형식만 students 테이블 → 통합 신청서 dict로 변경

## 핵심 원칙

- **Sheets가 SoT** — 코드는 Sheets에서 읽고, Sheets에 쓴다
- **DB는 채팅 기록만** — chat_data_layer.py만 PostgreSQL 사용
- **n8n은 당장 유지, 추후 제거** — P4가 비활성 상태로 남아있을 뿐
- **관리자가 보는 것이 곧 데이터** — 별도 동기화 레이어 없음
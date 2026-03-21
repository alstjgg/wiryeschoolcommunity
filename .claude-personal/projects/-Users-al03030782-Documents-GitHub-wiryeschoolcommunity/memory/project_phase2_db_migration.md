---
name: Phase 2 DB Migration In Progress
description: PostgreSQL SoT migration status - db.py created, payment/attendance/main partially converted, Railway infra setup underway
type: project
---

Phase 2-2 (챗봇 DB 전환) is actively in progress as of 2026-03-21.

**What's done:**
- `app/services/db.py` — full asyncpg data layer with schema auto-creation, CRUD for all 6 tables (members, students, enrollment_records, member_signups, fullmember_signups, attendance)
- `app/chains/payment.py` — converted from Sheets-first to DB SoT; removed `write_results_to_sheet`, `load_applicants_from_drive`, `load_students_from_sheet`, `load_members`; added `from app.services import db`
- `app/main.py` — flow changed: now asks for applicant file upload directly (removed Drive search); reads students/members from DB; writes results to DB via `db.update_student_payments`; attendance flow uses `db.load_registered_students`
- `app/chains/attendance.py` — staged but diff was empty (likely minor or already committed)

**What's not yet wired:**
- DB → Sheets sync after payment matching (old `write_results_to_sheet` removed, replacement TBD)
- Sheets → DB reverse sync for `등록상태` (출석부 생성 시)
- `attendance.py` full DB integration unclear

**Why:** Moving from Google Sheets as SoT to PostgreSQL for data integrity. Sheets becomes read-only admin view.

**How to apply:** All new data read/write code should use `app/services/db.py`. Sheets writes are only for admin view sync (DB → Sheets direction).

"""회원관리 시트 초기 데이터 생성 — 출석부 폴더의 수강생 XLSX에서 추출

사용법: python scripts/populate_members.py
"""

import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import MEMBERS_SHEET_ID, ATTENDANCE_FOLDER_ID
from app.services.google_auth import get_drive_service
from app.services.google_drive import list_files
from app.services.google_sheets import write_sheet, read_sheet
from app.services.excel import parse_student_list
from app.context.semester import get_current_semester


def load_all_students() -> list[dict]:
    """출석부 폴더의 모든 XLSX 파일에서 수강생 추출"""
    drive = get_drive_service()
    files = list_files(ATTENDANCE_FOLDER_ID)

    xlsx_files = [
        f for f in files
        if f["name"].endswith(".xlsx")
        or f["mimeType"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ]

    print(f"출석부 폴더에서 {len(xlsx_files)}개 파일 발견")

    all_students = []
    for file_info in xlsx_files:
        file_bytes = drive.files().get_media(fileId=file_info["id"]).execute()
        students = parse_student_list(file_bytes, filename=file_info["name"])
        print(f"  {file_info['name']}: {len(students)}명")
        all_students.extend(students)

    print(f"총 수강생 레코드: {len(all_students)}건")
    return all_students


def deduplicate_members(students: list[dict]) -> list[dict]:
    """이름ID 기준 중복 제거 — 다과목 수강생은 수강count 집계"""
    members = {}
    for s in students:
        sid = s["이름ID"]
        if sid in members:
            members[sid]["수강count"] += 1
        else:
            members[sid] = {
                "이름ID": sid,
                "이름": s["이름"],
                "성별": s.get("성별", ""),
                "전화번호": s.get("전화번호", ""),
                "주소": s.get("주소", ""),
                "나이": s.get("나이", ""),
                "수강count": 1,
            }

    result = list(members.values())
    print(f"중복 제거 후 고유 회원: {len(result)}명")
    return result


def build_member_rows(members: list[dict]) -> list[list]:
    """회원관리 시트 형식으로 변환"""
    semester = get_current_semester()
    label = semester["label"]

    header = [
        "이름ID", "이름", "성별", "전화번호", "주소", "나이",
        "등급", "가입날짜", "시작학기", "만료학기",
        "수강count", "출석률(누적)", "마지막수강학기", "active",
    ]

    rows = [header]
    for m in members:
        rows.append([
            m["이름ID"],
            m["이름"],
            m["성별"],
            m["전화번호"],
            m["주소"],
            m["나이"],
            "회원",       # 등급 기본값
            "",           # 가입날짜 (알 수 없음)
            label,        # 시작학기
            "",           # 만료학기 (정회원만 해당)
            m["수강count"],
            "",           # 출석률 (아직 데이터 없음)
            label,        # 마지막수강학기
            "Y",          # active
        ])

    return rows


def main():
    # 1. 기존 데이터 확인
    existing = read_sheet(MEMBERS_SHEET_ID, "회원관리!A1:A5")
    if existing and len(existing) > 1:
        print(f"회원관리 시트에 이미 {len(existing) - 1}건의 데이터가 있습니다.")
        confirm = input("덮어쓰시겠습니까? (y/n): ").strip().lower()
        if confirm != "y":
            print("취소됨.")
            return

    # 2. 출석부에서 수강생 로드
    students = load_all_students()
    if not students:
        print("수강생 데이터를 찾을 수 없습니다.")
        return

    # 3. 중복 제거
    members = deduplicate_members(students)

    # 4. 시트 형식 변환
    rows = build_member_rows(members)

    # 5. 시트에 쓰기
    print(f"\n회원관리 시트에 {len(rows) - 1}명 기록 중...")
    write_sheet(MEMBERS_SHEET_ID, "회원관리!A1", rows)
    print("완료!")

    # 요약 출력
    multi_course = sum(1 for m in members if m["수강count"] > 1)
    print(f"\n=== 요약 ===")
    print(f"총 회원: {len(members)}명")
    print(f"다과목 수강: {multi_course}명")


if __name__ == "__main__":
    main()

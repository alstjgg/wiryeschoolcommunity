"""수강생 시트 초기 데이터 생성 — 출석부 폴더의 수강생 XLSX에서 추출

사용법: python scripts/populate_students.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import STUDENTS_SHEET_ID, ATTENDANCE_FOLDER_ID
from app.services.google_auth import get_drive_service
from app.services.google_drive import list_files
from app.services.google_sheets import write_sheet, read_sheet
from app.services.excel import parse_student_list


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


def main():
    # 1. 기존 데이터 확인
    existing = read_sheet(STUDENTS_SHEET_ID, "수강생!A1:A5")
    if existing and len(existing) > 1:
        print(f"수강생 시트에 이미 {len(existing) - 1}건의 데이터가 있습니다.")
        confirm = input("덮어쓰시겠습니까? (y/n): ").strip().lower()
        if confirm != "y":
            print("취소됨.")
            return

    # 2. 출석부에서 수강생 로드
    students = load_all_students()
    if not students:
        print("수강생 데이터를 찾을 수 없습니다.")
        return

    # 3. 불량 데이터 필터링 (강좌명이 이름으로 들어간 케이스)
    bad_names = {"금융과 경제"}
    filtered = [s for s in students if s["이름"] not in bad_names]
    if len(filtered) < len(students):
        print(f"불량 데이터 {len(students) - len(filtered)}건 제거")
    students = filtered

    # 4. 수강생 시트 형식으로 변환 (이름ID + 과목명만, 나머지는 빈 값)
    header = ["이름ID", "과목명", "입금시간", "입금자명(적요)", "비고", "입금현황", "등록상태"]
    rows = [header]
    for s in students:
        rows.append([
            s["이름ID"],
            s["강좌명"],
            "",           # 입금시간
            "",           # 입금자명(적요)
            "",           # 비고
            "❌미입금",   # 입금현황 기본값
            "",           # 등록상태
        ])

    # 5. 시트에 쓰기
    print(f"\n수강생 시트에 {len(rows) - 1}건 기록 중...")
    write_sheet(STUDENTS_SHEET_ID, "수강생!A1", rows)
    print("완료!")

    # 요약
    courses = {s["강좌명"] for s in students}
    print(f"\n=== 요약 ===")
    print(f"총 수강 레코드: {len(students)}건")
    print(f"강좌 수: {len(courses)}개")


if __name__ == "__main__":
    main()

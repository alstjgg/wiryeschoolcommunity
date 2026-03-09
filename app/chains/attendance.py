"""출석부 생성 파이프라인 — 수강생 시트 기반으로 과목별 출석부 생성"""

from app.config import MAX_SESSIONS
from app.services.google_auth import get_drive_service, get_sheets_service
from app.services.google_drive import find_or_create_folder, find_spreadsheet_by_name
from app.services.google_sheets import read_sheet, write_sheet


def create_attendance_sheet(term_id: str, term_folder_id: str, students_sheet_id: str) -> dict:
    """출석부 Google Sheets 생성 (과목별 시트탭)

    1. 수강생 시트에서 등록상태="정상등록" 필터
    2. 과목별 그룹핑
    3. 출석부 폴더를 회차 폴더 안에서 찾거나 생성
    4. 출석부 파일 생성 (과목별 탭)
    5. 각 탭: ID, 이름, 1회차~12회차, 출석률

    Returns:
        dict with keys: spreadsheet_id, spreadsheet_url, courses, total_students
    """
    # 1. 수강생 시트에서 정상등록 수강자만 로드
    rows = read_sheet(students_sheet_id, "수강생!A1:G500")
    if not rows or len(rows) < 2:
        raise ValueError("수강생 시트가 비어있습니다.")

    registered = []
    for row in rows[1:]:
        if len(row) < 7:
            continue
        등록상태 = row[6] if len(row) > 6 else ""
        if 등록상태 == "정상등록":
            student_id = row[0]
            이름 = student_id.rstrip("0123456789") if student_id else ""
            registered.append({
                "이름ID": student_id,
                "이름": 이름,
                "과목명": row[1] if len(row) > 1 else "",
            })

    if not registered:
        raise ValueError("정상등록된 수강생이 없습니다. 입금 대조를 먼저 완료해주세요.")

    # 2. 과목별 그룹핑
    courses = {}
    for s in registered:
        course = s["과목명"]
        if course not in courses:
            courses[course] = []
        courses[course].append(s)

    # 3. 출석부 폴더를 회차 폴더 안에서 찾거나 생성
    attendance_folder = find_or_create_folder(term_folder_id, "출석부")
    attendance_folder_id = attendance_folder["id"]

    # 4. 출석부 Google Sheets 생성
    drive = get_drive_service()
    sheets_service = get_sheets_service()

    file_metadata = {
        "name": "출석부",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [attendance_folder_id],
    }
    file = drive.files().create(body=file_metadata, fields="id, webViewLink").execute()
    spreadsheet_id = file["id"]
    spreadsheet_url = file.get("webViewLink", "")

    # 5. 과목별 탭 추가, 기본 Sheet1 삭제
    course_names = sorted(courses.keys())

    requests = []
    for i, course_name in enumerate(course_names):
        requests.append({
            "addSheet": {
                "properties": {
                    "title": course_name,
                    "index": i,
                }
            }
        })

    if requests:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()

    # 기본 Sheet1 삭제
    sheet_metadata = sheets_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id
    ).execute()
    for sheet in sheet_metadata.get("sheets", []):
        if sheet["properties"]["title"] == "Sheet1":
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "requests": [{
                        "deleteSheet": {
                            "sheetId": sheet["properties"]["sheetId"]
                        }
                    }]
                },
            ).execute()
            break

    # 6. 각 과목 탭에 데이터 입력
    for course_name in course_names:
        students = courses[course_name]
        # 헤더: ID, 이름, 1회차~12회차, 출석률
        header = ["ID", "이름"]
        for i in range(1, MAX_SESSIONS + 1):
            header.append(f"{i}회차")
        header.append("출석률")

        data_rows = [header]
        for row_idx, s in enumerate(students):
            row_num = row_idx + 2  # 1-indexed, 헤더가 1행
            row = [s["이름ID"], s["이름"]]
            # 1회차~12회차 빈 칸
            row.extend([""] * MAX_SESSIONS)
            # 출석률 수식: 출석수(O) / 총회차 × 100
            col_start = "C"
            col_end = chr(ord("C") + MAX_SESSIONS - 1)  # "N"
            formula = f'=IFERROR(COUNTIF({col_start}{row_num}:{col_end}{row_num},"O")/{MAX_SESSIONS}*100,0)'
            row.append(formula)
            data_rows.append(row)

        write_sheet(spreadsheet_id, f"{course_name}!A1", data_rows)

    return {
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_url": spreadsheet_url,
        "courses": course_names,
        "total_students": len(registered),
    }

"""출석부 생성 파이프라인 — DB SoT 기반

등록상태='정상등록' 수강생을 DB에서 읽어 과목별 출석부 Google Sheets를 생성.
"""

from app.config import MAX_SESSIONS
from app.services import db
from app.services.google_auth import get_drive_service, get_sheets_service
from app.services.google_drive import find_or_create_folder
from app.services.google_sheets import write_sheet


async def create_attendance_sheet(
    term_id: str,
    term_folder_id: str,
) -> dict:
    """출석부 Google Sheets 생성 (과목별 시트탭)

    1. DB에서 등록상태='정상등록' 수강생 로드
    2. 과목별 그룹핑
    3. 출석부 폴더를 회차 폴더 안에서 찾거나 생성
    4. 출석부 파일 생성 (과목별 탭)
    5. 각 탭: ID, 이름, 1회차~12회차, 출석률 수식

    Returns:
        dict with keys: spreadsheet_id, spreadsheet_url, courses, total_students
    """
    # 1. DB에서 정상등록 수강생 로드
    registered = await db.load_registered_students(term_id)

    if not registered:
        raise ValueError(
            "정상등록된 수강생이 없습니다. "
            "입금 대조 후 배움숲에서 등록 처리를 완료하고 수강생 시트에 등록상태를 체크해주세요."
        )

    # 2. 과목별 그룹핑
    courses: dict[str, list] = {}
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
    file = drive.files().create(
        body=file_metadata, fields="id, webViewLink", supportsAllDrives=True
    ).execute()
    spreadsheet_id = file["id"]
    spreadsheet_url = file.get("webViewLink", "")

    # 5. 과목별 탭 추가, 기본 Sheet1 삭제
    course_names = sorted(courses.keys())

    requests = [
        {
            "addSheet": {
                "properties": {"title": course_name, "index": i}
            }
        }
        for i, course_name in enumerate(course_names)
    ]

    if requests:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()

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
        header = ["ID", "이름"]
        for i in range(1, MAX_SESSIONS + 1):
            header.append(f"{i}회차")
        header.append("출석률")

        data_rows = [header]
        for row_idx, s in enumerate(students):
            row_num = row_idx + 2  # 1-indexed, 헤더가 1행
            row = [s["이름ID"], s["이름"]]
            row.extend([""] * MAX_SESSIONS)
            col_end = chr(ord("C") + MAX_SESSIONS - 1)  # "N"
            formula = (
                f'=IFERROR(COUNTIF(C{row_num}:{col_end}{row_num},"O")'
                f"/{MAX_SESSIONS}*100,0)"
            )
            row.append(formula)
            data_rows.append(row)

        write_sheet(spreadsheet_id, f"{course_name}!A1", data_rows)

    return {
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_url": spreadsheet_url,
        "courses": course_names,
        "total_students": len(registered),
    }

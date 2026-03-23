"""출석부 생성 파이프라인 — Dual-Write (DB + Sheets) + 인쇄용 PDF

출석부 시트 구조:
  탭1 수강생: 이름ID, 이름, 과목명, 출석률 (종강 처리 시 출석률 채워짐)
  탭N {과목명}: 이름, 1회차~12회차 (OCR 기록용 + PDF 출력 원본)

처리상태는 Sheets에서만 관리자가 편집 가능 (드롭다운: 등록완료/환불완료/취소완료/보류).
DB 모드에서도 처리상태는 Sheets에서 읽은 뒤 DB applications와 머지하여 필터링.
"""

import io
import logging
from pathlib import Path

from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, PageBreak,
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.config import MAX_SESSIONS, USE_DB_SOT
from app.services.google_auth import get_drive_service, get_sheets_service
from app.services.google_drive import find_or_create_folder
from app.services.google_sheets import read_sheet, write_sheet

logger = logging.getLogger(__name__)


# ================================================= 폰트 등록 =====

def _register_korean_font() -> tuple[str, str]:
    """NanumGothic 폰트를 ReportLab에 등록.

    Returns: (regular_font_name, bold_font_name)
    폰트 파일이 없으면 기본 폰트(Helvetica) 반환 (한국어 깨짐 주의).
    """
    font_dir = Path(__file__).parent.parent.parent / "assets" / "fonts"
    regular = font_dir / "NanumGothic.ttf"
    bold = font_dir / "NanumGothicBold.ttf"

    try:
        if regular.exists():
            pdfmetrics.registerFont(TTFont("NanumGothic", str(regular)))
        else:
            return "Helvetica", "Helvetica-Bold"
        if bold.exists():
            pdfmetrics.registerFont(TTFont("NanumGothicBold", str(bold)))
            return "NanumGothic", "NanumGothicBold"
        return "NanumGothic", "NanumGothic"
    except Exception:
        return "Helvetica", "Helvetica-Bold"


# ================================================= 수강생 로드 =====

def _load_registered_from_sheets(
    applications_sheet_id: str,
    term_id: str = "",
) -> list[dict]:
    """Sheets에서 유형='수강' AND 처리상태='등록완료'인 행 로드.

    DB 모드: 회원관리 파일(MEMBERS_SHEET_ID)의 '신청기록' 탭에서 읽기 + term_id 필터.
    Sheets 모드: 회차별 '신청서' 탭에서 읽기.
    """
    from app.config import MEMBERS_SHEET_ID

    if USE_DB_SOT:
        sheet_id = MEMBERS_SHEET_ID
        tab = "신청기록"
    else:
        sheet_id = applications_sheet_id
        tab = "신청서"

    rows = read_sheet(sheet_id, f"{tab}!A1:L5000")
    if not rows or len(rows) < 2:
        return []
    header = rows[0]
    result = []
    for row in rows[1:]:
        data = dict(zip(header, row + [""] * (len(header) - len(row))))
        # DB 모드: 전 회차 누적이므로 현재 회차만 필터
        if USE_DB_SOT and term_id and data.get("회차", "").strip() != term_id:
            continue
        처리상태 = data.get("처리상태", "").strip()
        if data.get("유형") == "수강" and 처리상태 == "등록완료":
            result.append(data)
    return result


async def _load_registered_from_db(
    term_id: str,
    applications_sheet_id: str,
) -> list[dict]:
    """DB에서 수강 신청 로드 + Sheets에서 처리상태만 읽어서 머지.

    처리상태는 관리자가 Sheets에서 직접 편집하므로 Sheets가 SoT.
    DB 모드: 회원관리 파일(MEMBERS_SHEET_ID)의 '신청기록' 탭에서 처리상태 읽기.
    """
    from app.services import db
    from app.config import MEMBERS_SHEET_ID

    apps = await db.load_applications(term_id)

    # Sheets에서 처리상태 컬럼만 읽기
    if USE_DB_SOT:
        sheet_id = MEMBERS_SHEET_ID
        tab = "신청기록"
    else:
        sheet_id = applications_sheet_id
        tab = "신청서"

    rows = read_sheet(sheet_id, f"{tab}!A1:L5000")
    status_map: dict[tuple, str] = {}
    if rows and len(rows) >= 2:
        header = rows[0]
        for row in rows[1:]:
            data = dict(zip(header, row + [""] * (len(header) - len(row))))
            # DB 모드: 전 회차 누적이므로 현재 회차만 필터
            if USE_DB_SOT and term_id and data.get("회차", "").strip() != term_id:
                continue
            key = (data.get("이름ID", ""), data.get("유형", ""), data.get("과목명", ""))
            status_map[key] = data.get("처리상태", "").strip()

    result = []
    for a in apps:
        if a.get("유형") != "수강":
            continue
        key = (a.get("이름ID", ""), a.get("유형", ""), a.get("과목명", ""))
        if status_map.get(key) == "등록완료":
            result.append(a)
    return result


async def load_registered_students(
    applications_sheet_id: str,
    term_id: str = "",
) -> list[dict]:
    """처리상태='등록완료'인 수강생 로드.

    DB 모드: DB에서 applications 읽기 + Sheets에서 처리상태 머지.
    Sheets 모드: Sheets에서 전체 읽기.
    """
    if USE_DB_SOT and term_id:
        try:
            return await _load_registered_from_db(term_id, applications_sheet_id)
        except Exception as e:
            logger.error("DB read failed, falling back to Sheets: %s", e)

    return _load_registered_from_sheets(applications_sheet_id, term_id=term_id)


# ============================================= 출석부 시트 생성 =====

async def create_attendance_sheet(
    term_id: str,
    term_folder_id: str,
    applications_sheet_id: str,
) -> dict:
    """출석부 Google Sheets + 과목별 인쇄용 PDF 생성.

    1. 신청서에서 등록완료 수강생 로드
    2. 과목별 그룹핑
    3. 출석부 폴더 생성 (term_folder_id/출석부/)
    4. 출석부 시트 생성
       - 탭1 수강생: 이름ID, 이름, 과목명, 출석률(빈칸)
       - 탭N 과목명: 이름, 1~12회차 (헤더+빈 셀)
    5. 과목별 PDF 생성 + Drive 업로드

    Returns:
        {
            "spreadsheet_id": str,
            "spreadsheet_url": str,
            "attendance_folder_url": str,
            "courses": list[str],
            "total_students": int,
            "pdf_urls": {course_name: url or None},
        }
    """
    registered = await load_registered_students(
        applications_sheet_id, term_id=term_id,
    )

    if not registered:
        raise ValueError(
            "처리상태가 '등록완료'인 수강생이 없습니다. "
            "입금 대조 후 배움숲에서 등록 처리를 완료하고 신청기록 시트에서 처리상태를 '등록완료'로 설정해주세요."
        )

    # 과목별 그룹핑
    courses: dict[str, list] = {}
    for s in registered:
        course = s.get("과목명", "")
        if not course:
            continue
        if course not in courses:
            courses[course] = []
        courses[course].append(s)

    course_names = sorted(courses.keys())

    # 출석부 폴더 생성
    attendance_folder = find_or_create_folder(term_folder_id, "출석부")
    attendance_folder_id = attendance_folder["id"]
    attendance_folder_url = (
        f"https://drive.google.com/drive/folders/{attendance_folder_id}"
    )

    # 출석부 Sheets 생성
    drive = get_drive_service()
    sheets_svc = get_sheets_service()

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

    # 탭 생성: 수강생 탭 + 과목별 탭
    tab_titles = ["수강생"] + course_names
    add_requests = [
        {"addSheet": {"properties": {"title": title, "index": i}}}
        for i, title in enumerate(tab_titles)
    ]
    sheets_svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": add_requests},
    ).execute()

    # 기본 Sheet1 삭제
    meta = sheets_svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == "Sheet1":
            sheets_svc.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"deleteSheet": {
                    "sheetId": sheet["properties"]["sheetId"]
                }}]},
            ).execute()
            break

    # 탭1: 수강생 탭 데이터 입력
    student_header = ["이름ID", "이름", "과목명", "출석률"]
    student_rows = [student_header]
    for course_name in course_names:
        for s in courses[course_name]:
            student_rows.append([
                s.get("이름ID", ""),
                s.get("이름", ""),
                course_name,
                "",  # 출석률: 종강 처리 시 채워짐
            ])
    write_sheet(spreadsheet_id, "수강생!A1", student_rows)

    # 탭2+: 과목별 탭 데이터 입력
    # 컬럼: 이름 | 1회차 ~ 12회차 (이름ID, 출석률 없음)
    course_header = ["이름"] + [f"{i}회차" for i in range(1, MAX_SESSIONS + 1)]

    for course_name in course_names:
        students = courses[course_name]
        data_rows = [course_header]
        for s in students:
            row = [s.get("이름", "")] + [""] * MAX_SESSIONS
            data_rows.append(row)
        write_sheet(spreadsheet_id, f"{course_name}!A1", data_rows)

    # 모든 탭에 BasicFilter 설정
    meta_final = sheets_svc.spreadsheets().get(
        spreadsheetId=spreadsheet_id
    ).execute()
    sheet_id_map = {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in meta_final.get("sheets", [])
    }

    filter_requests = []
    # 수강생 탭: 4컬럼
    if "수강생" in sheet_id_map:
        filter_requests.append({"setBasicFilter": {"filter": {"range": {
            "sheetId": sheet_id_map["수강생"],
            "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": 4,
        }}}})
    # 과목별 탭: 이름(1) + 12회차 = 13컬럼
    for course_name in course_names:
        sid = sheet_id_map.get(course_name)
        if sid is not None:
            filter_requests.append({"setBasicFilter": {"filter": {"range": {
                "sheetId": sid,
                "startRowIndex": 0, "startColumnIndex": 0,
                "endColumnIndex": 1 + MAX_SESSIONS,
            }}}})

    if filter_requests:
        sheets_svc.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": filter_requests},
        ).execute()

    # 과목별 PDF 생성 + Drive 업로드
    pdf_urls: dict[str, str | None] = {}
    for course_name in course_names:
        try:
            students = courses[course_name]
            pdf_bytes = _generate_attendance_pdf(term_id, course_name, students)
            pdf_url = _save_pdf_to_drive(
                pdf_bytes, term_id, course_name, attendance_folder_id
            )
            pdf_urls[course_name] = pdf_url
        except Exception:
            pdf_urls[course_name] = None

    return {
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_url": spreadsheet_url,
        "attendance_folder_url": attendance_folder_url,
        "courses": course_names,
        "total_students": sum(len(v) for v in courses.values()),
        "pdf_urls": pdf_urls,
    }


# ================================================= PDF 생성 =====

def _generate_attendance_pdf(
    term_id: str,
    course_name: str,
    students: list[dict],
) -> bytes:
    """과목별 출석부 PDF 생성.

    레이아웃:
    - A4 가로(landscape): 841.9 x 595.3pt
    - 좌우 여백: 30pt, 상단: 30pt, 하단: 50pt (페이지번호 공간)
    - 컬럼: 이름(65pt) | 1~12회차(각 ~59pt)
    - 행 높이: 헤더 30pt, 데이터 26pt
    - 1페이지 약 17명
    - 2페이지 이상 시 상단 타이틀 + 헤더 반복
    """
    font_name, font_bold = _register_korean_font()

    PAGE_W, PAGE_H = landscape(A4)
    MARGIN = 30.0
    USABLE_W = PAGE_W - 2 * MARGIN
    NAME_COL_W = 65.0
    SESSION_COL_W = (USABLE_W - NAME_COL_W) / MAX_SESSIONS
    HEADER_ROW_H = 30.0
    DATA_ROW_H = 26.0

    ROWS_PER_PAGE = int((PAGE_H - 140) / DATA_ROW_H)

    col_widths = [NAME_COL_W] + [SESSION_COL_W] * MAX_SESSIONS
    header_row = ["이름"] + [f"{i}회차" for i in range(1, MAX_SESSIONS + 1)]
    data_rows = [[s.get("이름", "")] + [""] * MAX_SESSIONS for s in students]

    # 페이지 분할
    pages: list[list] = []
    for i in range(0, max(1, len(data_rows)), ROWS_PER_PAGE):
        pages.append(data_rows[i: i + ROWS_PER_PAGE])
    total_pages = len(pages)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN + 20,
    )

    title_style = ParagraphStyle(
        "title",
        fontName=font_bold,
        fontSize=14,
        leading=20,
        spaceAfter=8,
    )
    pagenum_style = ParagraphStyle(
        "pagenum",
        fontName=font_name,
        fontSize=10,
        alignment=2,  # RIGHT
        spaceBefore=6,
    )

    tbl_style = TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  colors.HexColor("#E8E8E8")),
        ("FONTNAME",       (0, 0), (-1, 0),  font_bold),
        ("FONTSIZE",       (0, 0), (-1, 0),  12),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8F8F8")]),
        ("FONTNAME",       (0, 1), (-1, -1), font_name),
        ("FONTSIZE",       (0, 1), (-1, -1), 12),
        ("ALIGN",          (0, 0), (0, -1),  "LEFT"),
        ("ALIGN",          (1, 0), (-1, -1), "CENTER"),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",    (0, 0), (0, -1),  8),
        ("RIGHTPADDING",   (0, 0), (0, -1),  4),
        ("GRID",           (0, 0), (-1, -1), 0.5, colors.HexColor("#AAAAAA")),
        ("LINEBELOW",      (0, 0), (-1, 0),  1.0, colors.HexColor("#666666")),
    ])

    story = []
    title_text = f"{course_name}  \u2014  {term_id} 출석부"

    for page_idx, page_rows in enumerate(pages):
        story.append(Paragraph(title_text, title_style))

        table_data = [header_row] + page_rows
        row_heights = [HEADER_ROW_H] + [DATA_ROW_H] * len(page_rows)

        tbl = Table(table_data, colWidths=col_widths, rowHeights=row_heights)
        tbl.setStyle(tbl_style)
        story.append(tbl)

        story.append(Paragraph(
            f"{page_idx + 1} / {total_pages}", pagenum_style
        ))

        if page_idx < len(pages) - 1:
            story.append(PageBreak())

    doc.build(story)
    return buf.getvalue()


def _save_pdf_to_drive(
    pdf_bytes: bytes,
    term_id: str,
    course_name: str,
    folder_id: str,
) -> str:
    """PDF bytes를 Google Drive 폴더에 업로드.

    파일명: {term_id}_{course_name}_출석부.pdf
    Returns: webViewLink
    """
    from googleapiclient.http import MediaIoBaseUpload

    drive = get_drive_service()
    safe_course = (
        course_name.replace("/", "_").replace("(", "").replace(")", "")
    )
    filename = f"{term_id}_{safe_course}_출석부.pdf"

    file_metadata = {
        "name": filename,
        "parents": [folder_id],
        "mimeType": "application/pdf",
    }
    media = MediaIoBaseUpload(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        resumable=False,
    )
    file = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True,
    ).execute()
    return file.get("webViewLink", "")

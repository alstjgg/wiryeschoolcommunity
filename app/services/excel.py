"""Excel 파일 파싱 — 입금내역(.xls/.xlsx) 및 수강생 명단"""

import io
from openpyxl import load_workbook


def parse_bank_statement(file_bytes: bytes) -> list[dict]:
    """입금내역 파일 파싱 → 입금 거래 리스트 반환

    포맷:
    - Row 1~6: 메타정보 (스킵)
    - Row 7: 헤더 (거래일시 | 적요 | 비고 | 의뢰인/수취인 | 입금 | 출금 | ...)
    - Row 8+: 거래 데이터
    - 마지막 행: 합계 (스킵)
    """
    # .xls (OLE2) → xlrd, .xlsx → openpyxl
    try:
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
    except Exception:
        # openpyxl 실패 → xlrd로 시도 (.xls 포맷)
        import xlrd
        xls_wb = xlrd.open_workbook(file_contents=file_bytes)
        xls_ws = xls_wb.sheet_by_index(0)
        rows = [xls_ws.row_values(i) for i in range(xls_ws.nrows)]

    if len(rows) < 8:
        return []

    # Row 7 (index 6) = 헤더, Row 8+ = 데이터, 마지막 행 = 합계 제외
    data_rows = rows[7:-1]  # index 7부터 마지막 전까지

    transactions = []
    for row in data_rows:
        if len(row) < 5:
            continue

        거래일시 = str(row[0]).strip() if row[0] else ""
        적요 = str(row[1]).strip() if row[1] else ""
        의뢰인 = str(row[3]).strip() if row[3] else ""

        # 입금액 파싱
        raw_amount = row[4]
        if raw_amount is None or str(raw_amount).strip() == "":
            continue
        try:
            입금 = int(float(str(raw_amount).replace(",", "")))
        except (ValueError, TypeError):
            continue

        if 입금 <= 0:
            continue

        transactions.append({
            "거래일시": 거래일시,
            "적요": 적요,
            "의뢰인": 의뢰인,
            "입금": 입금,
        })

    return transactions


def parse_student_list(file_bytes: bytes, filename: str = "") -> list[dict]:
    """수강생 명단 .xlsx 파싱 → 학생 리스트 반환

    포맷: 번호 | 강좌명 | 이름 | 휴대전화 | 성별 | 나이 | 행정동
    ID = 이름 + 전화번호 뒤 4자리
    """
    wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if len(rows) < 2:
        return []

    students = []
    for row in rows[1:]:  # 헤더 스킵
        if len(row) < 4:
            continue

        이름 = str(row[2]).strip() if row[2] else ""
        강좌명 = str(row[1]).strip() if row[1] else ""
        전화번호 = str(row[3]).strip() if row[3] else ""

        if not 이름:
            continue

        # ID 생성: 이름 + 전화번호 뒤 4자리
        phone_suffix = 전화번호.replace("-", "").replace(" ", "")[-4:] if 전화번호 else ""
        student_id = f"{이름}{phone_suffix}" if phone_suffix else 이름

        성별 = str(row[4]).strip() if len(row) > 4 and row[4] else ""
        나이 = str(row[5]).strip() if len(row) > 5 and row[5] else ""
        행정동 = str(row[6]).strip() if len(row) > 6 and row[6] else ""

        students.append({
            "이름ID": student_id,
            "이름": 이름,
            "강좌명": 강좌명,
            "전화번호": 전화번호,
            "성별": 성별,
            "나이": 나이,
            "주소": 행정동,
        })

    return students

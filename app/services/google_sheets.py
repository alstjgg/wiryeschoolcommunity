"""Google Sheets API 래퍼"""

from app.services.google_auth import get_sheets_service


def read_sheet(spreadsheet_id: str, range_name: str) -> list[list[str]]:
    """시트에서 데이터 읽기. 빈 시트면 빈 리스트 반환."""
    service = get_sheets_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name)
        .execute()
    )
    return result.get("values", [])


def write_sheet(
    spreadsheet_id: str, range_name: str, values: list[list]
) -> dict:
    """시트에 데이터 쓰기"""
    service = get_sheets_service()
    return (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body={"values": values},
        )
        .execute()
    )


def append_sheet(
    spreadsheet_id: str, range_name: str, values: list[list]
) -> dict:
    """시트에 행 추가"""
    service = get_sheets_service()
    return (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        )
        .execute()
    )


def add_sheet_tab(spreadsheet_id: str, title: str) -> dict:
    """새 시트 탭 추가"""
    service = get_sheets_service()
    return (
        service.spreadsheets()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [{"addSheet": {"properties": {"title": title}}}]
            },
        )
        .execute()
    )

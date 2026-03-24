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


def clear_range(spreadsheet_id: str, range_name: str) -> None:
    """시트 범위의 값만 삭제 (서식/드롭다운/보호 유지)."""
    service = get_sheets_service()
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=range_name, body={},
    ).execute()


def get_tab_gids(spreadsheet_id: str) -> dict[str, int]:
    """스프레드시트의 탭명 → sheetId(gid) 매핑을 반환."""
    service = get_sheets_service()
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties.title,sheets.properties.sheetId",
    ).execute()
    return {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in meta.get("sheets", [])
    }


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

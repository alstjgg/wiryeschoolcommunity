"""Google Drive API 래퍼"""

from app.services.google_auth import get_drive_service
from app.config import OPERATIONS_FOLDER_ID


def find_file(name: str, parent_id: str | None = None) -> dict | None:
    """이름으로 파일/폴더 검색. 첫 번째 결과 반환."""
    service = get_drive_service()
    query = f"name = '{name}' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name, mimeType)",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = results.get("files", [])
    return files[0] if files else None


def list_files(parent_id: str, mime_type: str | None = None) -> list[dict]:
    """폴더 내 파일 목록 조회"""
    service = get_drive_service()
    query = f"'{parent_id}' in parents and trashed = false"
    if mime_type:
        query += f" and mimeType = '{mime_type}'"

    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name, mimeType)",
            pageSize=100,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return results.get("files", [])


def find_folder_by_path(path_parts: list[str]) -> str | None:
    """경로 부분 리스트로 폴더 ID를 순차 탐색. 예: ['위례인생학교 자료실', '학사운영(연도별)', '2026']"""
    current_parent = None
    for part in path_parts:
        folder = find_file(part, parent_id=current_parent)
        if not folder:
            return None
        current_parent = folder["id"]
    return current_parent


def find_term_folder(term_id: str) -> dict | None:
    """Drive에서 회차 폴더를 동적 탐색.

    경로: 02_학사운영 → {year} → {term_id}로 시작하는 폴더
    예: term_id="2026-1" → "2026" 폴더 내 "2026-1_겨울학기" 폴더 반환.
    폴더명 구분자(space/underscore)는 startswith 검색에 영향 없음.
    """
    year = term_id.split("-")[0]  # "2026"

    # Step 1: 학사운영 → 연도 폴더
    year_folder = find_file(year, parent_id=OPERATIONS_FOLDER_ID)
    if not year_folder:
        return None

    # Step 2: 연도 폴더 → term_id로 시작하는 폴더
    items = list_files(year_folder["id"])
    for item in items:
        if item["name"].startswith(term_id) and "folder" in item["mimeType"]:
            return item
    return None


def find_file_by_prefix(parent_id: str, prefix: str) -> dict | None:
    """폴더 내에서 이름이 prefix로 시작하는 첫 번째 파일 반환."""
    items = list_files(parent_id)
    for item in items:
        if item["name"].startswith(prefix):
            return item
    return None


def find_spreadsheet_by_name(parent_id: str, name: str) -> dict | None:
    """폴더 내에서 이름이 정확히 일치하는 Google Sheets 파일 반환."""
    items = list_files(parent_id)
    for item in items:
        if item["name"] == name and "spreadsheet" in item["mimeType"]:
            return item
    return None


def find_or_create_folder(parent_id: str, name: str) -> dict:
    """폴더 내에서 이름으로 하위 폴더를 찾고, 없으면 생성."""
    existing = find_file(name, parent_id=parent_id)
    if existing and "folder" in existing["mimeType"]:
        return existing

    service = get_drive_service()
    file_metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(
        body=file_metadata, fields="id, name, mimeType", supportsAllDrives=True
    ).execute()
    return folder

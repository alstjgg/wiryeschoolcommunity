"""Google Drive API 래퍼"""

from app.services.google_auth import get_drive_service


def find_file(name: str, parent_id: str | None = None) -> dict | None:
    """이름으로 파일/폴더 검색. 첫 번째 결과 반환."""
    service = get_drive_service()
    query = f"name = '{name}' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = (
        service.files()
        .list(q=query, fields="files(id, name, mimeType)", pageSize=1)
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
        .list(q=query, fields="files(id, name, mimeType)", pageSize=100)
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

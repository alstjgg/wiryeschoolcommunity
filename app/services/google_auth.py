"""Google API 인증 — Service Account + Domain-wide Delegation"""

import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config import GOOGLE_SA_KEY_PATH, GOOGLE_DELEGATED_USER, GOOGLE_SCOPES

_credentials = None
_sheets_service = None
_drive_service = None


def _get_credentials():
    global _credentials
    if _credentials is None:
        # GOOGLE_SA_KEY_JSON 환경변수가 있으면 우선 사용 (Fly.io 등 PaaS)
        sa_key_json = os.environ.get("GOOGLE_SA_KEY_JSON")
        if sa_key_json:
            info = json.loads(sa_key_json)
            _credentials = service_account.Credentials.from_service_account_info(
                info,
                scopes=GOOGLE_SCOPES,
                subject=GOOGLE_DELEGATED_USER,
            )
        else:
            _credentials = service_account.Credentials.from_service_account_file(
                GOOGLE_SA_KEY_PATH,
                scopes=GOOGLE_SCOPES,
                subject=GOOGLE_DELEGATED_USER,
            )
    return _credentials


def get_sheets_service():
    """Google Sheets API v4 서비스 객체 반환 (싱글톤)"""
    global _sheets_service
    if _sheets_service is None:
        _sheets_service = build("sheets", "v4", credentials=_get_credentials())
    return _sheets_service


def get_drive_service():
    """Google Drive API v3 서비스 객체 반환 (싱글톤)"""
    global _drive_service
    if _drive_service is None:
        _drive_service = build("drive", "v3", credentials=_get_credentials())
    return _drive_service

"""n8n 웹훅 트리거 — DB→Sheets 동기화 요청

챗봇이 DB에 데이터를 쓴 뒤, n8n에 fire-and-forget HTTP POST를 보내
Sheets를 즉시 업데이트한다.

N8N_WEBHOOK_URL 환경변수가 없으면 아무 동작 안 함 (n8n 미배포 시).
"""

import logging

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest

from app.config import N8N_WEBHOOK_URL, USE_DB_SOT
from app.services.google_auth import _get_credentials

logger = logging.getLogger(__name__)

# n8n 웹훅 엔드포인트 경로
_SYNC_PATH = "/webhook/sheets-sync"


async def trigger_sheets_sync(
    sync_type: str,
    payload: dict | None = None,
) -> None:
    """n8n에 DB→Sheets 동기화 웹훅을 fire-and-forget으로 전송.

    Args:
        sync_type: 동기화 유형
            - "applications": 신청서 시트 업데이트
            - "members": 회원목록/회원기록 업데이트
            - "graduation": 종강 처리 (회원+수강기록+출석부 전체)
            - "attendance": 출석부 특정 과목 업데이트
        payload: 추가 데이터 (예: {"term_id": "2026-1", "course_name": "오카리나"})
    """
    if not USE_DB_SOT or not N8N_WEBHOOK_URL:
        return

    url = N8N_WEBHOOK_URL.rstrip("/") + _SYNC_PATH

    # Get fresh Google access token for n8n to use in Sheets API calls
    creds = _get_credentials()
    if not creds.valid:
        creds.refresh(GoogleAuthRequest())
    access_token = creds.token

    body = {"type": sync_type, "access_token": access_token, **(payload or {})}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json=body)
            logger.info(
                "n8n sync triggered: type=%s status=%d", sync_type, resp.status_code,
            )
    except Exception as e:
        # 동기화 실패는 치명적이지 않음 — 일일 동기화(Sync-1)가 보완
        logger.warning("n8n sync trigger failed (non-critical): %s", e)

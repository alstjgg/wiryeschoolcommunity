"""환경 변수 및 비즈니스 상수 설정"""
import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_SA_KEY_PATH = os.environ.get("GOOGLE_SA_KEY_PATH", "sa-key.json")
GOOGLE_DELEGATED_USER = os.environ.get(
    "GOOGLE_DELEGATED_USER", "wirye@wiryeschoolcomunity.com"
)

# Google API Scopes
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

# 비즈니스 상수 (변동 가능 → 추후 시트에서 읽어오는 구조로 전환 가능)
TUITION_FEE = 20000  # 수강료
MEMBERSHIP_FEE = 10000  # 가입비
FULL_MEMBERSHIP_FEE = 120000  # 정회원비
REFUND_DEADLINE_DAYS = 7  # 개강 후 환불 마감일
MAX_SESSIONS = 12  # 강좌당 최대 회차

SEMESTERS = {
    1: "1~3월",
    2: "4~6월",
    3: "7~9월",
    4: "10~12월",
}

# LLM 설정
LLM_MODEL = "claude-sonnet-4-20250514"

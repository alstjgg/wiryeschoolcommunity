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

# Google Drive/Sheets IDs
ROOT_FOLDER_ID = "1N24ROJ12BEDRrSmtezXdV70Pi3Np7sRm"
STUDENTS_SHEET_ID = "16vRPnHWX7AEd66xsxVSYAZrKNRtTdz_5DbjSYDo5RBY"
MEMBERS_SHEET_ID = "193r34mtLHd0-oX7MKJOWq1Ane9iBfbBZB5yYf78R3Bo"
RECORDS_SHEET_ID = "1cKolq6Mr-5u65nQDeMq8z4DsFWpHTLVthkAvyt4Rb6s"
ATTENDANCE_FOLDER_ID = "1i-sixwrwPU_XxYhOwhDvaIqfvCxICWB8"

# LLM 설정
LLM_MODEL = "claude-sonnet-4-20250514"

# 강좌 키워드 매핑 (약어/비정형 → 정식 강좌명)
COURSE_KEYWORDS = {
    "경제기초": "경제해설(기초)",
    "경제심화": "경제해설(심화)",
    "경제뉴스": "경제해설(기초)",
    "경제": "금융과경제",
    "영어": "All In One 영어",
    "올인원": "All In One 영어",
    "allinone": "All In One 영어",
    "오카리나": "기초탄탄오카리나",
    "요들": "요들송배우기",
    "우쿨": "우쿨렐레중급",
    "우크렐": "우쿨렐레중급",
    "명상": "나마음챙김명상",
    "마음챙김": "나마음챙김명상",
    "심리": "심리상담교실TA",
    "심리상담": "심리상담교실TA",
    "어반": "어반스케치",
    "어빈": "어반스케치",
    "사진": "사진촬영기초",
    "한국화": "한국화",
    "수묵": "한국화",
    "여행영어": "스마트폰으로 배우는 여행영어",
    "스마트폰": "스마트폰으로 배우는 여행영어",
    "법률": "생활교양법률",
    "책": "책가끔은낭독",
    "낭독": "책가끔은낭독",
    "미술관": "미술관투어",
    "금융": "금융과경제",
}

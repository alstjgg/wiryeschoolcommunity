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

TERMS = {
    1: "겨울(1~3월)",
    2: "봄(4~6월)",
    3: "여름(7~9월)",
    4: "가을(10~12월)",
}

TERM_SEASONS = {1: "겨울", 2: "봄", 3: "여름", 4: "가을"}

# Google Drive/Sheets IDs (영속 리소스만 — 회차별 ID는 런타임에 동적 탐색)
ROOT_FOLDER_ID = "1N24ROJ12BEDRrSmtezXdV70Pi3Np7sRm"
OPERATIONS_FOLDER_ID = "19EazEwzYEgD2Wsocv6bU5K907vHRY6sK"  # 학사운영(연도별)
MEMBERS_SHEET_ID = "193r34mtLHd0-oX7MKJOWq1Ane9iBfbBZB5yYf78R3Bo"
RECORDS_SHEET_ID = "1cKolq6Mr-5u65nQDeMq8z4DsFWpHTLVthkAvyt4Rb6s"

# LLM 설정
LLM_MODEL = "claude-sonnet-4-20250514"

# 강좌 키워드 매핑 (약어/비정형 → 정식 강좌명, fuzzy matching 폴백용)
COURSE_KEYWORDS = {
    "경제기초": "경제뉴스로 배우는 경제해설(기초)",
    "경제심화": "경제뉴스로 배우는 경제해설(심화)",
    "경제뉴스기초": "경제뉴스로 배우는 경제해설(기초)",
    "경제뉴스심화": "경제뉴스로 배우는 경제해설(심화)",
    "경제뉴스": "경제뉴스로 배우는 경제해설(기초)",
    "경제해설기초": "경제뉴스로 배우는 경제해설(기초)",
    "경제해설심화": "경제뉴스로 배우는 경제해설(심화)",
    "금융": "금융과 경제",
    "경제": "금융과 경제",
    "오카리나": "기초탄탄 오카리나",
    "명상": "나, 마음챙김 명상",
    "마음챙김": "나, 마음챙김 명상",
    "영어": "다시 시작하는 All In One 영어",
    "올인원": "다시 시작하는 All In One 영어",
    "allinone": "다시 시작하는 All In One 영어",
    "라이프코칭": "라이프코칭상담",
    "코칭": "라이프코칭상담",
    "미술관": "미술관투어",
    "투어": "미술관투어",
    "사진": "사진촬영기초(스마트폰 활용)",
    "사진촬영": "사진촬영기초(스마트폰 활용)",
    "스마트폰사진": "사진촬영기초(스마트폰 활용)",
    "법률": "생활교양법률",
    "교양법률": "생활교양법률",
    "여행영어": "스마트폰으로 배우는 여행영어",
    "스마트폰영어": "스마트폰으로 배우는 여행영어",
    "심리": "심리상담교실(TA)",
    "심리상담": "심리상담교실(TA)",
    "어반": "어반스케치",
    "어빈": "어반스케치",
    "스케치": "어반스케치",
    "요들": "요들송배우기",
    "요들송": "요들송배우기",
    "우쿨": "우쿨렐레 중급",
    "우크렐": "우쿨렐레 중급",
    "우쿨렐레": "우쿨렐레 중급",
    "책": "책, 가끔은 낭독",
    "낭독": "책, 가끔은 낭독",
}

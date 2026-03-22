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
ROOT_FOLDER_ID = "0AANInBeWsB7dUk9PVA"
OPERATIONS_FOLDER_ID = "1WuqNFt-g5qhnY1nMk0a8dsowZHKQVRMm"  # 02 학사운영
MEMBERS_FOLDER_ID = "12xm3vG4w5nOPTwoWgmyGCpz939KvJ93e"  # 03 회원과 강사/회원(회원명단/가입서/정회원) — 회원관리·수강기록 소재
MEMBERS_SHEET_ID = "193r34mtLHd0-oX7MKJOWq1Ane9iBfbBZB5yYf78R3Bo"  # 회원관리 시트 (3탭: 회원목록, 회원기록, 수강기록)
# 가입 신청서 폴더 위치
# ASIS: 관리자 개인 드라이브 (현재 활성)
# TOBE: 공유 드라이브 (이전 완료 후 주석 전환)
#
# 전환 방법:
#   1. 관리자가 공유 드라이브로 파일 이전 완료
#   2. 아래 ACTIVE 값을 TOBE ID로 교체
#   3. 배포

MEMBER_SIGNUP_FOLDER_ID_ASIS = "1wbxBPC6SRHaUnPRHbevYCy9tUKcpaSqr"       # 개인 드라이브
MEMBER_SIGNUP_FOLDER_ID_TOBE = "10ZL8rD9j7OyyZOihfyJ6GRTzmBrTBgWe"       # 공유 드라이브
MEMBER_SIGNUP_FOLDER_ID = MEMBER_SIGNUP_FOLDER_ID_ASIS                     # ← 현재 활성

FULLMEMBER_SIGNUP_FOLDER_ID_ASIS = "1--3kKT19bGMl4uvA_qYRtWZss1DOA6So"   # 개인 드라이브
FULLMEMBER_SIGNUP_FOLDER_ID_TOBE = "17tsWfYwIRgHHcT1DQEj8Sqa4ys6pe0Vy"   # 공유 드라이브
FULLMEMBER_SIGNUP_FOLDER_ID = FULLMEMBER_SIGNUP_FOLDER_ID_ASIS             # ← 현재 활성

# 회원관리 파일 내 탭명 (3탭 구조)
MEMBERS_TAB = "회원목록"          # 현재 상태 스냅샷
MEMBER_RECORDS_TAB = "회원기록"   # 등급 변경 이력
COURSE_RECORDS_TAB = "수강기록"   # 수강 이력

# 가입 신청서 응답 시트 탭명 (구글폼 기본값)
SIGNUP_SHEET_TAB = "Form Responses 1"

# 회원기록 헤더
MEMBER_RECORD_HEADER = ["이름ID", "이름", "변경일시", "변경전등급", "변경후등급", "사유", "관련회차"]

# 수강기록 헤더
COURSE_RECORD_HEADER = ["이름ID", "회차", "과목명", "출석률"]

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
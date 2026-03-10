"""입금 매칭 로직 — 이름/강좌 추출 + 규칙 기반 매칭"""

import re
from app.config import TUITION_FEE, MEMBERSHIP_FEE, FULL_MEMBERSHIP_FEE, COURSE_KEYWORDS

# 카카오페이/토스 의뢰인명
THIRD_PARTY_SENDERS = {"(주)카카오페이", "(주)비바리퍼블리카", "카카오페이", "비바리퍼블리카", "토스"}

# 구분자 패턴 (이름과 강좌 사이)
SEPARATOR_PATTERN = re.compile(r"[_,\-./\s]")


def extract_name_from_sender(의뢰인: str) -> str:
    """의뢰인 컬럼에서 이름 추출 (은행명 등 제거)"""
    name = 의뢰인.strip()
    # 괄호 안 은행명 제거: "홍길동(국민)" → "홍길동"
    name = re.sub(r"\(.*?\)$", "", name).strip()
    return name


def is_third_party(의뢰인: str) -> bool:
    """카카오페이/토스 등 제3자 결제 여부"""
    return any(tp in 의뢰인 for tp in THIRD_PARTY_SENDERS)


def extract_name_from_memo(적요: str, student_names: list[str]) -> str | None:
    """적요에서 학생 이름 추출 — 학생 목록과 대조"""
    if not 적요:
        return None
    for name in sorted(student_names, key=len, reverse=True):
        if name in 적요:
            return name
    return None


def extract_course_hint(적요: str, course_names: list[str]) -> str | None:
    """적요에서 강좌명 추출 — 키워드 매핑 + 정식 강좌명 직접 매칭"""
    if not 적요:
        return None

    text = 적요.lower().strip()

    # 1. 정식 강좌명 직접 포함 여부 (긴 것부터)
    for course in sorted(course_names, key=len, reverse=True):
        if course.lower() in text or course in 적요:
            return course

    # 2. 키워드 매핑 (긴 키워드부터 매칭)
    for keyword in sorted(COURSE_KEYWORDS.keys(), key=len, reverse=True):
        if keyword in text:
            mapped = COURSE_KEYWORDS[keyword]
            # 매핑된 강좌가 실제 강좌 목록에 있는지 확인
            if mapped in course_names:
                return mapped
            # 부분 일치 시도
            for course in course_names:
                if mapped in course or keyword in course.lower():
                    return course

    return None


def detect_special_type(적요: str, amount: int) -> str | None:
    """특수 입금 유형 감지"""
    text = 적요 if 적요 else ""

    # 취소/대기 건
    if any(kw in text for kw in ["취소됨", "대기", "반환"]):
        return "취소"

    # 예금이자 등 소액
    if amount < 10000:
        return "소액"

    # 가입비
    if "가입" in text and amount == MEMBERSHIP_FEE:
        return "가입비"

    # 정회원비
    if amount == FULL_MEMBERSHIP_FEE:
        return "정회원"

    return None


def classify_amount(amount: int) -> str:
    """금액으로 입금 유형 분류"""
    if amount == MEMBERSHIP_FEE:
        return "가입비(1만)"
    elif amount == TUITION_FEE:
        return "수강료(2만)"
    elif amount == MEMBERSHIP_FEE + TUITION_FEE:
        return "수강료+가입비(3만)"
    elif amount == TUITION_FEE * 2:
        return "2과목(4만)"
    elif amount > TUITION_FEE * 2:
        return f"다과목/합산({amount // 10000}만)"
    else:
        return f"기타({amount:,}원)"


def match_transaction(
    tx: dict,
    students: list[dict],
    course_names: list[str],
) -> dict:
    """단일 거래를 학생과 매칭. 결과 dict 반환.

    매칭은 적요(memo)와 의뢰인(sender) 컬럼만 사용.
    """
    적요 = tx.get("적요", "")
    의뢰인 = tx.get("의뢰인", "")
    amount = tx.get("입금", 0)

    result = {
        "거래일시": tx.get("거래일시", ""),
        "적요": 적요,
        "의뢰인": 의뢰인,
        "입금": amount,
        "금액분류": classify_amount(amount),
        "매칭이름": None,
        "매칭강좌": None,
        "매칭ID": None,
        "상태": "❌미매칭",
        "메모": "",
    }

    # 1. 특수 유형 감지
    special = detect_special_type(적요, amount)
    if special == "소액":
        result["상태"] = "⏭️스킵"
        result["메모"] = "소액(예금이자 등)"
        return result
    if special == "취소":
        result["상태"] = "⏭️스킵"
        result["메모"] = "취소/대기 건"
        return result

    # 가입비 또는 정회원비 → 이름 매칭은 계속하되 ✅정상 불가
    force_review: str | None = None
    if special == "가입비":
        force_review = "가입비만 납부 — 수강료 별도 확인 필요"
    elif special == "정회원":
        force_review = "정회원비 납부 — 회원관리 등급 수동 업데이트 필요"

    # 2. 이름 추출 (적요 + 의뢰인만 사용)
    student_names = list({s["이름"] for s in students})

    if is_third_party(의뢰인):
        # 카카오페이/토스: 적요에서 이름 추출
        name = extract_name_from_memo(적요, student_names)
        result["메모"] = (result["메모"] + " 간편결제").strip()
    else:
        # 일반: 의뢰인에서 이름 추출, 없으면 적요에서 시도
        sender_name = extract_name_from_sender(의뢰인)
        if sender_name in student_names:
            name = sender_name
        else:
            name = extract_name_from_memo(적요, student_names)
            if not name:
                # 의뢰인 이름이 적요에 포함되어 있을 수도 있음
                name = extract_name_from_memo(sender_name, student_names)

    if not name:
        # 이름 매칭 실패
        result["상태"] = "🔶확인필요"
        result["메모"] = (result["메모"] + " 이름매칭실패").strip()
        return result

    result["매칭이름"] = name

    # 3. 강좌 추출 (적요에서만)
    course = extract_course_hint(적요, course_names)
    result["매칭강좌"] = course

    # 4. 해당 이름의 학생 찾기
    matched_students = [s for s in students if s["이름"] == name]

    if not matched_students:
        result["상태"] = "🔶확인필요"
        result["메모"] = (result["메모"] + " 수강생 미등록").strip()
        return result

    if len(matched_students) == 1:
        # 단일 매칭
        student = matched_students[0]
        result["매칭ID"] = student["이름ID"]
        result["매칭강좌"] = course or student["강좌명"]
        if force_review:
            result["상태"] = "🔶확인필요"
            result["메모"] = force_review
        else:
            result["상태"] = "✅정상"
        return result

    # 5. 동명이인 처리 — 강좌로 구분
    if course:
        course_matched = [s for s in matched_students if s["강좌명"] == course]
        if len(course_matched) == 1:
            result["매칭ID"] = course_matched[0]["이름ID"]
            result["매칭강좌"] = course
            if force_review:
                result["상태"] = "🔶확인필요"
                result["메모"] = force_review
            else:
                result["상태"] = "✅정상"
            return result

    # 동명이인 + 강좌 구분 불가
    result["상태"] = "🔶확인필요"
    result["메모"] = (result["메모"] + f" 동명이인({len(matched_students)}명)").strip()
    return result


def run_code_matching(
    transactions: list[dict],
    students: list[dict],
) -> tuple[list[dict], list[dict]]:
    """규칙 기반 매칭 실행. (매칭결과 전체, 미매칭 건) 반환."""
    course_names = list({s["강좌명"] for s in students})
    results = []
    unmatched = []

    for tx in transactions:
        result = match_transaction(tx, students, course_names)
        results.append(result)
        if result["상태"] in ("🔶확인필요", "❌미매칭"):
            unmatched.append(result)

    return results, unmatched

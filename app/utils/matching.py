"""입금 매칭 로직 — 적요 우선 이름 추출 + 금액 기반 판별 + 룰베이스 매칭

적요(B열)가 최우선, 의뢰인(D열)은 fallback.
COURSE_KEYWORDS 하드코딩 제거 — 이름을 적요에서 제거한 나머지를 강좌 힌트로 사용.
"""

import re
from app.config import TUITION_FEE, MEMBERSHIP_FEE, FULL_MEMBERSHIP_FEE

# 카카오페이/토스 의뢰인명
THIRD_PARTY_SENDERS = {
    "(주)카카오페이", "(주)비바리퍼블리카",
    "카카오페이", "비바리퍼블리카", "토스",
}

# 가입비 키워드
_MEMBERSHIP_KEYWORDS = {"가입", "가입비", "회비", "회원가입", "회원비", "신규", "신규회비", "신규회원"}
# 정회원 키워드
_FULLMEMBER_KEYWORDS = {"정회원", "연회비", "연회원", "정회원비"}


def is_third_party(의뢰인: str) -> bool:
    """카카오페이/토스 등 제3자 결제 여부"""
    return any(tp in 의뢰인 for tp in THIRD_PARTY_SENDERS)


def extract_name(
    적요: str, 의뢰인: str, student_names: list[str],
) -> str | None:
    """적요 우선으로 수강생 이름 추출. 의뢰인은 fallback.

    student_names는 긴 이름부터 정렬되어 전달되어야 함.
    """
    # Step 1: 적요에서 이름 찾기 (최우선)
    for name in student_names:
        if name in 적요:
            return name

    # Step 2: 의뢰인에서 fallback (핀테크 법인이 아닌 경우만)
    if not is_third_party(의뢰인):
        clean_payer = re.sub(r"\(.*?\)$", "", 의뢰인).strip()
        for name in student_names:
            if name == clean_payer or name in clean_payer:
                return name

    return None


def extract_course_hint(적요: str, matched_name: str) -> str:
    """적요에서 이름을 제거하고 남은 텍스트를 강좌 힌트로 반환.

    Examples:
        "김기춘경제뉴스로기초" → "경제뉴스로기초"
        "류동원_요들" → "요들"
        "마음챙김명상김영옥" → "마음챙김명상"
        "황용섭(경제)" → "경제"
    """
    hint = 적요.replace(matched_name, "").strip()
    hint = re.sub(r'^[_,\-./\s()+]+|[_,\-./\s()+]+$', '', hint)
    # 괄호 내용 추출: "황용섭(경제)" → 이름 제거 후 "(경제)" → 괄호 벗기기
    paren_match = re.search(r'\(([^)]+)\)', hint)
    if paren_match:
        hint = paren_match.group(1)
    return hint


def fuzzy_course_match(
    hint: str, course_names: list[str],
) -> str | None:
    """강좌 힌트를 과목 목록과 fuzzy 매칭.

    정확 포함, 부분 포함, 한글 자소 유사도 순으로 시도.
    """
    if not hint:
        return None

    hint_lower = hint.lower()

    # 1. 힌트가 과목명에 포함되는 경우 (긴 과목명부터)
    for course in sorted(course_names, key=len, reverse=True):
        if hint_lower in course.lower():
            return course

    # 2. 과목명의 핵심 부분이 힌트에 포함되는 경우
    for course in course_names:
        # 괄호 안 내용 추출: "경제뉴스로 배우는 경제해설(기초)" → "기초"
        paren = re.search(r'\(([^)]+)\)', course)
        if paren and paren.group(1).lower() in hint_lower:
            # 괄호 바깥도 일부 매칭되어야 함
            base = course[:course.index('(')].strip().lower()
            if any(part in hint_lower for part in base.split() if len(part) >= 2):
                return course

    # 3. 짧은 힌트(2글자+)가 과목명 시작 부분과 매칭
    if len(hint) >= 2:
        for course in course_names:
            if course.lower().startswith(hint_lower):
                return course

    return None


def classify_by_amount(
    amount: int,
    num_courses: int,
    적요: str = "",
) -> dict:
    """금액과 신청 과목 수로 입금 유형 판별.

    Returns: {
        "type": str,
        "paid_courses": int,
        "includes_membership": bool,
        "auto_confirmable": bool,
    }
    """
    text = 적요.lower() if 적요 else ""
    has_membership_kw = any(kw in text for kw in _MEMBERSHIP_KEYWORDS)
    has_fullmember_kw = any(kw in text for kw in _FULLMEMBER_KEYWORDS)

    result = {
        "type": "unknown",
        "paid_courses": 0,
        "includes_membership": False,
        "auto_confirmable": False,
    }

    # 소액 (예금이자 등)
    if amount < MEMBERSHIP_FEE:
        result["type"] = "small_amount"
        return result

    # 정회원비 + 가입비 합산 (13만)
    if amount == MEMBERSHIP_FEE + FULL_MEMBERSHIP_FEE:
        result["type"] = "membership_plus_fullmember"
        result["includes_membership"] = True
        result["auto_confirmable"] = True
        return result

    # 정회원비 (금액 또는 키워드)
    if amount == FULL_MEMBERSHIP_FEE or (has_fullmember_kw and amount >= FULL_MEMBERSHIP_FEE):
        result["type"] = "fullmember"
        result["auto_confirmable"] = True
        return result

    # 가입비 단독
    if amount == MEMBERSHIP_FEE and has_membership_kw:
        result["type"] = "membership_fee"
        result["includes_membership"] = True
        result["auto_confirmable"] = True
        return result

    # 수강료 계산
    tuition_amount = amount
    if amount % TUITION_FEE != 0 and (amount - MEMBERSHIP_FEE) % TUITION_FEE == 0:
        # 가입비 포함 합산
        tuition_amount = amount - MEMBERSHIP_FEE
        result["includes_membership"] = True

    if tuition_amount % TUITION_FEE == 0:
        paid = tuition_amount // TUITION_FEE
        result["paid_courses"] = paid

        if paid == 1 and num_courses == 1:
            result["type"] = "single_course"
            result["auto_confirmable"] = True
        elif paid == num_courses:
            result["type"] = "all_courses"
            result["auto_confirmable"] = True
        elif paid < num_courses:
            result["type"] = "partial_courses"
            result["auto_confirmable"] = False
        else:
            result["type"] = "amount_mismatch"
            result["auto_confirmable"] = False
    else:
        # 가입비 단독 (키워드 없이 1만원)
        if amount == MEMBERSHIP_FEE:
            result["type"] = "membership_fee"
            result["includes_membership"] = True
            result["auto_confirmable"] = True
        else:
            result["type"] = "amount_mismatch"

    return result


def match_transaction(
    tx: dict,
    students: list[dict],
    student_names: list[str],
    course_names: list[str],
) -> dict:
    """단일 거래를 학생과 매칭. 결과 dict 반환."""
    적요 = tx.get("적요", "")
    의뢰인 = tx.get("의뢰인", "")
    amount = tx.get("입금", 0)

    result = {
        "거래일시": tx.get("거래일시", ""),
        "적요": 적요,
        "의뢰인": 의뢰인,
        "입금": amount,
        "매칭이름": None,
        "매칭강좌": None,
        "매칭ID": None,
        "상태": "❌미매칭",
        "메모": "",
    }

    # 1. 소액/취소 스킵
    if amount < MEMBERSHIP_FEE:
        result["상태"] = "⏭️스킵"
        result["메모"] = "소액(예금이자 등)"
        return result

    if any(kw in 적요 for kw in ["취소됨", "대기", "반환"]):
        result["상태"] = "⏭️스킵"
        result["메모"] = "취소/대기 건"
        return result

    # 2. 이름 추출 (적요 우선)
    name = extract_name(적요, 의뢰인, student_names)

    if not name:
        result["상태"] = "🔶확인필요"
        result["메모"] = "수강생 목록에서 이름을 찾지 못함"
        return result

    result["매칭이름"] = name

    # 대리입금 감지: 적요에서 이름을 찾았고, 의뢰인이 다른 사람인 경우
    if not is_third_party(의뢰인):
        clean_payer = re.sub(r"\(.*?\)$", "", 의뢰인).strip()
        if clean_payer and clean_payer != name and name in 적요:
            result["메모"] = f"대리입금 추정 (의뢰인: {clean_payer})"

    # 3. 해당 이름의 학생 찾기
    matched_students = [s for s in students if s["이름"] == name]
    if not matched_students:
        result["상태"] = "🔶확인필요"
        result["메모"] = (result["메모"] + " 수강생 미등록").strip()
        return result

    # 4. 금액 판별
    num_courses = len(matched_students)
    amount_info = classify_by_amount(amount, num_courses, 적요)

    # 가입비/정회원 유형 → cascade에서 등급 전환 처리
    if amount_info["type"] in ("membership_fee", "fullmember", "membership_plus_fullmember"):
        result["매칭ID"] = matched_students[0]["이름ID"]
        type_labels = {
            "membership_fee": f"가입비 입금 ({amount:,}원)",
            "fullmember": f"정회원비 입금 ({amount:,}원)",
            "membership_plus_fullmember": f"가입비+정회원비 합산 ({amount:,}원)",
        }
        type_map = {
            "membership_fee": "신규가입",
            "fullmember": "정회원",
            "membership_plus_fullmember": "신규가입",
        }
        result["메모"] = type_labels.get(amount_info["type"], amount_info["type"])
        result["_match_type"] = type_map[amount_info["type"]]
        result["_amount_type"] = amount_info["type"]

        # 동명이인 체크: 같은 이름이지만 다른 이름ID가 있는지 확인
        unique_ids = {s["이름ID"] for s in matched_students}
        if len(unique_ids) == 1:
            result["상태"] = "✅정상"
        else:
            # 진짜 동명이인 — 어느 사람인지 특정 불가
            result["상태"] = "🔶확인필요"
            result["메모"] += " (동명이인 — 수동 확인 필요)"
        return result

    # 5. 수강료 매칭
    if len(matched_students) == 1 and amount_info["auto_confirmable"]:
        # 단일 학생 + 자동 확정 가능
        result["매칭ID"] = matched_students[0]["이름ID"]
        result["매칭강좌"] = matched_students[0]["강좌명"]
        result["상태"] = "✅정상"
        result["메모"] = (result["메모"] + " 1과목 신청 — 금액 일치").strip()
        return result

    # 6. 강좌 힌트 추출 + 매칭 시도
    hint = extract_course_hint(적요, name)
    matched_course = fuzzy_course_match(hint, course_names) if hint else None

    if matched_course:
        course_students = [s for s in matched_students if s["강좌명"] == matched_course]
        if len(course_students) == 1:
            result["매칭ID"] = course_students[0]["이름ID"]
            result["매칭강좌"] = matched_course
            if amount_info["paid_courses"] >= 1:
                # 힌트로 특정 과목 확인 + 금액이 1과목 이상 → 정상
                result["상태"] = "✅정상"
                result["메모"] = (result["메모"] + f" 적요에서 강좌명 확인: {hint}→{matched_course}").strip()
            else:
                result["상태"] = "🔶확인필요"
                result["메모"] = f"금액 불일치 ({amount:,}원)"
            return result

    # 7. 자동 확정 가능하지만 강좌 특정 필요 → LLM으로 넘김
    if amount_info["auto_confirmable"] and num_courses > 1:
        # 전과목 합산 → 전부 확정
        result["매칭ID"] = matched_students[0]["이름ID"]
        result["상태"] = "✅정상"
        result["매칭강좌"] = None  # apply_matching_results에서 전 슬롯 소진
        result["메모"] = f"전과목합산 — {num_courses}과목 전체 합산입금 ({num_courses}×{TUITION_FEE // 10000}만원)"
        return result

    # 8. 강좌 특정 필요 → LLM 태깅
    result["매칭ID"] = matched_students[0]["이름ID"]
    result["상태"] = "🔶확인필요"
    result["메모"] = f"다과목({num_courses}과목) — 강좌 힌트 없음" if not hint else f"다과목({num_courses}과목) — 강좌 특정 필요"
    result["_llm_context"] = {
        "matched_name": name,
        "course_hint": hint or "",
        "candidate_courses": [s["강좌명"] for s in matched_students],
        "amount_info": amount_info,
    }
    return result


def run_code_matching(
    transactions: list[dict],
    students: list[dict],
    all_student_names: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """규칙 기반 매칭 실행. (매칭결과 전체, LLM에 넘길 건) 반환.

    all_student_names가 주어지면, 이름 추출(extract_name)에 사용한다.
    수강+신규가입+정회원 전체 이름을 포함해야 가입비/정회원비 deposit도 매칭됨.
    슬롯 배정(match_transaction)에는 students(pending 수강)만 사용한다.
    """
    # 이름 추출용: 전체 신청자 (수강+신규가입+정회원, 보존 건 포함)
    student_names = all_student_names if all_student_names else sorted(
        {s["이름"] for s in students}, key=len, reverse=True,
    )
    # 슬롯 배정용: pending 수강생만
    course_names = list({s["강좌명"] for s in students})
    results = []
    needs_llm = []

    for tx in transactions:
        result = match_transaction(tx, students, student_names, course_names)
        results.append(result)
        if "_llm_context" in result:
            needs_llm.append(result)

    return results, needs_llm

"""Tool 함수 모듈 — LangChain Agent가 호출하는 tool 목록."""

from app.tools.qa_tool import answer_question
from app.tools.payment_tool import process_payment
from app.tools.attendance_tool import create_attendance
from app.tools.ocr_tool import check_attendance_ocr
from app.tools.graduation_tool import process_graduation
from app.tools.query_tool import query_data
from app.tools.plan_tool import review_plan
from app.tools.report_tool import generate_report

ALL_TOOLS = [
    answer_question,
    process_payment,
    create_attendance,
    check_attendance_ocr,
    process_graduation,
    query_data,
    review_plan,
    generate_report,
]

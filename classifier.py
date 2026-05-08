"""Haiku 4.5 noise filter for hash-detected page changes.

Only called when the cleaned content hash differs. Returns:
    {"meaningful": bool, "reason": str}

Prompt-caches the system block so the per-call cost is just the diff + output.
Fails open: if the API errors or JSON can't be parsed, returns meaningful=True so
real changes are never silently swallowed.
"""
import difflib
import json
import logging
import os
import re

from anthropic import Anthropic

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_DIFF_CHARS = 3000
MIN_DIFF_CHARS = 40

DOC_TYPE_LABELS = {
    "de_an_tuyen_sinh": "Đề án tuyển sinh",
    "ba_cong_khai": "Báo cáo 3 công khai",
    "dashboard_thuong_nien": "Báo cáo thường niên",
}

SYSTEM_PROMPT = """Bạn là bộ phân loại thay đổi cho hệ thống giám sát website đại học Việt Nam.

NHIỆM VỤ: Cho một unified diff giữa hai snapshot của một trang web đại học, hãy phân loại:
- meaningful=true: Thay đổi có Ý NGHĨA (cập nhật nội dung thật)
- meaningful=false: Chỉ là NHIỄU ĐỘNG (dynamic noise, không liên quan nội dung)

THAY ĐỔI CÓ Ý NGHĨA (meaningful=true):
- Đăng tải / sửa / gỡ thông báo, văn bản, đề án, báo cáo
- Thêm hoặc thay link tới file PDF/DOCX báo cáo
- Thay đổi chỉ tiêu, phương thức, ngành, mã ngành, học phí, hạn nộp
- Thay đổi số liệu trong báo cáo 3 công khai (doanh thu, diện tích, quy mô SV)
- Tin tức / thông báo chính thức mới

NHIỄU ĐỘNG (meaningful=false):
- Lượt xem / lượt truy cập, bộ đếm
- Dòng "Cập nhật lúc...", "Ngày đăng...", "Lần xem cuối..."
- Banner / quảng cáo xoay vòng
- Thay đổi menu điều hướng, footer, copyright year
- Tham số session / cache trong URL
- Sắp xếp lại thứ tự không thêm nội dung mới
- Khoảng trắng / format / HTML attribute

ĐẦU RA: JSON nghiêm ngặt, không kèm văn bản giải thích bên ngoài.
Định dạng: {"meaningful": true|false, "reason": "<một câu tiếng Việt ngắn>"}

VÍ DỤ:

Diff:
- Lượt xem: 1234
+ Lượt xem: 1235
→ {"meaningful": false, "reason": "Chỉ là tăng bộ đếm lượt xem."}

Diff:
+ [Đề án tuyển sinh năm 2026 (PDF)](https://example.edu.vn/de-an-2026.pdf)
→ {"meaningful": true, "reason": "Có file đề án tuyển sinh năm mới được đăng tải."}

Diff:
- Cập nhật: 06/05/2026
+ Cập nhật: 07/05/2026
→ {"meaningful": false, "reason": "Chỉ thay đổi mốc thời gian cập nhật."}

Diff:
- Tổng chỉ tiêu: 5000
+ Tổng chỉ tiêu: 5500
→ {"meaningful": true, "reason": "Tổng chỉ tiêu tuyển sinh đã thay đổi."}
"""

_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
        _client = Anthropic(api_key=api_key)
    return _client


def build_diff(old_text, new_text):
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    diff = "\n".join(
        difflib.unified_diff(old_lines, new_lines, lineterm="", n=2)
    )
    return diff


def _meaningful_diff_size(diff):
    body = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            body.append(line[1:].strip())
    return len(re.sub(r"\s+", "", "".join(body)))


def _parse_response(text):
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object in response: {text[:200]}")
    obj = json.loads(match.group(0))
    return {
        "meaningful": bool(obj.get("meaningful", True)),
        "reason": str(obj.get("reason", "")).strip() or "(không có lý do)",
    }


def classify_change(uni_name, doc_type, old_clean, new_clean):
    """Return {meaningful: bool, reason: str, diff_excerpt: str}.

    `doc_type` should be a key from DOC_TYPE_LABELS; falls back to raw value.
    """
    diff = build_diff(old_clean, new_clean)

    if _meaningful_diff_size(diff) < MIN_DIFF_CHARS:
        return {
            "meaningful": False,
            "reason": "Diff quá nhỏ, có thể chỉ là nhiễu.",
            "diff_excerpt": diff[:500],
        }

    if len(diff) > MAX_DIFF_CHARS:
        diff_for_llm = diff[:MAX_DIFF_CHARS] + "\n... [diff truncated]"
    else:
        diff_for_llm = diff

    label = DOC_TYPE_LABELS.get(doc_type, doc_type)
    user_msg = (
        f"Trường: {uni_name}\n"
        f"Loại văn bản phát hiện: {label}\n"
        f"Diff:\n{diff_for_llm}"
    )

    try:
        resp = _get_client().messages.create(
            model=MODEL,
            max_tokens=200,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        parsed = _parse_response(text)
        log.info(
            f"  classifier[{label}]: meaningful={parsed['meaningful']} — {parsed['reason']}"
        )
        return {**parsed, "diff_excerpt": diff[:1500]}
    except Exception as e:
        log.error(f"  classifier failed ({type(e).__name__}: {e}); failing open as meaningful=True")
        return {
            "meaningful": True,
            "reason": f"(Lỗi gọi AI: {type(e).__name__}) — coi như có thay đổi để an toàn.",
            "diff_excerpt": diff[:1500],
        }

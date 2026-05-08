import asyncio
import hashlib
import os
import smtplib
import logging
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy, URLPatternFilter, FilterChain
from dotenv import load_dotenv

import db
import classifier

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
ALERT_RECIPIENT = os.getenv("ALERT_RECIPIENT")

KEYWORD_GROUPS = {
    "de_an_tuyen_sinh": [
        "đề án tuyển sinh",
        "thông tin tuyển sinh đại học",
        "chỉ tiêu tuyển sinh năm",
        "phương thức xét tuyển năm",
        "quy mô đào tạo",
        "tổng chỉ tiêu",
    ],
    "ba_cong_khai": [
        "ba công khai",
        "báo cáo ba công khai",
        "công khai chất lượng đào tạo",
        "công khai tài chính",
        "diện tích đất",
        "quy mô sinh viên",
    ],
    "dashboard_thuong_nien": [
        "báo cáo thường niên",
        "dashboard trao đổi",
        "annual report",
        "số liệu thống kê năm",
    ],
}

DOC_LABELS = {
    "de_an_tuyen_sinh": "Đề án tuyển sinh",
    "ba_cong_khai": "Báo cáo 3 công khai",
    "dashboard_thuong_nien": "Báo cáo thường niên",
}

# Status priority: higher overrides lower when multiple pages map to same doc_type.
PRIO_NOT_FOUND = 0
PRIO_UNCHANGED = 1
PRIO_NOISE = 2
PRIO_FIRST = 3
PRIO_CHANGED = 4

LOG_FILE = "crawler.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def clean_content_for_hashing(content):
    content = re.sub(r"\d{5,}", "", content)
    content = re.sub(r"^\s*\d+\s*$", "", content, flags=re.MULTILINE)
    content = re.sub(r"\d{1,2}/\d{1,2}/\d{4}", "", content)
    content = re.sub(r"\d{4}-\d{2}-\d{2}", "", content)
    content = re.sub(r"Tháng \d{1,2}", "", content)
    content = re.sub(r"tháng \d{1,2}", "", content)
    content = re.sub(r"ngày \d{1,2}", "", content)
    content = re.sub(r"\?[a-zA-Z0-9=&_\-\.]+", "", content)
    content = re.sub(r"#[a-zA-Z0-9_\-]+", "", content)
    lines = content.splitlines()
    filtered = []
    for line in lines:
        s = line.strip()
        if s.startswith("* [") or s.startswith("+ ["):
            continue
        if s.startswith("![") or s == "[]":
            continue
        if not s or s in ["-", "|", "/", "\\"]:
            continue
        filtered.append(line)
    return "\n".join(filtered)


def get_hash(cleaned):
    return hashlib.md5(cleaned.encode("utf-8")).hexdigest()


def strip_navigation(markdown):
    lines = markdown.splitlines()
    filtered = []
    for line in lines:
        s = line.strip()
        if s.startswith("* [") and s.endswith(")"):
            continue
        if s.startswith("+ [") and s.endswith(")"):
            continue
        if any(skip in s for skip in ["Copyright", "Bản quyền", "All rights"]):
            continue
        filtered.append(line)
    return "\n".join(filtered)


def detect_doc_type(content, url):
    content_clean = strip_navigation(content).lower()
    url_lower = url.lower()
    found = {}

    url_hints = {
        "de_an_tuyen_sinh": ["tuyen-sinh", "tuyensinh", "de-an"],
        "ba_cong_khai": ["cong-khai", "ba-cong-khai"],
        "dashboard_thuong_nien": ["thuong-nien", "annual", "dashboard"],
    }

    for doc_type, keywords in KEYWORD_GROUPS.items():
        for kw in keywords:
            if kw in content_clean:
                hints = url_hints.get(doc_type, [])
                url_match = any(h in url_lower for h in hints)
                if url_match or content_clean.count(kw) >= 2:
                    found[doc_type] = kw
                    break
    return found


def empty_doc_state():
    return {
        doc_type: {
            "label": "Không tìm thấy",
            "detail": "",
            "url": None,
            "priority": PRIO_NOT_FOUND,
        }
        for doc_type in KEYWORD_GROUPS
    }


def maybe_set(doc_state, doc_type, priority, label, detail, url):
    if priority >= doc_state[doc_type]["priority"]:
        doc_state[doc_type] = {
            "label": label,
            "detail": detail,
            "url": url,
            "priority": priority,
        }


def render_status(entry):
    if entry["detail"]:
        return f"{entry['label']} — {entry['detail']}"
    return entry["label"]


def format_email(results):
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    competitors = [r for r in results if r["is_competitor"]]
    internal = [r for r in results if not r["is_competitor"]]

    def render_uni_block(r, indent="  "):
        lines = [f"Trường: {r['name']}"]
        for doc_type in KEYWORD_GROUPS:
            entry = r["doc_status"][doc_type]
            lines.append(f"{indent}- {DOC_LABELS[doc_type]:<22}: {render_status(entry)}")
        urls = {dt: e["url"] for dt, e in r["doc_status"].items() if e["url"]}
        if urls:
            lines.append(f"{indent}📎 Trang liên quan:")
            for dt, url in urls.items():
                lines.append(f"{indent}   {DOC_LABELS[dt]}: {url}")
        return "\n".join(lines)

    def render_changed_section(title, group):
        changed = [r for r in group if r["any_change"]]
        if not changed:
            return ""
        out = [f"\n{title} ({len(changed)} trường có thay đổi):\n"]
        for r in changed:
            out.append(render_uni_block(r))
            out.append("")
        return "\n".join(out)

    body = f"Báo cáo giám sát tuyển sinh — {now}\n"
    body += "=" * 60 + "\n"

    total_changed = sum(1 for r in results if r["any_change"])
    body += f"\n⚠️  Tổng cộng: {total_changed}/{len(results)} trường có thay đổi.\n"

    body += render_changed_section("🔴 TRƯỜNG ĐỐI THỦ", competitors)
    body += render_changed_section("🔵 CÁC TRƯỜNG KHÁC", internal)

    body += "\n" + "-" * 60 + "\n"
    body += "📋 TỔNG HỢP TẤT CẢ TRƯỜNG:\n\n"

    if competitors:
        body += "── Trường đối thủ ──\n"
        for r in competitors:
            body += render_uni_block(r) + "\n\n"
    if internal:
        body += "── Các trường khác ──\n"
        for r in internal:
            body += render_uni_block(r) + "\n\n"

    body += "=" * 60 + "\n"
    body += "\n---\nFPTU Crawler System"
    return body


def send_email_alert(results):
    log.info("Đang gửi email...")
    changed_count = sum(1 for r in results if r["any_change"])
    subject = (
        f"[FPTU Crawler] {changed_count} trường có thay đổi"
        f" — {datetime.now().strftime('%d/%m/%Y')}"
    )
    body = format_email(results)

    msg = MIMEMultipart()
    msg["From"] = GMAIL_USER
    msg["To"] = ALERT_RECIPIENT
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        log.info("✅ Email đã gửi thành công!")
    except Exception as e:
        log.error(f"❌ Gửi email thất bại: {e}")


async def crawl_university(crawler, uni):
    log.info(f"Đang deep crawl: {uni['name']}...")

    url_filter = URLPatternFilter(patterns=[
        "*tuyen-sinh*", "*tuyensinh*",
        "*cong-khai*", "*ba-cong-khai*",
        "*thuong-nien*", "*annual*",
        "*thong-bao*", "*dao-tao*",
    ])

    asset_filter = URLPatternFilter(
        patterns=[
            "*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp", "*.svg", "*.ico", "*.bmp",
            "*.css", "*.js", "*.woff", "*.woff2", "*.ttf", "*.otf",
            "*.mp4", "*.mp3", "*.avi", "*.mov", "*.webm",
            "*.pdf", "*.doc", "*.docx", "*.xls", "*.xlsx", "*.ppt", "*.pptx",
            "*.zip", "*.rar", "*.7z",
        ],
        reverse=True,
    )

    config = CrawlerRunConfig(
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=2,
            max_pages=20,
            filter_chain=FilterChain([asset_filter, url_filter]),
        )
    )

    doc_status = empty_doc_state()
    any_change = False

    try:
        results = await crawler.arun(url=uni["url"], config=config)
        if not isinstance(results, list):
            results = [results]

        for result in results:
            if not result.success or not result.markdown:
                continue

            page_url = result.url
            content = result.markdown
            content_clean = clean_content_for_hashing(content)
            current_hash = get_hash(content_clean)
            detected = detect_doc_type(content, page_url)

            snapshot = db.get_snapshot(uni["id"], page_url)
            primary_doc_type = next(iter(detected), None)

            for doc_type in detected:
                if snapshot is None:
                    maybe_set(
                        doc_status, doc_type,
                        PRIO_FIRST, "🆕 Lần đầu phát hiện", "",
                        page_url,
                    )
                    db.log_change_event(
                        uni["id"], page_url, doc_type,
                        llm_meaningful=None,
                        llm_reason="Trang mới phát hiện",
                        diff_excerpt="",
                    )
                    any_change = True

                elif current_hash != snapshot["content_hash"]:
                    verdict = classifier.classify_change(
                        uni["name"], doc_type,
                        snapshot.get("content_clean") or "",
                        content_clean,
                    )
                    db.log_change_event(
                        uni["id"], page_url, doc_type,
                        llm_meaningful=verdict["meaningful"],
                        llm_reason=verdict["reason"],
                        diff_excerpt=verdict["diff_excerpt"],
                    )
                    if verdict["meaningful"]:
                        maybe_set(
                            doc_status, doc_type,
                            PRIO_CHANGED, "🔴 Có thay đổi", verdict["reason"],
                            page_url,
                        )
                        any_change = True
                    else:
                        maybe_set(
                            doc_status, doc_type,
                            PRIO_NOISE, "🟡 Không thay đổi (nhiễu động)", verdict["reason"],
                            page_url,
                        )

                else:
                    maybe_set(
                        doc_status, doc_type,
                        PRIO_UNCHANGED, "✅ Không thay đổi", "",
                        page_url,
                    )

            db.upsert_snapshot(
                uni_id=uni["id"],
                page_url=page_url,
                doc_type=primary_doc_type,
                content_clean=content_clean,
                content_hash=current_hash,
                changed=(snapshot is None) or (current_hash != snapshot["content_hash"]),
            )

    except Exception as e:
        log.error(f"❌ Lỗi khi crawl {uni['name']}: {e}")

    return {
        "id": uni["id"],
        "name": uni["name"],
        "url": uni["url"],
        "is_competitor": bool(uni["is_competitor"]),
        "doc_status": doc_status,
        "any_change": any_change,
    }


async def check_universities():
    log.info("=== Bắt đầu chu kỳ kiểm tra ===")
    db.init_schema()
    universities = db.list_universities()
    if not universities:
        log.error("❌ Bảng universities trống. Chạy `python init_db.py` trước.")
        return

    all_results = []
    async with AsyncWebCrawler() as crawler:
        for uni in universities:
            result = await crawl_university(crawler, uni)
            all_results.append(result)
            statuses = " | ".join(
                f"{DOC_LABELS[dt]}={result['doc_status'][dt]['label']}"
                for dt in KEYWORD_GROUPS
            )
            log.info(f"  {uni['name']}: {statuses}")

    send_email_alert(all_results)
    log.info("=== Kết thúc chu kỳ kiểm tra ===\n")


if __name__ == "__main__":
    asyncio.run(check_universities())

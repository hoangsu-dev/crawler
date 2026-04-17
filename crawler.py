import asyncio
import hashlib
import json
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from crawl4ai import AsyncWebCrawler
from dotenv import load_dotenv

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
ALERT_RECIPIENT = os.getenv("ALERT_RECIPIENT")

UNIVERSITIES = [
    {"name": "HCMUT", "url": "https://tuyensinh.hcmut.edu.vn/"},
    {"name": "PTIT",  "url": "https://tuyensinh.ptit.edu.vn/"},
]

SNAPSHOT_FILE = "snapshots.json"
LOG_FILE = "crawler.log"

# Setup log file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

def load_snapshots():
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_snapshots(snapshots):
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshots, f, ensure_ascii=False, indent=2)

def get_hash(content):
    return hashlib.md5(content.encode("utf-8")).hexdigest()

def get_diff_summary(old_content, new_content):
    old_lines = set(old_content.splitlines())
    new_lines = set(new_content.splitlines())

    added = [l for l in new_lines - old_lines if l.strip()]
    removed = [l for l in old_lines - new_lines if l.strip()]

    summary = ""
    if added:
        summary += "\n  📌 Nội dung mới xuất hiện:\n"
        for line in list(added)[:5]:
            summary += f"    + {line[:100]}\n"
    if removed:
        summary += "\n  🗑️ Nội dung bị xóa:\n"
        for line in list(removed)[:5]:
            summary += f"    - {line[:100]}\n"

    return summary if summary else "\n  (Không xác định được chi tiết thay đổi)\n"

def send_email_alert(changes):
    log.info("Đang gửi email alert...")

    subject = f"[FPTU Crawler] Phát hiện {len(changes)} trường cập nhật thông tin"

    body = "Xin chào,\n\nHệ thống giám sát tuyển sinh FPTU phát hiện các trường sau vừa cập nhật thông tin:\n"

    for change in changes:
        body += f"\n{'='*50}\n"
        body += f"🚨 {change['name']}\n"
        body += f"🔗 {change['url']}\n"
        body += f"📊 Chi tiết thay đổi:{change['diff']}\n"

    body += f"\n{'='*50}\n"
    body += f"Thời gian phát hiện: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
    body += "\n---\nFPTU Crawler System"

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
        log.info("Email đã được gửi thành công!")
    except Exception as e:
        log.error(f"Gửi email thất bại: {e}")

async def check_universities():
    log.info("=== Bắt đầu chu kỳ kiểm tra ===")
    snapshots = load_snapshots()
    changes = []

    async with AsyncWebCrawler() as crawler:
        for uni in UNIVERSITIES:
            log.info(f"Đang kiểm tra: {uni['name']}...")
            result = await crawler.arun(url=uni["url"])

            if not result.success:
                log.error(f"Không thể truy cập {uni['name']}: {result.error_message}")
                continue

            current_hash = get_hash(result.markdown)
            last_hash = snapshots.get(uni["name"], {}).get("hash")
            last_content = snapshots.get(uni["name"], {}).get("content", "")

            if last_hash is None:
                log.info(f"{uni['name']}: Lần đầu crawl — lưu snapshot.")
                snapshots[uni["name"]] = {
                    "hash": current_hash,
                    "content": result.markdown,
                    "last_checked": datetime.now().isoformat(),
                    "url": uni["url"]
                }
            elif current_hash != last_hash:
                log.info(f"{uni['name']}: PHÁT HIỆN THAY ĐỔI!")
                diff = get_diff_summary(last_content, result.markdown)
                changes.append({
                    "name": uni["name"],
                    "url": uni["url"],
                    "diff": diff
                })
                snapshots[uni["name"]]["hash"] = current_hash
                snapshots[uni["name"]]["content"] = result.markdown
                snapshots[uni["name"]]["last_checked"] = datetime.now().isoformat()
            else:
                log.info(f"{uni['name']}: Không có thay đổi.")
                snapshots[uni["name"]]["last_checked"] = datetime.now().isoformat()

    save_snapshots(snapshots)

    if changes:
        log.info(f"Tổng cộng {len(changes)} trường thay đổi — gửi email...")
        send_email_alert(changes)
    else:
        log.info("Không có trường nào thay đổi.")

    log.info("=== Kết thúc chu kỳ kiểm tra ===\n")

asyncio.run(check_universities())
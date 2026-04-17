# FPTU Crawler

A Python web crawler that monitors Vietnamese university admissions pages for content changes and sends email alerts via Gmail when updates are detected.

## What it does

- Periodically crawls the admissions pages of configured universities
- Compares the current content against a stored snapshot using MD5 hashing
- Detects added and removed lines and summarises the diff
- Sends a Gmail alert email listing all changed pages when a difference is found
- Logs all activity to `crawler.log`

## Monitored universities

| Name  | URL                              |
|-------|----------------------------------|
| HCMUT | https://tuyensinh.hcmut.edu.vn/  |
| PTIT  | https://tuyensinh.ptit.edu.vn/   |

You can add more entries to the `UNIVERSITIES` list in `crawler.py`.

## Requirements

- Python 3.9+
- [crawl4ai](https://github.com/unclecode/crawl4ai)
- python-dotenv

Install dependencies:

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install crawl4ai python-dotenv
```

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

`.env` contents:

```
GMAIL_USER=your_gmail@gmail.com
GMAIL_APP_PASSWORD=your_gmail_app_password
ALERT_RECIPIENT=recipient_email@gmail.com
```

> **Gmail App Password** — Go to your Google Account → Security → 2-Step Verification → App passwords, and generate a password for "Mail". Use that 16-character password here, **not** your regular Gmail password.

## Running

### Manually

```bash
python crawler.py
```

### Windows batch file

Double-click `run_crawler.bat` or run it from a terminal. It activates the local `venv` automatically.

### Scheduled (Windows Task Scheduler)

1. Open **Task Scheduler** → Create Basic Task.
2. Set the trigger (e.g. every 6 hours).
3. Set the action to run `run_crawler.bat`.

## Project structure

```
fptu-crawler/
├── crawler.py          # Main crawler script
├── run_crawler.bat     # Windows convenience launcher
├── .env.example        # Credential template (safe to commit)
├── .env                # Your actual credentials (gitignored)
├── snapshots.json      # Cached page snapshots (gitignored)
├── crawler.log         # Runtime log (gitignored)
└── venv/               # Virtual environment (gitignored)
```

## Notes

- `snapshots.json` is excluded from git because it can grow large and contains scraped page content.
- Never commit `.env` — it contains your Gmail app password.

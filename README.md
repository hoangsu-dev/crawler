# FPTU Crawler

A Python web crawler that monitors Vietnamese university admission / transparency
pages, detects content changes, and emails alerts. Uses Claude Haiku 4.5 to
filter dynamic page noise (view counters, timestamps, banners) so only
meaningful changes are reported.

## What it does

- Deep-crawls each university's site looking for three doc types:
  **Đề án tuyển sinh**, **Báo cáo 3 công khai**, **Báo cáo thường niên**.
- Hashes a cleaned version of each page; on a hash diff, sends a unified diff
  to Claude Haiku 4.5 with prompt caching, which classifies it as a
  meaningful update or just dynamic noise.
- Stores everything in `snapshots.db` (SQLite) — pages, snapshots, and a
  `change_events` audit log.
- Sends a Gmail alert split into two sections: **Trường đối thủ** and
  **Các trường khác**.

## Requirements

- Python 3.9+
- [crawl4ai](https://github.com/unclecode/crawl4ai)
- python-dotenv
- anthropic

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install crawl4ai python-dotenv anthropic
```

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```
GMAIL_USER=your_gmail@gmail.com
GMAIL_APP_PASSWORD=your_gmail_app_password
ALERT_RECIPIENT=recipient_email@gmail.com
ANTHROPIC_API_KEY=sk-ant-...
```

> **Gmail App Password** — Google Account → Security → 2-Step Verification → App passwords.
> **Anthropic API key** — https://console.anthropic.com/settings/keys

## Editing the university list

The list lives in `universities.csv` and is editable in Excel / Google Sheets.

| column          | meaning                                         |
|-----------------|-------------------------------------------------|
| `name`          | Display name (must be unique)                   |
| `url`           | Homepage URL                                    |
| `region`        | Hà Nội / Đà Nẵng / TP.HCM / Cần Thơ             |
| `is_competitor` | `TRUE` or `FALSE` — affects email grouping       |
| `notes`         | Free text                                       |

After editing, re-seed the database:

```bash
python init_db.py
```

This is idempotent: rows are upserted by `name`. Existing snapshots are
preserved.

## First-time setup

```bash
python init_db.py     # seed universities into snapshots.db
python crawler.py     # first run — every page is "🆕 Lần đầu phát hiện"
```

The first run produces a large alert (every detected page is new). Subsequent
runs only flag pages where Haiku 4.5 confirms a meaningful change.

## Running

```bash
python crawler.py
```

### Windows batch file

`run_crawler.bat` activates the local `venv` and runs the crawler.

### Scheduled (Windows Task Scheduler)

1. Task Scheduler → Create Basic Task.
2. Trigger: e.g. once daily at 8 AM.
3. Action: run `run_crawler.bat`.

## Project structure

```
fptu-crawler/
├── crawler.py          # Main crawler + email
├── classifier.py       # Haiku 4.5 noise filter (prompt-cached)
├── db.py               # SQLite schema + helpers
├── init_db.py          # CSV → DB seeder (run after editing the CSV)
├── universities.csv    # Editable university list
├── run_crawler.bat     # Windows convenience launcher
├── .env.example        # Credential template (safe to commit)
├── .env                # Actual credentials (gitignored)
├── snapshots.db        # SQLite store (gitignored)
├── crawler.log         # Runtime log (gitignored)
└── venv/               # Virtual environment (gitignored)
```

## Cost estimate

At ~30 hash-diff pages per daily run with Haiku 4.5 + prompt caching:
roughly **$2 / month**. The system fails open — if the API errors, the page
is reported as changed so nothing real is silently dropped.

## Inspecting history

`snapshots.db` is a normal SQLite file — open with the `sqlite3` CLI or any
GUI viewer.

```sql
-- Most recent meaningful changes
SELECT u.name, e.page_url, e.detected_at, e.llm_reason
FROM change_events e JOIN universities u ON u.id = e.uni_id
WHERE e.llm_meaningful = 1
ORDER BY e.detected_at DESC LIMIT 20;
```

## Notes

- `snapshots.db` is excluded from git — it grows and contains scraped content.
- Never commit `.env`.
- The classifier fails open: API failures are treated as meaningful changes.

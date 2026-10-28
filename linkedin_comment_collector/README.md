# LeadStream AI — LinkedIn Comment Intelligence & Lead Extraction Hub

A high-performance, 100% free, and open-source enterprise platform for collecting, analyzing, categorizing, and exporting leads from LinkedIn post comments and nested discussions.

---

## 1. Key Capabilities & Architectural Highlights

- **100% Free & Open-Source Engine (No Paid API Keys):**
  - Powered by local headless Chromium via Playwright (`playwright_scraper.py`).
  - Completely eliminates paid third-party dependencies (e.g. Apify actors) and recurring monthly compute costs.
  - Unlimited scraping capacity at **$0.00 cost forever**.
- **High-Speed Execution (5–15 Seconds):**
  - Optimized non-blocking extraction pipeline with in-memory corporate domain resolution and caching (`_COMPANY_WEBSITE_CACHE`, `_COMPANY_LINKEDIN_CACHE`).
  - Instant redirect validation prevents hanging on expired short links or login walls.
- **Smart Cumulative Boundary Expansion:** Fetch comments across multiple runs without data loss. Subsequent scrapes automatically calculate existing database records and expand the fetch limit:
  $$\text{target\_scrape\_limit} = \text{existing\_in\_db} + \text{requested\_batch}$$
  New comments are inserted via `INSERT OR IGNORE` (`UNIQUE(post_id, source_comment_id)`), ensuring 100% idempotency with zero duplicate records.
- **Automated Contact & Company Enrichment:**
  - Extracts emails and phone numbers directly from comment bodies using RFC-compliant pattern matchers.
  - Automatically identifies and formats official **Company Websites** and **Company LinkedIn Pages** from email domains and headlines.
- **Vendor & Lead Deduplication:**
  - **Unique Vendors Only**: Aggregates repeat commenters into unique lead entities with consolidated comment counts and unified outreach profiles.
  - **Exclude Author Replies**: Separates original audience leads from post creator follow-ups.
- **SSRF Protection & URL Normalization:** Strict domain verification restricts traffic exclusively to authorized LinkedIn hosts (`linkedin.com`, `lnkd.in`), safely unrolling short links while blocking loopback (`127.0.0.1`, `localhost`) and private subnets.
- **Executive React 19 + Vite UI (Port 3000):**
  - Executive KPI metric cards with live conversion metrics (Unique ratio %, Verified emails, Direct dials).
  - High-density sticky table header with interactive row hover and high-contrast dark/light mode typography.
  - **Interactive Lead Detail Drawer**: Click any commenter to inspect their full bio, uncropped comments, and instant contact copy tools.
  - **1-Click "Copy Emails"**: Copies all filtered emails to clipboard with a single click.
  - Multi-post dataset switcher and aggregated combined views.
- **Outreach-Ready CSV Exports:** Instant downloads formatted with clean columns (`Lead Name`, `LinkedIn Profile`, `Headline`, `Email`, `Company Website`, `Company LinkedIn Link`, `Phone`, `Comment Text`).

---

## 2. Project Directory Structure

```text
linkedin_comment_collector/
├── api_server.py             # FastAPI backend (REST API & static web serving)
├── collector_service.py      # Core business logic: lead parsing, domain resolution, SQLite WAL batch writes
├── playwright_scraper.py     # 100% Free open-source Playwright browser scraper engine
├── db_manager.py             # SQLite schema & database connection management (WAL mode enabled)
├── comments.db               # SQLite database (persists posts, comments & collection runs)
├── dashboard.py              # Streamlit analytics dashboard (Port 8501)
├── requirements.txt          # Python backend dependencies
├── pyproject.toml            # Project packaging metadata
├── Dockerfile                # Production container definition
├── docker-compose.yml        # Multi-service container orchestration
├── .env.example              # Environment variables template
├── README.md                 # Complete system documentation
│
├── frontend/                 # Modern React 19 + Vite Dashboard (Port 3000)
│   ├── src/
│   │   ├── App.jsx           # Main application view with Lead Drawer, KPI Cards & Filters
│   │   ├── index.css         # Minimalist enterprise design system (Dark & Light theme tokens)
│   │   └── main.jsx          # React DOM entrypoint
│   ├── index.html            # Vite HTML template
│   ├── package.json          # Frontend dependencies
│   └── vite.config.js        # Vite build and proxy configuration
│
├── tests/                    # Automated Test Suite (100% passing)
│   ├── test_production.py    # Unit & integration regression tests
│   ├── test_company_detection.py # Domain & company LinkedIn resolution tests
│   └── __init__.py
│
└── web/                      # FastAPI Static Web Assets (Port 8000)
    └── index.html            # Standalone single-file UI fallback
```

---

## 3. Database Schema (`comments.db`)

Managed natively via `db_manager.py` with SQLite WAL mode enabled (`PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=30000;`).

### `posts`
| Column | Type | Description |
|---|---|---|
| `id` | TEXT (PK) | Unique UUID identifier |
| `canonical_url` | TEXT (UNIQUE) | Query-stripped normalized LinkedIn URL |
| `original_url` | TEXT | Raw submitted post link |
| `urn` | TEXT | LinkedIn Activity/Share URN (`urn:li:activity:...`) |
| `title` | TEXT | Post headline or slug |
| `author_name` | TEXT | Post author name |
| `author_url` | TEXT | Post author profile URL |
| `total_comments_count` | INTEGER | Total collected comments in database |
| `last_collected_at` | TEXT | ISO timestamp of most recent collection |
| `created_at` / `updated_at`| TEXT | Audit timestamps |

### `comments`
| Column | Type | Description |
|---|---|---|
| `id` | TEXT (PK) | Unique UUID identifier |
| `post_id` | TEXT (FK) | Reference to `posts(id)` (ON DELETE CASCADE) |
| `source_comment_id` | TEXT | LinkedIn unique comment ID/URN |
| `parent_comment_id` | TEXT (FK) | Reference to parent comment (NULL for top-level) |
| `parent_source_id` | TEXT | Source ID of parent comment |
| `comment_text` | TEXT | Cleaned comment body |
| `author_name` | TEXT | Commenter full name |
| `author_profile_url` | TEXT | Commenter LinkedIn profile URL |
| `author_headline` | TEXT | Job title / headline |
| `author_avatar_url` | TEXT | Profile picture URL |
| `email` | TEXT | Extracted lead email address (or NULL) |
| `phone` | TEXT | Extracted lead phone number (or NULL) |
| `company_website` | TEXT | Official company domain or website |
| `company_linkedin_url` | TEXT | Official LinkedIn company page link |
| `reaction_count` | INTEGER | Likes and reactions count |
| `depth` | INTEGER | Thread depth (0 = top-level, 1+ = reply) |
| `commented_at` | TEXT | Comment timestamp |

*Constraint:* `UNIQUE(post_id, source_comment_id)` prevents comment duplication across multiple collection passes.

### `collection_runs`
| Column | Type | Description |
|---|---|---|
| `id` | TEXT (PK) | Unique collection run UUID |
| `post_id` | TEXT | Target post ID |
| `status` | TEXT | `IN_PROGRESS`, `COMPLETED`, `FAILED` |
| `provider_name` | TEXT | Extraction engine (`playwright` or `direct`) |
| `total_expected_comments` | INTEGER | Target comment count requested |
| `total_collected_comments` | INTEGER | Actual comments successfully persisted |
| `error_message` | TEXT | Failure details if run encounters an error |

---

## 4. REST API Reference

Interactive Swagger documentation is available at `http://127.0.0.1:8000/docs`.

### Extract Leads & Collect Comments
`POST /api/extract`

Initiates collection via the free local Playwright engine, enriches contact details, and persists into SQLite.

**Request Body:**
```json
{
  "post_url": "https://www.linkedin.com/posts/username_post-title-activity-7123456789012345678-abcd",
  "max_comments": 100
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "post": {
    "id": "220771d6-9007-45c8-a3cd-ee129f13a27e",
    "title": "Post Title",
    "canonical_url": "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678",
    "total_comments_count": 35
  },
  "total_comments": 35,
  "comments": [...]
}
```

### List All Collected Posts
`GET /api/posts`

Returns summary of all past collection runs stored in the database.

### Retrieve Post & Categorized Comments
`GET /api/posts/{post_id}`

Retrieves full metadata and comment list for a specific post.

### Export Clean Outreach CSV
`GET /api/export/{post_id}?deduplicate_authors=true`

Downloads an outreach-ready CSV file. When `deduplicate_authors=true`, repeat commenters are aggregated into a single row.

### Health Check
`GET /api/health`

Returns engine status and timestamp:
```json
{
  "status": "healthy",
  "engine": "LeadStream Enterprise",
  "version": "2.0.0"
}
```

---

## 5. Quickstart & Local Setup

### 1. Install Dependencies
```bash
# Python backend dependencies
pip install -r requirements.txt

# Install Playwright browser binaries
playwright install chromium

# React frontend dependencies
cd frontend
npm install
cd ..
```

### 2. Configure Environment (Optional)
The system runs 100% free with zero configuration. An optional `LINKEDIN_LI_AT` cookie can be provided if you wish to scrape deep comments on restricted posts:
```env
# Optional: Burner LinkedIn session cookie for deep authenticated scraping
LINKEDIN_LI_AT=
```

### 3. Launch Services

#### Start FastAPI Backend (Port 8000)
```bash
python -m uvicorn api_server:app --host 127.0.0.1 --port 8000 --reload
```
- API & Docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Standalone HTML UI: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)

#### Start React + Vite Frontend (Port 3000)
```bash
cd frontend
npm run dev
```
- Dashboard: [http://localhost:3000](http://localhost:3000)

#### Start Streamlit Analytics Dashboard (Port 8501 - Optional)
```bash
streamlit run dashboard.py
```

---

## 6. Running Automated Tests

Run the complete regression test suite:
```bash
python -m unittest discover tests
```
Tests verify:
1. SQLite schema initialization and connection timeout hardening.
2. SSRF protection against loopback/internal hosts.
3. Automated email and phone regex extraction.
4. Corporate domain and company LinkedIn link extraction.
5. Boundary-expanded collection and database deduplication.
6. Exported CSV format and categorized headers.

---

## 7. Production Docker Deployment

Build and run using Docker:
```bash
docker-compose up --build
```
The FastAPI engine will be exposed on port `8000`.

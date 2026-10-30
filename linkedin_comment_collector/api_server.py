"""
LeadStream API Backend Server (FastAPI)
Provides high-performance REST endpoints for LinkedIn comment collection,
lead categorization, SQLite persistence, and CSV exports.
"""
import os
import uuid
import io
import csv
import sqlite3
import datetime
from typing import Optional, List, Dict, Any
import json
import hmac
import threading
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local"))
load_dotenv(os.path.join(os.path.dirname(__file__), ".env.local"))

from fastapi import FastAPI, HTTPException, Query, Response, UploadFile, File, BackgroundTasks, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from collector_service import CollectorService, SSRFProtectionError, InvalidURLError
from db_manager import init_db, DB_FILE

# Initialize Database Schema
init_db(DB_FILE)

app = FastAPI(
    title="LeadStream API",
    description="Enterprise LinkedIn Comment Extraction & Lead Categorization Backend",
    version="2.0.0"
)

# Thread-safe Async Job Tracker with DB-backed persistence across restarts
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
MAX_IN_MEMORY_JOBS = 500
STALE_JOB_SECONDS = 360  # 6 minutes (exceeds Apify 5-minute run_timeout)


def _sanitize_spreadsheet_cell(val: Any) -> Any:
    """Prevents CSV/Excel formula injection (OWASP CSV Injection) on untrusted strings."""
    if not isinstance(val, str) or not val:
        return val
    stripped = val.lstrip(" \t\r\n")
    if stripped and stripped[0] in ("=", "+", "-", "@"):
        # Allow legitimate E.164 phone numbers like '+1 (555) 234-5678' only if strictly digits/spaces/dashes/parens
        if stripped[0] == "+" and all(ch in "0123456789 -().+" for ch in stripped):
            return "'" + val
        return "'" + val
    if val[0] in ("\t", "\r"):
        return "'" + val
    return val


PERSONAL_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "ymail.com", "rediffmail.com",
    "hotmail.com", "outlook.com", "live.com", "msn.com", "icloud.com", "me.com", "mac.com", "aol.com",
    "proton.me", "protonmail.com", "zoho.com", "zohomail.in", "mail.com", "gmx.com", "gmx.net", "yandex.com", "fastmail.com",
}


def _is_guessed_from_personal_email(value: Optional[str], email: Optional[str]) -> bool:
    """Detects company links that older versions guessed from a personal email username (venky@gmail.com -> venky.in)."""
    if not value or not email or "@" not in email:
        return False
    user, domain = email.strip().lower().split("@", 1)
    if not user or domain not in PERSONAL_EMAIL_DOMAINS:
        return False
    v = value.strip().lower().split("://", 1)[-1]
    v = (v[4:] if v.startswith("www.") else v).rstrip("/")
    user_slug = "".join(ch if ch.isalnum() else "-" for ch in user)
    return v.split("/")[0].split(".")[0] == user or v in (f"linkedin.com/company/{user}", f"linkedin.com/company/{user_slug}")


def _is_timestamp_stale(ts_str: Optional[str], max_age_seconds: int = STALE_JOB_SECONDS) -> bool:
    if not ts_str:
        return False
    try:
        clean_ts = str(ts_str).strip().replace("Z", "+00:00")
        if "T" in clean_ts:
            dt = datetime.datetime.fromisoformat(clean_ts)
        else:
            dt = datetime.datetime.strptime(clean_ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        age = (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()
        return age > max_age_seconds
    except Exception:
        return False


def _require_admin_token(x_admin_token: Optional[str]) -> None:
    """Protects destructive endpoints. Disabled by default in production unless ADMIN_API_TOKEN is set and matched in constant time."""
    configured_token = (os.environ.get("ADMIN_API_TOKEN") or "").strip()
    provided_token = (x_admin_token or "").strip()
    if not configured_token or not provided_token or not hmac.compare_digest(provided_token.encode("utf-8"), configured_token.encode("utf-8")):
        raise HTTPException(
            status_code=403,
            detail="Data deletion is disabled to protect production records, or requires a valid X-Admin-Token header."
        )


def _update_job(job_id: str, **updates):
    with JOBS_LOCK:
        if job_id not in JOBS:
            JOBS[job_id] = {"job_id": job_id}
        JOBS[job_id].update(updates)
        if len(JOBS) > MAX_IN_MEMORY_JOBS:
            completed_keys = [
                k for k, v in JOBS.items()
                if k != job_id and v.get("status") in ("COMPLETED", "FAILED")
            ]
            for k in completed_keys[:len(JOBS) - MAX_IN_MEMORY_JOBS]:
                JOBS.pop(k, None)


def _serialize_job(job_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a JSON-safe copy of the job dictionary without internal thread references."""
    return {k: v for k, v in job_dict.items() if k != "_worker_thread"}


def run_extraction_worker(job_id: str, post_url: str, provider: str, apify_token: Optional[str], max_comments: Optional[int]):
    """Background worker that performs extraction without holding HTTP connection open."""
    try:
        _update_job(
            job_id,
            status="IN_PROGRESS",
            message="Scraping comments and identifying commenters...",
            _worker_thread=threading.current_thread()
        )

        res = CollectorService.execute_collection_and_persist(
            db_path=DB_FILE,
            post_url=post_url.strip(),
            provider=provider or "apify",
            apify_token=apify_token,
            max_comments=max_comments,
            run_id=job_id
        )
        post_id = res["post_id"]

        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, author_name, author_profile_url, author_headline,
                       email, phone, company_website, company_linkedin_url, comment_text, reaction_count, likes_count, commented_at, depth, parent_source_id, parent_comment_id
                FROM comments
                WHERE post_id = ?
                ORDER BY reaction_count DESC, commented_at DESC
            """, (post_id,))
            comments = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT id, title, canonical_url, original_url, total_comments_count FROM posts WHERE id = ?", (post_id,))
            post_row = dict(cur.fetchone())
        finally:
            conn.close()

        _update_job(
            job_id,
            status="COMPLETED",
            result={
                "success": True,
                "post": post_row,
                "total_comments": len(comments),
                "comments": comments,
                "timings": res.get("timings", {})
            },
            message=f"Extraction complete: {len(comments)} leads gathered",
            _worker_thread=None
        )
    except Exception as e:
        _update_job(
            job_id,
            status="FAILED",
            error=str(e),
            message=f"Error: {str(e)}",
            _worker_thread=None
        )


# Enable CORS for modern frontend dev servers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

web_dir = os.path.join(os.path.dirname(__file__), "web")
os.makedirs(web_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=web_dir), name="static")

frontend_dist = os.path.join(os.path.dirname(__file__), "frontend", "dist")
if os.path.exists(os.path.join(frontend_dist, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

@app.get("/")
def serve_index():
    dist_index = os.path.join(frontend_dist, "index.html")
    if os.path.exists(dist_index):
        return FileResponse(dist_index)
    index_file = os.path.join(web_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "LeadStream API online. Web interface is loading..."}

@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "engine": "LeadStream Enterprise",
        "version": "2.0.0",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

class ExtractRequest(BaseModel):
    post_url: str = Field(..., description="LinkedIn post link (full or short link)")
    max_comments: Optional[int] = Field(default=0, ge=0, le=1000, description="Target comments to collect (0 for default, max 1000)")
    provider: Optional[str] = Field(default="apify", description="Scraper provider: 'apify' or 'playwright'")
    apify_token: Optional[str] = Field(default=None, description="Apify API Token (optional override)")

class BatchExtractRequest(BaseModel):
    post_urls: List[str] = Field(..., description="List of LinkedIn post links")
    max_comments: Optional[int] = Field(default=0, ge=0, le=1000, description="Target comments to collect per post (0 for default, max 1000)")
    provider: Optional[str] = Field(default="apify", description="Scraper provider: 'apify' or 'playwright'")
    apify_token: Optional[str] = Field(default=None, description="Apify API Token (optional override)")

def get_db_connection():
    from db_manager import get_connection
    return get_connection(DB_FILE)

@app.post("/api/parse-links")
async def parse_links_from_file(file: UploadFile = File(...)):
    """Extracts valid LinkedIn post URLs from uploaded files (PDF, DOCX, DOC, CSV, TXT, XLSX, XLS)."""
    import re
    filename = file.filename or ""
    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size exceeds the 15 MB limit.")
    extracted_text = ""

    ext = os.path.splitext(filename)[1].lower()
    supported_exts = {".pdf", ".docx", ".doc", ".xlsx", ".xlsm", ".xltx", ".xltm", ".xls", ".csv", ".txt", ".tsv"}
    if ext and ext not in supported_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Supported formats: CSV, TXT, PDF, DOCX, DOC, XLSX, XLS."
        )

    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            pdf_stream = io.BytesIO(content)
            reader = PdfReader(pdf_stream)
            if getattr(reader, "is_encrypted", False):
                try:
                    reader.decrypt("")
                except Exception:
                    raise ValueError("PDF file is password-protected/encrypted and cannot be read.")
            extracted_text = "\n".join([page.extract_text() or "" for page in reader.pages])
        elif ext in [".docx", ".doc"]:
            try:
                from docx import Document
                docx_stream = io.BytesIO(content)
                doc = Document(docx_stream)
                text_parts = [p.text for p in doc.paragraphs]
                for table in doc.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            text_parts.append(cell.text)
                if hasattr(doc, 'part') and hasattr(doc.part, 'rels'):
                    for rel in doc.part.rels.values():
                        if "hyperlink" in getattr(rel, 'reltype', '') and getattr(rel, 'target_ref', None):
                            text_parts.append(rel.target_ref)
                extracted_text = "\n".join(text_parts)
            except Exception as doc_err:
                if ext == ".docx":
                    raise ValueError(f"Malformed or corrupted .docx file: {doc_err}")
                # Legacy binary .doc (OLE2) fallback: extract both ASCII and UTF-16LE text streams
                ascii_text = content.decode("latin-1", errors="ignore")
                utf16_text = content.decode("utf-16le", errors="ignore")
                extracted_text = ascii_text + "\n" + utf16_text
        elif ext in [".xlsx", ".xlsm", ".xltx", ".xltm"]:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(content), data_only=True)
            text_parts = []
            for sheet in wb.worksheets:
                for row in sheet.iter_rows():
                    for cell in row:
                        if cell.value is not None:
                            text_parts.append(str(cell.value))
                        if cell.hyperlink and getattr(cell.hyperlink, 'target', None):
                            text_parts.append(str(cell.hyperlink.target))
            extracted_text = "\n".join(text_parts)
        elif ext == ".xls":
            try:
                import xlrd
                wb = xlrd.open_workbook(file_contents=content)
                text_parts = []
                for s in wb.sheets():
                    for row_idx in range(s.nrows):
                        for col_idx in range(s.ncols):
                            val = s.cell_value(row_idx, col_idx)
                            if val:
                                text_parts.append(str(val))
                extracted_text = "\n".join(text_parts)
            except Exception:
                extracted_text = content.decode("latin-1", errors="ignore")
        else:  # .csv, .txt, .tsv
            extracted_text = content.decode("utf-8", errors="ignore")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")

    # Extract LinkedIn links (both lnkd.in and linkedin.com/...)
    link_pattern = re.compile(
        r'(?:https?://)?(?:www\.)?(?:linkedin\.com/(?:posts|feed/update|pulse)/[^\s\'"<>,;]+|lnkd\.in/[^\s\'"<>,;]+)',
        re.IGNORECASE
    )
    raw_matches = link_pattern.findall(extracted_text)

    # Clean and deduplicate while maintaining order
    clean_urls = []
    seen = set()
    for m in raw_matches:
        cleaned = m.rstrip(".,;)>'\"")
        if not cleaned.startswith(("http://", "https://")):
            cleaned = "https://" + cleaned
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            clean_urls.append(cleaned)

    return {
        "success": True,
        "filename": filename,
        "found_count": len(clean_urls),
        "urls": clean_urls
    }

@app.post("/api/extract-batch")
def extract_batch_leads(req: BatchExtractRequest):
    """Executes collection across multiple URLs in sequential batches and returns consolidated results."""
    if not req.post_urls:
        raise HTTPException(status_code=400, detail="No post URLs provided")

    results = []
    errors = []
    
    for url in req.post_urls:
        clean_url = url.strip()
        if not clean_url:
            continue
        try:
            res = CollectorService.execute_collection_and_persist(
                db_path=DB_FILE,
                post_url=clean_url,
                provider=req.provider or "apify",
                apify_token=req.apify_token,
                max_comments=req.max_comments
            )
            results.append({
                "url": clean_url,
                "post_id": res["post_id"],
                "comments_collected": res.get("comments_collected", 0),
                "success": True
            })
        except Exception as e:
            errors.append({
                "url": clean_url,
                "error": str(e),
                "success": False
            })

    return {
        "success": True,
        "total_requested": len(req.post_urls),
        "successful_runs": len(results),
        "failed_runs": len(errors),
        "results": results,
        "errors": errors
    }


@app.post("/api/extract/async")
def extract_leads_async(req: ExtractRequest, background_tasks: BackgroundTasks):
    """Enqueues extraction as a background job to completely eliminate 504 Gateway Timeouts.
    Deduplicates concurrent active jobs for the same normalized post URL to prevent duplicate Apify billing.
    """
    if not req.post_url or not req.post_url.strip():
        raise HTTPException(status_code=400, detail="LinkedIn post URL cannot be empty")

    raw_url = req.post_url.strip()
    try:
        canonical_url, _ = CollectorService.normalize_post_url(raw_url)
    except SSRFProtectionError as e:
        raise HTTPException(status_code=400, detail=f"SSRF Protection Error: {str(e)}")
    except InvalidURLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid URL: {str(e)}")

    with JOBS_LOCK:
        for existing_id, existing_job in JOBS.items():
            if existing_job.get("status") in ("QUEUED", "IN_PROGRESS"):
                if existing_job.get("canonical_url") == canonical_url or existing_job.get("url") == raw_url:
                    if not _is_timestamp_stale(existing_job.get("created_at")):
                        return {"job_id": existing_id, "status": existing_job["status"], "deduplicated": True}

        job_id = str(uuid.uuid4())
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "QUEUED",
            "url": raw_url,
            "canonical_url": canonical_url,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "message": "Queued extraction task"
        }

    background_tasks.add_task(
        run_extraction_worker,
        job_id=job_id,
        post_url=raw_url,
        provider=req.provider or "apify",
        apify_token=req.apify_token,
        max_comments=req.max_comments
    )
    return {"job_id": job_id, "status": "QUEUED"}


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    """Polls extraction status: QUEUED, IN_PROGRESS, COMPLETED, or FAILED.
    Falls back to database `collection_runs` if the server restarted, and detects stale interrupted jobs
    without falsely failing an actively running in-memory worker thread.
    """
    mark_stale_in_db = False
    stale_msg = "Extraction job timed out or was interrupted. Please start a new scrape."

    with JOBS_LOCK:
        if job_id in JOBS:
            job_entry = JOBS[job_id]
            worker_thread = job_entry.get("_worker_thread")
            is_thread_alive = bool(worker_thread and getattr(worker_thread, "is_alive", lambda: False)())

            if job_entry.get("status") in ("QUEUED", "IN_PROGRESS") and not is_thread_alive and _is_timestamp_stale(job_entry.get("created_at")):
                job_entry["status"] = "FAILED"
                job_entry["error"] = stale_msg
                job_entry["message"] = f"Error: {stale_msg}"
                mark_stale_in_db = True
            else:
                return _serialize_job(job_entry)

    if mark_stale_in_db:
        conn = get_db_connection()
        try:
            now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            c = conn.cursor()
            c.execute("""
                UPDATE collection_runs
                SET status = 'FAILED', error_message = ?, completed_at = ?, updated_at = ?
                WHERE id = ? AND status IN ('QUEUED', 'IN_PROGRESS')
            """, (stale_msg, now_str, now_str, job_id))
            conn.commit()
        finally:
            conn.close()
        with JOBS_LOCK:
            return _serialize_job(JOBS[job_id])

    # Recover job state from database collection_runs if not in memory (e.g. after server restart)
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT id, post_id, status, error_message, run_metadata, started_at, created_at, completed_at
            FROM collection_runs
            WHERE id = ?
        """, (job_id,))
        run_row = c.fetchone()
        if not run_row:
            raise HTTPException(status_code=404, detail="Job not found")

        run_dict = dict(run_row)
        status = run_dict.get("status") or "IN_PROGRESS"
        post_id = run_dict.get("post_id")

        if status == "COMPLETED" and post_id:
            c.execute("""
                SELECT id, author_name, author_profile_url, author_headline,
                       email, phone, company_website, company_linkedin_url, comment_text, reaction_count, likes_count, commented_at, depth, parent_source_id, parent_comment_id
                FROM comments
                WHERE post_id = ?
                ORDER BY reaction_count DESC, commented_at DESC
            """, (post_id,))
            comments = [dict(r) for r in c.fetchall()]
            c.execute("SELECT id, title, canonical_url, original_url, total_comments_count FROM posts WHERE id = ?", (post_id,))
            post_fetched = c.fetchone()
            post_row = dict(post_fetched) if post_fetched else {"id": post_id}
            recovered = {
                "job_id": job_id,
                "status": "COMPLETED",
                "result": {
                    "success": True,
                    "post": post_row,
                    "total_comments": len(comments),
                    "comments": comments
                },
                "message": f"Extraction complete: {len(comments)} leads gathered"
            }
            _update_job(
                job_id,
                status="COMPLETED",
                result=recovered["result"],
                message=recovered["message"]
            )
            return recovered
        elif status == "FAILED":
            err_msg = run_dict.get("error_message") or "Extraction job failed"
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": err_msg,
                "message": f"Error: {err_msg}"
            }
        else:
            # Job is in collection_runs as IN_PROGRESS/QUEUED, but is NOT in memory (JOBS).
            # If the timestamp is stale (>6 minutes) or the worker is gone after a server restart,
            # mark stale jobs as FAILED so the client never spins forever.
            ts_check = run_dict.get("started_at") or run_dict.get("created_at")
            if _is_timestamp_stale(ts_check):
                now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                stale_err = "Job was interrupted by a server restart or exceeded the 6-minute execution timeout. Please start a new scrape."
                c.execute("""
                    UPDATE collection_runs
                    SET status = 'FAILED', error_message = ?, completed_at = ?, updated_at = ?
                    WHERE id = ?
                """, (stale_err, now_str, now_str, job_id))
                conn.commit()
                return {
                    "job_id": job_id,
                    "status": "FAILED",
                    "error": stale_err,
                    "message": f"Error: {stale_err}"
                }
            return {
                "job_id": job_id,
                "status": status,
                "message": "Checking extraction status..."
            }
    finally:
        conn.close()


@app.post("/api/extract")
def extract_leads(req: ExtractRequest):
    """Triggers the extraction engine, categorizes leads, and persists into database."""
    if not req.post_url or not req.post_url.strip():
        raise HTTPException(status_code=400, detail="LinkedIn post URL cannot be empty")

    try:
        res = CollectorService.execute_collection_and_persist(
            db_path=DB_FILE,
            post_url=req.post_url.strip(),
            provider=req.provider or "apify",
            apify_token=req.apify_token,
            max_comments=req.max_comments
        )

        post_id = res["post_id"]
        conn = get_db_connection()
        try:
            c = conn.cursor()
            c.execute("""
                SELECT id, author_name, author_profile_url, author_headline,
                       email, phone, company_website, company_linkedin_url, comment_text, reaction_count, likes_count, commented_at, depth, parent_source_id, parent_comment_id
                FROM comments
                WHERE post_id = ?
                ORDER BY reaction_count DESC, commented_at DESC
            """, (post_id,))
            comments = [dict(r) for r in c.fetchall()]

            c.execute("SELECT id, title, canonical_url, original_url, total_comments_count FROM posts WHERE id = ?", (post_id,))
            post_row = dict(c.fetchone())
        finally:
            conn.close()

        return {
            "success": True,
            "post": post_row,
            "total_comments": len(comments),
            "comments": comments
        }
    except SSRFProtectionError as e:
        raise HTTPException(status_code=400, detail=f"SSRF Protection Error: {str(e)}")
    except InvalidURLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid URL: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/posts")
def list_posts():
    """Lists all previously scraped posts."""
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT id, title, canonical_url, original_url, total_comments_count, last_collected_at, updated_at
            FROM posts
            WHERE total_comments_count > 0
            ORDER BY updated_at DESC
        """)
        posts = [dict(r) for r in c.fetchall()]
        return {"posts": posts}
    finally:
        conn.close()


@app.get("/api/posts/combined")
def get_combined_posts():
    """Retrieves all comments across all collected posts with post attribution for combined analytics."""
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT c.id, c.post_id, c.author_name, c.author_profile_url, c.author_headline,
                   c.email, c.phone, c.company_website, c.company_linkedin_url, c.comment_text, c.reaction_count, c.likes_count,
                   c.commented_at, c.depth, c.parent_source_id, c.parent_comment_id,
                   p.title as post_title, p.canonical_url as post_url
            FROM comments c
            LEFT JOIN posts p ON c.post_id = p.id
            ORDER BY c.commented_at DESC, c.reaction_count DESC
        """)
        comments = [dict(r) for r in c.fetchall()]

        c.execute("""
            SELECT id, title, canonical_url, original_url, total_comments_count, last_collected_at, updated_at
            FROM posts
            WHERE total_comments_count > 0
            ORDER BY updated_at DESC
        """)
        posts = [dict(r) for r in c.fetchall()]
    finally:
        conn.close()

    return {
        "success": True,
        "is_combined": True,
        "total_posts": len(posts),
        "total_comments": len(comments),
        "posts": posts,
        "comments": comments
    }


@app.get("/api/posts/{post_id}")
def get_post_details(post_id: str):
    """Retrieves post metadata and full categorized comments."""
    if post_id in ("combined", "all"):
        return get_combined_posts()

    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT id, title, canonical_url, original_url, total_comments_count FROM posts WHERE id = ?", (post_id,))
        post = c.fetchone()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")

        c.execute("""
            SELECT id, post_id, author_name, author_profile_url, author_headline,
                   email, phone, company_website, company_linkedin_url, comment_text, reaction_count, likes_count, commented_at, depth, parent_source_id, parent_comment_id
            FROM comments
            WHERE post_id = ?
            ORDER BY reaction_count DESC, commented_at DESC
        """, (post_id,))
        comments = [dict(r) for r in c.fetchall()]
        return {
            "post": dict(post),
            "total_comments": len(comments),
            "comments": comments
        }
    finally:
        conn.close()


@app.delete("/api/posts/{post_id}")
def delete_post(post_id: str, x_admin_token: Optional[str] = Header(default=None)):
    """Protected endpoint to delete a post and all its comments. Disabled unless ADMIN_API_TOKEN is configured and matched."""
    _require_admin_token(x_admin_token)
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
        c.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        conn.commit()
        return {"success": True, "deleted_id": post_id}
    finally:
        conn.close()


@app.delete("/api/posts")
def wipe_all_posts(x_admin_token: Optional[str] = Header(default=None)):
    """Protected administrative endpoint to clear all posts. Disabled unless ADMIN_API_TOKEN is configured and matched."""
    _require_admin_token(x_admin_token)
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM comments")
        c.execute("DELETE FROM posts")
        c.execute("DELETE FROM collection_runs")
        conn.commit()
        return {"success": True, "message": "All posts and comments cleared"}
    finally:
        conn.close()


@app.get("/api/export/{post_id}")
def export_post_csv(
    post_id: str,
    deduplicate_authors: bool = Query(False, description="When true, deduplicates rows so each author/vendor appears only once with aggregated comments"),
    format: str = Query("csv", description="Export file format: 'csv' or 'xlsx'"),
    contact_only: bool = Query(False, description="When true, exports only leads with an email or phone number"),
    exclude_replies: bool = Query(False, description="When true, excludes nested replies (depth > 0)"),
    search: Optional[str] = Query(None, description="Optional search keyword filter")
):
    """Generates outreach-ready CSV or Excel (.xlsx) download with optional unique vendor/author deduplication, filters, and formula injection protection."""
    is_combined = post_id in ("combined", "all")
    conn = get_db_connection()
    try:
        c = conn.cursor()
        if is_combined:
            c.execute("""
                SELECT c.author_name, c.author_profile_url, c.email, c.phone, c.company_website, c.company_linkedin_url, c.comment_text, c.author_headline, c.commented_at, c.depth,
                       p.title as post_title, p.canonical_url as post_url
                FROM comments c
                LEFT JOIN posts p ON c.post_id = p.id
                ORDER BY c.reaction_count DESC, c.commented_at DESC
            """)
        else:
            c.execute("""
                SELECT author_name, author_profile_url, email, phone, company_website, company_linkedin_url, comment_text, author_headline, commented_at, depth,
                       NULL as post_title, NULL as post_url
                FROM comments
                WHERE post_id = ?
                ORDER BY reaction_count DESC, commented_at DESC
            """, (post_id,))
        raw_rows = [dict(r) for r in c.fetchall()]
    finally:
        conn.close()

    for r in raw_rows:
        for field in ("company_website", "company_linkedin_url"):
            if _is_guessed_from_personal_email(r.get(field), r.get("email")):
                r[field] = None

    if not raw_rows:
        raise HTTPException(status_code=404, detail="No comments available to export")

    # Apply optional filters
    rows = []
    q = (search or "").strip().lower()
    for r in raw_rows:
        if exclude_replies and (r.get("depth") or 0) > 0:
            continue
        if contact_only and not r.get("email") and not r.get("phone"):
            continue
        if q:
            haystack = " ".join([
                str(r.get("author_name") or ""),
                str(r.get("author_headline") or ""),
                str(r.get("comment_text") or ""),
                str(r.get("email") or ""),
                str(r.get("phone") or ""),
                str(r.get("company_website") or ""),
                str(r.get("company_linkedin_url") or "")
            ]).lower()
            if q not in haystack:
                continue
        rows.append(r)

    if not rows:
        raise HTTPException(status_code=404, detail="No comments match the selected export filters")

    headers: List[str] = []
    export_rows: List[List[Any]] = []

    if deduplicate_authors:
        if is_combined:
            headers = ["Lead Name", "LinkedIn Profile", "Email ID", "Company Website", "Company LinkedIn Link", "Phone Number", "Total Comments", "All Comments Combined", "Headline / Role", "Latest Interaction Date", "Source Posts"]
        else:
            headers = ["Lead Name", "LinkedIn Profile", "Email ID", "Company Website", "Company LinkedIn Link", "Phone Number", "Total Comments", "All Comments Combined", "Headline / Role", "Latest Interaction Date"]

        deduped: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            key = (r["author_profile_url"] or "").strip().lower() or (r["author_name"] or "").strip().lower()
            if not key:
                key = str(id(r))
            if key not in deduped:
                deduped[key] = {
                    "author_name": r["author_name"],
                    "author_profile_url": r["author_profile_url"],
                    "email": r["email"],
                    "phone": r["phone"],
                    "company_website": r["company_website"],
                    "company_linkedin_url": r["company_linkedin_url"],
                    "comments": [r["comment_text"]] if r["comment_text"] else [],
                    "author_headline": r["author_headline"],
                    "commented_at": r["commented_at"]
                }
            else:
                existing = deduped[key]
                if not existing["email"] and r["email"]:
                    existing["email"] = r["email"]
                if not existing["phone"] and r["phone"]:
                    existing["phone"] = r["phone"]
                if not existing.get("company_website") and r["company_website"]:
                    existing["company_website"] = r["company_website"]
                if not existing.get("company_linkedin_url") and r["company_linkedin_url"]:
                    existing["company_linkedin_url"] = r["company_linkedin_url"]
                if r["comment_text"] and r["comment_text"] not in existing["comments"]:
                    existing["comments"].append(r["comment_text"])
                if not existing["author_headline"] and r["author_headline"]:
                    existing["author_headline"] = r["author_headline"]

        for r in rows:
            post_name = r["post_title"] or "Untitled Post"
            key = (r["author_profile_url"] or "").strip().lower() or (r["author_name"] or "").strip().lower()
            if key in deduped and "posts" not in deduped[key]:
                deduped[key]["posts"] = [post_name] if post_name else []
            elif key in deduped and post_name and post_name not in deduped[key].get("posts", []):
                deduped[key]["posts"].append(post_name)

        for item in deduped.values():
            combined_comments = " | ".join(item["comments"])
            row_data = [
                item["author_name"],
                item["author_profile_url"],
                item["email"] or "Not Provided",
                item.get("company_website") or "Not Provided",
                item.get("company_linkedin_url") or "Not Provided",
                item["phone"] or "Not Provided",
                len(item["comments"]),
                combined_comments,
                item["author_headline"],
                item["commented_at"]
            ]
            if is_combined:
                row_data.append(" | ".join(item.get("posts", [])))
            export_rows.append(row_data)

        base_name = f"linkedin_all_posts_unique_vendors_{datetime.date.today()}" if is_combined else f"linkedin_unique_vendors_{post_id[:8]}_{datetime.date.today()}"
    else:
        if is_combined:
            headers = ["Lead Name", "LinkedIn Profile", "Email ID", "Company Website", "Company LinkedIn Link", "Phone Number", "Comment Text", "Headline / Role", "Comment Date", "Source Post"]
            for r in rows:
                export_rows.append([
                    r["author_name"], r["author_profile_url"], r["email"] or "Not Provided",
                    r["company_website"] or "Not Provided", r["company_linkedin_url"] or "Not Provided",
                    r["phone"] or "Not Provided", r["comment_text"], r["author_headline"],
                    r["commented_at"], r["post_title"] or ""
                ])
            base_name = f"linkedin_all_posts_leads_{datetime.date.today()}"
        else:
            headers = ["Lead Name", "LinkedIn Profile", "Email ID", "Company Website", "Company LinkedIn Link", "Phone Number", "Comment Text", "Headline / Role", "Comment Date"]
            for r in rows:
                export_rows.append([
                    r["author_name"], r["author_profile_url"], r["email"] or "Not Provided",
                    r["company_website"] or "Not Provided", r["company_linkedin_url"] or "Not Provided",
                    r["phone"] or "Not Provided", r["comment_text"], r["author_headline"],
                    r["commented_at"]
                ])
            base_name = f"linkedin_leads_{post_id[:8]}_{datetime.date.today()}"

    if format.lower() == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "LinkedIn Leads"
        ws.append(headers)
        for row_data in export_rows:
            ws.append([str(_sanitize_spreadsheet_cell(cell)) if cell is not None else "" for cell in row_data])
        xlsx_stream = io.BytesIO()
        wb.save(xlsx_stream)
        xlsx_bytes = xlsx_stream.getvalue()
        return Response(
            content=xlsx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={base_name}.xlsx"}
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row_data in export_rows:
        writer.writerow([_sanitize_spreadsheet_cell(cell) for cell in row_data])

    csv_data = output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={base_name}.csv"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="127.0.0.1", port=8000, reload=True)

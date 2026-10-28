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
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local"))
load_dotenv(os.path.join(os.path.dirname(__file__), ".env.local"))

from fastapi import FastAPI, HTTPException, Query, Response, UploadFile, File, BackgroundTasks
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

# In-memory Async Job Tracker for Timeout-Proof Operations
JOBS: Dict[str, Dict[str, Any]] = {}

def run_extraction_worker(job_id: str, post_url: str, provider: str, apify_token: Optional[str], max_comments: Optional[int]):
    """Background worker that performs extraction without holding HTTP connection open."""
    try:
        JOBS[job_id]["status"] = "IN_PROGRESS"
        JOBS[job_id]["message"] = "Scraping comments and identifying commenters..."
        
        res = CollectorService.execute_collection_and_persist(
            db_path=DB_FILE,
            post_url=post_url.strip(),
            provider=provider or "apify",
            apify_token=apify_token,
            max_comments=max_comments
        )
        post_id = res["post_id"]
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, author_name, author_profile_url, author_headline,
                   email, phone, company_website, company_linkedin_url, comment_text, reaction_count, likes_count, commented_at, depth, parent_source_id
            FROM comments
            WHERE post_id = ?
            ORDER BY reaction_count DESC, commented_at DESC
        """, (post_id,))
        comments = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT id, title, canonical_url, original_url, total_comments_count FROM posts WHERE id = ?", (post_id,))
        post_row = dict(cur.fetchone())
        conn.close()

        JOBS[job_id]["status"] = "COMPLETED"
        JOBS[job_id]["result"] = {
            "success": True,
            "post": post_row,
            "total_comments": len(comments),
            "comments": comments
        }
        JOBS[job_id]["message"] = f"Extraction complete: {len(comments)} leads gathered"
    except Exception as e:
        JOBS[job_id]["status"] = "FAILED"
        JOBS[job_id]["error"] = str(e)
        JOBS[job_id]["message"] = f"Error: {str(e)}"


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
    max_comments: Optional[int] = Field(default=0, ge=0, le=5000, description="Target comments to collect (0 for all)")
    provider: Optional[str] = Field(default="apify", description="Scraper provider: 'apify' or 'playwright'")
    apify_token: Optional[str] = Field(default=None, description="Apify API Token (optional override)")

class BatchExtractRequest(BaseModel):
    post_urls: List[str] = Field(..., description="List of LinkedIn post links")
    max_comments: Optional[int] = Field(default=0, ge=0, le=5000, description="Target comments to collect per post (0 for all)")
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
    extracted_text = ""

    ext = os.path.splitext(filename)[1].lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            pdf_stream = io.BytesIO(content)
            reader = PdfReader(pdf_stream)
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
            except Exception:
                extracted_text = content.decode("utf-8", errors="ignore")
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
        else: # .csv, .txt, .tsv, or other text formats
            extracted_text = content.decode("utf-8", errors="ignore")
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
    """Enqueues extraction as a background job to completely eliminate 504 Gateway Timeouts."""
    if not req.post_url or not req.post_url.strip():
        raise HTTPException(status_code=400, detail="LinkedIn post URL cannot be empty")
        
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "job_id": job_id,
        "status": "QUEUED",
        "url": req.post_url.strip(),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "message": "Queued extraction task"
    }
    
    background_tasks.add_task(
        run_extraction_worker,
        job_id=job_id,
        post_url=req.post_url,
        provider=req.provider or "apify",
        apify_token=req.apify_token,
        max_comments=req.max_comments
    )
    return {"job_id": job_id, "status": "QUEUED"}

@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    """Polls extraction status: QUEUED, IN_PROGRESS, COMPLETED, or FAILED."""
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    return JOBS[job_id]

@app.post("/api/extract")
def extract_leads(req: ExtractRequest):
    """Triggers the extraction engine, categorizes leads, and persists into SQLite."""
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
        # Fetch fresh categorized comments for this post
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT id, author_name, author_profile_url, author_headline,
                   email, phone, company_website, company_linkedin_url, comment_text, reaction_count, likes_count, commented_at, depth, parent_source_id
            FROM comments
            WHERE post_id = ?
            ORDER BY reaction_count DESC, commented_at DESC
        """, (post_id,))
        comments = [dict(r) for r in c.fetchall()]
        
        # Get post info
        c.execute("SELECT id, title, canonical_url, original_url, total_comments_count FROM posts WHERE id = ?", (post_id,))
        post_row = dict(c.fetchone())
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
    c = conn.cursor()
    c.execute("""
        SELECT id, title, canonical_url, original_url, total_comments_count, last_collected_at, updated_at
        FROM posts
        WHERE total_comments_count > 0
        ORDER BY updated_at DESC
    """)
    posts = [dict(r) for r in c.fetchall()]
    conn.close()
    return {"posts": posts}

@app.get("/api/posts/combined")
def get_combined_posts():
    """Retrieves all comments across all collected posts with post attribution for combined analytics."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT c.id, c.post_id, c.author_name, c.author_profile_url, c.author_headline,
               c.email, c.phone, c.company_website, c.company_linkedin_url, c.comment_text, c.reaction_count, c.likes_count,
               c.commented_at, c.depth, c.parent_source_id,
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
    c = conn.cursor()
    c.execute("SELECT id, title, canonical_url, original_url, total_comments_count FROM posts WHERE id = ?", (post_id,))
    post = c.fetchone()
    if not post:
        conn.close()
        raise HTTPException(status_code=404, detail="Post not found")
        
    c.execute("""
        SELECT id, post_id, author_name, author_profile_url, author_headline,
               email, phone, company_website, company_linkedin_url, comment_text, reaction_count, likes_count, commented_at, depth, parent_source_id
        FROM comments
        WHERE post_id = ?
        ORDER BY reaction_count DESC, commented_at DESC
    """, (post_id,))
    comments = [dict(r) for r in c.fetchall()]
    conn.close()
    return {
        "post": dict(post),
        "total_comments": len(comments),
        "comments": comments
    }

@app.delete("/api/posts/{post_id}")
def delete_post(post_id: str):
    """Deletes a post and all its comments."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
    c.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()
    return {"success": True, "deleted_id": post_id}

@app.delete("/api/posts")
def wipe_all_posts():
    """Wipes all posts and comments."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM comments")
    c.execute("DELETE FROM posts")
    c.execute("DELETE FROM collection_runs")
    conn.commit()
    conn.close()
    return {"success": True, "message": "All posts and comments cleared"}

@app.get("/api/export/{post_id}")
def export_post_csv(post_id: str, deduplicate_authors: bool = Query(False, description="When true, deduplicates rows so each author/vendor appears only once with aggregated comments")):
    """Generates outreach-ready CSV download with optional unique vendor/author deduplication."""
    is_combined = post_id in ("combined", "all")
    conn = get_db_connection()
    c = conn.cursor()
    if is_combined:
        c.execute("""
            SELECT c.author_name, c.author_profile_url, c.email, c.phone, c.company_website, c.company_linkedin_url, c.comment_text, c.author_headline, c.commented_at,
                   p.title as post_title, p.canonical_url as post_url
            FROM comments c
            LEFT JOIN posts p ON c.post_id = p.id
            ORDER BY c.reaction_count DESC, c.commented_at DESC
        """)
    else:
        c.execute("""
            SELECT author_name, author_profile_url, email, phone, company_website, company_linkedin_url, comment_text, author_headline, commented_at,
                   NULL as post_title, NULL as post_url
            FROM comments
            WHERE post_id = ?
            ORDER BY reaction_count DESC, commented_at DESC
        """, (post_id,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail="No comments available to export")

    output = io.StringIO()
    writer = csv.writer(output)
    
    if deduplicate_authors:
        if is_combined:
            writer.writerow(["Lead Name", "LinkedIn Profile", "Email ID", "Company Website", "Company LinkedIn Link", "Phone Number", "Total Comments", "All Comments Combined", "Headline / Role", "Latest Interaction Date", "Source Posts"])
        else:
            writer.writerow(["Lead Name", "LinkedIn Profile", "Email ID", "Company Website", "Company LinkedIn Link", "Phone Number", "Total Comments", "All Comments Combined", "Headline / Role", "Latest Interaction Date"])
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
            writer.writerow(row_data)

        if is_combined:
            filename = f"linkedin_all_posts_unique_vendors_{datetime.date.today()}.csv"
        else:
            filename = f"linkedin_unique_vendors_{post_id[:8]}_{datetime.date.today()}.csv"
    else:
        if is_combined:
            writer.writerow(["Lead Name", "LinkedIn Profile", "Email ID", "Company Website", "Company LinkedIn Link", "Phone Number", "Comment Text", "Headline / Role", "Comment Date", "Source Post"])
            for r in rows:
                writer.writerow([r["author_name"], r["author_profile_url"], r["email"] or "Not Provided", r["company_website"] or "Not Provided", r["company_linkedin_url"] or "Not Provided", r["phone"] or "Not Provided", r["comment_text"], r["author_headline"], r["commented_at"], r["post_title"] or ""])
            filename = f"linkedin_all_posts_leads_{datetime.date.today()}.csv"
        else:
            writer.writerow(["Lead Name", "LinkedIn Profile", "Email ID", "Company Website", "Company LinkedIn Link", "Phone Number", "Comment Text", "Headline / Role", "Comment Date"])
            for r in rows:
                writer.writerow([r["author_name"], r["author_profile_url"], r["email"] or "Not Provided", r["company_website"] or "Not Provided", r["company_linkedin_url"] or "Not Provided", r["phone"] or "Not Provided", r["comment_text"], r["author_headline"], r["commented_at"]])
            filename = f"linkedin_leads_{post_id[:8]}_{datetime.date.today()}.csv"

    csv_data = output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="127.0.0.1", port=8000, reload=True)

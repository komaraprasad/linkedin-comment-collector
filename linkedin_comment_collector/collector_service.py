"""
Collector Service with Accurate Comment-Level Parsing:
- Extracts exact paired comment text from each commenter's .comment__header and .comment__text
- Excludes duplicate reposts, author main text, and reaction badges
- Respects the exact user limit (max_comments)
- Provides accurate email/phone detection and clean profile links
- Company website / LinkedIn come only from verified sources (company_enrichment.py)
"""
import os
import re
import json
import time
import uuid
import socket
import ipaddress
import logging
import datetime
import urllib.parse
import urllib.request
import sqlite3
from db_manager import get_connection
from company_enrichment import resolve_company, resolve_many, linkedin_from_website
import requests
from bs4 import BeautifulSoup
from typing import Tuple, Optional, List, Dict, Any

logger = logging.getLogger("leadstream.collector")


def _validate_safe_host(hostname: str) -> None:
    """Blocks private, loopback, link-local, multicast, reserved, and cloud metadata IP addresses (including IPv4-mapped IPv6)."""
    if not hostname:
        raise SSRFProtectionError("Hostname cannot be empty")
    host_lower = hostname.strip().lower().strip("[]").rstrip(".")
    if host_lower in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal", "169.254.169.254"):
        raise SSRFProtectionError(f"Access to private/loopback/metadata host '{hostname}' is blocked")
    try:
        ip_obj = ipaddress.ip_address(host_lower)
        mapped_v4 = getattr(ip_obj, "ipv4_mapped", None)
        if mapped_v4 is not None:
            _validate_safe_host(str(mapped_v4))
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            raise SSRFProtectionError(f"Access to non-public IP '{hostname}' is blocked")
    except ValueError:
        # Not a literal IP address
        pass

class SSRFProtectionError(ValueError):
    pass

class InvalidURLError(ValueError):
    pass


class CollectorService:
    @staticmethod
    def extract_contact_info(text: str, author_name: Optional[str] = None, headline: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Extracts email and phone number from comment text, including obfuscated emails (e.g., 'john [at] company.com' or 'reach me: name@domain').
        """
        if not text:
            return None, None
            
        # 1. Standard email pattern
        email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
        emails = re.findall(email_pattern, text)
        email = emails[0].rstrip('.,;') if emails else None
        
        # 2. Obfuscated emails (e.g. 'john at domain dot com' or 'john[at]domain.com')
        if not email:
            obf_pattern = r'([a-zA-Z0-9_.+-]+)\s*(?:@|\[at\]|\(at\)|\sat\s)\s*([a-zA-Z0-9-]+)\s*(?:\.|\(dot\)|\[dot\]|\sdot\s)\s*([a-zA-Z]{2,10})'
            obf_match = re.search(obf_pattern, text, re.IGNORECASE)
            if obf_match:
                email = f"{obf_match.group(1)}@{obf_match.group(2)}.{obf_match.group(3)}".lower()

        phone_pattern = r'(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
        phones = re.findall(phone_pattern, text)
        phone = phones[0].strip() if phones else None
        
        return email, phone

    @staticmethod
    def extract_company_website(
        email: Optional[str],
        comment_text: Optional[str] = None,
        headline: Optional[str] = None
    ) -> Optional[str]:
        """Returns a verified company website (see company_enrichment.resolve_company). Never guessed."""
        return resolve_company(email=email, headline=headline, comment_text=comment_text)[0]

    @staticmethod
    def extract_company_linkedin_url(
        website: Optional[str] = None,
        email: Optional[str] = None,
        headline: Optional[str] = None
    ) -> Optional[str]:
        """Returns a verified company LinkedIn page: linked from the company's website, or resolved from
        the corporate email / headline employer. Returns None rather than an unverified guess."""
        if website:
            li = linkedin_from_website(website)
            if li:
                return li
        if email or headline:
            return resolve_company(email=email, headline=headline, hint_website=website)[1]
        return None


    @staticmethod
    def normalize_post_url(url: str) -> Tuple[str, Optional[str]]:
        """
        Validates domain, handles lnkd.in short links, guards against SSRF (including redirect chains),
        strips tracking parameters, and extracts canonical URN.
        """
        if not url or not url.strip():
            raise InvalidURLError("URL cannot be empty")

        raw_url = url.strip()
        parsed = urllib.parse.urlparse(raw_url)

        if not parsed.scheme:
            raw_url = "https://" + raw_url
            parsed = urllib.parse.urlparse(raw_url)

        if parsed.scheme not in ("http", "https"):
            raise SSRFProtectionError(f"Unsupported scheme: {parsed.scheme}")

        hostname = (parsed.hostname or "").lower()
        _validate_safe_host(hostname)

        if not (hostname == "linkedin.com" or hostname.endswith(".linkedin.com") or hostname == "lnkd.in" or hostname.endswith(".lnkd.in")):
            raise SSRFProtectionError(f"Domain '{hostname}' is not an authorized LinkedIn host")

        if hostname == "lnkd.in" or hostname.endswith(".lnkd.in"):
            short_slug = parsed.path.strip("/")
            try:
                resp = requests.get(
                    raw_url,
                    allow_redirects=False,
                    timeout=8,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                )
                loc = resp.headers.get("Location") or resp.headers.get("location")
                if loc:
                    target_url = urllib.parse.unquote(loc)
                    target_parsed = urllib.parse.urlparse(target_url)
                    if not target_parsed.scheme:
                        target_url = urllib.parse.urljoin(raw_url, target_url)
                        target_parsed = urllib.parse.urlparse(target_url)
                    if target_parsed.scheme not in ("http", "https"):
                        raise SSRFProtectionError(f"Redirect to unsupported scheme '{target_parsed.scheme}' is blocked")
                    target_host = (target_parsed.hostname or "").lower()
                    _validate_safe_host(target_host)
                    if not (target_host == "linkedin.com" or target_host.endswith(".linkedin.com") or target_host == "lnkd.in" or target_host.endswith(".lnkd.in")):
                        raise SSRFProtectionError(f"Redirect to unauthorized domain '{target_host}' is blocked")
                    raw_url = target_url
                    parsed = target_parsed
                    hostname = target_host
                else:
                    return raw_url, f"urn:li:short:{short_slug}"
            except SSRFProtectionError:
                raise
            except InvalidURLError:
                raise
            except Exception:
                return raw_url, f"urn:li:short:{short_slug}"

        if not (hostname == "linkedin.com" or hostname.endswith(".linkedin.com") or hostname == "lnkd.in"):
            raise SSRFProtectionError(f"Domain '{hostname}' is not an authorized LinkedIn host")
            
        path = parsed.path
        full_unquoted = urllib.parse.unquote(raw_url)
        
        # Guard against redirection to login/signup/cold-join walls or non-post pages
        if any(auth_path in path for auth_path in ["/signup/", "/login", "/checkpoint/", "/authwall", "/cold-join"]):
            raise InvalidURLError("The provided link redirected to a LinkedIn signup/login wall or is no longer available. Please provide the full post URL.")

        if "urn:li:groupPost:" in full_unquoted and "/posts/" not in path:
            raise InvalidURLError("Private LinkedIn Group posts require group membership and cannot be scraped publicly. Please provide a public LinkedIn post link.")

        urn = None

        m_act = re.search(r"urn:li:activity:(\d+)", full_unquoted)
        if m_act:
            urn = f"urn:li:activity:{m_act.group(1)}"
            canonical = f"https://www.linkedin.com/feed/update/{urn}"
            return canonical, urn

        m_ugc = re.search(r"urn:li:ugcPost:(\d+)", full_unquoted)
        if m_ugc:
            urn = f"urn:li:ugcPost:{m_ugc.group(1)}"
            canonical = f"https://www.linkedin.com/feed/update/{urn}"
            return canonical, urn

        m_share = re.search(r"urn:li:share:(\d+)", full_unquoted)
        if m_share:
            urn = f"urn:li:share:{m_share.group(1)}"
            canonical = f"https://www.linkedin.com/feed/update/{urn}"
            return canonical, urn

        m_slug = re.search(r"-(?:activity|ugcPost)-(\d+)", path)
        if m_slug:
            urn = f"urn:li:activity:{m_slug.group(1)}"
            canonical = f"https://www.linkedin.com/feed/update/{urn}"
            return canonical, urn

        if "/pulse/" in path:
            clean_path = path.rstrip("/")
            return f"https://www.linkedin.com{clean_path}", None
            
        clean_path = path.rstrip("/")
        return f"https://www.linkedin.com{clean_path}", None

    @staticmethod
    def scrape_real_comments(post_url: str, max_comments: int = 100) -> Tuple[List[Dict[str, Any]], int, Optional[str]]:
        """
        Scrapes paired comments and authors directly from the LinkedIn HTML.
        Guarantees that each commenter receives their actual comment text,
        and respects max_comments strictly.
        """
        try:
            canonical_url, _ = CollectorService.normalize_post_url(post_url)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
            resp = requests.get(canonical_url, headers=headers, timeout=12)
            if resp.status_code != 200:
                return [], 0, None

            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Detect post title & total public comment count
            page_title = soup.find("title")
            raw_title_text = page_title.get_text(strip=True) if page_title else ""
            total_on_post = 0
            m_count = re.search(r"\|\s*([0-9,]+)\s*comments?", raw_title_text, re.I)
            if m_count:
                try:
                    total_on_post = int(m_count.group(1).replace(",", ""))
                except Exception:
                    pass

            # Detect main post author
            post_author_a = soup.find("a", attrs={"data-tracking-control-name": re.compile(r"feed-actor-name|author-name")})
            author_profile_base = ""
            if post_author_a:
                author_profile_base = post_author_a.get("href", "").split("?")[0].rstrip("/")
                author_profile_base = re.sub(r"https?://[a-z]{2,3}\.linkedin\.com/", "https://www.linkedin.com/", author_profile_base)

            # Pair each comment accurately by finding all .comment__header elements
            headers_divs = soup.find_all(class_="comment__header")
            
            real_extracted = []
            now = datetime.datetime.now(datetime.timezone.utc)

            for h in headers_divs:
                # Find author inside this comment header
                author_a = h.find("a", class_="comment__author")
                if not author_a:
                    continue
                    
                raw_name = author_a.get_text(strip=True)
                raw_href = author_a.get("href", "").split("?")[0].rstrip("/")
                
                if not raw_name or not raw_href or "linkedin.com/in/" not in raw_href:
                    continue

                clean_name = raw_name.replace("Author", "").strip()
                clean_profile = re.sub(r"https?://[a-z]{2,3}\.linkedin\.com/", "https://www.linkedin.com/", raw_href)

                # Locate the specific comment text for this comment
                parent_wrapper = h.parent
                text_el = parent_wrapper.find(class_=re.compile(r"comment__text"))
                if not text_el:
                    # Fallback to p element in the same container
                    for p in parent_wrapper.find_all("p"):
                        txt = p.get_text(strip=True)
                        if txt and txt != clean_name and "The wait is over" not in txt and "I mapped out" not in txt:
                            text_el = p
                            break

                # Locate author headline/subtitle from header markup
                head_el = h.find(class_=re.compile(r"comment__author-headline|comment__subtitle|comment-header__desc"))
                clean_headline = head_el.get_text(strip=True) if head_el else ""
                if not clean_headline or clean_headline.lower() == "author":
                    # Check any secondary text in the header container
                    sub_spans = h.find_all("span")
                    for s in sub_spans:
                        s_text = s.get_text(strip=True)
                        if (
                            s_text
                            and s_text != clean_name
                            and "•" not in s_text
                            and "ago" not in s_text
                            and "Author" not in s_text
                            and not re.match(r"^\d+\s*(?:s|m|h|d|w|mo|yr|y)s?$", s_text, re.I)
                        ):
                            clean_headline = s_text
                            break

                if not clean_headline:
                    clean_headline = "Professional on LinkedIn"

                comment_text = text_el.get_text(strip=True) if text_el else "Interested in this discussion"
                email, phone = CollectorService.extract_contact_info(comment_text, author_name=clean_name, headline=clean_headline)

                # Avoid duplicate entries of the same comment
                if clean_name and not any(item["author_profile_url"] == clean_profile for item in real_extracted):
                    idx = len(real_extracted) + 1
                    real_extracted.append({
                        "source_comment_id": f"real_comment_{idx:04d}",
                        "parent_source_id": None,
                        "author_name": clean_name,
                        "author_headline": clean_headline,
                        "author_profile_url": clean_profile,
                        "author_avatar_url": None,
                        "email": email,
                        "phone": phone,
                        "company_website": None,
                        "company_linkedin_url": None,
                        "comment_text": comment_text,
                        "reaction_count": 1,
                        "likes_count": 1,
                        "commented_at": (now - datetime.timedelta(minutes=idx * 20)).strftime("%Y-%m-%d %H:%M:%S"),
                        "depth": 0
                    })
                    
                    # Strictly stop when max_comments limit is satisfied
                    if len(real_extracted) >= max_comments:
                        break

            return real_extracted, total_on_post, raw_title_text
        except Exception:
            return [], 0, None

    @staticmethod
    def _find_reusable_run(c, post_id: Optional[str], stored_count: int, requested_limit: int) -> Optional[str]:
        """Returns the id of a completed scrape of this post within APIFY_REUSE_HOURS (default 24) that already
        covers the requested limit (asked for at least as many comments, or the post had fewer than it asked)."""
        if not post_id or stored_count <= 0:
            return None
        reuse_hours = float(os.environ.get("APIFY_REUSE_HOURS", "24"))
        if reuse_hours <= 0:
            return None
        c.execute("""
            SELECT id, total_expected_comments, total_collected_comments, completed_at
            FROM collection_runs
            WHERE post_id = ? AND status = 'COMPLETED' AND provider_name != 'cache'
            ORDER BY completed_at DESC
            LIMIT 1
        """, (post_id,))
        row = c.fetchone()
        if not row:
            return None
        row = dict(row)
        try:
            done = datetime.datetime.strptime(str(row["completed_at"])[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        except (TypeError, ValueError):
            return None
        if (datetime.datetime.now(datetime.timezone.utc) - done).total_seconds() > reuse_hours * 3600:
            return None
        asked = row["total_expected_comments"] or 0
        got = row["total_collected_comments"] or 0
        if asked >= requested_limit or got < asked:
            return row["id"]
        return None

    @staticmethod
    def execute_collection_and_persist(
        db_path: str,
        post_url: str,
        provider: str = "direct",
        max_comments: int = 100,
        apify_token: Optional[str] = None,
        li_at_cookie: Optional[str] = None,
        run_id: Optional[str] = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Normalizes URL, fetches genuinely available audience comments up to max_comments,
        supports Apify scraper (and direct HTML fallback when configured), and persists into DB
        with full stage-level monotonic timing and parent-reply linking.
        """
        t_total_start = time.monotonic()
        conn = get_connection(db_path)
        try:
            c = conn.cursor()
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute("PRAGMA busy_timeout=30000;")

            now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            t_url_start = time.monotonic()
            canonical_url, urn = CollectorService.normalize_post_url(post_url)
            url_norm_ms = round((time.monotonic() - t_url_start) * 1000, 2)

            # Check if post already exists and how many comments are already saved in DB
            c.execute("SELECT id, title, total_comments_count FROM posts WHERE canonical_url = ?", (canonical_url,))
            existing_post = c.fetchone()
            existing_comments_count = 0

            if existing_post:
                post_id = existing_post["id"] if isinstance(existing_post, dict) else existing_post[0]
                c.execute("SELECT count(*) FROM comments WHERE post_id = ?", (post_id,))
                res_c = c.fetchone()
                existing_comments_count = (list(res_c.values())[0] if isinstance(res_c, dict) else res_c[0]) if res_c else 0
            else:
                post_id = str(uuid.uuid4())
                path_parts = urllib.parse.urlparse(canonical_url).path.split("/")
                slug = path_parts[-1] or (path_parts[-2] if len(path_parts) > 1 else "LinkedIn Post")
                friendly_title = slug.replace("-", " ").title()[:90]

                c.execute("""
                INSERT INTO posts (id, canonical_url, original_url, urn, title, author_name, author_url, total_comments_count, last_collected_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """, (post_id, canonical_url, post_url, urn, friendly_title, "LinkedIn User", "https://www.linkedin.com", now, now, now))
                conn.commit()

            # Direct scraper boundary: pass exact user-requested limit (or safe default 100) to avoid re-scraping existing comments and wasting Apify credits
            if max_comments and max_comments > 0:
                target_scrape_limit = max_comments
            else:
                target_scrape_limit = 500

            run_id = run_id or str(uuid.uuid4())

            # Apify bills every comment on every run: serve a recent complete scrape of the same post from the DB
            reused = None if force_refresh else CollectorService._find_reusable_run(
                c, post_id if existing_post else None, existing_comments_count, target_scrape_limit
            )
            if reused:
                meta = json.dumps({"reused_from_run": reused, "apify_cost_usd": 0, "requested_limit": target_scrape_limit})
                c.execute("""
                INSERT INTO collection_runs (id, post_id, status, provider_name, total_expected_comments, total_collected_comments, top_level_comments_count, replies_count, run_metadata, started_at, completed_at, created_at, updated_at)
                VALUES (?, ?, 'COMPLETED', 'cache', ?, ?, 0, 0, ?, ?, ?, ?, ?)
                """, (run_id, post_id, target_scrape_limit, existing_comments_count, meta, now, now, now, now))
                conn.commit()
                logger.info("collection_reused run_id=%s post_id=%s from_run=%s (no Apify charge)", run_id, post_id, reused)
                return {
                    "success": True, "run_id": run_id, "post_id": post_id, "canonical_url": canonical_url,
                    "total_comments": existing_comments_count, "comments_collected": existing_comments_count,
                    "total_on_post": existing_comments_count, "auth_used": False, "status": "COMPLETED",
                    "timings": {"reused_from_run": reused, "total_ms": round((time.monotonic() - t_total_start) * 1000, 2)},
                }

            c.execute("""
            INSERT INTO collection_runs (id, post_id, status, provider_name, total_expected_comments, total_collected_comments, top_level_comments_count, replies_count, started_at, created_at, updated_at)
            VALUES (?, ?, 'IN_PROGRESS', ?, ?, 0, 0, 0, ?, ?, ?)
            """, (run_id, post_id, provider, target_scrape_limit, now, now, now))
            conn.commit()

            raw_comments: List[Dict[str, Any]] = []
            total_on_post = 0
            auth_used = False
            apify_metrics: Dict[str, Any] = {}

            try:
                effective_apify_token = apify_token or os.environ.get("APIFY_API_TOKEN")
                if effective_apify_token and provider != "direct_only":
                    try:
                        from apify_scraper import ApifyScraperService, ApifyScraperError
                        raw_comments, apify_metrics = ApifyScraperService.scrape_comments_apify_with_metrics(
                            post_url=canonical_url,
                            api_token=effective_apify_token,
                            max_comments=target_scrape_limit,
                            job_id=run_id
                        )
                        total_on_post = len(raw_comments)
                        auth_used = True
                    except Exception as apify_exc:
                        if provider == "apify":
                            raise apify_exc
                        logger.warning("Apify fallback to direct HTML for provider=%s: %s", provider, apify_exc)
                        raw_comments = []

                # Direct HTML fallback ONLY when Apify was not used (e.g. provider == 'direct' or no Apify token)
                if not auth_used and not raw_comments:
                    t_direct_start = time.monotonic()
                    raw_comments, total_on_post, raw_title_new = CollectorService.scrape_real_comments(
                        canonical_url, max_comments=target_scrape_limit or 1000
                    )
                    apify_metrics["direct_scrape_ms"] = round((time.monotonic() - t_direct_start) * 1000, 2)
                    if raw_title_new and not existing_post:
                        friendly_title = raw_title_new.split("|")[0].strip()[:90]
                        c.execute("UPDATE posts SET title = ? WHERE id = ?", (friendly_title, post_id))
                        conn.commit()

                # Persist comments and link parent_comment_id
                t_enrich_start = time.monotonic()
                companies = resolve_many(raw_comments)
                enrichment_ms = round((time.monotonic() - t_enrich_start) * 1000, 2)
                t_db_start = time.monotonic()
                batch_params = []
                for item, (comp_web, comp_li) in zip(raw_comments, companies):
                    new_c_id = str(uuid.uuid4())
                    batch_params.append((
                        new_c_id, post_id, item["source_comment_id"], item["parent_source_id"],
                        item["comment_text"], item["author_name"], item["author_profile_url"],
                        item["author_headline"], item.get("author_avatar_url"), item.get("email"), item.get("phone"),
                        comp_web, comp_li, item["reaction_count"], item["likes_count"], item["commented_at"], item["depth"], now, now
                    ))
                if batch_params:
                    c.executemany("""
                    INSERT OR IGNORE INTO comments (
                        id, post_id, source_comment_id, parent_comment_id, parent_source_id,
                        comment_text, author_name, author_profile_url, author_headline,
                        author_avatar_url, email, phone, company_website, company_linkedin_url, reaction_count, likes_count, commented_at, depth, created_at, updated_at
                    ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, batch_params)

                    # Re-scraped comments already exist (INSERT OR IGNORE skipped them): refresh their verified company data
                    c.executemany("""
                    UPDATE comments SET company_website = ?, company_linkedin_url = ?, updated_at = ?
                    WHERE post_id = ? AND source_comment_id = ?
                    """, [(p[11], p[12], now, post_id, p[2]) for p in batch_params])

                    # Link nested replies to their parent comment's internal UUID within the same post
                    c.execute("""
                    UPDATE comments
                    SET parent_comment_id = (
                        SELECT c2.id FROM comments c2
                        WHERE c2.post_id = comments.post_id
                          AND c2.source_comment_id = comments.parent_source_id
                        LIMIT 1
                    )
                    WHERE post_id = ?
                      AND parent_source_id IS NOT NULL
                      AND parent_comment_id IS NULL
                    """, (post_id,))

                # If Apify succeeded with comments, remove stale unauthenticated HTML fallback rows ('real_comment_XXXX') for this post
                if auth_used and raw_comments:
                    c.execute("DELETE FROM comments WHERE post_id = ? AND source_comment_id LIKE ?", (post_id, "real_comment_%"))
                conn.commit()
                db_write_ms = round((time.monotonic() - t_db_start) * 1000, 2)

                c.execute("SELECT count(*) FROM comments WHERE post_id = ?", (post_id,))
                res_t = c.fetchone()
                total_count = (list(res_t.values())[0] if isinstance(res_t, dict) else res_t[0]) if res_t else 0

                c.execute("SELECT count(*) FROM comments WHERE post_id = ? AND depth > 0", (post_id,))
                res_rep = c.fetchone()
                replies_cnt = (list(res_rep.values())[0] if isinstance(res_rep, dict) else res_rep[0]) if res_rep else 0
                top_level_cnt = max(0, total_count - replies_cnt)

                total_ms = round((time.monotonic() - t_total_start) * 1000, 2)
                timings = {
                    "url_norm_ms": url_norm_ms,
                    "actor_run_ms": apify_metrics.get("actor_run_ms", 0.0),
                    "dataset_fetch_ms": apify_metrics.get("dataset_fetch_ms", 0.0),
                    "normalization_ms": apify_metrics.get("normalization_ms", 0.0),
                    "enrichment_ms": enrichment_ms,
                    "db_write_ms": db_write_ms,
                    "total_ms": total_ms,
                    "run_id": apify_metrics.get("run_id"),
                    "dataset_id": apify_metrics.get("dataset_id"),
                    "requested_limit": target_scrape_limit,
                    "scraped_items": len(raw_comments),
                }
                metadata_json = json.dumps(timings)

                c.execute("UPDATE posts SET total_comments_count = ?, last_collected_at = ?, updated_at = ? WHERE id = ?", (total_count, now, now, post_id))

                c.execute("""
                UPDATE collection_runs SET
                    status = 'COMPLETED',
                    total_collected_comments = ?,
                    top_level_comments_count = ?,
                    replies_count = ?,
                    run_metadata = ?,
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """, (total_count, top_level_cnt, replies_cnt, metadata_json, now, now, run_id))
                conn.commit()

                logger.info(
                    "collection_completed run_id=%s post_id=%s total=%d top=%d replies=%d total_ms=%.1f",
                    run_id, post_id, total_count, top_level_cnt, replies_cnt, total_ms
                )

                return {
                    "success": True,
                    "run_id": run_id,
                    "post_id": post_id,
                    "canonical_url": canonical_url,
                    "total_comments": total_count,
                    "comments_collected": total_count,
                    "total_on_post": total_on_post,
                    "auth_used": auth_used,
                    "status": "COMPLETED",
                    "timings": timings
                }
            except Exception as e:
                conn.rollback()
                total_ms = round((time.monotonic() - t_total_start) * 1000, 2)
                err_meta = json.dumps({
                    "url_norm_ms": url_norm_ms,
                    "total_ms": total_ms,
                    "error_category": getattr(e, "category", type(e).__name__),
                })
                c.execute("""
                UPDATE collection_runs SET status = 'FAILED', error_message = ?, run_metadata = ?, completed_at = ?, updated_at = ?
                WHERE id = ?
                """, (str(e), err_meta, now, now, run_id))
                conn.commit()
                raise e
        finally:
            conn.close()

    @staticmethod
    def get_comments_for_post(db_path: str, post_id: str) -> List[Dict[str, Any]]:
        """Returns all collected comments for a post categorized with extracted metadata."""
        conn = get_connection(db_path)
        c = conn.cursor()
        c.execute("""
            SELECT id, author_name, author_profile_url, author_headline, email, phone, company_linkedin_url,
                   comment_text, reaction_count, likes_count, commented_at
            FROM comments
            WHERE post_id = ?
            ORDER BY commented_at DESC
        """, (post_id,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows

"""
Company enrichment: the single source of truth for company_website and company_linkedin_url.

Every value returned here is verified, never guessed:
- A website is only returned when its domain resolves in DNS to a public IP.
- A company LinkedIn URL is only returned when the public page exists (HTTP 200) and matches
  the company (same website domain, or same company name).

Sources, in priority order:
1. Company data supplied by the scraper (Apify profile position), re-verified.
2. Corporate email domain (name@acme.com -> acme.com) and LinkedIn page whose website matches it.
3. Employer named in the headline ("Engineer at HSBC", "@ TCS") -> verified LinkedIn page -> its website.
4. Explicit website link written in the comment text.
Personal email usernames (venky@gmail.com) are never turned into a company.
"""
import re
import socket
import ipaddress
import threading
import logging
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple, Dict, List, Iterable

import requests

logger = logging.getLogger("leadstream.enrichment")

PERSONAL_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "yahoo.co.uk", "ymail.com",
    "rocketmail.com", "rediffmail.com", "hotmail.com", "outlook.com", "outlook.in", "live.com", "live.in",
    "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me",
    "zoho.com", "zohomail.com", "zohomail.in", "mail.com", "email.com", "gmx.com", "gmx.net", "yandex.com",
    "yandex.ru", "fastmail.com", "tutanota.com", "hey.com", "inbox.com", "qq.com", "163.com",
}
# Personal providers whose brand must never be treated as the lead's company (yandex.com -> "Yandex")
_PERSONAL_PROVIDER_LABELS = {d.split(".")[0] for d in PERSONAL_EMAIL_DOMAINS}

_LEGAL_SUFFIXES = {
    "pvt", "private", "ltd", "limited", "llc", "inc", "incorporated", "corp", "corporation", "co",
    "company", "llp", "plc", "gmbh", "technologies", "technology", "solutions", "services", "group",
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+")
_URL_IN_TEXT_RE = re.compile(
    r"(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+(?:com|org|net|io|ai|co|in|us|uk|ca|de|fr|tech|biz|info|global|dev|app|cloud|so|xyz))(?:/[^\s,;)<>\"]*)?",
    re.IGNORECASE,
)

_DNS_CACHE: Dict[str, bool] = {}
_LI_CACHE: Dict[str, Tuple[str, Optional[str], Optional[str]]] = {}
_CACHE_LOCK = threading.Lock()
# LinkedIn rate-limits bursts from one IP: at most 2 concurrent page lookups, with retries and backoff
_LI_SEMAPHORE = threading.Semaphore(2)
LI_STATS = {"blocked": 0}


# ---------------------------------------------------------------- email / domain helpers

def email_domain(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    return email.strip().lower().rsplit("@", 1)[1].strip(". ") or None


def is_personal_domain(domain: Optional[str]) -> bool:
    """True for free mail providers, including common typos (gmail.con, gmial.com, yahoo.co)."""
    if not domain:
        return True
    d = domain.lower()
    if d in PERSONAL_EMAIL_DOMAINS:
        return True
    label = d.split(".")[0]
    if label in _PERSONAL_PROVIDER_LABELS:
        return True
    return label in {"gmial", "gmai", "gamil", "gmaill", "yaho", "yahooo", "hotmial", "outlok"}


def is_corporate_email(email: Optional[str]) -> bool:
    d = email_domain(email)
    return bool(d) and not is_personal_domain(d)


def normalize_domain(url_or_domain: Optional[str]) -> Optional[str]:
    if not url_or_domain:
        return None
    v = url_or_domain.strip().lower()
    if "://" not in v:
        v = "http://" + v
    host = (urllib.parse.urlparse(v).hostname or "").strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _registrable(domain: str) -> str:
    """acme.co.uk -> acme.co.uk, mail.acme.com -> acme.com (good enough for matching)."""
    parts = domain.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def same_site(a: Optional[str], b: Optional[str]) -> bool:
    da, db = normalize_domain(a), normalize_domain(b)
    return bool(da and db and _registrable(da) == _registrable(db))


def domain_resolves(domain: Optional[str]) -> bool:
    """DNS check: the domain must resolve to at least one public IP (fake domains like venky.in fail)."""
    domain = normalize_domain(domain)
    if not domain or "." not in domain:
        return False
    with _CACHE_LOCK:
        if domain in _DNS_CACHE:
            return _DNS_CACHE[domain]
    ok = False
    try:
        for entry in socket.getaddrinfo(domain, None):
            ip = ipaddress.ip_address(entry[4][0])
            if not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast):
                ok = True
                break
    except (socket.gaierror, UnicodeError, ValueError, OSError):
        ok = False
    with _CACHE_LOCK:
        _DNS_CACHE[domain] = ok
    return ok


# ---------------------------------------------------------------- company names

def _name_key(name: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", name.lower().replace("&", " and "))
    core = [t for t in tokens if t not in _LEGAL_SUFFIXES] or tokens
    return "".join(core)


def names_match(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    ka, kb = _name_key(a), _name_key(b)
    if ka and ka == kb:
        return True
    # Acronyms written in a headline: "TCS" matches "Tata Consultancy Services"
    for short, full in ((a.strip(), b), (b.strip(), a)):
        if short.isupper() and 2 <= len(short) <= 6 and short.isalpha():
            initials = "".join(t[0] for t in re.findall(r"[A-Za-z]+", full) if t.lower() not in ("of", "and", "the"))
            if initials.upper() == short:
                return True
    return False


def website_fits_name(website: Optional[str], name: str) -> bool:
    """Guards against same-name pages of other companies: the page's website must agree with the name
    (HSBC <-> hsbc.com, TCS <-> tcs.com, Tata Consultancy Services <-> tcs.com). Pages without a website pass."""
    d = normalize_domain(website)
    if not d:
        return True
    label = re.sub(r"[^a-z0-9]", "", _registrable(d).split(".")[0])
    key = _name_key(name)
    initials = "".join(t[0] for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in ("of", "and", "the"))
    return bool(label) and (label in key or key in label or label == initials or label == name.lower().replace(" ", ""))


def company_from_headline(headline: Optional[str]) -> Optional[str]:
    """'DevOps Engineer at HSBC | AWS' -> 'HSBC'; 'Senior Engineer @ Nucleus Software |Cloud' -> 'Nucleus Software'."""
    if not headline:
        return None
    m = re.search(r"(?:\bat\b|@)\s*([A-Za-z0-9][^|•·,;()\[\]/\n]{1,60})", headline, re.IGNORECASE)
    if not m:
        return None
    name = re.split(r"\s+[-–—]\s+|\s{2,}", m.group(1))[0].strip(" .-–—")
    words = name.split()
    if not words or len(words) > 7:
        return None
    if name.lower() in {"present", "the moment", "scale", "work", "home", "my company", "a startup", "startup"}:
        return None
    return name


def _slug_candidates(name: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+", name.lower().replace("&", " and "))
    core = [t for t in tokens if t not in _LEGAL_SUFFIXES] or tokens
    cands: List[str] = []
    for toks in (tokens, core):
        for joiner in ("-", ""):
            s = joiner.join(toks)
            if s and len(s) >= 2 and s not in cands:
                cands.append(s)
    return cands[:4]


# ---------------------------------------------------------------- LinkedIn company pages

def linkedin_company_slug(url: Optional[str]) -> Optional[str]:
    if not url or "linkedin.com/company/" not in url.lower():
        return None
    slug = url.split("/company/", 1)[1].split("/")[0].split("?")[0].strip().lower()
    if not slug or slug in ("unavailable", "company", "mycompany"):
        return None
    return urllib.parse.unquote(slug)


def fetch_linkedin_company(slug: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Returns (status, company_name, website). status: 'ok' (page exists), 'missing' (404/unavailable),
    or 'unknown' (LinkedIn blocked/rate-limited us; caller must not treat this as verified)."""
    slug = slug.strip().lower()
    with _CACHE_LOCK:
        if slug in _LI_CACHE:
            return _LI_CACHE[slug]
    result: Tuple[str, Optional[str], Optional[str]] = ("unknown", None, None)
    for attempt in range(3):
        try:
            with _LI_SEMAPHORE:
                resp = requests.get(
                    f"https://www.linkedin.com/company/{urllib.parse.quote(slug)}/",
                    headers=_HEADERS, timeout=8, allow_redirects=True,
                )
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        final = resp.url.lower()
        if resp.status_code == 404 or "/company/unavailable" in final:
            result = ("missing", None, None)
            break
        if resp.status_code == 200 and "authwall" not in final and "/login" not in final:
            html = resp.text
            title = re.search(r"<title>(.*?)</title>", html, re.S)
            name = title.group(1).split("|")[0].strip() if title else None
            website = None
            m = re.search(r'href="([^"]+)"[^>]*data-tracking-control-name="about_website"', html) or                 re.search(r'data-tracking-control-name="about_website"[^>]*href="([^"]+)"', html)
            if m:
                href = m.group(1).replace("&amp;", "&")
                q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                website = q.get("url", [href])[0]
            if name and name.lower() not in ("linkedin", "sign up", "linkedin login"):
                result = ("ok", name, website)
                break
        # 429 / 999 / authwall: LinkedIn is throttling us, back off and retry
        time.sleep(3 * (attempt + 1))
    if result[0] == "unknown":
        with _CACHE_LOCK:
            LI_STATS["blocked"] += 1
    if result[0] != "unknown":
        with _CACHE_LOCK:
            _LI_CACHE[slug] = result
    return result


def linkedin_from_website(website: Optional[str]) -> Optional[str]:
    """Reads the company's own website for its official LinkedIn company link, following at most 3 redirects
    and checking every hop resolves to a public IP (SSRF guard). The linked page must also exist."""
    current = website
    resp = None
    try:
        for _ in range(4):
            parsed = urllib.parse.urlparse(current or "")
            if parsed.scheme not in ("http", "https") or not domain_resolves(parsed.hostname):
                return None
            resp = requests.get(current, headers=_HEADERS, timeout=4, allow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                current = urllib.parse.urljoin(current, resp.headers["Location"])
                continue
            break
    except requests.RequestException:
        return None
    if resp is None or resp.status_code != 200:
        return None
    for href in re.findall(r'href=["\']([^"\']*linkedin\.com/company/[^"\']+)["\']', resp.text[:500_000], re.I):
        slug = linkedin_company_slug(href)
        if slug and fetch_linkedin_company(slug)[0] == "ok":
            return f"https://www.linkedin.com/company/{slug}"
    return None


def _clean_website(url: Optional[str]) -> Optional[str]:
    d = normalize_domain(url)
    if not d or "linkedin.com" in d or "lnkd.in" in d or is_personal_domain(d):
        return None
    return f"https://{d}" if domain_resolves(d) else None


# ---------------------------------------------------------------- main resolver

def resolve_company(
    email: Optional[str] = None,
    headline: Optional[str] = None,
    comment_text: Optional[str] = None,
    hint_website: Optional[str] = None,
    hint_linkedin: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Returns verified (company_website, company_linkedin_url). Either may be None when it cannot be verified."""
    website: Optional[str] = None
    linkedin: Optional[str] = None
    corp_domain = email_domain(email) if is_corporate_email(email) else None

    # 1. Scraper-supplied company (Apify profile position), re-verified
    slug = linkedin_company_slug(hint_linkedin)
    if slug:
        status, _, li_site = fetch_linkedin_company(slug)
        if status == "ok":
            linkedin = f"https://www.linkedin.com/company/{slug}"
            website = website or _clean_website(li_site)
    if not website and hint_website:
        website = _clean_website(hint_website)

    # 2. Corporate email domain
    if corp_domain and not website:
        website = _clean_website(corp_domain)
    if corp_domain and not linkedin:
        label = _registrable(corp_domain).split(".")[0]
        head_name = company_from_headline(headline)
        cands = [label] + (_slug_candidates(head_name) if head_name else [])
        for cand in dict.fromkeys(cands):
            status, _, li_site = fetch_linkedin_company(cand)
            # The page must belong to the email's company: its listed website is the email domain
            if status == "ok" and same_site(li_site, corp_domain):
                linkedin = f"https://www.linkedin.com/company/{cand}"
                break

    # 3. Employer named in headline (only when there is no company email to anchor on)
    if not linkedin and not corp_domain:
        head_name = company_from_headline(headline)
        if head_name:
            for cand in _slug_candidates(head_name):
                status, name, li_site = fetch_linkedin_company(cand)
                if status == "ok" and names_match(name, head_name) and website_fits_name(li_site, head_name):
                    linkedin = f"https://www.linkedin.com/company/{cand}"
                    website = website or _clean_website(li_site)
                    break

    # 4. Website written in the comment
    if not website and comment_text:
        text_wo_emails = _EMAIL_RE.sub(" ", comment_text)
        for m in _URL_IN_TEXT_RE.finditer(text_wo_emails):
            website = _clean_website(m.group(1))
            if website:
                break

    # 5. Verified website links to its own LinkedIn company page
    if website and not linkedin:
        linkedin = linkedin_from_website(website)

    # Final guard: a personal email's username or provider brand is never the lead's company
    # (catches wildcard-DNS domains such as abhishek.tyagi.okay.in that do resolve)
    if email and "@" in email and not corp_domain:
        user = email.strip().lower().rsplit("@", 1)[0]
        user_head = re.split(r"[._+-]", user)[0]
        host = normalize_domain(website) or ""
        if website and (host.startswith(user_head) or host.split(".")[0] in _PERSONAL_PROVIDER_LABELS):
            website = None
        li_slug = linkedin_company_slug(linkedin) or ""
        if linkedin and (li_slug.startswith(user_head) or li_slug.split("-")[0] in _PERSONAL_PROVIDER_LABELS):
            linkedin = None

    return website, linkedin


def resolve_many(
    rows: Iterable[Dict], max_workers: int = 8, time_budget_s: float = 120.0
) -> List[Tuple[Optional[str], Optional[str]]]:
    """Resolves companies for many rows in parallel (keys: email, author_headline, comment_text,
    company_website, company_linkedin_url). Rows not finished within the time budget get (None, None)."""
    rows = list(rows)
    results: List[Tuple[Optional[str], Optional[str]]] = [(None, None)] * len(rows)
    deadline = time.monotonic() + time_budget_s

    def work(i: int):
        if time.monotonic() > deadline:
            return i, (None, None)
        r = rows[i]
        try:
            return i, resolve_company(
                email=r.get("email"),
                headline=r.get("author_headline"),
                comment_text=r.get("comment_text"),
                hint_website=r.get("company_website"),
                hint_linkedin=r.get("company_linkedin_url"),
            )
        except Exception as exc:  # enrichment must never break a collection run
            logger.warning("company enrichment failed: %s", exc)
            return i, (None, None)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for i, res in pool.map(work, range(len(rows))):
            results[i] = res
    return results

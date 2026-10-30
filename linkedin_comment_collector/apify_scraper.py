"""
Apify Scraper Service
Integrates Apify actors (such as harvestapi/linkedin-post-comments or atomus/linkedin-comments-scraper-pro)
to reliably scrape 500+ LinkedIn post comments using managed Apify proxies and actor pools.
"""
import os
import re
import time
import hashlib
import logging
import datetime
from datetime import timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple
from apify_client import ApifyClient

logger = logging.getLogger("leadstream.apify")


class ApifyScraperError(RuntimeError):
    """Raised when the Apify Actor execution fails, times out, or is aborted."""
    def __init__(self, message: str, category: str = "APIFY_ERROR", run_id: Optional[str] = None):
        super().__init__(message)
        self.category = category
        self.run_id = run_id


class ApifyScraperService:
    DEFAULT_ACTOR_ID = "harvestapi/linkedin-post-comments"
    # harvestapi/linkedin-post-comments bills $0.002 per comment on the FREE/BRONZE/SILVER plans
    # ($0.0015 GOLD, $0.001 PLATINUM). Set APIFY_PRICE_PER_COMMENT_USD to your plan's price.
    DEFAULT_PRICE_PER_COMMENT_USD = "0.002"

    @classmethod
    def _parse_single_item(
        cls,
        item: Dict[str, Any],
        idx: int,
        now_str: str,
        parent_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """Normalizes a single Apify comment or nested reply item into our unified database schema.
        Guarantees that missing optional fields (email, phone, headline, avatar, company) never discard a comment.
        """
        actor = item.get("actor") if isinstance(item.get("actor"), dict) else {}
        author_data = (
            item.get("author") if isinstance(item.get("author"), dict)
            else (item.get("commenter") if isinstance(item.get("commenter"), dict) else {})
        )

        author_name = (
            actor.get("name")
            or author_data.get("name")
            or author_data.get("fullName")
            or item.get("commenter_name")
            or item.get("commenterName")
            or item.get("authorName")
            or item.get("name")
            or "LinkedIn Member"
        )

        author_profile_url = (
            actor.get("linkedinUrl")
            or author_data.get("url")
            or author_data.get("linkedinUrl")
            or author_data.get("profileUrl")
            or item.get("commenter_linkedin_url")
            or item.get("commenterUrl")
            or item.get("authorProfileUrl")
            or item.get("authorUrl")
            or item.get("profileUrl")
            or ""
        )

        author_headline = (
            actor.get("position")
            or actor.get("headline")
            or actor.get("occupation")
            or item.get("commenter_headline")
            or author_data.get("headline")
            or author_data.get("occupation")
            or author_data.get("jobTitle")
            or item.get("commenterHeadline")
            or item.get("authorHeadline")
            or item.get("headline")
            or item.get("occupation")
            or item.get("jobTitle")
            or ""
        )

        author_avatar = (
            actor.get("pictureUrl")
            or author_data.get("image")
            or author_data.get("profilePictureUrl")
            or item.get("commenter_profile_picture")
            or item.get("commenterProfilePicture")
            or item.get("profilePictureUrl")
            or ""
        )

        raw_text = (
            item.get("commentary")
            or item.get("comment_text")
            or item.get("commentText")
            or item.get("text")
            or item.get("content")
            or ""
        )
        comment_text = str(raw_text).strip()

        raw_source_id = item.get("id") or item.get("commentId") or item.get("urn")
        if raw_source_id:
            source_id = str(raw_source_id)
        else:
            stable_timestamp = str(item.get("createdAt") or item.get("formattedTime") or item.get("date") or "")
            stable_payload = f"{author_profile_url}|{author_name}|{comment_text}|{stable_timestamp}|{parent_override or ''}"
            digest = hashlib.sha256(stable_payload.encode("utf-8", errors="ignore")).hexdigest()[:20]
            source_id = f"apify_det_{digest}"

        parent_id = (
            parent_override
            or item.get("parentCommentId")
            or item.get("parentId")
            or None
        )

        engagement = item.get("engagement") if isinstance(item.get("engagement"), dict) else {}
        try:
            likes_count = int(
                item.get("likesCount")
                or item.get("likes")
                or engagement.get("likes")
                or 0
            )
        except (ValueError, TypeError):
            likes_count = 0

        try:
            reaction_count = int(
                item.get("reactionCount")
                or item.get("reactionsCount")
                or engagement.get("reactionsCount")
                or likes_count
            )
        except (ValueError, TypeError):
            reaction_count = likes_count

        commented_at = (
            item.get("createdAt")
            or item.get("formattedTime")
            or item.get("date")
            or now_str
        )

        # Contact parsing regex (including obfuscated [at] / (dot) emails)
        email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
        emails = re.findall(email_pattern, comment_text)
        email = emails[0].rstrip('.,;') if emails else None
        if not email:
            obf_pattern = r'([a-zA-Z0-9_.+-]+)\s*(?:@|\[at\]|\(at\)|\sat\s)\s*([a-zA-Z0-9-]+)\s*(?:\.|\(dot\)|\[dot\]|\sdot\s)\s*([a-zA-Z]{2,10})'
            obf_match = re.search(obf_pattern, comment_text, re.IGNORECASE)
            if obf_match:
                email = f"{obf_match.group(1)}@{obf_match.group(2)}.{obf_match.group(3)}".lower()
        if not email and isinstance(actor.get("emails"), list) and len(actor["emails"]) > 0:
            email = str(actor["emails"][0]).strip()

        phone_pattern = r'(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
        phones = re.findall(phone_pattern, comment_text)
        phone = phones[0].strip() if phones else None

        # Enriched company details directly from actor
        company_li = None
        company_web = None
        curr_pos = actor.get("currentPosition") or actor.get("experience") or []
        if isinstance(curr_pos, list) and len(curr_pos) > 0 and isinstance(curr_pos[0], dict):
            company_li = curr_pos[0].get("companyLinkedinUrl")
            company_web = curr_pos[0].get("companyWebsite")

        return {
            "source_comment_id": source_id,
            "parent_source_id": str(parent_id) if parent_id else None,
            "comment_text": comment_text,
            "author_name": str(author_name),
            "author_profile_url": str(author_profile_url),
            "author_headline": str(author_headline),
            "author_avatar_url": str(author_avatar),
            "email": email,
            "phone": phone,
            "company_website": company_web,
            "company_linkedin_url": company_li,
            "reaction_count": reaction_count,
            "likes_count": likes_count,
            "commented_at": str(commented_at),
            "depth": 1 if parent_id else 0
        }

    @classmethod
    def scrape_comments_apify_with_metrics(
        cls,
        post_url: str,
        api_token: Optional[str] = None,
        actor_id: Optional[str] = None,
        max_comments: Optional[int] = None,
        job_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Calls Apify actor to extract comments and nested replies, returning both normalized comments
        and stage-level execution metrics (actor_run_ms, dataset_fetch_ms, normalization_ms, run_id, status).
        """
        token = (api_token or os.environ.get("APIFY_API_TOKEN") or "").strip()
        if not token:
            raise ApifyScraperError(
                "APIFY_API_TOKEN is missing. Please provide a valid Apify API token.",
                category="AUTH_MISSING"
            )

        target_actor = (actor_id or os.environ.get("APIFY_ACTOR_ID") or cls.DEFAULT_ACTOR_ID).strip()
        client = ApifyClient(token)

        effective_limit = max_comments if (max_comments and max_comments > 0) else 500
        run_input: Dict[str, Any] = {
            "posts": [post_url],
            "scrapeReplies": True,
            "maxItems": effective_limit,
        }

        # Hard spending cap enforced by Apify itself: the run stops once it has charged this much
        price = Decimal(os.environ.get("APIFY_PRICE_PER_COMMENT_USD") or cls.DEFAULT_PRICE_PER_COMMENT_USD)
        max_charge_usd = max(Decimal("0.01"), price * effective_limit)

        t_actor_start = time.monotonic()
        try:
            run = client.actor(target_actor).call(
                run_input=run_input,
                max_items=effective_limit,
                max_total_charge_usd=max_charge_usd,
                run_timeout=timedelta(minutes=5)
            )
        except Exception as exc:
            err_msg = str(exc)
            category = "ACTOR_CALL_ERROR"
            if "401" in err_msg or "unauthorized" in err_msg.lower() or "token" in err_msg.lower():
                category = "AUTH_INVALID"
            elif "402" in err_msg or "403" in err_msg or "quota" in err_msg.lower() or "credit" in err_msg.lower() or "limit exceeded" in err_msg.lower():
                category = "QUOTA_EXCEEDED"
            elif "timeout" in err_msg.lower():
                category = "ACTOR_TIMEOUT"
            raise ApifyScraperError(f"Apify actor execution error ({category}): {err_msg}", category=category) from exc
        actor_run_ms = round((time.monotonic() - t_actor_start) * 1000, 2)

        if not run:
            raise ApifyScraperError("Apify actor returned an empty run response.", category="EMPTY_RUN_RESPONSE")

        run_id = (
            getattr(run, "id", None)
            or (run.get("id") if isinstance(run, dict) else None)
        )
        run_status = str(
            getattr(run, "status", None)
            or (run.get("status") if isinstance(run, dict) else "UNKNOWN")
        ).upper()
        dataset_id = (
            getattr(run, "default_dataset_id", None)
            or (run.get("defaultDatasetId") if isinstance(run, dict) else None)
            or (run.get("default_dataset_id") if isinstance(run, dict) else None)
        )

        if run_status in ("FAILED", "TIMED-OUT", "TIMED_OUT", "ABORTED"):
            status_msg = (
                getattr(run, "status_message", None)
                or (run.get("statusMessage") if isinstance(run, dict) else None)
                or f"Actor run finished with terminal status {run_status}"
            )
            cat = "ACTOR_TIMEOUT" if "TIME" in run_status else f"ACTOR_{run_status}"
            raise ApifyScraperError(
                f"Apify actor run {run_id} {run_status}: {status_msg}",
                category=cat,
                run_id=run_id
            )

        if not dataset_id:
            raise ApifyScraperError(
                f"Apify actor run {run_id} did not return a dataset ID.",
                category="MISSING_DATASET_ID",
                run_id=run_id
            )

        # Retrieve all dataset items with pagination support
        t_ds_start = time.monotonic()
        dataset_items: List[Dict[str, Any]] = []
        offset = 0
        page_limit = 1000
        while True:
            page_resp = client.dataset(dataset_id).list_items(offset=offset, limit=page_limit)
            items_batch = getattr(page_resp, "items", None)
            if items_batch is None and isinstance(page_resp, dict):
                items_batch = page_resp.get("items", [])
            items_batch = items_batch or []
            dataset_items.extend(items_batch)
            if len(items_batch) < page_limit:
                break
            offset += len(items_batch)
            if offset >= 10000:
                break
        dataset_fetch_ms = round((time.monotonic() - t_ds_start) * 1000, 2)

        t_norm_start = time.monotonic()
        normalized_comments: List[Dict[str, Any]] = []
        seen_ids = set()
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        top_level_count = 0
        replies_count = 0

        for idx, item in enumerate(dataset_items):
            if not isinstance(item, dict):
                continue
            parsed_top = cls._parse_single_item(item, idx, now_str)
            top_id = parsed_top["source_comment_id"]
            if top_id not in seen_ids:
                seen_ids.add(top_id)
                normalized_comments.append(parsed_top)
                if parsed_top["depth"] == 0:
                    top_level_count += 1
                else:
                    replies_count += 1

            # Unpack nested replies if embedded inside item['replies']
            replies_list = item.get("replies")
            if isinstance(replies_list, list):
                for r_idx, reply_item in enumerate(replies_list):
                    if isinstance(reply_item, dict):
                        parsed_reply = cls._parse_single_item(
                            reply_item,
                            idx * 1000 + r_idx + 1,
                            now_str,
                            parent_override=top_id
                        )
                        r_id = parsed_reply["source_comment_id"]
                        if r_id not in seen_ids:
                            seen_ids.add(r_id)
                            normalized_comments.append(parsed_reply)
                            replies_count += 1

        normalization_ms = round((time.monotonic() - t_norm_start) * 1000, 2)

        metrics = {
            "actor_id": target_actor,
            "run_id": run_id,
            "run_status": run_status,
            "dataset_id": dataset_id,
            "requested_limit": effective_limit,
            "max_charge_usd": float(max_charge_usd),
            "raw_dataset_items": len(dataset_items),
            "top_level_count": top_level_count,
            "replies_count": replies_count,
            "total_normalized": len(normalized_comments),
            "actor_run_ms": actor_run_ms,
            "dataset_fetch_ms": dataset_fetch_ms,
            "normalization_ms": normalization_ms,
        }
        logger.info(
            "apify_scrape_complete job_id=%s run_id=%s status=%s items=%d top=%d replies=%d actor_ms=%.1f ds_ms=%.1f norm_ms=%.1f",
            job_id or "sync",
            run_id,
            run_status,
            len(normalized_comments),
            top_level_count,
            replies_count,
            actor_run_ms,
            dataset_fetch_ms,
            normalization_ms
        )
        return normalized_comments, metrics

    @classmethod
    def scrape_comments_apify(
        cls,
        post_url: str,
        api_token: Optional[str] = None,
        actor_id: Optional[str] = None,
        max_comments: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Backwards-compatible entry point returning normalized comments list."""
        comments, _ = cls.scrape_comments_apify_with_metrics(
            post_url=post_url,
            api_token=api_token,
            actor_id=actor_id,
            max_comments=max_comments
        )
        return comments

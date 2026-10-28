"""
Apify Scraper Service
Integrates Apify actors (such as harvestapi/linkedin-post-comments or atomus/linkedin-comments-scraper-pro)
to reliably scrape 500+ LinkedIn post comments using managed Apify proxies and actor pools.
"""
import os
import re
import datetime
from typing import List, Dict, Any, Optional
from apify_client import ApifyClient

class ApifyScraperService:
    DEFAULT_ACTOR_ID = "harvestapi/linkedin-post-comments"

    @classmethod
    def scrape_comments_apify(
        cls,
        post_url: str,
        api_token: Optional[str] = None,
        actor_id: Optional[str] = None,
        max_comments: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Calls Apify actor to extract all comments for a post URL.
        Returns a list of standardized comment dictionaries compatible with SQLite persistence.
        """
        token = api_token or os.environ.get("APIFY_API_TOKEN")
        if not token:
            raise ValueError("APIFY_API_TOKEN is missing. Please provide a valid Apify API token.")

        target_actor = actor_id or os.environ.get("APIFY_ACTOR_ID") or cls.DEFAULT_ACTOR_ID
        client = ApifyClient(token)

        # High-Speed Actor input payload: captures comments & nested replies efficiently
        # Guard against unbounded scraping (default to max_comments or actor default)
        run_input: Dict[str, Any] = {
            "posts": [post_url],
            "scrapeReplies": True,  # Captures nested replies as separate items
        }
        if max_comments and max_comments > 0:
            run_input["maxItems"] = max_comments
        else:
            # When unspecified or 0, cap at a safe ceiling (500 comments) to prevent runaway credit exhaustion
            # on viral posts with tens of thousands of comments
            run_input["maxItems"] = 500

        # Trigger actor execution synchronously with strict timeout safeguard (5 minutes max)
        # Prevents hanging runs on Apify from continuously consuming compute units
        from datetime import timedelta
        run = client.actor(target_actor).call(
            run_input=run_input,
            run_timeout=timedelta(minutes=5)
        )
        
        # Support both Pydantic model (v3.x apify-client) and legacy dict
        dataset_id = (
            getattr(run, "default_dataset_id", None)
            or (run.get("defaultDatasetId") if isinstance(run, dict) else None)
            or (run.get("default_dataset_id") if isinstance(run, dict) else None)
        )

        if not run or not dataset_id:
            raise RuntimeError(f"Apify actor run did not return a dataset ID: {run}")

        dataset_items = client.dataset(dataset_id).list_items().items
        normalized_comments: List[Dict[str, Any]] = []

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        for idx, item in enumerate(dataset_items):
            # Author & Actor enrichment (harvestapi returns actor dict with full profile)
            actor = item.get("actor") or {}
            author_data = item.get("author") or item.get("commenter") or {}
            
            author_name = (
                actor.get("name")
                or (author_data.get("name") if isinstance(author_data, dict) else None)
                or (author_data.get("fullName") if isinstance(author_data, dict) else None)
                or item.get("commenter_name")
                or item.get("commenterName")
                or item.get("authorName")
                or item.get("name")
                or "LinkedIn Member"
            )

            author_profile_url = (
                actor.get("linkedinUrl")
                or (author_data.get("url") if isinstance(author_data, dict) else None)
                or (author_data.get("linkedinUrl") if isinstance(author_data, dict) else None)
                or (author_data.get("profileUrl") if isinstance(author_data, dict) else None)
                or item.get("commenter_linkedin_url")
                or item.get("commenterUrl")
                or item.get("authorProfileUrl")
                or item.get("authorUrl")
                or item.get("profileUrl")
                or ""
            )

            # Extract headline / occupation / position
            author_headline = (
                actor.get("position")
                or actor.get("headline")
                or actor.get("occupation")
                or item.get("commenter_headline")
                or (author_data.get("headline") if isinstance(author_data, dict) else None)
                or (author_data.get("occupation") if isinstance(author_data, dict) else None)
                or (author_data.get("jobTitle") if isinstance(author_data, dict) else None)
                or item.get("commenterHeadline")
                or item.get("authorHeadline")
                or item.get("headline")
                or item.get("occupation")
                or item.get("jobTitle")
                or ""
            )

            author_avatar = (
                actor.get("pictureUrl")
                or (author_data.get("image") if isinstance(author_data, dict) else None)
                or (author_data.get("profilePictureUrl") if isinstance(author_data, dict) else None)
                or item.get("commenter_profile_picture")
                or item.get("commenterProfilePicture")
                or item.get("profilePictureUrl")
                or ""
            )

            comment_text = (
                item.get("commentary")
                or item.get("comment_text")
                or item.get("commentText")
                or item.get("text")
                or item.get("content")
                or ""
            ).strip()

            source_id = str(
                item.get("id")
                or item.get("commentId")
                or item.get("urn")
                or f"apify_{idx}_{hash(comment_text)}"
            )

            parent_id = (
                item.get("parentCommentId")
                or item.get("parentId")
                or None
            )

            likes_count = int(item.get("likesCount") or item.get("likes") or item.get("engagement", {}).get("likes", 0) if isinstance(item.get("engagement"), dict) else 0)
            reaction_count = int(item.get("reactionCount") or item.get("reactionsCount") or likes_count)

            commented_at = (
                item.get("createdAt")
                or item.get("formattedTime")
                or item.get("date")
                or now_str
            )

            # Contact parsing regex
            email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
            emails = re.findall(email_pattern, comment_text)
            email = emails[0].rstrip('.,;') if emails else None
            if not email and actor.get("emails") and isinstance(actor.get("emails"), list) and len(actor.get("emails")) > 0:
                email = actor["emails"][0]

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

            normalized_comments.append({
                "source_comment_id": source_id,
                "parent_source_id": parent_id,
                "comment_text": comment_text,
                "author_name": author_name,
                "author_profile_url": author_profile_url,
                "author_headline": author_headline,
                "author_avatar_url": author_avatar,
                "email": email,
                "phone": phone,
                "company_website": company_web,
                "company_linkedin_url": company_li,
                "reaction_count": reaction_count,
                "likes_count": likes_count,
                "commented_at": str(commented_at),
                "depth": 1 if parent_id else 0
            })

        return normalized_comments

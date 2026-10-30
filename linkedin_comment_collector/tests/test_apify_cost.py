import os
import unittest
from unittest.mock import patch

from collector_service import CollectorService
from db_manager import init_db

TEST_DB = os.path.join(os.path.dirname(__file__), "test_apify_cost.db")
URL = "https://www.linkedin.com/feed/update/urn:li:activity:7111222333444555666"

COMMENT = {
    "source_comment_id": "c1", "parent_source_id": None, "comment_text": "Interested",
    "author_name": "A", "author_profile_url": "https://www.linkedin.com/in/a", "author_headline": "Dev",
    "author_avatar_url": "", "email": None, "phone": None, "company_website": None,
    "company_linkedin_url": None, "reaction_count": 0, "likes_count": 0,
    "commented_at": "2026-10-28 12:00:00", "depth": 0,
}


class TestApifyCostControls(unittest.TestCase):
    def setUp(self):
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(TEST_DB + ext):
                os.remove(TEST_DB + ext)
        init_db(TEST_DB)

    def tearDown(self):
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(TEST_DB + ext):
                os.remove(TEST_DB + ext)

    def _run(self, **kw):
        return CollectorService.execute_collection_and_persist(
            db_path=TEST_DB, post_url=URL, provider="apify", apify_token="t", max_comments=50, **kw
        )

    @patch("company_enrichment.resolve_company", return_value=(None, None))
    def test_repeat_scrape_is_served_from_db_without_apify(self, _):
        with patch("apify_scraper.ApifyScraperService.scrape_comments_apify_with_metrics",
                   return_value=([COMMENT], {})) as apify:
            first = self._run()
            second = self._run()
            self.assertEqual(apify.call_count, 1, "second scrape of the same post must not be billed")
            self.assertEqual(second["total_comments"], first["total_comments"])
            self.assertIn("reused_from_run", second["timings"])

            self._run(force_refresh=True)
            self.assertEqual(apify.call_count, 2, "force_refresh must call Apify")

    @patch("company_enrichment.resolve_company", return_value=(None, None))
    def test_reuse_disabled_when_hours_zero(self, _):
        with patch.dict(os.environ, {"APIFY_REUSE_HOURS": "0"}), \
             patch("apify_scraper.ApifyScraperService.scrape_comments_apify_with_metrics",
                   return_value=([COMMENT], {})) as apify:
            self._run()
            self._run()
            self.assertEqual(apify.call_count, 2)


if __name__ == "__main__":
    unittest.main()

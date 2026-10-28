"""
Comprehensive Production Verification Suite for LinkedIn Comment Collector
Tests:
1. Database Schema & Settings Vault
2. SSRF Protection & URL Normalization
3. Contact Information Regex Extraction (Email & Phone)
4. Comment Collection & Persistence
5. CSV Categorized Export Structure
"""
import unittest
import os
import sqlite3
import pandas as pd
from db_manager import init_db, get_setting, set_setting, delete_setting
from collector_service import CollectorService, SSRFProtectionError, InvalidURLError

TEST_DB = "test_suite_comments.db"

class TestProductionCommentCollector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        init_db(TEST_DB)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)

    def test_01_settings_vault(self):
        """Verify internal session cookie vault stores, retrieves, and updates securely."""
        set_setting("li_at", "test_cookie_value_12345", db_path=TEST_DB)
        val = get_setting("li_at", db_path=TEST_DB)
        self.assertEqual(val, "test_cookie_value_12345")

        # Update
        set_setting("li_at", "updated_cookie_99999", db_path=TEST_DB)
        val_up = get_setting("li_at", db_path=TEST_DB)
        self.assertEqual(val_up, "updated_cookie_99999")

        # Delete
        delete_setting("li_at", db_path=TEST_DB)
        self.assertIsNone(get_setting("li_at", db_path=TEST_DB))

    def test_02_url_normalization_and_ssrf(self):
        """Verify SSRF protections guard against malicious/internal endpoints while allowing valid LinkedIn posts."""
        # Malicious loopback attempts
        with self.assertRaises(SSRFProtectionError):
            CollectorService.normalize_post_url("http://localhost:8000/admin")
        with self.assertRaises(SSRFProtectionError):
            CollectorService.normalize_post_url("http://127.0.0.1/sensitive")
        with self.assertRaises(SSRFProtectionError):
            CollectorService.normalize_post_url("https://malicious-site.com/exploit")

        # Valid LinkedIn URLs
        canon, urn = CollectorService.normalize_post_url("https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678?utm_source=share")
        self.assertEqual(urn, "urn:li:activity:7123456789012345678")
        self.assertEqual(canon, "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678")

    def test_03_contact_info_extraction(self):
        """Verify automated lead detection for emails and phone numbers."""
        text_with_both = "Please send the report to alex.turner@company.io or call me at +1 (555) 234-5678 for details."
        email, phone = CollectorService.extract_contact_info(text_with_both)
        self.assertEqual(email, "alex.turner@company.io")
        self.assertEqual(phone, "+1 (555) 234-5678")

        text_none = "Great post, completely agree with the insights shared here!"
        email_n, phone_n = CollectorService.extract_contact_info(text_none)
        self.assertIsNone(email_n)
        self.assertIsNone(phone_n)

    def test_04_direct_collection_and_persistence(self):
        """Verify real comments are scraped and categorized into SQLite."""
        post_url = "https://www.linkedin.com/posts/sidhantkabra_i-mapped-out-the-voice-ai-landscape-in-hr-share-7518707570979598336-SYLf"
        res = CollectorService.execute_collection_and_persist(
            db_path=TEST_DB,
            post_url=post_url,
            provider="direct",
            max_comments=10
        )
        self.assertTrue(res["success"])
        self.assertGreater(res["total_comments"], 0)

        # Inspect SQLite rows
        conn = sqlite3.connect(TEST_DB)
        c = conn.cursor()
        c.execute("SELECT author_name, author_profile_url, comment_text, email, phone FROM comments WHERE post_id = ?", (res["post_id"],))
        rows = c.fetchall()
        conn.close()

        self.assertGreater(len(rows), 0)
        for name, profile, comment, email, phone in rows:
            self.assertTrue(name and len(name) > 0, "Name must be present")
            self.assertTrue("linkedin.com/in/" in profile, f"Profile must be a valid LinkedIn in URL: {profile}")
            self.assertTrue(len(comment) > 0, "Comment text must not be empty")

    def test_05_csv_export_format(self):
        """Verify exported CSV contains all required categorized columns."""
        conn = sqlite3.connect(TEST_DB)
        df = pd.read_sql_query("SELECT author_name, author_profile_url, email, phone, comment_text, author_headline, commented_at FROM comments", conn)
        conn.close()

        export_df = df.rename(columns={
            "author_name": "Commenter Name",
            "author_profile_url": "Profile Link",
            "email": "Email ID",
            "phone": "Phone Number",
            "comment_text": "Comment Posted",
            "author_headline": "Headline",
            "commented_at": "Comment Date"
        })

        expected_columns = ["Commenter Name", "Profile Link", "Email ID", "Phone Number", "Comment Posted", "Headline", "Comment Date"]
        for col in expected_columns:
            self.assertIn(col, export_df.columns)

    def test_06_document_parsing_and_multi_link(self):
        """Verify automated LinkedIn link extraction from Excel, Word, and CSV documents."""
        import asyncio
        import io
        import openpyxl
        from fastapi import UploadFile
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from api_server import parse_links_from_file

        async def run_parse_test():
            wb = openpyxl.Workbook()
            ws = wb.active
            ws['A1'] = "Post 1: https://www.linkedin.com/posts/testuser_ai-lead-generation-activity-7123456789"
            ws['B2'] = "Post 2: linkedin.com/feed/update/urn:li:activity:79998887771122"
            cell = ws['C3']
            cell.value = "Click here"
            cell.hyperlink = "https://lnkd.in/postTestLink123"
            
            stream = io.BytesIO()
            wb.save(stream)
            stream.seek(0)
            upload_excel = UploadFile(filename="leads_data.xlsx", file=stream)
            res_excel = await parse_links_from_file(upload_excel)
            self.assertTrue(res_excel["success"])
            self.assertEqual(res_excel["found_count"], 3)
            self.assertIn("https://www.linkedin.com/posts/testuser_ai-lead-generation-activity-7123456789", res_excel["urls"])
            self.assertIn("https://linkedin.com/feed/update/urn:li:activity:79998887771122", res_excel["urls"])
            self.assertIn("https://lnkd.in/postTestLink123", res_excel["urls"])

            csv_content = (
                b"PostId,PostUrl\n"
                b"1,https://www.linkedin.com/feed/update/urn:li:activity:7111222333444\n"
                b"2,https://www.linkedin.com/posts/founder_update-activity-7555666777\n"
            )
            upload_csv = UploadFile(filename="batch_posts.csv", file=io.BytesIO(csv_content))
            res_csv = await parse_links_from_file(upload_csv)
            self.assertTrue(res_csv["success"])
            self.assertEqual(res_csv["found_count"], 2)

        asyncio.run(run_parse_test())

if __name__ == "__main__":
    unittest.main()


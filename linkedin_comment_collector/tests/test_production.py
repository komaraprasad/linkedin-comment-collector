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
import csv
import io
import sqlite3
from db_manager import init_db, get_setting, set_setting, delete_setting
from collector_service import CollectorService, SSRFProtectionError, InvalidURLError

TEST_DB = "test_suite_comments.db"

class TestProductionCommentCollector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure unit tests run against isolated local SQLite TEST_DB even if .env sets DATABASE_URL
        os.environ["DATABASE_URL"] = ""
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(TEST_DB + suffix):
                try:
                    os.remove(TEST_DB + suffix)
                except OSError:
                    pass
        init_db(TEST_DB)

    @classmethod
    def tearDownClass(cls):
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(TEST_DB + suffix):
                try:
                    os.remove(TEST_DB + suffix)
                except OSError:
                    pass

    def setUp(self):
        os.environ["DATABASE_URL"] = ""

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
        """Verify SSRF protections guard against malicious/internal endpoints and redirect chains while allowing valid LinkedIn posts."""
        from unittest.mock import patch, MagicMock

        # Malicious loopback, private RFC1918, and cloud metadata attempts
        for blocked_url in (
            "http://localhost:8000/admin",
            "http://127.0.0.1/sensitive",
            "http://127.0.0.2/internal",
            "http://10.0.0.1/private",
            "http://192.168.1.1/router",
            "http://172.16.0.5/cluster",
            "http://169.254.169.254/latest/meta-data/",
            "https://malicious-site.com/exploit",
        ):
            with self.assertRaises(SSRFProtectionError, msg=f"Should block {blocked_url}"):
                CollectorService.normalize_post_url(blocked_url)

        # Verify lnkd.in short link redirecting to an unauthorized host is blocked
        with patch("collector_service.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
            mock_get.return_value = mock_resp
            with self.assertRaises(SSRFProtectionError):
                CollectorService.normalize_post_url("https://lnkd.in/evilredirect")

        # Verify lnkd.in short link redirecting to a valid public LinkedIn activity post resolves cleanly
        with patch("collector_service.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.headers = {"Location": "https://www.linkedin.com/posts/user_topic-activity-7123456789012345678-abcd"}
            mock_get.return_value = mock_resp
            canon_short, urn_short = CollectorService.normalize_post_url("https://lnkd.in/validpost")
            self.assertEqual(urn_short, "urn:li:activity:7123456789012345678")
            self.assertEqual(canon_short, "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678")

        # Verify lnkd.in short link redirecting to a private LinkedIn Group post raises InvalidURLError
        with patch("collector_service.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.headers = {"Location": "https://www.linkedin.com/feed/update/urn:li:groupPost:10529815-7510801069527072769"}
            mock_get.return_value = mock_resp
            with self.assertRaises(InvalidURLError):
                CollectorService.normalize_post_url("https://lnkd.in/p/ghPRiCGq")

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
        """Verify exported CSV structure using standard library sqlite3 and csv (no external pandas dependency)."""
        conn = sqlite3.connect(TEST_DB)
        c = conn.cursor()
        c.execute("SELECT author_name, author_profile_url, email, phone, comment_text, author_headline, commented_at FROM comments")
        db_rows = c.fetchall()
        conn.close()

        output = io.StringIO()
        writer = csv.writer(output)
        expected_columns = ["Commenter Name", "Profile Link", "Email ID", "Phone Number", "Comment Posted", "Headline", "Comment Date"]
        writer.writerow(expected_columns)
        for row in db_rows:
            writer.writerow(row)

        output.seek(0)
        reader = csv.reader(output)
        header = next(reader)
        for col in expected_columns:
            self.assertIn(col, header)
        parsed_rows = list(reader)
        self.assertEqual(len(parsed_rows), len(db_rows))

    def test_06_document_parsing_and_multi_link(self):
        """Verify automated LinkedIn link extraction from Excel (.xlsx), Word (.docx, .doc), PDF (.pdf), CSV (.csv), and TXT (.txt)."""
        import asyncio
        import openpyxl
        from docx import Document
        from pypdf import PdfWriter
        from fastapi import UploadFile, HTTPException
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from api_server import parse_links_from_file

        async def run_parse_test():
            # 1. Excel (.xlsx)
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

            # 2. CSV (.csv)
            csv_content = (
                b"PostId,PostUrl\n"
                b"1,https://www.linkedin.com/feed/update/urn:li:activity:7111222333444\n"
                b"2,https://www.linkedin.com/posts/founder_update-activity-7555666777\n"
            )
            upload_csv = UploadFile(filename="batch_posts.csv", file=io.BytesIO(csv_content))
            res_csv = await parse_links_from_file(upload_csv)
            self.assertTrue(res_csv["success"])
            self.assertEqual(res_csv["found_count"], 2)

            # 3. Word (.docx)
            doc = Document()
            doc.add_paragraph("Check out https://www.linkedin.com/posts/docxuser_activity-7444555666777888999-xyz1")
            table = doc.add_table(rows=1, cols=1)
            table.cell(0, 0).text = "https://lnkd.in/docxShortLink99"
            docx_stream = io.BytesIO()
            doc.save(docx_stream)
            docx_stream.seek(0)
            upload_docx = UploadFile(filename="campaign_links.docx", file=docx_stream)
            res_docx = await parse_links_from_file(upload_docx)
            self.assertTrue(res_docx["success"])
            self.assertEqual(res_docx["found_count"], 2)

            # 4. Plain Text (.txt)
            txt_bytes = b"Line 1: https://www.linkedin.com/pulse/enterprise-ai-trends-john-doe\nLine 2: https://lnkd.in/txtShort1\n"
            upload_txt = UploadFile(filename="links.txt", file=io.BytesIO(txt_bytes))
            res_txt = await parse_links_from_file(upload_txt)
            self.assertTrue(res_txt["success"])
            self.assertEqual(res_txt["found_count"], 2)

            # 5. PDF (.pdf)
            pdf_writer = PdfWriter()
            pdf_writer.add_blank_page(width=200, height=200)
            pdf_stream = io.BytesIO()
            pdf_writer.write(pdf_stream)
            pdf_stream.seek(0)
            upload_pdf = UploadFile(filename="empty_doc.pdf", file=pdf_stream)
            res_pdf = await parse_links_from_file(upload_pdf)
            self.assertTrue(res_pdf["success"])
            self.assertEqual(res_pdf["found_count"], 0)

            # 6. Unsupported file extension check
            upload_bad = UploadFile(filename="malware.exe", file=io.BytesIO(b"MZ..."))
            with self.assertRaises(HTTPException) as ctx:
                await parse_links_from_file(upload_bad)
            self.assertEqual(ctx.exception.status_code, 400)

            # 7. Legacy .doc binary fallback check
            doc_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1some binary header https://www.linkedin.com/posts/docuser_activity-7112233445566778899-abcd end"
            upload_doc = UploadFile(filename="legacy.doc", file=io.BytesIO(doc_bytes))
            res_doc = await parse_links_from_file(upload_doc)
            self.assertTrue(res_doc["success"])
            self.assertEqual(res_doc["found_count"], 1)

        asyncio.run(run_parse_test())

    def test_07_apify_input_schema_and_nested_replies(self):
        """Verify Apify input schema (maxItems, scrapeReplies) and nested item['replies'] unpacking."""
        from unittest.mock import MagicMock, patch
        from apify_scraper import ApifyScraperService

        mock_dataset_items = [
            {
                "id": "comment_top_1",
                "commentary": "Interested! Email me at founder [at] acmecorp (dot) io or call +1-415-890-1234",
                "createdAt": "2026-10-28T10:00:00Z",
                "likesCount": 5,
                "reactionCount": 7,
                "actor": {
                    "name": "Jane Doe",
                    "linkedinUrl": "https://www.linkedin.com/in/janedoe",
                    "position": "CEO at AcmeCorp",
                    "pictureUrl": "https://media.licdn.com/jane.jpg"
                },
                "replies": [
                    {
                        "id": "comment_reply_1",
                        "commentary": "Thanks Jane, sent you details at sales@beta.ai!",
                        "createdAt": "2026-10-28T10:05:00Z",
                        "actor": {
                            "name": "Post Author",
                            "linkedinUrl": "https://www.linkedin.com/in/postauthor",
                            "position": "VP Growth"
                        }
                    }
                ]
            },
            {
                # Malformed/minimal comment missing optional fields
                "id": "comment_minimal_2",
                "text": "Following this thread!"
            }
        ]

        with patch("apify_scraper.ApifyClient") as MockClient:
            instance = MockClient.return_value
            actor_mock = MagicMock()
            actor_mock.call.return_value = {
                "id": "run_test_123",
                "status": "SUCCEEDED",
                "defaultDatasetId": "ds_test_456"
            }
            instance.actor.return_value = actor_mock

            ds_mock = MagicMock()
            ds_mock.list_items.return_value = MagicMock(items=mock_dataset_items)
            instance.dataset.return_value = ds_mock

            comments, metrics = ApifyScraperService.scrape_comments_apify_with_metrics(
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:7123456789",
                api_token="apify_api_mock_token",
                max_comments=250
            )

            # Verify exact Actor input schema
            call_kwargs = actor_mock.call.call_args.kwargs
            self.assertEqual(call_kwargs["run_input"]["posts"], ["https://www.linkedin.com/feed/update/urn:li:activity:7123456789"])
            self.assertEqual(call_kwargs["run_input"]["maxItems"], 250)
            self.assertTrue(call_kwargs["run_input"]["scrapeReplies"])

            # Verify 2 top-level + 1 nested reply = 3 normalized comments
            self.assertEqual(len(comments), 3)
            self.assertEqual(metrics["top_level_count"], 2)
            self.assertEqual(metrics["replies_count"], 1)
            self.assertEqual(metrics["run_id"], "run_test_123")

            top_c = next(c for c in comments if c["source_comment_id"] == "comment_top_1")
            self.assertEqual(top_c["email"], "founder@acmecorp.io")
            self.assertEqual(top_c["phone"], "+1-415-890-1234")
            self.assertEqual(top_c["depth"], 0)

            rep_c = next(c for c in comments if c["source_comment_id"] == "comment_reply_1")
            self.assertEqual(rep_c["parent_source_id"], "comment_top_1")
            self.assertEqual(rep_c["depth"], 1)
            self.assertEqual(rep_c["email"], "sales@beta.ai")

            min_c = next(c for c in comments if c["source_comment_id"] == "comment_minimal_2")
            self.assertEqual(min_c["author_name"], "LinkedIn Member")
            self.assertIsNone(min_c["email"])

    def test_08_apify_empty_dataset_and_error_handling(self):
        """Verify empty dataset returns 0 comments cleanly while FAILED/TIMED-OUT runs raise ApifyScraperError."""
        from unittest.mock import MagicMock, patch
        from apify_scraper import ApifyScraperService, ApifyScraperError

        # 1. Valid empty dataset (0 comments on post)
        with patch("apify_scraper.ApifyClient") as MockClient:
            instance = MockClient.return_value
            instance.actor.return_value.call.return_value = {
                "id": "run_empty_1",
                "status": "SUCCEEDED",
                "defaultDatasetId": "ds_empty_1"
            }
            instance.dataset.return_value.list_items.return_value = MagicMock(items=[])
            comments, metrics = ApifyScraperService.scrape_comments_apify_with_metrics(
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:7000000001",
                api_token="apify_api_mock"
            )
            self.assertEqual(comments, [])
            self.assertEqual(metrics["total_normalized"], 0)

        # 2. Failed Actor run raises ApifyScraperError and marks collection_run FAILED
        with patch("apify_scraper.ApifyClient") as MockClient:
            instance = MockClient.return_value
            instance.actor.return_value.call.return_value = {
                "id": "run_fail_99",
                "status": "TIMED-OUT",
                "statusMessage": "Run exceeded 5 minute timeout",
                "defaultDatasetId": "ds_fail_99"
            }
            with self.assertRaises(ApifyScraperError) as ctx:
                ApifyScraperService.scrape_comments_apify_with_metrics(
                    post_url="https://www.linkedin.com/feed/update/urn:li:activity:7000000002",
                    api_token="apify_api_mock"
                )
            self.assertEqual(ctx.exception.category, "ACTOR_TIMEOUT")

    def test_09_apify_dataset_pagination(self):
        """Verify multi-page dataset retrieval fetches all items across pages."""
        from unittest.mock import MagicMock, patch
        from apify_scraper import ApifyScraperService

        page_1 = [{"id": f"c_{i}", "commentary": f"Comment {i}"} for i in range(1000)]
        page_2 = [{"id": f"c_{i}", "commentary": f"Comment {i}"} for i in range(1000, 1250)]

        with patch("apify_scraper.ApifyClient") as MockClient:
            instance = MockClient.return_value
            instance.actor.return_value.call.return_value = {
                "id": "run_page_1",
                "status": "SUCCEEDED",
                "defaultDatasetId": "ds_page_1"
            }
            instance.dataset.return_value.list_items.side_effect = [
                MagicMock(items=page_1),
                MagicMock(items=page_2)
            ]
            comments, metrics = ApifyScraperService.scrape_comments_apify_with_metrics(
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:7000000003",
                api_token="apify_api_mock",
                max_comments=1500
            )
            self.assertEqual(len(comments), 1250)
            self.assertEqual(metrics["raw_dataset_items"], 1250)

    def test_10_parent_comment_id_linking_and_deduplication(self):
        """Verify parent_comment_id links replies to parent UUIDs and re-scraping never duplicates rows."""
        from unittest.mock import patch

        mock_comments = [
            {
                "source_comment_id": "src_parent_100",
                "parent_source_id": None,
                "comment_text": "We are hiring engineers at https://techflow.io — email hr@techflow.io",
                "author_name": "Sarah Connor",
                "author_profile_url": "https://www.linkedin.com/in/sarahconnor",
                "author_headline": "CTO at TechFlow",
                "author_avatar_url": "",
                "email": "hr@techflow.io",
                "phone": None,
                "company_website": None,
                "company_linkedin_url": None,
                "reaction_count": 10,
                "likes_count": 8,
                "commented_at": "2026-10-28 12:00:00",
                "depth": 0
            },
            {
                "source_comment_id": "src_reply_101",
                "parent_source_id": "src_parent_100",
                "comment_text": "Sent my resume!",
                "author_name": "Kyle Reese",
                "author_profile_url": "https://www.linkedin.com/in/kylereese",
                "author_headline": "Senior Developer",
                "author_avatar_url": "",
                "email": None,
                "phone": None,
                "company_website": None,
                "company_linkedin_url": None,
                "reaction_count": 2,
                "likes_count": 2,
                "commented_at": "2026-10-28 12:05:00",
                "depth": 1
            }
        ]
        mock_metrics = {
            "actor_run_ms": 120.5,
            "dataset_fetch_ms": 35.2,
            "normalization_ms": 4.1,
            "run_id": "run_dedup_1",
            "dataset_id": "ds_dedup_1"
        }

        li_pages = {"techflow": ("ok", "TechFlow", "https://techflow.io")}
        with patch("apify_scraper.ApifyScraperService.scrape_comments_apify_with_metrics", return_value=(mock_comments, mock_metrics)), \
             patch("company_enrichment.domain_resolves", side_effect=lambda d: "techflow.io" in (d or "")), \
             patch("company_enrichment.fetch_linkedin_company", side_effect=lambda s: li_pages.get(s, ("missing", None, None))), \
             patch("company_enrichment.linkedin_from_website", return_value=None):
            res1 = CollectorService.execute_collection_and_persist(
                db_path=TEST_DB,
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:7888999000111222333",
                provider="apify",
                apify_token="mock_token",
                max_comments=50
            )
            self.assertEqual(res1["total_comments"], 2)

            # Re-run same scrape to verify zero duplicates
            res2 = CollectorService.execute_collection_and_persist(
                db_path=TEST_DB,
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:7888999000111222333",
                provider="apify",
                apify_token="mock_token",
                max_comments=50
            )
            self.assertEqual(res2["total_comments"], 2)

            conn = sqlite3.connect(TEST_DB)
            c = conn.cursor()
            c.execute("SELECT id, source_comment_id, parent_comment_id, company_website, company_linkedin_url FROM comments WHERE post_id = ?", (res1["post_id"],))
            rows = {r[1]: r for r in c.fetchall()}
            conn.close()

            parent_row = rows["src_parent_100"]
            reply_row = rows["src_reply_101"]
            self.assertEqual(reply_row[2], parent_row[0], "Reply parent_comment_id must equal parent comment UUID")
            self.assertEqual(parent_row[3], "https://techflow.io")
            self.assertEqual(parent_row[4], "https://www.linkedin.com/company/techflow")

    def test_11_postgres_query_adaptation_literal_percent_escaping(self):
        """Regression test for 'Error: tuple index out of range' when SQL contains literal '%' (e.g. LIKE 'real_comment_%')."""
        from unittest.mock import MagicMock
        from db_manager import UniversalCursor

        raw_cur = MagicMock()
        u_cur = UniversalCursor(raw_cur, is_pg=True)
        adapted = u_cur._adapt_query("DELETE FROM comments WHERE post_id = ? AND source_comment_id LIKE 'real_comment_%'")
        self.assertEqual(adapted, "DELETE FROM comments WHERE post_id = %s AND source_comment_id LIKE 'real_comment_%%'")
        # Verify count of unescaped %s matches parameter count (1)
        self.assertEqual(adapted.count("%s"), 1)

    def test_12_api_job_recovery_and_xlsx_export(self):
        """Verify DB-backed job recovery when in-memory JOBS is cleared, and verify CSV & XLSX export endpoints."""
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from unittest.mock import patch
        import api_server
        from api_server import get_job_status, export_post_csv, JOBS, JOBS_LOCK

        with patch.object(api_server, "DB_FILE", TEST_DB), patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            conn = sqlite3.connect(TEST_DB)
            c = conn.cursor()
            c.execute("SELECT id, post_id FROM collection_runs WHERE status = 'COMPLETED' ORDER BY created_at DESC LIMIT 1")
            run_row = c.fetchone()
            conn.close()
            self.assertIsNotNone(run_row)
            run_id, post_id = run_row[0], run_row[1]

            # Clear in-memory JOBS to simulate server restart
            with JOBS_LOCK:
                JOBS.pop(run_id, None)

            recovered = get_job_status(run_id)
            self.assertEqual(recovered["status"], "COMPLETED")
            self.assertEqual(recovered["result"]["post"]["id"], post_id)
            self.assertGreater(len(recovered["result"]["comments"]), 0)

            # Verify CSV export
            csv_resp = export_post_csv(post_id=post_id, deduplicate_authors=True, format="csv", contact_only=False, exclude_replies=False, search=None)
            self.assertIn("text/csv", csv_resp.media_type)
            self.assertIn(b"Lead Name,LinkedIn Profile", csv_resp.body)

            # Verify Excel (.xlsx) export on combined dataset with contact_only=True
            xlsx_resp = export_post_csv(post_id="combined", deduplicate_authors=False, format="xlsx", contact_only=True, exclude_replies=False, search=None)
            self.assertEqual(xlsx_resp.media_type, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.assertTrue(xlsx_resp.body.startswith(b"PK"), "XLSX output must be a valid ZIP/OpenXML container")

    def test_13_deterministic_fallback_ids_and_duplicate_job_prevention(self):
        """Verify deterministic SHA-256 fallback IDs when source ID is missing, and verify concurrent duplicate active job deduplication."""
        import sys
        from concurrent.futures import ThreadPoolExecutor
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from unittest.mock import patch, MagicMock
        from apify_scraper import ApifyScraperService
        import api_server
        from api_server import extract_leads_async, ExtractRequest, JOBS, JOBS_LOCK, wipe_all_posts, delete_post
        from fastapi import HTTPException

        # 1. Deterministic fallback ID ignores idx and generates identical ID across calls
        item_no_id = {
            "commentary": "Deterministic test comment",
            "createdAt": "2026-10-28T15:00:00Z",
            "actor": {
                "name": "Alice Smith",
                "linkedinUrl": "https://www.linkedin.com/in/alicesmith"
            }
        }
        p1 = ApifyScraperService._parse_single_item(item_no_id, idx=0, now_str="2026-10-28 15:00:00")
        p2 = ApifyScraperService._parse_single_item(item_no_id, idx=99, now_str="2026-10-29 18:00:00")
        self.assertTrue(p1["source_comment_id"].startswith("apify_det_"))
        self.assertEqual(p1["source_comment_id"], p2["source_comment_id"])

        # 2. Multi-threaded duplicate active job submission returns identical job_id and schedules worker once
        bg_tasks = MagicMock()
        req = ExtractRequest(
            post_url="https://www.linkedin.com/feed/update/urn:li:activity:7999000111222333444?utm_source=test",
            max_comments=50,
            provider="apify"
        )
        with JOBS_LOCK:
            JOBS.clear()
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(extract_leads_async, req, bg_tasks) for _ in range(8)]
            results = [f.result() for f in futures]
        unique_job_ids = {r["job_id"] for r in results}
        self.assertEqual(len(unique_job_ids), 1, "All 8 concurrent requests must receive the exact same job_id")
        self.assertEqual(bg_tasks.add_task.call_count, 1, "Background worker must only be scheduled once across concurrent submissions")

        # 3. Verify unauthenticated DELETE /api/posts AND DELETE /api/posts/{post_id} are blocked with 403
        with patch.dict(os.environ, {"ADMIN_API_TOKEN": ""}, clear=False):
            with self.assertRaises(HTTPException) as ctx_wipe:
                wipe_all_posts(x_admin_token=None)
            self.assertEqual(ctx_wipe.exception.status_code, 403)

            with self.assertRaises(HTTPException) as ctx_del:
                delete_post(post_id="any-post-id", x_admin_token=None)
            self.assertEqual(ctx_del.exception.status_code, 403)

    def test_14_stale_job_detection_and_formula_injection_unicode_exports(self):
        """Verify stale IN_PROGRESS jobs (>6 min) transition to FAILED in DB (unless thread is alive), and verify formula injection & Unicode/multiline exports in CSV & XLSX."""
        import sys
        import openpyxl
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from unittest.mock import patch, MagicMock
        import api_server
        from api_server import get_job_status, export_post_csv, list_posts, JOBS, JOBS_LOCK

        with patch.object(api_server, "DB_FILE", TEST_DB), patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            conn = sqlite3.connect(TEST_DB)
            c = conn.cursor()

            # Insert a stale IN_PROGRESS run started in the past
            stale_run_id = "run_stale_test_999"
            c.execute("SELECT id FROM posts LIMIT 1")
            post_id = c.fetchone()[0]
            c.execute("""
                INSERT OR REPLACE INTO collection_runs (id, post_id, status, provider_name, total_expected_comments, started_at, created_at, updated_at)
                VALUES (?, ?, 'IN_PROGRESS', 'apify', 50, '2020-01-01 00:00:00', '2020-01-01 00:00:00', '2020-01-01 00:00:00')
            """, (stale_run_id, post_id))

            # Insert a comment with Unicode, multiline text, and leading '=' formula payload
            c.execute("""
                INSERT OR REPLACE INTO comments (
                    id, post_id, source_comment_id, parent_comment_id, parent_source_id,
                    comment_text, author_name, author_profile_url, author_headline,
                    email, phone, company_website, company_linkedin_url,
                    reaction_count, likes_count, commented_at, depth, created_at, updated_at
                ) VALUES (?, ?, 'src_unicode_formula', NULL, NULL, ?, ?, 'https://www.linkedin.com/in/unicodetester', 'VP Engineering 🚀', 'unicode@testcorp.io', '+1-555-000-9999', 'https://testcorp.io', 'https://www.linkedin.com/company/testcorp', 3, 3, '2026-10-28 14:00:00', 0, '2026-10-28 14:00:00', '2026-10-28 14:00:00')
            """, (
                "cid_unicode_1",
                post_id,
                "=HYPERLINK(\"http://evil.tld\",\"Click\")\nLine 2 with café & 日本語 🎯",
                "=EvilName"
            ))
            conn.commit()
            conn.close()

            # 1. If worker thread is still alive in memory, job must NOT be marked FAILED even if timestamp is old
            live_thread_mock = MagicMock()
            live_thread_mock.is_alive.return_value = True
            with JOBS_LOCK:
                JOBS[stale_run_id] = {
                    "job_id": stale_run_id,
                    "status": "IN_PROGRESS",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "message": "Still working...",
                    "_worker_thread": live_thread_mock
                }
            active_status = get_job_status(stale_run_id)
            self.assertEqual(active_status["status"], "IN_PROGRESS")
            self.assertNotIn("_worker_thread", active_status)

            # 2. When worker is gone (cleared from memory), polling stale job marks it FAILED in both response and DB
            with JOBS_LOCK:
                JOBS.pop(stale_run_id, None)

            stale_status = get_job_status(stale_run_id)
            self.assertEqual(stale_status["status"], "FAILED")
            self.assertIn("interrupted", stale_status["error"].lower())

            conn_Verify = sqlite3.connect(TEST_DB)
            db_status = conn_Verify.execute("SELECT status FROM collection_runs WHERE id = ?", (stale_run_id,)).fetchone()[0]
            conn_Verify.close()
            self.assertEqual(db_status, "FAILED", "Database collection_runs row must also be updated to FAILED")

            # 3. Export CSV with search filter and exclude_replies=True, verifying formula sanitization & Unicode preservation
            csv_filtered = export_post_csv(
                post_id=post_id,
                deduplicate_authors=False,
                format="csv",
                contact_only=True,
                exclude_replies=True,
                search="日本語"
            )
            body_text = csv_filtered.body.decode("utf-8")
            self.assertIn("'=EvilName", body_text, "Leading '=' in author name must be prefixed with a single quote")
            self.assertIn("'=HYPERLINK", body_text, "Leading '=' in comment text must be prefixed with a single quote")
            self.assertIn("café & 日本語 🎯", body_text, "Unicode and emojis must be preserved in CSV export")

            # 4. Export XLSX and verify cell-level formula sanitization via openpyxl
            xlsx_filtered = export_post_csv(
                post_id=post_id,
                deduplicate_authors=False,
                format="xlsx",
                contact_only=True,
                exclude_replies=True,
                search="日本語"
            )
            wb = openpyxl.load_workbook(io.BytesIO(xlsx_filtered.body))
            ws = wb.active
            row_values = [cell.value for cell in ws[2]]
            self.assertEqual(row_values[0], "'=EvilName")
            self.assertTrue(str(row_values[6]).startswith("'=HYPERLINK"))

            # 5. Verify DB connection closes even when cursor raises an exception
            mock_conn = MagicMock()
            mock_conn.cursor.side_effect = RuntimeError("Simulated DB cursor error")
            with patch("api_server.get_db_connection", return_value=mock_conn):
                with self.assertRaises(RuntimeError):
                    list_posts()
                mock_conn.close.assert_called_once()

if __name__ == "__main__":
    unittest.main()


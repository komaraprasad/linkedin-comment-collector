import unittest
from collector_service import CollectorService

class TestCompanyDetection(unittest.TestCase):
    def test_corporate_email_domain_extraction(self):
        # Inspironlabs example
        url = CollectorService.extract_company_website("komara.p@inspironlabs.com")
        self.assertEqual(url, "https://inspironlabs.com")

        # Multi-part TLD example
        url_uk = CollectorService.extract_company_website("alex@stripe.co.uk")
        self.assertEqual(url_uk, "https://stripe.co.uk")

        # Comment with explicit website
        url_text = CollectorService.extract_company_website(None, "Check our platform at https://acme.io or contact us")
        self.assertEqual(url_text, "https://acme.io")

    def test_generic_email_filtering(self):
        # Generic personal emails should not map to company websites
        url_gmail = CollectorService.extract_company_website("user123@gmail.com")
        self.assertIsNone(url_gmail)

        url_yahoo = CollectorService.extract_company_website("test@yahoo.com")
        self.assertIsNone(url_yahoo)

    def test_no_email_returns_none(self):
        url_none = CollectorService.extract_company_website(None)
        self.assertIsNone(url_none)

        url_empty = CollectorService.extract_company_website("")
        self.assertIsNone(url_empty)

        url_gmail = CollectorService.extract_company_website("user@gmail.com")
        self.assertIsNone(url_gmail)

    def test_official_company_linkedin_detection(self):
        # Terrascale website has outbound official linkedin link
        li_url = CollectorService.extract_company_linkedin_url(website="https://terrascale.in")
        self.assertEqual(li_url, "https://www.linkedin.com/company/terrascale-innovation-pvt-ltd")

    def test_invalid_candidate_returns_none_preventing_404(self):
        # Speculative/non-existent companies must return None, never a broken link
        li_url = CollectorService.extract_company_linkedin_url(website="https://nonexistent-fake-domain-12345.xyz")
        self.assertIsNone(li_url)

if __name__ == "__main__":
    unittest.main()

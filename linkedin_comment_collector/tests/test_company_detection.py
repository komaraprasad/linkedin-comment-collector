import unittest
from unittest.mock import patch

import company_enrichment as ce
from collector_service import CollectorService

REAL_DOMAINS = {"inspironlabs.com", "stripe.co.uk", "acme.io", "hsbc.com", "chiptekus.com"}
LINKEDIN_PAGES = {
    "hsbc": ("ok", "HSBC", "http://www.hsbc.com"),
    "tata-consultancy-services": ("ok", "Tata Consultancy Services", "https://www.tcs.com"),
    "tcs": ("ok", "Tata Consultancy Services", "https://www.tcs.com"),
    "chiptekus": ("ok", "Chiptek LLC", "https://chiptekus.com"),
}


def fake_resolves(domain):
    d = ce.normalize_domain(domain)
    return bool(d) and ce._registrable(d) in REAL_DOMAINS


def fake_fetch(slug):
    return LINKEDIN_PAGES.get(slug, ("missing", None, None))


@patch("company_enrichment.linkedin_from_website", return_value=None)
@patch("company_enrichment.fetch_linkedin_company", side_effect=fake_fetch)
@patch("company_enrichment.domain_resolves", side_effect=fake_resolves)
class TestCompanyDetection(unittest.TestCase):
    def test_corporate_email_domain_extraction(self, *_):
        self.assertEqual(CollectorService.extract_company_website("komara.p@inspironlabs.com"), "https://inspironlabs.com")
        self.assertEqual(CollectorService.extract_company_website("alex@stripe.co.uk"), "https://stripe.co.uk")
        self.assertEqual(
            CollectorService.extract_company_website(None, "Check our platform at https://acme.io or contact us"),
            "https://acme.io",
        )

    def test_personal_email_never_becomes_company(self, *_):
        for email in ("venkydama39@gmail.com", "ani.varma18@gmail.com", "user@yahoo.com",
                      "rahul@yandex.com", "prajwal005@gmail.con"):
            self.assertEqual(ce.resolve_company(email=email, headline="DevOps Engineer | AWS"), (None, None), email)

    def test_fake_domain_rejected(self, *_):
        self.assertIsNone(CollectorService.extract_company_website(None, "visit venkydama39.in"))

    def test_no_email_returns_none(self, *_):
        self.assertIsNone(CollectorService.extract_company_website(None))
        self.assertIsNone(CollectorService.extract_company_website(""))

    def test_headline_employer_resolves_verified_company(self, *_):
        self.assertEqual(
            ce.resolve_company(email="reddy@gmail.com", headline="DevOps Engineer at HSBC"),
            ("https://hsbc.com", "https://www.linkedin.com/company/hsbc"),
        )

    def test_corporate_email_linkedin_must_match_website(self, *_):
        web, li = ce.resolve_company(email="rosy@chiptekus.com", headline="Bench Sales Recruiter at Chiptek LLC")
        self.assertEqual(web, "https://chiptekus.com")
        self.assertEqual(li, "https://www.linkedin.com/company/chiptekus")

    def test_missing_linkedin_page_returns_none(self, *_):
        self.assertIsNone(ce.resolve_company(email="x@inspironlabs.com", headline="Engineer at Nowhere Corp")[1])


class TestHeadlineParsing(unittest.TestCase):
    def test_company_from_headline(self):
        self.assertEqual(ce.company_from_headline("DevOps Engineer at HSBC"), "HSBC")
        self.assertEqual(ce.company_from_headline("Senior Software Engineer @ Nucleus Software |Cloud"), "Nucleus Software")
        self.assertEqual(ce.company_from_headline("Cloud Engineer at Cybrowse Digital Solutions Pvt Ltd"),
                         "Cybrowse Digital Solutions Pvt Ltd")
        self.assertIsNone(ce.company_from_headline("Site Reliability Engineer | AWS | Kubernetes"))

    def test_names_match(self):
        self.assertTrue(ce.names_match("Chiptek LLC", "Chiptek"))
        self.assertTrue(ce.names_match("TCS", "Tata Consultancy Services"))
        self.assertFalse(ce.names_match("HSBC", "Nucleus Software"))

    def test_personal_domain_typos(self):
        for d in ("gmail.com", "gmail.con", "gmail.co", "yandex.com", "rediffmail.com"):
            self.assertTrue(ce.is_personal_domain(d), d)
        self.assertFalse(ce.is_personal_domain("infokeysinc.com"))


if __name__ == "__main__":
    unittest.main()

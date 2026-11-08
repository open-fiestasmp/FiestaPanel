import unittest
import os
import sys
import json
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import app as portal_app
from website_news_manager import WebsiteNewsManager

class TestWebsiteNewsStudio(unittest.TestCase):
    def setUp(self):
        self.app = portal_app.app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.manager = WebsiteNewsManager()
        self.csrf_token = "test-news-studio-csrf-token"

    def _login(self, role="owner", permissions=None):
        if permissions is None:
            permissions = ["all"]
        user = portal_app.load_users().get("admin", {})
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"] = "admin"
            sess["role"] = role
            sess["permissions"] = permissions
            sess["csrf_token"] = self.csrf_token
            sess["password_version"] = portal_app.session_password_version(user.get("password_hash"))

    def test_rss_xml_is_valid_and_complete(self):
        """Verify generated RSS feed is 100% valid XML and contains all items."""
        res = self.manager.rebuild()
        self.assertTrue(res["success"], f"Hugo rebuild failed: {res}")

        rss_path = os.path.join(self.manager.news_dir, "index.xml")
        self.assertTrue(os.path.isfile(rss_path), "news/index.xml must exist")

        tree = ET.parse(rss_path)
        root = tree.getroot()
        self.assertEqual(root.tag, "rss")
        self.assertEqual(root.attrib.get("version"), "2.0")

        channel = root.find("channel")
        self.assertIsNotNone(channel)
        self.assertEqual(channel.find("title").text, "FiestaSMP - News & Updates")
        self.assertEqual(channel.find("link").text, "https://open-fiestasmp.github.io/news/")

        items = channel.findall("item")
        self.assertGreaterEqual(len(items), 4, "Should have at least 4 published news items")

        for it in items:
            self.assertTrue(bool(it.find("title").text))
            self.assertTrue(bool(it.find("link").text))
            self.assertTrue(bool(it.find("pubDate").text))
            self.assertTrue(bool(it.find("guid").text))

        self.assertTrue(os.path.isfile(os.path.join(self.manager.news_dir, "rss.xml")))
        self.assertTrue(os.path.isfile(os.path.join(self.manager.repo_dir, "rss.xml")))
        self.assertTrue(os.path.isfile(os.path.join(self.manager.repo_dir, "feed.xml")))

    def test_articles_crud_lifecycle(self):
        """Test creating, editing, and deleting an article via WebsiteNewsManager."""
        test_slug = "test-ci-automated-announcement"
        payload = {
            "title": "CI Automated Announcement Test",
            "slug": test_slug,
            "date": "2026-11-08T16:00:00+05:30",
            "author": "debarkak",
            "coauthor": "cbladeOfficial",
            "summary": "This is an automated test announcement for CI verification.",
            "tags": ["Announcements", "AutomatedTest"],
            "draft": False,
            "content": "This is test body content with a **bold feature** and a list:\n\n- Feature 1\n- Feature 2"
        }

        # 1. Create
        save_res = self.manager.save_article(payload)
        self.assertTrue(save_res["success"])
        self.assertEqual(save_res["slug"], test_slug)

        # 2. Read back
        art = self.manager.get_article(test_slug)
        self.assertIsNotNone(art)
        self.assertEqual(art["title"], payload["title"])
        self.assertEqual(art["author"], "debarkak")
        self.assertEqual(art["coauthor"], "cbladeOfficial")
        self.assertIn("Feature 1", art["content"])

        # 3. Update
        payload["title"] = "CI Automated Announcement Test (Updated)"
        payload["orig_slug"] = test_slug
        payload["content"] += "\n- Feature 3 Added"
        update_res = self.manager.save_article(payload)
        self.assertTrue(update_res["success"])

        art_updated = self.manager.get_article(test_slug)
        self.assertEqual(art_updated["title"], "CI Automated Announcement Test (Updated)")
        self.assertIn("Feature 3 Added", art_updated["content"])

        # 4. Delete & cleanup
        del_res = self.manager.delete_article(test_slug)
        self.assertTrue(del_res["success"])
        self.assertIsNone(self.manager.get_article(test_slug))

    def test_news_studio_page_and_api_auth(self):
        """Verify API endpoints require authentication and allow authorized admin access."""
        # Unauthenticated access must fail
        res = self.client.get("/api/news/articles")
        self.assertEqual(res.status_code, 401)

        res = self.client.get("/admin/news-studio")
        self.assertEqual(res.status_code, 302)

        # Authenticate
        self._login(role="owner")

        # Authenticated access to News Studio page
        res = self.client.get("/admin/news-studio")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("FiestaSMP News Studio", html)
        self.assertIn("FiestaSMP Official Website", html)
        self.assertIn("Articles Archive", html)
        self.assertIn("CSRF_TOKEN", html)
        self.assertIn('meta name="csrf-token"', html)

        # Authenticated access to articles API
        res = self.client.get("/api/news/articles")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertGreaterEqual(len(data["articles"]), 4)

        # CSRF enforcement test on article creation and deletion
        dummy_slug = "csrf-test-article"
        dummy_payload = {
            "title": "CSRF Test Article",
            "author": "debarkak",
            "slug": dummy_slug,
            "summary": "Testing CSRF token enforcement on articles API",
            "content": "This is a test article body."
        }

        # 1. POST without CSRF token must be rejected with 403
        res_no_csrf = self.client.post("/api/news/articles", json=dummy_payload)
        self.assertEqual(res_no_csrf.status_code, 403)
        self.assertEqual(res_no_csrf.get_json().get("error"), "Invalid CSRF token")

        # 2. POST with CSRF token must succeed
        res_with_csrf = self.client.post(
            "/api/news/articles",
            json=dummy_payload,
            headers={"X-CSRF-Token": self.csrf_token}
        )
        self.assertEqual(res_with_csrf.status_code, 200)
        self.assertTrue(res_with_csrf.get_json().get("success"))

        # 3. DELETE without CSRF token must be rejected with 403
        res_del_no_csrf = self.client.delete(f"/api/news/articles/{dummy_slug}")
        self.assertEqual(res_del_no_csrf.status_code, 403)
        self.assertEqual(res_del_no_csrf.get_json().get("error"), "Invalid CSRF token")

        # 4. DELETE with CSRF token must succeed
        res_del_with_csrf = self.client.delete(
            f"/api/news/articles/{dummy_slug}",
            headers={"X-CSRF-Token": self.csrf_token}
        )
        self.assertEqual(res_del_with_csrf.status_code, 200)
        self.assertTrue(res_del_with_csrf.get_json().get("success"))

        # Authenticated access to media API
        res = self.client.get("/api/news/media")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIsInstance(data["media"], list)

        # Authenticated access to status API
        res = self.client.get("/api/news/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("rss_url", data)

if __name__ == "__main__":
    unittest.main()

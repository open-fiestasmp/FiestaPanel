import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys
import os
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as portal_app

class PanelUpdateFeatureTests(unittest.TestCase):
    def setUp(self):
        portal_app.app.config['TESTING'] = True
        self.client = portal_app.app.test_client()
        self.csrf_token = "test-panel-update-csrf-token"

    def _login(self, role="owner", permissions=None):
        if permissions is None:
            permissions = ["all"]
        user = portal_app.load_users().get('admin', {})
        with self.client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['user'] = 'admin'
            sess['role'] = role
            sess['permissions'] = permissions
            sess['csrf_token'] = self.csrf_token
            sess['password_version'] = portal_app.session_password_version(user.get('password_hash'))

    def test_check_update_non_logged_in(self):
        res = self.client.get('/api/panel/update/check')
        self.assertIn(res.status_code, (401, 302))

    def test_check_update_non_admin_forbidden(self):
        mock_user = {
            "username": "moderator_user",
            "password_hash": "scrypt:dummy",
            "role": "moderator",
            "permissions": ["console", "parameters"],
            "status": "active"
        }
        mock_users = {"moderator_user": mock_user}

        with patch.object(portal_app, 'load_users', return_value=mock_users), \
             patch.object(portal_app, 'session_password_version', return_value="v1"):
            with self.client.session_transaction() as sess:
                sess['logged_in'] = True
                sess['user'] = "moderator_user"
                sess['role'] = "moderator"
                sess['permissions'] = ["console", "parameters"]
                sess['csrf_token'] = self.csrf_token
                sess['password_version'] = "v1"

            res1 = self.client.get('/api/panel/update/check')
            self.assertEqual(res1.status_code, 403)

            res2 = self.client.get('/api/system/update/check')
            self.assertEqual(res2.status_code, 403)

    def test_check_update_admin_allowed(self):
        self._login(role="owner", permissions=["all"])
        res = self.client.get('/api/panel/update/check?fetch=0')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("current_commit", data)
        self.assertIn("current_branch", data)
        self.assertIn("latest_commit", data)
        self.assertIn("commits_behind", data)
        self.assertIn("update_available", data)

        res2 = self.client.get('/api/system/update/check?fetch=0')
        self.assertEqual(res2.status_code, 200)

    def test_check_update_mocked_pending_commits(self):
        self._login(role="owner", permissions=["all"])

        mock_info = {
            "success": True,
            "is_git_repo": True,
            "current_commit": "abc1234",
            "current_commit_full": "abc1234567890",
            "current_commit_date": "2026-11-08 12:00:00",
            "current_commit_msg": "Previous commit",
            "current_branch": "main",
            "latest_commit": "def5678",
            "latest_commit_full": "def5678901234",
            "latest_commit_date": "2026-11-08 13:00:00",
            "latest_commit_msg": "New upstream feature",
            "commits_behind": 1,
            "update_available": True,
            "remote_url": "https://github.com/open-fiestasmp/FiestaPanel",
            "changelog": [
                {
                    "hash": "def5678",
                    "author": "Fiesta Dev",
                    "message": "New upstream feature",
                    "date": "2026-11-08"
                }
            ],
            "last_checked": "2026-11-08 13:05:00 UTC",
            "error": None
        }

        with patch.object(portal_app, 'get_panel_git_info', return_value=mock_info):
            res = self.client.get('/api/panel/update/check')
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data["update_available"])
            self.assertEqual(data["commits_behind"], 1)
            self.assertEqual(len(data["changelog"]), 1)
            self.assertEqual(data["changelog"][0]["hash"], "def5678")

    def test_apply_update_non_admin_forbidden(self):
        mock_user = {
            "username": "moderator_user",
            "password_hash": "scrypt:dummy",
            "role": "moderator",
            "permissions": ["console"],
            "status": "active"
        }
        mock_users = {"moderator_user": mock_user}

        with patch.object(portal_app, 'load_users', return_value=mock_users), \
             patch.object(portal_app, 'session_password_version', return_value="v1"):
            with self.client.session_transaction() as sess:
                sess['logged_in'] = True
                sess['user'] = "moderator_user"
                sess['role'] = "moderator"
                sess['permissions'] = ["console"]
                sess['csrf_token'] = self.csrf_token
                sess['password_version'] = "v1"

            res1 = self.client.post('/api/panel/update/apply', headers={"X-CSRF-Token": self.csrf_token})
            self.assertEqual(res1.status_code, 403)

            res2 = self.client.post('/api/system/update/apply', headers={"X-CSRF-Token": self.csrf_token})
            self.assertEqual(res2.status_code, 403)

    def test_apply_update_csrf_required(self):
        self._login(role="owner", permissions=["all"])
        res = self.client.post('/api/panel/update/apply')
        self.assertEqual(res.status_code, 403)
        self.assertIn("CSRF", res.get_json().get("error", ""))

    def test_apply_update_success(self):
        self._login(role="owner", permissions=["all"])
        with patch.object(portal_app, 'apply_panel_git_update', return_value=(True, "FiestaPanel updated successfully! Portal service is restarting...")):
            res = self.client.post('/api/panel/update/apply', headers={"X-CSRF-Token": self.csrf_token})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertIn("restarting", data.get("message", "").lower())

    def test_apply_update_git_failure_returns_500(self):
        self._login(role="owner", permissions=["all"])
        with patch.object(portal_app, 'apply_panel_git_update', return_value=(False, "Git pull failed: merge conflict in app.py")):
            res = self.client.post('/api/panel/update/apply', headers={"X-CSRF-Token": self.csrf_token})
            self.assertEqual(res.status_code, 500)
            data = res.get_json()
            self.assertFalse(data.get("success"))
            self.assertIn("Git pull failed", data.get("error", ""))

    def test_dashboard_admin_sees_update_view(self):
        self._login(role="owner", permissions=["all"])
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("view-sys-update", html)
        self.assertIn("btn-panel-check-update", html)
        self.assertIn("btn-panel-apply-update", html)
        self.assertIn("FiestaPanel System Update", html)

    def test_dashboard_non_admin_does_not_see_update_view(self):
        mock_user = {
            "username": "moderator_user",
            "password_hash": "scrypt:dummy",
            "role": "moderator",
            "permissions": ["console"],
            "status": "active"
        }
        mock_users = {"moderator_user": mock_user}

        with patch.object(portal_app, 'load_users', return_value=mock_users), \
             patch.object(portal_app, 'session_password_version', return_value="v1"):
            with self.client.session_transaction() as sess:
                sess['logged_in'] = True
                sess['user'] = "moderator_user"
                sess['role'] = "moderator"
                sess['permissions'] = ["console"]
                sess['csrf_token'] = self.csrf_token
                sess['password_version'] = "v1"

            res = self.client.get('/')
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)
            self.assertNotIn("id=\"view-sys-update\"", html)
            self.assertNotIn("btn-panel-apply-update", html)

if __name__ == '__main__':
    unittest.main()

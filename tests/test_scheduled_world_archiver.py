import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys
import os
import json
import importlib.util
import tempfile
import shutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as portal_app
from addon_manager import AddonManager, AddonContext

class ScheduledWorldArchiverTests(unittest.TestCase):
    def setUp(self):
        portal_app.app.config['TESTING'] = True
        self.client = portal_app.app.test_client()

        # Load scheduled_world_archiver module directly
        module_path = ROOT / "addons" / "scheduled_world_archiver" / "main.py"
        self.assertTrue(module_path.exists())
        spec = importlib.util.spec_from_file_location("test_scheduled_world_archiver", str(module_path))
        self.swa_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.swa_mod)

    def test_addon_json_validity(self):
        addon_json_path = ROOT / "addons" / "scheduled_world_archiver" / "addon.json"
        self.assertTrue(addon_json_path.exists())
        with open(addon_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("id"), "scheduled_world_archiver")
        self.assertEqual(data.get("entrypoint"), "main.py")
        self.assertIn("ui_slots", data)
        self.assertIn("tab", data["ui_slots"])
        self.assertIn("settings_schema", data)
        self.assertIn("default_settings", data)

    def test_tab_html_contains_critical_elements(self):
        tab_html_path = ROOT / "addons" / "scheduled_world_archiver" / "tab.html"
        self.assertTrue(tab_html_path.exists())
        with open(tab_html_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Check for Pre, Post, and General snapshot buttons
        self.assertIn("Take Pre-Event Snapshot (Before)", html)
        self.assertIn("Take Post-Event Snapshot (After)", html)
        self.assertIn("Create Snapshot Package Now", html)
        # Check for multi-cloud references
        self.assertIn("GitHub Releases", html)
        self.assertIn("archive.org", html.lower())
        self.assertIn("S3", html)
        self.assertIn("Calendar", html)
        self.assertIn("Scheduled Public Release Date", html)
        self.assertIn("swaTriggerSnapshot", html)

    def test_init_addon_starts_scheduler(self):
        ctx = MagicMock()
        ctx.manager = MagicMock()
        ctx.manager.server_dir = "/home/ubuntu/fiesta"
        self.swa_mod.init_addon(ctx)
        self.assertTrue(self.swa_mod._SCHEDULER_RUNNING)

    def test_action_get_status(self):
        ctx = MagicMock()
        ctx.get_settings.return_value = {
            "default_cloud_target": "archive_org",
            "github_token": "",
            "github_repo": "",
            "archive_org_access_key": "test_ak",
            "archive_org_secret_key": "test_sk"
        }
        res = self.swa_mod.on_action(ctx, "get_status", {})
        self.assertTrue(res.get("success"))
        self.assertIn("archives", res)
        self.assertIn("calendar_events", res)
        self.assertIn("task", res)
        self.assertIn("next_episode_num", res)

    def test_action_test_github_credentials(self):
        ctx = MagicMock()
        # Missing token/repo
        res = self.swa_mod.on_action(ctx, "test_github_credentials", {})
        self.assertFalse(res.get("success"))

        # Valid mock repo
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "full_name": "open-fiestasmp/world-downloads",
                "permissions": {"push": True}
            }
            mock_get.return_value = mock_resp

            res2 = self.swa_mod.on_action(ctx, "test_github_credentials", {
                "github_token": "valid_token",
                "github_repo": "open-fiestasmp/world-downloads"
            })
            self.assertTrue(res2.get("success"))
            self.assertIn("open-fiestasmp/world-downloads", res2.get("message", ""))

    def test_action_test_ia_credentials(self):
        ctx = MagicMock()
        res = self.swa_mod.on_action(ctx, "test_ia_credentials", {})
        self.assertFalse(res.get("success"))

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp

            res2 = self.swa_mod.on_action(ctx, "test_ia_credentials", {
                "archive_org_access_key": "valid_ak",
                "archive_org_secret_key": "valid_sk"
            })
            self.assertTrue(res2.get("success"))

    def test_action_create_calendar_event(self):
        temp_dir = tempfile.mkdtemp()
        try:
            ctx = MagicMock()
            ctx.manager.base_dir = temp_dir

            res = self.swa_mod.on_action(ctx, "create_calendar_event", {
                "title": "Season 1 Finale World Release",
                "start_time": "2026-11-15T18:00"
            })
            self.assertTrue(res.get("success"))

            events_path = os.path.join(temp_dir, "events.json")
            self.assertTrue(os.path.isfile(events_path))
            with open(events_path, "r", encoding="utf-8") as f:
                evts = json.load(f)
            self.assertEqual(len(evts), 1)
            self.assertEqual(evts[0]["title"], "Season 1 Finale World Release")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_addon_manager_discovery_and_render(self):
        mgr = AddonManager("/home/ubuntu/fiesta-portal/data", "/home/ubuntu/fiesta-portal", None, None, None, "/home/ubuntu/fiesta")
        mgr.scan_addons()
        self.assertIn("scheduled_world_archiver", mgr.installed_addons)

        html = mgr.render_addon_tab("scheduled_world_archiver")
        self.assertIn("Scheduled World Archiver", html)
        self.assertIn("Multi-Cloud Publisher", html)

    def test_portal_routes_with_login(self):
        user = portal_app.load_users().get('admin', {})
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"] = "admin"
            sess["role"] = "owner"
            sess["permissions"] = ["all"]
            sess["csrf_token"] = "test_token"
            sess["password_version"] = portal_app.session_password_version(user.get("password_hash"))

        # Test view route
        resp = self.client.get("/api/panel-addons/scheduled_world_archiver/view")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Scheduled World Archiver", resp.data)

        # Test action route
        resp_act = self.client.post(
            "/api/panel-addons/scheduled_world_archiver/action",
            headers={"X-CSRF-Token": "test_token"},
            json={"action": "get_status", "payload": {}}
        )
        self.assertEqual(resp_act.status_code, 200)
        data = resp_act.get_json()
        self.assertTrue(data.get("success"))

if __name__ == "__main__":
    unittest.main()

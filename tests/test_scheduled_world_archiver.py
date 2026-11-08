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
        # Check for Instant Save & Direct Cloud Upload
        self.assertIn("Save Current World & Directly Upload", html.replace("&amp;", "&"))
        self.assertIn("swaTriggerInstantSaveAndUpload", html)
        # Check for multi-cloud references
        self.assertIn("GitHub Releases", html)
        self.assertIn("archive.org", html.lower())
        self.assertIn("S3", html)
        self.assertIn("Calendar", html)
        self.assertIn("Scheduled Public Release Date", html)
        self.assertIn("swaTriggerSnapshot", html)
        # Check for multi-platform checkboxes
        self.assertIn("swa-instant-target-chk", html)
        self.assertIn("swa-sched-target-chk", html)
        self.assertIn("swa-picker-target-chk", html)
        self.assertIn("swa-def-target-chk", html)
        self.assertIn("swaSelectAllInstantTargets", html)
        self.assertIn("swaSelectAllPickerTargets", html)

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

    def test_dispatch_cloud_upload_multi_targets(self):
        ctx = MagicMock()
        ctx.get_settings.return_value = {}

        with patch.object(self.swa_mod, "_execute_ia_upload", return_value=(True, "https://ia.mock")) as mock_ia, \
             patch.object(self.swa_mod, "_execute_github_upload", return_value=(True, "https://gh.mock")) as mock_gh:

            self.swa_mod._dispatch_cloud_upload(ctx, "arc_123", ["archive_org", "github"])

            mock_ia.assert_called_once()
            mock_gh.assert_called_once()
            self.assertEqual(self.swa_mod._CURRENT_TASK.get("status"), "completed")
            self.assertIn("Internet Archive", self.swa_mod._CURRENT_TASK.get("stage", ""))
            self.assertIn("GitHub Releases", self.swa_mod._CURRENT_TASK.get("stage", ""))

    def test_action_upload_archive_multi_targets(self):
        ctx = MagicMock()
        with patch("threading.Thread") as mock_thread:
            mock_instance = MagicMock()
            mock_thread.return_value = mock_instance

            res = self.swa_mod.on_action(ctx, "upload_archive", {
                "archive_id": "arc_abc",
                "targets": ["archive_org", "s3"]
            })
            self.assertTrue(res.get("success"))
            self.assertIn("archive_org, s3", res.get("message"))
            mock_instance.start.assert_called_once()

    def test_dashboard_fiesta_studio_and_ellipsis(self):
        dash_path = ROOT / "templates" / "dashboard.html"
        self.assertTrue(dash_path.exists())
        with open(dash_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Check Fiesta Studio link and buttons
        self.assertIn("nav-sub-studio-link", html)
        self.assertIn("btn-fiesta-studio", html)
        self.assertIn("Fiesta Studio &#8599;", html)
        self.assertIn("STUDIO", html)

        # Check installed addons ellipsis styling
        self.assertIn("clampTextEllipsis(rawDesc, 140)", html)
        self.assertIn("-webkit-line-clamp: 2", html)
        self.assertIn("text-overflow: ellipsis", html)

if __name__ == "__main__":
    unittest.main()

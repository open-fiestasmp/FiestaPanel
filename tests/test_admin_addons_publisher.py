import unittest
from unittest.mock import patch
from pathlib import Path
import sys
import json
import tempfile
import os
import shutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as portal_app

class AdminAddonsPublisherTests(unittest.TestCase):
    def setUp(self):
        portal_app.app.config['TESTING'] = True
        self.client = portal_app.app.test_client()
        self.csrf_token = "test-csrf-token"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        # Temp files for isolation
        self.temp_config = os.path.join(self.temp_dir.name, "config.json")
        self.temp_branding = os.path.join(self.temp_dir.name, "branding.json")
        self.temp_repo = os.path.join(self.temp_dir.name, "repository.json")

        if os.path.exists(portal_app.CONFIG_FILE):
            shutil.copy2(portal_app.CONFIG_FILE, self.temp_config)
        else:
            with open(self.temp_config, "w", encoding="utf-8") as f:
                json.dump({}, f)

        if os.path.exists(portal_app.BRANDING_FILE):
            shutil.copy2(portal_app.BRANDING_FILE, self.temp_branding)
        else:
            with open(self.temp_branding, "w", encoding="utf-8") as f:
                json.dump({}, f)

        # Create a test repository file
        test_repo_data = {
            "version": "1.0",
            "name": "FiestaPanel Official Addon Repository",
            "official_discord": "https://discord.gg/fiestasmp",
            "addons": [
                {
                    "id": "mock_tool",
                    "name": "Mock Test Tool",
                    "version": "1.0.0",
                    "category": "Tools",
                    "description": "Mock addon for testing.",
                    "official": True,
                    "readme": "# Mock Readme",
                    "files": {
                        "main.py": "# Mock main\n",
                        "tab.html": "<div>Mock</div>"
                    }
                }
            ]
        }
        with open(self.temp_repo, "w", encoding="utf-8") as f:
            json.dump(test_repo_data, f, indent=2)

        self.orig_addon_repo_file = portal_app.addon_manager.repo_file
        self.orig_server_config = portal_app.SERVER_CONFIG.copy()
        portal_app.addon_manager.repo_file = self.temp_repo

        self.patch_cfg = patch.object(portal_app, 'CONFIG_FILE', self.temp_config)
        self.patch_br = patch.object(portal_app, 'BRANDING_FILE', self.temp_branding)
        self.patch_repo_file = patch.object(portal_app, 'OFFICIAL_REPO_FILE', self.temp_repo)
        self.patch_cfg.start()
        self.patch_br.start()
        self.patch_repo_file.start()

        # Set a known test dev key
        self.test_dev_key = "fixture-dev-key"
        portal_app.SERVER_CONFIG["fiesta_dev_key"] = self.test_dev_key
        with open(self.temp_config, "r+", encoding="utf-8") as f:
            cfg = json.load(f)
            cfg["fiesta_dev_key"] = self.test_dev_key
            f.seek(0)
            json.dump(cfg, f, indent=2)
            f.truncate()

    def tearDown(self):
        portal_app.SERVER_CONFIG = self.orig_server_config
        portal_app.addon_manager.repo_file = self.orig_addon_repo_file
        self.patch_cfg.stop()
        self.patch_br.stop()
        self.patch_repo_file.stop()

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

    def _post(self, url, data=None):
        headers = {"X-CSRF-Token": self.csrf_token}
        if data is not None:
            headers["Content-Type"] = "application/json"
            return self.client.post(url, data=json.dumps(data), headers=headers)
        return self.client.post(url, headers=headers)

    def test_unauthenticated_panel_user_redirected(self):
        # Without logging into the panel, navigating to /admin/addons-publisher redirects to login
        res = self.client.get('/admin/addons-publisher')
        self.assertEqual(res.status_code, 302)
        self.assertIn('/login', res.headers.get('Location', ''))

    def test_gatekeeper_renders_locked_state(self):
        self._login()
        res = self.client.get('/admin/addons-publisher')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("Fiesta Developer Studio", html)
        self.assertIn("Fiesta Team Developer Gateway Locked", html)
        self.assertIn("Enter Fiesta Developer Secret Key", html)

    def test_publisher_disabled_when_no_key_configured(self):
        self._login()
        # Remove key configuration
        portal_app.SERVER_CONFIG.pop("fiesta_dev_key", None)
        with open(self.temp_config, "w", encoding="utf-8") as f:
            json.dump({}, f)

        # 1. Page shows disabled warning
        res = self.client.get('/admin/addons-publisher')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("Developer Publishing Disabled", html)
        self.assertNotIn("Enter Fiesta Developer Secret Key", html)

        # 2. Old hardcoded key is rejected and unlock is 403
        res_old = self._post('/api/admin/addons-publisher/unlock', {"key": "legacy-fixture-key"})
        self.assertEqual(res_old.status_code, 403)
        self.assertFalse(res_old.get_json().get("success"))
        self.assertIn("disabled on this server instance", res_old.get_json().get("error", ""))

    def test_unlock_with_invalid_key_fails(self):
        self._login()
        res = self._post('/api/admin/addons-publisher/unlock', {"key": "wrong_key"})
        self.assertEqual(res.status_code, 401)
        data = res.get_json()
        self.assertFalse(data.get("success"))
        self.assertIn("Invalid Developer Key", data.get("error", ""))

    def test_repo_mutations_blocked_without_dev_key(self):
        self._login()
        # Normal logged in admin cannot edit repo without dev authentication
        res = self._post('/api/admin/addons-publisher/discord-link', {"url": "https://discord.gg/hacked"})
        self.assertEqual(res.status_code, 403)
        self.assertFalse(res.get_json().get("success"))

        res_save = self._post('/api/admin/addons-publisher/save-addon', {
            "id": "unauthorized_addon",
            "name": "Bad Addon",
            "version": "1.0.0"
        })
        self.assertEqual(res_save.status_code, 403)
        self.assertFalse(res_save.get_json().get("success"))

        res_del = self.client.delete('/api/admin/addons-publisher/delete-addon/mock_tool', headers={"X-CSRF-Token": self.csrf_token})
        self.assertEqual(res_del.status_code, 403)

    def test_full_dev_publisher_lifecycle(self):
        self._login()

        # 1. Unlock developer studio
        res_unlock = self._post('/api/admin/addons-publisher/unlock', {"key": self.test_dev_key})
        self.assertEqual(res_unlock.status_code, 200)
        self.assertTrue(res_unlock.get_json().get("success"))

        # Verify page renders unlocked studio
        res_page = self.client.get('/admin/addons-publisher')
        self.assertEqual(res_page.status_code, 200)
        html = res_page.get_data(as_text=True)
        self.assertIn("Publisher Unlocked", html)

        # 2. Get repository catalog
        res_repo = self.client.get('/api/admin/addons-publisher/repo')
        self.assertEqual(res_repo.status_code, 200)
        repo_data = res_repo.get_json()
        self.assertTrue(repo_data.get("success"))
        self.assertEqual(len(repo_data.get("repository", {}).get("addons", [])), 1)

        # 3. Update Official Discord URL
        new_discord = "https://discord.gg/new-fiesta-invite"
        res_disc = self._post('/api/admin/addons-publisher/discord-link', {"url": new_discord})
        self.assertEqual(res_disc.status_code, 200)
        self.assertTrue(res_disc.get_json().get("success"))
        with open(self.temp_repo, "r", encoding="utf-8") as f:
            saved_repo = json.load(f)
            self.assertEqual(saved_repo.get("official_discord"), new_discord)

        # 4. Save/Publish a new Addon
        new_addon_data = {
            "id": "fiesta_motd_pro",
            "name": "Fiesta MOTD Pro",
            "version": "1.2.0",
            "category": "Visuals",
            "description": "Professional server MOTD colorizer and gradient animator.",
            "author": "FiestaSMP Team",
            "icon": "palette",
            "badge": "Official",
            "official": True,
            "min_portal_version": "2.0.0",
            "readme": "# Fiesta MOTD Pro\n\nAnimated gradients for your server.",
            "main_py": "# Fiesta MOTD Pro\nprint('Running MOTD Pro')\n",
            "tab_html": "<div><h3>MOTD Pro Live</h3></div>",
            "settings": {
                "animation_speed": 500,
                "banner_text": "Welcome to FiestaSMP"
            }
        }
        res_save = self._post('/api/admin/addons-publisher/save-addon', new_addon_data)
        self.assertEqual(res_save.status_code, 200)
        self.assertTrue(res_save.get_json().get("success"))

        # Verify addon was added to repository.json
        with open(self.temp_repo, "r", encoding="utf-8") as f:
            updated_repo = json.load(f)
            addons_list = updated_repo.get("addons", [])
            addon_ids = [a["id"] for a in addons_list]
            self.assertIn("fiesta_motd_pro", addon_ids)
            saved_addon = next(a for a in addons_list if a["id"] == "fiesta_motd_pro")
            self.assertEqual(saved_addon["name"], "Fiesta MOTD Pro")
            self.assertEqual(saved_addon["category"], "Visuals")
            self.assertEqual(saved_addon["files"]["main.py"], new_addon_data["main_py"])
            self.assertEqual(saved_addon["files"]["tab.html"], new_addon_data["tab_html"])

        # 5. Export addon zip bundle
        res_export = self.client.get('/api/admin/addons-publisher/export/fiesta_motd_pro')
        self.assertEqual(res_export.status_code, 200)
        self.assertEqual(res_export.mimetype, 'application/zip')
        self.assertIn('fiesta_motd_pro', res_export.headers.get('Content-Disposition', ''))
        self.assertTrue(res_export.headers.get('Content-Disposition', '').endswith('.zip'))

        # 6. Delete addon
        res_del = self.client.delete('/api/admin/addons-publisher/delete-addon/fiesta_motd_pro', headers={"X-CSRF-Token": self.csrf_token})
        self.assertEqual(res_del.status_code, 200)
        self.assertTrue(res_del.get_json().get("success"))

        with open(self.temp_repo, "r", encoding="utf-8") as f:
            final_repo = json.load(f)
            final_ids = [a["id"] for a in final_repo.get("addons", [])]
            self.assertNotIn("fiesta_motd_pro", final_ids)

        # 7. Rotate Developer Key
        new_key = "rotated_super_secret_fiesta_key_9999"
        res_rot = self._post('/api/admin/addons-publisher/key', {"new_key": new_key})
        self.assertEqual(res_rot.status_code, 200)
        self.assertTrue(res_rot.get_json().get("success"))
        with open(self.temp_config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            self.assertEqual(cfg.get("fiesta_dev_key"), new_key)

        # 8. Lock developer studio
        res_lock = self._post('/api/admin/addons-publisher/lock')
        self.assertEqual(res_lock.status_code, 200)

        # Now any mutation attempt should be 403 Forbidden again
        res_blocked = self._post('/api/admin/addons-publisher/discord-link', {"url": "https://discord.gg/fail"})
        self.assertEqual(res_blocked.status_code, 403)


if __name__ == '__main__':
    unittest.main()

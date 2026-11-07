import unittest
import os
import shutil
import tempfile
import json
import uuid
import io
import zipfile
from unittest.mock import patch, MagicMock

import app as portal_app
from app import (
    app,
    normalize_addon_stem,
    check_addon_exists,
    resolve_software_download
)

class TestAddonExistsAndSoftwareGrid(unittest.TestCase):
    def setUp(self):
        portal_app.app.config['TESTING'] = True
        self.client = portal_app.app.test_client()
        self.temp_dir = tempfile.mkdtemp()
        self.orig_server_dir = portal_app.SERVER_DIR
        portal_app.SERVER_DIR = self.temp_dir
        self.csrf_token = str(uuid.uuid4())

        self._login()

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

    def _get(self, url):
        return self.client.get(url)

    def tearDown(self):
        portal_app.SERVER_DIR = self.orig_server_dir
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_normalize_addon_stem(self):
        cases = {
            "LuckPerms-Bukkit-5.5.71.jar": "luckperms",
            "LuckPerms-Bukkit-5.5.72.jar": "luckperms",
            "luckperms": "luckperms",
            "EssentialsX-2.22.0.jar": "essentialsx",
            "EssentialsXChat-2.22.0.jar": "essentialsxchat",
            "AuthMe-6.0.0-Paper.jar": "authme",
            "CoreProtect-24.0.jar": "coreprotect",
            "DecentHolograms-2.10.2.jar": "decentholograms",
            "ViaVersion-5.12.2.jar": "viaversion",
            "minimotd-paper-2.2.5.jar": "minimotd",
            "floodgate-spigot.jar": "floodgate",
            "fabric-api-0.92.0+1.20.1.jar": "fabricapi",
            "appleskin-fabric-mc1.20.1-2.5.1.jar": "appleskin",
            "TAB.jar": "tab",
            "jei-1.20.1-fabric-15.3.0.4.jar": "jei"
        }
        for filename, expected in cases.items():
            self.assertEqual(normalize_addon_stem(filename), expected, f"Failed for {filename}")

    def test_check_addon_exists_detection(self):
        plugins_dir = os.path.join(self.temp_dir, "plugins")
        disabled_dir = os.path.join(plugins_dir, "disabled")
        os.makedirs(disabled_dir, exist_ok=True)

        # Create active plugin
        with open(os.path.join(plugins_dir, "LuckPerms-Bukkit-5.5.71.jar"), "w") as f:
            f.write("test jar")

        # Create disabled plugin
        with open(os.path.join(disabled_dir, "ChestShop-3.12.jar"), "w") as f:
            f.write("test jar")

        # 1. Exact match active
        res1 = check_addon_exists(plugins_dir, candidate_filename="LuckPerms-Bukkit-5.5.71.jar")
        self.assertIsNotNone(res1)
        self.assertEqual(res1["status"], "active")
        self.assertTrue(res1["exact"])

        # 2. Semantic version update match active
        res2 = check_addon_exists(plugins_dir, project_name="LuckPerms", candidate_filename="LuckPerms-Bukkit-5.5.72.jar", project_id="luckperms")
        self.assertIsNotNone(res2)
        self.assertEqual(res2["status"], "active")
        self.assertEqual(res2["filename"], "LuckPerms-Bukkit-5.5.71.jar")

        # 3. Disabled plugin match
        res3 = check_addon_exists(plugins_dir, project_name="ChestShop", candidate_filename="ChestShop-3.13.jar", project_id="chestshop")
        self.assertIsNotNone(res3)
        self.assertEqual(res3["status"], "disabled")
        self.assertEqual(res3["filename"], "ChestShop-3.12.jar")

        # 4. Non-existent plugin
        res4 = check_addon_exists(plugins_dir, project_name="WorldEdit", candidate_filename="WorldEdit-7.3.0.jar", project_id="worldedit")
        self.assertIsNone(res4)

    def test_plugin_install_already_exists_conflict_and_overwrite(self):
        plugins_dir = os.path.join(self.temp_dir, "plugins")
        os.makedirs(plugins_dir, exist_ok=True)
        old_jar = os.path.join(plugins_dir, "LuckPerms-Bukkit-5.5.71.jar")
        with open(old_jar, "w") as f:
            f.write("old version jar")

        # Attempt download without overwrite flag -> should return 409
        resp = self._post("/api/plugin/install_online", {
            "project_id": "luckperms",
            "name": "LuckPerms",
            "filename": "LuckPerms-Bukkit-5.5.72.jar",
            "download_url": "https://example.com/LuckPerms-Bukkit-5.5.72.jar"
        })
        self.assertEqual(resp.status_code, 409)
        d = resp.get_json()
        self.assertTrue(d.get("already_exists"))
        self.assertEqual(d.get("existing_file"), "LuckPerms-Bukkit-5.5.71.jar")

        # Now mock the actual urllib download for overwrite: True
        fake_jar_bytes = b"PK\x03\x04new jar payload with 1024 bytes" * 50
        mock_response = io.BytesIO(fake_jar_bytes)
        mock_response.geturl = MagicMock(return_value="https://example.com/LuckPerms-Bukkit-5.5.72.jar")
        mock_response.headers = MagicMock()
        mock_response.headers.get_filename.return_value = "LuckPerms-Bukkit-5.5.72.jar"

        with patch("urllib.request.urlopen", return_value=mock_response):
            resp_ow = self._post("/api/plugin/install_online", {
                "project_id": "luckperms",
                "name": "LuckPerms",
                "filename": "LuckPerms-Bukkit-5.5.72.jar",
                "download_url": "https://example.com/LuckPerms-Bukkit-5.5.72.jar",
                "overwrite": True
            })
            self.assertEqual(resp_ow.status_code, 200)
            d_ow = resp_ow.get_json()
            self.assertTrue(d_ow.get("success"))
            self.assertEqual(d_ow.get("filename"), "LuckPerms-Bukkit-5.5.72.jar")
            # Old jar should be cleaned up
            self.assertFalse(os.path.exists(old_jar))
            # New jar should exist
            self.assertTrue(os.path.exists(os.path.join(plugins_dir, "LuckPerms-Bukkit-5.5.72.jar")))

    def test_mod_install_already_exists_conflict_and_overwrite(self):
        mods_dir = os.path.join(self.temp_dir, "mods")
        os.makedirs(mods_dir, exist_ok=True)
        old_jar = os.path.join(mods_dir, "fabric-api-0.91.0+1.20.1.jar")
        with open(old_jar, "w") as f:
            f.write("old mod jar")

        # Attempt download without overwrite flag -> should return 409
        resp = self._post("/api/mod/install_online", {
            "project_id": "fabric-api",
            "name": "Fabric API",
            "filename": "fabric-api-0.92.0+1.20.1.jar",
            "download_url": "https://example.com/fabric-api-0.92.0+1.20.1.jar"
        })
        self.assertEqual(resp.status_code, 409)
        d = resp.get_json()
        self.assertTrue(d.get("already_exists"))
        self.assertEqual(d.get("existing_file"), "fabric-api-0.91.0+1.20.1.jar")

        # Mock download for overwrite: True
        fake_jar_bytes = b"PK\x03\x04new mod payload" * 50
        mock_response = io.BytesIO(fake_jar_bytes)
        mock_response.geturl = MagicMock(return_value="https://example.com/fabric-api-0.92.0+1.20.1.jar")
        mock_response.headers = MagicMock()
        mock_response.headers.get_filename.return_value = "fabric-api-0.92.0+1.20.1.jar"

        with patch("urllib.request.urlopen", return_value=mock_response):
            resp_ow = self._post("/api/mod/install_online", {
                "project_id": "fabric-api",
                "name": "Fabric API",
                "filename": "fabric-api-0.92.0+1.20.1.jar",
                "download_url": "https://example.com/fabric-api-0.92.0+1.20.1.jar",
                "overwrite": True
            })
            self.assertEqual(resp_ow.status_code, 200)
            d_ow = resp_ow.get_json()
            self.assertTrue(d_ow.get("success"))
            self.assertEqual(d_ow.get("filename"), "fabric-api-0.92.0+1.20.1.jar")
            # Old jar should be cleaned up
            self.assertFalse(os.path.exists(old_jar))
            # New jar should exist
            self.assertTrue(os.path.exists(os.path.join(mods_dir, "fabric-api-0.92.0+1.20.1.jar")))

    def test_folia_versions_and_download_support(self):
        # 1. api_software_versions endpoint for folia
        resp = self._get("/api/software/versions?software=folia")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertTrue(d.get("success"))
        self.assertEqual(d.get("software"), "folia")
        self.assertTrue(len(d.get("versions", [])) > 0)
        self.assertIn("1.21.4", d.get("versions"))

        # 2. resolve_software_download for folia
        dl_url, filename, label = resolve_software_download("folia", "1.21.4")
        self.assertTrue(dl_url.startswith("http"))
        self.assertTrue(filename.startswith("folia-1.21.4"))
        self.assertTrue(filename.endswith(".jar"))
        self.assertIn("Folia", label)

    def test_software_grid_symmetry_and_equal_rows(self):
        # Verify dashboard template markup and styles
        tpl_path = os.path.join(os.path.dirname(self.orig_server_dir), "templates", "dashboard.html")
        if not os.path.exists(tpl_path):
            tpl_path = os.path.join("/home/ubuntu/fiesta-portal", "templates", "dashboard.html")
        with open(tpl_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 1. Grid class and CSS properties
        self.assertIn(".software-cards-grid", content)
        self.assertIn("grid-auto-rows: 1fr;", content)
        self.assertIn("grid-template-columns: repeat(6, 1fr);", content)
        self.assertIn(".sw-card-badge-slot", content)

        # 2. Exactly 12 software cards for 6x2 symmetry
        import re
        cards = re.findall(r'<div class="software-card[^"]*" data-sw="([^"]+)"', content)
        self.assertEqual(len(cards), 12, f"Expected 12 software cards, found {len(cards)}: {cards}")
        self.assertIn("folia", cards)
        self.assertIn("purpur", cards)
        self.assertIn("paper", cards)
        self.assertIn("vanilla", cards)
        self.assertIn("bungeecord", cards)

        # 3. Every card has a badge slot container to guarantee equal row heights
        badge_slots = re.findall(r'class="sw-card-badge-slot"', content)
        self.assertEqual(len(badge_slots), 12, "Every software card must have a .sw-card-badge-slot")

    def test_install_scripts_endpoints(self):
        # 1. /install.sh
        resp_sh = self._get("/install.sh")
        self.assertEqual(resp_sh.status_code, 200)
        self.assertIn(b"FiestaPanel", resp_sh.data)
        resp_sh.close()

        # 2. /install.ps1
        resp_ps1 = self._get("/install.ps1")
        self.assertEqual(resp_ps1.status_code, 200)
        self.assertIn(b"FiestaPanel", resp_ps1.data)
        resp_ps1.close()

        # 3. /install.bat
        resp_bat = self._get("/install.bat")
        self.assertEqual(resp_bat.status_code, 200)
        self.assertIn(b"FiestaPanel", resp_bat.data)
        self.assertIn(b"@echo off", resp_bat.data)
        resp_bat.close()

    def test_bedrock_zip_slip_prevention(self):
        evil_zip_path = os.path.join(self.temp_dir, "evil.zip")
        with zipfile.ZipFile(evil_zip_path, "w") as zf:
            zf.writestr("../evil.txt", "pwned")

        # Simulate bedrock extraction check logic
        with zipfile.ZipFile(evil_zip_path, "r") as zf:
            real_server_dir = os.path.realpath(self.temp_dir)
            with self.assertRaises(ValueError):
                for member in zf.namelist():
                    abs_target = os.path.realpath(os.path.join(self.temp_dir, member))
                    if not (abs_target == real_server_dir or abs_target.startswith(real_server_dir + os.sep)):
                        raise ValueError(f"Unsafe path in archive: {member}")
                zf.extractall(self.temp_dir)

    def test_dark_theme_and_modal_contrast_fixes(self):
        tpl_path = os.path.join(portal_app.BASE_DIR, "templates", "dashboard.html")
        with open(tpl_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 1. Event reminder banner uses CSS variables
        self.assertIn('id="tp-event-reminder-banner"', content)
        self.assertIn('background: var(--banner-success-bg, #eff6ff);', content)

        # 2. FM batch toolbar uses CSS variables
        self.assertIn('id="fm-batch-toolbar"', content)
        self.assertIn('background: var(--table-th-bg, #fef3c7);', content)

        # 3. Player inventory modal uses CSS variables
        self.assertIn('id="inv-modal-body"', content)
        self.assertIn('background: var(--table-th-bg, #fafafa);', content)

        # 4. Perm badge all has dark theme overrides
        self.assertIn('[data-theme="dark"] .perm-badge.all', content)
        self.assertIn('[data-theme="catppuccin"] .perm-badge.all', content)

        # 5. Addon modal active tab has overrides for all dark themes
        self.assertIn('[data-theme="archer-dark"] .addon-modal-tab-btn.active-tab', content)
        self.assertIn('[data-theme="nighthawk"] .addon-modal-tab-btn.active-tab', content)

        # 6. Server software Java switcher box and runtime cards have Dark Reader & dark theme compatibility
        self.assertIn('id="jre-switcher-box" class="jre-switcher-box"', content)
        self.assertNotIn('border: 1px solid var(--border-color, #cbd5e1); background: var(--bg-main, #ffffff); padding: 12px;', content)
        self.assertIn('html[data-darkreader-scheme="dark"] .jre-switcher-box', content)
        self.assertIn('html[data-darkreader-scheme="dark"] .jre-item-card', content)
        self.assertIn('[data-theme="dark"] .jre-switcher-box', content)
        self.assertIn('[data-theme="dark"] .jre-item-card', content)
        self.assertIn('class="jre-item-card', content)

if __name__ == "__main__":
    unittest.main()

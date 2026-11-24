import importlib.util
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "archiver_reliability", ROOT / "addons/scheduled_world_archiver/main.py"
)
archiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archiver)


class Context:
    def __init__(self, directory, online=False):
        self.manager = SimpleNamespace(server_dir=str(directory / "server"))
        self.online = online
        self.command = (True, "sent")

    def get_server_status(self):
        return {"online": self.online}

    def get_settings(self):
        return {"archive_org_access_key": "access", "archive_org_secret_key": "secret"}

    def send_command(self, command):
        return self.command


class ArchiverReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "backups").mkdir()
        (self.root / "server/world").mkdir(parents=True)
        (self.root / "server/world/level.dat").write_bytes(b"world-test")
        self.ctx = Context(self.root)
        archiver._CURRENT_TASK = {
            "task_id": "test",
            "status": "running",
            "cancelled": False,
            "start_time": time.time(),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def archive(self, auto_upload=False, upload_target=""):
        archiver._execute_archive_task(
            self.ctx,
            0,
            "World",
            "snapshot",
            "manual",
            "",
            "",
            {"overworld": True, "nether": False, "the_end": False},
            auto_upload,
            upload_target,
            "",
        )

    def test_snapshot_writes_world_and_manifest(self):
        self.archive()
        self.assertEqual(archiver._CURRENT_TASK["status"], "completed")
        item = archiver._load_manifest(self.ctx)[0]
        with zipfile.ZipFile(item["filepath"]) as archive:
            self.assertEqual(archive.read("world/level.dat"), b"world-test")

    def test_local_only_destination_finishes_without_upload(self):
        with patch.object(archiver, "_dispatch_cloud_upload") as dispatch:
            self.archive(auto_upload=True, upload_target=["local"])
        self.assertEqual(archiver._CURRENT_TASK["status"], "completed")
        dispatch.assert_not_called()

    def test_failed_save_command_stops_online_snapshot(self):
        self.ctx.online = True
        self.ctx.command = (False, "console unavailable")
        (self.root / "server/logs").mkdir()
        (self.root / "server/logs/latest.log").touch()
        self.archive()
        self.assertEqual(archiver._CURRENT_TASK["status"], "error")
        self.assertIn("console unavailable", archiver._CURRENT_TASK["error"])
        self.assertFalse(archiver._load_manifest(self.ctx))

    def test_online_snapshot_waits_for_save_confirmation(self):
        self.ctx.online = True
        (self.root / "server/logs").mkdir()
        log = self.root / "server/logs/latest.log"
        log.touch()

        def send(command):
            with open(log, "a") as stream:
                stream.write("[Server thread/INFO]: Saved the game\n")
            return True, "sent"

        self.ctx.send_command = send
        self.archive()
        self.assertEqual(archiver._CURRENT_TASK["status"], "completed")

    def test_file_error_does_not_register_partial_archive(self):
        with patch.object(zipfile.ZipFile, "write", side_effect=OSError("read failed")):
            self.archive()
        self.assertEqual(archiver._CURRENT_TASK["status"], "error")
        self.assertFalse(archiver._load_manifest(self.ctx))
        self.assertFalse(list((self.root / "backups/episodes").glob("*.zip")))

    def test_manifest_error_does_not_report_success(self):
        with patch.object(archiver, "_save_manifest", return_value=False):
            self.archive()
        self.assertEqual(archiver._CURRENT_TASK["status"], "error")
        self.assertFalse(list((self.root / "backups/episodes").glob("*.zip")))

    def test_ia_stream_sends_exact_bytes_and_headers_with_unicode_title(self):
        self.archive()
        item = archiver._load_manifest(self.ctx)[0]
        item["title"] = "Celebration 🎉"
        archiver._save_manifest(self.ctx, [item])
        captured = {}

        def put(url, data, headers, timeout, allow_redirects):
            prepared = requests.Request("PUT", url, data=data, headers=headers).prepare()
            captured.update(
                headers=prepared.headers,
                url=url,
                body=data.read(),
                redirects=allow_redirects,
            )
            return SimpleNamespace(status_code=200, text="OK")

        with patch.object(archiver.requests, "put", side_effect=put):
            ok, url = archiver._execute_ia_upload(self.ctx, item["id"])
        self.assertTrue(ok)
        self.assertEqual(captured["body"], Path(item["filepath"]).read_bytes())
        self.assertEqual(int(captured["headers"]["Content-Length"]), len(captured["body"]))
        self.assertNotIn("Transfer-Encoding", captured["headers"])
        self.assertFalse(captured["redirects"])
        captured["headers"]["x-archive-meta-title"].encode("latin-1")
        self.assertIn("archive.org/download/", url)

    def test_credential_test_does_not_claim_authentication(self):
        with patch.object(archiver.requests, "get", return_value=SimpleNamespace(status_code=200)):
            result = archiver.on_action(
                self.ctx,
                "test_ia_credentials",
                {"archive_org_access_key": "a", "archive_org_secret_key": "b"},
            )
        self.assertTrue(result["success"])
        self.assertIn("cannot verify", result["message"])
        with patch.object(archiver.requests, "get", return_value=SimpleNamespace(status_code=500)):
            result = archiver.on_action(
                self.ctx,
                "test_ia_credentials",
                {"archive_org_access_key": "a", "archive_org_secret_key": "b"},
            )
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()

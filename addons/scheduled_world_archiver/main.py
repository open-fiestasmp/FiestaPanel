import os
import re
import json
import time
import datetime
import threading
import hashlib
import zipfile
import urllib.request
import urllib.error
import urllib.parse
import ssl
import logging
from typing import Dict, Any, List, Optional

import requests
try:
    import boto3
    from botocore.client import Config as BotoConfig
except ImportError:
    boto3 = None
    BotoConfig = None

logger = logging.getLogger("fiestapanel.addon.scheduled_world_archiver")

# Global in-memory task tracker
_TASK_LOCK = threading.Lock()
_CURRENT_TASK = {
    "task_id": None,
    "type": "idle",
    "status": "idle",
    "stage": "",
    "progress": 0,
    "total_bytes": 0,
    "processed_bytes": 0,
    "start_time": 0,
    "error": None,
    "cancelled": False,
    "archive_id": None
}

_SCHEDULER_RUNNING = False
_SCHEDULER_THREAD = None
_LAST_RECURRING_CHECK = 0


def _get_archives_dir(ctx) -> str:
    """Returns the dedicated directory for storing world archives."""
    base_backups = "/home/ubuntu/backups"
    if hasattr(ctx, "manager") and ctx.manager and hasattr(ctx.manager, "server_dir"):
        parent = os.path.dirname(os.path.realpath(ctx.manager.server_dir))
        candidate = os.path.join(parent, "backups")
        if os.path.isdir(candidate):
            base_backups = candidate

    ep_dir = os.path.join(base_backups, "episodes")
    os.makedirs(ep_dir, exist_ok=True)
    return ep_dir


def _get_manifest_path(ctx) -> str:
    """Returns the absolute path to the archives manifest file."""
    ep_dir = _get_archives_dir(ctx)
    return os.path.join(ep_dir, "episodes_manifest.json")


def _load_manifest(ctx) -> List[Dict[str, Any]]:
    """Loads all archived entries from manifest."""
    manifest_path = _get_manifest_path(ctx)
    if not os.path.isfile(manifest_path):
        return []
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception as e:
        logger.error(f"Failed to load archives manifest: {e}")
        return []


def _save_manifest(ctx, episodes: List[Dict[str, Any]]) -> bool:
    """Saves the archives manifest atomically."""
    manifest_path = _get_manifest_path(ctx)
    temp_path = manifest_path + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(episodes, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, manifest_path)
        return True
    except Exception as e:
        logger.error(f"Failed to save archives manifest: {e}")
        return False


def _format_bytes(bytes_count: int) -> str:
    """Formats raw bytes into human readable string."""
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_count / (1024 * 1024 * 1024):.2f} GB"


def _send_discord_announcement(webhook_url: str, title: str, description: str, fields: List[Dict[str, Any]] = None, color: int = 0x38bdf8):
    """Sends rich release announcement embed to Discord."""
    if not webhook_url or not webhook_url.startswith("http"):
        return
    payload = {
        "username": "FiestaSMP World Archiver",
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "fields": fields or [],
                "footer": {"text": "FiestaPanel Scheduled World Archiver • Multi-Cloud Sync"},
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
        ]
    }
    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "FiestaPanel-WorldArchiver/1.1"}
        )
        ctx_ssl = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=8, context=ctx_ssl) as resp:
            pass
    except Exception as e:
        logger.error(f"Error sending Discord announcement: {e}")


def _load_events_from_portal(ctx) -> List[Dict[str, Any]]:
    """Loads scheduled events from FiestaPanel events.json and Google Calendar feed."""
    events = []
    base_dir = "/home/ubuntu/fiesta-portal"
    if hasattr(ctx, "manager") and ctx.manager and hasattr(ctx.manager, "base_dir"):
        base_dir = ctx.manager.base_dir

    events_file = os.path.join(base_dir, "events.json")
    if os.path.isfile(events_file):
        try:
            with open(events_file, "r", encoding="utf-8") as f:
                raw_events = json.load(f)
                if isinstance(raw_events, list):
                    for evt in raw_events:
                        events.append({
                            "id": evt.get("id"),
                            "title": evt.get("title") or "Unnamed Event",
                            "start_time": evt.get("start_time") or "",
                            "status": evt.get("status") or "scheduled",
                            "source": "Event Manager"
                        })
        except Exception as e:
            logger.error(f"Failed to read events.json: {e}")

    settings = ctx.get_settings()
    gcal_url = (settings.get("google_calendar_feed_url") or "").strip()
    if gcal_url and gcal_url.startswith("http"):
        try:
            r = requests.get(gcal_url, timeout=5)
            if r.status_code == 200:
                gcal_events = _parse_ics_events(r.text)
                events.extend(gcal_events)
        except Exception as e:
            logger.warning(f"Error fetching Google Calendar feed: {e}")

    events.sort(key=lambda x: x.get("start_time") or "", reverse=False)
    return events


def _parse_ics_events(ics_text: str) -> List[Dict[str, Any]]:
    """Extracts summary and start times from raw ICS calendar data."""
    parsed = []
    current_evt = None
    for line in ics_text.splitlines():
        line = line.strip()
        if line == "BEGIN:VEVENT":
            current_evt = {}
        elif line == "END:VEVENT" and current_evt:
            summary = current_evt.get("summary")
            dtstart = current_evt.get("dtstart")
            if summary and dtstart:
                iso_time = ""
                m = re.match(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2}))?", dtstart)
                if m:
                    y, mo, d, h, mi, s = m.groups()
                    h = h or "00"
                    mi = mi or "00"
                    iso_time = f"{y}-{mo}-{d}T{h}:{mi}"
                parsed.append({
                    "id": f"gcal_{len(parsed)}_{dtstart}",
                    "title": summary,
                    "start_time": iso_time,
                    "status": "scheduled",
                    "source": "Google Calendar"
                })
            current_evt = None
        elif current_evt is not None:
            if line.startswith("SUMMARY:"):
                current_evt["summary"] = line[len("SUMMARY:"):].strip()
            elif line.startswith("DTSTART"):
                parts = line.split(":")
                if len(parts) >= 2:
                    current_evt["dtstart"] = parts[-1].strip()
    return parsed


# ── Standing Background Scheduler Loop ─────────────────────────────────────

def _release_scheduler_loop(ctx):
    """Background monitoring loop that auto-releases scheduled downloads and triggers recurring snapshots."""
    global _LAST_RECURRING_CHECK
    logger.info("Scheduled World Archiver: Release & recurring scheduler loop started.")
    while True:
        try:
            time.sleep(15)
            now = datetime.datetime.now()
            settings = ctx.get_settings()
            manifest = _load_manifest(ctx)
            modified = False

            # 1. Check one-time scheduled releases
            for ep in manifest:
                if ep.get("status") == "scheduled":
                    release_time_str = ep.get("release_time")
                    if release_time_str:
                        try:
                            rel_dt = datetime.datetime.fromisoformat(release_time_str)
                            if now >= rel_dt:
                                ep["status"] = "released"
                                ep["released_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                                modified = True
                                logger.info(f"Auto-releasing world archive: {ep.get('title')} ({ep.get('id')})!")

                                # Dispatch in-game tellraw announcement
                                if settings.get("broadcast_on_release", True):
                                    ep_title = ep.get("title", "World Snapshot")
                                    dl_url = ep.get("cloud_download_url") or ep.get("ia_download_url") or ep.get("github_download_url") or ep.get("local_download_url") or "https://archive.org"
                                    type_label = ep.get("type_label") or ("Before Recording" if ep.get("type") == "pre" else "World Download")

                                    chat_json = json.dumps([
                                        "",
                                        {"text": "[FiestaSMP] ", "color": "gold", "bold": True},
                                        {"text": f"{ep_title} ", "color": "yellow", "bold": True},
                                        {"text": f"({type_label}) is now RELEASED! ", "color": "white"},
                                        {
                                            "text": "[Click to Download]",
                                            "color": "aqua",
                                            "bold": True,
                                            "underlined": True,
                                            "clickEvent": {"action": "open_url", "value": dl_url},
                                            "hoverEvent": {"action": "show_text", "value": f"Download {ep_title} World Zip ({ep.get('filesize_formatted', '')})"}
                                        }
                                    ])
                                    ctx.send_command(f"tellraw @a {chat_json}")

                                # Dispatch Discord announcement
                                discord_url = (settings.get("discord_webhook_url") or "").strip()
                                if discord_url:
                                    ep_title = ep.get("title", "World Snapshot")
                                    dl_url = ep.get("cloud_download_url") or ep.get("ia_download_url") or ep.get("github_download_url") or ep.get("local_download_url") or "https://archive.org"
                                    type_label = ep.get("type_label") or "World Download"
                                    _send_discord_announcement(
                                        discord_url,
                                        f"[FiestaSMP] World Download Released: {ep_title}",
                                        f"The official world archive for **{ep_title}** ({type_label}) has reached its scheduled release time and is now publicly available for download!",
                                        [
                                            {"name": "Snapshot Title", "value": ep_title, "inline": True},
                                            {"name": "Category", "value": ep.get("purpose", "world_archive").capitalize(), "inline": True},
                                            {"name": "Archive Size", "value": ep.get("filesize_formatted", "N/A"), "inline": True},
                                            {"name": "Download Link", "value": f"[Download World Archive Zip]({dl_url})", "inline": False}
                                        ],
                                        color=0x22c55e
                                    )
                        except Exception as parse_err:
                            logger.error(f"Error parsing release_time for {ep.get('id')}: {parse_err}")

            if modified:
                _save_manifest(ctx, manifest)

            # 2. Check recurring automated snapshot schedule
            recur_interval = settings.get("recurring_snapshot_interval", "disabled")
            if recur_interval and recur_interval != "disabled":
                interval_seconds = 0
                if recur_interval == "6h": interval_seconds = 6 * 3600
                elif recur_interval == "12h": interval_seconds = 12 * 3600
                elif recur_interval == "24h": interval_seconds = 24 * 3600
                elif recur_interval == "7d": interval_seconds = 7 * 86400

                now_ts = time.time()
                if interval_seconds > 0:
                    # Find last snapshot time
                    last_snap_time = 0
                    for ep in manifest:
                        cat_time = ep.get("created_at_ts") or 0
                        if cat_time > last_snap_time:
                            last_snap_time = cat_time

                    if now_ts - last_snap_time >= interval_seconds and (now_ts - _LAST_RECURRING_CHECK >= 300):
                        _LAST_RECURRING_CHECK = now_ts
                        with _TASK_LOCK:
                            is_running = (_CURRENT_TASK["status"] == "running")
                        if not is_running:
                            logger.info(f"Triggering scheduled recurring world snapshot (interval: {recur_interval})...")
                            snap_title = f"Scheduled Automated Backup ({now.strftime('%b %d, %Y')})"
                            t = threading.Thread(
                                target=_execute_archive_task,
                                args=(
                                    ctx,
                                    0,
                                    snap_title,
                                    "snapshot",
                                    "release",
                                    "",
                                    "",
                                    {"overworld": True, "nether": True, "the_end": True},
                                    settings.get("auto_upload_cloud", False),
                                    settings.get("default_cloud_target", "archive_org"),
                                    f"Automated recurring world snapshot ({recur_interval} interval)."
                                ),
                                daemon=True
                            )
                            t.start()
        except Exception as e:
            logger.error(f"Error in release scheduler loop: {e}")


# ── Archive Creation Worker ────────────────────────────────────────────────

def _execute_archive_task(
    ctx,
    ep_num: int,
    title: str,
    snap_type: str,
    purpose: str,
    linked_event_id: str,
    release_time: str,
    dimensions: Dict[str, bool],
    auto_upload: bool,
    upload_target: str,
    notes: str
):
    """Worker thread that flushes Minecraft data, compresses world folders, and saves manifest entry."""
    global _CURRENT_TASK
    task_id = _CURRENT_TASK["task_id"]

    try:
        # Step 1: Flush server world
        with _TASK_LOCK:
            _CURRENT_TASK["stage"] = "Flushing Minecraft chunk cache to disk (save-all flush)..."
            _CURRENT_TASK["progress"] = 5

        try:
            ctx.send_command("save-all flush")
            time.sleep(2)
        except Exception as cmd_err:
            logger.warning(f"save-all flush command issue: {cmd_err}")

        server_dir = "/home/ubuntu/fiesta"
        if hasattr(ctx, "manager") and ctx.manager and hasattr(ctx.manager, "server_dir"):
            server_dir = ctx.manager.server_dir

        include_folders = []
        if dimensions.get("overworld", True) and os.path.isdir(os.path.join(server_dir, "world")):
            include_folders.append(("world", os.path.join(server_dir, "world")))
        if dimensions.get("nether", True) and os.path.isdir(os.path.join(server_dir, "world_nether")):
            include_folders.append(("world_nether", os.path.join(server_dir, "world_nether")))
        if dimensions.get("the_end", True) and os.path.isdir(os.path.join(server_dir, "world_the_end")):
            include_folders.append(("world_the_end", os.path.join(server_dir, "world_the_end")))

        if not include_folders:
            world_base = os.path.join(server_dir, "world")
            if os.path.isdir(world_base):
                include_folders.append(("world", world_base))
            else:
                raise ValueError(f"No Minecraft world folders detected in {server_dir}.")

        # Step 2: Scan files and count total uncompressed bytes
        with _TASK_LOCK:
            _CURRENT_TASK["stage"] = "Scanning world region files and calculating size..."
            _CURRENT_TASK["progress"] = 10

        files_to_pack = []
        total_bytes = 0
        skip_patterns = {".lock", ".tmp", ".git", ".tar.gz", ".zip", ".tar.zst"}

        for arc_prefix, folder_path in include_folders:
            for root, dirs, files in os.walk(folder_path):
                if _CURRENT_TASK["cancelled"]:
                    raise InterruptedError("Archive creation cancelled by user.")
                for fn in files:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in skip_patterns or fn.startswith("session.lock") or fn.startswith("uid.dat"):
                        continue
                    full_p = os.path.join(root, fn)
                    rel_p = os.path.relpath(full_p, server_dir)
                    try:
                        sz = os.path.getsize(full_p)
                        files_to_pack.append((full_p, rel_p, sz))
                        total_bytes += sz
                    except OSError:
                        pass

        with _TASK_LOCK:
            _CURRENT_TASK["total_bytes"] = total_bytes
            _CURRENT_TASK["processed_bytes"] = 0
            _CURRENT_TASK["stage"] = f"Compressing {len(files_to_pack)} world files ({_format_bytes(total_bytes)})..."
            _CURRENT_TASK["progress"] = 15

        # Step 3: Zip files into destination
        ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_type = snap_type if snap_type in ("pre", "post", "snapshot") else "snapshot"
        safe_purpose = re.sub(r"[^a-zA-Z0-9_-]", "", purpose or "world").lower()

        if purpose == "episode" and ep_num > 0:
            filename = f"fiestasmp_ep{ep_num:02d}_{safe_type}_{ts_str}.zip"
            archive_id = f"ep{ep_num:02d}_{safe_type}_{ts_str}"
        else:
            filename = f"fiestasmp_{safe_purpose}_{safe_type}_{ts_str}.zip"
            archive_id = f"{safe_purpose}_{safe_type}_{ts_str}"

        archives_dir = _get_archives_dir(ctx)
        target_zip = os.path.join(archives_dir, filename)

        processed = 0
        last_progress_update = time.time()

        with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for full_p, arc_name, sz in files_to_pack:
                if _CURRENT_TASK["cancelled"]:
                    zf.close()
                    try:
                        os.remove(target_zip)
                    except OSError:
                        pass
                    raise InterruptedError("Archive creation cancelled by user.")

                try:
                    zf.write(full_p, arcname=arc_name)
                except Exception as write_err:
                    logger.warning(f"Could not pack {full_p}: {write_err}")

                processed += sz
                now_t = time.time()
                if now_t - last_progress_update >= 0.8:
                    last_progress_update = now_t
                    pct = 15 + int((processed / max(total_bytes, 1)) * 75)
                    with _TASK_LOCK:
                        _CURRENT_TASK["processed_bytes"] = processed
                        _CURRENT_TASK["progress"] = min(pct, 90)
                        _CURRENT_TASK["stage"] = f"Zipping world ({_format_bytes(processed)} / {_format_bytes(total_bytes)})..."

        # Step 4: Verify archive size and calculate SHA256
        with _TASK_LOCK:
            _CURRENT_TASK["stage"] = "Finalizing archive package and generating SHA256 hash..."
            _CURRENT_TASK["progress"] = 92

        final_size = os.path.getsize(target_zip)
        sha256_hash = hashlib.sha256()
        with open(target_zip, "rb") as f:
            for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
                sha256_hash.update(chunk)
        digest = sha256_hash.hexdigest()

        # Step 5: Register archive in manifest
        type_labels = {
            "pre": "Pre-Event (Before)",
            "post": "Post-Event (After)",
            "snapshot": "Complete Snapshot"
        }
        type_label = type_labels.get(safe_type, "World Snapshot")

        entry_title = title.strip() if title else (f"Episode {ep_num}" if purpose == "episode" else f"{purpose.capitalize()} Snapshot")

        ep_entry = {
            "id": archive_id,
            "purpose": purpose or "custom",
            "episode_num": ep_num if purpose == "episode" else 0,
            "title": entry_title,
            "type": safe_type,
            "type_label": type_label,
            "filename": filename,
            "filepath": target_zip,
            "filesize": final_size,
            "filesize_formatted": _format_bytes(final_size),
            "sha256": digest,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "created_at_ts": time.time(),
            "dimensions": dimensions,
            "linked_event_id": linked_event_id or "",
            "release_time": release_time or "",
            "status": "scheduled" if release_time else "ready",
            "cloud_provider": "",
            "cloud_download_url": "",
            "cloud_details_url": "",
            "ia_item_id": "",
            "ia_download_url": "",
            "ia_details_url": "",
            "github_release_url": "",
            "github_download_url": "",
            "s3_download_url": "",
            "local_download_url": f"/api/panel-addons/scheduled_world_archiver/download/{filename}",
            "notes": notes or ""
        }

        manifest = _load_manifest(ctx)
        manifest.insert(0, ep_entry)
        _save_manifest(ctx, manifest)

        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "completed"
            _CURRENT_TASK["progress"] = 100
            _CURRENT_TASK["stage"] = f"Archive successfully created! ({_format_bytes(final_size)})"
            _CURRENT_TASK["archive_id"] = archive_id

        # Trigger auto-upload if requested
        settings = ctx.get_settings()
        should_upload = auto_upload or settings.get("auto_upload_cloud", False)
        target_dest = upload_target or settings.get("default_cloud_target", "archive_org")

        if should_upload and target_dest != "local":
            logger.info(f"Direct cloud upload starting for archive {archive_id} to destination '{target_dest}'...")
            _dispatch_cloud_upload(ctx, archive_id, target_dest)

    except InterruptedError:
        logger.info("Snapshot archive task was cancelled.")
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "cancelled"
            _CURRENT_TASK["stage"] = "Operation cancelled."
    except Exception as e:
        logger.error(f"Snapshot creation failed: {e}", exc_info=True)
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = str(e)
            _CURRENT_TASK["stage"] = f"Error: {e}"


# ── Cloud Upload Dispatcher & Providers ────────────────────────────────────

def _dispatch_cloud_upload(ctx, archive_id: str, target: str):
    """Dispatches upload task to the selected cloud destination."""
    if target == "github":
        _execute_github_upload(ctx, archive_id)
    elif target == "s3":
        _execute_s3_upload(ctx, archive_id)
    elif target == "custom_post":
        _execute_custom_post_upload(ctx, archive_id)
    else:  # archive_org default
        _execute_ia_upload(ctx, archive_id)


# Progress File Wrapper for requests streaming uploads
class ProgressFileWrapper:
    def __init__(self, file_obj, total_size, task_label="Uploading"):
        self.file_obj = file_obj
        self.total_size = total_size
        self.uploaded = 0
        self.task_label = task_label
        self.last_update = time.time()

    def read(self, size=-1):
        if _CURRENT_TASK["cancelled"]:
            raise InterruptedError("Upload cancelled by user.")
        chunk = self.file_obj.read(size)
        if chunk:
            self.uploaded += len(chunk)
            now_t = time.time()
            if now_t - self.last_update >= 0.8:
                self.last_update = now_t
                pct = int((self.uploaded / max(self.total_size, 1)) * 95)
                elapsed = max(now_t - _CURRENT_TASK["start_time"], 0.1)
                speed = self.uploaded / elapsed
                with _TASK_LOCK:
                    _CURRENT_TASK["processed_bytes"] = self.uploaded
                    _CURRENT_TASK["progress"] = pct
                    _CURRENT_TASK["stage"] = f"{self.task_label} ({_format_bytes(self.uploaded)} / {_format_bytes(self.total_size)} • {_format_bytes(int(speed))}/s)..."
        return chunk


def _execute_github_upload(ctx, archive_id: str):
    """Uploads an archive zip file as an asset to a GitHub Release."""
    global _CURRENT_TASK
    manifest = _load_manifest(ctx)
    entry = next((e for e in manifest if e.get("id") == archive_id), None)
    if not entry:
        logger.error(f"Archive entry {archive_id} not found in manifest.")
        return

    filepath = entry.get("filepath")
    if not filepath or not os.path.isfile(filepath):
        archives_dir = _get_archives_dir(ctx)
        filepath = os.path.join(archives_dir, entry.get("filename", ""))

    if not os.path.isfile(filepath):
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = "Local zip file not found on disk."
        return

    settings = ctx.get_settings()
    token = (settings.get("github_token") or "").strip()
    repo = (settings.get("github_repo") or "").strip()
    tag_prefix = (settings.get("github_tag_prefix") or "v").strip()

    if not token or not repo or "/" not in repo:
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = "GitHub Personal Access Token and valid repository (owner/repo) are required in settings."
        return

    filename = entry.get("filename")
    filesize = os.path.getsize(filepath)
    title = entry.get("title") or "FiestaSMP World Archive"
    clean_id = re.sub(r"[^a-zA-Z0-9._-]", "", archive_id).lower()
    tag_name = f"{tag_prefix}{clean_id}"

    with _TASK_LOCK:
        _CURRENT_TASK["task_id"] = f"gh_{archive_id}_{int(time.time())}"
        _CURRENT_TASK["type"] = "upload"
        _CURRENT_TASK["status"] = "running"
        _CURRENT_TASK["stage"] = f"Connecting to GitHub API for {repo}..."
        _CURRENT_TASK["progress"] = 5
        _CURRENT_TASK["total_bytes"] = filesize
        _CURRENT_TASK["processed_bytes"] = 0
        _CURRENT_TASK["start_time"] = time.time()
        _CURRENT_TASK["archive_id"] = archive_id
        _CURRENT_TASK["cancelled"] = False

    try:
        api_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "FiestaPanel-WorldArchiver/1.1"
        }

        # Step A: Check if release for tag exists or create one
        get_rel_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag_name}"
        rel_resp = requests.get(get_rel_url, headers=api_headers, timeout=12)
        release_obj = None

        if rel_resp.status_code == 200:
            release_obj = rel_resp.json()
        elif rel_resp.status_code == 404:
            # Create new release
            create_rel_url = f"https://api.github.com/repos/{repo}/releases"
            rel_payload = {
                "tag_name": tag_name,
                "name": f"FiestaSMP: {title}",
                "body": f"Official Minecraft server world archive for **{title}**.\n\n- File: `{filename}`\n- Size: {_format_bytes(filesize)}\n- SHA256: `{entry.get('sha256', '')}`\n\n*Generated by FiestaPanel Scheduled World Archiver.*",
                "draft": False,
                "prerelease": False
            }
            c_resp = requests.post(create_rel_url, headers=api_headers, json=rel_payload, timeout=15)
            if c_resp.status_code in (200, 201):
                release_obj = c_resp.json()
            else:
                raise RuntimeError(f"Failed to create GitHub release (HTTP {c_resp.status_code}): {c_resp.text[:200]}")
        else:
            raise RuntimeError(f"GitHub API error (HTTP {rel_resp.status_code}): {rel_resp.text[:200]}")

        release_id = release_obj.get("id")
        release_html_url = release_obj.get("html_url")

        # Step B: If asset already exists on this release, delete it first
        for existing_asset in release_obj.get("assets", []):
            if existing_asset.get("name") == filename:
                del_url = f"https://api.github.com/repos/{repo}/releases/assets/{existing_asset.get('id')}"
                requests.delete(del_url, headers=api_headers, timeout=10)
                break

        # Step C: Stream asset upload
        with _TASK_LOCK:
            _CURRENT_TASK["stage"] = f"Uploading {filename} to GitHub Releases..."
            _CURRENT_TASK["progress"] = 10

        upload_url = f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name={urllib.parse.quote(filename)}"
        upload_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/zip",
            "Content-Length": str(filesize),
            "User-Agent": "FiestaPanel-WorldArchiver/1.1"
        }

        with open(filepath, "rb") as f_obj:
            wrapper = ProgressFileWrapper(f_obj, filesize, task_label="Uploading to GitHub Releases")
            up_resp = requests.post(upload_url, data=wrapper, headers=upload_headers, timeout=900)

        if up_resp.status_code in (200, 201):
            asset_data = up_resp.json()
            dl_url = asset_data.get("browser_download_url") or f"https://github.com/{repo}/releases/download/{tag_name}/{filename}"

            manifest = _load_manifest(ctx)
            for m in manifest:
                if m.get("id") == archive_id:
                    m["cloud_provider"] = "github"
                    m["cloud_download_url"] = dl_url
                    m["cloud_details_url"] = release_html_url
                    m["github_release_url"] = release_html_url
                    m["github_download_url"] = dl_url
                    m["uploaded_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    break
            _save_manifest(ctx, manifest)

            with _TASK_LOCK:
                _CURRENT_TASK["status"] = "completed"
                _CURRENT_TASK["progress"] = 100
                _CURRENT_TASK["stage"] = "Upload to GitHub Releases succeeded!"
            logger.info(f"Archive {archive_id} uploaded to GitHub Releases: {dl_url}")
        else:
            err_msg = f"GitHub upload returned HTTP {up_resp.status_code}: {up_resp.text[:200]}"
            with _TASK_LOCK:
                _CURRENT_TASK["status"] = "error"
                _CURRENT_TASK["error"] = err_msg
            logger.error(err_msg)

    except InterruptedError:
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "cancelled"
            _CURRENT_TASK["stage"] = "GitHub upload cancelled by user."
    except Exception as e:
        logger.error(f"GitHub upload failed: {e}", exc_info=True)
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = str(e)


def _execute_s3_upload(ctx, archive_id: str):
    """Uploads an archive zip file to Custom S3 / Cloudflare R2 / AWS S3."""
    global _CURRENT_TASK
    manifest = _load_manifest(ctx)
    entry = next((e for e in manifest if e.get("id") == archive_id), None)
    if not entry:
        logger.error(f"Archive entry {archive_id} not found in manifest.")
        return

    filepath = entry.get("filepath")
    if not filepath or not os.path.isfile(filepath):
        archives_dir = _get_archives_dir(ctx)
        filepath = os.path.join(archives_dir, entry.get("filename", ""))

    if not os.path.isfile(filepath):
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = "Local zip file not found on disk."
        return

    settings = ctx.get_settings()
    endpoint = (settings.get("s3_endpoint_url") or "").strip()
    bucket = (settings.get("s3_bucket_name") or "").strip()
    access_key = (settings.get("s3_access_key") or "").strip()
    secret_key = (settings.get("s3_secret_key") or "").strip()
    region = (settings.get("s3_region") or "auto").strip()
    public_base = (settings.get("s3_public_base_url") or "").strip()

    if not bucket or not access_key or not secret_key:
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = "S3 Bucket Name, Access Key, and Secret Key are required in settings."
        return

    filename = entry.get("filename")
    filesize = os.path.getsize(filepath)

    with _TASK_LOCK:
        _CURRENT_TASK["task_id"] = f"s3_{archive_id}_{int(time.time())}"
        _CURRENT_TASK["type"] = "upload"
        _CURRENT_TASK["status"] = "running"
        _CURRENT_TASK["stage"] = f"Connecting to S3/Cloud Storage for {bucket}..."
        _CURRENT_TASK["progress"] = 5
        _CURRENT_TASK["total_bytes"] = filesize
        _CURRENT_TASK["processed_bytes"] = 0
        _CURRENT_TASK["start_time"] = time.time()
        _CURRENT_TASK["archive_id"] = archive_id
        _CURRENT_TASK["cancelled"] = False

    try:
        if not boto3:
            raise RuntimeError("boto3 Python package is required for custom S3 uploads.")

        s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region or "us-east-1",
            config=BotoConfig(signature_version="s3v4") if BotoConfig else None
        )

        class S3ProgressCallback:
            def __init__(self, total_size):
                self.total_size = total_size
                self.uploaded = 0
                self.last_update = time.time()

            def __call__(self, bytes_transferred):
                if _CURRENT_TASK["cancelled"]:
                    raise InterruptedError("S3 upload cancelled by user.")
                self.uploaded += bytes_transferred
                now_t = time.time()
                if now_t - self.last_update >= 0.8:
                    self.last_update = now_t
                    pct = int((self.uploaded / max(self.total_size, 1)) * 95)
                    elapsed = max(now_t - _CURRENT_TASK["start_time"], 0.1)
                    speed = self.uploaded / elapsed
                    with _TASK_LOCK:
                        _CURRENT_TASK["processed_bytes"] = self.uploaded
                        _CURRENT_TASK["progress"] = pct
                        _CURRENT_TASK["stage"] = f"Uploading to S3/R2 ({_format_bytes(self.uploaded)} / {_format_bytes(self.total_size)} • {_format_bytes(int(speed))}/s)..."

        object_key = f"worlds/{filename}"
        s3_client.upload_file(filepath, bucket, object_key, Callback=S3ProgressCallback(filesize))

        # Build download URL
        if public_base:
            dl_url = f"{public_base.rstrip('/')}/{object_key}"
        elif endpoint and "amazonaws.com" not in endpoint:
            dl_url = f"{endpoint.rstrip('/')}/{bucket}/{object_key}"
        else:
            dl_url = f"https://{bucket}.s3.{region}.amazonaws.com/{object_key}"

        manifest = _load_manifest(ctx)
        for m in manifest:
            if m.get("id") == archive_id:
                m["cloud_provider"] = "s3"
                m["cloud_download_url"] = dl_url
                m["cloud_details_url"] = dl_url
                m["s3_download_url"] = dl_url
                m["uploaded_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break
        _save_manifest(ctx, manifest)

        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "completed"
            _CURRENT_TASK["progress"] = 100
            _CURRENT_TASK["stage"] = "Upload to S3/Cloud storage succeeded!"
        logger.info(f"Archive {archive_id} uploaded to S3: {dl_url}")

    except InterruptedError:
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "cancelled"
            _CURRENT_TASK["stage"] = "S3 upload cancelled by user."
    except Exception as e:
        logger.error(f"S3 upload failed: {e}", exc_info=True)
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = str(e)


def _execute_ia_upload(ctx, archive_id: str):
    """Worker thread that uploads an archive zip to Internet Archive (archive.org) S3 endpoint."""
    global _CURRENT_TASK
    manifest = _load_manifest(ctx)
    entry = next((e for e in manifest if e.get("id") == archive_id), None)
    if not entry:
        logger.error(f"Archive entry {archive_id} not found in manifest.")
        return

    filepath = entry.get("filepath")
    if not filepath or not os.path.isfile(filepath):
        archives_dir = _get_archives_dir(ctx)
        filepath = os.path.join(archives_dir, entry.get("filename", ""))

    if not os.path.isfile(filepath):
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = "Local zip file not found on disk."
        return

    settings = ctx.get_settings()
    access_key = (settings.get("archive_org_access_key") or "").strip()
    secret_key = (settings.get("archive_org_secret_key") or "").strip()
    collection = (settings.get("archive_org_collection") or "opensource").strip()
    item_prefix = (settings.get("item_prefix") or "fiestasmp_world_").strip()

    if not access_key or not secret_key:
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = "Internet Archive S3 Access Key and Secret Key are required in settings."
        return

    ep_num = entry.get("episode_num", 0)
    snap_type = entry.get("type", "snapshot")
    if ep_num > 0:
        item_id = f"{item_prefix}ep{ep_num:02d}_{snap_type}".lower()
    else:
        clean_entry_id = re.sub(r"[^a-z0-9_-]", "", entry.get("id", "world")).lower()
        item_id = f"{item_prefix}{clean_entry_id}"
    item_id = re.sub(r"[^a-z0-9_-]", "", item_id)
    filename = entry.get("filename")
    filesize = os.path.getsize(filepath)

    with _TASK_LOCK:
        _CURRENT_TASK["task_id"] = f"upload_{archive_id}_{int(time.time())}"
        _CURRENT_TASK["type"] = "upload"
        _CURRENT_TASK["status"] = "running"
        _CURRENT_TASK["stage"] = f"Initiating Internet Archive upload for {filename}..."
        _CURRENT_TASK["progress"] = 5
        _CURRENT_TASK["total_bytes"] = filesize
        _CURRENT_TASK["processed_bytes"] = 0
        _CURRENT_TASK["start_time"] = time.time()
        _CURRENT_TASK["archive_id"] = archive_id
        _CURRENT_TASK["cancelled"] = False

    try:
        url = f"https://s3.us.archive.org/{item_id}/{filename}"
        title = entry.get("title", "World Snapshot")
        type_str = entry.get("type_label") or ("Pre-Event (Before)" if snap_type == "pre" else "Post-Event (After)")

        headers = {
            "authorization": f"LOW {access_key}:{secret_key}",
            "x-archive-auto-make-bucket": "1",
            "x-archive-meta-title": f"FiestaSMP World Archive - {title} ({type_str})",
            "x-archive-meta-mediatype": "data",
            "x-archive-meta-collection": collection,
            "x-archive-meta-creator": "FiestaSMP",
            "x-archive-meta-description": f"Official Minecraft server world download for FiestaSMP: {title}. Snapshot type: {type_str}.",
            "Content-Type": "application/zip",
            "Content-Length": str(filesize)
        }

        with open(filepath, "rb") as f_obj:
            wrapped = ProgressFileWrapper(f_obj, filesize, task_label="Uploading to archive.org")
            res = requests.put(url, data=wrapped, headers=headers, timeout=600)

        if res.status_code in (200, 201):
            ia_dl = f"https://archive.org/download/{item_id}/{filename}"
            ia_details = f"https://archive.org/details/{item_id}"

            manifest = _load_manifest(ctx)
            for m in manifest:
                if m.get("id") == archive_id:
                    m["cloud_provider"] = "archive_org"
                    m["cloud_download_url"] = ia_dl
                    m["cloud_details_url"] = ia_details
                    m["ia_item_id"] = item_id
                    m["ia_download_url"] = ia_dl
                    m["ia_details_url"] = ia_details
                    m["uploaded_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    break
            _save_manifest(ctx, manifest)

            with _TASK_LOCK:
                _CURRENT_TASK["status"] = "completed"
                _CURRENT_TASK["progress"] = 100
                _CURRENT_TASK["stage"] = "Upload to Internet Archive succeeded!"
            logger.info(f"Archive {archive_id} uploaded to {ia_dl}")
        else:
            err_msg = f"Archive.org returned HTTP {res.status_code}: {res.text[:200]}"
            with _TASK_LOCK:
                _CURRENT_TASK["status"] = "error"
                _CURRENT_TASK["error"] = err_msg
            logger.error(err_msg)

    except InterruptedError:
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "cancelled"
            _CURRENT_TASK["stage"] = "Upload cancelled by user."
    except Exception as e:
        logger.error(f"Internet Archive upload failed: {e}", exc_info=True)
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = str(e)


def _execute_custom_post_upload(ctx, archive_id: str):
    """Uploads an archive zip file to a custom HTTP/Webhook POST endpoint."""
    global _CURRENT_TASK
    manifest = _load_manifest(ctx)
    entry = next((e for e in manifest if e.get("id") == archive_id), None)
    if not entry:
        return

    filepath = entry.get("filepath")
    if not filepath or not os.path.isfile(filepath):
        return

    settings = ctx.get_settings()
    url = (settings.get("custom_post_url") or "").strip()
    auth_h = (settings.get("custom_post_auth_header") or "").strip()

    if not url or not url.startswith("http"):
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = "Valid Custom Upload Endpoint URL is required."
        return

    filename = entry.get("filename")
    filesize = os.path.getsize(filepath)

    with _TASK_LOCK:
        _CURRENT_TASK["task_id"] = f"post_{archive_id}_{int(time.time())}"
        _CURRENT_TASK["type"] = "upload"
        _CURRENT_TASK["status"] = "running"
        _CURRENT_TASK["stage"] = f"Uploading {filename} to custom endpoint..."
        _CURRENT_TASK["progress"] = 5
        _CURRENT_TASK["total_bytes"] = filesize
        _CURRENT_TASK["processed_bytes"] = 0
        _CURRENT_TASK["start_time"] = time.time()
        _CURRENT_TASK["archive_id"] = archive_id
        _CURRENT_TASK["cancelled"] = False

    try:
        headers = {"User-Agent": "FiestaPanel-WorldArchiver/1.1"}
        if auth_h:
            headers["Authorization"] = auth_h

        with open(filepath, "rb") as f_obj:
            files = {"file": (filename, f_obj, "application/zip")}
            r = requests.post(url, files=files, headers=headers, timeout=600)

        if r.status_code in (200, 201, 204):
            dl_url = ""
            try:
                res_data = r.json()
                dl_url = res_data.get("url") or res_data.get("download_url") or ""
            except Exception:
                pass

            manifest = _load_manifest(ctx)
            for m in manifest:
                if m.get("id") == archive_id:
                    m["cloud_provider"] = "custom_post"
                    if dl_url:
                        m["cloud_download_url"] = dl_url
                        m["cloud_details_url"] = dl_url
                    m["uploaded_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    break
            _save_manifest(ctx, manifest)

            with _TASK_LOCK:
                _CURRENT_TASK["status"] = "completed"
                _CURRENT_TASK["progress"] = 100
                _CURRENT_TASK["stage"] = "Upload to custom endpoint succeeded!"
        else:
            with _TASK_LOCK:
                _CURRENT_TASK["status"] = "error"
                _CURRENT_TASK["error"] = f"Custom endpoint returned HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = str(e)


# ── Addon Life-Cycle Entrypoints ──────────────────────────────────────────

def init_addon(ctx):
    """Initializes the Scheduled World Archiver addon and standing scheduler loop."""
    global _SCHEDULER_RUNNING, _SCHEDULER_THREAD
    _get_archives_dir(ctx)

    if not _SCHEDULER_RUNNING:
        _SCHEDULER_RUNNING = True
        _SCHEDULER_THREAD = threading.Thread(target=_release_scheduler_loop, args=(ctx,), daemon=True)
        _SCHEDULER_THREAD.start()

    logger.info("Scheduled World Archiver addon initialized successfully.")


def on_action(ctx, action_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Action dispatcher handling dashboard AJAX requests."""
    global _CURRENT_TASK

    if action_name == "get_status":
        manifest = _load_manifest(ctx)
        calendar_events = _load_events_from_portal(ctx)
        with _TASK_LOCK:
            task_copy = dict(_CURRENT_TASK)

        # Detect highest episode number
        max_ep = 0
        for ep in manifest:
            try:
                num = int(ep.get("episode_num", 0))
                if num > max_ep:
                    max_ep = num
            except (ValueError, TypeError):
                pass

        return {
            "success": True,
            "task": task_copy,
            "archives": manifest,
            "calendar_events": calendar_events,
            "next_episode_num": max_ep + 1,
            "settings": ctx.get_settings()
        }

    elif action_name == "start_snapshot":
        with _TASK_LOCK:
            if _CURRENT_TASK["status"] == "running":
                return {"success": False, "error": "An archiving or upload operation is already running in the background."}

        ep_num = int(payload.get("episode_num", 0))
        purpose = payload.get("purpose", "custom")
        title = (payload.get("title") or ("Episode " + str(ep_num) if purpose == "episode" else f"{purpose.capitalize()} Snapshot")).strip()
        snap_type = payload.get("type", "snapshot")  # "pre", "post", "snapshot"
        linked_event_id = payload.get("linked_event_id", "")
        release_time = payload.get("release_time", "")
        dimensions = payload.get("dimensions", {"overworld": True, "nether": True, "the_end": True})
        auto_upload = bool(payload.get("auto_upload", False))
        upload_target = payload.get("upload_target", "")
        notes = (payload.get("notes") or "").strip()

        with _TASK_LOCK:
            _CURRENT_TASK = {
                "task_id": f"archive_{purpose}_{int(time.time())}",
                "type": "archive",
                "status": "running",
                "stage": "Initializing snapshot...",
                "progress": 0,
                "total_bytes": 0,
                "processed_bytes": 0,
                "start_time": time.time(),
                "error": None,
                "cancelled": False,
                "archive_id": None
            }

        t = threading.Thread(
            target=_execute_archive_task,
            args=(ctx, ep_num, title, snap_type, purpose, linked_event_id, release_time, dimensions, auto_upload, upload_target, notes),
            daemon=True
        )
        t.start()
        return {"success": True, "message": f"Started world archive creation for '{title}'."}

    elif action_name == "upload_archive":
        archive_id = payload.get("archive_id")
        target = payload.get("target") or "default"
        if not archive_id:
            return {"success": False, "error": "Missing archive_id parameter."}

        with _TASK_LOCK:
            if _CURRENT_TASK["status"] == "running":
                return {"success": False, "error": "A task is already actively running. Please wait for it to complete."}

        settings = ctx.get_settings()
        if target == "default":
            target = settings.get("default_cloud_target", "archive_org")

        threading.Thread(target=_dispatch_cloud_upload, args=(ctx, archive_id, target), daemon=True).start()
        return {"success": True, "message": f"Initiated cloud upload to '{target}'."}

    elif action_name == "cancel_task":
        with _TASK_LOCK:
            if _CURRENT_TASK["status"] == "running":
                _CURRENT_TASK["cancelled"] = True
                return {"success": True, "message": "Cancellation signal dispatched."}
        return {"success": False, "error": "No running task to cancel."}

    elif action_name == "release_now":
        archive_id = payload.get("archive_id")
        manifest = _load_manifest(ctx)
        found = False
        target_ep = None
        for m in manifest:
            if m.get("id") == archive_id:
                m["status"] = "released"
                m["released_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                found = True
                target_ep = m
                break

        if not found:
            return {"success": False, "error": "Archive not found."}

        _save_manifest(ctx, manifest)

        # Broadcast in-game
        settings = ctx.get_settings()
        if settings.get("broadcast_on_release", True) and target_ep:
            ep_title = target_ep.get("title", "World Snapshot")
            dl_url = target_ep.get("cloud_download_url") or target_ep.get("ia_download_url") or target_ep.get("github_download_url") or target_ep.get("local_download_url") or "https://archive.org"
            type_label = target_ep.get("type_label") or "World Download"
            chat_json = json.dumps([
                "",
                {"text": "[FiestaSMP] ", "color": "gold", "bold": True},
                {"text": f"{ep_title} ", "color": "yellow", "bold": True},
                {"text": f"({type_label}) is now RELEASED! ", "color": "white"},
                {
                    "text": "[Click to Download]",
                    "color": "aqua",
                    "bold": True,
                    "underlined": True,
                    "clickEvent": {"action": "open_url", "value": dl_url}
                }
            ])
            ctx.send_command(f"tellraw @a {chat_json}")

        return {"success": True, "message": "World download released publicly!"}

    elif action_name == "lock_archive":
        archive_id = payload.get("archive_id")
        manifest = _load_manifest(ctx)
        found = False
        for m in manifest:
            if m.get("id") == archive_id:
                m["status"] = "scheduled" if m.get("release_time") else "ready"
                found = True
                break
        if not found:
            return {"success": False, "error": "Archive not found."}
        _save_manifest(ctx, manifest)
        return {"success": True, "message": "Archive release locked/scheduled."}

    elif action_name == "set_schedule":
        archive_id = payload.get("archive_id")
        release_time = payload.get("release_time", "").strip()
        linked_event_id = payload.get("linked_event_id", "").strip()
        manifest = _load_manifest(ctx)
        found = False
        for m in manifest:
            if m.get("id") == archive_id:
                m["release_time"] = release_time
                m["linked_event_id"] = linked_event_id
                if release_time:
                    m["status"] = "scheduled"
                found = True
                break
        if not found:
            return {"success": False, "error": "Archive not found."}
        _save_manifest(ctx, manifest)
        return {"success": True, "message": "Release schedule updated successfully."}

    elif action_name == "delete_archive":
        archive_id = payload.get("archive_id")
        delete_local_file = bool(payload.get("delete_file", True))
        manifest = _load_manifest(ctx)
        entry = next((e for e in manifest if e.get("id") == archive_id), None)
        if not entry:
            return {"success": False, "error": "Archive record not found."}

        if delete_local_file:
            fp = entry.get("filepath")
            if fp and os.path.isfile(fp):
                try:
                    os.remove(fp)
                except OSError as oe:
                    logger.warning(f"Error removing file {fp}: {oe}")

        new_manifest = [e for e in manifest if e.get("id") != archive_id]
        _save_manifest(ctx, new_manifest)
        return {"success": True, "message": "World archive record removed."}

    elif action_name == "test_ia_credentials":
        ak = (payload.get("archive_org_access_key") or "").strip()
        sk = (payload.get("archive_org_secret_key") or "").strip()
        if not ak or not sk:
            return {"success": False, "error": "Please provide both Archive.org S3 Access Key and Secret Key."}
        try:
            r = requests.get("https://s3.us.archive.org", headers={"authorization": f"LOW {ak}:{sk}"}, timeout=8)
            if r.status_code in (200, 403):
                if r.status_code == 200:
                    return {"success": True, "message": "Archive.org S3 API keys verified successfully!"}
                else:
                    return {"success": False, "error": f"Authentication failed (HTTP 403). Please verify your S3 keys from archive.org/account/s3.php."}
            return {"success": True, "message": f"Connected to Internet Archive S3 endpoint (HTTP {r.status_code})."}
        except Exception as e:
            return {"success": False, "error": f"Failed to connect to Internet Archive: {e}"}

    elif action_name == "test_github_credentials":
        token = (payload.get("github_token") or "").strip()
        repo = (payload.get("github_repo") or "").strip()
        if not token or not repo or "/" not in repo:
            return {"success": False, "error": "Please provide both GitHub Personal Access Token and valid repository (owner/repo)."}
        try:
            r = requests.get(
                f"https://api.github.com/repos/{repo}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "FiestaPanel/1.1"},
                timeout=8
            )
            if r.status_code == 200:
                data = r.json()
                repo_full = data.get("full_name", repo)
                can_push = data.get("permissions", {}).get("push", False)
                perm_text = "Write permission verified" if can_push else "Read permission verified"
                return {"success": True, "message": f"Connected to GitHub repo {repo_full} ({perm_text})!"}
            elif r.status_code == 401:
                return {"success": False, "error": "GitHub authentication failed (HTTP 401). Check your Personal Access Token."}
            elif r.status_code == 404:
                return {"success": False, "error": f"Repository '{repo}' not found or token lacks repo access."}
            else:
                return {"success": False, "error": f"GitHub API error (HTTP {r.status_code}): {r.text[:150]}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to connect to GitHub API: {e}"}

    elif action_name == "test_s3_credentials":
        endpoint = (payload.get("s3_endpoint_url") or "").strip()
        bucket = (payload.get("s3_bucket_name") or "").strip()
        ak = (payload.get("s3_access_key") or "").strip()
        sk = (payload.get("s3_secret_key") or "").strip()
        region = (payload.get("s3_region") or "auto").strip()

        if not bucket or not ak or not sk:
            return {"success": False, "error": "Bucket Name, Access Key ID, and Secret Access Key are required."}
        try:
            if not boto3:
                return {"success": False, "error": "boto3 library is missing on server."}
            s3_c = boto3.client(
                "s3",
                endpoint_url=endpoint or None,
                aws_access_key_id=ak,
                aws_secret_access_key=sk,
                region_name=region or "us-east-1",
                config=BotoConfig(signature_version="s3v4") if BotoConfig else None
            )
            s3_c.head_bucket(Bucket=bucket)
            return {"success": True, "message": f"Connected to S3 bucket '{bucket}' successfully!"}
        except Exception as e:
            return {"success": False, "error": f"S3 connection check failed: {e}"}

    elif action_name == "create_calendar_event":
        title = (payload.get("title") or "").strip()
        start_time = (payload.get("start_time") or "").strip()
        description = (payload.get("description") or "SMP World Download Release").strip()
        if not title or not start_time:
            return {"success": False, "error": "Event title and start time are required."}

        base_dir = "/home/ubuntu/fiesta-portal"
        if hasattr(ctx, "manager") and ctx.manager and hasattr(ctx.manager, "base_dir"):
            base_dir = ctx.manager.base_dir
        events_file = os.path.join(base_dir, "events.json")

        evts = []
        if os.path.isfile(events_file):
            try:
                with open(events_file, "r", encoding="utf-8") as f:
                    evts = json.load(f)
            except Exception:
                evts = []

        new_evt = {
            "id": f"evt_{int(time.time())}",
            "title": title,
            "start_time": start_time,
            "duration": 60,
            "description": description,
            "recurring": "none",
            "commands": "",
            "broadcast": f"✦ Scheduled Event: {title} ✦",
            "status": "scheduled",
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        evts.insert(0, new_evt)
        try:
            with open(events_file, "w", encoding="utf-8") as f:
                json.dump(evts, f, indent=2)
            return {"success": True, "message": f"Scheduled event '{title}' created in Event Manager!", "event": new_evt}
        except Exception as e:
            return {"success": False, "error": f"Failed to save event: {e}"}

    elif action_name == "save_settings":
        settings = payload.get("settings", {})
        ctx.save_settings(settings)
        return {"success": True, "message": "Scheduled World Archiver settings saved!"}

    return {"success": False, "error": f"Unknown action '{action_name}'."}

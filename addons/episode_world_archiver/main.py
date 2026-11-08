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

logger = logging.getLogger("fiestapanel.addon.episode_world_archiver")

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


def _get_archives_dir(ctx) -> str:
    """Returns the dedicated directory for storing episode world archives."""
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
    """Returns the absolute path to the episodes manifest file."""
    ep_dir = _get_archives_dir(ctx)
    return os.path.join(ep_dir, "episodes_manifest.json")


def _load_manifest(ctx) -> List[Dict[str, Any]]:
    """Loads all archived episodes from manifest."""
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
        logger.error(f"Failed to load episodes manifest: {e}")
        return []


def _save_manifest(ctx, episodes: List[Dict[str, Any]]) -> bool:
    """Saves the episodes manifest atomically."""
    manifest_path = _get_manifest_path(ctx)
    temp_path = manifest_path + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(episodes, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, manifest_path)
        return True
    except Exception as e:
        logger.error(f"Failed to save episodes manifest: {e}")
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
        "username": "FiestaSMP Episode Archiver",
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "fields": fields or [],
                "footer": {"text": "FiestaPanel Episode World Archiver • Archive.org Sync"},
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
        ]
    }
    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "FiestaPanel-EpisodeArchiver/1.0"}
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
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

    # Fetch optional Google Calendar ICS feed if configured
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

    # Filter and sort upcoming
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
                # Convert iCal dtstart (e.g. 20261110T180000Z or 20261110)
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


def _release_scheduler_loop(ctx):
    """Background monitoring loop that auto-releases scheduled world downloads."""
    logger.info("Episode World Archiver: Release scheduler loop started.")
    while True:
        try:
            time.sleep(20)
            manifest = _load_manifest(ctx)
            modified = False
            now = datetime.datetime.now()

            for ep in manifest:
                if ep.get("status") == "scheduled":
                    release_time_str = ep.get("release_time")
                    if release_time_str:
                        try:
                            # Handle ISO strings like 2026-11-10T18:00
                            rel_dt = datetime.datetime.fromisoformat(release_time_str)
                            if now >= rel_dt:
                                ep["status"] = "released"
                                ep["released_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                                modified = True
                                logger.info(f"Auto-releasing world download for Episode {ep.get('episode_num')} ({ep.get('title')})!")

                                # Dispatch server broadcast
                                settings = ctx.get_settings()
                                if settings.get("broadcast_on_release", True):
                                    ep_num = ep.get("episode_num", 1)
                                    ep_title = ep.get("title", f"Episode {ep_num}")
                                    dl_url = ep.get("ia_download_url") or ep.get("local_download_url") or "https://archive.org"
                                    type_label = "Before Recording" if ep.get("type") == "pre" else "After Recording"

                                    # Rich Minecraft tellraw command
                                    chat_json = json.dumps([
                                        "",
                                        {"text": "[FiestaSMP] ", "color": "gold", "bold": True},
                                        {"text": f"Episode {ep_num} World Download ", "color": "yellow", "bold": True},
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
                                    ep_num = ep.get("episode_num", 1)
                                    ep_title = ep.get("title", f"Episode {ep_num}")
                                    type_label = "Pre-Episode (Before)" if ep.get("type") == "pre" else "Post-Episode (After)"
                                    dl_url = ep.get("ia_download_url") or ep.get("local_download_url") or "https://archive.org"
                                    _send_discord_announcement(
                                        discord_url,
                                        f"📦 FiestaSMP Episode {ep_num} World Download is LIVE!",
                                        f"The official world archive for **{ep_title}** ({type_label}) has reached its scheduled release time and is now publicly available for download!",
                                        [
                                            {"name": "Episode", "value": f"Episode {ep_num}", "inline": True},
                                            {"name": "Snapshot Type", "value": type_label, "inline": True},
                                            {"name": "Archive Size", "value": ep.get("filesize_formatted", "N/A"), "inline": True},
                                            {"name": "Download Link", "value": f"[Download World Zip from Internet Archive]({dl_url})", "inline": False}
                                        ],
                                        color=0x22c55e
                                    )
                        except Exception as parse_err:
                            logger.error(f"Error parsing release_time for {ep.get('id')}: {parse_err}")

            if modified:
                _save_manifest(ctx, manifest)
        except Exception as e:
            logger.error(f"Error in release scheduler loop: {e}")


def _execute_archive_task(ctx, ep_num: int, title: str, snap_type: str, linked_event_id: str, release_time: str, dimensions: Dict[str, bool], auto_upload: bool, notes: str):
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

        # Identify world folders to include
        include_folders = []
        if dimensions.get("overworld", True) and os.path.isdir(os.path.join(server_dir, "world")):
            include_folders.append(("world", os.path.join(server_dir, "world")))
        if dimensions.get("nether", True) and os.path.isdir(os.path.join(server_dir, "world_nether")):
            include_folders.append(("world_nether", os.path.join(server_dir, "world_nether")))
        if dimensions.get("the_end", True) and os.path.isdir(os.path.join(server_dir, "world_the_end")):
            include_folders.append(("world_the_end", os.path.join(server_dir, "world_the_end")))

        if not include_folders:
            # Fallback to base 'world' directory
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
        safe_type = "pre" if snap_type == "pre" else "post"
        ep_padded = f"{ep_num:02d}"
        filename = f"fiestasmp_ep{ep_padded}_{safe_type}_{ts_str}.zip"
        archives_dir = _get_archives_dir(ctx)
        target_zip = os.path.join(archives_dir, filename)

        processed = 0
        last_progress_update = time.time()

        # Use fast compression (compresslevel=1) so 3-6GB region files compress rapidly with minimal CPU spike
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

        # Step 4: Verify archive size and calculate SHA256 checksum
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
        archive_id = f"ep{ep_padded}_{safe_type}_{ts_str}"
        ep_entry = {
            "id": archive_id,
            "episode_num": ep_num,
            "title": title or f"Episode {ep_num}",
            "type": safe_type,
            "filename": filename,
            "filepath": target_zip,
            "filesize": final_size,
            "filesize_formatted": _format_bytes(final_size),
            "sha256": digest,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "dimensions": dimensions,
            "linked_event_id": linked_event_id or "",
            "release_time": release_time or "",
            "status": "scheduled" if release_time else "ready",
            "ia_item_id": "",
            "ia_download_url": "",
            "ia_details_url": "",
            "local_download_url": f"/api/panel-addons/episode_world_archiver/download/{filename}",
            "notes": notes or ""
        }

        manifest = _load_manifest(ctx)
        # Prepend so newest is at the top
        manifest.insert(0, ep_entry)
        _save_manifest(ctx, manifest)

        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "completed"
            _CURRENT_TASK["progress"] = 100
            _CURRENT_TASK["stage"] = f"Archive successfully created! ({_format_bytes(final_size)})"
            _CURRENT_TASK["archive_id"] = archive_id

        # Trigger auto-upload if requested
        settings = ctx.get_settings()
        ak = (settings.get("archive_org_access_key") or "").strip()
        sk = (settings.get("archive_org_secret_key") or "").strip()
        if (auto_upload or settings.get("auto_upload_ia", False)) and ak and sk:
            logger.info(f"Auto-uploading archive {archive_id} to Internet Archive...")
            threading.Thread(target=_execute_ia_upload, args=(ctx, archive_id), daemon=True).start()

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
    item_prefix = (settings.get("item_prefix") or "fiestasmp_episode_").strip()

    if not access_key or not secret_key:
        with _TASK_LOCK:
            _CURRENT_TASK["status"] = "error"
            _CURRENT_TASK["error"] = "Internet Archive S3 Access Key and Secret Key are required in settings."
        return

    ep_num = entry.get("episode_num", 1)
    snap_type = entry.get("type", "snap")
    # Clean item ID (alphanumeric, dashes, underscores)
    item_id = f"{item_prefix}{ep_num:02d}_{snap_type}".lower()
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
        ep_title = entry.get("title", f"Episode {ep_num}")
        type_str = "Pre-Episode (Before)" if snap_type == "pre" else "Post-Episode (After)"

        headers = {
            "authorization": f"LOW {access_key}:{secret_key}",
            "x-archive-auto-make-bucket": "1",
            "x-archive-meta-title": f"FiestaSMP Episode {ep_num} - {ep_title} ({type_str} World Download)",
            "x-archive-meta-mediatype": "data",
            "x-archive-meta-collection": collection,
            "x-archive-meta-creator": "FiestaSMP",
            "x-archive-meta-description": f"Official Minecraft server world download for FiestaSMP Episode {ep_num} ({ep_title}). Snapshot type: {type_str}.",
            "Content-Type": "application/zip",
            "Content-Length": str(filesize)
        }

        # Streaming upload generator with progress monitoring
        class ProgressFileWrapper:
            def __init__(self, file_obj, total_size):
                self.file_obj = file_obj
                self.total_size = total_size
                self.uploaded = 0
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
                            _CURRENT_TASK["stage"] = f"Uploading to archive.org ({_format_bytes(self.uploaded)} / {_format_bytes(self.total_size)} • {_format_bytes(int(speed))}/s)..."
                return chunk

        with open(filepath, "rb") as f_obj:
            wrapped = ProgressFileWrapper(f_obj, filesize)
            res = requests.put(url, data=wrapped, headers=headers, timeout=600)

        if res.status_code in (200, 201):
            ia_dl = f"https://archive.org/download/{item_id}/{filename}"
            ia_details = f"https://archive.org/details/{item_id}"

            # Update entry in manifest
            manifest = _load_manifest(ctx)
            for m in manifest:
                if m.get("id") == archive_id:
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


# ── Addon Life-Cycle Entrypoints ──────────────────────────────────────────

def init_addon(ctx):
    """Initializes the Episode Archiver addon and standing scheduler loop."""
    global _SCHEDULER_RUNNING, _SCHEDULER_THREAD
    # Ensure local directory exists
    _get_archives_dir(ctx)

    if not _SCHEDULER_RUNNING:
        _SCHEDULER_RUNNING = True
        _SCHEDULER_THREAD = threading.Thread(target=_release_scheduler_loop, args=(ctx,), daemon=True)
        _SCHEDULER_THREAD.start()

    logger.info("Episode World Archiver addon initialized successfully.")


def on_action(ctx, action_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Action dispatcher handling dashboard AJAX requests."""
    global _CURRENT_TASK

    if action_name == "get_status":
        manifest = _load_manifest(ctx)
        calendar_events = _load_events_from_portal(ctx)
        with _TASK_LOCK:
            task_copy = dict(_CURRENT_TASK)

        # Detect highest episode number to recommend next
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

        ep_num = int(payload.get("episode_num", 1))
        title = (payload.get("title") or f"Episode {ep_num}").strip()
        snap_type = payload.get("type", "pre")  # "pre" or "post"
        linked_event_id = payload.get("linked_event_id", "")
        release_time = payload.get("release_time", "")
        dimensions = payload.get("dimensions", {"overworld": True, "nether": True, "the_end": True})
        auto_upload = bool(payload.get("auto_upload", False))
        notes = (payload.get("notes") or "").strip()

        with _TASK_LOCK:
            _CURRENT_TASK = {
                "task_id": f"archive_{ep_num}_{snap_type}_{int(time.time())}",
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
            args=(ctx, ep_num, title, snap_type, linked_event_id, release_time, dimensions, auto_upload, notes),
            daemon=True
        )
        t.start()
        type_str = "Pre-Episode (Before)" if snap_type == "pre" else "Post-Episode (After)"
        return {"success": True, "message": f"Started {type_str} world archive for Episode {ep_num}."}

    elif action_name == "upload_archive":
        archive_id = payload.get("archive_id")
        if not archive_id:
            return {"success": False, "error": "Missing archive_id parameter."}

        with _TASK_LOCK:
            if _CURRENT_TASK["status"] == "running":
                return {"success": False, "error": "A task is already actively running. Please wait for it to complete."}

        threading.Thread(target=_execute_ia_upload, args=(ctx, archive_id), daemon=True).start()
        return {"success": True, "message": "Initiated Internet Archive cloud upload."}

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
            ep_num = target_ep.get("episode_num", 1)
            dl_url = target_ep.get("ia_download_url") or target_ep.get("local_download_url") or "https://archive.org"
            type_label = "Before Recording" if target_ep.get("type") == "pre" else "After Recording"
            chat_json = json.dumps([
                "",
                {"text": "[FiestaSMP] ", "color": "gold", "bold": True},
                {"text": f"Episode {ep_num} World Download ", "color": "yellow", "bold": True},
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
        return {"success": True, "message": "Episode archive record removed."}

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

    elif action_name == "create_calendar_event":
        title = (payload.get("title") or "").strip()
        start_time = (payload.get("start_time") or "").strip()
        description = (payload.get("description") or "SMP Episode World Download Release").strip()
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
        return {"success": True, "message": "Episode World Archiver settings saved!"}

    return {"success": False, "error": f"Unknown action '{action_name}'."}

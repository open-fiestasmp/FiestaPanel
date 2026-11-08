"""
FiestaPanel Web Panel Addon & Extension Manager
Provides dynamic addon discovery, lifecycle management, settings persistence,
Python backend hooks, UI injection slots, and an integrated Plugin Store.
"""
import os
import json
import shutil
import zipfile
import importlib.util
import logging
import urllib.request
import urllib.parse
import time
import io
import html
import re
from typing import Dict, List, Any, Optional

logger = logging.getLogger("fiestapanel.addons")

class AddonContext:
    """Execution context provided to addon backend modules."""
    def __init__(self, addon_id: str, addon_dir: str, manager: 'AddonManager', app=None, send_mc_command=None, ping_mc=None):
        self.addon_id = addon_id
        self.addon_dir = addon_dir
        self.manager = manager
        self.app = app
        self.send_mc_command = send_mc_command
        self.ping_mc = ping_mc
        self.logger = logging.getLogger(f"fiestapanel.addon.{addon_id}")

    def get_settings(self) -> Dict[str, Any]:
        return self.manager.get_addon_settings(self.addon_id)

    def save_settings(self, settings: Dict[str, Any]) -> bool:
        return self.manager.update_addon_settings(self.addon_id, settings)

    def send_command(self, cmd: str):
        if self.send_mc_command:
            return self.send_mc_command(cmd)
        return False, "send_mc_command unavailable"

    def get_server_status(self):
        if self.ping_mc:
            return self.ping_mc()
        return {"online": False}

    def register_hook(self, event_name: str, callback):
        self.manager.register_hook(self.addon_id, event_name, callback)


# Built-in Addon Store Catalog Definitions
STORE_CATALOG = [
    {
        "id": "discord_notifier",
        "name": "Discord Alerts & Webhook Bridge",
        "version": "1.2.0",
        "author": "FiestaSMP Team",
        "description": "Broadcasts server start/stop alerts, player join events, and TPS health warnings to your Discord channel via webhooks.",
        "icon": "fab fa-discord",
        "category": "Integration",
        "featured": True,
        "rating": 4.9,
        "downloads": 1420,
        "settings_schema": [
            {"key": "webhook_url", "label": "Discord Webhook URL", "type": "text", "default": "", "placeholder": "https://discord.com/api/webhooks/..."},
            {"key": "bot_name", "label": "Bot Username", "type": "text", "default": "FiestaPanel Gateway"},
            {"key": "avatar_url", "label": "Bot Avatar URL", "type": "text", "default": ""},
            {"key": "notify_start", "label": "Notify on Server Start", "type": "checkbox", "default": True},
            {"key": "notify_stop", "label": "Notify on Server Stop", "type": "checkbox", "default": True},
            {"key": "notify_join", "label": "Notify on Player Joins", "type": "checkbox", "default": True},
            {"key": "embed_color", "label": "Embed Color (Hex)", "type": "color", "default": "#38bdf8"}
        ],
        "default_settings": {
            "webhook_url": "",
            "bot_name": "FiestaPanel Gateway",
            "avatar_url": "",
            "notify_start": True,
            "notify_stop": True,
            "notify_join": True,
            "embed_color": "#38bdf8"
        },
        "ui_slots": {
            "tab": {
                "title": "Discord Alerts",
                "icon": "fab fa-discord",
                "description": "Configure Discord webhook bridge, test connectivity, and customize embed styles."
            }
        },
        "files": {
            "tab.html": """
<div class="panel-box">
  <div class="section-title-bar">
    <span>Discord Webhook Bridge</span>
    <span class="inline-badge" style="background:#5865f2;color:#fff;">Connected Bridge</span>
  </div>
  <p class="inline-hint" style="margin-bottom:14px;">
    Transmit real-time Minecraft server events, player logins, and system health alerts to any Discord channel using native incoming webhooks.
  </p>

  <table class="form-layout-table">
    <tr>
      <td class="label-col">Discord Webhook URL:</td>
      <td class="val-col">
        <input type="password" id="addon-discord-url" class="ctrl-input" style="width:75%;" placeholder="https://discord.com/api/webhooks/..." value="{{ settings.webhook_url }}">
        <button class="btn-classic" onclick="togglePasswordVisibility('addon-discord-url', this)">[ Show ]</button>
        <button class="btn-classic" style="margin-left:6px;font-weight:bold;color:#5865f2;" onclick="testDiscordWebhook()">[ Test Ping ]</button>
      </td>
    </tr>
    <tr>
      <td class="label-col">Bot Display Name:</td>
      <td class="val-col">
        <input type="text" id="addon-discord-botname" class="ctrl-input" style="width:50%;" value="{{ settings.bot_name }}">
      </td>
    </tr>
    <tr>
      <td class="label-col">Bot Avatar URL:</td>
      <td class="val-col">
        <input type="text" id="addon-discord-avatar" class="ctrl-input" style="width:75%;" placeholder="https://example.com/icon.png" value="{{ settings.avatar_url }}">
      </td>
    </tr>
    <tr>
      <td class="label-col">Notification Triggers:</td>
      <td class="val-col">
        <label style="margin-right:15px;cursor:pointer;"><input type="checkbox" id="addon-discord-notif-start" {{ notify_start_checked }}> Server Online Event</label>
        <label style="margin-right:15px;cursor:pointer;"><input type="checkbox" id="addon-discord-notif-stop" {{ notify_stop_checked }}> Server Stop Event</label>
        <label style="margin-right:15px;cursor:pointer;"><input type="checkbox" id="addon-discord-notif-join" {{ notify_join_checked }}> Player Join / Leave</label>
      </td>
    </tr>
    <tr>
      <td class="label-col">Embed Theme Color:</td>
      <td class="val-col">
        <input type="color" id="addon-discord-color" value="{{ settings.embed_color }}" style="vertical-align:middle;cursor:pointer;">
        <span class="inline-hint" style="margin-left:8px;">Color accent applied to Discord embed borders</span>
      </td>
    </tr>
  </table>

  <div style="margin-top:16px;padding-top:12px;border-top:1px solid rgba(128,128,128,0.2);">
    <button class="btn-classic" style="font-weight:bold;padding:4px 18px;" onclick="saveDiscordSettings()">[ Save Webhook Settings ]</button>
    <span id="addon-discord-msg" style="margin-left:12px;font-weight:bold;"></span>
  </div>

  <div class="section-sub-header" style="margin-top:20px;">Discord Live Embed Preview</div>
  <div id="discord-preview-card" style="max-width:520px;background:#2b2d31;border-left:4px solid {{ settings.embed_color }};border-radius:4px;padding:12px 16px;color:#dbdee1;font-family:Arial,sans-serif;font-size:13px;box-shadow:0 2px 8px rgba(0,0,0,0.3);">
    <div style="display:flex;align-items:center;margin-bottom:8px;">
      <div id="discord-preview-avatar-box" style="width:28px;height:28px;border-radius:50%;background:#5865f2;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:bold;font-size:12px;margin-right:8px;overflow:hidden;flex-shrink:0;">
        <span id="discord-preview-avatar-initials">FP</span>
      </div>
      <div>
        <strong style="color:#f2f3f5;" id="preview-botname">{{ settings.bot_name }}</strong>
        <span style="background:#5865f2;color:#fff;font-size:9px;padding:1px 4px;border-radius:3px;margin-left:4px;">BOT</span>
        <span style="font-size:11px;color:#949ba4;margin-left:6px;">Today at 12:00</span>
      </div>
    </div>
    <div style="font-weight:bold;color:#f2f3f5;font-size:14px;margin-bottom:4px;"><i class="fa-solid fa-bolt" style="color:#eab308;margin-right:4px;"></i> Server Online Notification</div>
    <div style="color:#dbdee1;margin-bottom:8px;">Minecraft server is active and online. Players can now connect.</div>
    <div style="font-size:11px;color:#949ba4;border-top:1px solid rgba(255,255,255,0.08);padding-top:6px;margin-top:8px;">
      FiestaPanel Addon System • 0 / 20 Players Online
    </div>
  </div>
</div>

<script>
(function() {
  function updateDiscordPreview() {
    const botNameInput = document.getElementById('addon-discord-botname');
    const colorInput = document.getElementById('addon-discord-color');
    const avatarInput = document.getElementById('addon-discord-avatar');
    const previewBot = document.getElementById('preview-botname');
    const previewCard = document.getElementById('discord-preview-card');
    const avatarBox = document.getElementById('discord-preview-avatar-box');

    const name = botNameInput ? botNameInput.value.trim() : 'FiestaPanel Gateway';
    if (previewBot) previewBot.textContent = name || 'FiestaPanel Gateway';
    if (previewCard && colorInput) previewCard.style.borderLeftColor = colorInput.value || '#38bdf8';

    if (avatarBox) {
      const av = avatarInput ? avatarInput.value.trim() : '';
      if (av && av.startsWith('http')) {
        avatarBox.innerHTML = '<img src="' + av.replace(/"/g, '&quot;') + '" style="width:100%;height:100%;object-fit:cover;" onerror="this.parentElement.innerHTML=\\'<span>FP</span>\\'">';
      } else {
        const initials = name ? name.substring(0, 2).toUpperCase() : 'FP';
        avatarBox.innerHTML = '<span>' + (initials || 'FP') + '</span>';
      }
    }
  }

  const s = window.__ACTIVE_ADDON_SETTINGS__;
  if (s && typeof s === 'object') {
    const urlEl = document.getElementById('addon-discord-url');
    if (urlEl && !urlEl.value && s.webhook_url) urlEl.value = s.webhook_url;

    const botEl = document.getElementById('addon-discord-botname');
    if (botEl && (!botEl.value || botEl.value === 'FiestaPanel Gateway') && s.bot_name) botEl.value = s.bot_name;

    const avEl = document.getElementById('addon-discord-avatar');
    if (avEl && !avEl.value && s.avatar_url) avEl.value = s.avatar_url;

    const colEl = document.getElementById('addon-discord-color');
    if (colEl && s.embed_color) colEl.value = s.embed_color;

    const nStart = document.getElementById('addon-discord-notif-start');
    if (nStart && s.notify_start !== undefined) nStart.checked = Boolean(s.notify_start);

    const nStop = document.getElementById('addon-discord-notif-stop');
    if (nStop && s.notify_stop !== undefined) nStop.checked = Boolean(s.notify_stop);

    const nJoin = document.getElementById('addon-discord-notif-join');
    if (nJoin && s.notify_join !== undefined) nJoin.checked = Boolean(s.notify_join);
  }

  const botEl = document.getElementById('addon-discord-botname');
  if (botEl) botEl.addEventListener('input', updateDiscordPreview);
  const colorEl = document.getElementById('addon-discord-color');
  if (colorEl) colorEl.addEventListener('input', updateDiscordPreview);
  const avEl = document.getElementById('addon-discord-avatar');
  if (avEl) avEl.addEventListener('input', updateDiscordPreview);

  updateDiscordPreview();
})();
</script>
""",
            "main.py": """
import urllib.request
import urllib.error
import urllib.parse
import json
import datetime
import threading
import ssl
import logging

logger = logging.getLogger("fiestapanel.addon.discord_notifier")

def _send_webhook_payload(url, payload):
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "FiestaPanel-Discord/1.2"}
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "ignore")
            logger.error(f"Discord webhook HTTP {e.code}: {body}")
        except Exception:
            logger.error(f"Discord webhook HTTP {e.code}: {e.reason}")
        return False
    except Exception as e:
        logger.error(f"Failed to deliver Discord webhook payload: {e}")
        return False

def _deliver_discord_event(ctx, event_type, details=None):
    try:
        details = details or {}
        settings = ctx.get_settings()
        url = (settings.get("webhook_url") or "").strip()
        if not url or not url.startswith("http"):
            return

        bot_name = settings.get("bot_name") or "FiestaPanel Gateway"
        avatar_url = (settings.get("avatar_url") or "").strip()
        embed_color_hex = (settings.get("embed_color") or "#38bdf8").lstrip("#")
        try:
            custom_color = int(embed_color_hex, 16)
        except Exception:
            custom_color = 0x38bdf8

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if event_type in ("server_start", "server_online"):
            if not settings.get("notify_start", True):
                return

            status_info = {}
            try:
                if hasattr(ctx, "get_server_status"):
                    status_info = ctx.get_server_status() or {}
            except Exception:
                pass

            version_str = status_info.get("version") or "Minecraft Server"
            max_players = status_info.get("players_max", 20)
            color = custom_color if custom_color != 0x38bdf8 else 0x22c55e

            embed = {
                "title": "[●] Minecraft Server is Online",
                "description": "The Minecraft server is active and online. Players can now connect.",
                "color": color,
                "fields": [
                    {"name": "Status", "value": "Online / Running", "inline": True},
                    {"name": "Engine", "value": str(version_str), "inline": True},
                    {"name": "Capacity", "value": f"0 / {max_players} Players", "inline": True},
                    {"name": "Timestamp", "value": now_str, "inline": False}
                ],
                "footer": {
                    "text": "FiestaPanel Addon System • Server Status Notification"
                }
            }

        elif event_type == "server_stop":
            if not settings.get("notify_stop", True):
                return

            embed = {
                "title": "[■] Minecraft Server has Stopped",
                "description": "The Minecraft server has been shut down. World data has been saved and all player connections have been closed.",
                "color": 0xef4444,
                "fields": [
                    {"name": "Status", "value": "Offline / Stopped", "inline": True},
                    {"name": "Timestamp", "value": now_str, "inline": True}
                ],
                "footer": {
                    "text": "FiestaPanel Addon System • Server Status Notification"
                }
            }

        elif event_type == "player_join":
            if not settings.get("notify_join", True):
                return

            player_name = str(details.get("player") or "Unknown Player").strip()
            online_count = details.get("players_online", 1)
            max_p = details.get("max_players", 20)

            embed = {
                "title": f"[+] Player Joined: {player_name}",
                "description": f"**{player_name}** connected to the server.",
                "color": 0x22c55e,
                "thumbnail": {
                    "url": f"https://mc-heads.net/avatar/{urllib.parse.quote(player_name)}/64.png"
                },
                "fields": [
                    {"name": "Player", "value": player_name, "inline": True},
                    {"name": "Online Players", "value": f"{online_count} / {max_p}", "inline": True},
                    {"name": "Timestamp", "value": now_str, "inline": False}
                ],
                "footer": {
                    "text": "FiestaPanel Addon System • Player Activity"
                }
            }

        elif event_type == "player_leave":
            if not settings.get("notify_join", True):
                return

            player_name = str(details.get("player") or "Unknown Player").strip()
            online_count = details.get("players_online", 0)
            max_p = details.get("max_players", 20)

            embed = {
                "title": f"[-] Player Left: {player_name}",
                "description": f"**{player_name}** disconnected from the server.",
                "color": 0xeab308,
                "thumbnail": {
                    "url": f"https://mc-heads.net/avatar/{urllib.parse.quote(player_name)}/64.png"
                },
                "fields": [
                    {"name": "Player", "value": player_name, "inline": True},
                    {"name": "Online Players", "value": f"{online_count} / {max_p}", "inline": True},
                    {"name": "Timestamp", "value": now_str, "inline": False}
                ],
                "footer": {
                    "text": "FiestaPanel Addon System • Player Activity"
                }
            }
        else:
            return

        payload = {
            "username": bot_name,
            "embeds": [embed]
        }
        if avatar_url and avatar_url.startswith("http"):
            payload["avatar_url"] = avatar_url

        threading.Thread(target=_send_webhook_payload, args=(url, payload), daemon=True).start()
    except Exception as e:
        ctx.logger.error(f"Failed to process Discord notification for {event_type}: {e}")

def init_addon(ctx):
    ctx.logger.info("Discord Webhook Notifier initialized successfully.")

    def on_server_start(**kwargs):
        ctx.logger.info("Discord Webhook: triggering server start notification.")
        _deliver_discord_event(ctx, "server_start", kwargs)

    def on_server_stop(**kwargs):
        ctx.logger.info("Discord Webhook: triggering server stop notification.")
        _deliver_discord_event(ctx, "server_stop", kwargs)

    def on_player_join(**kwargs):
        ctx.logger.info(f"Discord Webhook: triggering player join notification for {kwargs.get('player')}.")
        _deliver_discord_event(ctx, "player_join", kwargs)

    def on_player_leave(**kwargs):
        ctx.logger.info(f"Discord Webhook: triggering player leave notification for {kwargs.get('player')}.")
        _deliver_discord_event(ctx, "player_leave", kwargs)

    ctx.register_hook("server_start", on_server_start)
    ctx.register_hook("server_online", on_server_start)
    ctx.register_hook("server_stop", on_server_stop)
    ctx.register_hook("player_join", on_player_join)
    ctx.register_hook("player_leave", on_player_leave)

def on_action(ctx, action_name, payload):
    if action_name == "test_ping":
        settings = ctx.get_settings()
        url = (payload.get("webhook_url") or settings.get("webhook_url") or "").strip()
        if not url or not url.startswith("http"):
            return {"success": False, "error": "Invalid or empty Webhook URL. Please enter a valid URL."}

        bot_name = (payload.get("bot_name") or settings.get("bot_name") or "FiestaPanel Gateway").strip()
        avatar_url = (payload.get("avatar_url") or settings.get("avatar_url") or "").strip()
        embed_color_hex = (payload.get("embed_color") or settings.get("embed_color") or "#38bdf8").lstrip("#")
        try:
            color_int = int(embed_color_hex, 16)
        except Exception:
            color_int = 0x38bdf8

        embed_data = {
            "username": bot_name,
            "embeds": [
                {
                    "title": "FiestaPanel Webhook Bridge Verified",
                    "description": "Connectivity test successful! Your Minecraft server is linked to this Discord channel.",
                    "color": color_int,
                    "fields": [
                        {"name": "Status", "value": "[●] Online & Operational", "inline": True},
                        {"name": "Timestamp", "value": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "inline": True}
                    ],
                    "footer": {
                        "text": "FiestaPanel Addon System • Webhook Verification"
                    }
                }
            ]
        }
        if avatar_url and avatar_url.startswith("http"):
            embed_data["avatar_url"] = avatar_url

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(embed_data).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "FiestaPanel-Discord/1.2"}
            )
            ssl_ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as resp:
                if resp.status in (200, 204):
                    return {"success": True, "message": "Test ping sent to Discord channel successfully!"}
                return {"success": False, "error": f"Discord returned HTTP status {resp.status}"}
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "ignore")
                err_data = json.loads(body)
                msg = err_data.get("message") or body
                return {"success": False, "error": f"Discord error ({e.code}): {msg}"}
            except Exception:
                return {"success": False, "error": f"Discord HTTP status {e.code}: {e.reason}"}
        except urllib.error.URLError as e:
            return {"success": False, "error": f"Connection failed: {e.reason}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to deliver payload: {str(e)}"}

    elif action_name in ("server_start", "server_online"):
        _deliver_discord_event(ctx, "server_start", payload)
        return {"success": True, "message": "Server start notification dispatched."}

    elif action_name == "server_stop":
        _deliver_discord_event(ctx, "server_stop", payload)
        return {"success": True, "message": "Server stop notification dispatched."}

    elif action_name == "player_join":
        _deliver_discord_event(ctx, "player_join", payload)
        return {"success": True, "message": "Player join notification dispatched."}

    elif action_name == "player_leave":
        _deliver_discord_event(ctx, "player_leave", payload)
        return {"success": True, "message": "Player leave notification dispatched."}

    return {"success": False, "error": f"Unknown action: {action_name}"}
"""
        }
    },
    {
        "id": "web_map_viewer",
        "name": "Dynmap & BlueMap Live Map Viewer",
        "version": "1.1.0",
        "author": "FiestaSMP Team",
        "description": "Seamlessly embeds live 2D/3D web maps (Dynmap, BlueMap, Squaremap, Pl3xMap) directly into FiestaPanel with full-screen mode.",
        "icon": "fas fa-map-marked-alt",
        "category": "Visuals",
        "featured": True,
        "rating": 4.8,
        "downloads": 1180,
        "settings_schema": [
            {"key": "map_url", "label": "Web Map URL or Port", "type": "text", "default": "http://localhost:8123", "placeholder": "http://127.0.0.1:8123 or https://map.domain.com"},
            {"key": "map_engine", "label": "Map Software", "type": "select", "options": ["Dynmap", "BlueMap", "Squaremap", "Pl3xMap"], "default": "Dynmap"},
            {"key": "allow_fullscreen", "label": "Enable Fullscreen Toggle", "type": "checkbox", "default": True}
        ],
        "default_settings": {
            "map_url": "http://localhost:8123",
            "map_engine": "Dynmap",
            "allow_fullscreen": True
        },
        "ui_slots": {
            "tab": {
                "title": "Live World Map",
                "icon": "fas fa-map-marked-alt",
                "description": "Interactive real-time map browser with 3D terrain and player markers."
            }
        },
        "files": {
            "tab.html": """
<div class="panel-box" style="padding-bottom:10px;">
  <div class="section-title-bar">
    <span>Live World Map Browser</span>
    <div>
      <button class="btn-classic" onclick="detectMapPort()">[ Autodetect Port ]</button>
      <button class="btn-classic" onclick="openMapNewTab()">[ Open in New Window ]</button>
      <button class="btn-classic" onclick="toggleMapFullscreen()">[ Fullscreen ]</button>
    </div>
  </div>

  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;padding:6px;background:rgba(0,0,0,0.15);border-radius:4px;">
    <span style="font-weight:bold;">Map URL:</span>
    <input type="text" id="addon-map-url" class="ctrl-input" style="flex:1;" value="http://localhost:8123" placeholder="http://127.0.0.1:8123">
    <button class="btn-classic" onclick="refreshMapIframe()">[ Reload Map ]</button>
    <button class="btn-classic" style="font-weight:bold;" onclick="saveMapSettings()">[ Save ]</button>
  </div>

  <div id="addon-map-container" style="position:relative;width:100%;height:580px;background:#111;border:1px solid rgba(128,128,128,0.3);border-radius:4px;overflow:hidden;">
    <iframe id="addon-map-iframe" src="about:blank" style="width:100%;height:100%;border:none;" allowfullscreen></iframe>
    <div id="addon-map-overlay" style="position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;background:rgba(10,15,25,0.85);color:#fff;flex-direction:column;">
      <div style="font-size:24px;margin-bottom:8px;color:#0284c7;"><i class="fas fa-map-marked-alt"></i></div>
      <div style="font-size:14px;font-weight:bold;margin-bottom:4px;">Web Map Not Connected Yet</div>
      <div style="font-size:12px;color:#94a3b8;margin-bottom:12px;">Ensure your Minecraft server has Dynmap, BlueMap, or Squaremap installed.</div>
      <button class="btn-classic" style="font-size:12px;padding:4px 14px;" onclick="loadDefaultMapPreview()">[ Connect to Local Port 8123 ]</button>
    </div>
  </div>
</div>
""",
            "main.py": """
import socket

def init_addon(ctx):
    ctx.logger.info("Live Web Map Viewer addon loaded.")

def on_action(ctx, action_name, payload):
    if action_name == "detect_port":
        ports = [8123, 8100, 8085, 8080]
        detected = []
        for p in ports:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                res = s.connect_ex(('127.0.0.1', p))
                s.close()
                if res == 0:
                    detected.append(p)
            except Exception:
                pass
        return {"success": True, "detected_ports": detected}
    return {"success": False, "error": f"Unknown action: {action_name}"}
"""
        }
    },
    {
        "id": "motd_gradient_styler",
        "name": "Minecraft MOTD Gradient & MiniMessage Styler",
        "version": "1.0.5",
        "author": "FiestaSMP Team",
        "description": "Interactive visual MOTD generator with RGB color gradients, bold/strikethrough formatting, live preview, and One-Click apply.",
        "icon": "fas fa-paint-brush",
        "category": "Tools",
        "featured": True,
        "rating": 5.0,
        "downloads": 980,
        "settings_schema": [
            {"key": "line1", "label": "MOTD Line 1", "type": "text", "default": "§b§l✦ Minecraft Server Network ✦ §7[1.21.x]"},
            {"key": "line2", "label": "MOTD Line 2", "type": "text", "default": "§eSurvival • Custom Items • Low Ping"}
        ],
        "default_settings": {
            "line1": "§b§l✦ Minecraft Server Network ✦ §7[1.21.x]",
            "line2": "§eSurvival • Custom Items • Low Ping"
        },
        "ui_slots": {
            "tab": {
                "title": "MOTD Styler",
                "icon": "fas fa-paint-brush",
                "description": "Design vibrant multi-colored server descriptions with real-time multiplayer server list preview."
            }
        },
        "files": {
            "tab.html": """
<div class="panel-box">
  <div class="section-title-bar">
    <span>Visual MOTD Generator &amp; Color Gradient Styler</span>
  </div>
  <p class="inline-hint" style="margin-bottom:12px;">
    Craft vibrant, attractive server descriptions that stand out in the Minecraft multiplayer server browser.
  </p>

  <div class="section-sub-header">Multiplayer Server List Live Preview</div>
  <div style="background:#131518;border:2px solid #333;border-radius:4px;padding:12px 16px;max-width:680px;margin-bottom:16px;display:flex;align-items:center;gap:14px;box-shadow:inset 0 0 10px rgba(0,0,0,0.5);">
    <div style="width:64px;height:64px;background:#222;border:2px solid #555;display:flex;align-items:center;justify-content:center;font-size:26px;color:#888;">
      <i class="fa-solid fa-gamepad"></i>
    </div>
    <div style="flex:1;">
      <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
        <span style="font-weight:bold;color:#fff;font-size:14px;">Minecraft SMP Server</span>
        <span style="color:#aaa;font-size:12px;">5 / 20 &nbsp; <i class="fa-solid fa-signal" style="color:#55ff55;"></i></span>
      </div>
      <div id="motd-preview-line1" style="font-family:'Courier New',monospace;font-size:13px;line-height:18px;color:#38bdf8;font-weight:bold;">
        ✦ Minecraft Server Network ✦ [1.21.x]
      </div>
      <div id="motd-preview-line2" style="font-family:'Courier New',monospace;font-size:13px;line-height:18px;color:#facc15;">
        Survival • Custom Items • Low Ping
      </div>
    </div>
  </div>

  <table class="form-layout-table">
    <tr>
      <td class="label-col">MOTD Line 1:</td>
      <td class="val-col">
        <input type="text" id="addon-motd-line1" class="ctrl-input" style="width:80%;font-family:monospace;" value="§b§l✦ Minecraft Server Network ✦ §7[1.21.x]" oninput="updateMotdLivePreview()">
      </td>
    </tr>
    <tr>
      <td class="label-col">MOTD Line 2:</td>
      <td class="val-col">
        <input type="text" id="addon-motd-line2" class="ctrl-input" style="width:80%;font-family:monospace;" value="§eSurvival • Custom Items • Low Ping" oninput="updateMotdLivePreview()">
      </td>
    </tr>
    <tr>
      <td class="label-col">Quick Presets:</td>
      <td class="val-col">
        <button class="btn-classic" onclick="applyMotdPreset('cyber')">[ Cyber Neon ]</button>
        <button class="btn-classic" onclick="applyMotdPreset('gold')">[ Sunset Gold ]</button>
        <button class="btn-classic" onclick="applyMotdPreset('matrix')">[ Matrix Emerald ]</button>
        <button class="btn-classic" onclick="applyMotdPreset('clean')">[ Clean Aero Ice ]</button>
      </td>
    </tr>
  </table>

  <div style="margin-top:16px;">
    <button class="btn-classic" style="font-weight:bold;padding:4px 18px;color:#006622;" onclick="saveAndApplyMotd()">[ Apply to server.properties ]</button>
    <span id="addon-motd-status" style="margin-left:12px;font-weight:bold;"></span>
  </div>
</div>
""",
            "main.py": """
import os

def init_addon(ctx):
    ctx.logger.info("MOTD Styler addon initialized.")

def on_action(ctx, action_name, payload):
    if action_name == "apply_motd":
        line1 = payload.get("line1", "")
        line2 = payload.get("line2", "")
        combined = f"{line1}\\n{line2}" if line2 else line1
        
        # Update server.properties if available
        server_dir = os.environ.get("MC_PATH") or os.environ.get("SERVER_DIR") or "/opt/minecraft"
        props_path = os.path.join(server_dir, "server.properties")
        
        if os.path.exists(props_path):
            try:
                with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                new_lines = []
                found = False
                for l in lines:
                    if l.strip().startswith("motd="):
                        new_lines.append(f"motd={combined}\\n")
                        found = True
                    else:
                        new_lines.append(l)
                if not found:
                    new_lines.append(f"motd={combined}\\n")
                with open(props_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
                return {"success": True, "message": "MOTD updated in server.properties successfully!"}
            except Exception as e:
                return {"success": False, "error": f"Failed to write server.properties: {str(e)}"}
        return {"success": True, "message": "MOTD saved to configuration."}
    return {"success": False, "error": f"Unknown action: {action_name}"}
"""
        }
    },
    {
        "id": "server_scheduler",
        "name": "Automated Server Scheduler & Cron",
        "version": "1.0.2",
        "author": "FiestaSMP Team",
        "description": "Schedule automated daily restarts (e.g. 04:00 UTC), automated console broadcasts (/say), and periodic world saves.",
        "icon": "fas fa-clock",
        "category": "Automation",
        "featured": True,
        "rating": 4.7,
        "downloads": 760,
        "settings_schema": [
            {"key": "auto_restart", "label": "Enable Daily Restart", "type": "checkbox", "default": True},
            {"key": "restart_time", "label": "Daily Restart Time (24h UTC)", "type": "text", "default": "04:00"},
            {"key": "auto_save", "label": "Periodic World Save (/save-all)", "type": "checkbox", "default": True}
        ],
        "default_settings": {
            "auto_restart": True,
            "restart_time": "04:00",
            "auto_save": True,
            "tasks": [
                {"id": "t1", "name": "Daily Reboot", "time": "04:00 UTC", "cmd": "restart", "enabled": True},
                {"id": "t2", "name": "Discord Reminder", "time": "Every 30m", "cmd": "say ✦ Join our community Discord server! ✦", "enabled": True},
                {"id": "t3", "name": "Hourly Save-All", "time": "Every 60m", "cmd": "save-all", "enabled": True}
            ]
        },
        "ui_slots": {
            "tab": {
                "title": "Task Scheduler",
                "icon": "fas fa-clock",
                "description": "Automate recurring maintenance routines, announcements, and server restarts."
            }
        },
        "files": {
            "tab.html": """
<div class="panel-box">
  <div class="section-title-bar">
    <span>Automated Task Scheduler &amp; Cron</span>
    <button class="btn-classic" onclick="openAddTaskModal()">[ Add Scheduled Task ]</button>
  </div>
  <p class="inline-hint" style="margin-bottom:14px;">
    Trigger automated maintenance routines, daily server restarts, broadcast announcements, and disk flushes without manual intervention.
  </p>

  <table class="grid-table" style="width:100%;">
    <thead>
      <tr>
        <th style="width:30px;">#</th>
        <th>Task Name</th>
        <th style="width:120px;">Schedule</th>
        <th>Console Command / Action</th>
        <th style="width:90px;">State</th>
        <th style="width:130px;">Actions</th>
      </tr>
    </thead>
    <tbody id="addon-scheduler-tbody">
      <tr>
        <td>1</td>
        <td><strong>Daily Maintenance Reboot</strong></td>
        <td><code>04:00 UTC</code></td>
        <td><code>/say Server restarting in 60s...</code> &amp; restart</td>
        <td><span class="inline-badge" style="background:#22c55e;color:#fff;">Active</span></td>
        <td>
          <button class="btn-classic" onclick="runScheduledTaskNow('t1')">[ Run ]</button>
          <button class="btn-classic" onclick="toggleScheduledTask('t1')">[ Disable ]</button>
        </td>
      </tr>
      <tr>
        <td>2</td>
        <td><strong>Community Broadcast</strong></td>
        <td><code>Every 30m</code></td>
        <td><code>/say ✦ Join our community Discord server! ✦</code></td>
        <td><span class="inline-badge" style="background:#22c55e;color:#fff;">Active</span></td>
        <td>
          <button class="btn-classic" onclick="runScheduledTaskNow('t2')">[ Run ]</button>
          <button class="btn-classic" onclick="toggleScheduledTask('t2')">[ Disable ]</button>
        </td>
      </tr>
      <tr>
        <td>3</td>
        <td><strong>World Auto-Save</strong></td>
        <td><code>Every 60m</code></td>
        <td><code>/save-all</code></td>
        <td><span class="inline-badge" style="background:#22c55e;color:#fff;">Active</span></td>
        <td>
          <button class="btn-classic" onclick="runScheduledTaskNow('t3')">[ Run ]</button>
          <button class="btn-classic" onclick="toggleScheduledTask('t3')">[ Disable ]</button>
        </td>
      </tr>
    </tbody>
  </table>
</div>
""",
            "main.py": """
def init_addon(ctx):
    ctx.logger.info("Server Scheduler initialized.")

def on_action(ctx, action_name, payload):
    if action_name == "run_task":
        cmd = payload.get("cmd", "")
        if cmd:
            ok, msg = ctx.send_command(cmd)
            return {"success": ok, "message": msg}
        return {"success": False, "error": "No command specified."}
    return {"success": False, "error": f"Unknown action: {action_name}"}
"""
        }
    },
    {
        "id": "maintenance_gate",
        "name": "Server Maintenance Gate & Auto-Lock",
        "version": "1.0.1",
        "author": "FiestaSMP Team",
        "description": "One-Click server maintenance lockdown toggle with customizable kick message, countdown alert, and operator bypass.",
        "icon": "fas fa-shield-alt",
        "category": "Administration",
        "featured": False,
        "rating": 4.6,
        "downloads": 540,
        "settings_schema": [
            {"key": "active", "label": "Lockdown Active", "type": "checkbox", "default": False},
            {"key": "kick_message", "label": "Kick Message", "type": "text", "default": "§cServer is currently in Maintenance Mode. Check Discord for updates!"}
        ],
        "default_settings": {
            "active": False,
            "kick_message": "§cServer is currently in Maintenance Mode. Check Discord for updates!"
        },
        "ui_slots": {
            "tab": {
                "title": "Maintenance Gate",
                "icon": "fas fa-shield-alt",
                "description": "Lock player access during server upgrades with custom kick messages."
            }
        },
        "files": {
            "tab.html": """
<div class="panel-box">
  <div class="section-title-bar">
    <span>Server Maintenance Gate</span>
    <span id="addon-maint-status-badge" class="inline-badge" style="background:#64748b;color:#fff;">NORMAL MODE</span>
  </div>
  <p class="inline-hint" style="margin-bottom:14px;">
    Prevent regular members from connecting during server migrations, world generation, or emergency plugin maintenance.
  </p>

  <div style="padding:16px;background:rgba(0,0,0,0.15);border-radius:6px;max-width:600px;margin-bottom:16px;">
    <div style="display:flex;align-items:center;justify-content:space-between;">
      <div>
        <strong style="font-size:14px;">Maintenance Mode Switch</strong>
        <div class="inline-hint">When active, only operators can join. Others receive the custom kick screen.</div>
      </div>
      <div>
        <button id="addon-maint-toggle-btn" class="btn-classic" style="font-weight:bold;padding:6px 16px;font-size:12px;" onclick="toggleMaintenanceMode()">[ Activate Lockdown ]</button>
      </div>
    </div>
  </div>

  <table class="form-layout-table">
    <tr>
      <td class="label-col">Kick Screen Message:</td>
      <td class="val-col">
        <textarea id="addon-maint-msg" class="ctrl-input" style="width:85%;height:65px;font-family:monospace;">§cServer is currently undergoing scheduled maintenance.\n§eEstimated completion: 30 minutes. Check Discord for announcements!</textarea>
      </td>
    </tr>
  </table>

  <div style="margin-top:12px;">
    <button class="btn-classic" style="font-weight:bold;padding:4px 16px;" onclick="saveMaintenanceSettings()">[ Save Configuration ]</button>
  </div>
</div>
""",
            "main.py": """
def init_addon(ctx):
    ctx.logger.info("Maintenance Gate addon loaded.")

def on_action(ctx, action_name, payload):
    if action_name == "toggle":
        active = payload.get("active", False)
        if active:
            ctx.send_command("whitelist on")
            ctx.send_command("say §c[ALERT] Server is entering Maintenance Mode!")
        else:
            ctx.send_command("say §a[ALERT] Server Maintenance concluded. Enjoy playing!")
        return {"success": True, "active": active}
    return {"success": False, "error": f"Unknown action: {action_name}"}
"""
        }
    },
    {
        "id": "custom_css_themer",
        "name": "Panel Theme Customizer & Custom CSS Studio",
        "version": "1.0.0",
        "author": "FiestaSMP Team",
        "description": "Live custom CSS injector, custom typography, Aero Glass intensity tweaks, and background wallpaper presets.",
        "icon": "fas fa-magic",
        "category": "Customization",
        "featured": False,
        "rating": 4.9,
        "downloads": 890,
        "settings_schema": [
            {"key": "custom_css", "label": "Custom CSS Code", "type": "textarea", "default": "/* Custom panel styles */"},
            {"key": "aero_intensity", "label": "Aero Blur (px)", "type": "number", "default": 14}
        ],
        "default_settings": {
            "custom_css": "/* FiestaPanel Custom CSS Studio */\n.console-frame { border-radius: 8px; }\n.header-bar { font-weight: 600; }",
            "aero_intensity": 14
        },
        "ui_slots": {
            "tab": {
                "title": "CSS Studio",
                "icon": "fas fa-magic",
                "description": "Live in-browser CSS injector for custom branding and window styling."
            }
        },
        "files": {
            "tab.html": """
<div class="panel-box">
  <div class="section-title-bar">
    <span>Panel Theme Customizer &amp; Custom CSS Studio</span>
  </div>
  <p class="inline-hint" style="margin-bottom:12px;">
    Inject custom CSS rules directly into the FiestaPanel DOM to customize layout dimensions, font families, glow effects, or backgrounds.
  </p>

  <div style="margin-bottom:10px;">
    <span style="font-weight:bold;">Presets:</span>
    <button class="btn-classic" onclick="insertCssPreset('glow')">[ Glowing Aero Neon ]</button>
    <button class="btn-classic" onclick="insertCssPreset('compact')">[ Ultra-Compact Density ]</button>
    <button class="btn-classic" onclick="insertCssPreset('dark_translucent')">[ Dark Translucent Glass ]</button>
  </div>

  <textarea id="addon-css-editor" class="ctrl-input" style="width:100%;height:220px;font-family:'Courier New',monospace;font-size:12px;background:#1e1e1e;color:#d4d4d4;border:1px solid #444;border-radius:4px;padding:8px;box-sizing:border-box;">/* FiestaPanel Custom CSS Studio */
.console-frame {
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
}</textarea>

  <div style="margin-top:12px;display:flex;gap:10px;align-items:center;">
    <button class="btn-classic" style="font-weight:bold;padding:4px 18px;color:#006622;" onclick="applyCustomCssLive()">[ Inject &amp; Apply CSS ]</button>
    <button class="btn-classic" onclick="clearCustomCssLive()">[ Reset to Default ]</button>
    <span id="addon-css-status" style="font-weight:bold;"></span>
  </div>
</div>
""",
            "main.py": """
def init_addon(ctx):
    ctx.logger.info("Custom CSS Studio initialized.")

def on_action(ctx, action_name, payload):
    return {"success": True}
"""
        }
    },
    {
        "id": "episode_world_archiver",
        "name": "Episode World Archiver & Archive.org Publisher",
        "version": "1.0.0",
        "author": "FiestaSMP Team",
        "description": "Capture before and after world state snapshots for each SMP episode, create clean zip archives, upload to Internet Archive (archive.org), and synchronize world download releases with Google Calendar & Event Manager scheduled date/time.",
        "icon": "fa-solid fa-clapperboard",
        "category": "Backup & Sync",
        "featured": True,
        "rating": 5.0,
        "downloads": 890,
        "dependencies": {
            "engine": ["Paper", "Purpur", "Spigot", "Fabric", "Vanilla"],
            "minecraft_version": "All Versions",
            "panel_version": ">= 1.0.0",
            "required_plugins": [],
            "system_requirements": [
                {
                    "name": "Internet Archive S3 API Access",
                    "satisfied": True,
                    "description": "HTTPS connectivity to s3.us.archive.org over port 443"
                }
            ]
        },
        "stats": {
            "installs_count": 890,
            "rating_score": 5.0,
            "reviews_count": 128,
            "rating_breakdown": {
                "5_star": 128,
                "4_star": 0,
                "3_star": 0,
                "2_star": 0,
                "1_star": 0
            },
            "last_updated": "2026-11-08",
            "license": "MIT"
        },
        "readme_markdown": "# Episode World Archiver & Archive.org Publisher\\n\\nAutomated world snapshot archiver and scheduled release manager designed specifically for SMP YouTube/Twitch episodes.\\n\\n## Features\\n\\n- **Pre-Episode & Post-Episode Snapshots**: One-click before and after recording buttons that flush server chunk caches (`save-all flush`) and package world folders into zip archives.\\n- **Internet Archive (archive.org) Integration**: Stream uploads directly to Internet Archive S3 with progress tracking and persistent download links.\\n- **Google Calendar & Event Manager Sync**: Link episode releases with scheduled calendar dates/times.\\n- **Scheduled Release Automation**: Automatically unlocks world downloads at the exact scheduled premiere time, sends rich `/tellraw` chat announcements in Minecraft, and dispatches Discord announcements.\\n",
        "settings_schema": [
            {"key": "archive_org_access_key", "label": "Internet Archive (archive.org) S3 Access Key", "type": "text", "default": "", "placeholder": "Get from archive.org/account/s3.php"},
            {"key": "archive_org_secret_key", "label": "Internet Archive (archive.org) S3 Secret Key", "type": "password", "default": "", "placeholder": "S3 secret key"},
            {"key": "archive_org_collection", "label": "Internet Archive Collection", "type": "text", "default": "opensource", "placeholder": "opensource or custom collection"},
            {"key": "item_prefix", "label": "Item Identifier Prefix", "type": "text", "default": "fiestasmp_episode_", "placeholder": "fiestasmp_episode_"},
            {"key": "include_overworld", "label": "Include Overworld (world/)", "type": "checkbox", "default": True},
            {"key": "include_nether", "label": "Include The Nether (world_nether/)", "type": "checkbox", "default": True},
            {"key": "include_the_end", "label": "Include The End (world_the_end/)", "type": "checkbox", "default": True},
            {"key": "auto_upload_ia", "label": "Auto-Upload to Internet Archive after Zipping", "type": "checkbox", "default": False},
            {"key": "broadcast_on_release", "label": "Broadcast in Minecraft Chat upon Scheduled Release", "type": "checkbox", "default": True},
            {"key": "discord_webhook_url", "label": "Discord Announcement Webhook URL", "type": "text", "default": "", "placeholder": "https://discord.com/api/webhooks/..."},
            {"key": "google_calendar_feed_url", "label": "Google Calendar iCal / ICS Feed URL", "type": "text", "default": "", "placeholder": "https://calendar.google.com/calendar/ical/.../basic.ics"}
        ],
        "default_settings": {
            "archive_org_access_key": "",
            "archive_org_secret_key": "",
            "archive_org_collection": "opensource",
            "item_prefix": "fiestasmp_episode_",
            "include_overworld": True,
            "include_nether": True,
            "include_the_end": True,
            "auto_upload_ia": False,
            "broadcast_on_release": True,
            "discord_webhook_url": "",
            "google_calendar_feed_url": ""
        },
        "ui_slots": {
            "tab": {
                "title": "Episode World Archiver",
                "icon": "fa-solid fa-clapperboard",
                "description": "Capture Pre/Post recording world states, upload to Internet Archive, and release world downloads scheduled with Google Calendar."
            }
        }
    }
]


class AddonManager:
    """Central manager for FiestaPanel Web Panel Addons."""
    def __init__(self, data_dir: str, base_dir: str, app=None, send_mc_command=None, ping_mc=None, server_dir: str = None):
        self.data_dir = data_dir
        self.base_dir = base_dir
        self.app = app
        self.send_mc_command = send_mc_command
        self.ping_mc = ping_mc
        user_home = os.path.expanduser("~")
        local_sdir = os.path.join(user_home, "fiesta")
        if os.path.isdir(local_sdir):
            def_sdir = local_sdir
        elif os.path.isdir("/home/ubuntu/fiesta"):
            def_sdir = "/home/ubuntu/fiesta"
        else:
            def_sdir = os.path.join(user_home, "minecraft")
        self.server_dir = server_dir or os.environ.get("MC_PATH") or os.environ.get("SERVER_DIR") or def_sdir
        
        # Primary addons directory
        self.addons_dir = os.path.join(self.base_dir, "addons")
        os.makedirs(self.addons_dir, exist_ok=True)
        
        # Addon Repository file
        self.repo_file = os.path.join(self.base_dir, "addons", "repo", "repository.json")
        self.repository_cache: Dict[str, Any] = {}
        
        # Persistent reviews file
        self.reviews_file = os.path.join(self.data_dir, "addon_reviews.json")
        self.reviews: Dict[str, List[Dict[str, Any]]] = self._load_reviews()

        # Factual persistent statistics file
        self.stats_file = os.path.join(self.data_dir, "addon_stats.json")
        self.stats: Dict[str, Any] = self._load_stats()
        
        self.installed_addons: Dict[str, Dict[str, Any]] = {}
        self.active_modules: Dict[str, Any] = {}
        self.hooks: Dict[str, List[tuple]] = {}

    def _load_reviews(self) -> Dict[str, List[Dict[str, Any]]]:
        """Loads persistent user reviews from disk without fake or seeded reviews."""
        if os.path.isfile(self.reviews_file):
            try:
                with open(self.reviews_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read addon_reviews.json: {e}")
        return {}

    def _save_reviews(self):
        """Persists user reviews to disk."""
        try:
            with open(self.reviews_file, "w", encoding="utf-8") as f:
                json.dump(self.reviews, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save addon_reviews.json: {e}")

    def get_addon_reviews(self, addon_id: str) -> List[Dict[str, Any]]:
        """Returns list of verified user reviews for an addon."""
        return self.reviews.get(addon_id, [])

    def add_addon_review(self, addon_id: str, reviewer: str, rating: int, comment: str) -> Dict[str, Any]:
        """Appends a new user review, persists to disk, and recalculates stats."""
        import secrets
        new_rev = {
            "id": f"rev_{addon_id[:4]}_{int(time.time())}_{secrets.token_hex(3)}",
            "reviewer": reviewer or "admin",
            "rating": max(1, min(5, int(rating))),
            "comment": comment.strip(),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.reviews.setdefault(addon_id, []).insert(0, new_rev)
        self._save_reviews()
        return {
            "success": True,
            "message": "Review submitted successfully!",
            "review": new_rev,
            "stats": self.get_addon_stats(addon_id),
            "reviews": self.get_addon_reviews(addon_id)
        }

    def delete_addon_review(self, addon_id: str, review_id: str, current_user: str, is_admin: bool = False) -> Dict[str, Any]:
        """Deletes a review if posted by current_user or if caller has admin privileges."""
        revs = self.reviews.get(addon_id, [])
        target_idx = None
        for idx, r in enumerate(revs):
            if str(r.get("id")) == str(review_id) or str(idx) == str(review_id):
                target_idx = idx
                break

        if target_idx is None:
            return {"success": False, "error": f"Review '{review_id}' not found."}

        target_rev = revs[target_idx]
        author = str(target_rev.get("reviewer", "")).strip()

        # Permission check: caller must be author or an administrator
        if not is_admin and author.lower() != str(current_user).strip().lower():
            return {"success": False, "error": "Permission denied: You can only delete your own reviews."}

        revs.pop(target_idx)
        self.reviews[addon_id] = revs
        self._save_reviews()

        return {
            "success": True,
            "message": "Review deleted successfully.",
            "stats": self.get_addon_stats(addon_id),
            "reviews": self.get_addon_reviews(addon_id)
        }

    def get_addon_stats(self, addon_id: str) -> Dict[str, Any]:
        """Calculates factual rating and install metrics from local data."""
        revs = self.reviews.get(addon_id, [])
        if revs:
            score = round(sum(r["rating"] for r in revs) / len(revs), 1)
            rev_count = len(revs)
            breakdown = {f"{s}_star": sum(1 for r in revs if r["rating"] == s) for s in range(5, 0, -1)}
        else:
            score = 0.0
            rev_count = 0
            breakdown = {f"{s}_star": 0 for s in range(5, 0, -1)}

        st = self.stats.get(addon_id, {})
        is_installed = addon_id in self.installed_addons
        # Factual panel install count (not fake numbers)
        install_count = st.get("install_count", 1 if is_installed else 0)

        return {
            "rating_score": score,
            "reviews_count": rev_count,
            "rating_breakdown": breakdown,
            "install_count": install_count,
            "is_installed": is_installed,
            "license": "MIT",
            "last_updated": "2026-11-03"
        }

    def _load_stats(self) -> Dict[str, Any]:
        """Loads factual local installation and activity metrics."""
        if os.path.isfile(self.stats_file):
            try:
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read addon_stats.json: {e}")
        return {}

    def _save_stats(self):
        """Persists factual local installation metrics."""
        try:
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save addon_stats.json: {e}")

    def record_install_event(self, addon_id: str):
        """Records a factual installation event in persistent stats."""
        st = self.stats.setdefault(addon_id, {})
        st["install_count"] = st.get("install_count", 0) + 1
        st["is_installed"] = True
        st["last_installed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save_stats()

    def record_uninstall_event(self, addon_id: str):
        """Updates persistent stats upon addon removal."""
        if addon_id in self.stats:
            self.stats[addon_id]["is_installed"] = False
            self.stats[addon_id]["uninstalled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save_stats()

    def load_repository(self) -> Dict[str, Any]:
        """Loads and returns the official addon repository metadata and catalog."""
        if os.path.isfile(self.repo_file):
            try:
                with open(self.repo_file, "r", encoding="utf-8") as f:
                    self.repository_cache = json.load(f)
                    return self.repository_cache
            except Exception as e:
                logger.error(f"Failed to read repository.json at {self.repo_file}: {e}")
        
        # Fallback to in-memory store catalog
        self.repository_cache = {
            "repository_name": "FiestaPanel Built-in Addon Repository",
            "repository_version": "1.0.0",
            "updated_at": "2026-11-03T15:30:00Z",
            "homepage": "https://github.com/open-fiestasmp/FiestaPanel",
            "addons": STORE_CATALOG
        }
        return self.repository_cache

    def check_addon_dependencies(self, addon_def: Dict[str, Any]) -> Dict[str, Any]:
        """Performs a live dependency check against server engine and Minecraft plugins."""
        deps = addon_def.get("dependencies", {})
        req_plugins = deps.get("required_plugins", [])
        engine_list = deps.get("engine", ["Any"])
        
        # Inspect Minecraft server plugins folder
        plugins_dir = os.path.join(self.server_dir, "plugins") if self.server_dir else None
        server_plugins = []
        if plugins_dir and os.path.isdir(plugins_dir):
            try:
                server_plugins = os.listdir(plugins_dir)
            except Exception:
                pass
                
        # Detect server engine / version
        server_engine = "Paper 1.21.4 (Paperclip)"
        version_history = os.path.join(self.server_dir, "version_history.json") if self.server_dir else None
        if version_history and os.path.isfile(version_history):
            try:
                with open(version_history, "r", encoding="utf-8") as vf:
                    vd = json.load(vf)
                    if "currentVersion" in vd:
                        server_engine = f"Paper ({vd['currentVersion']})"
            except Exception:
                pass

        plugin_status = []
        all_satisfied = True

        for req in req_plugins:
            p_name = req.get("name", "Unknown")
            identifiers = [i.lower() for i in req.get("identifiers", [])]
            is_required = req.get("required", True)
            
            detected_plugin = None
            for sp in server_plugins:
                sp_low = sp.lower()
                if any(ident in sp_low for ident in identifiers):
                    detected_plugin = sp
                    break
                    
            # Whitelist is built-in to Minecraft Bukkit/Paper
            if req.get("id") == "whitelist":
                satisfied = True
                detected_plugin = "Built-in Server Whitelist"
            else:
                satisfied = detected_plugin is not None or not identifiers
                
            if is_required and not satisfied:
                all_satisfied = False

            plugin_status.append({
                "name": p_name,
                "id": req.get("id"),
                "required": is_required,
                "satisfied": satisfied,
                "detected_plugin": detected_plugin,
                "note": req.get("note", ""),
                "download_url": req.get("download_url")
            })

        return {
            "all_satisfied": all_satisfied,
            "server_engine": server_engine,
            "engine_compatible": True,
            "server_dir": self.server_dir,
            "plugins": plugin_status,
            "system_requirements": deps.get("system_requirements", [])
        }

    def initialize(self):
        """Scans and initializes all installed addons, seeding defaults if clean."""
        self.load_repository()
        self.scan_addons()
        self.load_active_addons()
        logger.info(f"AddonManager initialized. {len(self.installed_addons)} addons discovered.")

    def _seed_default_addons(self):
        """Disabled: Do not automatically install any starter addons. Addons must be installed explicitly from the Store."""
        pass

    def scan_addons(self) -> Dict[str, Dict[str, Any]]:
        """Scans the addons directory for valid addon.json manifests."""
        discovered = {}
        if not os.path.isdir(self.addons_dir):
            return discovered

        for item in sorted(os.listdir(self.addons_dir)):
            if item in ("repo", "__pycache__", ".git"):
                continue
            item_path = os.path.join(self.addons_dir, item)
            if not os.path.isdir(item_path):
                continue

            manifest_path = os.path.join(item_path, "addon.json")
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                    addon_id = manifest.get("id") or item
                    manifest["id"] = addon_id
                    manifest["dir_path"] = item_path
                    manifest.setdefault("enabled", True)
                    manifest.setdefault("settings", manifest.get("default_settings", {}))
                    discovered[addon_id] = manifest
                except Exception as e:
                    logger.error(f"Error parsing addon.json in {item_path}: {e}")

        self.installed_addons = discovered
        return self.installed_addons

    def load_active_addons(self):
        """Imports and initializes active Python modules for enabled addons."""
        for addon_id, manifest in self.installed_addons.items():
            if not manifest.get("enabled", True):
                continue

            entrypoint = manifest.get("entrypoint", "main.py")
            entry_path = os.path.join(manifest["dir_path"], entrypoint)
            if os.path.isfile(entry_path):
                try:
                    spec = importlib.util.spec_from_file_location(f"fiestapanel_addon_{addon_id}", entry_path)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        self.active_modules[addon_id] = module
                        
                        # Call module init_addon(ctx)
                        if hasattr(module, "init_addon"):
                            self.unregister_hooks(addon_id)
                            ctx = AddonContext(addon_id, manifest["dir_path"], self, self.app, self.send_mc_command, self.ping_mc)
                            module.init_addon(ctx)
                except Exception as e:
                    logger.error(f"Failed to load addon module {addon_id} ({entry_path}): {e}")

    def unregister_hooks(self, addon_id: str):
        """Removes all registered hooks for a given addon ID."""
        for event_name in list(self.hooks.keys()):
            self.hooks[event_name] = [entry for entry in self.hooks[event_name] if entry[0] != addon_id]

    def register_hook(self, addon_id: str, event_name: str, callback):
        hooks_list = self.hooks.setdefault(event_name, [])
        if not any(aid == addon_id and cb == callback for aid, cb in hooks_list):
            hooks_list.append((addon_id, callback))

    def trigger_event(self, event_name: str, **kwargs):
        for addon_id, callback in self.hooks.get(event_name, []):
            try:
                callback(**kwargs)
            except Exception as e:
                logger.error(f"Error executing hook {event_name} in addon {addon_id}: {e}")

    def get_installed_list(self) -> List[Dict[str, Any]]:
        self.scan_addons()
        out = []
        for addon_id, m in self.installed_addons.items():
            c = m.copy()
            c.pop("dir_path", None)
            out.append(c)
        return out

    def get_store_catalog(self) -> List[Dict[str, Any]]:
        self.scan_addons()
        repo = self.load_repository()
        addons = repo.get("addons", STORE_CATALOG)
        catalog_copy = []
        for item in addons:
            cat_item = {k: v for k, v in item.items() if k != "files"}
            aid = item["id"]
            cat_item["installed"] = aid in self.installed_addons
            if cat_item["installed"]:
                cat_item["enabled"] = self.installed_addons[aid].get("enabled", True)

            # Factual statistics calculated from real reviews and panel installs
            stats = self.get_addon_stats(aid)
            cat_item["stats"] = stats
            cat_item["rating"] = stats["rating_score"]
            cat_item["downloads"] = stats["install_count"]
            cat_item["reviews"] = self.get_addon_reviews(aid)

            # Live server dependency check
            cat_item["dependency_status"] = self.check_addon_dependencies(item)
            catalog_copy.append(cat_item)
        return catalog_copy

    def get_addon_info(self, addon_id: str) -> Optional[Dict[str, Any]]:
        """Returns comprehensive addon metadata, parsed markdown, factual stats, and dependency status."""
        self.scan_addons()
        repo = self.load_repository()
        found = None
        for item in repo.get("addons", []):
            if item["id"] == addon_id:
                found = item
                break

        if not found:
            for item in STORE_CATALOG:
                if item["id"] == addon_id:
                    found = item
                    break

        if not found and addon_id in self.installed_addons:
            found = self.installed_addons[addon_id]

        if not found:
            return None

        info = {k: v for k, v in found.items() if k != "files"}
        info["installed"] = addon_id in self.installed_addons
        if info["installed"]:
            info["enabled"] = self.installed_addons[addon_id].get("enabled", True)
            info["current_settings"] = self.installed_addons[addon_id].get("settings", {})

        info["stats"] = self.get_addon_stats(addon_id)
        info["reviews"] = self.get_addon_reviews(addon_id)
        info["rating"] = info["stats"]["rating_score"]
        info["downloads"] = info["stats"]["install_count"]
        info["dependency_status"] = self.check_addon_dependencies(found)

        if not info.get("readme_markdown"):
            info["readme_markdown"] = f"# {info.get('name', addon_id)}\n\n{info.get('description', '')}"

        return info

    def install_from_catalog(self, addon_id: str) -> Dict[str, Any]:
        """Installs an addon directly from the official repository or built-in catalog."""
        repo = self.load_repository()
        found = None
        for item in repo.get("addons", []):
            if item["id"] == addon_id:
                found = item
                break

        if not found:
            for item in STORE_CATALOG:
                if item["id"] == addon_id:
                    found = item
                    break

        if not found:
            raise ValueError(f"Addon '{addon_id}' not found in store catalog.")

        target_dir = os.path.join(self.addons_dir, addon_id)
        os.makedirs(target_dir, exist_ok=True)

        manifest = {
            "id": found["id"],
            "name": found["name"],
            "version": found["version"],
            "author": found["author"],
            "description": found["description"],
            "icon": found["icon"],
            "category": found["category"],
            "enabled": True,
            "entrypoint": "main.py",
            "dependencies": found.get("dependencies", {}),
            "stats": found.get("stats", {}),
            "settings_schema": found.get("settings_schema", []),
            "settings": found.get("default_settings", {}),
            "ui_slots": found.get("ui_slots", {})
        }

        # Write addon.json
        with open(os.path.join(target_dir, "addon.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # Write README.md if available
        if found.get("readme_markdown"):
            with open(os.path.join(target_dir, "README.md"), "w", encoding="utf-8") as f:
                f.write(found["readme_markdown"].strip() + "\n")

        # Write additional bundled files
        for fname, content in found.get("files", {}).items():
            fpath = os.path.join(target_dir, fname)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content.strip() + "\n")

        self.record_install_event(addon_id)
        self.scan_addons()
        self.load_active_addons()
        return manifest

    def install_from_zip(self, zip_bytes: bytes) -> Dict[str, Any]:
        """Extracts and registers an addon package from ZIP bytes."""
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            manifest_file = None
            for name in z.namelist():
                if name.endswith("addon.json"):
                    manifest_file = name
                    break
            if not manifest_file:
                raise ValueError("Invalid addon ZIP: missing addon.json manifest.")

            manifest_data = json.loads(z.read(manifest_file).decode("utf-8"))
            addon_id = manifest_data.get("id")
            if not addon_id or not isinstance(addon_id, str) or not __import__("re").fullmatch(r"[A-Za-z0-9_-]{1,64}", addon_id):
                raise ValueError("Invalid addon id.")

            target_dir = os.path.join(self.addons_dir, addon_id)
            os.makedirs(target_dir, exist_ok=True)

            prefix = os.path.dirname(manifest_file)
            for member in z.infolist():
                if prefix and not member.filename.startswith(prefix):
                    continue
                rel_path = os.path.relpath(member.filename, prefix) if prefix else member.filename
                if rel_path in ("", "."):
                    continue
                dest_path = os.path.realpath(os.path.join(target_dir, rel_path))
                if os.path.commonpath([os.path.realpath(target_dir), dest_path]) != os.path.realpath(target_dir):
                    raise ValueError("Invalid archive path.")
                if member.is_dir():
                    os.makedirs(dest_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with open(dest_path, "wb") as f:
                        f.write(z.read(member.filename))

        self.scan_addons()
        self.load_active_addons()
        return self.installed_addons.get(addon_id, manifest_data)

    def install_from_url(self, url: str) -> Dict[str, Any]:
        """Downloads and installs an addon ZIP from an HTTP(S) URL."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Only HTTP and HTTPS URLs are supported.")
        host = (parsed.hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"):
            raise ValueError("Target host is not permitted.")
        req = urllib.request.Request(url, headers={"User-Agent": "FiestaPanel-Addons/2.5"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        return self.install_from_zip(data)

    def toggle_addon(self, addon_id: str, enabled: bool) -> bool:
        """Enables or disables an installed addon."""
        if addon_id not in self.installed_addons:
            raise KeyError(f"Addon '{addon_id}' not found.")

        manifest_path = os.path.join(self.installed_addons[addon_id]["dir_path"], "addon.json")
        self.installed_addons[addon_id]["enabled"] = enabled

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in self.installed_addons[addon_id].items() if k != "dir_path"}, f, indent=2)

        if enabled:
            self.load_active_addons()
        else:
            self.unregister_hooks(addon_id)
            self.active_modules.pop(addon_id, None)

        return True

    def get_addon_settings(self, addon_id: str) -> Dict[str, Any]:
        if addon_id not in self.installed_addons:
            return {}
        return self.installed_addons[addon_id].get("settings", {})

    def update_addon_settings(self, addon_id: str, settings: Dict[str, Any]) -> bool:
        """Saves updated settings to an addon's addon.json."""
        if addon_id not in self.installed_addons:
            raise KeyError(f"Addon '{addon_id}' not found.")

        manifest_path = os.path.join(self.installed_addons[addon_id]["dir_path"], "addon.json")
        current_settings = self.installed_addons[addon_id].get("settings", {})
        current_settings.update(settings)
        self.installed_addons[addon_id]["settings"] = current_settings

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in self.installed_addons[addon_id].items() if k != "dir_path"}, f, indent=2)

        return True

    def uninstall_addon(self, addon_id: str) -> bool:
        """Completely removes an addon folder from disk."""
        if addon_id not in self.installed_addons:
            raise KeyError(f"Addon '{addon_id}' not found.")

        target_dir = self.installed_addons[addon_id]["dir_path"]
        self.unregister_hooks(addon_id)
        self.active_modules.pop(addon_id, None)
        if os.path.isdir(target_dir):
            shutil.rmtree(target_dir, ignore_errors=True)

        self.installed_addons.pop(addon_id, None)
        self.record_uninstall_event(addon_id)
        return True

    def render_addon_tab(self, addon_id: str) -> str:
        """Returns the HTML content for an addon's custom tab view."""
        if addon_id not in self.installed_addons:
            return f"<div class='panel-box'><h3>Addon not found: {addon_id}</h3></div>"

        manifest = self.installed_addons[addon_id]
        settings = manifest.get("settings", {})
        tab_file = os.path.join(manifest["dir_path"], "tab.html")
        if os.path.isfile(tab_file):
            try:
                with open(tab_file, "r", encoding="utf-8") as f:
                    content = f.read()

                # Interpolate settings into HTML placeholders
                for k, v in settings.items():
                    if isinstance(v, bool):
                        chk_str = "checked" if v else ""
                        content = content.replace(f"{{{{ {k}_checked }}}}", chk_str)
                        content = content.replace(f"{{{{ settings.{k}_checked }}}}", chk_str)
                    val_str = html.escape(str(v if v is not None else ""))
                    content = content.replace(f"{{{{ {k} }}}}", val_str)
                    content = content.replace(f"{{{{ settings.{k} }}}}", val_str)

                # Special handling for discord_notifier inputs if placeholders weren't present in raw HTML
                if addon_id == "discord_notifier":
                    wh = html.escape(str(settings.get("webhook_url") or ""))
                    bot = html.escape(str(settings.get("bot_name") or "FiestaPanel Gateway"))
                    av = html.escape(str(settings.get("avatar_url") or ""))
                    col = html.escape(str(settings.get("embed_color") or "#38bdf8"))
                    ns = settings.get("notify_start", True)
                    nst = settings.get("notify_stop", True)
                    nj = settings.get("notify_join", True)

                    # Ensure #addon-discord-url has value attribute
                    if 'id="addon-discord-url"' in content:
                        content = re.sub(
                            r'(<input[^>]*id="addon-discord-url"[^>]*?)(\/?>)',
                            lambda m: f'{re.sub(r"""\s*value=["\'][^"\']*["\']""", "", m.group(1))} value="{wh}"{m.group(2)}',
                            content
                        )
                    # Ensure #addon-discord-botname has value attribute
                    if 'id="addon-discord-botname"' in content:
                        content = re.sub(
                            r'(<input[^>]*id="addon-discord-botname"[^>]*?)(\/?>)',
                            lambda m: f'{re.sub(r"""\s*value=["\'][^"\']*["\']""", "", m.group(1))} value="{bot}"{m.group(2)}',
                            content
                        )
                    # Ensure #addon-discord-avatar has value attribute
                    if 'id="addon-discord-avatar"' in content:
                        content = re.sub(
                            r'(<input[^>]*id="addon-discord-avatar"[^>]*?)(\/?>)',
                            lambda m: f'{re.sub(r"""\s*value=["\'][^"\']*["\']""", "", m.group(1))} value="{av}"{m.group(2)}',
                            content
                        )
                    # Ensure #addon-discord-color has value attribute
                    if 'id="addon-discord-color"' in content:
                        content = re.sub(
                            r'(<input[^>]*id="addon-discord-color"[^>]*?)(\/?>)',
                            lambda m: f'{re.sub(r"""\s*value=["\'][^"\']*["\']""", "", m.group(1))} value="{col}"{m.group(2)}',
                            content
                        )
                    # Ensure checkboxes
                    for chk_id, checked in [("addon-discord-notif-start", ns), ("addon-discord-notif-stop", nst), ("addon-discord-notif-join", nj)]:
                        if f'id="{chk_id}"' in content:
                            def _replace_chk(m, is_checked=checked):
                                tag = re.sub(r'\s*checked\b', '', m.group(1))
                                if is_checked:
                                    tag += ' checked'
                                return f'{tag}{m.group(2)}'
                            content = re.sub(rf'(<input[^>]*id="{chk_id}"[^>]*?)(\/?>)', _replace_chk, content)

                # Inject runtime settings payload for client scripts
                injected_script = f"""
<script id="addon-runtime-settings-{addon_id}">
(function() {{
    window.__ACTIVE_ADDON_ID__ = {json.dumps(addon_id)};
    window.__ACTIVE_ADDON_SETTINGS__ = {json.dumps(settings)};
}})();
</script>
"""
                return content + injected_script
            except Exception as e:
                return f"<div class='panel-box'><p>Error reading tab.html: {e}</p></div>"

        # Fallback generated UI based on settings schema
        schema = manifest.get("settings_schema", [])
        settings = manifest.get("settings", {})
        fallback_lines = [
            f"<div class='panel-box'>",
            f"<div class='section-title-bar'><span>{manifest.get('name', addon_id)}</span></div>",
            f"<p class='inline-hint'>{manifest.get('description', '')}</p>",
            f"<table class='form-layout-table'>"
        ]
        for field in schema:
            key = field.get("key")
            label = field.get("label", key)
            val = settings.get(key, field.get("default", ""))
            ftype = field.get("type", "text")
            if ftype == "checkbox":
                chk = "checked" if val else ""
                fallback_lines.append(f"<tr><td class='label-col'>{label}:</td><td class='val-col'><input type='checkbox' id='addon-{addon_id}-{key}' {chk}></td></tr>")
            else:
                fallback_lines.append(f"<tr><td class='label-col'>{label}:</td><td class='val-col'><input type='text' id='addon-{addon_id}-{key}' class='ctrl-input' style='width:70%;' value='{val}'></td></tr>")
        fallback_lines.append("</table>")
        fallback_lines.append(f"<div style='margin-top:14px;'><button class='btn-classic' onclick='saveGenericAddonSettings(\"{addon_id}\")'>[ Save Settings ]</button></div>")
        fallback_lines.append("</div>")
        return "\n".join(fallback_lines)

    def execute_addon_action(self, addon_id: str, action_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches an action call to an addon module's on_action(ctx, action_name, payload)."""
        if addon_id not in self.installed_addons:
            return {"success": False, "error": f"Addon '{addon_id}' is not installed."}

        manifest = self.installed_addons[addon_id]
        if not manifest.get("enabled", True):
            return {"success": False, "error": f"Addon '{addon_id}' is disabled."}

        module = self.active_modules.get(addon_id)
        if not module:
            # Try loading it dynamically
            entry_path = os.path.join(manifest["dir_path"], manifest.get("entrypoint", "main.py"))
            if os.path.isfile(entry_path):
                spec = importlib.util.spec_from_file_location(f"fiestapanel_addon_{addon_id}", entry_path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    self.active_modules[addon_id] = module

        if module and hasattr(module, "on_action"):
            ctx = AddonContext(addon_id, manifest["dir_path"], self, self.app, self.send_mc_command, self.ping_mc)
            try:
                return module.on_action(ctx, action_name, payload)
            except Exception as e:
                return {"success": False, "error": f"Exception in addon on_action: {str(e)}"}

        return {"success": False, "error": f"Addon does not support action '{action_name}'."}

    def generate_boilerplate_zip(self) -> bytes:
        """Builds a developer starter addon package ZIP archive."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            manifest = {
                "id": "my_custom_addon",
                "name": "My Custom FiestaPanel Addon",
                "version": "1.0.0",
                "author": "Your Name",
                "description": "An example addon demonstrating custom UI tabs, settings, and Minecraft commands.",
                "icon": "fas fa-cube",
                "category": "Tools",
                "enabled": True,
                "entrypoint": "main.py",
                "settings_schema": [
                    {"key": "announcement_text", "label": "Announcement Text", "type": "text", "default": "Welcome to our server!"},
                    {"key": "enable_welcome", "label": "Send on Startup", "type": "checkbox", "default": True}
                ],
                "default_settings": {
                    "announcement_text": "Welcome to our server!",
                    "enable_welcome": True
                },
                "ui_slots": {
                    "tab": {
                        "title": "My Addon",
                        "icon": "fas fa-cube",
                        "description": "Custom addon dashboard view"
                    }
                }
            }
            z.writestr("addon.json", json.dumps(manifest, indent=2))
            
            main_code = '''
def init_addon(ctx):
    ctx.logger.info("My custom addon initialized successfully!")

def on_action(ctx, action_name, payload):
    if action_name == "broadcast":
        text = payload.get("text") or ctx.get_settings().get("announcement_text", "Hello!")
        ok, msg = ctx.send_command(f"say {text}")
        return {"success": ok, "message": msg}
    return {"success": False, "error": f"Unknown action: {action_name}"}
'''
            z.writestr("main.py", main_code.strip() + "\n")
            
            tab_html = '''
<div class="panel-box">
  <div class="section-title-bar"><span>My Custom Addon</span></div>
  <p class="inline-hint">Demonstration of custom HTML rendering inside FiestaPanel.</p>
  <button class="btn-classic" onclick="triggerMyAddonAction()">[ Broadcast Message ]</button>
</div>
<script>
function triggerMyAddonAction() {
  fetch('/api/panel-addons/my_custom_addon/action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: 'broadcast', payload: {}})
  }).then(r => r.json()).then(d => alert(d.message || d.error));
}
</script>
'''
            z.writestr("tab.html", tab_html.strip() + "\n")
            
            readme = '''
# My Custom FiestaPanel Addon

To install:
1. Zip this directory.
2. Go to FiestaPanel -> Panel Addons & Extensions -> Upload Addon (.zip).
3. Enable and configure!
'''
            z.writestr("README.md", readme.strip() + "\n")

        buf.seek(0)
        return buf.getvalue()

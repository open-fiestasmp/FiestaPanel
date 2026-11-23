<p align="center">
  <img src="assets/banner.png" alt="FiestaPanel Banner" width="100%">
</p>

<h1 align="center">FiestaPanel</h1>

<p align="center">
  <strong>The Modern & Retro Minecraft Server Management Suite</strong><br>
  Frutiger Aero Aesthetics • Real-Time Telemetry • Visual Inventory Inspector • Modrinth Plugin Manager • Jailed File Manager • Turnkey Deployment
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.9+-brightgreen.svg" alt="Python 3.9+"></a>
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS%20%7C%20Docker-blue.svg" alt="Platform: Cross-Platform">
  <img src="https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker&logoColor=white" alt="Docker Ready">
  <img src="https://img.shields.io/badge/Minecraft-Java%20Edition-success.svg" alt="Minecraft">
</p>

---

<img width="1920" height="880" alt="image" src="https://github.com/user-attachments/assets/d291e8bd-0571-44f4-b81b-5b013db7c885" />
<img width="1920" height="880" alt="image" src="https://github.com/user-attachments/assets/f5d5116c-5ef6-4491-b8c3-4e6de4af9bb1" />

##  Quick Start

On first installation, enter and confirm an admin password of at least **12 characters** when prompted. The input is hidden, the owner username is `admin`, and there is no preset password. All installers and standalone launchers use the same setup: a scrypt hash is stored in `users.json`, and existing accounts are preserved. The owner can change passwords under **Security → Sub-Users & Access**. For unattended setup, set `FIESTA_ADMIN_PASS` instead of using an interactive prompt.

### Linux & macOS (1-Line Install)
```bash
curl -sSL https://raw.githubusercontent.com/open-fiestasmp/FiestaPanel/main/install.sh | sudo bash
```
*(Or via short mirror: `curl -sSL https://fiesta.debarkak.xyz/install.sh | sudo bash`)*

### Windows 10 / 11 / Server (1-Line PowerShell Install)
Open PowerShell (as regular user or Administrator) and run:
```powershell
irm https://raw.githubusercontent.com/open-fiestasmp/FiestaPanel/main/install.ps1 | iex
```
*(From Windows Command Prompt (CMD): `powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/open-fiestasmp/FiestaPanel/main/install.ps1 | iex"`)*  
*(Or via short mirror: `irm https://fiesta.debarkak.xyz/install.ps1 | iex`)*  
*(Manual install: Clone repo and run `install.bat` or double-click `start.bat`)*

### Docker & Docker Compose (Container Install)
Deploy FiestaPanel and your Minecraft server in an isolated container with Java 21 LTS pre-installed:

```bash
# Clone and launch turnkey container
git clone https://github.com/open-fiestasmp/FiestaPanel.git
cd FiestaPanel
read -rs -p "Create admin password (12+ characters): " FIESTA_ADMIN_PASS; echo
export FIESTA_ADMIN_PASS
docker compose up -d
unset FIESTA_ADMIN_PASS
```
*(Or 1-line Docker run)*:
```bash
read -rs -p "Create admin password (12+ characters): " FIESTA_ADMIN_PASS; echo
export FIESTA_ADMIN_PASS
docker run -d --name fiestapanel --restart unless-stopped \
  -e FIESTA_ADMIN_PASS \
  -p 8080:8080 -p 25565:25565 -p 19132:19132/udp \
  -v $(pwd)/minecraft_server:/server \
  -v $(pwd)/data:/app/data \
  ghcr.io/open-fiestasmp/fiestapanel:latest
unset FIESTA_ADMIN_PASS
```
Open **`http://localhost:8080`** in your browser. Complete guide & options in [**INSTALL.md**](INSTALL.md#option-4-docker--docker-compose-containerized).

> [!NOTE]
> **No Pre-installed Server Required**: You do not need to install Minecraft beforehand. You can install and launch **Paper, Purpur, Fabric, NeoForge, Spigot, or Vanilla** in One-Click directly from the web panel without opening SSH or terminal!

---

## Documentation Index

| Guide | Description |
|---|---|
| [**Installation Guide**](INSTALL.md) | Complete step-by-step setup, 1-line Windows/Linux/macOS installers, systemd, and Nginx SSL setup |
| [**Addon Developer Guide**](docs/ADDONS_GUIDE.md) | How to build custom FiestaPanel extensions, UI tabs, Discord webhooks, and submit to the Plugin Store |
|  [**Architecture & Design**](docs/ARCHITECTURE.md) | Deep-dive into process model, daemon control, texture atlas, and data flow |
| [**Feature Tour**](docs/FEATURES.md) | Exhaustive guide covering console, inventory inspector, Modrinth, world controls, etc. |
| [**REST API Reference**](docs/API.md) | Full JSON API specification for headless integrations and automation |
|  [**Security & Best Practices**](docs/SECURITY.md) | scrypt hashing, path traversal defenses, role-based access control, firewall setup |

---

## Features at a Glance

| Feature | Highlights |
|---|---|
| **Dedicated Worlds Hub** | Download world ZIPs, upload world archives, One-Click Nether & The End dimension resets, world generator wizard, and datapacks manager. |
| **Game Options Switchboard** | Instant toggles for Online Mode (offline accounts vs Mojang authentication), PvP, Whitelist, Flight, Command Blocks, Mob Spawning, Hardcore, and Idle Timeout. |
| **Expanded Parameter Editor** | Direct editing for Paper (`paper-global.yml`, `paper-world-defaults.yml`), Purpur, Spigot, Bukkit, Geyser/Floodgate, and all plugin configs with `.bak` safety snapshots. |
| **Log Intelligence & One-Click mclo.gs** | Auto-detects crashes, OOM, port conflicts, and corrupted chunks; One-Click token-sanitized log upload to `mclo.gs`. |
| **Dedicated Banned IPs View** | Blacklisted IP records management, manual IP banning, and instant `/pardon-ip` unban action. |
| **Bedrock One-Click & Mobile QR** | `minecraft://?addExternalServer=...` direct join button and scannable camera QR code for Android, iOS, and Bedrock consoles. |
| **Resource Pack Host & Auto-SHA1** | In-panel `.zip` resource pack upload, static HTTP hosting, and automated SHA-1 generation synchronized with `server.properties`. |
| **Automated Tasks Scheduler** | Scheduled daily server restarts, automated daily world backups, and idle server auto-shutdown when empty. |
| **Full Process Control** | Start, stop, restart, and tail live ANSI logs with top/bottom scroll navigation. |
| **Web Panel Addon System & Store** | Modular Python & HTML extensions with One-Click in-panel Plugin Store (Discord Webhooks, Dynmap/BlueMap viewer, Cron Schedulers, MOTD Styler, Maintenance Gate). |
| **Visual Inventory Inspector** | Real-time armor, offhand, storage & hotbar viewer with authentic 1.21 item textures and giving search tool. |
| **Game Addons (Plugins & Mods)** | Context-aware manager for Bukkit/Spigot/Paper plugins and Fabric/Forge/NeoForge mods with One-Click Modrinth directory installation. |
| **Jailed File Manager** | In-browser code & config editor, directory navigation, and drag-and-drop file uploads with traversal protection. |
| **Sub-Users & Access Control** | Multi-user roles and scrypt password hashing. |
| **Windows 7 Aero & Retro Themes** | Authentic 7.css specular glass reflection, TP-Link legacy, Windows 98, Dark Mode, Matrix, and Cyberpunk. |
| **Complete Brand Customization** | Header icon picker (12 themed presets + local file upload + custom URL/SVG), custom title, hardware model number, and tagline. |

---

## Publishing Plugins & Extensions to the Store

FiestaPanel features an open and modular **Addon & Plugin System** where developers can build and publish custom integrations, tools, dashboards, and visual controls.

### 1. Building Your Plugin
Every FiestaPanel addon is a lightweight folder with:
- **`addon.json`**: Manifest declaring the unique `id`, `name`, `version`, `author`, `category`, FontAwesome `icon`, and optional `settings_schema` (rendered automatically in the web UI).
- **`main.py`**: Backend Python module handling lifecycle hooks (`on_enable`, `on_disable`) and client actions (`on_action`).
- **`tab.html`**: Interactive frontend HTML/JS template dynamically mounted into the panel workspace and navigation sidebar.
- **`README.md`**: Plugin documentation, usage guide, and setup instructions.

Refer to the [**Addon Developer Guide**](docs/ADDONS_GUIDE.md) for full specifications and templates.

### 2. Publishing to the Official In-Panel Plugin Store

To protect all FiestaPanel installations from security vulnerabilities and bugs, direct publishing to the official repository is locked for Fiesta team administrators. Community extensions are published through a verification and approval workflow:

1. **Package Your Addon as a ZIP**:
   Export your addon directly from your server's FiestaPanel UI or compress your folder:
   ```bash
   zip -r my_plugin.zip addon.json main.py tab.html README.md
   ```
2. **Submit Your Package for Review**:
   - **GitHub Pull Request**: Fork [`open-fiestasmp/FiestaPanel`](https://github.com/open-fiestasmp/FiestaPanel) on GitHub and open a Pull Request attaching your `.zip` bundle or adding your files to `addons/repo/repository.json`.
   - **Send via Discord**: Join the official FiestaSMP Discord community and send your `.zip` archive to the core team.
3. **Core Team Review & Approval**:
   - The **FiestaSMP core team will review, security-audit, test, and approve the sent .zip file**.
   - External server administrators cannot publish directly to the global store without this approval.
4. **Global Distribution**:
   - Once approved, the team deploys the addon to the official Plugin Store catalog (`addons/repo/repository.json`).
   - Your addon immediately becomes available for One-Click install across all FiestaPanel instances worldwide under **Panel Extensions / Store -> Plugin Store**!

### 3. Direct Community Distribution (.zip or URL)
If you wish to test your addon or distribute it independently without official store verification:
- Users navigate to **Panel Extensions / Store -> Installed Addons -> Upload Package (.zip)** in their dashboard to install your archive directly.
- Users can also install from a direct download link or drop the folder directly into `addons/<my_plugin>/`.

---

## Forgotten passwords and account recovery

### Owner (`admin`) password

If you have forgotten the owner password, run `reset-admin` on the machine hosting FiestaPanel. The command generates and displays a new password for the `admin` account; keep that output private. It preserves other accounts when the existing `users.json` is valid. The command requires write access to the panel's data directory.

- **Linux/macOS:** run `fiestapanel reset-admin`, or `./fiestapanel reset-admin` from the FiestaPanel directory. Use `sudo` if the data files require it.
- **Windows:** open PowerShell in the FiestaPanel directory and run `.\fiestapanel.bat reset-admin` (in Command Prompt, run `fiestapanel.bat reset-admin`).
- **Docker:** run `docker exec -it fiestapanel fiestapanel reset-admin`. Replace `fiestapanel` with your container name if different.

The command resets or creates the account named `admin`, even if the installation uses a different owner username. It operates on the data directory selected by `FIESTA_DATA_DIR` or `GATEWAY_DATA_DIR`; make sure it is the directory used by your running panel. Back up `users.json` first if you are unsure of its state: the current reset command can replace an unreadable or invalid user file, losing other accounts. For Docker, back up the persistent data volume.

Sign in as `admin` with the generated password, then choose a new password under **Security → Sub-Users & Access → Change Password**. Enter your current owner password and a new password of at least 12 characters. Other sessions for that account will be signed out.

### Sub-user password

Sub-users cannot recover or reset their own passwords. Ask the panel owner to sign in, open **Security → Sub-Users & Access**, and choose **Change Password** next to the affected account. The owner confirms their own current password and enters a new password of at least 12 characters. The sub-user's existing sessions are signed out, and they can then sign in using the new password. Share it through a secure channel.

If the owner has also forgotten their password, recover the owner account first using the steps above, then reset the sub-user's password.

---

## The `fiestapanel` CLI

FiestaPanel installs a system management CLI accessible anywhere:

```bash
fiestapanel status        # Check daemon health and memory usage
fiestapanel restart       # Gracefully restart FiestaPanel
fiestapanel logs          # Tail live systemd journal logs
fiestapanel reset-admin   # Generate a new admin password
fiestapanel autostart enable # Configure auto-start on system boot / logon
fiestapanel set-port 8080 # Update HTTP listener port
fiestapanel set-mc-path   # Switch managed Minecraft server path
fiestapanel info          # Print configuration overview
fiestapanel version       # FiestaPanel Software Suite v2.1.0
```

*(Note: `panelctl` is retained as an alias for backwards compatibility)*

---

##  Themes Supported

- **Windows 7 Aero**: Authentic design powered by [**7.css**](https://github.com/khang-nd/7.css) by Khang NT. Vibrant electric azure Aero Glass frames, specular glass reflection buttons, interactive titlebar controls (minimize/maximize/close), and Segoe UI typography.
- **TP-Link Classic**: Retro 2000s router web interface.
- **Windows 98**: Classic desktop bevels and teal aesthetic.
- **Dark Mode**: High contrast OLED dark styling.
- **Cyberpunk**: Neon cyan and magenta terminal aesthetics.
- **Matrix**: Phosphor green CRT monitor glow.

---

## Acknowledgements & Credits

- [**7.css**](https://github.com/khang-nd/7.css) by **Khang NT** ([@khang-nd](https://github.com/khang-nd)) — Powers the authentic Windows 7 Aero Glass styling, glossy specular highlights, and window control buttons (MIT License).
- [**Modrinth API**](https://modrinth.com) — Powers the built-in online Mod & Plugin search engine and One-Click installations.
- [**PaperMC API**](https://papermc.io) — Real-time upstream server software version detection and binary downloads.

---

## License

This project is licensed under the [MIT License](LICENSE).  
Copyright (c) 2026 FiestaPanel Contributors & Fiesta Team.

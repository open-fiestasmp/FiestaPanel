# FiestaPanel Feature Guide

This guide covers all capabilities, controls, and workflows in **FiestaPanel**.

---

## 1. System Status & Telemetry

The main status view delivers real-time health metrics:
- **Server Health Status**: Live indication whether the Minecraft server process is `ONLINE`, `STARTING`, or `STOPPED`.
- **System Resource Metrics**:
  - CPU utilization percentage.
  - RAM consumption and available system memory.
  - Disk space usage on the server volume.
  - Server process uptime in hours and minutes.
- **Network & Domain**:
  - Displays primary server domain and port.
  - **One-Click Copy**: Copies server address directly to clipboard for sharing with players.
- **World Management Shortcut**:
  - **"Flush & Save World (/save-all)"**: Issues immediate disk synchronization to prevent data loss before maintenance.

---

## 2. Server Console & Terminal

The retro terminal emulation provides deep inspection and control:
- **Real-Time Log Stream**: Displays colorized ANSI logs straight from the server output.
- **Scroll Controls**:
  - Dedicated **"Scroll to Top"** and **"Scroll to Bottom"** buttons for rapid navigation through long logs.
- **Process Controls**:
  - **Start**: Initiates the Minecraft daemon.
  - **Stop**: Issues a graceful server shutdown.
  - **Restart**: Gracefully cycles the server service.
- **Interactive Command Dispatch**:
  - Input bar with command history support (Up/Down arrow navigation).
  - Instant dispatch to server stdin / RCON.

---

## 3. Player Management & Visual Inventory Inspector

A comprehensive toolset for moderating players and viewing their gear:
- **All Players Table**:
  - Lists online and offline players with their UUID, avatar head, ping, and status.
  - Moderation actions: **Kick**, **Ban**, **Ban-IP**, **OP**, **De-OP**, **Whitelist**.
- **Visual Inventory Inspector**:
  - Inspect any player's inventory rendered in authentic Minecraft grid format:
    - **Armor Slots**: Helmet, Chestplate, Leggings, Boots.
    - **Offhand Slot**: Shield, Totem, Arrow, etc.
    - **Main Inventory Grid**: 27 storage slots.
    - **Hotbar**: 9 quick-access slots.
  - **Authentic Item Textures**: High-resolution 16x16 pixel textures extracted from vanilla Minecraft assets.
  - **Hover Tooltips**: Shows item names, lore, stack counts, and NBT tags.
- **Item Giving Tool**:
  - Interactive search bar with texture preview for over 1,300 Minecraft items.
  - Select item, quantity, and target player to grant items without command syntax errors.
- **Gamemode Controls**:
  - Switch any player between **Survival**, **Creative**, **Adventure**, and **Spectator** modes.

---

## 4. Modrinth Plugin Manager

FiestaPanel features built-in search and One-Click installation from Modrinth:
- **Online Catalog Search**: Search thousands of plugins and mods directly from Modrinth's official repository.
- **Compatibility Filtering**: Automatically filters for plugins and mods compatible with your server version and engine (Paper, Purpur, Spigot plugins, or NeoForge, Fabric, Forge mods).
- **One-Click Installation**: Downloads and places JAR files directly into `plugins/` or `mods/` directory.
- **Local Plugin Management**:
  - View installed plugins with file size and active status.
  - Enable or disable plugins (renaming `.jar` to `.jar.disabled`).
  - Delete obsolete plugins with confirmation safeguards.

---

## 5. World Controls & Weather

Control the in-game environment on the fly:
- **Time Controls**:
  - Set time to **Day** (`/time set day`), **Noon** (`/time set noon`), **Night** (`/time set night`), or **Midnight** (`/time set midnight`).
- **Weather Controls**:
  - Switch weather to **Clear** (`/weather clear`), **Rain** (`/weather rain`), or **Thunderstorm** (`/weather thunder`).
- **World Maintenance**:
  - Manual world save (`/save-all flush`).
  - Toggle world autosave (`/save-on` / `/save-off`).

---

## 6. Parameter & Configuration Editor

Edit `server.properties` through both a graphical GUI and raw text mode:
- **Dual Mode**:
  - **Graphical Controls**: Toggle switches and dropdowns for server port, max players, PvP, flight, online-mode, view distance, and difficulty.
  - **Raw Text Mode**: Direct text editor for advanced settings and custom flags.
- **Default Gamemode Selector**:
  - Configure default gamemode for new players: Survival, Creative, Adventure, or Hardcore.
- **Seed Viewer & Editor**: View and update the world generation seed.
- **Server Icon Uploader**: Upload 64x64 PNG server icon (`server-icon.png`) directly through the browser.

---

## 7. File Manager

A full-fledged web file manager isolated to the server root:
- **Directory Navigation**: Browse folders with breadcrumb navigation.
- **File Uploads**: Drag-and-drop or select multiple files with progress bar.
- **In-Browser Code Editor**: Edit configuration files (`config.yml`, `paper-global.yml`, `bukkit.yml`) with syntax highlighting and line numbers.
- **File Actions**: Create new files and folders, rename, download, and delete.
- **Security Jailing**: Enforces path validation to prevent traversal outside the Minecraft server folder.

---

## 8. Sub-Users & Multi-User Access (Like Aternos)

Delegate management tasks to staff members with granular permissions:
- **Admin**: Full access to all panel controls, files, settings, and user management.
- **Operator**: Access to console, player moderation, plugins, and world controls.
- **Viewer**: Read-only monitoring of server status, logs, and player counts.
- Add, edit, and revoke sub-user accounts anytime.

---

## 9. Branding Engine & Theme Studio

Personalize the panel for your server community:
- **Custom Branding**:
  - Change Portal Name (e.g. `MY-NETWORK-CONSOLE`).
  - Change Browser Tab Title.
  - Change Sub-heading / Tagline.
  - Change Hardware Model Number (e.g. `Model No. SRV-ENTERPRISE-X`).
  - Custom Footer Notice.
- **Header Icon Selector**:
  - Choose from 12 built-in icon presets, direct local file upload (PNG, JPG, SVG, WebP, GIF), or custom image/SVG URL.
- **Theme Selection**:
  - **Windows 7 Aero**: Faithfully styled using [**7.css**](https://github.com/khang-nd/7.css) by Khang NT (MIT License). Features vibrant electric azure Aero Glass frames, 3D specular reflection buttons, frosted blur (`backdrop-filter`), authentic interactive titlebar window controls (minimize, maximize/restore, close), and Segoe UI typography.
  - **TP-Link Classic**: Retro 2000s router web interface.
  - **Windows 98**: Classic desktop bevels and teal aesthetic.
  - **Dark Mode**: Modern OLED dark contrast.
  - **Cyberpunk**: Glowing neon teal and magenta.
  - **Matrix**: Phosphor green CRT terminal style.

---

## 10. Dedicated Worlds Hub & One-Click Dimension Resets (Aternos Worlds)

Complete world lifecycle management:
- **World Dimensions Overview**:
  - Live inspection of `world` (Overworld), `world_nether` (Nether), `world_the_end` (The End), and custom worlds.
  - Displays total disk size, region count (`.mca` files), world terrain seed, and last modification date.
- **One-Click Dimension Resets**:
  - **Reset Nether (DIM-1)**: Safely creates an automated snapshot backup, clears region chunks, fortress data, and entity state while leaving the Overworld pristine.
  - **Reset The End (DIM1)**: Resets End cities, dragon fight state, and islands, generating a fresh Ender Dragon battle.
- **World Archive Upload & Download**:
  - Download any world folder as a compressed `.zip` archive.
  - Upload `.zip` world archives with pre-upload safety snapshot backups and automated `level.dat` discovery.
- **World Generation Wizard**:
  - Configure World Name, Seed (with randomize button), World Type (`minecraft:normal`, `minecraft:flat`, `minecraft:large_biomes`, `minecraft:amplified`), Structures, and Hardcore mode.
- **World Chunk Optimizer**:
  - Flush all chunks to disk and prepare server for `--forceUpgrade --eraseCache` conversion.
- **Datapacks Manager**:
  - Manage installed datapacks in `/world/datapacks/`.
  - Upload `.zip` datapack packages with automatic inspection of `pack.mcmeta` description and format.
  - Enable, disable, and delete datapacks.

---

## 11. Game Options & Gameplay Switchboard (Aternos Options)

A responsive switchboard for all Minecraft server gameplay flags:
- **Online Mode Toggle**: Switch between official Mojang account authentication and offline accounts managed through AuthMe.
- **Player vs Player (PvP)**: Enable or disable combat between players.
- **White List**: Enforce whitelist access with One-Click.
- **Flight & Command Blocks**: Allow creative flight without kick warnings, and toggle command block automation.
- **Mob Spawning Controls**: Individual toggle switches for hostile monsters, passive animals, and villagers/NPCs.
- **Nether Dimension**: Enable or disable Nether portals.
- **Hardcore & Gamemode**: Configure permadeath and default join game modes.
- **Distances & Protection**: Fine-tune spawn protection radius, view distance, simulation distance, player slot capacities, and idle kick timeout.

---

## 12. Expanded Parameter Configuration Editor

Deep multi-file configuration editor for server engine and plugin parameters:
- **Supported Engines & Configs**:
  - `server.properties`
  - `paper-global.yml` & `paper-world-defaults.yml`
  - `purpur.yml`, `spigot.yml`, `bukkit.yml`
  - `commands.yml`, `permissions.yml`, `help.yml`
  - Geyser Bedrock bridge (`Geyser-Spigot/config.yml`, `floodgate/config.yml`)
  - Permissions, rollbacks, and utilities (`LuckPerms`, `CoreProtect`, `Chunky`, `WorldEdit`, `ViaVersion`, `ViaBackwards`, `ViaRewind`, `Vault`)
  - Essentials suite (`config.yml`, `spawn.yml`, `kits.yml`, `custom_items.yml`, `worth.yml`)
  - Security, authentication, and gameplay plugins (`AuthMe`, `TAB`, `DeluxeMenus`, `GriefPreventionData`, `UltimateAntiBot`, `ChestShop`, `SkinsRestorer`, `DecentHolograms`, `EzAuction`, `PlaceholderAPI`)
- **Dynamic Config Discovery**: Auto-scans all plugin directories for `.yml`, `.yaml`, `.properties`, and `.json` configuration files.
- **Automated `.bak` Snapshots**: Creates a timestamped `.bak` snapshot before every save.

---

## 13. Log Intelligence & One-Click mclo.gs Diagnostic Sharing

Intelligent diagnostics and sharing for server logs:
- **One-Click Share to mclo.gs**: Uploads server log directly to the official `mclo.gs` paste API for easy support sharing.
- **Zero-Exposure Sanitization**: Automatically scrubs and redacts all IPv4/IPv6 addresses, Discord webhook tokens, RCON passwords, database credentials, and secret keys before upload.
- **Automated Crash & Error Intelligence**:
  - Scans `logs/latest.log` for Java OutOfMemoryError (OOM), port binding conflicts, corrupted region chunks, tick loop overload, and plugin load failures.
  - Highlights exact error descriptions and recommended fixes.

---

## 14. Dedicated Banned IPs Blacklist View

Dedicated interface for managing IP bans:
- Inspect active IP bans stored in `/banned-ips.json`.
- Displays banned IP, associated player username, created date, and ban reason.
- One-Click **Pardon IP** action that executes native `/pardon-ip` and synchronizes the blacklist.
- Ban new IP addresses directly from the panel.

---

## 15. Bedrock One-Click Join URL & Scannable Mobile QR Code

Frictionless cross-play connection for mobile and tablet players:
- **One-Click Bedrock Link**: `minecraft://?addExternalServer=...` link opens Minecraft Bedrock Edition and pre-fills server connection settings.
- **Scannable QR Code**: Point a mobile device camera at the screen to join the server without manually typing hostnames or ports.

---

## 16. Resource Pack Host & Auto-SHA1 Generator

Effortless server-wide texture and resource pack deployment:
- Upload any `.zip` resource pack directly into FiestaPanel.
- Panel hosts the pack statically via internal HTTP endpoints.
- Automatically calculates cryptographic SHA-1 hash (`hashlib.sha1`).
- Directly updates `resource-pack`, `resource-pack-sha1`, and `require-resource-pack` in `server.properties`.

---

## 17. Automated Tasks & Autonomous Lifecycle Scheduler

Autonomous server routines running in a background daemon thread:
- **Scheduled Daily Restart**: Configurable 24-hour time (e.g. `04:00` AM) with in-game countdown broadcast, `/save-all flush`, and graceful restart.
- **Automated Daily World Backup**: Configurable time (e.g. `05:00` AM) to snapshot all world data into `/home/ubuntu/backups/`.
- **Idle Server Auto-Shutdown**: Monitors online player counts and safely stops the server after a configurable idle period (15m to 120m) with 0 players to conserve host resources.

---

## Credits & Acknowledgements

- **7.css** by [Khang NT](https://github.com/khang-nd) ([@khang-nd/7.css](https://github.com/khang-nd/7.css)) - Authentic design system and styles for Windows 7 Aero Glass components and controls.
- **Modrinth** ([modrinth.com](https://modrinth.com)) - Public open-source Minecraft mod and plugin repository API.
- **PaperMC** ([papermc.io](https://papermc.io)) - High-performance Minecraft server software API and version builds.


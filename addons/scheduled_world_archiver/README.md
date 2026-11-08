# Scheduled World Archiver & Multi-Cloud Publisher

> [!NOTE]
> Designed for Minecraft server administrators and content creators. The **Scheduled World Archiver & Multi-Cloud Publisher** packages world dimensions (`world`, `world_nether`, `world_the_end`) into fast-compressed zip packages, publishes them to **GitHub Releases**, **Internet Archive (`archive.org`)**, or **Cloudflare R2 / AWS S3**, and coordinates releases with any schedule or calendar.

---

## Key Features

- **Multi-Purpose Workflows**:
  - **SMP Episodes**: Pre-recording and post-recording snapshot buttons with auto-incrementing episode numbering.
  - **Server Milestones & Boss Fights**: Ender Dragon victory, Wither battles, mega-build completions, UHC tournaments, and season resets.
  - **Automated Public World Releases**: Weekly, monthly, or event-driven world downloads for players and viewers.
  - **Server Maintenance & Upgrades**: Clean snapshots before installing new plugins, mods, or engine updates.
  - **General / Custom Snapshots**: Flexible tags, titles, and release changelogs.

- **Safe Minecraft Flushing**:
  - Triggers `save-all flush` in-game prior to scanning and zipping, ensuring no player inventories or chunk modifications in RAM are lost.

- **Optimized Zlib Compression**:
  - Packs multi-gigabyte world directories rapidly with `compresslevel=1`, eliminating server CPU spikes and maintaining high tick rates (TPS).

- **Multi-Destination Cloud Publishing**:
  - **GitHub Releases**: Directly creates releases and streams world zip packages into GitHub release assets with public download links (`https://github.com/owner/repo/releases/download/...`).
  - **Internet Archive (`archive.org`)**: S3-compatible chunked streaming uploads to `archive.org/download/...`.
  - **Custom S3 / Cloudflare R2 / AWS S3**: Multipart S3 uploads with custom CDN download URLs.
  - **Custom HTTP/Webhook Endpoints**: Supports POSTing to custom remote file storage services.
  - **Local Storage**: Built-in direct download server route via FiestaPanel.

- **Any Schedule Given**:
  - **One-off Release Dates/Times**: Specify any exact release time (`datetime-local`).
  - **Calendar Synchronization**: Connects to the FiestaPanel Event Manager and Google Calendar iCal feeds (`.ics`).
  - **Automated Recurring Snapshots**: Set recurring background intervals: Every 6h, 12h, 24h, or 7d.
  - **Automated Unlocking & Announcements**: World download links stay locked until the release time arrives, then automatically mark as **RELEASED / PUBLIC**, announce in Minecraft chat via rich `/tellraw` with clickable download links, and post an embed to Discord.

---

## Setup & Cloud Credentials

1. Open FiestaPanel -> **Scheduled World Archiver** -> click **[ Cloud Keys & Schedules ]**.
2. **GitHub Releases Setup**:
   - Provide a GitHub Personal Access Token (with `repo` or `contents:write` scope).
   - Enter your target repository: `owner/repo` (e.g. `open-fiestasmp/world-downloads`).
   - Click **[ Test GitHub Connection ]** to verify write access.
3. **Internet Archive Setup**:
   - Provide S3 Access & Secret Keys from [archive.org/account/s3.php](https://archive.org/account/s3.php).
   - Click **[ Test Archive.org ]** to verify connectivity.
4. **Cloudflare R2 / AWS S3 Setup**:
   - Enter your S3 Endpoint URL, Bucket Name, and Access/Secret keys.
   - Click **[ Test S3 Connection ]** to verify bucket access.
5. Click **[ Save Settings ]**.

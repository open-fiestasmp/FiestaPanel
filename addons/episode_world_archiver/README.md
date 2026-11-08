# Episode World Archiver & Archive.org Publisher

> [!NOTE]
> Designed for SMP YouTube/Twitch creators. The Episode World Archiver packages world files (`world`, `world_nether`, and `world_the_end`) into fast-compressed zip packages, uploads them to the Internet Archive (`archive.org`), and links public world download releases with Google Calendar and the FiestaPanel Event Manager.

---

## Key Features

- **Pre-Episode & Post-Episode Snapshots**: One-click **[ Take Pre-Episode Snapshot (Before) ]** and **[ Take Post-Episode Snapshot (After) ]** buttons.
- **Safe Server Flushing**: Issues `save-all flush` to the Minecraft server before scanning and compressing to ensure no player inventory or block edits in RAM are lost.
- **Fast Chunked Zipping**: Uses optimized zlib compression (`compresslevel=1`) to rapidly pack multi-gigabyte Minecraft worlds without causing TPS drops or excessive CPU load.
- **Internet Archive S3 Cloud Storage**: Streams world zip archives directly to your Internet Archive (`archive.org`) collection via standard S3 API credentials. Provides direct, high-bandwidth public download links (`https://archive.org/download/...`).
- **Google Calendar & Event Manager Integration**: Link episode archives to upcoming scheduled events in your FiestaPanel Event Manager or Google Calendar iCal feed (`.ics`).
- **Automated Scheduled Releases**: World download links stay locked until the scheduled premiere date & time, then automatically unlock to **RELEASED / PUBLIC**.
- **Minecraft Chat & Discord Broadcasts**: Dispatches rich `/tellraw` chat announcements in-game with clickable download links, as well as Discord webhook embed announcements upon release.

---

## Setup & Archive.org S3 Keys

1. Create a free account at [archive.org](https://archive.org).
2. Generate your S3 API keys at [archive.org/account/s3.php](https://archive.org/account/s3.php).
3. In FiestaPanel, open the **Episode World Archiver** tab and click **[ Settings & Cloud Keys ]**.
4. Paste your S3 Access Key and Secret Key, then click **[ Test Keys ]** to verify connectivity.
5. (Optional) Enter your Google Calendar iCal / ICS feed URL and Discord announcement webhook URL.
6. Click **[ Save Settings ]**.

---

## How It Works

```
1. Before Recording: Click [ Take Pre-Episode Snapshot (Before) ]
   └── save-all flush -> zips world -> registers fiestasmp_ep01_pre_YYYYMMDD_HHMMSS.zip

2. Record your SMP Episode!

3. After Recording: Click [ Take Post-Episode Snapshot (After) ]
   └── save-all flush -> zips world -> registers fiestasmp_ep01_post_YYYYMMDD_HHMMSS.zip

4. Upload & Link to Premiere:
   └── Auto or manual upload to archive.org
   └── Set scheduled release time (e.g., matching YouTube/Twitch premiere)

5. Premiere Date/Time arrives:
   └── Background scheduler marks download as RELEASED / PUBLIC
   └── In-game tellraw announcement with clickable download link
   └── Discord webhook announcement dispatched
```

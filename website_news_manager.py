"""
Website News & Updates Studio Manager for FiestaSMP.
Provides complete headless CMS capabilities for the official Hugo website:
- Read, create, edit, and delete Markdown posts with YAML frontmatter.
- Live rebuild via Hugo and automated RSS feed synchronization (index.xml, rss.xml, feed.xml).
- Media uploads and library browser for article screenshots and banners.
- Automated Git deployment to GitHub Pages via gh auth credentials.
"""

import os
import re
import glob
import time
import shutil
import datetime
import subprocess
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("fiesta.website_news")

class WebsiteNewsManager:
    def __init__(self, repo_dir: Optional[str] = None):
        self.repo_dir = repo_dir or os.environ.get("FIESTA_WEBSITE_DIR") or "/home/ubuntu/open-fiestasmp.github.io"
        self.posts_dir = os.path.join(self.repo_dir, "content", "posts")
        self.news_dir = os.path.join(self.repo_dir, "news")
        self.media_dir = os.path.join(self.repo_dir, "assets", "img", "news")
        self.gallery_dir = os.path.join(self.repo_dir, "assets", "gallery")
        self.img_dir = os.path.join(self.repo_dir, "assets", "img")

        os.makedirs(self.posts_dir, exist_ok=True)
        os.makedirs(self.media_dir, exist_ok=True)

    # ── Slug & Date Utilities ────────────────────────────────────────────────
    @staticmethod
    def slugify(text: str) -> str:
        text = str(text or "").strip().lower()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[\s_-]+", "-", text)
        return text.strip("-") or "untitled-post"

    @staticmethod
    def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
        """Parses YAML frontmatter enclosed in --- delimiters."""
        if not content.startswith("---"):
            return {}, content

        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content

        raw_yaml = parts[1]
        body = parts[2].lstrip("\r\n")

        meta: Dict[str, Any] = {}
        for line in raw_yaml.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                val = val.strip()

                # Handle booleans
                if val.lower() == "true":
                    meta[key] = True
                elif val.lower() == "false":
                    meta[key] = False
                # Handle lists like ["tag1", "tag2"]
                elif val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1].strip()
                    items = [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
                    meta[key] = items
                # Handle quoted strings
                elif (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    meta[key] = val[1:-1]
                else:
                    meta[key] = val

        return meta, body

    @staticmethod
    def build_frontmatter(meta: Dict[str, Any], body: str) -> str:
        """Constructs clean YAML frontmatter and combines it with the body."""
        lines = ["---"]
        title_escaped = str(meta.get("title", "")).replace('"', '\\"')
        lines.append(f'title: "{title_escaped}"')
        
        # Date formatting
        date_val = meta.get("date")
        if isinstance(date_val, datetime.datetime):
            date_str = date_val.strftime("%Y-%m-%dT%H:%M:%S%z")
            if len(date_str) > 2 and date_str[-2] != ":":
                date_str = f"{date_str[:-2]}:{date_str[-2:]}"
        else:
            date_str = str(date_val or datetime.datetime.now().astimezone().isoformat())
        lines.append(f'date: {date_str}')

        lines.append(f'draft: {"true" if meta.get("draft") else "false"}')
        if meta.get("slug"):
            lines.append(f'slug: "{meta["slug"]}"')
        if meta.get("author"):
            lines.append(f'author: "{meta["author"]}"')
        if meta.get("coauthor"):
            lines.append(f'coauthor: "{meta["coauthor"]}"')
        if meta.get("summary"):
            summary_clean = str(meta["summary"]).replace('"', '\\"').replace("\n", " ").strip()
            lines.append(f'summary: "{summary_clean}"')
        if meta.get("tags"):
            tags = meta["tags"]
            if isinstance(tags, list):
                tag_str = ", ".join(f'"{t}"' for t in tags)
                lines.append(f'tags: [{tag_str}]')
            else:
                lines.append(f'tags: ["{tags}"]')

        lines.append("---")
        lines.append("")
        lines.append(body.strip())
        lines.append("")
        return "\n".join(lines)

    # ── Article CRUD Operations ──────────────────────────────────────────────
    def list_articles(self) -> List[Dict[str, Any]]:
        """Lists all news articles sorted by date descending."""
        articles = []
        md_files = glob.glob(os.path.join(self.posts_dir, "*.md"))
        for fpath in md_files:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                meta, body = self.parse_frontmatter(content)
                fname = os.path.basename(fpath)
                slug = meta.get("slug") or os.path.splitext(fname)[0]

                # Extract first paragraph if no summary
                summary = meta.get("summary")
                if not summary and body:
                    for para in body.split("\n\n"):
                        clean_para = para.strip().replace("#", "").strip()
                        if clean_para:
                            summary = clean_para[:180] + ("..." if len(clean_para) > 180 else "")
                            break

                mtime = os.path.getmtime(fpath)
                size = os.path.getsize(fpath)

                articles.append({
                    "filename": fname,
                    "slug": slug,
                    "title": meta.get("title") or slug.replace("-", " ").title(),
                    "date": meta.get("date") or datetime.datetime.fromtimestamp(mtime).isoformat(),
                    "author": meta.get("author") or "FiestaSMP Team",
                    "coauthor": meta.get("coauthor") or "",
                    "summary": summary or "",
                    "tags": meta.get("tags") or [],
                    "draft": bool(meta.get("draft", False)),
                    "word_count": len(body.split()),
                    "mtime": mtime,
                    "file_size": size,
                    "url": f"/news/{slug}/"
                })
            except Exception as e:
                logger.error(f"Error parsing article {fpath}: {e}")

        # Sort by date descending
        articles.sort(key=lambda a: str(a.get("date", "")), reverse=True)
        return articles

    def get_article(self, slug: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single article by slug or filename."""
        slug = slug.strip()
        fpath = os.path.join(self.posts_dir, f"{slug}.md")
        if not os.path.isfile(fpath):
            # Check by iterating files in case filename != slug
            for p in glob.glob(os.path.join(self.posts_dir, "*.md")):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        meta, body = self.parse_frontmatter(f.read())
                    if meta.get("slug") == slug or os.path.splitext(os.path.basename(p))[0] == slug:
                        fpath = p
                        break
                except Exception:
                    continue

        if not os.path.isfile(fpath):
            return None

        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()

        meta, body = self.parse_frontmatter(content)
        fname = os.path.basename(fpath)
        actual_slug = meta.get("slug") or os.path.splitext(fname)[0]

        return {
            "filename": fname,
            "slug": actual_slug,
            "title": meta.get("title", ""),
            "date": meta.get("date", ""),
            "author": meta.get("author", "cbladeOfficial"),
            "coauthor": meta.get("coauthor", "debarkak"),
            "summary": meta.get("summary", ""),
            "tags": meta.get("tags", []),
            "draft": bool(meta.get("draft", False)),
            "content": body,
            "url": f"/news/{actual_slug}/"
        }

    def save_article(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Creates or updates an article, re-compiles with Hugo, and updates RSS."""
        title = str(data.get("title") or "").strip()
        if not title:
            return {"success": False, "error": "Article title cannot be empty."}

        slug = self.slugify(data.get("slug") or title)
        orig_slug = str(data.get("orig_slug") or "").strip()

        date_val = data.get("date")
        if not date_val:
            date_val = datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
            if len(date_val) > 2 and date_val[-2] != ":":
                date_val = f"{date_val[:-2]}:{date_val[-2:]}"

        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        meta = {
            "title": title,
            "slug": slug,
            "date": date_val,
            "author": str(data.get("author") or "cbladeOfficial").strip(),
            "coauthor": str(data.get("coauthor") or "").strip(),
            "summary": str(data.get("summary") or "").strip(),
            "tags": tags,
            "draft": bool(data.get("draft", False))
        }

        body = str(data.get("content") or "").strip()
        full_markdown = self.build_frontmatter(meta, body)

        # Handle rename / slug change
        if orig_slug and orig_slug != slug:
            old_path = os.path.join(self.posts_dir, f"{orig_slug}.md")
            if os.path.isfile(old_path):
                try:
                    os.remove(old_path)
                except Exception as e:
                    logger.warning(f"Could not remove old file {old_path}: {e}")
            old_compiled = os.path.join(self.news_dir, orig_slug)
            if os.path.isdir(old_compiled):
                try:
                    shutil.rmtree(old_compiled, ignore_errors=True)
                except Exception:
                    pass

        new_path = os.path.join(self.posts_dir, f"{slug}.md")
        with open(new_path, "w", encoding="utf-8") as f:
            f.write(full_markdown)

        # Rebuild static site & sync RSS
        build_res = self.rebuild()

        return {
            "success": True,
            "slug": slug,
            "filename": f"{slug}.md",
            "url": f"/news/{slug}/",
            "build": build_res
        }

    def delete_article(self, slug: str) -> Dict[str, Any]:
        """Deletes an article file and its compiled directory, then rebuilds site."""
        slug = slug.strip()
        fpath = os.path.join(self.posts_dir, f"{slug}.md")

        deleted = False
        if os.path.isfile(fpath):
            os.remove(fpath)
            deleted = True
        else:
            # Search by slug inside file
            for p in glob.glob(os.path.join(self.posts_dir, "*.md")):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        meta, _ = self.parse_frontmatter(f.read())
                    if meta.get("slug") == slug:
                        os.remove(p)
                        deleted = True
                        break
                except Exception:
                    continue

        if not deleted:
            return {"success": False, "error": f"Article with slug '{slug}' not found."}

        # Remove compiled output directory if present
        compiled_dir = os.path.join(self.news_dir, slug)
        if os.path.isdir(compiled_dir):
            shutil.rmtree(compiled_dir, ignore_errors=True)

        build_res = self.rebuild()
        return {"success": True, "message": f"Article '{slug}' deleted successfully.", "build": build_res}

    # ── Site Build & RSS Synchronization ─────────────────────────────────────
    def rebuild(self) -> Dict[str, Any]:
        """Executes Hugo build and synchronizes RSS feed files."""
        try:
            start_time = time.time()
            proc = subprocess.run(
                ["hugo"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=False
            )
            duration = round((time.time() - start_time) * 1000, 1)

            # Sync RSS feeds
            src_xml = os.path.join(self.news_dir, "index.xml")
            if os.path.isfile(src_xml):
                for dest in [
                    os.path.join(self.news_dir, "rss.xml"),
                    os.path.join(self.repo_dir, "rss.xml"),
                    os.path.join(self.repo_dir, "feed.xml")
                ]:
                    try:
                        shutil.copyfile(src_xml, dest)
                    except Exception as e:
                        logger.warning(f"Error copying RSS to {dest}: {e}")

            return {
                "success": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "duration_ms": duration,
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        except Exception as e:
            logger.error(f"Failed to execute Hugo: {e}")
            return {
                "success": False,
                "returncode": -1,
                "stdout": "",
                "stderr": str(e),
                "duration_ms": 0,
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

    # ── Git Deployment ───────────────────────────────────────────────────────
    def deploy_to_github(self, message: Optional[str] = None) -> Dict[str, Any]:
        """Builds static site, commits changes, and pushes to origin/main."""
        build_res = self.rebuild()
        if not build_res["success"]:
            return {
                "success": False,
                "error": "Hugo build failed prior to Git push.",
                "build": build_res
            }

        if not message:
            message = f"News updates: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        try:
            # 1. git add .
            subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)

            # 2. Check if there are changes to commit
            status_proc = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo_dir, capture_output=True, text=True, check=True)
            if not status_proc.stdout.strip():
                return {
                    "success": True,
                    "message": "Site is already up-to-date with GitHub. Nothing new to commit.",
                    "build": build_res
                }

            # 3. git commit
            commit_proc = subprocess.run(["git", "commit", "-m", message], cwd=self.repo_dir, capture_output=True, text=True, check=True)

            # 4. git push origin main
            push_proc = subprocess.run(["git", "push", "origin", "main"], cwd=self.repo_dir, capture_output=True, text=True, check=True)

            return {
                "success": True,
                "message": "Successfully compiled Hugo and published news updates to GitHub Pages!",
                "commit_output": commit_proc.stdout,
                "push_output": push_proc.stdout,
                "build": build_res
            }
        except subprocess.CalledProcessError as e:
            return {
                "success": False,
                "error": f"Git command failed: {e.cmd}",
                "stdout": e.stdout if hasattr(e, "stdout") else "",
                "stderr": e.stderr if hasattr(e, "stderr") else str(e)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Media Library & Uploads ──────────────────────────────────────────────
    def list_media(self) -> List[Dict[str, Any]]:
        """Scans website image folders and returns media items."""
        media = []
        patterns = [
            (self.media_dir, "/assets/img/news/"),
            (self.img_dir, "/assets/img/"),
            (self.gallery_dir, "/assets/gallery/"),
            (os.path.join(self.img_dir, "avatars"), "/assets/img/avatars/")
        ]

        seen_paths = set()
        for folder, url_prefix in patterns:
            if not os.path.isdir(folder):
                continue
            for fname in os.listdir(folder):
                fpath = os.path.join(folder, fname)
                if not os.path.isfile(fpath):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext not in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"]:
                    continue
                if fpath in seen_paths:
                    continue
                seen_paths.add(fpath)

                size = os.path.getsize(fpath)
                mtime = os.path.getmtime(fpath)
                media.append({
                    "filename": fname,
                    "url": f"{url_prefix}{fname}",
                    "size": size,
                    "size_str": f"{round(size / 1024, 1)} KB" if size < 1048576 else f"{round(size / 1048576, 2)} MB",
                    "folder": os.path.basename(folder),
                    "mtime": mtime
                })

        media.sort(key=lambda m: m["mtime"], reverse=True)
        return media

    def save_media_file(self, file_storage, filename: Optional[str] = None) -> Dict[str, Any]:
        """Saves an uploaded image into assets/img/news/."""
        orig_name = filename or getattr(file_storage, "filename", "image.png")
        orig_name = os.path.basename(orig_name)
        ext = os.path.splitext(orig_name)[1].lower()
        if ext not in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"]:
            return {"success": False, "error": f"Unsupported media extension '{ext}'. Only images are allowed."}

        # Clean filename
        clean_stem = self.slugify(os.path.splitext(orig_name)[0])
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        target_name = f"{clean_stem}_{timestamp}{ext}"
        target_path = os.path.join(self.media_dir, target_name)

        try:
            if hasattr(file_storage, "save"):
                file_storage.save(target_path)
            elif hasattr(file_storage, "read"):
                with open(target_path, "wb") as f:
                    f.write(file_storage.read())
            else:
                return {"success": False, "error": "Invalid file storage object."}

            url = f"/assets/img/news/{target_name}"
            return {
                "success": True,
                "filename": target_name,
                "url": url,
                "markdown": f"![{clean_stem.replace('-', ' ').title()}]({url})",
                "shortcode": f'{{{{< media-image src="{url}" caption="{clean_stem.replace("-", " ").title()}" >}}}}'
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to save image: {e}"}

    def get_status(self) -> Dict[str, Any]:
        """Retrieves CMS status metrics."""
        articles = self.list_articles()
        media = self.list_media()

        # Check git status
        git_clean = True
        git_branch = "main"
        uncommitted_files = 0
        try:
            status_proc = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo_dir, capture_output=True, text=True, check=False)
            uncommitted = [l for l in status_proc.stdout.splitlines() if l.strip()]
            uncommitted_files = len(uncommitted)
            git_clean = (uncommitted_files == 0)

            branch_proc = subprocess.run(["git", "branch", "--show-current"], cwd=self.repo_dir, capture_output=True, text=True, check=False)
            git_branch = branch_proc.stdout.strip() or "main"
        except Exception:
            pass

        return {
            "total_articles": len(articles),
            "total_media": len(media),
            "git_clean": git_clean,
            "git_branch": git_branch,
            "uncommitted_files": uncommitted_files,
            "rss_url": "https://open-fiestasmp.github.io/news/index.xml",
            "rss_alias": "https://open-fiestasmp.github.io/news/rss.xml",
            "website_url": "https://open-fiestasmp.github.io/",
            "news_url": "https://open-fiestasmp.github.io/news/"
        }

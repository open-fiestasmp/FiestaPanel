import os
import platform
import tarfile
import tempfile
import subprocess
import time
import json
import shutil
import psutil
import glob
import sqlite3
import datetime
import socket
import struct
import threading
import urllib.request
import urllib.parse
import nbtlib
import io
import re
import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import zipfile
import gzip
from PIL import Image
from functools import wraps
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, send_file, send_from_directory, Response, has_request_context
from addon_manager import AddonManager
from cloud_backups import CloudBackupManager
from website_news_manager import WebsiteNewsManager
import autostart

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("FIESTA_DATA_DIR") or os.environ.get("GATEWAY_DATA_DIR") or BASE_DIR
os.makedirs(DATA_DIR, exist_ok=True)

def _get_secret_key():
    env_key = os.environ.get("FIESTA_SECRET_KEY")
    if env_key:
        return env_key.encode("utf-8")
    key_file = os.path.join(DATA_DIR, ".secret_key")
    if os.path.isfile(key_file):
        try:
            with open(key_file, "rb") as f:
                k = f.read().strip()
                if len(k) >= 32:
                    return k
        except Exception:
            pass
    k = os.urandom(32)
    try:
        with open(key_file, "wb") as f:
            f.write(k)
        os.chmod(key_file, 0o600)
    except Exception:
        pass
    return k

app = Flask(__name__)
app.secret_key = _get_secret_key()
_is_cookie_secure = os.environ.get("SESSION_COOKIE_SECURE", "").lower() in ("1", "true")
app.config.update(
    SESSION_COOKIE_SECURE=_is_cookie_secure,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=2048 * 1024 * 1024
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

DEFAULT_SPAWN_COORDINATES = {
    "x": 0,
    "y": 64,
    "z": 0,
    "dimension": "minecraft:overworld",
    "radius": 0
}

def load_server_config():
    is_docker = os.environ.get("DOCKER") == "1" or os.path.exists("/.dockerenv")
    user_home = os.path.expanduser("~")
    local_fiesta = os.path.join(user_home, "fiesta")
    local_mc = os.path.join(user_home, "minecraft")
    if is_docker:
        def_server_dir = "/server"
    elif os.environ.get("MINECRAFT_DIR"):
        def_server_dir = os.environ.get("MINECRAFT_DIR")
    elif os.path.isdir(local_fiesta):
        def_server_dir = local_fiesta
    elif os.path.isdir(local_mc):
        def_server_dir = local_mc
    elif os.path.isdir("/home/ubuntu/fiesta"):
        def_server_dir = "/home/ubuntu/fiesta"
    else:
        def_server_dir = local_mc

    local_backups = os.path.join(user_home, "backups")
    if os.environ.get("BACKUP_DIR"):
        def_backup_dir = os.environ.get("BACKUP_DIR")
    elif os.path.isdir(local_backups):
        def_backup_dir = local_backups
    elif os.path.isdir("/home/ubuntu/backups"):
        def_backup_dir = "/home/ubuntu/backups"
    else:
        def_backup_dir = os.path.join(os.path.dirname(def_server_dir), "backups")

    def_svc = "fiesta" if (os.path.isdir(local_fiesta) or os.path.isdir("/home/ubuntu/fiesta")) else "minecraft"
    cfg = {
        "admin_user": "admin",
        "admin_pass": None,
        "server_dir": def_server_dir,
        "service_name": def_svc,
        "screen_session": def_svc,
        "host": "0.0.0.0" if is_docker else "127.0.0.1",
        "port": 8080,
        "autostart": True,
        "backup_dir": def_backup_dir,
        "spawn_coordinates": DEFAULT_SPAWN_COORDINATES.copy()
    }
    target_cfg = CONFIG_FILE
    if not os.path.exists(target_cfg) and DATA_DIR != BASE_DIR and os.path.exists(os.path.join(BASE_DIR, "config.json")):
        target_cfg = os.path.join(BASE_DIR, "config.json")
    if os.path.exists(target_cfg):
        try:
            with open(target_cfg, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    cfg.update(loaded)
        except Exception:
            pass

    for prefix in ["FIESTA_", "GATEWAY_", ""]:
        u = os.environ.get(f"{prefix}ADMIN_USER")
        if u:
            cfg["admin_user"] = u
        p = os.environ.get(f"{prefix}ADMIN_PASS") or os.environ.get(f"{prefix}ADMIN_PASSWORD")
        if p:
            cfg["admin_pass"] = p
        sdir = os.environ.get(f"{prefix}SERVER_DIR") or os.environ.get(f"{prefix}MC_DIR") or os.environ.get(f"{prefix}MC_PATH")
        if sdir:
            cfg["server_dir"] = sdir
        sname = os.environ.get(f"{prefix}SERVICE_NAME")
        if sname:
            cfg["service_name"] = sname
        ssess = os.environ.get(f"{prefix}SCREEN_SESSION")
        if ssess:
            cfg["screen_session"] = ssess
        bdir = os.environ.get(f"{prefix}BACKUP_DIR")
        if bdir:
            cfg["backup_dir"] = bdir
        host = os.environ.get(f"{prefix}HOST")
        if host:
            cfg["host"] = host
        port = os.environ.get(f"{prefix}PORT")
        if port:
            try:
                cfg["port"] = int(port)
            except ValueError:
                pass
        astart = os.environ.get(f"{prefix}AUTOSTART")
        if astart is not None:
            cfg["autostart"] = astart.lower() in ("1", "true", "yes", "on")
        jpath = os.environ.get(f"{prefix}JAVA_PATH") or os.environ.get(f"{prefix}JAVA_BIN")
        if jpath:
            cfg["java_path"] = jpath
        em = os.environ.get(f"{prefix}ADMIN_EMAIL") or os.environ.get(f"{prefix}CONTACT_EMAIL")
        if em:
            cfg["admin_email"] = em
    return cfg

SERVER_CONFIG = load_server_config()
ADMIN_USER = SERVER_CONFIG["admin_user"]
ADMIN_PASS = SERVER_CONFIG["admin_pass"]
SERVER_DIR = SERVER_CONFIG["server_dir"]
SERVICE_NAME = SERVER_CONFIG["service_name"]
SCREEN_SESSION = SERVER_CONFIG.get("screen_session") or SERVER_CONFIG.get("service_name") or "minecraft"

def get_contact_email():
    """Retrieve user-agent contact email from SERVER_CONFIG or default."""
    return (SERVER_CONFIG.get("admin_email") or "admin@server.local").strip()

def get_panel_user_agent(include_email=True):
    """Generate compliant User-Agent string for external API requests."""
    if include_email:
        return f"FiestaPanel/2.5 ({get_contact_email()})"
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FiestaPanel/2.5"

def get_backup_dir():
    bdir = SERVER_CONFIG.get("backup_dir")
    if bdir:
        return os.path.abspath(bdir)
    parent = os.path.dirname(os.path.realpath(SERVER_DIR))
    candidate = os.path.join(parent, "backups")
    if os.path.exists(candidate):
        return candidate
    return os.path.join(SERVER_DIR, "backups")

def get_spawn_coordinates():
    spawn = SERVER_CONFIG.get("spawn_coordinates")
    if isinstance(spawn, dict) and "x" in spawn and "y" in spawn and "z" in spawn:
        try:
            return {
                "x": int(spawn.get("x", DEFAULT_SPAWN_COORDINATES["x"])),
                "y": int(spawn.get("y", DEFAULT_SPAWN_COORDINATES["y"])),
                "z": int(spawn.get("z", DEFAULT_SPAWN_COORDINATES["z"])),
                "dimension": str(spawn.get("dimension", DEFAULT_SPAWN_COORDINATES["dimension"])),
                "radius": int(spawn.get("radius", DEFAULT_SPAWN_COORDINATES["radius"]))
            }
        except (ValueError, TypeError):
            pass
    return DEFAULT_SPAWN_COORDINATES.copy()

def save_spawn_coordinates(coords):
    SERVER_CONFIG["spawn_coordinates"] = coords
    target_cfg = CONFIG_FILE
    existing = {}
    if os.path.exists(target_cfg):
        try:
            with open(target_cfg, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}
    existing["spawn_coordinates"] = coords
    try:
        with open(target_cfg, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print("Failed to save spawn coordinates to config:", e)

STATIC_DIR = os.path.join(BASE_DIR, "static")
MCASSETS_DIR = os.path.join(STATIC_DIR, "mcassets")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
LOG_FILE = os.path.join(SERVER_DIR, "logs", "latest.log")
START_TIME = time.time()

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"
UPLOAD_DIR = os.path.join(STATIC_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

def tail_file(filepath, n_lines=100, block_size=4096):
    """Cross-platform efficient log tail reader (backward seek without tail command)."""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "rb") as f:
            f.seek(0, os.SEEK_END)
            filesize = f.tell()
            buffer = bytearray()
            pointer = filesize
            lines = []
            while pointer > 0 and len(lines) <= n_lines:
                read_size = min(block_size, pointer)
                pointer -= read_size
                f.seek(pointer)
                chunk = f.read(read_size)
                buffer = chunk + buffer
                lines = buffer.split(b"\n")
            return [l.decode("utf-8", errors="replace").rstrip("\r") for l in lines[-n_lines:] if l]
    except Exception:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return [l.rstrip("\r\n") for l in f.readlines()[-n_lines:]]
        except Exception:
            return []

def get_single_java_info(bin_path):
    """Inspect an executable java binary and extract vendor, version string, and major version."""
    if not bin_path:
        return None
    resolved = shutil.which(bin_path) or (bin_path if os.path.isfile(bin_path) and os.access(bin_path, os.X_OK) else None)
    if not resolved or not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        return None
    try:
        res = subprocess.run([resolved, "-version"], capture_output=True, text=True, timeout=3)
        out = (res.stderr or res.stdout or "").strip()
        if not out:
            return None
        first_line = out.splitlines()[0].strip()
        major = None
        m = re.search(r'version\s+"(\d+)', first_line)
        if m:
            major = int(m.group(1))
        elif "1.8." in first_line:
            major = 8
        vendor = "Java"
        out_lower = out.lower()
        if "openjdk" in out_lower:
            vendor = "OpenJDK"
        elif "temurin" in out_lower:
            vendor = "Eclipse Temurin"
        elif "corretto" in out_lower:
            vendor = "Amazon Corretto"
        elif "zulu" in out_lower:
            vendor = "Azul Zulu"
        elif "graalvm" in out_lower:
            vendor = "GraalVM"
        elif "microsoft" in out_lower:
            vendor = "Microsoft"
        return {
            "path": resolved,
            "version_str": first_line,
            "major_version": major,
            "vendor": vendor
        }
    except Exception:
        return None

def detect_java_bin():
    """Locate java binary across Windows, macOS, and Linux. Prioritizes configured java_path."""
    try:
        cfg_java = SERVER_CONFIG.get("java_path") if isinstance(SERVER_CONFIG, dict) else None
        if cfg_java:
            resolved = shutil.which(cfg_java) or (cfg_java if os.path.isfile(cfg_java) and os.access(cfg_java, os.X_OK) else None)
            if resolved:
                return resolved
    except Exception:
        pass

    j = shutil.which("java") or shutil.which("java.exe")
    if j:
        return j
    jh = os.environ.get("JAVA_HOME")
    if jh:
        bin_j = os.path.join(jh, "bin", "java.exe" if IS_WINDOWS else "java")
        if os.path.exists(bin_j):
            return bin_j
    if IS_WINDOWS:
        for wr in [r"C:\Program Files\Eclipse Adoptium", r"C:\Program Files\Java", r"C:\Program Files\Microsoft", r"C:\Program Files\Amazon Corretto", r"C:\Program Files\BellSoft"]:
            if os.path.exists(wr):
                for root, dirs, files in os.walk(wr):
                    if "java.exe" in files:
                        return os.path.join(root, "java.exe")
    elif IS_MACOS:
        try:
            out = subprocess.check_output(["/usr/libexec/java_home"], text=True).strip()
            if out and os.path.exists(os.path.join(out, "bin", "java")):
                return os.path.join(out, "bin", "java")
        except Exception:
            pass
    return "java"

def detect_java_version_str(bin_path=None):
    j_bin = bin_path or detect_java_bin()
    try:
        res = subprocess.run([j_bin, "-version"], capture_output=True, text=True, timeout=2)
        out = res.stderr or res.stdout
        if out:
            return out.splitlines()[0].strip()
    except Exception:
        pass
    return "OpenJDK / Java Runtime"

def detect_all_installed_javas():
    """Scan and return all installed Java runtimes on the host."""
    found = {}

    if shutil.which("update-alternatives"):
        try:
            res = subprocess.run(["update-alternatives", "--list", "java"], capture_output=True, text=True, timeout=2)
            for line in res.stdout.splitlines():
                p = line.strip()
                if p and os.path.exists(p):
                    found[os.path.realpath(p)] = p
        except Exception:
            pass

    for p in glob.glob("/usr/lib/jvm/*/bin/java") + glob.glob("/opt/java/*/bin/java") + glob.glob("/usr/java/*/bin/java"):
        if os.path.exists(p):
            found[os.path.realpath(p)] = p

    if IS_MACOS:
        try:
            out = subprocess.check_output(["/usr/libexec/java_home", "-V"], stderr=subprocess.STDOUT, text=True, timeout=3)
            for line in out.splitlines():
                m = re.search(r'(/Library/Java/JavaVirtualMachines/[^\s]+/Contents/Home)', line)
                if m:
                    jp = os.path.join(m.group(1), "bin", "java")
                    if os.path.exists(jp):
                        found[os.path.realpath(jp)] = jp
        except Exception:
            pass

    if IS_WINDOWS:
        for wr in [r"C:\Program Files\Eclipse Adoptium", r"C:\Program Files\Java", r"C:\Program Files\Microsoft", r"C:\Program Files\Amazon Corretto", r"C:\Program Files\BellSoft"]:
            if os.path.exists(wr):
                for root, dirs, files in os.walk(wr):
                    if "java.exe" in files:
                        jp = os.path.join(root, "java.exe")
                        found[os.path.realpath(jp)] = jp

    sys_j = shutil.which("java") or shutil.which("java.exe")
    if sys_j:
        found[os.path.realpath(sys_j)] = sys_j

    jh = os.environ.get("JAVA_HOME")
    if jh:
        bin_j = os.path.join(jh, "bin", "java.exe" if IS_WINDOWS else "java")
        if os.path.exists(bin_j):
            found[os.path.realpath(bin_j)] = bin_j

    cfg_j = SERVER_CONFIG.get("java_path") if isinstance(SERVER_CONFIG, dict) else None
    if cfg_j and os.path.exists(cfg_j):
        found[os.path.realpath(cfg_j)] = cfg_j

    active_raw = detect_java_bin()
    active_bin = os.path.realpath(active_raw) if os.path.exists(active_raw) else active_raw

    results = []
    for rp, disp in found.items():
        info = get_single_java_info(rp)
        if info:
            info["display_path"] = disp
            info["active"] = (rp == active_bin or disp == active_raw or rp == active_raw)
            results.append(info)

    results.sort(key=lambda x: (x.get("major_version") or 0), reverse=True)
    return results

class ServerRunner:
    """
    Cross-platform Minecraft server manager for Linux, macOS, and Windows.
    Supports systemd when present, direct process execution with stdin, and screen.
    """
    def __init__(self, server_dir, service_name=None, screen_session=None):
        self.server_dir = os.path.abspath(server_dir)
        self.service_name = service_name
        self.screen_session = screen_session
        self.proc = None
        self.pid_file = os.path.join(self.server_dir, ".fiesta_server.pid")
        self.runtime_config_file = os.path.join(self.server_dir, "runtime_config.json")
        self.lock = threading.Lock()
        self.on_state_change = []

    def _notify_state(self, is_running):
        try:
            for cb in getattr(self, "on_state_change", []):
                cb(is_running)
        except Exception:
            pass

    def _detect_systemd(self):
        if not IS_LINUX or not shutil.which("systemctl") or not self.service_name:
            return False
        # Do not manage or stop the web portal daemon itself
        if self.service_name in ("fiestapanel", "fiesta-portal"):
            return False
        try:
            res = subprocess.run(["systemctl", "status", self.service_name], capture_output=True, timeout=2)
            return res.returncode in (0, 3)
        except Exception:
            return False

    def load_runtime_config(self):
        cfg = {
            "min_ram": "1G",
            "max_ram": "4G",
            "jvm_flags": ["-XX:+UseG1GC", "-XX:+ParallelRefProcEnabled", "-XX:MaxGCPauseMillis=200", "-XX:+UnlockExperimentalVMOptions", "-XX:+DisableExplicitGC", "-XX:+AlwaysPreTouch"],
            "jar_name": "server.jar"
        }
        if os.path.exists(self.runtime_config_file):
            try:
                with open(self.runtime_config_file, "r") as f:
                    cfg.update(json.load(f))
            except Exception:
                pass
        return cfg

    def save_runtime_config(self, cfg):
        os.makedirs(self.server_dir, exist_ok=True)
        try:
            with open(self.runtime_config_file, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def is_running(self):
        if self._detect_systemd():
            try:
                res = subprocess.run(["systemctl", "is-active", self.service_name], capture_output=True, text=True, timeout=2)
                if res.stdout.strip() == "active":
                    return True
            except Exception:
                pass

        if self.proc and self.proc.poll() is None:
            return True

        if os.path.exists(self.pid_file):
            try:
                with open(self.pid_file, "r") as f:
                    pid = int(f.read().strip())
                if psutil and psutil.pid_exists(pid):
                    p = psutil.Process(pid)
                    if any("server.jar" in str(arg) for arg in (p.cmdline() or [])):
                        return True
            except Exception:
                pass

        if psutil:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline') or []
                    if any('server.jar' in str(arg) for arg in cmdline):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        return False

    def get_process_info(self):
        if psutil:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info', 'cpu_percent']):
                try:
                    cmdline = proc.info.get('cmdline') or []
                    if any('server.jar' in str(arg) for arg in cmdline):
                        mem_mb = round(proc.info['memory_info'].rss / (1024 * 1024), 1)
                        return proc.info['pid'], mem_mb, proc.info.get('cpu_percent', 0.0)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        return None, 0, 0.0

    def start(self):
        with self.lock:
            if self.is_running():
                return True, "Server is already running."

            jar_path = os.path.join(self.server_dir, "server.jar")
            if not os.path.exists(jar_path):
                return False, "server.jar not found in server directory. Please install server software first."

            # Ensure eula.txt is true
            eula_file = os.path.join(self.server_dir, "eula.txt")
            if not os.path.exists(eula_file):
                try:
                    with open(eula_file, "w") as f:
                        f.write("# By changing the setting below to TRUE you are indicating your agreement to the Mojang EULA (https://aka.ms/MinecraftEULA).\neula=true\n")
                except Exception:
                    pass
            else:
                try:
                    with open(eula_file, "r") as f:
                        content = f.read()
                    if "eula=false" in content:
                        with open(eula_file, "w") as f:
                            f.write(content.replace("eula=false", "eula=true"))
                except Exception:
                    pass

            if self._detect_systemd():
                try:
                    res = subprocess.run(["sudo", "systemctl", "start", self.service_name], capture_output=True, text=True, timeout=10)
                    if res.returncode == 0:
                        self._notify_state(True)
                        return True, f"Started {self.service_name} via systemd."
                except Exception:
                    pass

            java_bin = detect_java_bin()
            cfg = self.load_runtime_config()
            min_ram = cfg.get("min_ram", "1G")
            max_ram = cfg.get("max_ram", "4G")
            jvm_flags = cfg.get("jvm_flags", ["-XX:+UseG1GC"])

            logs_dir = os.path.join(self.server_dir, "logs")
            os.makedirs(logs_dir, exist_ok=True)
            log_file = os.path.join(logs_dir, "latest.log")

            cmd = [java_bin, f"-Xms{min_ram}", f"-Xmx{max_ram}"] + jvm_flags + ["-jar", "server.jar", "nogui"]
            log_out = open(log_file, "a", encoding="utf-8", errors="replace")

            creationflags = 0
            if IS_WINDOWS:
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

            self.proc = subprocess.Popen(
                cmd,
                cwd=self.server_dir,
                stdin=subprocess.PIPE,
                stdout=log_out,
                stderr=subprocess.STDOUT,
                creationflags=creationflags
            )
            try:
                log_out.close()
            except Exception:
                pass

            try:
                with open(self.pid_file, "w") as f:
                    f.write(str(self.proc.pid))
            except Exception:
                pass

            self._notify_state(True)
            return True, f"Server process launched directly (PID: {self.proc.pid})."

    def stop(self):
        with self.lock:
            self.send_command("save-all")
            time.sleep(1)
            self.send_command("stop")

            if self._detect_systemd():
                try:
                    subprocess.run(["sudo", "systemctl", "stop", self.service_name], timeout=15)
                except Exception:
                    pass

            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                self.proc = None

            if os.path.exists(self.pid_file):
                try:
                    with open(self.pid_file, "r") as f:
                        pid = int(f.read().strip())
                    if psutil and psutil.pid_exists(pid):
                        p = psutil.Process(pid)
                        p.terminate()
                    os.remove(self.pid_file)
                except Exception:
                    pass

            self._notify_state(False)
            return True, "Server shutdown initiated."

    def restart(self):
        self.stop()
        time.sleep(2)
        return self.start()

    def send_command(self, cmd):
        clean_cmd = re.sub(r'[\r\n]+', ' ', cmd or "").strip()
        if not clean_cmd:
            return False, "Command cannot be empty."
        if self.proc and self.proc.stdin and self.proc.poll() is None:
            try:
                self.proc.stdin.write(f"{clean_cmd}\n".encode("utf-8"))
                self.proc.stdin.flush()
                return True, f"Executed: {clean_cmd}"
            except Exception as e:
                return False, f"Failed writing to stdin: {e}"
        return False, "Direct process stdin not available."

server_runner = ServerRunner(SERVER_DIR, SERVICE_NAME, SCREEN_SESSION)


def hash_password(pwd):
    return generate_password_hash(pwd, method='scrypt')

def session_password_version(stored):
    return hmac.new(app.secret_key, (stored or "").encode("utf-8"), hashlib.sha256).hexdigest()

def verify_password(stored, pwd):
    if re.fullmatch(r'[0-9a-f]{64}', stored or ''):
        return hmac.compare_digest(stored, hashlib.sha256(pwd.encode('utf-8')).hexdigest())
    try:
        return check_password_hash(stored, pwd)
    except (ValueError, TypeError):
        return False

def load_users():
    target = USERS_FILE
    if not os.path.exists(target) and DATA_DIR != BASE_DIR and os.path.exists(os.path.join(BASE_DIR, "users.json")):
        try:
            shutil.copy2(os.path.join(BASE_DIR, "users.json"), target)
        except Exception:
            target = os.path.join(BASE_DIR, "users.json")
    if not os.path.exists(target):
        return {}
    try:
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)

BRANDING_FILE = os.path.join(DATA_DIR, "branding.json")

DEFAULT_BRANDING = {
    "brand_name": "FIESTA-LINK",
    "tagline": "The Reliable Choice",
    "model_number": "Model No. FS-GATEWAY-PRO",
    "browser_title": "FIESTA-LINK | FS-GATEWAY-PRO Console",
    "footer_text": "Made by Fiesta and it's developers, Mockup UI of Legacy TP-Link Portal",
    "server_desc": "Server Gateway Console",
    "header_icon": "tplink",
    "header_icon_custom": "",
    "server_domain": "",
    "public_site_url": "",
    "bedrock_server_name": "Minecraft Server"
}

HEADER_ICONS = {
    "tplink": """<svg viewBox="0 0 36 28" width="36" height="28"><path d="M4 14 C4 8 9 4 15 4 L20 4 C24 4 28 8 28 12 L22 12 C22 10 19 8 16 8 L14 8 C11 8 8 11 8 14 C8 17 11 20 14 20 L16 20 C19 20 22 18 22 16 L28 16 C28 20 24 24 20 24 L15 24 C9 24 4 20 4 14 Z" fill="#ffffff" opacity="0.95"/><path d="M32 14 C32 20 27 24 21 24 L16 24 C12 24 8 20 8 16 L14 16 C14 18 17 20 20 20 L22 20 C25 20 28 17 28 14 C28 11 25 8 22 8 L20 8 C17 8 14 10 14 12 L8 12 C8 8 12 4 16 4 L21 4 C27 4 32 8 32 14 Z" fill="#bdf4c9" opacity="0.85"/></svg>""",
    "win7": """<svg viewBox="0 0 32 32" width="32" height="28"><path d="M4 6 C8 4 12 7 15 6 L15 15 C12 16 8 13 4 15 Z" fill="#e73827"/><path d="M17 6 C20 4 24 7 28 5 L28 14 C24 16 20 13 17 15 Z" fill="#4fae32"/><path d="M4 17 C8 15 12 18 15 17 L15 26 C12 27 8 24 4 26 Z" fill="#0080ff"/><path d="M17 17 C20 15 24 18 28 16 L28 25 C24 27 20 24 17 26 Z" fill="#fbb03b"/></svg>""",
    "minecraft": """<svg viewBox="0 0 32 32" width="30" height="28"><rect width="32" height="32" rx="4" fill="#3ca432"/><rect x="5" y="7" width="6" height="6" fill="#111111"/><rect x="21" y="7" width="6" height="6" fill="#111111"/><rect x="11" y="13" width="10" height="11" fill="#111111"/><rect x="8" y="17" width="3" height="11" fill="#111111"/><rect x="21" y="17" width="3" height="11" fill="#111111"/></svg>""",
    "win98": """<svg viewBox="0 0 32 32" width="32" height="28"><path d="M5 8 C8 7 11 9 14 8 L14 15 C11 16 8 14 5 15 Z" fill="#ff0000"/><path d="M16 8 C19 7 22 9 25 7 L25 14 C22 16 19 14 16 15 Z" fill="#00aa00"/><path d="M5 17 C8 16 11 18 14 17 L14 24 C11 25 8 23 5 24 Z" fill="#0000ff"/><path d="M16 17 C19 16 22 18 25 16 L25 23 C22 25 19 23 16 24 Z" fill="#ffcc00"/></svg>""",
    "server": """<svg viewBox="0 0 32 32" width="32" height="28"><rect x="3" y="4" width="26" height="7" rx="2" fill="#1e1e1e" stroke="#fff" stroke-width="1.5"/><circle cx="7" cy="7.5" r="1.5" fill="#00ff66"/><circle cx="12" cy="7.5" r="1.5" fill="#00ff66"/><line x1="17" y1="7.5" x2="25" y2="7.5" stroke="#aaa" stroke-width="1.5"/><rect x="3" y="13" width="26" height="7" rx="2" fill="#1e1e1e" stroke="#fff" stroke-width="1.5"/><circle cx="7" cy="16.5" r="1.5" fill="#00ff66"/><circle cx="12" cy="16.5" r="1.5" fill="#ffcc00"/><line x1="17" y1="16.5" x2="25" y2="16.5" stroke="#aaa" stroke-width="1.5"/><rect x="3" y="22" width="26" height="7" rx="2" fill="#1e1e1e" stroke="#fff" stroke-width="1.5"/><circle cx="7" cy="25.5" r="1.5" fill="#00ff66"/><circle cx="12" cy="25.5" r="1.5" fill="#00ff66"/><line x1="17" y1="25.5" x2="25" y2="25.5" stroke="#aaa" stroke-width="1.5"/></svg>""",
    "terminal": """<svg viewBox="0 0 32 32" width="32" height="28"><rect x="2" y="4" width="28" height="24" rx="3" fill="#111111" stroke="#00ff66" stroke-width="1.5"/><path d="M7 11 L13 16 L7 21" stroke="#00ff66" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/><line x1="16" y1="21" x2="24" y2="21" stroke="#00ff66" stroke-width="2.5" stroke-linecap="round"/></svg>""",
    "chip": """<svg viewBox="0 0 32 32" width="32" height="28"><rect x="7" y="7" width="18" height="18" rx="2" fill="#252525" stroke="#00f0ff" stroke-width="1.5"/><rect x="11" y="11" width="10" height="10" rx="1" fill="#00f0ff" opacity="0.3"/><path d="M11 4 L11 7 M16 4 L16 7 M21 4 L21 7 M11 25 L11 28 M16 25 L16 28 M21 25 L21 28 M4 11 L7 11 M4 16 L7 16 M4 21 L7 21 M25 11 L28 11 M25 16 L28 16 M25 21 L28 21" stroke="#00f0ff" stroke-width="1.5" stroke-linecap="round"/></svg>""",
    "shield": """<svg viewBox="0 0 32 32" width="32" height="28"><path d="M16 3 L27 7 V15 C27 22 22 27 16 29 C10 27 5 22 5 15 V7 Z" fill="#00aa44" stroke="#ffffff" stroke-width="1.5"/><path d="M11 15 L14.5 18.5 L21.5 11.5" stroke="#ffffff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>""",
    "antenna": """<svg viewBox="0 0 32 32" width="32" height="28"><rect x="4" y="18" width="24" height="9" rx="2" fill="#333" stroke="#fff" stroke-width="1.5"/><circle cx="9" cy="22.5" r="1.5" fill="#00ff66"/><circle cx="14" cy="22.5" r="1.5" fill="#00ff66"/><circle cx="19" cy="22.5" r="1.5" fill="#00ff66"/><line x1="8" y1="18" x2="4" y2="7" stroke="#fff" stroke-width="2" stroke-linecap="round"/><line x1="24" y1="18" x2="28" y2="7" stroke="#fff" stroke-width="2" stroke-linecap="round"/><path d="M13 13 C14.5 11 17.5 11 19 13" stroke="#00ff66" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg>""",
    "cyber": """<svg viewBox="0 0 32 32" width="32" height="28"><polygon points="16,2 29,8 29,24 16,30 3,24 3,8" fill="#1b0a2a" stroke="#ff2a85" stroke-width="1.5"/><path d="M17 7 L10 17 L16 17 L15 25 L22 15 L16 15 Z" fill="#00f0ff"/></svg>""",
    "flame": """<svg viewBox="0 0 32 32" width="32" height="28"><path d="M16 2 C16 7 20 10 20 14 C20 16 19 18 17 19 C18 15 15 13 14 11 C11 14 10 18 10 20 C10 25 13 29 18 29 C24 29 27 24 27 18 C27 11 21 6 16 2 Z" fill="#ef4444"/><path d="M16 14 C16 17 18 19 18 21 C18 23 17 24 16 25 C14 25 13 23 13 21 C13 18 15 16 16 14 Z" fill="#fbbf24"/></svg>""",
    "globe": """<svg viewBox="0 0 32 32" width="32" height="28"><circle cx="16" cy="16" r="12" fill="#005a9e" stroke="#ffffff" stroke-width="1.5"/><ellipse cx="16" cy="16" rx="6" ry="12" fill="none" stroke="#ffffff" stroke-width="1.2"/><line x1="4" y1="16" x2="28" y2="16" stroke="#ffffff" stroke-width="1.2"/><line x1="6" y1="10" x2="26" y2="10" stroke="#ffffff" stroke-width="1"/><line x1="6" y1="22" x2="26" y2="22" stroke="#ffffff" stroke-width="1"/></svg>"""
}

def get_header_icon_svg(icon_key, custom_val=""):
    if icon_key == "custom" and custom_val:
        custom_val = custom_val.strip()
        if custom_val.startswith("<svg"):
            return custom_val
        else:
            return f'<img src="{custom_val}" alt="logo" style="max-height: 28px; max-width: 36px; object-fit: contain; vertical-align: middle;">'
    return HEADER_ICONS.get(icon_key, HEADER_ICONS["tplink"])

def get_portal_favicon_url(branding=None):
    if not branding:
        branding = load_branding()
    icon_key = branding.get("header_icon", "tplink")
    custom_val = (branding.get("header_icon_custom") or "").strip()
    if icon_key == "custom" and custom_val and (custom_val.startswith("/static/") or custom_val.startswith("http")):
        return custom_val
    return f"/api/branding/favicon?v={icon_key}"

app.jinja_env.globals.update(
    get_header_icon_svg=get_header_icon_svg,
    get_portal_favicon_url=get_portal_favicon_url
)

def load_branding():
    target = BRANDING_FILE
    if not os.path.exists(target) and DATA_DIR != BASE_DIR and os.path.exists(os.path.join(BASE_DIR, "branding.json")):
        target = os.path.join(BASE_DIR, "branding.json")
    if not os.path.exists(target):
        merged = DEFAULT_BRANDING.copy()
    else:
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
                merged = DEFAULT_BRANDING.copy()
                merged.update(data)
        except Exception:
            merged = DEFAULT_BRANDING.copy()

    # Environment variables or server config overrides/fallbacks
    env_domain = os.environ.get("SERVER_DOMAIN") or SERVER_CONFIG.get("server_domain")
    if env_domain and not merged.get("server_domain"):
        merged["server_domain"] = env_domain.strip()
    env_site = os.environ.get("PUBLIC_SITE_URL") or SERVER_CONFIG.get("public_site_url")
    if env_site and not merged.get("public_site_url"):
        merged["public_site_url"] = env_site.strip()
    bedrock_name = os.environ.get("BEDROCK_SERVER_NAME") or SERVER_CONFIG.get("bedrock_server_name")
    if bedrock_name and (not merged.get("bedrock_server_name") or merged.get("bedrock_server_name") == DEFAULT_BRANDING["bedrock_server_name"]):
        merged["bedrock_server_name"] = bedrock_name.strip()
    if os.environ.get("BRAND_NAME") and merged.get("brand_name") == DEFAULT_BRANDING["brand_name"]:
        merged["brand_name"] = os.environ.get("BRAND_NAME").strip()
    return merged

def save_branding(data):
    with open(BRANDING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def get_allowed_configs():
    return {
        "server.properties": os.path.join(SERVER_DIR, "server.properties"),
        "bukkit.yml": os.path.join(SERVER_DIR, "bukkit.yml"),
        "spigot.yml": os.path.join(SERVER_DIR, "spigot.yml"),
        "purpur.yml": os.path.join(SERVER_DIR, "purpur.yml"),
        "paper-global.yml": os.path.join(SERVER_DIR, "config", "paper-global.yml"),
        "paper-world-defaults.yml": os.path.join(SERVER_DIR, "config", "paper-world-defaults.yml"),
        "commands.yml": os.path.join(SERVER_DIR, "commands.yml"),
        "permissions.yml": os.path.join(SERVER_DIR, "permissions.yml"),
        "help.yml": os.path.join(SERVER_DIR, "help.yml"),
        "floodgate/config.yml": os.path.join(SERVER_DIR, "plugins", "floodgate", "config.yml"),
        "Geyser-Spigot/config.yml": os.path.join(SERVER_DIR, "plugins", "Geyser-Spigot", "config.yml"),
        "LuckPerms/config.yml": os.path.join(SERVER_DIR, "plugins", "LuckPerms", "config.yml"),
        "CoreProtect/config.yml": os.path.join(SERVER_DIR, "plugins", "CoreProtect", "config.yml"),
        "Chunky/config.yml": os.path.join(SERVER_DIR, "plugins", "Chunky", "config.yml"),
        "WorldEdit/config.yml": os.path.join(SERVER_DIR, "plugins", "WorldEdit", "config.yml"),
        "ViaVersion/config.yml": os.path.join(SERVER_DIR, "plugins", "ViaVersion", "config.yml"),
        "ViaBackwards/config.yml": os.path.join(SERVER_DIR, "plugins", "ViaBackwards", "config.yml"),
        "ViaRewind/config.yml": os.path.join(SERVER_DIR, "plugins", "ViaRewind", "config.yml"),
        "Vault/config.yml": os.path.join(SERVER_DIR, "plugins", "Vault", "config.yml"),
        "Essentials/config.yml": os.path.join(SERVER_DIR, "plugins", "Essentials", "config.yml"),
        "Essentials/spawn.yml": os.path.join(SERVER_DIR, "plugins", "Essentials", "spawn.yml"),
        "Essentials/kits.yml": os.path.join(SERVER_DIR, "plugins", "Essentials", "kits.yml"),
        "Essentials/custom_items.yml": os.path.join(SERVER_DIR, "plugins", "Essentials", "custom_items.yml"),
        "Essentials/worth.yml": os.path.join(SERVER_DIR, "plugins", "Essentials", "worth.yml"),
        "AuthMe/config.yml": os.path.join(SERVER_DIR, "plugins", "AuthMe", "config.yml"),
        "AuthMe/spawn.yml": os.path.join(SERVER_DIR, "plugins", "AuthMe", "spawn.yml"),
        "TAB/config.yml": os.path.join(SERVER_DIR, "plugins", "TAB", "config.yml"),
        "DeluxeMenus/config.yml": os.path.join(SERVER_DIR, "plugins", "DeluxeMenus", "config.yml"),
        "DeluxeMenus/main_menu.yml": os.path.join(SERVER_DIR, "plugins", "DeluxeMenus", "gui_menus", "main_menu.yml"),
        "GriefPrevention/config.yml": os.path.join(SERVER_DIR, "plugins", "GriefPreventionData", "config.yml"),
        "UltimateAntiBot/config.yml": os.path.join(SERVER_DIR, "plugins", "UltimateAntiBot", "config.yml"),
        "ChestShop/config.yml": os.path.join(SERVER_DIR, "plugins", "ChestShop", "config.yml"),
        "SkinsRestorer/config.yml": os.path.join(SERVER_DIR, "plugins", "SkinsRestorer", "config.yml"),
        "DecentHolograms/config.yml": os.path.join(SERVER_DIR, "plugins", "DecentHolograms", "config.yml"),
        "EzAuction/auction.yml": os.path.join(SERVER_DIR, "plugins", "EzAuction", "auction.yml"),
        "PlaceholderAPI/config.yml": os.path.join(SERVER_DIR, "plugins", "PlaceholderAPI", "config.yml")
    }

ALLOWED_CONFIGS = get_allowed_configs()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated_function

def send_mc_command(cmd):
    clean_cmd = re.sub(r'[\r\n]+', ' ', cmd or "").strip()
    if not clean_cmd:
        return False, "Command cannot be empty."
    # 1. First attempt via direct process stdin if running as background process
    try:
        if "server_runner" in globals() and server_runner and server_runner.proc and server_runner.proc.stdin and server_runner.proc.poll() is None:
            executed, msg = server_runner.send_command(clean_cmd)
            if executed:
                return True, msg
    except Exception:
        pass

    # 2. Linux screen dispatch
    if IS_LINUX and shutil.which("screen"):
        try:
            screen_cmd = clean_cmd.replace('"', '\\"').replace("'", "\\'").strip()
            screen_name = (SERVER_CONFIG.get("screen_session") if isinstance(SERVER_CONFIG, dict) else None) or SCREEN_SESSION or SERVER_CONFIG.get("service_name") or "minecraft"
            subprocess.run(
                ["screen", "-p", "0", "-S", screen_name, "-X", "eval", f'stuff "{screen_cmd}\\015"'],
                check=True
            )
            return True, f"Executed: {clean_cmd}"
        except Exception as e:
            return False, str(e)
    return False, "Could not dispatch command: server process is not running or console is inaccessible."

def resolve_player_ip(player_name):
    """
    Resolves a player username to their last known IP address from usercache, AuthMe,
    Essentials userdata, or server connection logs.
    """
    if not player_name:
        return None
    target = str(player_name).strip()
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", target) or ":" in target:
        return target

    target_lower = target.lower()

    # 1. Quick UUID lookup in usercache.json -> Essentials userdata/{uuid}.yml
    cache_file = os.path.join(SERVER_DIR, "usercache.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                cache_data = json.load(f)
                for entry in cache_data:
                    if entry.get("name", "").lower() == target_lower:
                        uuid_val = entry.get("uuid")
                        if uuid_val:
                            ess_file = os.path.join(SERVER_DIR, "plugins", "Essentials", "userdata", f"{uuid_val}.yml")
                            if os.path.exists(ess_file):
                                with open(ess_file, "r", encoding="utf-8", errors="ignore") as ef:
                                    m = re.search(r"ip-address:\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})", ef.read())
                                    if m and m.group(1) not in ("127.0.0.1", "0.0.0.0"):
                                        return m.group(1).strip()
        except Exception:
            pass

    # 2. Check AuthMe SQLite DB
    authme_db = os.path.join(SERVER_DIR, "plugins", "AuthMe", "authme.db")
    if os.path.exists(authme_db):
        try:
            con = sqlite3.connect(authme_db, timeout=2.0)
            cur = con.cursor()
            cur.execute("SELECT ip FROM authme WHERE LOWER(username)=? OR LOWER(realname)=? LIMIT 1", (target_lower, target_lower))
            row = cur.fetchone()
            con.close()
            if row and row[0] and row[0] not in ("127.0.0.1", "0.0.0.0"):
                return row[0].strip()
        except Exception:
            pass

    # 3. Search Essentials userdata directory
    ess_dir = os.path.join(SERVER_DIR, "plugins", "Essentials", "userdata")
    if os.path.exists(ess_dir):
        for fpath in glob.glob(os.path.join(ess_dir, "*.yml")):
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    txt = f.read()
                    if f"last-account-name: {target_lower}" in txt.lower():
                        m = re.search(r"ip-address:\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})", txt)
                        if m and m.group(1) not in ("127.0.0.1", "0.0.0.0"):
                            return m.group(1).strip()
            except Exception:
                pass

    # 4. Search latest.log
    log_file = os.path.join(SERVER_DIR, "logs", "latest.log")
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in reversed(f.readlines()):
                    if target_lower in line.lower():
                        m = re.search(r"\[/([0-9]{1,3}(?:\.[0-9]{1,3}){3}):\d+\] logged in", line, re.I)
                        if m:
                            return m.group(1).strip()
                        m2 = re.search(r"\[AuthMe\]\s+" + re.escape(target) + r"\s+logged in\s+([0-9]{1,3}(?:\.[0-9]{1,3}){3})", line, re.I)
                        if m2:
                            return m2.group(1).strip()
        except Exception:
            pass

    return None

def add_to_banned_ips(ip, reason="IP-Banned by administrator"):
    ip = (ip or "").strip()
    if not ip:
        return
    ip_ban_file = os.path.join(SERVER_DIR, "banned-ips.json")
    try:
        entries = []
        if os.path.exists(ip_ban_file):
            with open(ip_ban_file, "r", encoding="utf-8") as f:
                entries = json.load(f)
        if not any(e.get("ip", "").strip() == ip for e in entries):
            now_str = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
            entries.append({
                "ip": ip,
                "created": now_str,
                "source": "Console",
                "expires": "forever",
                "reason": reason
            })
            with open(ip_ban_file, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2)
    except Exception:
        pass

def remove_from_banned_ips(ip_or_target):
    ip_or_target = (ip_or_target or "").strip()
    if not ip_or_target:
        return
    ip_ban_file = os.path.join(SERVER_DIR, "banned-ips.json")
    if os.path.exists(ip_ban_file):
        try:
            with open(ip_ban_file, "r", encoding="utf-8") as f:
                entries = json.load(f)
            new_entries = [e for e in entries if e.get("ip", "").strip().lower() != ip_or_target.lower()]
            if len(new_entries) != len(entries):
                with open(ip_ban_file, "w", encoding="utf-8") as f:
                    json.dump(new_entries, f, indent=2)
        except Exception:
            pass

def get_all_player_ips():
    """
    Returns a tuple (ip_map, reverse_map) mapping:
    - lowercase player username -> IP address
    - IP address -> player username
    """
    ip_map = {}
    reverse_map = {}

    authme_db = os.path.join(SERVER_DIR, "plugins", "AuthMe", "authme.db")
    if os.path.exists(authme_db):
        try:
            con = sqlite3.connect(authme_db, timeout=1.0)
            cur = con.cursor()
            cur.execute("SELECT LOWER(username), ip FROM authme WHERE ip IS NOT NULL AND ip != ''")
            for u, ip in cur.fetchall():
                ip = (ip or "").strip()
                if ip and ip not in ("127.0.0.1", "0.0.0.0"):
                    u_clean = u.strip()
                    ip_map[u_clean] = ip
                    if ip not in reverse_map:
                        reverse_map[ip] = u_clean
            con.close()
        except Exception:
            pass

    ess_dir = os.path.join(SERVER_DIR, "plugins", "Essentials", "userdata")
    usercache_file = os.path.join(SERVER_DIR, "usercache.json")
    if os.path.exists(usercache_file) and os.path.exists(ess_dir):
        try:
            with open(usercache_file, "r") as f:
                cached = json.load(f)
            for u in cached:
                name = (u.get("name") or "").lower().strip()
                uuid_val = u.get("uuid")
                if name and name not in ip_map and uuid_val:
                    ef = os.path.join(ess_dir, f"{uuid_val}.yml")
                    if os.path.exists(ef):
                        with open(ef, "r", encoding="utf-8", errors="ignore") as fyml:
                            m = re.search(r"ip-address:\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})", fyml.read())
                            if m and m.group(1) not in ("127.0.0.1", "0.0.0.0"):
                                found_ip = m.group(1).strip()
                                ip_map[name] = found_ip
                                if found_ip not in reverse_map:
                                    reverse_map[found_ip] = name
        except Exception:
            pass

    return ip_map, reverse_map

def execute_ip_ban(target, reason="Banned by administrator"):
    """
    Executes an IP ban for target (IP address or player username).
    Handles offline players by resolving their last known IP address and
    dispatching both Essentials and Paper native IP ban commands.
    """
    target = (target or "").strip()
    reason = (reason or "").strip() or "Banned by administrator"
    is_ip = bool(re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", target) or ":" in target)
    resolved_ip = resolve_player_ip(target)

    target_ip = target if is_ip else resolved_ip
    if target_ip:
        add_to_banned_ips(target_ip, reason)

    if is_ip:
        send_mc_command(f"banip {target} {reason}")
        send_mc_command(f"ban-ip {target} {reason}")
        return True, f"Banned IP address {target}"
    elif resolved_ip:
        send_mc_command(f"banip {target} {reason}")
        send_mc_command(f"ban-ip {resolved_ip} {reason}")
        return True, f"Banned IP {resolved_ip} for player {target}"
    else:
        send_mc_command(f"banip {target} {reason}")
        send_mc_command(f"ban-ip {target} {reason}")
        return True, f"Dispatched IP ban for {target}"

def execute_ip_pardon(target):
    """
    Unbans an IP or player username from the IP blacklist.
    """
    target = (target or "").strip()
    is_ip = bool(re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", target) or ":" in target)
    resolved_ip = resolve_player_ip(target)

    target_ip = target if is_ip else resolved_ip
    if target_ip:
        remove_from_banned_ips(target_ip)
    remove_from_banned_ips(target)

    if is_ip:
        send_mc_command(f"pardon-ip {target}")
        send_mc_command(f"unbanip {target}")
        return True, f"Unbanned IP {target}"
    elif resolved_ip:
        send_mc_command(f"pardon-ip {resolved_ip}")
        send_mc_command(f"unbanip {resolved_ip}")
        send_mc_command(f"unbanip {target}")
        return True, f"Unbanned IP {resolved_ip} for player {target}"
    else:
        send_mc_command(f"unbanip {target}")
        send_mc_command(f"pardon-ip {target}")
        return True, f"Dispatched IP pardon for {target}"

def get_server_properties_dict():
    props_path = os.path.join(SERVER_DIR, "server.properties")
    props = {}
    if os.path.exists(props_path):
        try:
            with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        props[k.strip()] = v.strip()
        except Exception:
            pass
    return props

def get_server_port(default=25565):
    """Safely retrieves integer server-port from server.properties with graceful fallback."""
    try:
        props = get_server_properties_dict()
        p = props.get("server-port", default)
        val = int(str(p).strip())
        return val if 1 <= val <= 65535 else default
    except (ValueError, TypeError):
        return default

def get_main_level_name():
    """Returns the configured level-name from server.properties, defaulting to 'world'."""
    props = get_server_properties_dict()
    return props.get("level-name", "world").strip() or "world"

def get_panel_port():
    """Retrieve active web panel HTTP port from config or environment."""
    try:
        return int(SERVER_CONFIG.get("port") or os.environ.get("GATEWAY_PORT") or os.environ.get("PORT") or 8080)
    except (ValueError, TypeError):
        return 8080

_PUBLIC_IP_CACHE = {"ip": None, "expires_at": 0}

def get_server_public_ip():
    """Retrieve host public IPv4 address with caching."""
    now = time.time()
    if _PUBLIC_IP_CACHE["ip"] and now < _PUBLIC_IP_CACHE["expires_at"]:
        return _PUBLIC_IP_CACHE["ip"]

    for url in ["https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                ip = resp.read().decode("utf-8").strip()
                if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip):
                    _PUBLIC_IP_CACHE["ip"] = ip
                    _PUBLIC_IP_CACHE["expires_at"] = now + 300
                    return ip
        except Exception:
            continue

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        local_ip = None
        for dns_ip in ("1.1.1.1", "8.8.8.8"):
            try:
                s.connect((dns_ip, 80))
                local_ip = s.getsockname()[0]
                break
            except Exception:
                continue
        if not local_ip:
            local_ip = socket.gethostbyname(socket.gethostname())
        s.close()
        if local_ip and not local_ip.startswith("127."):
            _PUBLIC_IP_CACHE["ip"] = local_ip
            _PUBLIC_IP_CACHE["expires_at"] = now + 60
            return local_ip
    except Exception:
        pass

    return "127.0.0.1"

def get_bedrock_port():
    """Detect Bedrock port from Geyser config or return standard 19132."""
    for gp in [
        os.path.join(SERVER_DIR, "plugins", "Geyser-Spigot", "config.yml"),
        os.path.join(SERVER_DIR, "plugins", "Geyser", "config.yml"),
        os.path.join(SERVER_DIR, "config", "Geyser-Fabric", "config.yml"),
        os.path.join(SERVER_DIR, "config", "Geyser-NeoForge", "config.yml")
    ]:
        if os.path.exists(gp):
            try:
                with open(gp, "r", encoding="utf-8") as f:
                    content = f.read()
                m = re.search(r"port:\s*(\d+)", content)
                if m:
                    return int(m.group(1))
            except Exception:
                pass
    return 19132

def get_effective_server_domain(fallback_to_request=True):
    """
    Returns the configured server domain, or automatically detects the current
    server host / dashboard URL when installed fresh or unconfigured.
    """
    b = load_branding()
    dom = (b.get("server_domain") or SERVER_CONFIG.get("server_domain") or "").strip()
    legacy = ("play.your-server.net", "mc.yourserver.net", "mc.example.com", "play.example.com")
    if dom and dom not in legacy:
        return dom

    # 1. Incoming HTTP request header (dashboard URL hostname)
    if fallback_to_request and has_request_context():
        try:
            forwarded = request.headers.get("X-Forwarded-Host")
            raw = (forwarded.split(",")[0] if forwarded else request.host).strip()
            h = raw.split(":")[0].strip()
            if h and h not in ("0.0.0.0", "localhost", "127.0.0.1", ""):
                return h
        except Exception:
            pass

    # 2. Server public IP
    try:
        pub = get_server_public_ip()
        if pub and pub != "127.0.0.1":
            return pub
    except Exception:
        pass

    # 3. Request host even if localhost
    if fallback_to_request and has_request_context():
        try:
            h = request.host.split(":")[0].strip()
            if h:
                return h
        except Exception:
            pass

    return "localhost"

def ping_mc():
    """Queries live Minecraft server via native SLP handshake using server-port from properties."""
    props = get_server_properties_dict()
    mc_port = get_server_port(25565)

    def_ver = "Minecraft Server"
    meta_file = os.path.join(SERVER_DIR, "installed_software.json")
    if os.path.exists(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as mf:
                def_ver = json.load(mf).get("label") or def_ver
        except Exception:
            pass

    def_max_players = 20
    try:
        def_max_players = int(props.get("max-players", 20))
    except (ValueError, TypeError):
        pass

    s = None
    try:
        t0 = time.time()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect(('127.0.0.1', mc_port))

        host = '127.0.0.1'.encode('utf-8')
        data = b'\x00' + b'\xff\x05' + bytes([len(host)]) + host + struct.pack('>H', mc_port) + b'\x01'
        s.sendall(bytes([len(data)]) + data)
        s.sendall(b'\x01\x00')

        def read_varint(sock):
            val = 0
            for i in range(5):
                chunk = sock.recv(1)
                if not chunk:
                    return 0
                b = ord(chunk)
                val |= (b & 0x7f) << (7 * i)
                if not (b & 0x80):
                    break
            return val

        _ = read_varint(s)
        _ = read_varint(s)
        str_len = read_varint(s)

        received = b''
        while len(received) < str_len:
            chunk = s.recv(min(4096, str_len - len(received)))
            if not chunk:
                break
            received += chunk
        ms = round((time.time() - t0) * 1000, 1)

        info = json.loads(received.decode('utf-8', errors='ignore'))
        desc_obj = info.get('description', '')
        if isinstance(desc_obj, dict):
            motd_txt = desc_obj.get('text', '')
            if not motd_txt and 'extra' in desc_obj and isinstance(desc_obj['extra'], list):
                motd_txt = "".join(x.get('text', '') for x in desc_obj['extra'] if isinstance(x, dict))
        else:
            motd_txt = str(desc_obj or '')

        return {
            'online': True,
            'ping_ms': ms,
            'version': info.get('version', {}).get('name', def_ver),
            'players_online': info.get('players', {}).get('online', 0),
            'players_max': info.get('players', {}).get('max', def_max_players),
            'player_sample': [p.get('name') for p in info.get('players', {}).get('sample', []) if 'name' in p],
            'motd': motd_txt
        }
    except Exception as e:
        return {
            'online': False,
            'ping_ms': -1,
            'error': str(e),
            'version': f"{def_ver} (Offline)",
            'players_online': 0,
            'players_max': def_max_players,
            'player_sample': [],
            'motd': props.get('motd', '')
        }
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass

# Initialize FiestaPanel Addons & Extension Engine
# All browser mutations require a per-session CSRF token. Permissions are
# reloaded for every request so disabling or changing an account takes effect.
_login_attempts = defaultdict(deque)
_login_lock = threading.Lock()

@app.context_processor
def inject_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return {"csrf_token": session["csrf_token"]}

@app.before_request
def enforce_security():
    if request.method == "POST" and (request.path == "/login" or (request.path.startswith("/api/access/users/") and request.path.endswith("/password"))):
        now = time.monotonic()
        key = request.remote_addr or "unknown"
        with _login_lock:
            for old in list(_login_attempts):
                while _login_attempts[old] and _login_attempts[old][0] < now - 300:
                    _login_attempts[old].popleft()
                if not _login_attempts[old]:
                    del _login_attempts[old]
            attempts = _login_attempts[key]
            if len(attempts) >= 10:
                return jsonify(error="Too many login attempts; try again later."), 429
            attempts.append(now)
    if session.get("logged_in"):
        user = load_users().get(session.get("user"))
        if not user or user.get("status") != "active" or not hmac.compare_digest(session.get("password_version", ""), session_password_version(user.get("password_hash"))):
            session.clear()
            return jsonify(error="Session expired"), 401
        session["role"] = user.get("role", "moderator")
        session["permissions"] = user.get("permissions", [])
    if request.method == "GET" and session.get("logged_in"):
        perms = session.get("permissions", [])
        role = session.get("role", "")
        is_admin = (role in ("owner", "administrator") or "all" in perms)
        path = request.path
        if not is_admin:
            if path.startswith("/api/panel/update") or path.startswith("/api/system/update"):
                return jsonify(error="Administrator permission required"), 403
            elif path.startswith("/api/files/") and "filemanager" not in perms:
                return jsonify(error="Owner permission required"), 403
            elif path.startswith("/api/config") and "parameters" not in perms:
                return jsonify(error="Owner permission required"), 403
            elif path.startswith("/api/log") and "console" not in perms:
                return jsonify(error="Owner permission required"), 403
            elif path.startswith("/api/backup") and "backups" not in perms:
                return jsonify(error="Owner permission required"), 403
            elif (path.startswith("/api/panel-addons/scheduled_world_archiver/download") or path.startswith("/api/panel-addons/episode_world_archiver/download")) and "backups" not in perms and "filemanager" not in perms:
                return jsonify(error="Owner permission required"), 403
            elif path.startswith("/api/server/properties") and "parameters" not in perms:
                return jsonify(error="Owner permission required"), 403
            elif path.startswith("/api/access/users") and "access" not in perms:
                return jsonify(error="Owner permission required"), 403
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.path.startswith("/api/player/") and request.is_json:
            data = request.get_json(silent=True) or {}
            player = data.get("player", "")
            item = data.get("item", "")
            param = data.get("param", "")
            if not isinstance(player, str) or (player and not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", player)):
                return jsonify(error="Invalid player name"), 400
            if not isinstance(item, str) or (item and not re.fullmatch(r"[a-z0-9_:.\-/]{1,128}", item)):
                return jsonify(error="Invalid item identifier"), 400
            if not isinstance(param, str) or any(ord(c) < 32 or ord(c) == 127 or c == "\\" for c in param):
                return jsonify(error="Invalid action parameter"), 400
        expected = session.get("csrf_token", "")
        supplied = request.headers.get("X-CSRF-Token", "") or request.form.get("csrf_token", "")
        if not expected or not hmac.compare_digest(expected, supplied):
            return jsonify(error="Invalid CSRF token"), 403
        if request.path.startswith("/api/") and session.get("logged_in"):
            # Operations are mapped to specific sub-user permissions or admin roles
            path = request.path
            permission = None
            if path == "/api/command": permission = "console"
            elif path == "/api/server/power": permission = "power"
            elif path == "/api/action":
                act = (request.get_json(silent=True) or {}).get("action", "")
                permission = "power" if act in ("restart", "stop", "start") else "parameters"
            elif path.startswith("/api/player/"): permission = "players"
            elif path.startswith("/api/files/"): permission = "filemanager"
            elif path.startswith("/api/backup"): permission = "backups"
            elif path.startswith("/api/config") or path == "/api/server/properties" or path == "/api/options": permission = "parameters"
            elif path.startswith("/api/software/"): permission = "software"
            elif path.startswith("/api/access/"): permission = "access"
            elif path == "/api/banned-ips" or path == "/api/whitelist/action": permission = "players"
            elif path.startswith("/api/plugin/") or path.startswith("/api/mod/") or path.startswith("/api/modpack") or path.startswith("/api/modrinth/"): permission = "filemanager"
            elif path.startswith("/api/worlds/") or path == "/api/motd": permission = "parameters"
            elif path.startswith("/api/resourcepack/"): permission = "parameters"
            elif path.startswith("/api/panel/update") or path.startswith("/api/system/update"): permission = "admin_only"
            elif path.startswith("/api/system/"): permission = "parameters"
            elif path.startswith("/api/events/"): permission = "events"
            elif path.startswith("/api/panel-addons/") and "/review" in path:
                permission = "allow_any_logged_in"
            elif path.startswith("/api/panel-addons/") and ("/settings" in path or "/action" in path or "/view" in path):
                permission = "parameters"
            perms = session.get("permissions", [])
            role = session.get("role", "")
            is_admin = (role in ("owner", "administrator") or "all" in perms)
            if permission != "allow_any_logged_in" and not is_admin and (not permission or permission not in perms):
                return jsonify(error="Owner permission required"), 403

addon_manager = AddonManager(DATA_DIR, BASE_DIR, app, send_mc_command, ping_mc, SERVER_DIR)
OFFICIAL_REPO_FILE = os.path.join(BASE_DIR, "addons", "repo", "repository.json")
addon_manager.repo_file = OFFICIAL_REPO_FILE
try:
    addon_manager.initialize()
except Exception as e:
    app.logger.warning(f"Error during addon_manager initialization: {e}")

cloud_backup_mgr = CloudBackupManager(DATA_DIR)
website_news = WebsiteNewsManager()

_last_known_server_running = None
_last_known_online_players = None
_state_notification_lock = threading.Lock()

def check_and_notify_server_state(force_state=None):
    global _last_known_server_running, _last_known_online_players
    with _state_notification_lock:
        current_state = force_state if force_state is not None else server_runner.is_running()
        if _last_known_server_running is None:
            # Seed initial state without broadcasting spurious alerts on panel boot
            _last_known_server_running = current_state
            if current_state:
                try:
                    pinfo = ping_mc() or {}
                    _last_known_online_players = set(pinfo.get("player_sample") or [])
                except Exception:
                    _last_known_online_players = set()
            return

        if current_state and not _last_known_server_running:
            _last_known_server_running = True
            try:
                addon_manager.trigger_event("server_start")
                addon_manager.trigger_event("server_online")
            except Exception as err:
                app.logger.error(f"Error dispatching server_start addon event: {err}")
            try:
                pinfo = ping_mc() or {}
                _last_known_online_players = set(pinfo.get("player_sample") or [])
            except Exception:
                _last_known_online_players = set()

        elif not current_state and _last_known_server_running:
            _last_known_server_running = False
            _last_known_online_players = None
            try:
                addon_manager.trigger_event("server_stop")
            except Exception as err:
                app.logger.error(f"Error dispatching server_stop addon event: {err}")

        elif current_state and _last_known_server_running:
            try:
                pinfo = ping_mc() or {}
                raw_sample = pinfo.get("player_sample") or []
                current_players = set(raw_sample)
                if _last_known_online_players is not None:
                    joined = current_players - _last_known_online_players
                    left = _last_known_online_players - current_players
                    max_p = pinfo.get("players_max", 20)
                    online_count = pinfo.get("players_online", len(current_players))
                    for p in joined:
                        if p:
                            addon_manager.trigger_event("player_join", player=p, players_online=online_count, max_players=max_p)
                    for p in left:
                        if p:
                            addon_manager.trigger_event("player_leave", player=p, players_online=online_count, max_players=max_p)
                _last_known_online_players = current_players
            except Exception:
                pass

server_runner.on_state_change = [check_and_notify_server_state]

def _server_state_monitor_loop():
    while True:
        try:
            time.sleep(3)
            check_and_notify_server_state()
        except Exception:
            pass

threading.Thread(target=_server_state_monitor_loop, daemon=True).start()

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        data = request.get_json() if request.is_json else request.form
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        users = load_users()
        user_entry = users.get(username)

        is_valid = False
        role = "moderator"
        permissions = ["console", "players"]

        if user_entry:
            if user_entry.get("status") != "active":
                msg = "This sub-user account has been disabled."
                if request.is_json:
                    return jsonify({"success": False, "message": msg}), 403
                return render_template(
                    "login.html",
                    error=msg,
                    branding=load_branding(),
                    server_domain=get_effective_server_domain(),
                    mc_port=get_server_port(),
                    bedrock_port=get_bedrock_port()
                )
            if verify_password(user_entry.get("password_hash"), password):
                if re.fullmatch(r"[0-9a-f]{64}", user_entry.get("password_hash", "")):
                    user_entry["password_hash"] = hash_password(password)
                    save_users(users)
                is_valid = True
                role = user_entry.get("role", "moderator")
                permissions = user_entry.get("permissions", ["console", "players"])

        if is_valid:
            session["logged_in"] = True
            session["password_version"] = session_password_version(user_entry.get("password_hash"))
            session["user"] = username
            session["role"] = role
            session["permissions"] = permissions
            if request.is_json:
                return jsonify({"success": True})
            return redirect(url_for("dashboard"))

        if request.is_json:
            return jsonify({"success": False, "message": "Invalid username or password"}), 401
        mc_port = get_server_port()
        return render_template(
            "login.html",
            error="Invalid credentials",
            branding=load_branding(),
            server_domain=get_effective_server_domain(),
            mc_port=mc_port,
            bedrock_port=get_bedrock_port()
        )
    mc_port = get_server_port()
    return render_template(
        "login.html",
        branding=load_branding(),
        server_domain=get_effective_server_domain(),
        mc_port=mc_port,
        bedrock_port=get_bedrock_port()
    )

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

@app.route("/")
@login_required
def dashboard():
    rt_cfg = server_runner.load_runtime_config()
    try:
        mem_total_gb = round(psutil.virtual_memory().total / (1024 * 1024 * 1024), 1) if psutil else 4.0
    except Exception:
        mem_total_gb = 4.0
    return render_template(
        "dashboard.html",
        user=session.get("user", "admin"),
        role=session.get("role", "owner"),
        permissions=session.get("permissions", ["all"]),
        branding=load_branding(),
        server_domain=get_effective_server_domain(),
        server_running=server_runner.is_running(),
        level_name=get_main_level_name(),
        mc_port=get_server_port(),
        bedrock_port=get_bedrock_port(),
        panel_port=get_panel_port(),
        panel_commit=get_cached_panel_commit(),
        is_admin=(session.get("role") in ("owner", "administrator") or "all" in session.get("permissions", [])),
        max_heap=rt_cfg.get("max_ram", "4G"),
        total_ram_gb=mem_total_gb
    )

_PANEL_COMMIT_CACHE = {"commit": None, "ts": 0}

def get_cached_panel_commit():
    global _PANEL_COMMIT_CACHE
    now = time.time()
    if _PANEL_COMMIT_CACHE["commit"] and (now - _PANEL_COMMIT_CACHE["ts"] < 60):
        return _PANEL_COMMIT_CACHE["commit"]
    try:
        res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=BASE_DIR, capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            _PANEL_COMMIT_CACHE["commit"] = res.stdout.strip()
            _PANEL_COMMIT_CACHE["ts"] = now
            return _PANEL_COMMIT_CACHE["commit"]
    except Exception:
        pass
    return _PANEL_COMMIT_CACHE["commit"] or "git"

LATEST_PAPER_VERSION_CACHE = {"version": "1.21.4", "ts": 0}

def get_latest_paper_version():
    global LATEST_PAPER_VERSION_CACHE
    now = time.time()
    if now - LATEST_PAPER_VERSION_CACHE["ts"] < 300:
        return LATEST_PAPER_VERSION_CACHE["version"]

    try:
        req = urllib.request.Request(
            "https://fill.papermc.io/v3/projects/paper",
            headers={"User-Agent": get_panel_user_agent(include_email=False)}
        )
        with urllib.request.urlopen(req, timeout=4) as r:
            d = json.loads(r.read())
            v_dict = d.get("versions", {})
            for group, vers in v_dict.items():
                for v in vers:
                    if "-" not in v:
                        LATEST_PAPER_VERSION_CACHE["version"] = v
                        LATEST_PAPER_VERSION_CACHE["ts"] = now
                        return v
    except Exception:
        pass

    return LATEST_PAPER_VERSION_CACHE["version"]

@app.route("/api/software/latest", methods=["GET"])
@login_required
def api_software_latest():
    sw = request.args.get("software", "paper").lower().strip()
    if sw == "neoforge":
        try:
            req = urllib.request.Request("https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.loads(r.read())
                all_v = d.get("versions", [])
                stables = [v for v in all_v if "beta" not in v]
                ver = stables[-1] if stables else all_v[-1]
                return jsonify({
                    "success": True,
                    "software": sw,
                    "latest_version": ver,
                    "display_label": f"NeoForge {ver}"
                })
        except Exception:
            return jsonify({
                "success": True,
                "software": sw,
                "latest_version": "26.3.0.101",
                "display_label": "NeoForge 26.3"
            })
    ver = get_latest_paper_version()
    return jsonify({
        "success": True,
        "software": sw,
        "latest_version": ver,
        "display_label": f"Paper {ver}"
    })

@app.route("/api/status")
@login_required
def api_status():
    cpu_percent = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    try:
        disk_target = SERVER_DIR if os.path.exists(SERVER_DIR) else "/"
        disk = psutil.disk_usage(disk_target)
    except Exception:
        disk = psutil.disk_usage("/")

    jar_exists = os.path.exists(os.path.join(SERVER_DIR, "server.jar"))
    if not _asset_sync_started and jar_exists:
        try:
            check_and_sync_existing_server_assets()
        except Exception:
            pass
    mc_running = server_runner.is_running()
    mc_pid, mc_mem_mb, _ = server_runner.get_process_info()

    # Read active OPs
    ops = []
    ops_file = os.path.join(SERVER_DIR, "ops.json")
    if os.path.exists(ops_file):
        try:
            with open(ops_file, "r") as f:
                ops = json.load(f)
        except Exception:
            pass

    # Read Banned players
    banned = []
    ban_file = os.path.join(SERVER_DIR, "banned-players.json")
    if os.path.exists(ban_file):
        try:
            with open(ban_file, "r") as f:
                banned = json.load(f)
        except Exception:
            pass

    # Whitelist
    whitelist_count = 0
    wl_file = os.path.join(SERVER_DIR, "whitelist.json")
    if os.path.exists(wl_file):
        try:
            with open(wl_file, "r") as f:
                whitelist_count = len(json.load(f))
        except Exception:
            pass

    mc_uptime_sec = 0
    mc_start_time = None
    if mc_running and mc_pid and psutil and psutil.pid_exists(mc_pid):
        try:
            p = psutil.Process(mc_pid)
            mc_start_time = p.create_time()
            mc_uptime_sec = max(0, int(time.time() - mc_start_time))
        except Exception:
            mc_uptime_sec = int(time.time() - START_TIME)
    elif mc_running:
        mc_uptime_sec = int(time.time() - START_TIME)

    ping_info = ping_mc()

    service_active = False
    try:
        if SERVICE_NAME:
            res = subprocess.run(["systemctl", "is-active", SERVICE_NAME], capture_output=True, text=True, timeout=2)
            service_active = (res.stdout.strip() == "active")
    except Exception:
        pass

    return jsonify({
        "server_running": mc_running,
        "service_active": service_active,
        "pid": mc_pid,
        "cpu_usage": cpu_percent,
        "cpu_count": psutil.cpu_count(logical=True) or 1,
        "ram_used_mb": round(mem.used / (1024 * 1024), 1),
        "ram_total_mb": round(mem.total / (1024 * 1024), 1),
        "ram_percent": mem.percent,
        "mc_memory_mb": mc_mem_mb,
        "disk_used_gb": round(disk.used / (1024 * 1024 * 1024), 1),
        "disk_total_gb": round(disk.total / (1024 * 1024 * 1024), 1),
        "disk_percent": disk.percent,
        "uptime_seconds": mc_uptime_sec if mc_running else 0,
        "server_start_time": mc_start_time if mc_running else None,
        "server_time": time.time(),
        "portal_uptime_seconds": int(time.time() - START_TIME),
        "ops_count": len(ops),
        "bans_count": len(banned),
        "whitelist_count": whitelist_count,
        "version": "Active",
        "panel_commit": get_cached_panel_commit(),
        "platform": f"{platform.system()} ({platform.machine()})",
        "os_platform": platform.system(),
        "server_installed": jar_exists,
        "latest_paper_version": get_latest_paper_version(),
        "server_dir": SERVER_DIR,
        "service_name": SERVICE_NAME,
        "screen_session": SCREEN_SESSION,
        "backup_dir": get_backup_dir(),
        "level_name": get_main_level_name(),
        "mc_port": get_server_port(),
        "bedrock_port": get_bedrock_port(),
        "panel_port": get_panel_port(),
        "server_domain": get_effective_server_domain(),
        "spawn_coordinates": get_spawn_coordinates(),
        "java_version": detect_java_version_str(),
        "runtime_config": server_runner.load_runtime_config(),
        "mc_ping": ping_info
    })

@app.route("/api/server/power", methods=["POST"])
@login_required
def api_server_power():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "power" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Power permission required."}), 403

    data = request.get_json() or {}
    action = data.get("action", "").strip()

    if action == "start":
        def start_worker():
            server_runner.start()
        threading.Thread(target=start_worker, daemon=True).start()
        return jsonify({"success": True, "message": "Server startup sequence dispatched."})

    elif action == "stop":
        if not server_runner.is_running():
            return jsonify({"success": False, "message": "Server is already stopped. Cannot stop server."}), 400
        def stop_worker():
            server_runner.stop()
        threading.Thread(target=stop_worker, daemon=True).start()
        return jsonify({"success": True, "message": "World data saved; server shutdown dispatched."})

    elif action == "restart":
        if not server_runner.is_running():
            return jsonify({"success": False, "message": "Server is already stopped. Cannot restart server."}), 400
        def restart_worker():
            server_runner.restart()
        threading.Thread(target=restart_worker, daemon=True).start()
        return jsonify({"success": True, "message": "World data saved; server restart sequence dispatched."})

    return jsonify({"success": False, "message": "Unknown power action"}), 400

@app.route("/api/logs/archive")
@login_required
def api_logs_archive():
    logs_dir = os.path.join(SERVER_DIR, "logs")
    if not os.path.isdir(logs_dir):
        return jsonify({"success": True, "files": []})

    files_list = []
    try:
        for entry in os.listdir(logs_dir):
            if entry == "latest.log" or entry.endswith(".log.gz") or entry.endswith(".log"):
                fp = os.path.join(logs_dir, entry)
                if os.path.isfile(fp):
                    st = os.stat(fp)
                    files_list.append({
                        "name": entry,
                        "size": st.st_size,
                        "size_str": format_file_size(st.st_size),
                        "mtime": st.st_mtime,
                        "date": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "is_latest": (entry == "latest.log")
                    })
        # Sort: latest.log first, then remaining files by modification time descending
        files_list.sort(key=lambda x: (not x["is_latest"], -x["mtime"]))
        return jsonify({"success": True, "files": files_list})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/logs")
@login_required
def api_logs():
    try:
        lines_count = int(request.args.get("lines", 100))
        if lines_count < 1:
            lines_count = 100
        elif lines_count > 5000:
            lines_count = 5000
    except (ValueError, TypeError):
        lines_count = 100
    query = request.args.get("q", "").strip().lower()
    level = request.args.get("level", "ALL").upper()
    req_file = request.args.get("file", "latest.log").strip()

    req_file = os.path.basename(req_file)
    if not (req_file.endswith(".log") or req_file.endswith(".log.gz")):
        req_file = "latest.log"

    target_log = os.path.join(SERVER_DIR, "logs", req_file)
    if not os.path.exists(target_log):
        return jsonify({"logs": [f"Log file '{req_file}' not found."], "file": req_file, "is_latest": (req_file == "latest.log")})

    try:
        raw_lines = []
        if req_file.endswith(".gz"):
            with gzip.open(target_log, "rt", encoding="utf-8", errors="replace") as gz:
                gz_lines = gz.readlines()
                if lines_count > 0 and len(gz_lines) > lines_count * 3:
                    raw_lines = gz_lines[-lines_count * 3:]
                else:
                    raw_lines = gz_lines
        elif req_file == "latest.log":
            raw_lines = tail_file(target_log, lines_count * 2) if lines_count > 0 else tail_file(target_log, 5000)
        else:
            with open(target_log, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                if lines_count > 0 and len(all_lines) > lines_count * 3:
                    raw_lines = all_lines[-lines_count * 3:]
                else:
                    raw_lines = all_lines

        filtered = []
        for line in raw_lines:
            line_str = line.rstrip("\r\n")
            if level != "ALL":
                if level == "WARN" and "WARN" not in line_str:
                    continue
                elif level == "ERROR" and "ERROR" not in line_str and "Exception" not in line_str:
                    continue
                elif level == "INFO" and "INFO" not in line_str:
                    continue
            if query and query not in line_str.lower():
                continue
            filtered.append(line_str)

        final_lines = filtered[-lines_count:] if lines_count > 0 else filtered
        return jsonify({
            "logs": final_lines,
            "file": req_file,
            "is_latest": (req_file == "latest.log"),
            "total_matched": len(filtered)
        })
    except Exception as e:
        return jsonify({"logs": [f"Error reading logs ({req_file}): {e}"], "file": req_file, "is_latest": (req_file == "latest.log")}), 500

@app.route("/api/log/download")
@login_required
def api_download_log():
    req_file = request.args.get("file", "latest.log").strip()
    req_file = os.path.basename(req_file)
    if not (req_file.endswith(".log") or req_file.endswith(".log.gz")):
        req_file = "latest.log"

    target_log = os.path.join(SERVER_DIR, "logs", req_file)
    if os.path.exists(target_log) and os.path.isfile(target_log):
        svc_prefix = (SERVICE_NAME or "server").lower().replace(" ", "-")
        download_name = f"{svc_prefix}-{req_file}" if req_file == "latest.log" else req_file
        return send_file(target_log, as_attachment=True, download_name=download_name)
    return jsonify({"error": f"Log file '{req_file}' not found"}), 404

@app.route("/api/players")
@login_required
def api_players():
    usercache_file = os.path.join(SERVER_DIR, "usercache.json")
    ops_file = os.path.join(SERVER_DIR, "ops.json")
    ban_file = os.path.join(SERVER_DIR, "banned-players.json")
    ip_ban_file = os.path.join(SERVER_DIR, "banned-ips.json")

    users = []
    ops_names = set()
    banned_names = set()
    banned_ips_set = set()

    if os.path.exists(ops_file):
        try:
            with open(ops_file, "r") as f:
                for op in json.load(f):
                    ops_names.add(op.get("name", "").lower())
        except Exception:
            pass

    if os.path.exists(ban_file):
        try:
            with open(ban_file, "r") as f:
                for b in json.load(f):
                    banned_names.add(b.get("name", "").lower())
        except Exception:
            pass

    if os.path.exists(ip_ban_file):
        try:
            with open(ip_ban_file, "r") as f:
                for ib in json.load(f):
                    ip_val = ib.get("ip", "").strip()
                    if ip_val:
                        banned_ips_set.add(ip_val.lower())
        except Exception:
            pass

    ip_map, _ = get_all_player_ips()

    ping_info = ping_mc()
    online_names = [name.lower() for name in ping_info.get("player_sample", [])]

    if os.path.exists(usercache_file):
        try:
            with open(usercache_file, "r") as f:
                cached = json.load(f)
                for u in cached:
                    name = u.get("name")
                    name_lower = (name or "").lower()
                    player_ip = ip_map.get(name_lower)
                    is_ip_banned = bool((player_ip and player_ip.lower() in banned_ips_set) or (name_lower in banned_ips_set))
                    is_online = name_lower in online_names
                    users.append({
                        "name": name,
                        "uuid": u.get("uuid"),
                        "ip": player_ip,
                        "expires": u.get("expiresOn"),
                        "is_op": name_lower in ops_names,
                        "is_banned": name_lower in banned_names,
                        "is_ip_banned": is_ip_banned,
                        "is_online": is_online
                    })
        except Exception:
            pass

    # Sort so online players appear first, then alphabetically
    users.sort(key=lambda x: (not x["is_online"], x["name"].lower()))

    return jsonify({
        "players": users,
        "ops_count": len(ops_names),
        "banned_count": len(banned_names),
        "banned_ip_count": len(banned_ips_set),
        "online_count": ping_info.get("players_online", 0)
    })

@app.route("/api/player/action", methods=["POST"])
@login_required
def api_player_action():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "players" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Player controls permission required."}), 403

    data = request.get_json() or {}
    player = data.get("player", "").strip()
    action = data.get("action", "").strip()
    param = data.get("param", "").strip()

    if not player or not action:
        return jsonify({"success": False, "message": "Missing player or action"}), 400

    if action == "ban_ip":
        success, msg = execute_ip_ban(player, param or "Banned by administrator")
        return jsonify({"success": success, "message": msg})

    if action == "pardon_ip":
        success, msg = execute_ip_pardon(player)
        return jsonify({"success": success, "message": msg})

    coords = get_spawn_coordinates()
    cmd_map = {
        "op": f"op {player}",
        "deop": f"deop {player}",
        "kick": f"kick {player} {param or 'Kicked by administrator'}",
        "ban": f"ban {player} {param or 'Banned by administrator'}",
        "pardon": f"pardon {player}",
        "gamemode_survival": f"gamemode survival {player}",
        "gamemode_creative": f"gamemode creative {player}",
        "gamemode_adventure": f"gamemode adventure {player}",
        "gamemode_spectator": f"gamemode spectator {player}",
        "hardcore_spectator": f"gamemode spectator {player}",
        "kill": f"kill {player}",
        "heal": f"effect give {player} minecraft:instant_health 1 255",
        "feed": f"effect give {player} minecraft:saturation 1 255",
        "clear": f"clear {player}",
        "tp_spawn": f"tp {player} {coords['x']} {coords['y']} {coords['z']}",
        "msg": f"msg {player} [Admin] {param}"
    }

    if action in cmd_map:
        success, msg = send_mc_command(cmd_map[action])
        return jsonify({"success": success, "message": msg})

    return jsonify({"success": False, "message": "Unknown player action"}), 400

def extract_item_display_name(item_dict):
    item_id = str(item_dict.get("id", ""))
    clean_title = item_id.split(":")[-1].replace("_", " ").title()
    comps = item_dict.get("components", {})
    if comps and "minecraft:custom_name" in comps:
        raw_cn = comps["minecraft:custom_name"]
        if isinstance(raw_cn, dict):
            extra = raw_cn.get("extra", [])
            if extra and isinstance(extra, list):
                txt = "".join(x.get("text", "") for x in extra if isinstance(x, dict))
                if txt:
                    return txt
            txt = raw_cn.get("text", "")
            if txt:
                return txt
        elif isinstance(raw_cn, str):
            try:
                parsed = json.loads(raw_cn)
                if isinstance(parsed, dict) and "text" in parsed:
                    return parsed["text"]
            except Exception:
                return raw_cn
    tag = item_dict.get("tag", {})
    if tag and "display" in tag and "Name" in tag["display"]:
        name_val = str(tag["display"]["Name"])
        try:
            parsed = json.loads(name_val)
            if isinstance(parsed, dict) and "text" in parsed:
                return parsed["text"]
        except Exception:
            return name_val
    return clean_title

@app.route("/api/player/inventory")
@login_required
def api_player_inventory():
    target = request.args.get("player", "").strip()
    uuid = request.args.get("uuid", "").strip()

    usercache_file = os.path.join(SERVER_DIR, "usercache.json")
    player_name = target

    # Resolve UUID if only name provided
    if not uuid and target:
        if os.path.exists(usercache_file):
            try:
                with open(usercache_file, "r") as f:
                    for u in json.load(f):
                        if u.get("name", "").lower() == target.lower():
                            uuid = u.get("uuid")
                            player_name = u.get("name")
                            break
            except Exception:
                pass

    if not uuid:
        return jsonify({"success": False, "message": "Player UUID not found"}), 404

    if not re.match(r'^[a-fA-F0-9\-]{32,36}$', uuid):
        return jsonify({"success": False, "message": "Invalid player UUID format"}), 400

    # If player name not resolved yet, lookup from UUID
    if not player_name and os.path.exists(usercache_file):
        try:
            with open(usercache_file, "r") as f:
                for u in json.load(f):
                    if u.get("uuid") == uuid:
                        player_name = u.get("name")
                        break
        except Exception:
            pass

    level_name = get_main_level_name()
    dat_path = os.path.join(SERVER_DIR, level_name, "playerdata", f"{uuid}.dat")
    if not os.path.exists(dat_path) and level_name != "world":
        fallback_path = os.path.join(SERVER_DIR, "world", "playerdata", f"{uuid}.dat")
        if os.path.exists(fallback_path):
            dat_path = fallback_path
    if not os.path.exists(dat_path):
        return jsonify({"success": False, "message": f"No playerdata file found for {player_name or uuid}"}), 404

    try:
        nbt = nbtlib.load(dat_path)

        hotbar = [None] * 9
        main_inv = [None] * 27
        armor = {"head": None, "chest": None, "legs": None, "feet": None}
        offhand = None

        raw_inv = nbt.get("Inventory", [])
        for item in raw_inv:
            s = int(item.get("Slot", -1))
            item_id = str(item.get("id", ""))
            count = int(item.get("count", item.get("Count", 1)))
            clean_name = extract_item_display_name(item)
            item_data = {
                "slot": s,
                "id": item_id,
                "name": clean_name,
                "count": count
            }
            if 0 <= s <= 8:
                hotbar[s] = item_data
            elif 9 <= s <= 35:
                main_inv[s - 9] = item_data
            elif s == 103:
                armor["head"] = item_data
            elif s == 102:
                armor["chest"] = item_data
            elif s == 101:
                armor["legs"] = item_data
            elif s == 100:
                armor["feet"] = item_data
            elif s == -106:
                offhand = item_data

        # Minecraft 1.20.5 / 1.21 modern equipment compound
        equip_compound = nbt.get("equipment", {})
        if equip_compound and isinstance(equip_compound, dict):
            for piece in ["head", "chest", "legs", "feet"]:
                if piece in equip_compound and equip_compound[piece]:
                    eq_item = equip_compound[piece]
                    eq_id = str(eq_item.get("id", ""))
                    if eq_id and eq_id != "minecraft:air":
                        eq_count = int(eq_item.get("count", eq_item.get("Count", 1)))
                        eq_name = extract_item_display_name(eq_item)
                        armor[piece] = {
                            "slot": piece,
                            "id": eq_id,
                            "name": eq_name,
                            "count": eq_count
                        }
            if "offhand" in equip_compound and equip_compound["offhand"]:
                eq_item = equip_compound["offhand"]
                eq_id = str(eq_item.get("id", ""))
                if eq_id and eq_id != "minecraft:air":
                    eq_count = int(eq_item.get("count", eq_item.get("Count", 1)))
                    eq_name = extract_item_display_name(eq_item)
                    offhand = {
                        "slot": "offhand",
                        "id": eq_id,
                        "name": eq_name,
                        "count": eq_count
                    }

        ender_items = []
        for item in nbt.get("EnderItems", []):
            s = int(item.get("Slot", -1))
            item_id = str(item.get("id", ""))
            count = int(item.get("count", item.get("Count", 1)))
            clean_name = extract_item_display_name(item)
            ender_items.append({
                "slot": s,
                "id": item_id,
                "name": clean_name,
                "count": count
            })

        pos = [round(float(p), 1) for p in nbt.get("Pos", [0, 0, 0])]
        health = round(float(nbt.get("Health", 20.0)), 1)
        food = int(nbt.get("foodLevel", 20))
        xp_level = int(nbt.get("XpLevel", 0))
        score = int(nbt.get("Score", 0))
        gamemode_num = int(nbt.get("playerGameType", 0))
        gamemodes = {0: "Survival", 1: "Creative", 2: "Adventure", 3: "Spectator"}
        dimension = str(nbt.get("Dimension", "minecraft:overworld"))

        # Check online status
        ping_info = ping_mc()
        online_sample = [n.lower() for n in ping_info.get("player_sample", [])]
        is_online = player_name.lower() in online_sample if player_name else False

        return jsonify({
            "success": True,
            "player": player_name or uuid,
            "uuid": uuid,
            "is_online": is_online,
            "health": health,
            "food": food,
            "xp_level": xp_level,
            "score": score,
            "gamemode": gamemodes.get(gamemode_num, "Survival"),
            "dimension": dimension.replace("minecraft:", "").replace("_", " ").title(),
            "pos": pos,
            "armor": armor,
            "offhand": offhand,
            "hotbar": hotbar,
            "main_inventory": main_inv,
            "ender_items": ender_items
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error parsing player inventory: {str(e)}"}), 500

@app.route("/api/player/inventory/give", methods=["POST"])
@login_required
def api_player_inventory_give():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "players" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Player controls permission required."}), 403

    data = request.get_json() or {}
    player = data.get("player", "").strip()
    item = data.get("item", "").strip()
    try:
        count = int(data.get("count", 1))
        if count < 1: count = 1
        elif count > 64: count = 64
    except (ValueError, TypeError):
        count = 1

    if not player or not item:
        return jsonify({"success": False, "message": "Player and item required"}), 400

    item_id = item if ":" in item else f"minecraft:{item}"
    success, msg = send_mc_command(f"give {player} {item_id} {count}")
    return jsonify({"success": success, "message": f"Issued: give {player} {item_id} {count}"})

@app.route("/api/player/inventory/clear", methods=["POST"])
@login_required
def api_player_inventory_clear():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "players" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Player controls permission required."}), 403

    data = request.get_json() or {}
    player = data.get("player", "").strip()
    item = data.get("item", "").strip()

    if not player:
        return jsonify({"success": False, "message": "Player name required"}), 400

    if item:
        item_id = item if ":" in item else f"minecraft:{item}"
        cmd = f"clear {player} {item_id}"
    else:
        cmd = f"clear {player}"

    success, msg = send_mc_command(cmd)
    return jsonify({"success": success, "message": f"Executed: {cmd}"})

def get_installed_mc_version():
    meta_path = os.path.join(SERVER_DIR, "installed_software.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                v = data.get("version")
                if v:
                    return str(v).strip()
        except Exception:
            pass
    jar_path = os.path.join(SERVER_DIR, "server.jar")
    if os.path.exists(jar_path):
        try:
            with zipfile.ZipFile(jar_path, "r") as zf:
                if "version.json" in zf.namelist():
                    vdata = json.loads(zf.read("version.json"))
                    v = vdata.get("id") or vdata.get("name")
                    if v:
                        return str(v).strip()
        except Exception:
            pass
    return "1.21.1"

_asset_sync_lock = threading.Lock()
_asset_sync_started = False

def check_and_sync_existing_server_assets(force=False):
    """
    Checks if an existing Minecraft server exists (server.jar or installed_software.json),
    and if static/mcassets is empty or missing catalog, downloads textures in the background.
    """
    global _asset_sync_started
    with _asset_sync_lock:
        if _asset_sync_started and not force:
            return None
        jar_path = os.path.join(SERVER_DIR, "server.jar")
        meta_path = os.path.join(SERVER_DIR, "installed_software.json")
        has_server = os.path.exists(jar_path) or os.path.exists(meta_path)
        if not has_server:
            return None

        png_count = 0
        if os.path.isdir(MCASSETS_DIR):
            png_count = len([f for f in os.listdir(MCASSETS_DIR) if f.endswith(".png") and not f.startswith("empty_")])
        catalog_exists = os.path.exists(os.path.join(MCASSETS_DIR, "catalog.json"))

        if force or png_count < 50 or not catalog_exists:
            _asset_sync_started = True
            v = get_installed_mc_version()
            logging.info(f"Existing Minecraft server detected (version {v}, {png_count} assets). Starting background mc asset generation...")
            try:
                from fetch_mc_assets import sync_mc_assets
                t = threading.Thread(
                    target=sync_mc_assets,
                    args=(v,),
                    daemon=True,
                    name=f"mc-assets-sync-{v}"
                )
                t.start()
                return t
            except Exception as e:
                logging.warning(f"Could not trigger mc-assets sync: {e}")
                return None
        return None

# Check on module initialization for existing server
try:
    check_and_sync_existing_server_assets()
except Exception:
    pass

@app.route("/api/mc-item-icon/<path:item_id>")
def api_mc_item_icon(item_id):
    clean = item_id.lower().replace("minecraft:", "").strip()
    clean = re.sub(r"[^a-z0-9_\-]", "", clean)
    cached = os.path.join(MCASSETS_DIR, f"{clean}.png")
    if os.path.exists(cached) and os.path.getsize(cached) > 50:
        return send_from_directory(MCASSETS_DIR, f"{clean}.png", mimetype="image/png", max_age=604800)

    # 1. First priority for 3D block entities & special items: Minecraft Wiki Invicon
    try:
        from fetch_mc_assets import WIKI_PREFERRED_ITEMS, fetch_wiki_item
        if clean in WIKI_PREFERRED_ITEMS:
            if fetch_wiki_item(clean, cached):
                return send_from_directory(MCASSETS_DIR, f"{clean}.png", mimetype="image/png", max_age=604800)
    except Exception:
        pass

    # 2. Dynamic fallback check using installed version candidates
    try:
        from fetch_mc_assets import get_version_candidates
        v_list = get_version_candidates(get_installed_mc_version())
    except Exception:
        v_list = ["1.21.4", "1.21.1", "1.21"]

    candidates = []
    for v in v_list:
        candidates.extend([
            f"https://assets.mcasset.cloud/{v}/assets/minecraft/textures/item/{clean}.png",
            f"https://assets.mcasset.cloud/{v}/assets/minecraft/textures/block/{clean}.png",
            f"https://raw.githubusercontent.com/PrismarineJS/minecraft-assets/master/data/{v}/items/{clean}.png",
            f"https://raw.githubusercontent.com/PrismarineJS/minecraft-assets/master/data/{v}/blocks/{clean}.png"
        ])

    for tmpl in candidates:
        try:
            req = urllib.request.Request(tmpl, headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    data = resp.read()
                    if len(data) > 50:
                        os.makedirs(MCASSETS_DIR, exist_ok=True)
                        with open(cached, "wb") as f_out:
                            f_out.write(data)
                        return send_from_directory(MCASSETS_DIR, f"{clean}.png", mimetype="image/png", max_age=604800)
        except Exception:
            continue

    # 3. Third fallback: Minecraft Wiki MediaWiki API & Invicon
    try:
        from fetch_mc_assets import fetch_wiki_item
        if fetch_wiki_item(clean, cached):
            return send_from_directory(MCASSETS_DIR, f"{clean}.png", mimetype="image/png", max_age=604800)
    except Exception:
        pass

    return jsonify({"error": "Icon not found"}), 404

@app.route("/api/mc-item-info/<path:item_id>")
def api_mc_item_info(item_id):
    clean = item_id.lower().replace("minecraft:", "").strip()
    clean = re.sub(r"[^a-z0-9_\-]", "", clean)
    try:
        from fetch_mc_assets import get_item_description, format_item_name
        info = get_item_description(clean)
        return jsonify({
            "success": True,
            "id": f"minecraft:{clean}",
            "clean_id": clean,
            "name": format_item_name(clean),
            "icon_url": f"/api/mc-item-icon/{clean}",
            "description": info.get("description", ""),
            "wiki_title": info.get("title", ""),
            "wiki_url": info.get("wiki_url", f"https://minecraft.wiki/w/{clean}")
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/mc-items-list")
def api_mc_items_list():
    catalog_path = os.path.join(MCASSETS_DIR, "catalog.json")
    if os.path.exists(catalog_path):
        return send_from_directory(MCASSETS_DIR, "catalog.json", mimetype="application/json", max_age=86400)
    check_and_sync_existing_server_assets()
    return jsonify([])

@app.route("/api/mc-assets/sync", methods=["POST"])
@login_required
def api_mc_assets_sync():
    version = (request.json or {}).get("version") if request.is_json else None
    if not version:
        version = get_installed_mc_version()
    try:
        from fetch_mc_assets import sync_mc_assets
        threading.Thread(
            target=sync_mc_assets,
            args=(version,),
            daemon=True,
            name=f"mc-assets-manual-sync-{version}"
        ).start()
        return jsonify({"success": True, "message": f"Asset synchronization started for Minecraft {version}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── Portal Branding & Customization APIs ──────────────────────

@app.route("/api/system/java", methods=["GET", "POST"])
@login_required
def api_system_java():
    if request.method == "POST":
        curr_perms = session.get("permissions", [])
        if "all" not in curr_perms and "parameters" not in curr_perms and "access" not in curr_perms and session.get("role") not in ("owner", "administrator"):
            return jsonify({"success": False, "message": "Permission denied: Administrative privileges required to configure Java runtime."}), 403

        data = request.get_json(silent=True) or {}
        req_path = str(data.get("java_path", "")).strip()
        is_test_only = bool(data.get("test_only", False))

        if not req_path or req_path.lower() in ("default", "system", "auto"):
            if is_test_only:
                def_bin = shutil.which("java") or "java"
                vstr = detect_java_version_str(def_bin)
                return jsonify({"success": True, "message": "System default runtime is valid.", "version_str": vstr})

            SERVER_CONFIG.pop("java_path", None)
            target_cfg = CONFIG_FILE
            try:
                cfg = {}
                if os.path.exists(target_cfg):
                    with open(target_cfg, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                cfg.pop("java_path", None)
                with open(target_cfg, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)
            except Exception as e:
                return jsonify({"success": False, "message": f"Failed to persist config: {e}"}), 500

            active = detect_java_bin()
            vstr = detect_java_version_str(active)
            return jsonify({
                "success": True,
                "message": f"Java runtime reset to system default auto-detection ({vstr}). Server restart required to apply.",
                "path": active,
                "version_str": vstr
            })

        resolved = shutil.which(req_path) or (req_path if os.path.isfile(req_path) and os.access(req_path, os.X_OK) else None)
        if not resolved:
            return jsonify({"success": False, "message": f"Specified binary '{req_path}' was not found or is not executable."}), 400

        single_info = get_single_java_info(resolved)
        if not single_info:
            return jsonify({"success": False, "message": f"Binary '{resolved}' executed but failed to report valid Java version information."}), 400

        if is_test_only:
            return jsonify({
                "success": True,
                "message": f"Verified valid executable: {single_info['vendor']} Java {single_info['major_version'] or ''}",
                "path": resolved,
                "version_str": single_info["version_str"],
                "major_version": single_info["major_version"],
                "vendor": single_info["vendor"]
            })

        SERVER_CONFIG["java_path"] = resolved
        target_cfg = CONFIG_FILE
        try:
            cfg = {}
            if os.path.exists(target_cfg):
                with open(target_cfg, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["java_path"] = resolved
            with open(target_cfg, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            return jsonify({"success": False, "message": f"Failed to persist Java selection to config: {e}"}), 500

        return jsonify({
            "success": True,
            "message": f"Switched Java runtime to {single_info['vendor']} Java {single_info['major_version'] or ''} ({resolved}). Restart server for changes to take effect.",
            "path": resolved,
            "version_str": single_info["version_str"],
            "major_version": single_info["major_version"],
            "vendor": single_info["vendor"]
        })

    active_bin = detect_java_bin()
    ver_str = detect_java_version_str(active_bin)
    major_ver = None
    m = re.search(r'version\s+"(\d+)', ver_str)
    if m:
        major_ver = int(m.group(1))
    elif "1.8." in ver_str:
        major_ver = 8

    configured_path = SERVER_CONFIG.get("java_path") or ""
    all_javas = detect_all_installed_javas()

    info = {
        "installed": bool(active_bin),
        "path": active_bin,
        "configured_path": configured_path,
        "is_custom": bool(configured_path),
        "version_str": ver_str,
        "major_version": major_ver,
        "platform": platform.system(),
        "arch": platform.machine(),
        "is_compatible_1_21": bool(major_ver and major_ver >= 21),
        "recommendation": "Java 21 (Temurin / OpenJDK) recommended for Minecraft 1.20.5 - 1.21.x",
        "available": all_javas
    }
    return jsonify({"success": True, "java": info, "available": all_javas})

@app.route("/api/connect/dynip", methods=["GET"])
@login_required
def api_connect_dynip():
    mc_port = get_server_port()
    bedrock_port = get_bedrock_port()
    pub_ip = get_server_public_ip()
    domain = get_effective_server_domain()

    return jsonify({
        "success": True,
        "public_ip": pub_ip,
        "domain": domain,
        "java_port": mc_port,
        "dynip_java": f"{pub_ip}:{mc_port}",
        "bedrock_port": bedrock_port,
        "dynip_bedrock": f"{pub_ip}:{bedrock_port}",
        "standard_address": f"{domain}:{mc_port}" if mc_port != 25565 else domain,
        "bedrock_address": domain
    })

@app.route("/api/system/autostart", methods=["GET", "POST"])
@login_required
def api_system_autostart():
    if request.method == "POST":
        curr_perms = session.get("permissions", [])
        if "all" not in curr_perms and session.get("role") != "owner":
            return jsonify({"success": False, "message": "Permission denied: Owner privileges required to configure auto-start."}), 403

        data = request.get_json(silent=True) or {}
        enable = bool(data.get("enabled", True))
        ok, msg = autostart.set_autostart(enable, DATA_DIR)
        status = autostart.get_autostart_status(DATA_DIR)
        return jsonify({"success": ok, "message": msg, "autostart": status})

    status = autostart.get_autostart_status(DATA_DIR)
    return jsonify({"success": True, "autostart": status})

@app.route("/api/branding/icon/upload", methods=["POST"])
@login_required
def api_upload_branding_icon():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and "access" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Parameter or Access management required."}), 403

    if "icon" not in request.files and "file" not in request.files:
        return jsonify({"success": False, "message": "No image file uploaded."}), 400

    file = request.files.get("icon") or request.files.get("file")
    if not file or not file.filename:
        return jsonify({"success": False, "message": "No file selected."}), 400

    allowed_exts = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".ico", ".gif"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_exts:
        return jsonify({"success": False, "message": f"Unsupported file format ({ext}). Allowed: PNG, SVG, JPG, WEBP, ICO."}), 400

    upload_dir = os.path.join(STATIC_DIR, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"branding_logo_{int(time.time())}{ext}"
    target_path = os.path.join(upload_dir, filename)
    file.save(target_path)

    icon_url = f"/static/uploads/{filename}"
    branding = load_branding()
    branding["header_icon"] = "custom"
    branding["header_icon_custom"] = icon_url
    save_branding(branding)

    return jsonify({
        "success": True,
        "message": "Brand logo uploaded and applied successfully!",
        "icon_url": icon_url,
        "branding": branding
    })

@app.route("/favicon.ico")
@app.route("/api/branding/favicon")
def api_branding_favicon():
    branding = load_branding()
    icon_key = branding.get("header_icon", "tplink")
    custom_val = (branding.get("header_icon_custom") or "").strip()

    # 1. Custom uploaded image (PNG, JPG, ICO, WEBP, SVG file)
    if icon_key == "custom" and custom_val:
        if custom_val.startswith("/static/"):
            rel_path = custom_val[len("/static/"):].lstrip("/")
            local_path = os.path.join(STATIC_DIR, rel_path)
            if os.path.exists(local_path) and os.path.isfile(local_path):
                ext = os.path.splitext(local_path)[1].lower()
                mime = "image/png"
                if ext == ".svg": mime = "image/svg+xml"
                elif ext in (".jpg", ".jpeg"): mime = "image/jpeg"
                elif ext == ".webp": mime = "image/webp"
                elif ext == ".ico": mime = "image/x-icon"
                elif ext == ".gif": mime = "image/gif"
                return send_file(local_path, mimetype=mime)
        elif custom_val.startswith("<svg"):
            svg_content = custom_val
            if "xmlns=" not in svg_content:
                svg_content = svg_content.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"')
            return Response(svg_content, mimetype="image/svg+xml")

    # 2. Preset SVG from HEADER_ICONS
    svg_raw = HEADER_ICONS.get(icon_key, HEADER_ICONS["tplink"])
    if icon_key == "tplink":
        svg_content = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 28"><rect width="36" height="28" rx="5" fill="#006622"/>{svg_raw.replace("<svg", "").replace("</svg>", "")}</svg>'
    else:
        svg_content = svg_raw
        if "xmlns=" not in svg_content:
            svg_content = svg_content.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"')

    return Response(svg_content, mimetype="image/svg+xml", headers={"Cache-Control": "no-cache, must-revalidate"})

@app.route("/api/branding", methods=["GET"])
def api_get_branding():
    return jsonify({"success": True, "branding": load_branding()})

@app.route("/api/branding", methods=["POST"])
@login_required
def api_save_branding():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and "access" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Parameter or Access management required."}), 403

    data = request.get_json() or {}
    cur = load_branding()

    brand_name = data.get("brand_name", cur.get("brand_name", "FIESTA-LINK")).strip()
    tagline = data.get("tagline", cur.get("tagline", "The Reliable Choice")).strip()
    model_number = data.get("model_number", cur.get("model_number", "Model No. FS-GATEWAY-PRO")).strip()
    browser_title = data.get("browser_title", cur.get("browser_title", f"{brand_name} | Console")).strip()
    footer_text = data.get("footer_text", cur.get("footer_text", "Made by Fiesta and it's developers, Mockup UI of Legacy TP-Link Portal")).strip()
    server_desc = data.get("server_desc", cur.get("server_desc", "Server Gateway Console")).strip()
    header_icon = data.get("header_icon", cur.get("header_icon", "tplink")).strip()
    header_icon_custom = data.get("header_icon_custom", cur.get("header_icon_custom", "")).strip()
    server_domain = data.get("server_domain", cur.get("server_domain", "")).strip()
    public_site_url = data.get("public_site_url", cur.get("public_site_url", "")).strip()
    bedrock_server_name = data.get("bedrock_server_name", cur.get("bedrock_server_name", cur.get("brand_name", "Minecraft Server"))).strip()

    if not brand_name:
        return jsonify({"success": False, "message": "Brand name cannot be blank."}), 400

    new_branding = {
        "brand_name": brand_name,
        "tagline": tagline,
        "model_number": model_number,
        "browser_title": browser_title,
        "footer_text": footer_text,
        "server_desc": server_desc,
        "header_icon": header_icon,
        "header_icon_custom": header_icon_custom,
        "server_domain": server_domain,
        "public_site_url": public_site_url,
        "bedrock_server_name": bedrock_server_name
    }
    save_branding(new_branding)
    return jsonify({"success": True, "message": "Portal branding updated successfully!", "branding": new_branding})

@app.route("/api/settings/domain", methods=["POST"])
@login_required
def api_update_server_domain():
    curr_role = session.get("role", "")
    curr_perms = session.get("permissions", [])
    is_admin = (curr_role in ["owner", "administrator"]) or ("all" in curr_perms)
    if not is_admin:
        return jsonify({"success": False, "message": "Permission denied: Only administrators can edit the server domain."}), 403

    data = request.get_json() or {}
    domain = (data.get("server_domain") or "").strip()
    if not domain:
        return jsonify({"success": False, "message": "Server domain cannot be empty."}), 400

    cur = load_branding()
    cur["server_domain"] = domain
    save_branding(cur)
    return jsonify({
        "success": True,
        "message": f"Server domain updated to '{domain}' successfully!",
        "server_domain": domain
    })

@app.route("/api/branding/reset", methods=["POST"])
@login_required
def api_reset_branding():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and "access" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied."}), 403

    save_branding(DEFAULT_BRANDING.copy())
    return jsonify({"success": True, "message": "Branding reset to factory default.", "branding": DEFAULT_BRANDING.copy()})

@app.route("/api/world/spawn", methods=["GET", "POST"])
@login_required
def api_world_spawn():
    if request.method == "GET":
        return jsonify({"success": True, "spawn": get_spawn_coordinates()})

    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Parameter management required."}), 403

    data = request.get_json() or {}
    try:
        x = int(data.get("x", DEFAULT_SPAWN_COORDINATES["x"]))
        y = int(data.get("y", DEFAULT_SPAWN_COORDINATES["y"]))
        z = int(data.get("z", DEFAULT_SPAWN_COORDINATES["z"]))
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Coordinates X, Y, and Z must be valid integers."}), 400

    dimension = str(data.get("dimension", DEFAULT_SPAWN_COORDINATES["dimension"])).strip()
    try:
        radius = max(0, int(data.get("radius", DEFAULT_SPAWN_COORDINATES["radius"])))
    except (ValueError, TypeError):
        radius = 0

    new_spawn = {
        "x": x,
        "y": y,
        "z": z,
        "dimension": dimension,
        "radius": radius
    }
    save_spawn_coordinates(new_spawn)

    cmd_msg = ""
    if server_runner.is_running():
        try:
            ok, _ = send_mc_command(f"setworldspawn {x} {y} {z}")
            if ok:
                cmd_msg = " and synchronized with in-game world"
        except Exception:
            pass

    return jsonify({
        "success": True,
        "message": f"World spawn updated to X: {x}, Y: {y}, Z: {z}{cmd_msg}!",
        "spawn": new_spawn
    })

@app.route("/api/system/settings", methods=["GET", "POST"])
@login_required
def api_system_settings():
    global SERVER_DIR, SERVICE_NAME, SCREEN_SESSION, SERVER_CONFIG, ALLOWED_CONFIGS

    if request.method == "GET":
        b = load_branding()
        return jsonify({
            "success": True,
            "settings": {
                "server_dir": SERVER_DIR,
                "service_name": SERVICE_NAME,
                "screen_session": SCREEN_SESSION,
                "backup_dir": get_backup_dir(),
                "server_domain": b.get("server_domain", ""),
                "public_site_url": b.get("public_site_url", ""),
                "bedrock_server_name": b.get("bedrock_server_name") or DEFAULT_BRANDING.get("bedrock_server_name", "Minecraft Server"),
                "spawn_coordinates": get_spawn_coordinates(),
                "java_path": SERVER_CONFIG.get("java_path", ""),
                "active_java": detect_java_version_str(),
                "admin_email": get_contact_email(),
                "panel_port": get_panel_port()
            }
        })

    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and session.get("role") != "owner":
        return jsonify({"success": False, "message": "Permission denied: Owner privileges required to update server environment."}), 403

    data = request.get_json() or {}
    s_dir = (data.get("server_dir") or "").strip()
    svc_name = (data.get("service_name") or "").strip()
    scr_name = (data.get("screen_session") or "").strip()
    b_dir = (data.get("backup_dir") or "").strip()

    target_cfg = CONFIG_FILE
    existing = {}
    if os.path.exists(target_cfg):
        try:
            with open(target_cfg, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    if s_dir:
        abs_sdir = os.path.abspath(s_dir)
        os.makedirs(abs_sdir, exist_ok=True)
        existing["server_dir"] = abs_sdir
        SERVER_DIR = abs_sdir
        SERVER_CONFIG["server_dir"] = abs_sdir
        server_runner.server_dir = abs_sdir
        ALLOWED_CONFIGS.clear()
        ALLOWED_CONFIGS.update(get_allowed_configs())

    if svc_name:
        existing["service_name"] = svc_name
        SERVICE_NAME = svc_name
        SERVER_CONFIG["service_name"] = svc_name
        server_runner.service_name = svc_name

    if scr_name:
        existing["screen_session"] = scr_name
        SCREEN_SESSION = scr_name
        SERVER_CONFIG["screen_session"] = scr_name
        server_runner.screen_session = scr_name

    if b_dir:
        abs_bdir = os.path.abspath(b_dir)
        os.makedirs(abs_bdir, exist_ok=True)
        existing["backup_dir"] = abs_bdir
        SERVER_CONFIG["backup_dir"] = abs_bdir

    if "java_path" in data:
        j_req = str(data.get("java_path") or "").strip()
        if not j_req or j_req.lower() in ("default", "system", "auto"):
            existing.pop("java_path", None)
            SERVER_CONFIG.pop("java_path", None)
        else:
            resolved = shutil.which(j_req) or (j_req if os.path.isfile(j_req) and os.access(j_req, os.X_OK) else None)
            if resolved:
                existing["java_path"] = resolved
                SERVER_CONFIG["java_path"] = resolved

    if "admin_email" in data:
        em_req = str(data.get("admin_email") or "").strip()
        if em_req:
            existing["admin_email"] = em_req
            SERVER_CONFIG["admin_email"] = em_req
        else:
            existing.pop("admin_email", None)
            SERVER_CONFIG.pop("admin_email", None)

    if "panel_port" in data or "port" in data:
        raw_p = data.get("panel_port") if "panel_port" in data else data.get("port")
        try:
            p_val = int(raw_p)
            if 1 <= p_val <= 65535:
                existing["port"] = p_val
                SERVER_CONFIG["port"] = p_val
        except (ValueError, TypeError):
            pass

    try:
        with open(target_cfg, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        return jsonify({"success": False, "message": f"Failed to persist config: {str(e)}"}), 500

    return jsonify({
        "success": True,
        "message": "Gateway server environment configuration updated and saved to config.json!",
        "settings": {
            "server_dir": SERVER_DIR,
            "service_name": SERVICE_NAME,
            "screen_session": SCREEN_SESSION,
            "backup_dir": get_backup_dir(),
            "java_path": SERVER_CONFIG.get("java_path", ""),
            "active_java": detect_java_version_str(),
            "admin_email": get_contact_email(),
            "panel_port": get_panel_port()
        }
    })

# ── FiestaPanel In-Place System Update APIs (Admin Only) ────────
_PANEL_UPDATE_CACHE = {"data": None, "ts": 0}

def get_panel_git_info(fetch_remote=False):
    """
    Inspects local git repository and optionally queries remote upstream for pending commits.
    Returns structured status with commit hashes, dates, messages, and changelog.
    """
    global _PANEL_UPDATE_CACHE
    now = time.time()
    if not fetch_remote and _PANEL_UPDATE_CACHE["data"] and (now - _PANEL_UPDATE_CACHE["ts"] < 60):
        return _PANEL_UPDATE_CACHE["data"]

    info = {
        "success": True,
        "is_git_repo": False,
        "current_commit": "unknown",
        "current_commit_full": "",
        "current_commit_date": "",
        "current_commit_msg": "",
        "current_branch": "main",
        "latest_commit": "unknown",
        "latest_commit_full": "",
        "latest_commit_date": "",
        "latest_commit_msg": "",
        "commits_behind": 0,
        "update_available": False,
        "remote_url": "https://github.com/open-fiestasmp/FiestaPanel",
        "changelog": [],
        "last_checked": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "error": None
    }

    git_dir = os.path.join(BASE_DIR, ".git")
    if not os.path.exists(git_dir):
        info["error"] = "Not a git repository"
        return info

    info["is_git_repo"] = True

    try:
        # Get remote URL
        res = subprocess.run(["git", "config", "--get", "remote.origin.url"], cwd=BASE_DIR, capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            info["remote_url"] = res.stdout.strip()

        # Current branch
        res = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=BASE_DIR, capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            info["current_branch"] = res.stdout.strip()

        # Current commit hash
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BASE_DIR, capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            full = res.stdout.strip()
            info["current_commit_full"] = full
            info["current_commit"] = full[:7]
            info["latest_commit_full"] = full
            info["latest_commit"] = full[:7]

        # Current commit date
        res = subprocess.run(["git", "log", "-1", "--format=%cd", "--date=iso"], cwd=BASE_DIR, capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            info["current_commit_date"] = res.stdout.strip()

        # Current commit subject
        res = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=BASE_DIR, capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            info["current_commit_msg"] = res.stdout.strip()
            info["latest_commit_msg"] = res.stdout.strip()

        # Fetch remote if requested
        branch = info["current_branch"] or "main"
        if fetch_remote:
            fetch_res = subprocess.run(
                ["git", "fetch", "origin", branch, "--quiet", "--no-tags"],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=15
            )
            if fetch_res.returncode != 0:
                info["error"] = f"Failed to fetch upstream: {fetch_res.stderr.strip() or 'Network timeout'}"

        # Inspect upstream origin/<branch>
        target_ref = f"origin/{branch}"
        res = subprocess.run(["git", "rev-parse", target_ref], cwd=BASE_DIR, capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            full_rem = res.stdout.strip()
            info["latest_commit_full"] = full_rem
            info["latest_commit"] = full_rem[:7]

            res_d = subprocess.run(["git", "log", "-1", "--format=%cd", "--date=iso", target_ref], cwd=BASE_DIR, capture_output=True, text=True, timeout=3)
            if res_d.returncode == 0 and res_d.stdout.strip():
                info["latest_commit_date"] = res_d.stdout.strip()

            res_m = subprocess.run(["git", "log", "-1", "--format=%s", target_ref], cwd=BASE_DIR, capture_output=True, text=True, timeout=3)
            if res_m.returncode == 0 and res_m.stdout.strip():
                info["latest_commit_msg"] = res_m.stdout.strip()

            # Commits behind count
            res_c = subprocess.run(["git", "rev-list", "--count", f"HEAD..{target_ref}"], cwd=BASE_DIR, capture_output=True, text=True, timeout=3)
            if res_c.returncode == 0 and res_c.stdout.strip().isdigit():
                behind = int(res_c.stdout.strip())
                info["commits_behind"] = behind
                info["update_available"] = (behind > 0)

                if behind > 0:
                    res_log = subprocess.run(
                        ["git", "log", "--pretty=format:%h|%an|%s|%cd", "--date=short", f"HEAD..{target_ref}", "-n", "30"],
                        cwd=BASE_DIR,
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if res_log.returncode == 0 and res_log.stdout.strip():
                        changelog = []
                        for line in res_log.stdout.strip().split("\n"):
                            parts = line.split("|", 3)
                            if len(parts) == 4:
                                changelog.append({
                                    "hash": parts[0],
                                    "author": parts[1],
                                    "message": parts[2],
                                    "date": parts[3]
                                })
                        info["changelog"] = changelog

        _PANEL_UPDATE_CACHE["data"] = info
        _PANEL_UPDATE_CACHE["ts"] = now
    except Exception as e:
        info["error"] = str(e)
        app.logger.warning(f"Error checking git panel status: {e}")

    return info


def apply_panel_git_update():
    """
    Executes git pull origin main and triggers a delayed background restart of fiesta-portal.service.
    """
    git_dir = os.path.join(BASE_DIR, ".git")
    if not os.path.exists(git_dir):
        return False, "Not a git repository"

    try:
        # Run git pull origin main
        pull_res = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=30
        )
        if pull_res.returncode != 0:
            err = pull_res.stderr.strip() or pull_res.stdout.strip() or "Git pull failed"
            return False, f"Git pull failed: {err}"

        # Clear commit and update caches
        global _PANEL_COMMIT_CACHE, _PANEL_UPDATE_CACHE
        _PANEL_COMMIT_CACHE = {"commit": None, "ts": 0}
        _PANEL_UPDATE_CACHE = {"data": None, "ts": 0}

        def trigger_restart():
            time.sleep(1.2)
            try:
                chk = subprocess.run(["sudo", "/usr/bin/systemctl", "is-active", "fiesta-portal.service"], capture_output=True, text=True, timeout=5)
                if chk.returncode == 0 or "active" in chk.stdout:
                    subprocess.run(["sudo", "/usr/bin/systemctl", "restart", "fiesta-portal.service"], timeout=15)
                else:
                    app.logger.info("fiesta-portal.service not active; skipping systemctl restart")
            except Exception as ex:
                app.logger.error(f"Error restarting fiesta-portal.service: {ex}")

        threading.Thread(target=trigger_restart, daemon=True).start()
        return True, "FiestaPanel updated successfully! Portal service is restarting..."
    except Exception as e:
        return False, str(e)


@app.route("/api/panel/update/check", methods=["GET"])
@app.route("/api/system/update/check", methods=["GET"])
@login_required
def api_panel_update_check():
    curr_perms = session.get("permissions", [])
    curr_role = session.get("role", "")
    is_admin = (curr_role in ("owner", "administrator") or "all" in curr_perms)
    if not is_admin:
        return jsonify({"success": False, "error": "Administrator privileges required to check updates."}), 403

    do_fetch = request.args.get("fetch", "1").lower() in ("1", "true", "yes")
    status_info = get_panel_git_info(fetch_remote=do_fetch)
    return jsonify(status_info)


@app.route("/api/panel/update/apply", methods=["POST"])
@app.route("/api/system/update/apply", methods=["POST"])
@login_required
def api_panel_update_apply():
    curr_perms = session.get("permissions", [])
    curr_role = session.get("role", "")
    is_admin = (curr_role in ("owner", "administrator") or "all" in curr_perms)
    if not is_admin:
        return jsonify({"success": False, "error": "Administrator privileges required to apply updates."}), 403

    ok, msg = apply_panel_git_update()
    if not ok:
        return jsonify({"success": False, "error": msg}), 500

    return jsonify({"success": True, "message": msg})


# ── Sub-Users & Access Control APIs (Aternos Style) ───────────
@app.route("/api/access/users", methods=["GET"])
@login_required
def api_get_subusers():
    users = load_users()
    clean_list = []
    for uname, u in users.items():
        clean_list.append({
            "username": uname,
            "role": u.get("role", "moderator"),
            "permissions": u.get("permissions", []),
            "status": u.get("status", "active"),
            "created_at": u.get("created_at", "N/A")
        })
    return jsonify({
        "success": True,
        "users": clean_list,
        "current_user": session.get("user", "admin"),
        "current_role": session.get("role", "owner"),
        "current_permissions": session.get("permissions", ["all"])
    })

@app.route("/api/access/users", methods=["POST"])
@login_required
def api_create_subuser():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "access" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Access management required."}), 403

    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    role = data.get("role", "moderator").strip()
    permissions = data.get("permissions", [])

    if not username or not re.match(r"^[a-zA-Z0-9_\-]{3,24}$", username):
        return jsonify({"success": False, "message": "Username must be 3-24 alphanumeric characters."}), 400
    if not password or len(password) < 12:
        return jsonify({"success": False, "message": "Password must be at least 12 characters long."}), 400

    users = load_users()
    if username.lower() in [u.lower() for u in users.keys()] or username.lower() == ADMIN_USER.lower():
        return jsonify({"success": False, "message": f"User '{username}' already exists."}), 400

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    users[username] = {
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "permissions": permissions,
        "created_at": now_str,
        "status": "active"
    }
    save_users(users)
    return jsonify({"success": True, "message": f"Sub-user '{username}' created successfully."})

@app.route("/api/access/users/<username>", methods=["PUT"])
@login_required
def api_update_subuser(username):
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "access" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Access management required."}), 403

    users = load_users()
    target_key = None
    for k in users.keys():
        if k.lower() == username.lower():
            target_key = k
            break

    if not target_key:
        return jsonify({"success": False, "message": "User not found."}), 404

    data = request.get_json() or {}
    user_entry = users[target_key]

    if user_entry.get("role") == "owner" and session.get("user") != target_key:
        return jsonify({"success": False, "message": "Cannot modify primary owner account."}), 403

    if "role" in data and user_entry.get("role") != "owner":
        user_entry["role"] = str(data["role"])
    if "permissions" in data and user_entry.get("role") != "owner":
        user_entry["permissions"] = list(data["permissions"])
    if "status" in data and user_entry.get("role") != "owner":
        user_entry["status"] = "active" if data["status"] == "active" else "disabled"
    if "password" in data:
        return jsonify({"success": False, "message": "Use the Change Password control to update passwords."}), 400

    users[target_key] = user_entry
    save_users(users)
    if target_key == session.get("user"):
        session["password_version"] = session_password_version(user_entry.get("password_hash"))
    return jsonify({"success": True, "message": f"Sub-user '{target_key}' updated successfully."})

@app.route("/api/access/users/<username>/password", methods=["POST"])
@login_required
def api_change_user_password(username):
    if session.get("role") != "owner":
        return jsonify(success=False, message="Only the owner can manage passwords."), 403
    data = request.get_json(silent=True) or {}
    current = data.get("current_password", "")
    password = data.get("new_password", "")
    if not isinstance(current, str) or not isinstance(password, str):
        return jsonify(success=False, message="Invalid password."), 400
    password = password.strip()
    if len(password) < 12:
        return jsonify(success=False, message="Password must be at least 12 characters long."), 400
    users = load_users()
    owner = users.get(session.get("user"))
    if not owner or not verify_password(owner.get("password_hash"), current.strip()):
        return jsonify(success=False, message="Your current password is incorrect."), 403
    target_key = next((key for key in users if key.lower() == username.lower()), None)
    if target_key is None:
        return jsonify(success=False, message="User not found."), 404
    if users[target_key].get("role") == "owner" and target_key != session.get("user"):
        return jsonify(success=False, message="Cannot change another owner's password."), 403
    users[target_key]["password_hash"] = hash_password(password)
    save_users(users)
    if target_key == session.get("user"):
        session["password_version"] = session_password_version(users[target_key]["password_hash"])
    return jsonify(success=True, message="Password updated. Other sessions for this account have been signed out.")

@app.route("/api/access/users/<username>", methods=["DELETE"])
@login_required
def api_delete_subuser(username):
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "access" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Access management required."}), 403

    users = load_users()
    target_key = None
    for k in users.keys():
        if k.lower() == username.lower():
            target_key = k
            break

    if not target_key:
        return jsonify({"success": False, "message": "User not found."}), 404

    if users[target_key].get("role") == "owner" or target_key == ADMIN_USER:
        return jsonify({"success": False, "message": "Cannot delete primary owner account."}), 400
    if target_key == session.get("user"):
        return jsonify({"success": False, "message": "Cannot delete your own active account."}), 400

    del users[target_key]
    save_users(users)
    return jsonify({"success": True, "message": f"Sub-user '{target_key}' deleted."})

# ── Server Software & Version Management (Aternos Style) ─────
def resolve_software_download(software, version, build="latest"):
    software = software.lower().strip()
    version = version.strip()

    if software == "purpur":
        if build == "latest":
            url_meta = f"https://api.purpurmc.org/v2/purpur/{version}"
            req = urllib.request.Request(url_meta, headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req, timeout=5) as r:
                d = json.loads(r.read())
                build = d.get("builds", {}).get("latest")
                if not build:
                    raise ValueError(f"No builds found for Purpur {version}")
        download_url = f"https://api.purpurmc.org/v2/purpur/{version}/{build}/download"
        return download_url, f"purpur-{version}-{build}.jar", f"Purpur {version} (Build #{build})"

    elif software in ("paper", "folia"):
        proj = software
        url_meta = f"https://fill.papermc.io/v3/projects/{proj}/versions/{version}"
        req = urllib.request.Request(url_meta, headers={"User-Agent": get_panel_user_agent(include_email=False)})
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())
            builds = d.get("builds", [])
            if not builds:
                raise ValueError(f"No builds found for {proj.capitalize()} {version}")
            target_build = max(builds, key=lambda b: int(b) if str(b).isdigit() else 0) if build == "latest" else build

        url_build = f"https://fill.papermc.io/v3/projects/{proj}/versions/{version}/builds/{target_build}"
        req_b = urllib.request.Request(url_build, headers={"User-Agent": get_panel_user_agent(include_email=False)})
        with urllib.request.urlopen(req_b, timeout=5) as r_b:
            d_b = json.loads(r_b.read())
            dl_info = d_b.get("downloads", {}).get("server:default", {})
            download_url = dl_info.get("url")
            filename = dl_info.get("name", f"{proj}-{version}-{target_build}.jar")
            if not download_url:
                raise ValueError(f"No download URL found for {proj.capitalize()} {version} build {target_build}")
            return download_url, filename, f"{proj.capitalize()} {version} (Build #{target_build})"

    elif software == "vanilla":
        req = urllib.request.Request("https://launchermeta.mojang.com/mc/game/version_manifest_v2.json", headers={"User-Agent": get_panel_user_agent(include_email=False)})
        with urllib.request.urlopen(req, timeout=5) as r:
            manifest = json.loads(r.read())
            matched = [v for v in manifest.get("versions", []) if v.get("id") == version]
            if not matched:
                raise ValueError(f"Version {version} not found in Mojang release manifest")
            pkg_url = matched[0]["url"]

        req_pkg = urllib.request.Request(pkg_url, headers={"User-Agent": get_panel_user_agent(include_email=False)})
        with urllib.request.urlopen(req_pkg, timeout=5) as r_pkg:
            pkg_data = json.loads(r_pkg.read())
            download_url = pkg_data.get("downloads", {}).get("server", {}).get("url")
            if not download_url:
                raise ValueError(f"Server download not available for Vanilla {version}")
            return download_url, f"vanilla-{version}-server.jar", f"Mojang Vanilla {version}"

    elif software == "fabric":
        loaders_url = "https://meta.fabricmc.net/v2/versions/loader"
        req = urllib.request.Request(loaders_url, headers={"User-Agent": get_panel_user_agent(include_email=False)})
        with urllib.request.urlopen(req, timeout=5) as r:
            loaders = json.loads(r.read())
            loader_ver = loaders[0]["version"]

        download_url = f"https://meta.fabricmc.net/v2/versions/loader/{version}/{loader_ver}/1.0.1/server/jar"
        return download_url, f"fabric-server-mc.{version}-loader.{loader_ver}.jar", f"Fabric {version} (Loader {loader_ver})"

    elif software == "spigot":
        download_url = f"https://download.getbukkit.org/spigot/spigot-{version}.jar"
        return download_url, f"spigot-{version}.jar", f"Spigot {version}"

    elif software == "neoforge":
        nf_ver = None
        try:
            req_m = urllib.request.Request("https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req_m, timeout=5) as r_m:
                d_m = json.loads(r_m.read())
                all_nf = d_m.get("versions", [])
                if version in all_nf:
                    nf_ver = version
                else:
                    if version.startswith("1.20."):
                        sub = version.split(".")[2]
                        prefix = f"20.{sub}."
                    elif version.startswith("1.21."):
                        sub = version.split(".")[2]
                        prefix = f"21.{sub}."
                    elif version == "1.21":
                        prefix = "21.0."
                    elif "." in version:
                        prefix = f"{version}."
                    else:
                        prefix = f"{version}."

                    candidates = [v for v in all_nf if v.startswith(prefix)]
                    if candidates:
                        stables = [v for v in candidates if "beta" not in v]
                        nf_ver = stables[-1] if stables else candidates[-1]
        except Exception:
            pass

        if not nf_ver:
            NEOFORGE_FALLBACK_MAP = {
                "26.3": "26.3.0.101",
                "26.2": "26.2.0.93",
                "26.1": "26.1.2.126",
                "1.21.11": "21.11.48",
                "1.21.10": "21.10.64",
                "1.21.8": "21.8.57",
                "1.21.5": "21.5.101",
                "1.21.4": "21.4.161",
                "1.21.3": "21.3.100",
                "1.21.1": "21.1.265",
                "1.21": "21.0.167",
                "1.20.6": "20.6.144",
                "1.20.4": "20.4.253",
                "1.20.2": "20.2.93"
            }
            nf_ver = NEOFORGE_FALLBACK_MAP.get(version, "21.1.265")

        download_url = f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{nf_ver}/neoforge-{nf_ver}-installer.jar"
        return download_url, f"neoforge-{nf_ver}-installer.jar", f"NeoForge {version} ({nf_ver})"

    elif software == "forge":
        forge_ver = None
        try:
            req_f = urllib.request.Request("https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req_f, timeout=5) as r_f:
                d_f = json.loads(r_f.read())
                promos = d_f.get("promos", {})
                forge_ver = promos.get(f"{version}-recommended") or promos.get(f"{version}-latest")
        except Exception:
            pass
        if not forge_ver:
            FORGE_FALLBACKS = {
                "1.21.1": "52.0.37", "1.20.4": "49.0.38", "1.20.2": "48.1.0", "1.20.1": "47.2.0",
                "1.19.4": "45.2.0", "1.19.2": "43.3.0", "1.18.2": "40.2.0",
                "1.16.5": "36.2.39", "1.12.2": "14.23.5.2860"
            }
            forge_ver = FORGE_FALLBACKS.get(version, "47.2.0")
        download_url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{version}-{forge_ver}/forge-{version}-{forge_ver}-installer.jar"
        return download_url, f"forge-{version}-{forge_ver}-installer.jar", f"Forge {version} ({forge_ver})"

    elif software == "quilt":
        loader_ver = "0.26.1"
        try:
            req_q = urllib.request.Request("https://meta.quiltmc.org/v3/versions/loader", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req_q, timeout=5) as r_q:
                loaders = json.loads(r_q.read())
                if loaders and isinstance(loaders, list):
                    loader_ver = loaders[0].get("version", loader_ver)
        except Exception:
            pass
        download_url = f"https://meta.quiltmc.org/v3/versions/loader/{version}/{loader_ver}/server/jar"
        return download_url, f"quilt-server-mc.{version}-loader.{loader_ver}.jar", f"Quilt {version} (Loader {loader_ver})"

    elif software == "velocity":
        vel_ver = version if version and version != "latest" else "3.3.0-SNAPSHOT"
        build_num = "436"
        try:
            req_v = urllib.request.Request(f"https://fill.papermc.io/v3/projects/velocity/versions/{vel_ver}", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req_v, timeout=5) as r_v:
                d_v = json.loads(r_v.read())
                builds = d_v.get("builds", [])
                build_num = builds[-1] if builds else "436"
        except Exception:
            pass
        download_url = f"https://fill.papermc.io/v3/projects/velocity/versions/{vel_ver}/builds/{build_num}/downloads/velocity-{vel_ver}-{build_num}.jar"
        return download_url, f"velocity-{vel_ver}-{build_num}.jar", f"Velocity {vel_ver} (Build #{build_num})"

    elif software == "bungeecord":
        download_url = "https://ci.md-5.net/job/BungeeCord/lastSuccessfulBuild/artifact/bootstrap/target/BungeeCord.jar"
        return download_url, "BungeeCord.jar", "BungeeCord (Latest Stable)"

    elif software in ("bedrock", "bds"):
        bds_ver = version if version and version != "latest" else "1.21.51.02"
        download_url = f"https://www.minecraft.net/bedrockdedicatedserver/bin-linux/bedrock-server-{bds_ver}.zip"
        return download_url, f"bedrock-server-{bds_ver}.zip", f"Bedrock Dedicated Server {bds_ver}"

    else:
        raise ValueError(f"Unsupported software engine: {software}")

@app.route("/api/software/info", methods=["GET"])
@login_required
def api_software_info():
    jar_path = os.path.join(SERVER_DIR, "server.jar")
    bak_path = os.path.join(SERVER_DIR, "server.jar.bak")
    meta_path = os.path.join(SERVER_DIR, "installed_software.json")

    has_jar = os.path.exists(jar_path)
    info = {
        "installed": has_jar,
        "software": "Purpur" if has_jar else "None",
        "version": "1.21.11" if has_jar else "Not installed",
        "build": "Build #2568" if has_jar else "N/A",
        "jar_size_mb": 0,
        "has_backup": os.path.exists(bak_path),
        "backup_size_mb": round(os.path.getsize(bak_path) / (1024*1024), 1) if os.path.exists(bak_path) else 0,
        "installed_at": "N/A",
        "java_version": detect_java_version_str(),
        "runtime_config": server_runner.load_runtime_config()
    }

    if os.path.exists(jar_path):
        info["jar_size_mb"] = round(os.path.getsize(jar_path) / (1024*1024), 1)
        info["installed_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(jar_path)))

    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                info.update(json.load(f))
        except Exception:
            pass
    else:
        try:
            with zipfile.ZipFile(jar_path, "r") as zf:
                if "version.json" in zf.namelist():
                    vdata = json.loads(zf.read("version.json"))
                    info["version"] = vdata.get("id", info["version"])
        except Exception:
            pass

    try:
        res = subprocess.run(["java", "-version"], capture_output=True, text=True, timeout=2)
        java_out = res.stderr or res.stdout
        info["java_version"] = java_out.splitlines()[0] if java_out else "OpenJDK 21"
    except Exception:
        pass

    return jsonify({"success": True, "info": info})

@app.route("/api/software/versions", methods=["GET"])
@login_required
def api_software_versions():
    sw = request.args.get("software", "purpur").lower().strip()
    versions = []

    try:
        if sw == "purpur":
            req = urllib.request.Request("https://api.purpurmc.org/v2/purpur", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.loads(r.read())
                raw_vers = d.get("versions", [])
                versions = list(reversed(raw_vers))
        elif sw in ("paper", "folia"):
            req = urllib.request.Request(f"https://fill.papermc.io/v3/projects/{sw}", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.loads(r.read())
                v_dict = d.get("versions", {})
                all_v = []
                for major in ["26.3", "26.2", "26.1", "1.21", "1.20", "1.19", "1.18", "1.17", "1.16"]:
                    if major in v_dict:
                        all_v.extend(v_dict[major])
                versions = all_v if all_v else ["26.3", "26.2", "26.1.2", "1.21.11", "1.21.10", "1.21.4", "1.20.4"]
        elif sw == "vanilla":
            req = urllib.request.Request("https://launchermeta.mojang.com/mc/game/version_manifest_v2.json", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.loads(r.read())
                versions = [v["id"] for v in d.get("versions", []) if v.get("type") == "release"][:40]
        elif sw == "fabric":
            req = urllib.request.Request("https://meta.fabricmc.net/v2/versions/game", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.loads(r.read())
                versions = [v["version"] for v in d if v.get("stable")][:40]
        elif sw == "spigot":
            versions = ["26.3", "26.2", "26.1.2", "1.21.11", "1.21.4", "1.20.4", "1.19.4", "1.18.2", "1.16.5"]
        elif sw == "neoforge":
            req = urllib.request.Request("https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.loads(r.read())
                all_v = d.get("versions", [])
                mc_set = set()
                for v in all_v:
                    parts = v.split(".")
                    if len(parts) >= 2:
                        if parts[0] == "20":
                            mc_set.add(f"1.20.{parts[1]}")
                        elif parts[0] == "21":
                            mc_set.add(f"1.21.{parts[1]}" if parts[1] != "0" else "1.21")
                        elif parts[0] in ["26", "25"]:
                            mc_set.add(f"{parts[0]}.{parts[1]}")
                def v_key(ver):
                    try:
                        return [int(x) for x in ver.split(".")]
                    except Exception:
                        return [0]
                versions = sorted(list(mc_set), key=v_key, reverse=True)
        elif sw == "forge":
            versions = ["1.21.1", "1.20.4", "1.20.2", "1.20.1", "1.19.4", "1.19.2", "1.18.2", "1.16.5", "1.12.2"]
        elif sw == "quilt":
            versions = ["1.21.4", "1.21.1", "1.20.4", "1.20.1", "1.19.4", "1.18.2"]
        elif sw == "velocity":
            versions = ["3.3.0-SNAPSHOT", "3.2.0-SNAPSHOT", "3.1.2-SNAPSHOT"]
        elif sw == "bungeecord":
            versions = ["latest"]
        elif sw == "folia":
            versions = ["26.3", "26.2", "26.1.2", "1.21.11", "1.21.4", "1.20.4", "1.19.4"]
        elif sw in ("bedrock", "bds"):
            versions = ["1.21.51.02", "1.21.44.01", "1.21.30.03", "1.20.81.01"]
    except Exception as e:
        if sw == "neoforge":
            versions = ["26.3", "26.2", "26.1", "1.21.11", "1.21.10", "1.21.4", "1.21.3", "1.21.1", "1.21", "1.20.6", "1.20.4", "1.20.2"]
        elif sw == "forge":
            versions = ["1.21.1", "1.20.4", "1.20.2", "1.20.1", "1.19.4", "1.19.2", "1.18.2", "1.16.5", "1.12.2"]
        elif sw == "quilt":
            versions = ["1.21.4", "1.21.1", "1.20.4", "1.20.1", "1.19.4", "1.18.2"]
        elif sw == "velocity":
            versions = ["3.3.0-SNAPSHOT", "3.2.0-SNAPSHOT"]
        elif sw == "bungeecord":
            versions = ["latest"]
        elif sw == "folia":
            versions = ["26.3", "26.2", "26.1.2", "1.21.11", "1.21.4", "1.20.4", "1.19.4"]
        elif sw in ("bedrock", "bds"):
            versions = ["1.21.51.02", "1.21.44.01", "1.21.30.03", "1.20.81.01"]
        else:
            versions = ["26.3", "26.2", "26.1.2", "1.21.11", "1.21.10", "1.21.4", "1.20.4", "1.19.4", "1.18.2", "1.16.5"]

    def get_java_req(v_str):
        if not v_str or v_str == "latest": return "Java 21+"
        if v_str.startswith("26.") or v_str.startswith("1.21") or v_str.startswith("1.20.5") or v_str.startswith("1.20.6"):
            return "Java 21+"
        elif v_str.startswith("1.18") or v_str.startswith("1.19") or v_str.startswith("1.20"):
            return "Java 17+"
        elif v_str.startswith("1.17"):
            return "Java 16"
        elif any(v_str.startswith(x) for x in ["1.16", "1.15", "1.14", "1.13", "1.12", "1.8", "1.7"]):
            return "Java 8 / 11"
        return "Java 17 / 21"

    advisories = {v: get_java_req(v) for v in versions}
    return jsonify({"success": True, "software": sw, "versions": versions, "java_advisories": advisories})

@app.route("/api/software/install", methods=["POST"])
@login_required
def api_software_install():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "software" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Software management required."}), 403

    data = request.get_json() or {}
    software = data.get("software", "purpur").lower().strip()
    version = data.get("version", "1.21.11").strip()
    build = data.get("build", "latest").strip()

    try:
        download_url, filename, label = resolve_software_download(software, version, build)
    except Exception as e:
        return jsonify({"success": False, "message": f"Failed to resolve download URL: {str(e)}"}), 400

    jar_path = os.path.join(SERVER_DIR, "server.jar")
    bak_path = os.path.join(SERVER_DIR, "server.jar.bak")
    tmp_path = os.path.join(SERVER_DIR, f"server.jar.{int(time.time())}.tmp")
    meta_path = os.path.join(SERVER_DIR, "installed_software.json")

    try:
        req = urllib.request.Request(download_url, headers={"User-Agent": get_panel_user_agent(include_email=False)})
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp_path, "wb") as f_out:
            shutil.copyfileobj(resp, f_out)

        dl_size = os.path.getsize(tmp_path)
        if dl_size < 3 * 1024 * 1024:
            if os.path.exists(tmp_path): os.remove(tmp_path)
            return jsonify({"success": False, "message": f"Downloaded jar too small ({dl_size} bytes), download aborted."}), 500

        had_server = os.path.exists(jar_path)
        if had_server:
            send_mc_command("save-all")
            send_mc_command(f"say [Server] Installing {label}, server restarting in a moment...")
            time.sleep(1.5)
            server_runner.stop()
            time.sleep(1)
            shutil.copy2(jar_path, bak_path)

        if data.get("clean_install"):
            try:
                create_backup("pre_reinstall_snapshot")
            except Exception:
                pass
            mods_dir = os.path.join(SERVER_DIR, "mods")
            if os.path.exists(mods_dir):
                shutil.rmtree(mods_dir, ignore_errors=True)
                os.makedirs(mods_dir, exist_ok=True)
            libraries_dir = os.path.join(SERVER_DIR, "libraries")
            if os.path.exists(libraries_dir):
                shutil.rmtree(libraries_dir, ignore_errors=True)

        if software in ("neoforge", "forge"):
            java_bin = detect_java_bin()
            args = [java_bin, "-jar", tmp_path, "--installServer", SERVER_DIR]
            if software == "neoforge":
                args.append("--server.jar")
            res = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=180
            )
            if res.returncode != 0:
                raise RuntimeError(f"{software.capitalize()} installer failed (code {res.returncode}): {res.stderr or res.stdout}")
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
        elif software in ("bedrock", "bds"):
            with zipfile.ZipFile(tmp_path, "r") as zf:
                real_server_dir = os.path.realpath(SERVER_DIR)
                for member in zf.namelist():
                    abs_target = os.path.realpath(os.path.join(SERVER_DIR, member))
                    if not (abs_target == real_server_dir or abs_target.startswith(real_server_dir + os.sep)):
                        raise ValueError(f"Unsafe path in archive: {member}")
                zf.extractall(SERVER_DIR)
            bds_bin = os.path.join(SERVER_DIR, "bedrock_server")
            if os.path.exists(bds_bin):
                try: os.chmod(bds_bin, 0o755)
                except Exception: pass
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
        else:
            shutil.move(tmp_path, jar_path)

        # Auto-accept EULA
        eula_file = os.path.join(SERVER_DIR, "eula.txt")
        if not os.path.exists(eula_file) or data.get("auto_eula", True):
            try:
                with open(eula_file, "w") as f_eula:
                    f_eula.write("# By changing the setting below to TRUE you are indicating your agreement to the Mojang EULA (https://aka.ms/MinecraftEULA).\neula=true\n")
            except Exception:
                pass

        # Ensure default server.properties if missing
        props_file = os.path.join(SERVER_DIR, "server.properties")
        if not os.path.exists(props_file):
            try:
                with open(props_file, "w") as f_props:
                    f_props.write(
                        "#Minecraft server properties - Configured by FiestaPanel\n"
                        "server-port=25565\n"
                        "gamemode=survival\n"
                        "difficulty=easy\n"
                        "pvp=true\n"
                        "max-players=20\n"
                        "online-mode=true\n"
                        "enable-rcon=false\n"
                        "motd=A Minecraft Server Powered by FiestaPanel\n"
                    )
            except Exception:
                pass

        # Memory configuration
        if "ram_gb" in data:
            try:
                ram_val = int(data["ram_gb"])
                rt_cfg = server_runner.load_runtime_config()
                rt_cfg["max_ram"] = f"{ram_val}G"
                server_runner.save_runtime_config(rt_cfg)
            except Exception:
                pass

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(meta_path, "w") as f_meta:
            json.dump({
                "software": "NeoForge" if software == "neoforge" else software.title(),
                "version": version,
                "build": build,
                "installed_at": now_str,
                "jar_name": filename if software != "neoforge" else "server.jar",
                "label": label
            }, f_meta, indent=2)

        # Ensure appropriate addon directory exists for software engine
        if software in ["fabric", "neoforge", "forge", "quilt"]:
            os.makedirs(os.path.join(SERVER_DIR, "mods"), exist_ok=True)
            os.makedirs(os.path.join(SERVER_DIR, "mods", "disabled"), exist_ok=True)
        elif software in ["purpur", "paper", "spigot", "velocity", "bungeecord"]:
            os.makedirs(os.path.join(SERVER_DIR, "plugins"), exist_ok=True)
            os.makedirs(os.path.join(SERVER_DIR, "plugins", "disabled"), exist_ok=True)

        if data.get("autostart", True):
            server_runner.start()

        # Trigger background asset synchronization for newly installed Minecraft version
        try:
            from fetch_mc_assets import sync_mc_assets
            threading.Thread(
                target=sync_mc_assets,
                args=(version,),
                daemon=True,
                name=f"mc-assets-sync-{version}"
            ).start()
            _asset_sync_started = True
        except Exception as e:
            logging.warning(f"Could not trigger asset sync: {e}")

        return jsonify({
            "success": True,
            "message": f"Successfully installed {label}! Server is starting up.",
            "label": label,
            "installed_at": now_str
        })
    except Exception as e:
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except Exception: pass
        return jsonify({"success": False, "message": f"Installation failed: {str(e)}"}), 500

@app.route("/api/software/rollback", methods=["POST"])
@login_required
def api_software_rollback():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "software" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Software management required."}), 403

    jar_path = os.path.join(SERVER_DIR, "server.jar")
    bak_path = os.path.join(SERVER_DIR, "server.jar.bak")
    meta_path = os.path.join(SERVER_DIR, "installed_software.json")

    if not os.path.exists(bak_path):
        return jsonify({"success": False, "message": "No backup server.jar.bak file found to restore."}), 400

    try:
        send_mc_command("save-all")
        time.sleep(1)
        server_runner.stop()
        time.sleep(1)

        current_tmp = os.path.join(SERVER_DIR, "server.jar.pre_rollback")
        if os.path.exists(jar_path):
            shutil.move(jar_path, current_tmp)
        shutil.copy2(bak_path, jar_path)

        if os.path.exists(meta_path):
            try: os.remove(meta_path)
            except Exception: pass

        server_runner.start()
        return jsonify({"success": True, "message": "Successfully rolled back to previous server jar and restarted service."})
    except Exception as e:
        return jsonify({"success": False, "message": f"Rollback failed: {str(e)}"}), 500

@app.route("/api/bans")
@login_required
def api_get_bans():
    ban_file = os.path.join(SERVER_DIR, "banned-players.json")
    ip_ban_file = os.path.join(SERVER_DIR, "banned-ips.json")
    banned = []
    ip_banned = []
    if os.path.exists(ban_file):
        try:
            with open(ban_file, "r") as f:
                banned = json.load(f)
        except Exception:
            pass
    if os.path.exists(ip_ban_file):
        try:
            with open(ip_ban_file, "r") as f:
                ip_banned = json.load(f)
        except Exception:
            pass

    ip_map, reverse_map = get_all_player_ips()
    banned_ips_set = set(b.get("ip", "").strip().lower() for b in ip_banned if b.get("ip"))

    for b in banned:
        pname = (b.get("name") or "").lower().strip()
        pip = ip_map.get(pname)
        if not pip:
            pip = resolve_player_ip(b.get("name"))
        b["ip"] = pip
        b["is_ip_banned"] = bool((pip and pip.lower() in banned_ips_set) or (pname in banned_ips_set))

    for ib in ip_banned:
        cur_ip = (ib.get("ip") or "").strip()
        ib["player"] = reverse_map.get(cur_ip)

    return jsonify({"bans": banned, "ip_bans": ip_banned, "total": len(banned) + len(ip_banned)})

@app.route("/api/ops")
@login_required
def api_get_ops():
    ops_file = os.path.join(SERVER_DIR, "ops.json")
    ops = []
    if os.path.exists(ops_file):
        try:
            with open(ops_file, "r") as f:
                ops = json.load(f)
        except Exception:
            pass
    return jsonify({"ops": ops, "total": len(ops)})

@app.route("/api/whitelist")
@login_required
def api_get_whitelist():
    wl_file = os.path.join(SERVER_DIR, "whitelist.json")
    wl = []
    if os.path.exists(wl_file):
        try:
            with open(wl_file, "r") as f:
                wl = json.load(f)
        except Exception:
            pass

    # Read whitelist status from server.properties
    enabled = False
    props_path = os.path.join(SERVER_DIR, "server.properties")
    if os.path.exists(props_path):
        try:
            with open(props_path, "r") as f:
                for line in f:
                    if line.strip().startswith("white-list="):
                        enabled = line.strip().split("=", 1)[1].lower() == "true"
                        break
        except Exception:
            pass

    return jsonify({"enabled": enabled, "whitelist": wl, "total": len(wl)})

@app.route("/api/whitelist/action", methods=["POST"])
@login_required
def api_whitelist_action():
    data = request.get_json() or {}
    action = data.get("action", "").strip()
    name = data.get("name", "").strip()

    if action == "toggle":
        enabled = data.get("enabled", False)
        cmd = "whitelist on" if enabled else "whitelist off"
        success, msg = send_mc_command(cmd)
        return jsonify({"success": success, "message": f"Whitelist {'enabled' if enabled else 'disabled'}"})

    elif action == "add":
        if not name:
            return jsonify({"success": False, "message": "Player name required"}), 400
        success, msg = send_mc_command(f"whitelist add {name}")
        return jsonify({"success": success, "message": f"Added {name} to whitelist"})

    elif action == "remove":
        if not name:
            return jsonify({"success": False, "message": "Player name required"}), 400
        success, msg = send_mc_command(f"whitelist remove {name}")
        return jsonify({"success": success, "message": f"Removed {name} from whitelist"})

    return jsonify({"success": False, "message": "Unknown action"}), 400

@app.route("/api/server/properties", methods=["GET", "POST"])
@login_required
def api_server_properties():
    props_path = os.path.join(SERVER_DIR, "server.properties")
    if not os.path.exists(props_path):
        return jsonify({"success": False, "message": "server.properties not found"}), 404

    if request.method == "GET":
        props = {}
        try:
            with open(props_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        props[k.strip()] = v.strip()
            return jsonify({"success": True, "properties": props})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500

    elif request.method == "POST":
        curr_perms = session.get("permissions", [])
        if "all" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
            return jsonify({"success": False, "message": "Permission denied: Parameter permission required."}), 403

        raw_updates = request.get_json() or {}
        if not raw_updates:
            return jsonify({"success": False, "message": "No updates provided"}), 400

        updates = {}
        for k, v in raw_updates.items():
            clean_k = re.sub(r'[^a-zA-Z0-9_\.\-]', '', str(k)).strip()
            if clean_k:
                updates[clean_k] = str(v).replace("\r", "").replace("\n", "\\n")

        if not updates:
            return jsonify({"success": False, "message": "No valid property updates provided"}), 400

        try:
            # Backup
            shutil.copy2(props_path, props_path + ".bak")

            lines = []
            seen = set()
            with open(props_path, "r") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and "=" in stripped:
                        k = stripped.split("=", 1)[0].strip()
                        if k in updates:
                            lines.append(f"{k}={updates[k]}\n")
                            seen.add(k)
                        else:
                            lines.append(line)
                    else:
                        lines.append(line)

            for k, v in updates.items():
                if k not in seen:
                    lines.append(f"{k}={v}\n")

            with open(props_path, "w") as f:
                f.writelines(lines)

            return jsonify({"success": True, "message": "Server properties updated successfully (restart needed for some settings)!"})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/server/icon", methods=["GET"])
def api_server_icon():
    icon_path = os.path.join(SERVER_DIR, "server-icon.png")
    if os.path.exists(icon_path):
        return send_file(icon_path, mimetype="image/png")
    return jsonify({"error": "No icon found"}), 404

@app.route("/api/server/icon/upload", methods=["POST"])
@login_required
def api_server_icon_upload():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Parameter permission required."}), 403

    if "icon" not in request.files:
        return jsonify({"success": False, "message": "No image file provided"}), 400
    file = request.files["icon"]
    if not file.filename:
        return jsonify({"success": False, "message": "No file selected"}), 400

    try:
        image = Image.open(file.stream)
        image = image.convert("RGBA")
        image = image.resize((64, 64), Image.Resampling.LANCZOS)
        icon_path = os.path.join(SERVER_DIR, "server-icon.png")
        image.save(icon_path, format="PNG")
        return jsonify({"success": True, "message": "Server icon updated & resized to 64x64 PNG! Restart server to see it in multiplayer list."})
    except Exception as e:
        return jsonify({"success": False, "message": f"Failed to process icon: {str(e)}"}), 500

@app.route("/api/broadcast", methods=["POST"])
@login_required
def api_broadcast():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "console" not in curr_perms and "players" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Console or Player management required."}), 403

    data = request.get_json() or {}
    msg = data.get("message", "").strip()
    msg_type = data.get("type", "chat")

    if not msg:
        return jsonify({"success": False, "message": "Message cannot be empty"}), 400

    json_text = json.dumps(msg)
    if msg_type == "title":
        send_mc_command(f'title @a title {{"text":{json_text},"color":"gold","bold":true}}')
    elif msg_type == "actionbar":
        send_mc_command(f'title @a actionbar {{"text":{json_text},"color":"yellow"}}')
    else:
        clean_msg = re.sub(r'[\r\n]+', ' ', msg).strip()
        send_mc_command(f'say {clean_msg}')

    return jsonify({"success": True, "message": "Broadcast sent!"})

@app.route("/api/command", methods=["POST"])
@login_required
def api_command():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "console" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Console permission required."}), 403

    data = request.get_json() or {}
    cmd = data.get("command", "").strip()
    if not cmd:
        return jsonify({"success": False, "message": "Command is empty"}), 400

    # Intercept ban-ip / banip and pardon-ip / unbanip in console to support offline players & direct IPs
    clean = cmd.lstrip('/')
    parts = clean.split(None, 2)
    cmd_name = parts[0].lower() if parts else ""

    if cmd_name in ("ban-ip", "banip") and len(parts) > 1:
        target = parts[1]
        reason = parts[2] if len(parts) > 2 else "Banned by administrator"
        success, msg = execute_ip_ban(target, reason)
        return jsonify({"success": success, "message": msg})

    if cmd_name in ("pardon-ip", "unbanip", "pardonip") and len(parts) > 1:
        target = parts[1]
        success, msg = execute_ip_pardon(target)
        return jsonify({"success": success, "message": msg})

    success, msg = send_mc_command(cmd)
    return jsonify({"success": success, "message": msg})

# ── Events Manager (Server & Player Events - Admin Only) ────────────────────
EVENTS_FILE = os.path.join(DATA_DIR, "events.json")

def load_events():
    if os.path.exists(EVENTS_FILE):
        try:
            with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d, list):
                    return d
        except Exception as e:
            app.logger.warning(f"Error reading events.json: {e}")
    return []

def get_calendar_feed_token():
    return hashlib.sha256(b"fiesta_cal_feed_" + _get_secret_key()).hexdigest()[:24]

def ical_escape(text):
    if not text:
        return ""
    text = str(text)
    text = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    text = text.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
    return text

def generate_ics(events, calendar_name="Fiesta Minecraft Server Events"):
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//FiestaPanel//Minecraft Events Manager//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ical_escape(calendar_name)}",
        "X-WR-TIMEZONE:UTC"
    ]
    for evt in events:
        eid = evt.get("id", "evt_" + hashlib.md5(str(evt).encode()).hexdigest()[:8])
        title = evt.get("title", "Minecraft Server Event")
        st = evt.get("start_time", "")
        try:
            dt = datetime.datetime.fromisoformat(st)
        except Exception:
            dt = datetime.datetime.now()
        dt_utc = dt.astimezone(datetime.timezone.utc)
        dtstart = dt_utc.strftime("%Y%m%dT%H%M%SZ")
        dtend = (dt_utc + datetime.timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")

        desc_parts = []
        if evt.get("description"):
            desc_parts.append(evt["description"])
        if evt.get("type"):
            desc_parts.append("Type: " + str(evt["type"]).upper())
        if evt.get("target_player"):
            desc_parts.append("Target Player: " + str(evt["target_player"]))
        if evt.get("broadcast_msg"):
            desc_parts.append("Broadcast: " + str(evt["broadcast_msg"]))
        desc_text = "\n".join(desc_parts)

        status_val = "CANCELLED" if evt.get("status") == "cancelled" else "CONFIRMED"

        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{eid}@fiestapanel")
        lines.append(f"DTSTAMP:{now_utc}")
        lines.append(f"DTSTART:{dtstart}")
        lines.append(f"DTEND:{dtend}")
        lines.append(f"SUMMARY:{ical_escape(title)}")
        if desc_text:
            lines.append(f"DESCRIPTION:{ical_escape(desc_text)}")
        lines.append("LOCATION:Minecraft Server")
        lines.append(f"STATUS:{status_val}")

        rec = str(evt.get("recurring", "")).lower()
        if rec == "daily":
            lines.append("RRULE:FREQ=DAILY")
        elif rec == "weekly":
            lines.append("RRULE:FREQ=WEEKLY")
        elif rec == "hourly":
            lines.append("RRULE:FREQ=HOURLY")

        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

def save_events(events_list):
    try:
        with open(EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(events_list, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        app.logger.error(f"Error saving events.json: {e}")
        return False

def execute_event(evt, trigger_source="Manual Admin"):
    title = evt.get("title", "Server Event")
    bcast = evt.get("broadcast_msg", "").strip()
    target_player = evt.get("target_player", "").strip()
    commands_raw = evt.get("commands", "")

    executed_cmds = []

    if bcast:
        clean_bcast = bcast.replace("&", "§")
        if target_player:
            send_mc_command(f'title {target_player} title {{"text":"{title}","color":"gold","bold":true}}')
            send_mc_command(f'msg {target_player} {clean_bcast}')
        else:
            send_mc_command(f'title @a title {{"text":"{title}","color":"gold","bold":true}}')
            send_mc_command(f'title @a actionbar {{"text":"{clean_bcast}","color":"yellow"}}')
            send_mc_command(f'say {clean_bcast}')

    if commands_raw:
        lines = [line.strip() for line in commands_raw.splitlines() if line.strip()]
        for cmd_line in lines:
            c = cmd_line.lstrip("/")
            if target_player:
                c = c.replace("{player}", target_player).replace("%player%", target_player)
            send_mc_command(c)
            executed_cmds.append(c)

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    evt["last_run"] = now_str

    recurring = evt.get("recurring", "none").lower()
    if recurring == "none":
        evt["status"] = "completed"
    else:
        try:
            curr_start = datetime.datetime.fromisoformat(evt.get("start_time"))
            if recurring == "hourly":
                next_start = curr_start + datetime.timedelta(hours=1)
            elif recurring == "daily":
                next_start = curr_start + datetime.timedelta(days=1)
            elif recurring == "weekly":
                next_start = curr_start + datetime.timedelta(weeks=1)
            else:
                next_start = curr_start + datetime.timedelta(days=1)
            evt["start_time"] = next_start.strftime("%Y-%m-%dT%H:%M")
            evt["status"] = "scheduled"
        except Exception:
            evt["status"] = "active"

    return True, f"Event '{title}' executed successfully ({len(executed_cmds)} commands dispatched)."

def _events_scheduler_loop():
    while True:
        try:
            time.sleep(20)
            events = load_events()
            now = datetime.datetime.now()
            modified = False
            for evt in events:
                if evt.get("status") == "scheduled":
                    st = evt.get("start_time")
                    if st:
                        try:
                            evt_time = datetime.datetime.fromisoformat(st)
                            if now >= evt_time:
                                execute_event(evt, trigger_source="Scheduler")
                                modified = True
                        except Exception:
                            pass
            if modified:
                save_events(events)
        except Exception:
            pass

threading.Thread(target=_events_scheduler_loop, daemon=True).start()

def is_admin_for_events():
    return session.get("role") in ("owner", "administrator") or "all" in session.get("permissions", []) or "events" in session.get("permissions", [])

@app.route("/api/events", methods=["GET"])
@login_required
def api_get_events():
    events = load_events()
    total = len(events)
    scheduled = sum(1 for e in events if e.get("status") == "scheduled")
    active = sum(1 for e in events if e.get("status") == "active")
    completed = sum(1 for e in events if e.get("status") == "completed")
    return jsonify({
        "success": True,
        "events": events,
        "counts": {
            "total": total,
            "scheduled": scheduled,
            "active": active,
            "completed": completed
        },
        "is_admin": is_admin_for_events()
    })

@app.route("/api/events/create", methods=["POST"])
@login_required
def api_create_event():
    if not is_admin_for_events():
        return jsonify({"success": False, "message": "Permission denied: Only administrators can configure events."}), 403

    data = request.get_json() or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"success": False, "message": "Event title is required."}), 400

    evt_type = data.get("type", "server").strip().lower()
    start_time = data.get("start_time", "").strip() or datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
    recurring = data.get("recurring", "none").strip().lower()

    new_evt = {
        "id": f"evt_{int(time.time())}_{secrets.token_hex(3)}",
        "title": title,
        "type": evt_type,
        "description": data.get("description", "").strip(),
        "start_time": start_time,
        "recurring": recurring,
        "broadcast_msg": data.get("broadcast_msg", "").strip(),
        "commands": data.get("commands", "").strip(),
        "target_player": data.get("target_player", "").strip(),
        "status": "scheduled",
        "created_by": session.get("user", "admin"),
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_run": None
    }

    events = load_events()
    events.insert(0, new_evt)
    save_events(events)
    return jsonify({"success": True, "message": f"Event '{title}' created successfully!", "event": new_evt})

@app.route("/api/events/update", methods=["POST"])
@login_required
def api_update_event():
    if not is_admin_for_events():
        return jsonify({"success": False, "message": "Permission denied: Only administrators can configure events."}), 403

    data = request.get_json() or {}
    evt_id = data.get("id", "").strip()
    if not evt_id:
        return jsonify({"success": False, "message": "Event ID is required."}), 400

    events = load_events()
    for evt in events:
        if evt.get("id") == evt_id:
            if "title" in data and data["title"].strip():
                evt["title"] = data["title"].strip()
            if "type" in data:
                evt["type"] = data["type"].strip().lower()
            if "description" in data:
                evt["description"] = data["description"].strip()
            if "start_time" in data:
                evt["start_time"] = data["start_time"].strip()
            if "recurring" in data:
                evt["recurring"] = data["recurring"].strip().lower()
            if "broadcast_msg" in data:
                evt["broadcast_msg"] = data["broadcast_msg"].strip()
            if "commands" in data:
                evt["commands"] = data["commands"].strip()
            if "target_player" in data:
                evt["target_player"] = data["target_player"].strip()
            if "status" in data:
                evt["status"] = data["status"].strip().lower()
            save_events(events)
            return jsonify({"success": True, "message": f"Event '{evt['title']}' updated successfully!"})

    return jsonify({"success": False, "message": "Event not found."}), 404

@app.route("/api/events/delete", methods=["POST"])
@login_required
def api_delete_event():
    if not is_admin_for_events():
        return jsonify({"success": False, "message": "Permission denied: Only administrators can configure events."}), 403

    data = request.get_json() or {}
    evt_id = data.get("id", "").strip()
    if not evt_id:
        return jsonify({"success": False, "message": "Event ID is required."}), 400

    events = load_events()
    new_events = [e for e in events if e.get("id") != evt_id]
    if len(new_events) == len(events):
        return jsonify({"success": False, "message": "Event not found."}), 404

    save_events(new_events)
    return jsonify({"success": True, "message": "Event deleted successfully!"})

@app.route("/api/events/trigger", methods=["POST"])
@login_required
def api_trigger_event():
    if not is_admin_for_events():
        return jsonify({"success": False, "message": "Permission denied: Only administrators can configure events."}), 403

    data = request.get_json() or {}
    evt_id = data.get("id", "").strip()
    events = load_events()
    for evt in events:
        if evt.get("id") == evt_id:
            success, msg = execute_event(evt, trigger_source="Manual Admin")
            save_events(events)
            return jsonify({"success": success, "message": msg})

    return jsonify({"success": False, "message": "Event not found."}), 404

@app.route("/api/events/calendar-token", methods=["GET"])
@login_required
def api_get_calendar_token():
    token = get_calendar_feed_token()
    return jsonify({"success": True, "token": token})

@app.route("/api/events/calendar.ics", methods=["GET"])
@app.route("/api/events/feed.ics", methods=["GET"])
def api_events_ics():
    req_token = request.args.get("token", "").strip()
    valid_token = get_calendar_feed_token()
    is_authed = session.get("logged_in") or session.get("user")
    if req_token != valid_token and not is_authed:
        return jsonify({"error": "Unauthorized calendar feed request. Invalid or missing feed token."}), 401

    events = load_events()
    cal_ics = generate_ics(events, calendar_name="Fiesta Minecraft Server Events")
    response = Response(cal_ics, mimetype="text/calendar")
    response.headers["Content-Type"] = "text/calendar; charset=utf-8"
    response.headers["Content-Disposition"] = 'inline; filename="minecraft_events.ics"'
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

@app.route("/api/software/check-update", methods=["GET"])
@login_required
def api_software_check_update():
    meta_path = os.path.join(SERVER_DIR, "installed_software.json")
    cur_sw = "Purpur"
    cur_ver = "1.21.11"
    cur_build = "Build #2568"

    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                d = json.load(f)
                cur_sw = d.get("software", cur_sw)
                cur_ver = d.get("version", cur_ver)
                cur_build = d.get("build", cur_build)
        except Exception:
            pass

    latest_ver = cur_ver
    try:
        sw_low = cur_sw.lower().strip()
        if sw_low == "purpur":
            req = urllib.request.Request("https://api.purpurmc.org/v2/purpur", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req, timeout=3) as r:
                p_data = json.loads(r.read())
                v_list = p_data.get("versions", [])
                if v_list:
                    latest_ver = v_list[-1]
        elif sw_low == "paper":
            req = urllib.request.Request("https://fill.papermc.io/v3/projects/paper", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req, timeout=3) as r:
                p_data = json.loads(r.read())
                v_dict = p_data.get("versions", {})
                for major in ["26.3", "26.2", "26.1", "1.21"]:
                    if major in v_dict and v_dict[major]:
                        latest_ver = v_dict[major][-1]
                        break
        elif sw_low == "neoforge":
            req = urllib.request.Request("https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge", headers={"User-Agent": get_panel_user_agent(include_email=False)})
            with urllib.request.urlopen(req, timeout=3) as r:
                p_data = json.loads(r.read())
                v_list = p_data.get("versions", [])
                if v_list:
                    stables = [v for v in v_list if "beta" not in v]
                    latest_ver = stables[-1] if stables else v_list[-1]
    except Exception:
        pass

    has_update = (latest_ver != cur_ver)
    return jsonify({
        "success": True,
        "has_update": has_update,
        "current_software": cur_sw,
        "current_version": cur_ver,
        "current_build": cur_build,
        "latest_version": latest_ver
    })

# ── Plugins Management ───────────────────────────────────────────────────────
@app.route("/api/plugins")
@login_required
def api_plugins():
    plugins_dir = os.path.join(SERVER_DIR, "plugins")
    plugins_list = []
    if os.path.exists(plugins_dir):
        for f in sorted(os.listdir(plugins_dir)):
            if f.endswith(".jar"):
                p = os.path.join(plugins_dir, f)
                if os.path.isfile(p):
                    size_kb = round(os.path.getsize(p) / 1024, 1)
                    plugins_list.append({
                        "name": f.replace(".jar", ""),
                        "filename": f,
                        "size_kb": size_kb
                    })
    return jsonify({"plugins": plugins_list, "total": len(plugins_list)})

@app.route("/api/plugins/disabled")
@login_required
def api_plugins_disabled():
    disabled_dir = os.path.join(SERVER_DIR, "plugins", "disabled")
    disabled_list = []
    if os.path.exists(disabled_dir):
        for f in sorted(os.listdir(disabled_dir)):
            p = os.path.join(disabled_dir, f)
            if os.path.isfile(p) and (f.endswith(".jar") or f.endswith(".bak")):
                size_kb = round(os.path.getsize(p) / 1024, 1)
                disabled_list.append({
                    "name": f.replace(".jar", "").replace(".bak", ""),
                    "filename": f,
                    "size_kb": size_kb
                })
    return jsonify({"disabled": disabled_list, "total": len(disabled_list)})

@app.route("/api/plugin/delete", methods=["POST"])
@login_required
def api_plugin_delete():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "plugins" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Plugin management required."}), 403

    data = request.get_json() or {}
    raw_name = (data.get("name") or "").strip()
    name = os.path.basename(raw_name)
    if name.lower().endswith(".jar"):
        name = name[:-4]
    if not name or name in (".", "..") or "/" in raw_name or "\\" in raw_name:
        return jsonify({"success": False, "message": "Plugin name required"}), 400

    plugins_dir = os.path.join(SERVER_DIR, "plugins")
    disabled_dir = os.path.join(plugins_dir, "disabled")
    src = os.path.join(plugins_dir, f"{name}.jar")

    if not os.path.isfile(src):
        return jsonify({"success": False, "message": f"Plugin file not found: {name}.jar"}), 404

    os.makedirs(disabled_dir, exist_ok=True)
    dst = os.path.join(disabled_dir, f"{name}.jar")
    try:
        shutil.move(src, dst)
        return jsonify({"success": True, "message": f"{name} moved to disabled/ — restart server to unload."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/plugin/enable", methods=["POST"])
@login_required
def api_plugin_enable():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "plugins" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Plugin management required."}), 403

    data = request.get_json() or {}
    raw_filename = (data.get("filename") or "").strip()
    filename = os.path.basename(raw_filename)
    if not filename or filename in (".", "..") or "/" in raw_filename or "\\" in raw_filename:
        return jsonify({"success": False, "message": "Filename required"}), 400

    plugins_dir = os.path.join(SERVER_DIR, "plugins")
    disabled_dir = os.path.join(plugins_dir, "disabled")
    src = os.path.join(disabled_dir, filename)

    if not os.path.isfile(src):
        return jsonify({"success": False, "message": f"Disabled plugin not found: {filename}"}), 404

    # Ensure extension is .jar
    target_name = filename[:-4] if filename.endswith(".bak") and filename.endswith(".jar.bak") else filename
    if not target_name.endswith(".jar"):
        target_name += ".jar"
    dst = os.path.join(plugins_dir, target_name)

    try:
        shutil.move(src, dst)
        return jsonify({"success": True, "message": f"{target_name} restored to active plugins/ — restart server to load."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/plugin/perm_delete", methods=["POST"])
@login_required
def api_plugin_perm_delete():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "plugins" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Plugin management required."}), 403

    data = request.get_json() or {}
    raw_filename = (data.get("filename") or "").strip()
    filename = os.path.basename(raw_filename)
    if not filename or filename in (".", "..") or "/" in raw_filename or "\\" in raw_filename:
        return jsonify({"success": False, "message": "Filename required"}), 400

    disabled_dir = os.path.join(SERVER_DIR, "plugins", "disabled")
    target = os.path.join(disabled_dir, filename)

    if not os.path.isfile(target):
        return jsonify({"success": False, "message": f"File not found in disabled/: {filename}"}), 404

    try:
        os.remove(target)
        return jsonify({"success": True, "message": f"Permanently deleted {filename}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/plugin/upload", methods=["POST"])
@login_required
def api_plugin_upload():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "plugins" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Plugin management required."}), 403

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file part in request"}), 400
    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"success": False, "message": "No file selected"}), 400
    if not file.filename.lower().endswith(".jar"):
        return jsonify({"success": False, "message": "Only .jar files are allowed"}), 400

    safe_name = os.path.basename(file.filename)
    if not safe_name or safe_name in (".", ".."):
        return jsonify({"success": False, "message": "Invalid filename"}), 400

    plugins_dir = os.path.join(SERVER_DIR, "plugins")
    os.makedirs(plugins_dir, exist_ok=True)
    dst = os.path.join(plugins_dir, safe_name)
    try:
        file.save(dst)
        return jsonify({"success": True, "message": f"{safe_name} uploaded successfully! Restart server to load."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ── Multi-Source Addon & Mod Search Helpers (Modrinth / CurseForge / SpigotMC / BukkitDev) ──
CURSEFORGE_API_KEY = os.environ.get("CURSEFORGE_API_KEY", "$2a$10$bL4bIL5pUWqfcO7KQtnMReakwtfHbNKh6v1uTpKlzhwoueEJQnPnm")

def get_cf_download_url(file_obj):
    if not file_obj or not isinstance(file_obj, dict):
        return ""
    if file_obj.get("downloadUrl"):
        return file_obj["downloadUrl"]
    fid = file_obj.get("id")
    fn = file_obj.get("fileName") or "addon.jar"
    if fid and isinstance(fid, int):
        return f"https://edge.forgecdn.net/files/{fid // 1000}/{fid % 1000}/{urllib.parse.quote(fn)}"
    return ""

def resolve_clean_jar_name(initial_name, resp_url, resp_headers=None):
    fn = ""
    if initial_name and initial_name.lower().endswith(".jar") and initial_name.lower() != "download.jar":
        fn = os.path.basename(initial_name)
    elif resp_headers:
        try:
            fn = resp_headers.get_filename() or ""
        except Exception:
            fn = ""
    if not fn or not fn.lower().endswith(".jar") or fn.lower() == "download.jar":
        path_name = os.path.basename(urllib.parse.urlparse(resp_url).path)
        if path_name and path_name.lower().endswith(".jar") and path_name.lower() != "download.jar":
            fn = path_name
    if not fn or not fn.lower().endswith(".jar") or fn.lower() == "download.jar":
        base = re.sub(r'[^a-zA-Z0-9_\-\.\(\)\+ ]', '_', initial_name or "addon").strip()
        if base.lower().endswith(".jar"):
            base = base[:-4].strip()
        if not base or base.lower() == "download":
            base = "addon"
        fn = f"{base}.jar"
    fn = re.sub(r'[^a-zA-Z0-9_\-\.\(\)\+ ]', '_', fn)
    while fn.lower().endswith(".jar.jar"):
        fn = fn[:-4]
    if not fn.lower().endswith(".jar"):
        fn += ".jar"
    return fn

def normalize_addon_stem(name):
    if not name:
        return ""
    s = str(name).strip().lower()
    for ext in [".jar.bak", ".jar", ".disabled", ".bak", ".zip"]:
        if s.endswith(ext):
            s = s[:-len(ext)]
    s = re.sub(r'\+.*$', '', s)
    s = re.sub(r'[-_]mc1\.\d+(\.\d+)*', '', s)
    s = re.sub(r'[-_]1\.\d+(\.\d+)?[-_]', '-', s)
    s = re.sub(r'[-_](bukkit|paper|spigot|fabric|forge|neoforge|quilt)([-_]|$)', '-', s)
    s = re.sub(r'[-_]v?\d+(\.\d+)+([-_a-z0-9]+)?$', '', s)
    s = re.sub(r'[-_]\d+$', '', s)
    s = re.sub(r'[^a-z0-9]+', '', s)
    return s

def check_addon_exists(folder_dir, project_name=None, candidate_filename=None, project_id=None):
    if not folder_dir or not os.path.isdir(folder_dir):
        return None

    c_names = set()
    if candidate_filename:
        c_names.add(os.path.basename(candidate_filename).lower())

    c_stems = set()
    for item in [candidate_filename, project_name, project_id]:
        if item and not str(item).isdigit():
            st = normalize_addon_stem(item)
            if len(st) >= 3:
                c_stems.add(st)

    locations = [("active", folder_dir), ("disabled", os.path.join(folder_dir, "disabled"))]

    # 1. Exact filename match check
    for status, loc in locations:
        if not os.path.isdir(loc):
            continue
        try:
            for f in os.listdir(loc):
                if f.lower() in c_names or (f.lower().endswith(".bak") and f[:-4].lower() in c_names):
                    return {
                        "matched": True,
                        "filename": f,
                        "status": status,
                        "exact": True,
                        "path": os.path.join(loc, f)
                    }
        except Exception:
            pass

    # 2. Semantic stem match check
    for status, loc in locations:
        if not os.path.isdir(loc):
            continue
        try:
            for f in os.listdir(loc):
                if not (f.endswith(".jar") or f.endswith(".jar.bak") or f.endswith(".bak")):
                    continue
                fst = normalize_addon_stem(f)
                if fst and fst in c_stems:
                    return {
                        "matched": True,
                        "filename": f,
                        "status": status,
                        "exact": False,
                        "path": os.path.join(loc, f)
                    }
        except Exception:
            pass

    return None

def search_spigot(query, limit=15):
    encoded_q = urllib.parse.quote(query)
    url = f"https://api.spiget.org/v2/search/resources/{encoded_q}?size={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": get_panel_user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    except Exception:
        return []

    hits = []
    for item in data:
        rid = item.get("id")
        name = item.get("name", "Unknown")
        tag = item.get("tag", "")
        author_id = item.get("author", {}).get("id") if isinstance(item.get("author"), dict) else item.get("author")
        author_str = f"Spigot #{author_id}" if author_id else "SpigotMC"
        icon_obj = item.get("icon") or {}
        icon_url = icon_obj.get("url") if isinstance(icon_obj, dict) and icon_obj.get("url") else ""
        if icon_url and not icon_url.startswith("http"):
            icon_url = f"https://www.spigotmc.org/{icon_url}"

        safe_fn = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()
        filename = f"{safe_fn}.jar" if safe_fn else f"spigot-{rid}.jar"

        hits.append({
            "project_id": str(rid),
            "slug": str(rid),
            "title": name,
            "description": tag,
            "author": author_str,
            "downloads": item.get("downloads", 0),
            "icon_url": icon_url,
            "categories": ["spigot", "paper", "bukkit"],
            "source": "spigotmc",
            "download_url": f"https://api.spiget.org/v2/resources/{rid}/download",
            "filename": filename
        })
    return hits

def search_curseforge(query, class_id=6, loader=None, limit=15, source_name="curseforge"):
    params = {
        "gameId": 432,
        "classId": class_id,
        "searchFilter": query,
        "pageSize": limit,
        "sortField": 2,  # TotalDownloads
        "sortOrder": "desc"
    }
    loader_map = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
    if loader and loader.lower() in loader_map:
        params["modLoaderType"] = loader_map[loader.lower()]

    url = "https://api.curseforge.com/v1/mods/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": get_panel_user_agent(),
        "x-api-key": CURSEFORGE_API_KEY,
        "Accept": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    except Exception:
        return []

    hits = []
    for item in data.get("data", []):
        latest_files = item.get("latestFiles", [])
        loaders = set()
        matched_file = None
        for f in latest_files:
            gvs = [str(g).lower() for g in f.get("gameVersions", [])]
            for l in ["fabric", "forge", "neoforge", "quilt"]:
                if l in gvs:
                    loaders.add(l)
            if not matched_file and f.get("fileName", "").endswith(".jar"):
                if loader and loader.lower() in loader_map:
                    if loader.lower() in gvs:
                        matched_file = f
                else:
                    matched_file = f
        if not matched_file and latest_files:
            matched_file = latest_files[0]

        dl_url = get_cf_download_url(matched_file) if matched_file else ""
        filename = matched_file.get("fileName") if matched_file else ""

        authors = [a.get("name") for a in item.get("authors", []) if a.get("name")]
        author_str = ", ".join(authors) if authors else "Unknown"
        logo_url = (item.get("logo") or {}).get("thumbnailUrl") or (item.get("logo") or {}).get("url") or ""

        hits.append({
            "project_id": str(item.get("id")),
            "slug": item.get("slug") or str(item.get("id")),
            "title": item.get("name") or "Unknown",
            "description": item.get("summary") or "",
            "author": author_str,
            "downloads": item.get("downloadCount", 0),
            "icon_url": logo_url,
            "categories": [str(c.get("name")) for c in item.get("categories", [])],
            "loaders": sorted(list(loaders)),
            "compatible_neoforge": "neoforge" in loaders,
            "compatible_fabric": "fabric" in loaders,
            "compatible_forge": "forge" in loaders,
            "compatible_quilt": "quilt" in loaders,
            "versions": [],
            "source": source_name,
            "download_url": dl_url,
            "filename": filename
        })
    return hits

def search_bukkitdev(query, limit=15):
    return search_curseforge(query, class_id=5, loader=None, limit=limit, source_name="bukkitdev")

@app.route("/api/plugins/search")
@login_required
def api_plugins_search():
    query = request.args.get("q", "").strip()
    source = request.args.get("source", "modrinth").strip().lower()
    try:
        limit = max(1, min(50, int(request.args.get("limit", 15))))
    except (ValueError, TypeError):
        limit = 15
    if not query:
        return jsonify({"hits": [], "total_hits": 0, "success": True})

    try:
        if source == "spigotmc":
            hits = search_spigot(query, limit=limit)
            return jsonify({"success": True, "hits": hits, "total_hits": len(hits), "source": "spigotmc"})
        elif source == "bukkitdev":
            hits = search_bukkitdev(query, limit=limit)
            return jsonify({"success": True, "hits": hits, "total_hits": len(hits), "source": "bukkitdev"})
        elif source == "curseforge":
            hits = search_curseforge(query, class_id=5, limit=limit, source_name="curseforge")
            return jsonify({"success": True, "hits": hits, "total_hits": len(hits), "source": "curseforge"})
        else:
            params = {
                "query": query,
                "facets": '[["project_type:plugin"]]',
                "limit": limit
            }
            url = "https://api.modrinth.com/v2/search?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": get_panel_user_agent()})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())

            hits = []
            for h in data.get("hits", []):
                hits.append({
                    "project_id": h.get("project_id"),
                    "slug": h.get("slug"),
                    "title": h.get("title"),
                    "description": h.get("description", ""),
                    "author": h.get("author", "Unknown"),
                    "downloads": h.get("downloads", 0),
                    "icon_url": h.get("icon_url") or "",
                    "categories": h.get("categories", []),
                    "source": "modrinth"
                })
            return jsonify({"success": True, "hits": hits, "total_hits": data.get("total_hits", len(hits)), "source": "modrinth"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "hits": []}), 500

@app.route("/api/modrinth/versions")
@app.route("/api/plugins/project-versions")
@app.route("/api/mods/project-versions")
@login_required
def api_modrinth_project_versions():
    project_id = request.args.get("project_id", "").strip()
    if not project_id or not re.match(r'^[a-zA-Z0-9_\-]+$', project_id):
        return jsonify({"success": False, "message": "Invalid project_id", "versions": []}), 400
    source = request.args.get("source", "modrinth").strip().lower()

    if source in ["curseforge", "bukkitdev"]:
        try:
            url = f"https://api.curseforge.com/v1/mods/{project_id}/files?pageSize=25"
            req = urllib.request.Request(url, headers={
                "User-Agent": get_panel_user_agent(),
                "x-api-key": CURSEFORGE_API_KEY,
                "Accept": "application/json"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw_data = json.loads(resp.read().decode())
            raw_files = raw_data.get("data", [])
            versions = []
            for f in raw_files:
                dl_url = get_cf_download_url(f)
                gvs = f.get("gameVersions", [])
                loaders = [l for l in gvs if l.lower() in ["fabric", "forge", "neoforge", "quilt", "paper", "spigot", "purpur"]]
                mc_versions = [v for v in gvs if (v.replace(".", "").isdigit() or any(c.isdigit() for c in v)) and not any(k in v.lower() for k in ["forge", "fabric", "client", "server", "quilt"])]
                rel_type = "release" if f.get("releaseType") == 1 else ("beta" if f.get("releaseType") == 2 else "alpha")
                versions.append({
                    "id": str(f.get("id")),
                    "name": f.get("displayName") or f.get("fileName"),
                    "version_number": f.get("fileName"),
                    "game_versions": mc_versions or gvs,
                    "loaders": loaders,
                    "version_type": rel_type,
                    "date_published": (f.get("fileDate") or "")[:10],
                    "downloads": 0,
                    "files": [{
                        "url": dl_url,
                        "filename": f.get("fileName"),
                        "primary": True,
                        "size": f.get("fileLength", 0),
                        "size_kb": round(f.get("fileLength", 0) / 1024, 1)
                    }]
                })
            return jsonify({"success": True, "versions": versions})
        except Exception as e:
            return jsonify({"success": False, "message": str(e), "versions": []}), 500

    if source == "spigotmc":
        try:
            url = f"https://api.spiget.org/v2/resources/{project_id}/versions?size=25"
            req = urllib.request.Request(url, headers={"User-Agent": get_panel_user_agent()})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw_v = json.loads(resp.read().decode())
            versions = []
            for v in raw_v:
                vid = str(v.get("id"))
                vname = v.get("name") or "v1.0"
                dl_url = f"https://api.spiget.org/v2/resources/{project_id}/download?version={vid}"
                fn = f"spigot-{project_id}-{vname}.jar"
                date_str = ""
                if v.get("releaseDate"):
                    try:
                        date_str = datetime.datetime.fromtimestamp(v.get("releaseDate")).strftime("%Y-%m-%d")
                    except Exception:
                        date_str = "N/A"
                versions.append({
                    "id": vid,
                    "name": vname,
                    "version_number": vname,
                    "game_versions": ["Spigot / Paper"],
                    "loaders": ["Spigot", "Paper"],
                    "version_type": "release",
                    "date_published": date_str,
                    "downloads": v.get("downloads", 0),
                    "files": [{
                        "url": dl_url,
                        "filename": fn,
                        "primary": True,
                        "size": 0,
                        "size_kb": 0
                    }]
                })
            versions.reverse()
            return jsonify({"success": True, "versions": versions})
        except Exception as e:
            return jsonify({"success": False, "message": str(e), "versions": []}), 500

    loaders = request.args.get("loaders", "").strip()
    game_version = request.args.get("game_version", "").strip()

    params = {}
    if loaders and loaders != "all":
        loader_list = [l.strip().lower() for l in loaders.split(",") if l.strip()]
        params["loaders"] = json.dumps(loader_list)
    if game_version and game_version != "all":
        params["game_versions"] = json.dumps([game_version])

    url = f"https://api.modrinth.com/v2/project/{project_id}/version"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers={"User-Agent": get_panel_user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_versions = json.loads(resp.read().decode())
        versions = []
        for v in raw_versions:
            files = []
            for f in v.get("files", []):
                files.append({
                    "url": f.get("url"),
                    "filename": f.get("filename"),
                    "primary": f.get("primary", False),
                    "size": f.get("size", 0),
                    "size_kb": round(f.get("size", 0) / 1024, 1)
                })
            versions.append({
                "id": v.get("id"),
                "name": v.get("name"),
                "version_number": v.get("version_number"),
                "game_versions": v.get("game_versions", []),
                "loaders": v.get("loaders", []),
                "version_type": v.get("version_type", "release"),
                "date_published": v.get("date_published", "")[:10],
                "downloads": v.get("downloads", 0),
                "files": files
            })
        return jsonify({"success": True, "versions": versions})
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "versions": []}), 500

@app.route("/api/plugin/install_online", methods=["POST"])
@login_required
def api_plugin_install_online():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "plugins" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Plugin management required."}), 403

    data = request.get_json() or {}
    project_id = str(data.get("project_id", "")).strip()
    project_name = str(data.get("name") or data.get("title") or "").strip()
    direct_url = data.get("download_url", "").strip()
    direct_name = data.get("filename", "").strip()
    version_id = str(data.get("version_id", "")).strip()
    source = (data.get("source") or request.args.get("source") or "modrinth").strip().lower()
    overwrite = bool(data.get("overwrite") or data.get("force"))

    if not project_id and not direct_url:
        return jsonify({"success": False, "message": "Project ID or download URL required"}), 400

    plugins_dir = os.path.join(SERVER_DIR, "plugins")
    os.makedirs(plugins_dir, exist_ok=True)

    # Pre-check if addon already exists before making remote download requests
    if not overwrite:
        pre_existing = check_addon_exists(plugins_dir, project_name=project_name, candidate_filename=direct_name, project_id=project_id)
        if pre_existing:
            return jsonify({
                "success": False,
                "already_exists": True,
                "existing_file": pre_existing["filename"],
                "existing_status": pre_existing["status"],
                "candidate_file": direct_name or project_name or "plugin.jar",
                "message": f"Plugin '{pre_existing['filename']}' ({pre_existing['status']}) already exists on the server. Do you want to update/replace it?"
            }), 409

    try:
        download_url = direct_url
        filename = direct_name

        if not download_url:
            if source == "spigotmc":
                if version_id:
                    download_url = f"https://api.spiget.org/v2/resources/{project_id}/download?version={version_id}"
                else:
                    download_url = f"https://api.spiget.org/v2/resources/{project_id}/download"
                if not filename:
                    filename = f"spigot-{project_id}.jar"
            elif source in ["curseforge", "bukkitdev"]:
                if version_id:
                    url_f = f"https://api.curseforge.com/v1/mods/{project_id}/files/{version_id}"
                    req_f = urllib.request.Request(url_f, headers={
                        "User-Agent": get_panel_user_agent(),
                        "x-api-key": CURSEFORGE_API_KEY,
                        "Accept": "application/json"
                    })
                    with urllib.request.urlopen(req_f, timeout=8) as resp:
                        f_info = json.loads(resp.read().decode()).get("data", {})
                    download_url = get_cf_download_url(f_info)
                    filename = f_info.get("fileName")
                if not download_url:
                    url_files = f"https://api.curseforge.com/v1/mods/{project_id}/files?pageSize=10"
                    req_files = urllib.request.Request(url_files, headers={
                        "User-Agent": get_panel_user_agent(),
                        "x-api-key": CURSEFORGE_API_KEY,
                        "Accept": "application/json"
                    })
                    with urllib.request.urlopen(req_files, timeout=8) as resp:
                        files_list = json.loads(resp.read().decode()).get("data", [])
                    target_f = next((f for f in files_list if f.get("fileName", "").endswith(".jar")), None)
                    if target_f:
                        download_url = get_cf_download_url(target_f)
                        filename = target_f.get("fileName")
            elif source == "modrinth":
                if version_id:
                    url_v = f"https://api.modrinth.com/v2/version/{version_id}"
                    req_v = urllib.request.Request(url_v, headers={"User-Agent": get_panel_user_agent()})
                    with urllib.request.urlopen(req_v, timeout=8) as resp:
                        v_info = json.loads(resp.read().decode())
                    files = v_info.get("files", [])
                    target_f = next((f for f in files if f.get("primary") and f.get("filename", "").endswith(".jar")), None)
                    if not target_f:
                        target_f = next((f for f in files if f.get("filename", "").endswith(".jar")), None)
                    if target_f:
                        download_url = target_f.get("url")
                        filename = target_f.get("filename")

                if not download_url:
                    loaders = '["paper","spigot","purpur","bukkit"]'
                    url = f"https://api.modrinth.com/v2/project/{project_id}/version?" + urllib.parse.urlencode({"loaders": loaders})
                    req = urllib.request.Request(url, headers={"User-Agent": get_panel_user_agent()})

                    versions = []
                    try:
                        with urllib.request.urlopen(req, timeout=8) as resp:
                            versions = json.loads(resp.read().decode())
                    except Exception:
                        pass

                    if not versions:
                        url_all = f"https://api.modrinth.com/v2/project/{project_id}/version"
                        req_all = urllib.request.Request(url_all, headers={"User-Agent": get_panel_user_agent()})
                        with urllib.request.urlopen(req_all, timeout=8) as resp:
                            versions = json.loads(resp.read().decode())

                    if not versions:
                        return jsonify({"success": False, "message": "No release versions found for this plugin."}), 404

                    target_file = None
                    for v in versions:
                        files = v.get("files", [])
                        for f in files:
                            if f.get("primary", False) and f.get("filename", "").endswith(".jar"):
                                target_file = f
                                break
                        if target_file:
                            break
                        for f in files:
                            if f.get("filename", "").endswith(".jar"):
                                target_file = f
                                break
                        if target_file:
                            break

                    if not target_file:
                        return jsonify({"success": False, "message": "No valid .jar file found in project releases."}), 404

                    download_url = target_file.get("url")
                    filename = target_file.get("filename")

        if not download_url:
            return jsonify({"success": False, "message": "Failed to determine plugin download URL."}), 404

        if not overwrite and filename:
            mid_existing = check_addon_exists(plugins_dir, project_name=project_name, candidate_filename=filename, project_id=project_id)
            if mid_existing:
                return jsonify({
                    "success": False,
                    "already_exists": True,
                    "existing_file": mid_existing["filename"],
                    "existing_status": mid_existing["status"],
                    "candidate_file": filename,
                    "message": f"Plugin '{mid_existing['filename']}' ({mid_existing['status']}) already exists on the server. Do you want to update/replace it?"
                }), 409

        dl_req = urllib.request.Request(download_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 FiestaPanel/1.0"
        })
        with urllib.request.urlopen(dl_req, timeout=30) as resp:
            final_url = resp.geturl()
            safe_name = resolve_clean_jar_name(filename, final_url, resp.headers)
            dest_path = os.path.join(plugins_dir, safe_name)

            existing_to_replace = check_addon_exists(plugins_dir, project_name=project_name, candidate_filename=safe_name, project_id=project_id)
            if existing_to_replace and not overwrite:
                return jsonify({
                    "success": False,
                    "already_exists": True,
                    "existing_file": existing_to_replace["filename"],
                    "existing_status": existing_to_replace["status"],
                    "candidate_file": safe_name,
                    "message": f"Plugin '{existing_to_replace['filename']}' ({existing_to_replace['status']}) already exists on the server. Do you want to update/replace it?"
                }), 409

            # Clean up old existing jar file if filenames differ and overwrite is confirmed
            if overwrite and existing_to_replace:
                old_p = existing_to_replace.get("path")
                if old_p and os.path.isfile(old_p) and os.path.abspath(old_p) != os.path.abspath(dest_path):
                    try:
                        os.remove(old_p)
                    except Exception:
                        pass

            with open(dest_path, "wb") as out_f:
                shutil.copyfileobj(resp, out_f)

        file_size_kb = round(os.path.getsize(dest_path) / 1024, 1)

        msg = f"Successfully updated and replaced {existing_to_replace['filename']} with {safe_name} ({file_size_kb} KB)! Restart server to load." if (existing_to_replace and overwrite) else f"Successfully downloaded {safe_name} ({file_size_kb} KB)! Restart server to load."

        return jsonify({
            "success": True,
            "filename": safe_name,
            "size_kb": file_size_kb,
            "message": msg
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Download failed: {str(e)}"}), 500

# ── Addons & Mods Management (Cross-Platform Fabric / Forge / Bukkit Support) ──
def detect_addon_context():
    meta_path = os.path.join(SERVER_DIR, "installed_software.json")
    software = "Unknown"
    if os.path.isfile(meta_path):
        try:
            with open(meta_path) as f:
                software = json.load(f).get("software", "Unknown")
        except Exception:
            pass

    plugins_dir = os.path.join(SERVER_DIR, "plugins")
    mods_dir = os.path.join(SERVER_DIR, "mods")

    has_plugins_dir = os.path.isdir(plugins_dir)
    has_mods_dir = os.path.isdir(mods_dir)

    plugins_count = 0
    if has_plugins_dir:
        try:
            plugins_count = len([f for f in os.listdir(plugins_dir) if f.endswith(".jar") and os.path.isfile(os.path.join(plugins_dir, f))])
        except Exception:
            pass

    mods_count = 0
    if has_mods_dir:
        try:
            mods_count = len([f for f in os.listdir(mods_dir) if f.endswith(".jar") and os.path.isfile(os.path.join(mods_dir, f))])
        except Exception:
            pass

    sw_lower = software.lower()
    if any(m in sw_lower for m in ["fabric", "forge", "neoforge", "quilt"]):
        primary = "mods"
    elif any(p in sw_lower for p in ["purpur", "paper", "spigot", "bukkit", "folia"]):
        primary = "plugins"
    elif mods_count > 0 and plugins_count == 0:
        primary = "mods"
    else:
        primary = "plugins"

    return {
        "primary_addon_type": primary,
        "software": software,
        "plugins_count": plugins_count,
        "mods_count": mods_count,
        "has_plugins_dir": has_plugins_dir,
        "has_mods_dir": has_mods_dir
    }

@app.route("/api/addons/context")
@login_required
def api_addons_context():
    return jsonify(detect_addon_context())

@app.route("/api/mods")
@login_required
def api_mods():
    mods_dir = os.path.join(SERVER_DIR, "mods")
    mods_list = []
    if os.path.exists(mods_dir):
        for f in sorted(os.listdir(mods_dir)):
            if f.endswith(".jar"):
                p = os.path.join(mods_dir, f)
                if os.path.isfile(p):
                    size_kb = round(os.path.getsize(p) / 1024, 1)
                    mods_list.append({
                        "name": f.replace(".jar", ""),
                        "filename": f,
                        "size_kb": size_kb
                    })
    return jsonify({"mods": mods_list, "total": len(mods_list)})

@app.route("/api/mods/disabled")
@login_required
def api_mods_disabled():
    disabled_dir = os.path.join(SERVER_DIR, "mods", "disabled")
    disabled_list = []
    if os.path.exists(disabled_dir):
        for f in sorted(os.listdir(disabled_dir)):
            p = os.path.join(disabled_dir, f)
            if os.path.isfile(p) and (f.endswith(".jar") or f.endswith(".bak")):
                size_kb = round(os.path.getsize(p) / 1024, 1)
                disabled_list.append({
                    "name": f.replace(".jar", "").replace(".bak", ""),
                    "filename": f,
                    "size_kb": size_kb
                })
    return jsonify({"disabled": disabled_list, "total": len(disabled_list)})

@app.route("/api/mod/delete", methods=["POST"])
@login_required
def api_mod_delete():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "mods" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Mod management required."}), 403

    data = request.get_json() or {}
    raw_name = (data.get("name") or "").strip()
    name = os.path.basename(raw_name)
    if name.lower().endswith(".jar"):
        name = name[:-4]
    if not name or name in (".", "..") or "/" in raw_name or "\\" in raw_name:
        return jsonify({"success": False, "message": "Mod name required"}), 400

    mods_dir = os.path.join(SERVER_DIR, "mods")
    disabled_dir = os.path.join(mods_dir, "disabled")
    src = os.path.join(mods_dir, f"{name}.jar")

    if not os.path.isfile(src):
        return jsonify({"success": False, "message": f"Mod file not found: {name}.jar"}), 404

    os.makedirs(disabled_dir, exist_ok=True)
    dst = os.path.join(disabled_dir, f"{name}.jar")
    try:
        shutil.move(src, dst)
        return jsonify({"success": True, "message": f"{name} moved to mods/disabled/ — restart server to unload."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/mod/enable", methods=["POST"])
@login_required
def api_mod_enable():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "mods" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Mod management required."}), 403

    data = request.get_json() or {}
    raw_filename = (data.get("filename") or "").strip()
    filename = os.path.basename(raw_filename)
    if not filename or filename in (".", "..") or "/" in raw_filename or "\\" in raw_filename:
        return jsonify({"success": False, "message": "Filename required"}), 400

    mods_dir = os.path.join(SERVER_DIR, "mods")
    disabled_dir = os.path.join(mods_dir, "disabled")
    src = os.path.join(disabled_dir, filename)

    if not os.path.isfile(src):
        return jsonify({"success": False, "message": f"Disabled mod not found: {filename}"}), 404

    target_name = filename[:-4] if filename.endswith(".bak") and filename.endswith(".jar.bak") else filename
    if not target_name.endswith(".jar"):
        target_name += ".jar"
    dst = os.path.join(mods_dir, target_name)

    try:
        shutil.move(src, dst)
        return jsonify({"success": True, "message": f"{target_name} restored to active mods/ — restart server to load."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/mod/perm_delete", methods=["POST"])
@login_required
def api_mod_perm_delete():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "mods" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Mod management required."}), 403

    data = request.get_json() or {}
    raw_filename = (data.get("filename") or "").strip()
    filename = os.path.basename(raw_filename)
    if not filename or filename in (".", "..") or "/" in raw_filename or "\\" in raw_filename:
        return jsonify({"success": False, "message": "Filename required"}), 400

    disabled_dir = os.path.join(SERVER_DIR, "mods", "disabled")
    target = os.path.join(disabled_dir, filename)

    if not os.path.isfile(target):
        return jsonify({"success": False, "message": f"File not found in disabled/: {filename}"}), 404

    try:
        os.remove(target)
        return jsonify({"success": True, "message": f"Permanently deleted {filename}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/mod/upload", methods=["POST"])
@login_required
def api_mod_upload():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "mods" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Mod management required."}), 403

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file part in request"}), 400
    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"success": False, "message": "No file selected"}), 400
    if not file.filename.lower().endswith(".jar"):
        return jsonify({"success": False, "message": "Only .jar files are allowed"}), 400

    safe_name = os.path.basename(file.filename)
    if not safe_name or safe_name in (".", ".."):
        return jsonify({"success": False, "message": "Invalid filename"}), 400

    mods_dir = os.path.join(SERVER_DIR, "mods")
    os.makedirs(mods_dir, exist_ok=True)
    dst = os.path.join(mods_dir, safe_name)
    try:
        file.save(dst)
        return jsonify({"success": True, "message": f"{safe_name} uploaded successfully! Restart server to load."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/mods/search")
@login_required
def api_mods_search():
    query = request.args.get("q", "").strip()
    source = request.args.get("source", "modrinth").strip().lower()
    loader = request.args.get("loader", "").strip().lower()
    try:
        limit = max(1, min(50, int(request.args.get("limit", 15))))
    except (ValueError, TypeError):
        limit = 15
    if not query:
        return jsonify({"hits": [], "total_hits": 0, "success": True})

    try:
        if source == "spigotmc":
            hits = search_spigot(query, limit=limit)
            return jsonify({"success": True, "hits": hits, "total_hits": len(hits), "loader": loader, "source": "spigotmc"})
        elif source == "bukkitdev":
            hits = search_bukkitdev(query, limit=limit)
            return jsonify({"success": True, "hits": hits, "total_hits": len(hits), "loader": loader, "source": "bukkitdev"})
        elif source == "curseforge":
            hits = search_curseforge(query, class_id=6, loader=loader if loader not in ["all", "any"] else None, limit=limit, source_name="curseforge")
            return jsonify({"success": True, "hits": hits, "total_hits": len(hits), "loader": loader, "source": "curseforge"})
        else:
            facets = [["project_type:mod"]]
            if loader and loader not in ["all", "any"]:
                facets.append([f"categories:{loader}"])

            params = {
                "query": query,
                "facets": json.dumps(facets),
                "limit": limit
            }
            url = "https://api.modrinth.com/v2/search?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": get_panel_user_agent()})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())

            hits = []
            for h in data.get("hits", []):
                cats = h.get("categories", [])
                mod_loaders = [c for c in cats if c in ["neoforge", "fabric", "forge", "quilt"]]
                hits.append({
                    "project_id": h.get("project_id"),
                    "slug": h.get("slug"),
                    "title": h.get("title"),
                    "description": h.get("description", ""),
                    "author": h.get("author", "Unknown"),
                    "downloads": h.get("downloads", 0),
                    "icon_url": h.get("icon_url") or "",
                    "categories": cats,
                    "loaders": mod_loaders,
                    "compatible_neoforge": "neoforge" in cats,
                    "compatible_fabric": "fabric" in cats,
                    "compatible_forge": "forge" in cats,
                    "compatible_quilt": "quilt" in cats,
                    "versions": h.get("versions", [])[:6],
                    "server_side": h.get("server_side", ""),
                    "source": "modrinth"
                })
            return jsonify({"success": True, "hits": hits, "total_hits": data.get("total_hits", len(hits)), "loader": loader, "source": "modrinth"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "hits": []}), 500

@app.route("/api/mod/install_online", methods=["POST"])
@login_required
def api_mod_install_online():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "mods" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Mod management required."}), 403

    data = request.get_json() or {}
    project_id = str(data.get("project_id", "")).strip()
    project_name = str(data.get("name") or data.get("title") or "").strip()
    direct_url = data.get("download_url", "").strip()
    direct_name = data.get("filename", "").strip()
    version_id = str(data.get("version_id", "")).strip()
    target_loader = data.get("loader", "").strip().lower()
    source = (data.get("source") or request.args.get("source") or "modrinth").strip().lower()
    overwrite = bool(data.get("overwrite") or data.get("force"))

    if not project_id and not direct_url:
        return jsonify({"success": False, "message": "Project ID or download URL required"}), 400

    mods_dir = os.path.join(SERVER_DIR, "mods")
    os.makedirs(mods_dir, exist_ok=True)

    # Pre-check if mod already exists before making remote download requests
    if not overwrite:
        pre_existing = check_addon_exists(mods_dir, project_name=project_name, candidate_filename=direct_name, project_id=project_id)
        if pre_existing:
            return jsonify({
                "success": False,
                "already_exists": True,
                "existing_file": pre_existing["filename"],
                "existing_status": pre_existing["status"],
                "candidate_file": direct_name or project_name or "mod.jar",
                "message": f"Mod '{pre_existing['filename']}' ({pre_existing['status']}) already exists on the server. Do you want to update/replace it?"
            }), 409

    try:
        download_url = direct_url
        filename = direct_name

        if not download_url:
            if source == "spigotmc":
                if version_id:
                    download_url = f"https://api.spiget.org/v2/resources/{project_id}/download?version={version_id}"
                else:
                    download_url = f"https://api.spiget.org/v2/resources/{project_id}/download"
                if not filename:
                    filename = f"spigot-{project_id}.jar"
            elif source in ["curseforge", "bukkitdev"]:
                if version_id:
                    url_f = f"https://api.curseforge.com/v1/mods/{project_id}/files/{version_id}"
                    req_f = urllib.request.Request(url_f, headers={
                        "User-Agent": get_panel_user_agent(),
                        "x-api-key": CURSEFORGE_API_KEY,
                        "Accept": "application/json"
                    })
                    with urllib.request.urlopen(req_f, timeout=8) as resp:
                        f_info = json.loads(resp.read().decode()).get("data", {})
                    download_url = get_cf_download_url(f_info)
                    filename = f_info.get("fileName")
                if not download_url:
                    url_files = f"https://api.curseforge.com/v1/mods/{project_id}/files?pageSize=20"
                    req_files = urllib.request.Request(url_files, headers={
                        "User-Agent": get_panel_user_agent(),
                        "x-api-key": CURSEFORGE_API_KEY,
                        "Accept": "application/json"
                    })
                    with urllib.request.urlopen(req_files, timeout=8) as resp:
                        files_list = json.loads(resp.read().decode()).get("data", [])
                    matched = None
                    if target_loader and target_loader not in ["all", "any"]:
                        for f in files_list:
                            gvs = [str(g).lower() for g in f.get("gameVersions", [])]
                            if target_loader in gvs and f.get("fileName", "").endswith(".jar"):
                                matched = f
                                break
                    if not matched:
                        matched = next((f for f in files_list if f.get("fileName", "").endswith(".jar")), None)
                    if matched:
                        download_url = get_cf_download_url(matched)
                        filename = matched.get("fileName")
            elif source == "modrinth":
                if version_id:
                    url_v = f"https://api.modrinth.com/v2/version/{version_id}"
                    req_v = urllib.request.Request(url_v, headers={"User-Agent": get_panel_user_agent()})
                    with urllib.request.urlopen(req_v, timeout=8) as resp:
                        v_info = json.loads(resp.read().decode())
                    files = v_info.get("files", [])
                    target_f = next((f for f in files if f.get("primary") and f.get("filename", "").endswith(".jar")), None)
                    if not target_f:
                        target_f = next((f for f in files if f.get("filename", "").endswith(".jar")), None)
                    if target_f:
                        download_url = target_f.get("url")
                        filename = target_f.get("filename")

                if not download_url:
                    if not target_loader or target_loader in ["all", "any"]:
                        ctx = detect_addon_context()
                        sw_name = ctx.get("software", "").lower()
                        if "neoforge" in sw_name:
                            target_loader = "neoforge"
                        elif "fabric" in sw_name:
                            target_loader = "fabric"
                        elif "forge" in sw_name:
                            target_loader = "forge"

                    versions = []
                    if target_loader and target_loader not in ["all", "any"]:
                        loaders_param = json.dumps([target_loader])
                        url = f"https://api.modrinth.com/v2/project/{project_id}/version?" + urllib.parse.urlencode({"loaders": loaders_param})
                        req = urllib.request.Request(url, headers={"User-Agent": get_panel_user_agent()})
                        try:
                            with urllib.request.urlopen(req, timeout=8) as resp:
                                versions = json.loads(resp.read().decode())
                        except Exception:
                            pass

                    if not versions:
                        loaders = '["neoforge","fabric","quilt","forge"]'
                        url = f"https://api.modrinth.com/v2/project/{project_id}/version?" + urllib.parse.urlencode({"loaders": loaders})
                        req = urllib.request.Request(url, headers={"User-Agent": get_panel_user_agent()})
                        try:
                            with urllib.request.urlopen(req, timeout=8) as resp:
                                versions = json.loads(resp.read().decode())
                        except Exception:
                            pass

                    if not versions:
                        url_all = f"https://api.modrinth.com/v2/project/{project_id}/version"
                        req_all = urllib.request.Request(url_all, headers={"User-Agent": get_panel_user_agent()})
                        with urllib.request.urlopen(req_all, timeout=8) as resp:
                            versions = json.loads(resp.read().decode())

                    if not versions:
                        return jsonify({"success": False, "message": "No release versions found for this mod."}), 404

                    target_file = None
                    for v in versions:
                        files = v.get("files", [])
                        for f in files:
                            if f.get("primary", False) and f.get("filename", "").endswith(".jar"):
                                target_file = f
                                break
                        if target_file:
                            break
                        for f in files:
                            if f.get("filename", "").endswith(".jar"):
                                target_file = f
                                break
                        if target_file:
                            break

                    if not target_file:
                        return jsonify({"success": False, "message": "No valid .jar file found in project releases."}), 404

                    download_url = target_file.get("url")
                    filename = target_file.get("filename")

        if not download_url:
            return jsonify({"success": False, "message": "Failed to determine mod download URL."}), 404

        if not overwrite and filename:
            mid_existing = check_addon_exists(mods_dir, project_name=project_name, candidate_filename=filename, project_id=project_id)
            if mid_existing:
                return jsonify({
                    "success": False,
                    "already_exists": True,
                    "existing_file": mid_existing["filename"],
                    "existing_status": mid_existing["status"],
                    "candidate_file": filename,
                    "message": f"Mod '{mid_existing['filename']}' ({mid_existing['status']}) already exists on the server. Do you want to update/replace it?"
                }), 409

        dl_req = urllib.request.Request(download_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 FiestaPanel/1.0"
        })
        with urllib.request.urlopen(dl_req, timeout=30) as resp:
            final_url = resp.geturl()
            safe_name = resolve_clean_jar_name(filename, final_url, resp.headers)
            dest_path = os.path.join(mods_dir, safe_name)

            existing_to_replace = check_addon_exists(mods_dir, project_name=project_name, candidate_filename=safe_name, project_id=project_id)
            if existing_to_replace and not overwrite:
                return jsonify({
                    "success": False,
                    "already_exists": True,
                    "existing_file": existing_to_replace["filename"],
                    "existing_status": existing_to_replace["status"],
                    "candidate_file": safe_name,
                    "message": f"Mod '{existing_to_replace['filename']}' ({existing_to_replace['status']}) already exists on the server. Do you want to update/replace it?"
                }), 409

            # Clean up old existing jar file if filenames differ and overwrite is confirmed
            if overwrite and existing_to_replace:
                old_p = existing_to_replace.get("path")
                if old_p and os.path.isfile(old_p) and os.path.abspath(old_p) != os.path.abspath(dest_path):
                    try:
                        os.remove(old_p)
                    except Exception:
                        pass

            with open(dest_path, "wb") as out_f:
                shutil.copyfileobj(resp, out_f)

        file_size_kb = round(os.path.getsize(dest_path) / 1024, 1)
        loader_note = f" (for {target_loader})" if target_loader and target_loader not in ["all", "any"] else ""
        msg = f"Successfully updated and replaced {existing_to_replace['filename']} with {safe_name}{loader_note} ({file_size_kb} KB)! Restart server to load." if (existing_to_replace and overwrite) else f"Successfully downloaded mod {safe_name}{loader_note} ({file_size_kb} KB)! Restart server to load."

        return jsonify({
            "success": True,
            "filename": safe_name,
            "size_kb": file_size_kb,
            "message": msg
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Download failed: {str(e)}"}), 500

# ── Modpack Install & Management Systems (Aternos Style) ────────────
@app.route("/api/modpacks/search")
@login_required
def api_modpacks_search():
    query = request.args.get("q", "").strip()
    source = request.args.get("source", "modrinth").strip().lower()
    try:
        limit = max(1, min(50, int(request.args.get("limit", 15))))
    except (ValueError, TypeError):
        limit = 15
    loader = request.args.get("loader", "").strip().lower()

    try:
        if source == "curseforge":
            hits = search_curseforge(query, class_id=4471, loader=loader if loader not in ["all", "any"] else None, limit=limit, source_name="curseforge")
            return jsonify({"success": True, "hits": hits, "total_hits": len(hits), "source": "curseforge"})
        else:
            facets = [["project_type:modpack"]]
            if loader and loader not in ["all", "any"]:
                facets.append([f"categories:{loader}"])

            params = {
                "facets": json.dumps(facets),
                "limit": limit
            }
            if query:
                params["query"] = query

            url = "https://api.modrinth.com/v2/search?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": get_panel_user_agent()})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            hits = []
            for h in data.get("hits", []):
                hits.append({
                    "project_id": h.get("project_id"),
                    "slug": h.get("slug"),
                    "title": h.get("title"),
                    "description": h.get("description", ""),
                    "author": h.get("author", "Unknown"),
                    "downloads": h.get("downloads", 0),
                    "icon_url": h.get("icon_url") or "",
                    "categories": h.get("categories", []),
                    "versions": h.get("versions", [])[:6],
                    "source": "modrinth"
                })
            return jsonify({"success": True, "hits": hits, "total_hits": data.get("total_hits", len(hits)), "source": "modrinth"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "hits": []}), 500

@app.route("/api/modpacks/versions")
@login_required
def api_modpacks_versions():
    project_id = request.args.get("project_id", "").strip()
    if not project_id or not re.match(r'^[a-zA-Z0-9_\-]+$', project_id):
        return jsonify({"success": False, "message": "Invalid project_id", "versions": []}), 400
    source = request.args.get("source", "modrinth").strip().lower()

    if source in ["curseforge"]:
        try:
            url = f"https://api.curseforge.com/v1/mods/{project_id}/files?pageSize=20"
            req = urllib.request.Request(url, headers={
                "User-Agent": get_panel_user_agent(),
                "x-api-key": CURSEFORGE_API_KEY,
                "Accept": "application/json"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw_data = json.loads(resp.read().decode())
            raw_files = raw_data.get("data", [])
            versions = []
            for f in raw_files:
                dl_url = get_cf_download_url(f)
                versions.append({
                    "id": str(f.get("id")),
                    "name": f.get("displayName") or f.get("fileName"),
                    "version_number": f.get("fileName"),
                    "game_versions": f.get("gameVersions", []),
                    "loaders": [l for l in f.get("gameVersions", []) if l.lower() in ["fabric", "forge", "neoforge", "quilt"]],
                    "date_published": (f.get("fileDate") or "")[:10],
                    "files": [{
                        "url": dl_url,
                        "filename": f.get("fileName"),
                        "primary": True,
                        "size_mb": round(f.get("fileLength", 0) / (1024*1024), 2)
                    }]
                })
            return jsonify({"success": True, "versions": versions})
        except Exception as e:
            return jsonify({"success": False, "message": str(e), "versions": []}), 500

    url = f"https://api.modrinth.com/v2/project/{project_id}/version"
    req = urllib.request.Request(url, headers={"User-Agent": get_panel_user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw_v = json.loads(resp.read().decode())
        versions = []
        for v in raw_v:
            files = []
            for f in v.get("files", []):
                files.append({
                    "url": f.get("url"),
                    "filename": f.get("filename"),
                    "primary": f.get("primary", False),
                    "size_mb": round(f.get("size", 0) / (1024*1024), 2)
                })
            versions.append({
                "id": v.get("id"),
                "name": v.get("name"),
                "version_number": v.get("version_number"),
                "game_versions": v.get("game_versions", []),
                "loaders": v.get("loaders", []),
                "date_published": v.get("date_published", "")[:10],
                "files": files
            })
        return jsonify({"success": True, "versions": versions})
    except urllib.error.HTTPError as e:
        status_code = 404 if e.code == 404 else 502
        return jsonify({"success": False, "error": f"Modpack not found: {e.code}", "versions": []}), status_code
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "versions": []}), 500

@app.route("/api/modpacks/install", methods=["POST"])
@login_required
def api_modpacks_install():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "software" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Software or File manager permission required."}), 403

    file_obj = request.files.get("file")
    data = (request.form.to_dict() if request.form else None) or request.get_json(silent=True) or {}

    project_id = data.get("project_id", "").strip()
    version_id = data.get("version_id", "").strip()
    download_url = data.get("download_url", "").strip()
    filename = data.get("filename", "").strip()
    create_backup = str(data.get("create_backup", "true")).lower() in ("true", "1")

    # Stop server if running
    was_running = server_runner.is_running()
    if was_running:
        send_mc_command("save-all")
        send_mc_command("say [Server] Installing modpack, stopping server...")
        time.sleep(1)
        server_runner.stop()
        time.sleep(1)

    # Optional backup
    if create_backup:
        try:
            b_dir = get_backup_dir()
            b_name = f"pre_modpack_backup_{int(time.time())}.tar.gz"
            b_path = os.path.join(b_dir, b_name)
            with tarfile.open(b_path, "w:gz") as tar:
                for item in os.listdir(SERVER_DIR):
                    if item.endswith((".tar.gz", ".zip")) or item == "backups":
                        continue
                    full_p = os.path.join(SERVER_DIR, item)
                    tar.add(full_p, arcname=item)
        except Exception:
            pass

    temp_mrpack = os.path.join(SERVER_DIR, f"modpack_{int(time.time())}.mrpack")
    try:
        if file_obj and file_obj.filename:
            file_obj.save(temp_mrpack)
        elif download_url:
            req = urllib.request.Request(download_url, headers={"User-Agent": get_panel_user_agent()})
            with urllib.request.urlopen(req, timeout=60) as resp, open(temp_mrpack, "wb") as f_out:
                shutil.copyfileobj(resp, f_out)
        elif project_id:
            v_url = f"https://api.modrinth.com/v2/project/{project_id}/version"
            req = urllib.request.Request(v_url, headers={"User-Agent": get_panel_user_agent()})
            with urllib.request.urlopen(req, timeout=8) as resp:
                v_list = json.loads(resp.read().decode())
            if not v_list:
                return jsonify({"success": False, "message": "No versions found for modpack."}), 404
            target_v = v_list[0]
            if version_id:
                for v in v_list:
                    if v.get("id") == version_id:
                        target_v = v
                        break
            files = target_v.get("files", [])
            mrpack_f = next((f for f in files if f.get("filename", "").endswith(".mrpack")), files[0] if files else None)
            if not mrpack_f or not mrpack_f.get("url"):
                return jsonify({"success": False, "message": "No .mrpack download file found."}), 404

            dl_req = urllib.request.Request(mrpack_f["url"], headers={"User-Agent": get_panel_user_agent()})
            with urllib.request.urlopen(dl_req, timeout=60) as resp, open(temp_mrpack, "wb") as f_out:
                shutil.copyfileobj(resp, f_out)
        else:
            return jsonify({"success": False, "message": "No modpack or file specified."}), 400

        installed_mods = 0
        extracted_configs = 0
        manifest = {}

        with zipfile.ZipFile(temp_mrpack, "r") as zf:
            if "modrinth.index.json" in zf.namelist():
                manifest = json.loads(zf.read("modrinth.index.json").decode("utf-8"))

            for member in zf.namelist():
                if member.startswith("overrides/") and not member.endswith("/"):
                    rel_dest = member[len("overrides/"):]
                    abs_dest = os.path.join(SERVER_DIR, rel_dest)
                    if safe_fiesta_path(rel_dest):
                        os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
                        with zf.open(member) as src, open(abs_dest, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        extracted_configs += 1

        files = manifest.get("files", [])
        mods_dir = os.path.join(SERVER_DIR, "mods")
        os.makedirs(mods_dir, exist_ok=True)

        for item in files:
            p = item.get("path", "")
            dls = item.get("downloads", [])
            if p and dls and p.startswith("mods/"):
                mod_filename = os.path.basename(p)
                mod_dest = os.path.join(mods_dir, mod_filename)
                mod_url = dls[0]
                try:
                    m_req = urllib.request.Request(mod_url, headers={"User-Agent": get_panel_user_agent()})
                    with urllib.request.urlopen(m_req, timeout=20) as resp, open(mod_dest, "wb") as mf:
                        shutil.copyfileobj(resp, mf)
                    installed_mods += 1
                except Exception:
                    pass

        meta_info = {
            "name": manifest.get("name", filename or "Custom Modpack"),
            "versionId": manifest.get("versionId", version_id),
            "summary": manifest.get("summary", ""),
            "dependencies": manifest.get("dependencies", {}),
            "installed_mods": installed_mods,
            "installed_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(os.path.join(SERVER_DIR, "installed_modpack.json"), "w", encoding="utf-8") as f:
            json.dump(meta_info, f, indent=2)

        if os.path.exists(temp_mrpack):
            try: os.remove(temp_mrpack)
            except Exception: pass

        if str(data.get("autostart", was_running)).lower() in ("true", "1", "yes", True):
            server_runner.start()

        return jsonify({
            "success": True,
            "message": f"Modpack '{meta_info['name']}' installed successfully! Installed {installed_mods} mods and {extracted_configs} configuration files.",
            "meta": meta_info
        })
    except Exception as e:
        if os.path.exists(temp_mrpack):
            try: os.remove(temp_mrpack)
            except Exception: pass

        if str(data.get("autostart", was_running)).lower() in ("true", "1", "yes", True):
            server_runner.start()
        return jsonify({"success": False, "message": f"Modpack installation failed: {str(e)}"}), 500

# ── Web Panel Addons & Extensions System ─────────────────────────────
@app.route("/api/panel-addons", methods=["GET"])
@login_required
def api_panel_addons_list():
    return jsonify({
        "success": True,
        "addons": addon_manager.get_installed_list(),
        "total": len(addon_manager.installed_addons)
    })

@app.route("/api/panel-addons/<addon_id>/toggle", methods=["POST"])
@login_required
def api_panel_addons_toggle(addon_id):
    data = request.get_json() or {}
    enabled = bool(data.get("enabled", True))
    try:
        addon_manager.toggle_addon(addon_id, enabled)
        return jsonify({"success": True, "addon_id": addon_id, "enabled": enabled})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/panel-addons/<addon_id>/settings", methods=["GET", "POST"])
@login_required
def api_panel_addons_settings(addon_id):
    if request.method == "GET":
        settings = addon_manager.get_addon_settings(addon_id)
        manifest = addon_manager.installed_addons.get(addon_id, {})
        return jsonify({
            "success": True,
            "addon_id": addon_id,
            "settings": settings,
            "settings_schema": manifest.get("settings_schema", [])
        })
    data = request.get_json() or {}
    settings = data.get("settings", {})
    try:
        addon_manager.update_addon_settings(addon_id, settings)
        return jsonify({
            "success": True,
            "message": "Settings updated successfully.",
            "settings": addon_manager.get_addon_settings(addon_id)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/panel-addons/<addon_id>/uninstall", methods=["POST"])
@login_required
def api_panel_addons_uninstall(addon_id):
    try:
        addon_manager.uninstall_addon(addon_id)
        return jsonify({"success": True, "message": f"Addon '{addon_id}' uninstalled successfully."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/panel-addons/<addon_id>/view", methods=["GET"])
@login_required
def api_panel_addons_view(addon_id):
    html = addon_manager.render_addon_tab(addon_id)
    return html

@app.route("/api/panel-addons/<addon_id>/action", methods=["POST"])
@login_required
def api_panel_addons_action(addon_id):
    data = request.get_json() or {}
    action_name = data.get("action", "")
    payload = data.get("payload", {})
    res = addon_manager.execute_addon_action(addon_id, action_name, payload)
    return jsonify(res)

@app.route("/api/panel-addons/scheduled_world_archiver/download/<path:filename>", methods=["GET"])
@app.route("/api/panel-addons/episode_world_archiver/download/<path:filename>", methods=["GET"])
@login_required
def api_panel_addons_download_archive(filename):
    safe_fn = os.path.basename(filename)
    ep_dir = "/home/ubuntu/backups/episodes"
    file_path = os.path.join(ep_dir, safe_fn)
    if not os.path.isfile(file_path):
        return jsonify({"success": False, "error": "Archive file not found."}), 404
    return send_file(file_path, as_attachment=True, download_name=safe_fn)

@app.route("/api/panel-addons/store", methods=["GET"])
@login_required
def api_panel_addons_store():
    return jsonify({
        "success": True,
        "catalog": addon_manager.get_store_catalog()
    })

@app.route("/api/panel-addons/repository", methods=["GET"])
@login_required
def api_panel_addons_repository():
    repo = addon_manager.load_repository()
    return jsonify({
        "success": True,
        "repository_info": {
            "name": repo.get("repository_name", "FiestaPanel Official Addon Repository"),
            "version": repo.get("repository_version", "1.0.0"),
            "updated_at": repo.get("updated_at", ""),
            "homepage": repo.get("homepage", "")
        },
        "catalog": addon_manager.get_store_catalog()
    })

@app.route("/api/panel-addons/info/<addon_id>", methods=["GET"])
@login_required
def api_panel_addons_info(addon_id):
    info = addon_manager.get_addon_info(addon_id)
    if not info:
        return jsonify({"success": False, "error": f"Addon '{addon_id}' not found in repository or installed list."}), 404
    return jsonify({
        "success": True,
        "addon": info
    })

@app.route("/api/panel-addons/<addon_id>/review", methods=["POST"])
@login_required
def api_panel_addons_submit_review(addon_id):
    data = request.get_json() or {}
    rating = data.get("rating")
    comment = (data.get("comment") or "").strip()
    reviewer = session.get("user") or session.get("username") or "admin"

    try:
        rating_int = int(rating)
        if rating_int < 1 or rating_int > 5:
            return jsonify({"success": False, "error": "Rating must be an integer between 1 and 5 stars."}), 400
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid rating format."}), 400

    if not comment:
        return jsonify({"success": False, "error": "Review comment cannot be empty."}), 400

    res = addon_manager.add_addon_review(addon_id, reviewer, rating_int, comment)
    return jsonify(res)

@app.route("/api/panel-addons/<addon_id>/review/<review_id>", methods=["DELETE", "POST"])
@login_required
def api_panel_addons_delete_review(addon_id, review_id):
    current_user = session.get("user") or session.get("username") or "admin"
    is_admin = session.get("role") == "owner" or "all" in session.get("permissions", [])
    res = addon_manager.delete_addon_review(addon_id, review_id, current_user, is_admin=is_admin)
    status_code = 200 if res.get("success") else (403 if "Permission" in res.get("error", "") else 404)
    return jsonify(res), status_code

@app.route("/api/panel-addons/repository/refresh", methods=["POST"])
@login_required
def api_panel_addons_repository_refresh():
    repo = addon_manager.load_repository()
    addon_manager.scan_addons()
    return jsonify({
        "success": True,
        "message": "Addon repository synchronized successfully.",
        "total": len(repo.get("addons", []))
    })

@app.route("/api/panel-addons/store/install", methods=["POST"])
@login_required
def api_panel_addons_store_install():
    data = request.get_json() or {}
    addon_id = data.get("addon_id", "").strip()
    if not addon_id:
        return jsonify({"success": False, "error": "addon_id is required."}), 400
    try:
        manifest = addon_manager.install_from_catalog(addon_id)
        return jsonify({"success": True, "message": f"Addon '{manifest.get('name', addon_id)}' installed successfully!", "manifest": manifest})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/panel-addons/upload", methods=["POST"])
@login_required
def api_panel_addons_upload():
    if "addon_zip" not in request.files:
        return jsonify({"success": False, "error": "No addon_zip file provided in form-data."}), 400
    file = request.files["addon_zip"]
    if not file.filename.endswith(".zip"):
        return jsonify({"success": False, "error": "Only .zip archive packages are supported."}), 400
    try:
        zip_bytes = file.read()
        manifest = addon_manager.install_from_zip(zip_bytes)
        return jsonify({"success": True, "message": f"Addon '{manifest.get('name', manifest.get('id'))}' uploaded and installed!", "manifest": manifest})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/panel-addons/install-url", methods=["POST"])
@login_required
def api_panel_addons_install_url():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url or not url.startswith("http"):
        return jsonify({"success": False, "error": "Valid HTTP(S) URL is required."}), 400
    try:
        manifest = addon_manager.install_from_url(url)
        return jsonify({"success": True, "message": f"Addon '{manifest.get('name', manifest.get('id'))}' installed from URL!", "manifest": manifest})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/panel-addons/template/download", methods=["GET"])
@login_required
def api_panel_addons_template_download():
    zip_bytes = addon_manager.generate_boilerplate_zip()
    return send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name="fiestapanel-addon-starter.zip"
    )

# ── Fiesta Team Official Addon Publisher Studio & Repository API ────────────

def get_fiesta_dev_key():
    env_key = os.environ.get("FIESTA_DEV_KEY", "").strip()
    if env_key:
        return env_key
    cfg_key = str(SERVER_CONFIG.get("fiesta_dev_key", "")).strip()
    if cfg_key:
        return cfg_key
    try:
        target = CONFIG_FILE
        if not os.path.exists(target) and DATA_DIR != BASE_DIR and os.path.exists(os.path.join(BASE_DIR, "config.json")):
            target = os.path.join(BASE_DIR, "config.json")
        if os.path.exists(target):
            with open(target, "r", encoding="utf-8") as f:
                c = json.load(f)
                val = str(c.get("fiesta_dev_key", "")).strip()
                if val:
                    SERVER_CONFIG["fiesta_dev_key"] = val
                    return val
    except Exception:
        pass
    return ""

def check_fiesta_dev_auth():
    """Verify owner role and Fiesta developer key authentication."""
    if session.get("role") != "owner" and "all" not in session.get("permissions", []):
        return False
    dev_key = get_fiesta_dev_key()
    if not dev_key:
        return False
    if session.get("fiesta_dev_authenticated") is True:
        return True
    header_key = request.headers.get("X-Fiesta-Dev-Key")
    if header_key and hmac.compare_digest(header_key.strip(), dev_key):
        return True
    return False

@app.route("/admin/addons-publisher", methods=["GET"])
@app.route("/fiesta-admin/addons", methods=["GET"])
@login_required
def admin_addons_publisher_page():
    if session.get("role") != "owner" and "all" not in session.get("permissions", []):
        return render_template("login.html", error="Access Denied: Owner privileges required for Developer Studio.", branding=load_branding()), 403

    branding = load_branding()
    dev_key = get_fiesta_dev_key()
    dev_key_configured = bool(dev_key)
    is_unlocked = bool(session.get("fiesta_dev_authenticated")) and dev_key_configured
    return render_template(
        "admin_addon_publisher.html",
        branding=branding,
        is_unlocked=is_unlocked,
        dev_key_configured=dev_key_configured,
        csrf_token=session.get("csrf_token", "")
    )

@app.route("/api/admin/addons-publisher/unlock", methods=["POST"])
@login_required
def api_admin_addons_unlock():
    if session.get("role") != "owner" and "all" not in session.get("permissions", []):
        return jsonify({"success": False, "error": "Owner privileges required."}), 403

    data = request.get_json(silent=True) or {}
    key = str(data.get("dev_key") or data.get("key") or "").strip()

    expected = get_fiesta_dev_key()
    if not expected:
        return jsonify({
            "success": False,
            "error": "Developer Publishing is disabled on this server instance. The Fiesta Developer Key must be configured via environment variable FIESTA_DEV_KEY or config.json."
        }), 403

    if not key or not hmac.compare_digest(key, expected):
        return jsonify({
            "success": False,
            "error": "Invalid Developer Key. Direct store publishing is restricted to the FiestaSMP core team. External server admins must submit community addons via GitHub Pull Request."
        }), 401

    session["fiesta_dev_authenticated"] = True
    return jsonify({"success": True, "message": "Fiesta Developer Publisher unlocked successfully!"})

@app.route("/api/admin/addons-publisher/lock", methods=["POST"])
@login_required
def api_admin_addons_lock():
    session["fiesta_dev_authenticated"] = False
    return jsonify({"success": True, "message": "Fiesta Developer Publisher session locked."})

@app.route("/api/admin/addons-publisher/key", methods=["POST"])
@login_required
def api_admin_addons_update_key():
    if not check_fiesta_dev_auth():
        return jsonify({"success": False, "error": "Unauthorized: Fiesta Developer Publisher authentication required."}), 403

    data = request.get_json(silent=True) or {}
    new_key = str(data.get("new_key", "")).strip()
    if len(new_key) < 8:
        return jsonify({"success": False, "error": "New Developer Key must be at least 8 characters long."}), 400

    SERVER_CONFIG["fiesta_dev_key"] = new_key
    target_cfg = CONFIG_FILE
    try:
        cfg = {}
        if os.path.exists(target_cfg):
            with open(target_cfg, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        cfg["fiesta_dev_key"] = new_key
        with open(target_cfg, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to persist new developer key: {e}"}), 500

    return jsonify({"success": True, "message": "Fiesta Developer Key rotated and updated in config.json!"})

@app.route("/api/admin/addons-publisher/repo", methods=["GET"])
@login_required
def api_admin_addons_get_repo():
    if not check_fiesta_dev_auth():
        return jsonify({"success": False, "error": "Unauthorized: Fiesta Developer Publisher authentication required."}), 403

    repo = addon_manager.load_repository()
    return jsonify({"success": True, "repository": repo})

@app.route("/api/admin/addons-publisher/discord-link", methods=["POST"])
@login_required
def api_admin_addons_discord_link():
    if not check_fiesta_dev_auth():
        return jsonify({"success": False, "error": "Unauthorized: Fiesta Developer Publisher authentication required."}), 403

    data = request.get_json(silent=True) or {}
    discord_url = str(data.get("discord_url") or data.get("url") or "").strip()
    if not discord_url or not (discord_url.startswith("http://") or discord_url.startswith("https://")):
        return jsonify({"success": False, "error": "Please enter a valid Discord invite URL (e.g. https://discord.gg/yourserver)."}), 400

    repo = addon_manager.load_repository()
    repo["community_discord"] = discord_url
    repo["official_discord"] = discord_url

    # Propagate into server_scheduler addon if present
    for a in repo.get("addons", []):
        if a.get("id") == "server_scheduler":
            tasks = a.get("default_settings", {}).get("tasks", [])
            for t in tasks:
                if t.get("id") == "t2" or "discord" in t.get("name", "").lower():
                    t["cmd"] = f"say ✦ Join our community Discord at {discord_url} ! ✦"

    repo_file = OFFICIAL_REPO_FILE
    try:
        os.makedirs(os.path.dirname(repo_file), exist_ok=True)
        repo["updated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tmp = f"{repo_file}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(repo, f, indent=2)
        os.replace(tmp, repo_file)
        addon_manager.load_repository()
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to save repository: {e}"}), 500

    # Also update branding
    try:
        b = load_branding()
        b["community_discord"] = discord_url
        target_b = BRANDING_FILE
        with open(target_b, "w", encoding="utf-8") as f:
            json.dump(b, f, indent=2)
    except Exception:
        pass

    return jsonify({
        "success": True,
        "message": f"FiestaSMP community Discord link updated to '{discord_url}' and synchronized into official addon broadcasts!",
        "community_discord": discord_url
    })

@app.route("/api/admin/addons-publisher/save-addon", methods=["POST"])
@login_required
def api_admin_addons_save_addon():
    if not check_fiesta_dev_auth():
        return jsonify({"success": False, "error": "Unauthorized: Fiesta Developer Publisher authentication required."}), 403

    data = request.get_json(silent=True) or {}
    addon_id = re.sub(r'[^a-z0-9_-]', '', str(data.get("id", "")).lower().strip())
    name = str(data.get("name", "")).strip()

    if not addon_id or len(addon_id) < 2:
        return jsonify({"success": False, "error": "Valid alphanumeric Addon ID (slug) is required."}), 400
    if not name:
        return jsonify({"success": False, "error": "Addon Display Name is required."}), 400

    repo_file = OFFICIAL_REPO_FILE
    repo = addon_manager.load_repository()
    addons = repo.get("addons", [])

    existing_idx = None
    for idx, a in enumerate(addons):
        if a.get("id") == addon_id:
            existing_idx = idx
            break

    target_addon = addons[existing_idx] if existing_idx is not None else {}
    target_addon["id"] = addon_id
    target_addon["name"] = name
    target_addon["version"] = str(data.get("version", target_addon.get("version", "1.0.0"))).strip()
    target_addon["author"] = str(data.get("author", target_addon.get("author", "FiestaSMP Team"))).strip()
    target_addon["category"] = str(data.get("category", target_addon.get("category", "Tools"))).strip()
    target_addon["featured"] = bool(data.get("featured", target_addon.get("featured", False)))
    target_addon["icon"] = str(data.get("icon", target_addon.get("icon", "fas fa-cube"))).strip()
    target_addon["summary"] = str(data.get("summary", target_addon.get("summary", ""))).strip()
    target_addon["description"] = str(data.get("description", target_addon.get("description", target_addon["summary"]))).strip()
    target_addon["readme_markdown"] = str(data.get("readme_markdown", target_addon.get("readme_markdown", f"# {name}\n\n{target_addon['description']}"))).strip()

    if "dependencies" in data and isinstance(data["dependencies"], dict):
        target_addon["dependencies"] = data["dependencies"]
    elif "dependencies" not in target_addon:
        target_addon["dependencies"] = {"engine": ["Any"], "required_plugins": []}

    if "default_settings" in data and isinstance(data["default_settings"], dict):
        target_addon["default_settings"] = data["default_settings"]
    elif "default_settings" not in target_addon:
        target_addon["default_settings"] = {}

    if "settings_schema" in data and isinstance(data["settings_schema"], list):
        target_addon["settings_schema"] = data["settings_schema"]
    elif "settings_schema" not in target_addon:
        target_addon["settings_schema"] = []

    if "ui_slots" in data and isinstance(data["ui_slots"], dict):
        target_addon["ui_slots"] = data["ui_slots"]
    elif "ui_slots" not in target_addon:
        target_addon["ui_slots"] = {"tab": {"title": name, "icon": target_addon["icon"], "description": target_addon["summary"]}}

    files = target_addon.get("files", {})
    if "files" in data and isinstance(data["files"], dict):
        files.update(data["files"])
    if "main_py" in data and str(data["main_py"]).strip():
        files["main.py"] = str(data["main_py"])
    if "tab_html" in data and str(data["tab_html"]).strip():
        files["tab.html"] = str(data["tab_html"])
    target_addon["files"] = files

    if not files.get("main.py"):
        files["main.py"] = f'# {name} Official Extension for FiestaPanel\ndef on_event(ctx, event_type, details=None):\n    pass\n'
    if not files.get("tab.html"):
        files["tab.html"] = f'<div class="panel-box">\n  <div class="section-title-bar">{name}</div>\n  <p>Official {name} interface.</p>\n</div>\n'

    if existing_idx is not None:
        addons[existing_idx] = target_addon
    else:
        addons.append(target_addon)

    repo["addons"] = addons
    repo["updated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        tmp = f"{repo_file}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(repo, f, indent=2)
        os.replace(tmp, repo_file)

        addon_dir = os.path.join(BASE_DIR, "addons", addon_id)
        if os.path.isdir(addon_dir):
            for fname, fcontent in files.items():
                fpath = os.path.join(addon_dir, fname)
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(fcontent)
                except Exception:
                    pass

        addon_manager.load_repository()
        addon_manager.scan_addons()
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to persist addon to repository: {e}"}), 500

    return jsonify({
        "success": True,
        "message": f"Official addon '{name}' ({addon_id}) saved and published to repository!",
        "addon": target_addon
    })

@app.route("/api/admin/addons-publisher/delete-addon/<addon_id>", methods=["DELETE", "POST"])
@login_required
def api_admin_addons_delete_addon(addon_id):
    if not check_fiesta_dev_auth():
        return jsonify({"success": False, "error": "Unauthorized: Fiesta Developer Publisher authentication required."}), 403

    repo_file = OFFICIAL_REPO_FILE
    repo = addon_manager.load_repository()
    addons = repo.get("addons", [])

    new_addons = [a for a in addons if a.get("id") != addon_id]
    if len(new_addons) == len(addons):
        return jsonify({"success": False, "error": f"Addon '{addon_id}' not found in official repository."}), 404

    repo["addons"] = new_addons
    repo["updated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        tmp = f"{repo_file}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(repo, f, indent=2)
        os.replace(tmp, repo_file)
        addon_manager.load_repository()
        addon_manager.scan_addons()
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to delete addon: {e}"}), 500

    return jsonify({"success": True, "message": f"Addon '{addon_id}' removed from official store repository."})

@app.route("/api/admin/addons-publisher/upload-bundle", methods=["POST"])
@login_required
def api_admin_addons_upload_bundle():
    if not check_fiesta_dev_auth():
        return jsonify({"success": False, "error": "Unauthorized: Fiesta Developer Publisher authentication required."}), 403

    if "bundle_file" not in request.files:
        return jsonify({"success": False, "error": "No bundle_file provided."}), 400

    file = request.files["bundle_file"]
    if not file.filename.endswith(".zip"):
        return jsonify({"success": False, "error": "Only .zip bundles are accepted."}), 400

    try:
        zip_bytes = file.read()
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        manifest_raw = None
        main_py = None
        tab_html = None
        readme_md = None

        for n in zf.namelist():
            lower = n.lower()
            if lower.endswith("addon.json"):
                manifest_raw = zf.read(n).decode("utf-8", errors="replace")
            elif lower.endswith("main.py"):
                main_py = zf.read(n).decode("utf-8", errors="replace")
            elif lower.endswith("tab.html"):
                tab_html = zf.read(n).decode("utf-8", errors="replace")
            elif lower.endswith("readme.md"):
                readme_md = zf.read(n).decode("utf-8", errors="replace")

        if not manifest_raw:
            return jsonify({"success": False, "error": "Bundle must contain an addon.json manifest."}), 400

        manifest = json.loads(manifest_raw)
        aid = re.sub(r'[^a-z0-9_-]', '', str(manifest.get("id", "")).lower().strip())
        if not aid:
            return jsonify({"success": False, "error": "Manifest missing valid 'id' field."}), 400

        files_dict = manifest.get("files", {})
        if main_py: files_dict["main.py"] = main_py
        if tab_html: files_dict["tab.html"] = tab_html
        manifest["files"] = files_dict
        if readme_md and not manifest.get("readme_markdown"):
            manifest["readme_markdown"] = readme_md

        repo_file = OFFICIAL_REPO_FILE
        repo = addon_manager.load_repository()
        addons = repo.get("addons", [])
        existing_idx = None
        for idx, a in enumerate(addons):
            if a.get("id") == aid:
                existing_idx = idx
                break

        if existing_idx is not None:
            addons[existing_idx] = manifest
        else:
            addons.append(manifest)

        repo["addons"] = addons
        repo["updated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        tmp = f"{repo_file}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(repo, f, indent=2)
        os.replace(tmp, repo_file)

        target_dir = os.path.join(BASE_DIR, "addons", aid)
        os.makedirs(target_dir, exist_ok=True)
        for fname, fcontent in files_dict.items():
            with open(os.path.join(target_dir, fname), "w", encoding="utf-8") as out:
                out.write(fcontent)
        with open(os.path.join(target_dir, "addon.json"), "w", encoding="utf-8") as out:
            json.dump({k: v for k, v in manifest.items() if k != "files"}, out, indent=2)

        addon_manager.load_repository()
        addon_manager.scan_addons()
        return jsonify({"success": True, "message": f"Addon '{manifest.get('name', aid)}' uploaded and published to store repository!", "addon": manifest})
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to unpack and publish bundle: {e}"}), 400

@app.route("/api/admin/addons-publisher/export/<addon_id>", methods=["GET"])
@login_required
def api_admin_addons_export_bundle(addon_id):
    if not check_fiesta_dev_auth():
        return jsonify({"success": False, "error": "Unauthorized: Fiesta Developer Publisher authentication required."}), 403

    repo = addon_manager.load_repository()
    found = None
    for a in repo.get("addons", []):
        if a.get("id") == addon_id:
            found = a
            break

    if not found:
        return jsonify({"success": False, "error": "Addon not found in store repository."}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest_clean = {k: v for k, v in found.items() if k != "files"}
        zf.writestr("addon.json", json.dumps(manifest_clean, indent=2))
        files = found.get("files", {})
        for fname, fcontent in files.items():
            zf.writestr(fname, fcontent)
        if found.get("readme_markdown"):
            zf.writestr("README.md", found["readme_markdown"])

    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{addon_id}-v{found.get('version', '1.0.0')}.zip"
    )

# ── Backups ──────────────────────────────────────────────────────────────────
def get_backup_search_dirs():
    user_home = os.path.expanduser('~')
    candidates = [
        get_backup_dir(),
        os.path.join(SERVER_DIR, "backups"),
        SERVER_DIR,
        user_home,
        os.path.join(user_home, 'backups'),
        os.path.dirname(SERVER_DIR),
        os.path.join(os.path.dirname(SERVER_DIR), 'backups')
    ]
    if os.path.isdir('/home/ubuntu'):
        candidates.extend(['/home/ubuntu', '/home/ubuntu/backups'])
    return [d for d in dict.fromkeys(candidates) if os.path.isdir(d)]

@app.route("/api/backups")
@login_required
def api_get_backups():
    search_dirs = get_backup_search_dirs()
    files = []
    seen_paths = set()
    patterns = ['*.tar.gz', '*.tar.zst', '*.tar.*', '*.zip']
    for d in search_dirs:
        for pat in patterns:
            for f in glob.glob(os.path.join(d, pat)):
                if os.path.isfile(f) and f not in seen_paths:
                    bname = os.path.basename(f)
                    if bname.startswith(("stream_dir_", "temp_", "fiesta_dl_", "tmp_")):
                        continue
                    seen_paths.add(f)
                    try:
                        stat = os.stat(f)
                        files.append({
                            'filename': os.path.basename(f),
                            'path': f,
                            'size_mb': round(stat.st_size / (1024 * 1024), 1),
                            'size_gb': round(stat.st_size / (1024 * 1024 * 1024), 2),
                            'mtime': datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                        })
                    except Exception:
                        pass
    files.sort(key=lambda x: x['mtime'], reverse=True)
    return jsonify({"backups": files, "total": len(files)})

@app.route("/api/backup/create", methods=["POST"])
@login_required
def api_create_backup():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "backups" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Backups permission required."}), 403

    def backup_task():
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = get_backup_dir()
        os.makedirs(backup_dir, exist_ok=True)
        svc_prefix = (SERVICE_NAME or "server").lower().replace(" ", "-")
        target_tar = os.path.join(backup_dir, f"{svc_prefix}_backup_{ts}.tar.gz")
        send_mc_command("save-all")
        time.sleep(2)
        try:
            def exclude_backups(tarinfo):
                if tarinfo.name.endswith((".tar.gz", ".tar.zst", ".zip", ".tar.bz2", ".tar.xz")):
                    return None
                parts = tarinfo.name.split("/")
                if "backups" in parts or ".fiesta_server.pid" in parts:
                    return None
                return tarinfo

            with tarfile.open(target_tar, "w:gz") as tar:
                tar.add(SERVER_DIR, arcname=os.path.basename(SERVER_DIR), filter=exclude_backups)
            # Trigger cloud sync if enabled
            c_cfg = cloud_backup_mgr.load_config()
            if c_cfg.get("enabled") and c_cfg.get("auto_sync", True):
                cloud_backup_mgr.start_async_upload(target_tar)
        except Exception as err:
            print("Backup creation error:", err)

    threading.Thread(target=backup_task, daemon=True).start()
    return jsonify({"success": True, "message": "Background backup archive initiated! Check Backups list shortly."})

@app.route("/api/backup/download")
@login_required
def api_download_backup():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "backups" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"error": "Permission denied: Backups permission required."}), 403

    filename = request.args.get("filename", "").strip()
    safe_name = os.path.basename(filename)
    if not safe_name or safe_name in (".", ".."):
        return jsonify({"error": "Invalid backup filename."}), 400

    search_dirs = get_backup_search_dirs()
    for folder in search_dirs:
        p = os.path.join(folder, safe_name)
        if os.path.isfile(p):
            return send_file(p, as_attachment=True, download_name=safe_name)
    return jsonify({"error": "Backup file not found"}), 404

@app.route("/api/backup/delete", methods=["POST"])
@login_required
def api_delete_backup():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "backups" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Backups permission required."}), 403

    data = request.get_json() or {}
    filename = os.path.basename(data.get("filename", "").strip())
    if not filename or not (filename.endswith(".tar.gz") or filename.endswith(".zip") or filename.endswith(".tar.zst") or ".tar." in filename):
        return jsonify({"success": False, "message": "Invalid backup archive filename."}), 400

    search_dirs = get_backup_search_dirs()
    deleted = False
    for folder in search_dirs:
        p = os.path.join(folder, filename)
        if os.path.isfile(p):
            try:
                os.remove(p)
                deleted = True
                break
            except Exception as e:
                return jsonify({"success": False, "message": f"Failed to delete backup file: {e}"}), 500

    if deleted:
        return jsonify({"success": True, "message": f"Backup archive '{filename}' deleted from local storage."})
    return jsonify({"success": False, "message": "Backup file not found."}), 404

# ── Cloud Offsite Backups Endpoints ──────────────────────────────────────────
@app.route("/api/backup/cloud/config", methods=["GET", "POST"])
@login_required
def api_backup_cloud_config():
    if request.method == "GET":
        cfg = cloud_backup_mgr.get_masked_config()
        return jsonify({"success": True, "config": cfg})

    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "backups" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Backups permission required."}), 403

    data = request.get_json() or {}
    ok, msg = cloud_backup_mgr.save_config(data)
    if ok:
        return jsonify({"success": True, "message": msg, "config": cloud_backup_mgr.get_masked_config()})
    return jsonify({"success": False, "message": msg}), 400

@app.route("/api/backup/cloud/test", methods=["POST"])
@login_required
def api_backup_cloud_test():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "backups" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Backups permission required."}), 403

    data = request.get_json()
    ok, msg = cloud_backup_mgr.test_connection(data if isinstance(data, dict) and data else None)
    return jsonify({"success": ok, "message": msg})

@app.route("/api/backup/cloud/status", methods=["GET"])
@login_required
def api_backup_cloud_status():
    return jsonify(cloud_backup_mgr.get_status())

@app.route("/api/backup/cloud/list", methods=["GET"])
@login_required
def api_backup_cloud_list():
    try:
        remotes = cloud_backup_mgr.list_remote_backups()
        return jsonify({"success": True, "backups": remotes, "total": len(remotes)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "backups": [], "total": 0}), 500

@app.route("/api/backup/cloud/upload", methods=["POST"])
@login_required
def api_backup_cloud_upload():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "backups" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Backups permission required."}), 403

    data = request.get_json() or {}
    filename = os.path.basename(data.get("filename", "").strip())
    if not filename:
        return jsonify({"success": False, "message": "Filename required."}), 400

    search_dirs = get_backup_search_dirs()
    target_path = None
    for folder in search_dirs:
        p = os.path.join(folder, filename)
        if os.path.isfile(p):
            target_path = p
            break

    if not target_path:
        return jsonify({"success": False, "message": f"Local backup archive '{filename}' not found."}), 404

    cloud_backup_mgr.start_async_upload(target_path)
    return jsonify({"success": True, "message": f"Initiated background cloud upload for '{filename}'. Check status monitor."})

@app.route("/api/backup/cloud/download", methods=["POST"])
@login_required
def api_backup_cloud_download():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "backups" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Backups permission required."}), 403

    data = request.get_json() or {}
    filename = os.path.basename(data.get("filename", "").strip())
    if not filename:
        return jsonify({"success": False, "message": "Filename required."}), 400

    bdir = get_backup_dir()
    def download_task():
        cloud_backup_mgr.download_remote_backup(filename, bdir)

    threading.Thread(target=download_task, daemon=True).start()
    return jsonify({"success": True, "message": f"Downloading '{filename}' from cloud storage into local backups directory in background..."})

@app.route("/api/backup/cloud/delete", methods=["POST"])
@login_required
def api_backup_cloud_delete():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "backups" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Backups permission required."}), 403

    data = request.get_json() or {}
    filename = os.path.basename(data.get("filename", "").strip())
    if not filename:
        return jsonify({"success": False, "message": "Filename required."}), 400

    ok, msg = cloud_backup_mgr.delete_remote_backup(filename)
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400

# ── File Manager ─────────────────────────────────────────────────────────────
def safe_fiesta_path(rel_path):
    rel_path = (rel_path or "").strip().lstrip("/")
    base_dir = os.path.realpath(SERVER_DIR)
    abs_path = os.path.realpath(os.path.join(base_dir, rel_path))
    if abs_path == base_dir or abs_path.startswith(base_dir + os.sep):
        return abs_path
    return None

def format_file_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{round(size_bytes / 1024, 1)} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{round(size_bytes / (1024 * 1024), 1)} MB"
    else:
        return f"{round(size_bytes / (1024 * 1024 * 1024), 2)} GB"

@app.route("/api/files/list")
@login_required
def api_files_list():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    rel_path = request.args.get("path", "").strip().lstrip("/")
    abs_path = safe_fiesta_path(rel_path)
    if not abs_path or not os.path.isdir(abs_path):
        return jsonify({"success": False, "message": "Invalid directory path"}), 404

    base_dir = os.path.realpath(SERVER_DIR)
    current_rel = os.path.relpath(abs_path, base_dir)
    if current_rel == ".":
        current_rel = ""

    parent_rel = ""
    if current_rel:
        parent_rel = os.path.dirname(current_rel)
        if parent_rel == ".":
            parent_rel = ""

    root_name = os.path.basename(os.path.realpath(SERVER_DIR)) or "server"
    crumbs = [{"name": root_name, "path": ""}]
    if current_rel:
        parts = current_rel.split(os.sep)
        curr_p = ""
        for part in parts:
            curr_p = os.path.join(curr_p, part) if curr_p else part
            crumbs.append({"name": part, "path": curr_p})

    items = []
    editable_exts = {'txt', 'yml', 'yaml', 'json', 'properties', 'toml', 'cfg', 'log', 'sk', 'md', 'sh', 'env', 'conf', 'csv'}
    try:
        with os.scandir(abs_path) as entries:
            for entry in entries:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    st = entry.stat()
                    name = entry.name
                    item_rel = os.path.join(current_rel, name) if current_rel else name
                    ext = name.split(".")[-1].lower() if "." in name and not is_dir else ""
                    size_b = st.st_size if not is_dir else 0

                    items.append({
                        "name": name,
                        "path": item_rel,
                        "is_dir": is_dir,
                        "size_bytes": size_b,
                        "size_formatted": "-" if is_dir else format_file_size(size_b),
                        "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                        "extension": ext,
                        "is_editable": (ext in editable_exts or size_b < 500 * 1024) and not is_dir
                    })
                except Exception:
                    pass
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return jsonify({
            "success": True,
            "current_path": current_rel,
            "parent_path": parent_rel,
            "breadcrumbs": crumbs,
            "items": items,
            "total_items": len(items)
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/files/read")
@login_required
def api_files_read():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    rel_path = request.args.get("path", "").strip()
    abs_path = safe_fiesta_path(rel_path)
    if not abs_path or not os.path.isfile(abs_path):
        return jsonify({"success": False, "message": "File not found"}), 404

    try:
        st = os.stat(abs_path)
        if st.st_size > 3 * 1024 * 1024:
            return jsonify({"success": False, "message": "File exceeds 3MB limit for web editor."}), 400

        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        return jsonify({
            "success": True,
            "path": rel_path,
            "filename": os.path.basename(abs_path),
            "size": st.st_size,
            "content": content
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/files/save", methods=["POST"])
@login_required
def api_files_save():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    data = request.get_json() or {}
    rel_path = data.get("path", "").strip()
    content = data.get("content", "")

    abs_path = safe_fiesta_path(rel_path)
    if not abs_path:
        return jsonify({"success": False, "message": "Access denied"}), 403

    if os.path.isdir(abs_path):
        return jsonify({"success": False, "message": "Cannot save content to a directory."}), 400

    try:
        if os.path.exists(abs_path):
            shutil.copy2(abs_path, abs_path + ".bak")

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)

        return jsonify({"success": True, "message": f"Saved {os.path.basename(abs_path)} successfully (backup created)!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/files/delete", methods=["POST"])
@login_required
def api_files_delete():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    data = request.get_json() or {}
    rel_path = data.get("path", "").strip()
    abs_path = safe_fiesta_path(rel_path)

    base_dir = os.path.realpath(SERVER_DIR)
    if not abs_path or abs_path == base_dir:
        return jsonify({"success": False, "message": "Cannot delete server root"}), 403

    try:
        if os.path.isdir(abs_path):
            shutil.rmtree(abs_path)
        elif os.path.isfile(abs_path):
            os.remove(abs_path)
        return jsonify({"success": True, "message": f"Deleted {os.path.basename(abs_path)}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/files/upload", methods=["POST"])
@login_required
def api_files_upload():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file attached"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"success": False, "message": "No file selected"}), 400

    target_dir_rel = request.form.get("path", "").strip()
    abs_dir = safe_fiesta_path(target_dir_rel)
    if not abs_dir or not os.path.isdir(abs_dir):
        return jsonify({"success": False, "message": "Invalid target directory"}), 400

    safe_name = os.path.basename(file.filename)
    if not safe_name or safe_name in (".", ".."):
        return jsonify({"success": False, "message": "Invalid filename"}), 400
    dst = os.path.join(abs_dir, safe_name)
    try:
        file.save(dst)
        return jsonify({"success": True, "message": f"Uploaded {safe_name} successfully."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/files/mkdir", methods=["POST"])
@login_required
def api_files_mkdir():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    data = request.get_json() or {}
    parent_rel = data.get("path", "").strip()
    folder_name = data.get("name", "").strip()

    if not folder_name or "/" in folder_name or "\\" in folder_name or folder_name in (".", ".."):
        return jsonify({"success": False, "message": "Invalid folder name"}), 400

    abs_dir = safe_fiesta_path(os.path.join(parent_rel, folder_name))
    if not abs_dir:
        return jsonify({"success": False, "message": "Invalid path"}), 400

    try:
        os.makedirs(abs_dir, exist_ok=True)
        return jsonify({"success": True, "message": f"Folder '{folder_name}' created."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/files/new_file", methods=["POST"])
@login_required
def api_files_new_file():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    data = request.get_json() or {}
    parent_rel = data.get("path", "").strip()
    filename = data.get("name", "").strip()

    if not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
        return jsonify({"success": False, "message": "Invalid file name"}), 400

    abs_file = safe_fiesta_path(os.path.join(parent_rel, filename))
    if not abs_file:
        return jsonify({"success": False, "message": "Invalid path"}), 400

    if os.path.exists(abs_file):
        return jsonify({"success": False, "message": "File already exists"}), 400

    try:
        with open(abs_file, "w", encoding="utf-8") as f:
            f.write("")
        return jsonify({"success": True, "message": f"File '{filename}' created."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/files/download")
@login_required
def api_files_download():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    rel_path = request.args.get("path", "").strip()
    abs_path = safe_fiesta_path(rel_path)
    if not abs_path or not os.path.isfile(abs_path):
        return jsonify({"success": False, "message": "File not found"}), 404

    return send_file(abs_path, as_attachment=True, download_name=os.path.basename(abs_path))

@app.route("/api/files/rename", methods=["POST"])
@login_required
def api_files_rename():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    data = request.get_json() or {}
    old_rel = data.get("old_path", "").strip()
    new_name = data.get("new_name", "").strip()

    if not old_rel or not new_name:
        return jsonify({"success": False, "message": "Original path and new name are required."}), 400
    if "/" in new_name or "\\" in new_name or new_name in (".", ".."):
        return jsonify({"success": False, "message": "Invalid new filename."}), 400

    abs_old = safe_fiesta_path(old_rel)
    base_dir = os.path.realpath(SERVER_DIR)
    if not abs_old or abs_old == base_dir:
        return jsonify({"success": False, "message": "Cannot rename server root."}), 403

    parent_dir = os.path.dirname(abs_old)
    abs_new = os.path.join(parent_dir, new_name)
    if os.path.exists(abs_new):
        return jsonify({"success": False, "message": f"An item named '{new_name}' already exists."}), 400

    try:
        shutil.move(abs_old, abs_new)
        return jsonify({"success": True, "message": f"Renamed to '{new_name}' successfully."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/files/archive", methods=["POST"])
@login_required
def api_files_archive():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    data = request.get_json() or {}
    paths = data.get("paths", [])
    archive_name = (data.get("name") or data.get("archive_name") or "").strip()
    dest_dir_rel = (data.get("dest_dir") or data.get("destination_dir") or "").strip()

    if not paths:
        return jsonify({"success": False, "message": "No files selected to archive."}), 400
    if not archive_name:
        archive_name = f"archive_{int(time.time())}.zip"
    if not archive_name.lower().endswith(".zip"):
        archive_name += ".zip"
    if "/" in archive_name or "\\" in archive_name:
        return jsonify({"success": False, "message": "Invalid archive filename."}), 400

    abs_dest_dir = safe_fiesta_path(dest_dir_rel)
    if not abs_dest_dir or not os.path.isdir(abs_dest_dir):
        abs_dest_dir = os.path.realpath(SERVER_DIR)

    target_zip = os.path.join(abs_dest_dir, archive_name)
    try:
        with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in paths:
                abs_p = safe_fiesta_path(p)
                if not abs_p or not os.path.exists(abs_p):
                    continue
                if os.path.isdir(abs_p):
                    for root, dirs, files in os.walk(abs_p):
                        for f in files:
                            full_f = os.path.join(root, f)
                            rel_f = os.path.relpath(full_f, SERVER_DIR)
                            zf.write(full_f, rel_f)
                else:
                    rel_p = os.path.relpath(abs_p, SERVER_DIR)
                    zf.write(abs_p, rel_p)
        return jsonify({"success": True, "message": f"Created archive '{archive_name}' with {len(paths)} item(s)."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/files/extract", methods=["POST"])
@login_required
def api_files_extract():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    data = request.get_json() or {}
    archive_rel = (data.get("path") or data.get("archive_path") or "").strip()
    dest_dir_rel = (data.get("dest_dir") or data.get("destination_dir") or "").strip()

    abs_archive = safe_fiesta_path(archive_rel)
    if not abs_archive or not os.path.isfile(abs_archive):
        return jsonify({"success": False, "message": "Archive file not found."}), 404

    target_dest = safe_fiesta_path(dest_dir_rel)
    if target_dest and not os.path.exists(target_dest):
        os.makedirs(target_dest, exist_ok=True)
    if not target_dest or not os.path.isdir(target_dest):
        target_dest = os.path.dirname(abs_archive)

    try:
        lower_name = abs_archive.lower()
        if lower_name.endswith((".zip", ".mrpack")):
            with zipfile.ZipFile(abs_archive, "r") as zf:
                for member in zf.namelist():
                    abs_target = os.path.realpath(os.path.join(target_dest, member))
                    if not abs_target.startswith(target_dest + os.sep) and abs_target != target_dest:
                        raise ValueError(f"Unsafe path in archive: {member}")
                zf.extractall(target_dest)
        elif lower_name.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar")):
            with tarfile.open(abs_archive, "r:*") as tf:
                for member in tf.getmembers():
                    abs_target = os.path.realpath(os.path.join(target_dest, member.name))
                    if not abs_target.startswith(target_dest + os.sep) and abs_target != target_dest:
                        raise ValueError(f"Unsafe path in archive: {member.name}")
                tf.extractall(target_dest)
        else:
            return jsonify({"success": False, "message": "Unsupported archive format (supports .zip, .mrpack, .tar.gz)."}), 400

        return jsonify({"success": True, "message": f"Successfully extracted '{os.path.basename(abs_archive)}'!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Extraction error: {str(e)}"}), 500

@app.route("/api/files/download_dir")
@login_required
def api_files_download_dir():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    rel_path = request.args.get("path", "").strip()
    abs_path = safe_fiesta_path(rel_path)
    if not abs_path or not os.path.isdir(abs_path):
        return jsonify({"success": False, "message": "Directory not found."}), 404

    base_dir = os.path.realpath(SERVER_DIR)
    folder_name = os.path.basename(abs_path) if abs_path != base_dir else "server_root"

    tmp_dir = tempfile.gettempdir()
    tmp_fd, tmp_zip = tempfile.mkstemp(prefix=f"fiesta_dl_{folder_name}_", suffix=".zip", dir=tmp_dir)
    os.close(tmp_fd)

    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(abs_path):
                for f in files:
                    full_p = os.path.join(root, f)
                    rel_f = os.path.relpath(full_p, abs_path)
                    try:
                        zf.write(full_p, os.path.join(folder_name, rel_f))
                    except Exception:
                        pass

        resp = send_file(tmp_zip, as_attachment=True, download_name=f"{folder_name}.zip")
        @resp.call_on_close
        def _cleanup():
            try:
                if os.path.exists(tmp_zip):
                    os.remove(tmp_zip)
            except Exception:
                pass
        return resp
    except Exception as e:
        try:
            if os.path.exists(tmp_zip):
                os.remove(tmp_zip)
        except Exception:
            pass
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/files/copy", methods=["POST"])
@login_required
def api_files_copy():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    data = request.get_json() or {}
    src_rel = (data.get("path") or data.get("source_path") or "").strip()
    dest_dir_rel = (data.get("dest_dir") or data.get("destination_dir") or "").strip()
    new_name = (data.get("new_name") or data.get("target_name") or "").strip()

    abs_src = safe_fiesta_path(src_rel)
    if not abs_src or not os.path.exists(abs_src):
        return jsonify({"success": False, "message": "Source item not found."}), 404

    abs_dest_dir = safe_fiesta_path(dest_dir_rel)
    if not abs_dest_dir or not os.path.isdir(abs_dest_dir):
        abs_dest_dir = os.path.dirname(abs_src)

    if not new_name:
        src_base = os.path.basename(abs_src)
        if "." in src_base and not os.path.isdir(abs_src):
            parts = src_base.rsplit(".", 1)
            new_name = f"{parts[0]}_copy.{parts[1]}"
        else:
            new_name = f"{src_base}_copy"

    if "/" in new_name or "\\" in new_name or new_name in (".", ".."):
        return jsonify({"success": False, "message": "Invalid target filename."}), 400

    abs_dest = os.path.join(abs_dest_dir, new_name)
    base_dir = os.path.realpath(SERVER_DIR)
    if not os.path.realpath(abs_dest).startswith(base_dir + os.sep) and os.path.realpath(abs_dest) != base_dir:
        return jsonify({"success": False, "message": "Destination out of server bounds."}), 400

    if os.path.exists(abs_dest):
        return jsonify({"success": False, "message": f"Target '{new_name}' already exists."}), 400

    try:
        if os.path.isdir(abs_src):
            shutil.copytree(abs_src, abs_dest)
        else:
            shutil.copy2(abs_src, abs_dest)
        return jsonify({"success": True, "message": f"Copied successfully to '{new_name}'."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/files/batch-delete", methods=["POST"])
@login_required
def api_files_batch_delete():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: File manager permission required."}), 403

    data = request.get_json() or {}
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"success": False, "message": "No files selected."}), 400

    base_dir = os.path.realpath(SERVER_DIR)
    deleted = 0
    errors = []

    for p in paths:
        abs_p = safe_fiesta_path(p)
        if not abs_p or abs_p == base_dir:
            continue
        try:
            if os.path.isdir(abs_p):
                shutil.rmtree(abs_p)
                deleted += 1
            elif os.path.isfile(abs_p):
                os.remove(abs_p)
                deleted += 1
        except Exception as e:
            errors.append(f"{os.path.basename(abs_p)}: {str(e)}")

    return jsonify({
        "success": True,
        "message": f"Deleted {deleted} item(s)." + (f" ({len(errors)} error(s))" if errors else ""),
        "deleted_count": deleted
    })

# ── Configs ──────────────────────────────────────────────────────────────────
@app.route("/api/configs")
@login_required
def api_list_configs():
    found_configs = []
    # 1. Existing allowed configs
    for k, p in ALLOWED_CONFIGS.items():
        if os.path.exists(p):
            found_configs.append(k)

    # 2. Dynamic discovery of plugins configs
    plugins_dir = os.path.join(SERVER_DIR, "plugins")
    if os.path.isdir(plugins_dir):
        for root, dirs, files in os.walk(plugins_dir):
            rel_dir = os.path.relpath(root, plugins_dir)
            if rel_dir == "." or rel_dir.startswith("."):
                continue
            for f in files:
                if f.endswith((".yml", ".yaml", ".properties", ".json")) and not f.startswith("."):
                    rel_p = os.path.join("plugins", rel_dir, f).replace("\\", "/")
                    if rel_p not in found_configs and len(rel_p) < 120:
                        found_configs.append(rel_p)

    return jsonify({"configs": found_configs})

@app.route("/api/config/read")
@login_required
def api_read_config():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Parameter or File Manager permission required."}), 403

    name = request.args.get("name", "").strip()
    target_path = None
    if name in ALLOWED_CONFIGS and os.path.exists(ALLOWED_CONFIGS[name]):
        target_path = ALLOWED_CONFIGS[name]
    else:
        abs_p = safe_fiesta_path(name)
        if abs_p and os.path.isfile(abs_p) and abs_p.endswith((".yml", ".yaml", ".properties", ".json", ".conf", ".txt")):
            target_path = abs_p

    if not target_path or not os.path.exists(target_path):
        return jsonify({"success": False, "message": "Config not found"}), 404
    try:
        with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return jsonify({"success": True, "name": name, "content": content})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/config/save", methods=["POST"])
@login_required
def api_save_config():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Parameter editor permission required."}), 403

    data = request.get_json() or {}
    name = data.get("name", "").strip()
    content = data.get("content", "")

    target_path = None
    if name in ALLOWED_CONFIGS:
        target_path = ALLOWED_CONFIGS[name]
    else:
        abs_p = safe_fiesta_path(name)
        if abs_p and abs_p.endswith((".yml", ".yaml", ".properties", ".json", ".conf", ".txt")):
            target_path = abs_p

    if not target_path:
        return jsonify({"success": False, "message": "Unauthorized file target"}), 403

    try:
        backup_path = target_path + ".bak"
        if os.path.exists(target_path):
            with open(target_path, "r", encoding="utf-8", errors="ignore") as src, \
                 open(backup_path, "w", encoding="utf-8") as dst:
                dst.write(src.read())

        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)
        return jsonify({"success": True, "message": f"Saved {name} successfully (snapshot {os.path.basename(name)}.bak created)!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ── Connect QR Code Generator ────────────────────────────────────────────────
@app.route("/api/connect/qr.png")
def api_connect_qr():
    data = request.args.get("data", "").strip()
    if not data:
        data = request.host_url
    try:
        import qrcode
        img = qrcode.make(data)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Banned IPs Dedicated Endpoints ───────────────────────────────────────────
@app.route("/api/banned-ips", methods=["GET", "POST", "DELETE"])
@login_required
def api_banned_ips_route():
    ip_ban_file = os.path.join(SERVER_DIR, "banned-ips.json")
    if request.method == "GET":
        ip_bans = []
        if os.path.exists(ip_ban_file):
            try:
                with open(ip_ban_file, "r", encoding="utf-8", errors="ignore") as f:
                    ip_bans = json.load(f)
            except Exception:
                pass
        _, reverse_map = get_all_player_ips()
        for ib in ip_bans:
            cur_ip = (ib.get("ip") or "").strip()
            ib["player"] = reverse_map.get(cur_ip, "Unknown")
        return jsonify({"success": True, "ip_bans": ip_bans, "total": len(ip_bans)})

    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "players" not in curr_perms and "security" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied"}), 403

    if request.method == "POST":
        data = request.get_json() or {}
        ip = data.get("ip", "").strip()
        reason = data.get("reason", "").strip() or "Banned by administrator"
        if not ip:
            return jsonify({"success": False, "message": "IP address is required"}), 400
        success, msg = execute_ip_ban(ip, reason)
        return jsonify({"success": success, "message": msg})

    if request.method == "DELETE":
        data = request.get_json() or {}
        ip = data.get("ip", "").strip()
        if not ip:
            return jsonify({"success": False, "message": "IP address is required"}), 400
        success, msg = execute_ip_pardon(ip)
        return jsonify({"success": success, "message": msg})

# ── Aternos Options Tab Switchboard API ──────────────────────────────────────
@app.route("/api/options", methods=["GET", "POST"])
@login_required
def api_game_options():
    props_path = os.path.join(SERVER_DIR, "server.properties")
    if not os.path.exists(props_path):
        return jsonify({"success": False, "message": "server.properties not found"}), 404

    if request.method == "GET":
        props = {}
        try:
            with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        props[k.strip()] = v.strip()
            return jsonify({"success": True, "options": props})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500

    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied"}), 403

    raw_updates = request.get_json() or {}
    if not raw_updates:
        return jsonify({"success": False, "message": "No settings to update"}), 400

    updates = {}
    for k, v in raw_updates.items():
        clean_k = re.sub(r'[^a-zA-Z0-9_\.\-]', '', str(k)).strip()
        if clean_k:
            updates[clean_k] = str(v).replace("\r", "").replace("\n", "\\n")

    if not updates:
        return jsonify({"success": False, "message": "No valid settings to update"}), 400

    try:
        shutil.copy2(props_path, props_path + ".bak")
        lines = []
        seen = set()
        with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    if k in updates:
                        lines.append(f"{k}={updates[k]}\n")
                        seen.add(k)
                    else:
                        lines.append(line)
                else:
                    lines.append(line)
        for k, v in updates.items():
            if k not in seen:
                lines.append(f"{k}={v}\n")
        with open(props_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return jsonify({"success": True, "message": "Game options saved successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ── Worlds Hub Endpoints ─────────────────────────────────────────────────────
@app.route("/api/worlds", methods=["GET"])
@login_required
def api_worlds_list():
    worlds = []
    level_name = get_main_level_name()
    known_dims = [level_name, f"{level_name}_nether", f"{level_name}_the_end"]
    if "world" not in known_dims:
        known_dims.append("world")

    seed = ""
    level_type = "minecraft:normal"
    props_path = os.path.join(SERVER_DIR, "server.properties")
    if os.path.exists(props_path):
        try:
            with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("level-seed="):
                        seed = line.split("=", 1)[1].strip()
                    elif line.startswith("level-type="):
                        level_type = line.split("=", 1)[1].strip()
        except Exception:
            pass

    for item in os.listdir(SERVER_DIR):
        item_path = os.path.join(SERVER_DIR, item)
        if not os.path.isdir(item_path):
            continue
        has_level = os.path.exists(os.path.join(item_path, "level.dat"))
        has_region = os.path.exists(os.path.join(item_path, "region")) or os.path.exists(os.path.join(item_path, "DIM-1")) or os.path.exists(os.path.join(item_path, "DIM1"))
        if has_level or has_region or item in known_dims:
            total_size = 0
            region_count = 0
            last_mtime = 0
            for root, dirs, files in os.walk(item_path):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        st = os.stat(fp)
                        total_size += st.st_size
                        if st.st_mtime > last_mtime:
                            last_mtime = st.st_mtime
                        if f.endswith(".mca"):
                            region_count += 1
                    except Exception:
                        pass

            dim_type = "Overworld"
            if "nether" in item.lower():
                dim_type = "Nether"
            elif "end" in item.lower():
                dim_type = "The End"

            datapack_count = 0
            dp_dir = os.path.join(item_path, "datapacks")
            if os.path.isdir(dp_dir):
                datapack_count = len([x for x in os.listdir(dp_dir) if not x.startswith(".")])

            worlds.append({
                "name": item,
                "type": dim_type,
                "is_active": (item == level_name),
                "size_bytes": total_size,
                "size_formatted": format_file_size(total_size),
                "region_files": region_count,
                "datapack_count": datapack_count,
                "seed": seed,
                "level_type": level_type,
                "last_modified": datetime.datetime.fromtimestamp(last_mtime).strftime("%Y-%m-%d %H:%M") if last_mtime else "N/A"
            })
    return jsonify({"success": True, "worlds": worlds, "active_world": level_name})

@app.route("/api/worlds/download")
@login_required
def api_worlds_download():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "backups" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Backups or Parameter permission required."}), 403

    world_name = request.args.get("world", "").strip()
    safe_name = os.path.basename(world_name)
    if not safe_name or safe_name in (".", "..") or "/" in world_name or "\\" in world_name:
        return jsonify({"success": False, "message": "Invalid world name"}), 400
    abs_path = safe_fiesta_path(safe_name)
    if not abs_path or not os.path.isdir(abs_path):
        return jsonify({"success": False, "message": "World directory not found"}), 404

    tmp_dir = tempfile.gettempdir()
    tmp_fd, zip_path = tempfile.mkstemp(prefix=f"fiesta_world_{safe_name}_", suffix=".zip", dir=tmp_dir)
    os.close(tmp_fd)

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(abs_path):
                for file in files:
                    if file.endswith(".lock") or file == "session.lock":
                        continue
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, abs_path)
                    try:
                        zf.write(full_path, os.path.join(safe_name, rel_path))
                    except Exception:
                        pass

        resp = send_file(zip_path, as_attachment=True, download_name=f"{safe_name}.zip")
        @resp.call_on_close
        def _cleanup():
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
            except Exception:
                pass
        return resp
    except Exception as e:
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except Exception:
            pass
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/worlds/upload", methods=["POST"])
@login_required
def api_worlds_upload():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Owner or Administrator permission required for world upload"}), 403

    if server_runner.is_running():
        return jsonify({"success": False, "message": "Minecraft server must be stopped before uploading or replacing world files."}), 400

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"success": False, "message": "No file selected"}), 400
    if not file.filename.lower().endswith(".zip"):
        return jsonify({"success": False, "message": "Only .zip world archives are supported"}), 400

    target_world = os.path.basename(request.form.get("world", get_main_level_name()).strip())
    if not target_world or target_world in (".", ".."):
        return jsonify({"success": False, "message": "Invalid world target"}), 400
    abs_world = safe_fiesta_path(target_world)
    if not abs_world:
        return jsonify({"success": False, "message": "Invalid world target"}), 400

    # Auto-snapshot before replacing
    backup_dir = get_backup_dir()
    os.makedirs(backup_dir, exist_ok=True)
    if os.path.exists(abs_world):
        snap_path = os.path.join(backup_dir, f"pre_upload_{target_world}_{int(time.time())}.zip")
        try:
            with zipfile.ZipFile(snap_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(abs_world):
                    for f in files:
                        if not f.endswith(".lock"):
                            fp = os.path.join(root, f)
                            zf.write(fp, os.path.relpath(fp, abs_world))
        except Exception:
            pass

    # Extract safely
    tmp_fd, temp_zip = tempfile.mkstemp(prefix=f"fiesta_upload_{target_world}_", suffix=".zip")
    os.close(tmp_fd)
    file.save(temp_zip)
    try:
        with zipfile.ZipFile(temp_zip, "r") as zf:
            for member in zf.namelist():
                if ".." in member or member.startswith("/") or member.startswith("\\"):
                    return jsonify({"success": False, "message": "Malicious zip archive detected"}), 400

            namelist = zf.namelist()
            root_prefix = ""
            for name in namelist:
                if name.endswith("level.dat"):
                    root_prefix = os.path.dirname(name)
                    break

            os.makedirs(abs_world, exist_ok=True)
            for member in zf.infolist():
                if member.is_dir():
                    continue
                orig_name = member.filename
                if root_prefix and orig_name.startswith(root_prefix + "/"):
                    rel = orig_name[len(root_prefix) + 1:]
                elif root_prefix and orig_name == root_prefix:
                    continue
                else:
                    rel = orig_name
                if not rel or rel.endswith(".lock"):
                    continue
                dest_file = os.path.join(abs_world, rel)
                os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                with zf.open(member) as src, open(dest_file, "wb") as dst:
                    shutil.copyfileobj(src, dst)

        return jsonify({"success": True, "message": f"World '{target_world}' uploaded and installed successfully! Reboot server to load."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if os.path.exists(temp_zip):
            try:
                os.remove(temp_zip)
            except Exception:
                pass

@app.route("/api/worlds/reset-dimension", methods=["POST"])
@login_required
def api_worlds_reset_dimension():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Owner or Administrator permission required for dimension reset"}), 403

    if server_runner.is_running():
        return jsonify({"success": False, "message": "Minecraft server must be stopped before resetting dimensions to prevent world corruption."}), 400

    data = request.get_json() or {}
    dim = data.get("dimension", "").lower().strip()

    if dim not in ("nether", "the_end", "overworld"):
        return jsonify({"success": False, "message": "Dimension must be 'nether', 'the_end', or 'overworld'"}), 400

    level_name = get_main_level_name()
    target_dir_name = f"{level_name}_nether" if dim == "nether" else (f"{level_name}_the_end" if dim == "the_end" else level_name)
    abs_dir = os.path.join(SERVER_DIR, target_dir_name)
    if not os.path.exists(abs_dir) and level_name != "world":
        fallback_dir_name = "world_nether" if dim == "nether" else ("world_the_end" if dim == "the_end" else "world")
        if os.path.exists(os.path.join(SERVER_DIR, fallback_dir_name)):
            target_dir_name = fallback_dir_name
            abs_dir = os.path.join(SERVER_DIR, target_dir_name)

    backup_dir = get_backup_dir()
    os.makedirs(backup_dir, exist_ok=True)
    if os.path.exists(abs_dir):
        snap_path = os.path.join(backup_dir, f"reset_snap_{dim}_{int(time.time())}.zip")
        try:
            with zipfile.ZipFile(snap_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(abs_dir):
                    for f in files:
                        if not f.endswith(".lock"):
                            fp = os.path.join(root, f)
                            zf.write(fp, os.path.relpath(fp, abs_dir))
        except Exception:
            pass

    dirs_to_clean = ["region", "entities", "poi"]
    for d in dirs_to_clean:
        dp = os.path.join(abs_dir, d)
        if os.path.isdir(dp):
            shutil.rmtree(dp, ignore_errors=True)
            os.makedirs(dp, exist_ok=True)

    main_world = os.path.join(SERVER_DIR, level_name)
    if not os.path.exists(main_world) and os.path.exists(os.path.join(SERVER_DIR, "world")):
        main_world = os.path.join(SERVER_DIR, "world")
    dim_sub = "DIM-1" if dim == "nether" else ("DIM1" if dim == "the_end" else "")
    if dim_sub:
        dim_sub_path = os.path.join(main_world, dim_sub)
        if os.path.isdir(dim_sub_path):
            for d in dirs_to_clean:
                dp = os.path.join(dim_sub_path, d)
                if os.path.isdir(dp):
                    shutil.rmtree(dp, ignore_errors=True)
                    os.makedirs(dp, exist_ok=True)

    label = "Nether" if dim == "nether" else ("The End" if dim == "the_end" else "Overworld")
    return jsonify({"success": True, "message": f"{label} dimension reset successfully! Pristine new terrain will generate when visited."})

@app.route("/api/worlds/generate", methods=["POST"])
@login_required
def api_worlds_generate():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Owner or Administrator permission required for world generation"}), 403

    if server_runner.is_running():
        return jsonify({"success": False, "message": "Minecraft server must be stopped before generating a new world."}), 400

    data = request.get_json() or {}
    world_name = data.get("name", get_main_level_name()).strip()
    safe_name = os.path.basename(world_name)
    if not safe_name or safe_name in (".", "..") or "/" in world_name or "\\" in world_name:
        return jsonify({"success": False, "message": "Invalid world directory name."}), 400
    abs_world = safe_fiesta_path(safe_name)
    if not abs_world:
        return jsonify({"success": False, "message": "World target path must remain inside server directory."}), 400

    seed = data.get("seed", "").strip()
    level_type = data.get("type", "minecraft:normal").strip()
    structures = str(data.get("structures", True)).lower()
    hardcore = str(data.get("hardcore", False)).lower()

    props_path = os.path.join(SERVER_DIR, "server.properties")
    if os.path.exists(props_path):
        updates = {
            "level-name": safe_name,
            "level-seed": seed,
            "level-type": level_type,
            "generate-structures": structures,
            "hardcore": hardcore
        }
        try:
            shutil.copy2(props_path, props_path + ".bak")
            lines = []
            seen = set()
            with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and "=" in stripped:
                        k = stripped.split("=", 1)[0].strip()
                        if k in updates:
                            lines.append(f"{k}={updates[k]}\n")
                            seen.add(k)
                        else:
                            lines.append(line)
                    else:
                        lines.append(line)
            for k, v in updates.items():
                if k not in seen:
                    lines.append(f"{k}={v}\n")
            with open(props_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception:
            pass

    if os.path.isdir(abs_world):
        backup_dir = get_backup_dir()
        os.makedirs(backup_dir, exist_ok=True)
        snap_path = os.path.join(backup_dir, f"pre_gen_{safe_name}_{int(time.time())}.zip")
        try:
            with zipfile.ZipFile(snap_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(abs_world):
                    for f in files:
                        if not f.endswith(".lock"):
                            fp = os.path.join(root, f)
                            zf.write(fp, os.path.relpath(fp, abs_world))
        except Exception:
            pass
        shutil.rmtree(abs_world, ignore_errors=True)

    return jsonify({"success": True, "message": f"New world configuration generated with seed '{seed or 'random'}'! Start or reboot the server to create terrain."})

@app.route("/api/worlds/set-active", methods=["POST"])
@login_required
def api_worlds_set_active():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Parameter permission required."}), 403

    data = request.get_json(silent=True) or {}
    world_name = data.get("world", "").strip()
    safe_name = os.path.basename(world_name)
    if not safe_name or safe_name in (".", "..") or "/" in world_name or "\\" in world_name:
        return jsonify({"success": False, "message": "Invalid world name."}), 400

    abs_world = safe_fiesta_path(safe_name)
    if not abs_world or not os.path.isdir(abs_world):
        return jsonify({"success": False, "message": f"World directory '{safe_name}' does not exist."}), 404

    props_path = os.path.join(SERVER_DIR, "server.properties")
    lines = []
    found = False
    if os.path.exists(props_path):
        with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.strip().startswith("level-name="):
                    lines.append(f"level-name={safe_name}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"level-name={safe_name}\n")

    with open(props_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return jsonify({"success": True, "message": f"Active world changed to '{safe_name}'. Restart the server to load this world."})

@app.route("/api/worlds/optimize", methods=["POST"])
@login_required
def api_worlds_optimize():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied: Parameter permission required."}), 403

    data = request.get_json(silent=True) or {}
    world_name = os.path.basename(data.get("world") or get_main_level_name())
    mode = data.get("mode", "clean_cache")

    abs_world = safe_fiesta_path(world_name)
    cleaned_count = 0
    if abs_world and os.path.isdir(abs_world):
        if not server_runner.is_running():
            for root, dirs, files in os.walk(abs_world):
                for f in files:
                    if f in ("session.lock", "uid.dat.tmp") or f.endswith(".tmp") or f.endswith(".lock"):
                        try:
                            os.remove(os.path.join(root, f))
                            cleaned_count += 1
                        except Exception:
                            pass

    flag_msg = ""
    if mode in ("force_upgrade", "both"):
        rt = server_runner.load_runtime_config()
        jvm_flags = rt.get("jvm_flags", [])
        if "--forceUpgrade" not in jvm_flags:
            jvm_flags.append("--forceUpgrade")
        if "--eraseCache" not in jvm_flags:
            jvm_flags.append("--eraseCache")
        rt["jvm_flags"] = jvm_flags
        server_runner.save_runtime_config(rt)
        flag_msg = " Configured server to run full force-upgrade on next reboot."

    if server_runner.is_running():
        send_mc_command("minecraft:save-all flush")
        return jsonify({"success": True, "message": f"World '{world_name}' flushed to disk.{flag_msg} (Cleaned {cleaned_count} lock files)"})
    else:
        return jsonify({"success": True, "message": f"World '{world_name}' optimized! Removed {cleaned_count} temporary/lock files.{flag_msg}"})

@app.route("/api/worlds/datapacks", methods=["GET", "POST"])
@login_required
def api_worlds_datapacks():
    target_world = request.args.get("world") or (request.form.get("world") if request.form else None) or ((request.get_json(silent=True) or {}).get("world")) or get_main_level_name()
    target_world = os.path.basename(str(target_world).strip())
    dp_dir = os.path.join(SERVER_DIR, target_world, "datapacks")
    if not os.path.exists(dp_dir) and os.path.exists(os.path.join(SERVER_DIR, "world", "datapacks")) and target_world == get_main_level_name():
        dp_dir = os.path.join(SERVER_DIR, "world", "datapacks")
    os.makedirs(dp_dir, exist_ok=True)

    if request.method == "GET":
        packs = []
        for item in os.listdir(dp_dir):
            if item.startswith("."):
                continue
            item_path = os.path.join(dp_dir, item)
            desc = "Datapack"
            pack_format = "Unknown"
            size = os.path.getsize(item_path) if os.path.isfile(item_path) else 0

            if os.path.isdir(item_path):
                mcmeta_path = os.path.join(item_path, "pack.mcmeta")
                if os.path.exists(mcmeta_path):
                    try:
                        with open(mcmeta_path, "r", encoding="utf-8", errors="ignore") as f:
                            meta = json.load(f)
                            desc = meta.get("pack", {}).get("description", desc)
                            pack_format = meta.get("pack", {}).get("pack_format", pack_format)
                    except Exception:
                        pass
                for root, dirs, files in os.walk(item_path):
                    for f in files:
                        try:
                            size += os.path.getsize(os.path.join(root, f))
                        except Exception:
                            pass
            elif item.endswith(".zip"):
                try:
                    with zipfile.ZipFile(item_path, "r") as zf:
                        if "pack.mcmeta" in zf.namelist():
                            with zf.open("pack.mcmeta") as mf:
                                meta = json.load(mf)
                                desc = meta.get("pack", {}).get("description", desc)
                                pack_format = meta.get("pack", {}).get("pack_format", pack_format)
                except Exception:
                    pass

            packs.append({
                "name": item,
                "description": desc if isinstance(desc, str) else json.dumps(desc),
                "format": pack_format,
                "size_formatted": format_file_size(size),
                "is_dir": os.path.isdir(item_path)
            })
        return jsonify({"success": True, "datapacks": packs, "world": target_world})

    if request.method == "POST":
        curr_perms = session.get("permissions", [])
        if "all" not in curr_perms and "parameters" not in curr_perms and "filemanager" not in curr_perms and session.get("role") not in ("owner", "administrator"):
            return jsonify({"success": False, "message": "Permission denied: Parameter or File Manager permission required."}), 403

    action = request.form.get("action") or (request.get_json(silent=True) or {}).get("action")
    if action == "upload":
        if "file" not in request.files:
            return jsonify({"success": False, "message": "No file uploaded"}), 400
        file = request.files["file"]
        if not file or not file.filename:
            return jsonify({"success": False, "message": "No file selected"}), 400
        safe_name = os.path.basename(file.filename)
        if not safe_name or safe_name in (".", ".."):
            return jsonify({"success": False, "message": "Invalid filename"}), 400
        dest = os.path.join(dp_dir, safe_name)
        file.save(dest)
        send_mc_command("datapack list")
        return jsonify({"success": True, "message": f"Datapack '{safe_name}' uploaded successfully to world '{target_world}'!"})

    if action == "delete":
        name = request.form.get("name") or (request.get_json(silent=True) or {}).get("name")
        safe_name = os.path.basename(str(name or "").strip())
        if not safe_name or safe_name in (".", ".."):
            return jsonify({"success": False, "message": "Valid datapack name required"}), 400
        target = os.path.join(dp_dir, safe_name)
        if os.path.exists(target):
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
            return jsonify({"success": True, "message": f"Datapack '{safe_name}' removed from world '{target_world}'."})
        return jsonify({"success": False, "message": "Datapack not found"}), 404

    if action == "toggle":
        name = request.form.get("name") or (request.get_json(silent=True) or {}).get("name")
        mode = str(request.form.get("mode") or (request.get_json(silent=True) or {}).get("mode") or "").strip().lower()
        if mode not in ("enable", "disable"):
            return jsonify({"success": False, "message": "Mode must be 'enable' or 'disable'"}), 400
        safe_name = os.path.basename(str(name or "").strip())
        if not safe_name or safe_name in (".", ".."):
            return jsonify({"success": False, "message": "Valid datapack name required"}), 400
        cmd = f"datapack {mode} \"file/{safe_name}\""
        success, msg = send_mc_command(cmd)
        return jsonify({"success": success, "message": f"Dispatched: {cmd}"})

    return jsonify({"success": False, "message": "Unknown action"}), 400

# ── Minecraft MOTD Visual Editor System ───────────────────────────────────────
def format_motd_preview(raw_motd):
    colors = {
        '0': '#000000', '1': '#0000AA', '2': '#00AA00', '3': '#00AAAA',
        '4': '#AA0000', '5': '#AA00AA', '6': '#FFAA00', '7': '#AAAAAA',
        '8': '#555555', '9': '#5555FF', 'a': '#55FF55', 'b': '#55FFFF',
        'c': '#FF5555', 'd': '#FF55FF', 'e': '#FFFF55', 'f': '#FFFFFF'
    }
    text = (raw_motd or "").replace('\\n', '\n').replace('&', '§')
    lines = text.split('\n')
    formatted_lines = []
    for line in lines[:2]:
        out = ''
        i = 0
        curr_color = '#AAAAAA'
        bold = italic = under = strike = obf = False
        while i < len(line):
            if line[i] == '§' and i + 1 < len(line):
                code = line[i+1].lower()
                if code in colors:
                    curr_color = colors[code]
                    bold = italic = under = strike = obf = False
                elif code == 'l': bold = True
                elif code == 'o': italic = True
                elif code == 'n': under = True
                elif code == 'm': strike = True
                elif code == 'k': obf = True
                elif code == 'r':
                    curr_color = '#AAAAAA'
                    bold = italic = under = strike = obf = False
                i += 2
                continue
            styles = [f'color:{curr_color}']
            if bold: styles.append('font-weight:bold')
            if italic: styles.append('font-style:italic')
            if under: styles.append('text-decoration:underline')
            if strike: styles.append('text-decoration:line-through')
            char = line[i].replace('<', '&lt;').replace('>', '&gt;').replace(' ', '&nbsp;')
            cls = 'class="mc-obf"' if obf else ''
            style_str = ";".join(styles)
            out += f'<span {cls} style="{style_str}">{char}</span>'
            i += 1
        formatted_lines.append(out or '<span style="color:#555555;">&nbsp;</span>')
    return formatted_lines

@app.route("/api/motd", methods=["GET", "POST"])
@login_required
def api_motd():
    props_path = os.path.join(SERVER_DIR, "server.properties")
    curr_perms = session.get("permissions", [])
    if request.method == "POST":
        if "all" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
            return jsonify({"success": False, "message": "Permission denied: Parameter permission required."}), 403

        data = request.get_json(silent=True) or {}
        line1 = data.get("line1", "").strip()
        line2 = data.get("line2", "").strip()
        raw_motd = data.get("motd")

        if raw_motd is None:
            raw_motd = f"{line1}\\n{line2}" if line2 else line1

        safe_motd = raw_motd.replace("\r", "")
        stored_motd = safe_motd.replace("\n", "\\n").replace('&', '§')
        lines = []
        found = False
        if os.path.exists(props_path):
            with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip().startswith("motd="):
                        lines.append(f"motd={stored_motd}\n")
                        found = True
                    else:
                        lines.append(line)
        if not found:
            lines.append(f"motd={stored_motd}\n")

        with open(props_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        norm_ret = safe_motd.replace('\\n', '\n')
        parts_ret = norm_ret.split('\n')
        l1_ret = parts_ret[0] if len(parts_ret) > 0 else ""
        l2_ret = parts_ret[1] if len(parts_ret) > 1 else ""
        preview_html = format_motd_preview(safe_motd)
        l1_html = preview_html[0] if len(preview_html) > 0 else ""
        l2_html = preview_html[1] if len(preview_html) > 1 else ""
        return jsonify({
            "success": True,
            "message": "MOTD updated successfully in server.properties!",
            "motd": safe_motd,
            "line1": l1_ret,
            "line2": l2_ret,
            "line1_html": l1_html,
            "line2_html": l2_html,
            "preview_html": preview_html
        })

    raw_motd = "A Minecraft Server Powered by FiestaPanel"
    if os.path.exists(props_path):
        try:
            with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip().startswith("motd="):
                        raw_motd = line.split("=", 1)[1].strip()
                        break
        except Exception:
            pass

    norm = raw_motd.replace('\\n', '\n')
    parts = norm.split('\n')
    line1 = parts[0].replace('§', '&') if len(parts) > 0 else ""
    line2 = parts[1].replace('§', '&') if len(parts) > 1 else ""

    return jsonify({
        "success": True,
        "motd": raw_motd,
        "line1": line1,
        "line2": line2,
        "preview_html": format_motd_preview(raw_motd)
    })

# ── Log Intelligence & One-Click mclo.gs ───────────────────────────────────────
@app.route("/api/logs/share-mclogs", methods=["POST"])
@login_required
def api_logs_share_mclogs():
    log_path = os.path.join(SERVER_DIR, "logs", "latest.log")
    if not os.path.exists(log_path):
        return jsonify({"success": False, "message": "latest.log not found"}), 404

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-6000:]
        raw_text = "".join(lines)

        sanitized = re.sub(r"\b(?!127\.0\.0\.1|0\.0\.0\.0)(?:\d{1,3}\.){3}\d{1,3}\b", "[IP REDACTED]", raw_text)
        sanitized = re.sub(r"https://discord(?:app)?\.com/api/webhooks/[a-zA-Z0-9_\-\/]+", "https://discord.com/api/webhooks/[REDACTED]", sanitized)
        sanitized = re.sub(r"(rcon\.password\s*=\s*)[^\n\r]+", r"\1[REDACTED]", sanitized)
        sanitized = re.sub(r"(password\s*:\s*)[^\n\r]+", r"\1[REDACTED]", sanitized)

        data = urllib.parse.urlencode({"content": sanitized}).encode("utf-8")
        req = urllib.request.Request("https://api.mclo.gs/1/log", data=data, headers={"User-Agent": get_panel_user_agent()})
        with urllib.request.urlopen(req, timeout=12) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))

        if res_data.get("success"):
            return jsonify({
                "success": True,
                "url": res_data.get("url"),
                "raw": res_data.get("raw"),
                "message": "Log uploaded securely to mclo.gs!"
            })
        return jsonify({"success": False, "message": res_data.get("error", "mclo.gs upload error")}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Upload failed: {str(e)}"}), 500

@app.route("/api/logs/analyze", methods=["GET"])
@login_required
def api_logs_analyze():
    log_path = os.path.join(SERVER_DIR, "logs", "latest.log")
    findings = []
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-3000:]
            text = "".join(lines)

            if "OutOfMemoryError" in text:
                findings.append({
                    "severity": "critical",
                    "title": "Out of Memory Error (OOM)",
                    "description": "Java Virtual Machine exhausted heap memory limit.",
                    "solution": "Increase memory (-Xmx) or remove heavy entity/chunk loaders."
                })
            if "FAILED TO BIND TO PORT" in text or "Address already in use" in text:
                configured_port = get_server_port()
                findings.append({
                    "severity": "critical",
                    "title": "Port Binding Conflict",
                    "description": f"Port {configured_port} is already occupied by another running server instance.",
                    "solution": "Kill lingering Java instances or adjust server-port in server.properties."
                })
            if "Corrupt chunk" in text or "RegionFileException" in text:
                findings.append({
                    "severity": "critical",
                    "title": "Corrupted Chunk Region",
                    "description": "Damaged world chunk detected during chunk load.",
                    "solution": "Prune corrupted MCA region files or restore from backup."
                })
            if "Could not load" in text:
                failed = re.findall(r"Could not load [\'\"]plugins/([^\'\"]+)", text)
                if failed:
                    findings.append({
                        "severity": "warning",
                        "title": f"Plugin Load Failure ({len(failed)})",
                        "description": f"Failed plugins: {', '.join(failed[:3])}",
                        "solution": "Verify plugin dependencies and Java 21 compatibility."
                    })
            if "Can't keep up! Is the server overloaded?" in text:
                findings.append({
                    "severity": "warning",
                    "title": "Tick Loop Overload",
                    "description": "Server fell behind real time by several ticks.",
                    "solution": "Check /spark health, lower simulation distance, or run /gc."
                })
        except Exception:
            pass
    return jsonify({"success": True, "findings": findings, "healthy": len(findings) == 0})

# ── Resource Pack Host & Auto-SHA1 ───────────────────────────────────────────
@app.route("/api/resourcepack/upload", methods=["POST"])
@login_required
def api_resourcepack_upload():
    curr_perms = session.get("permissions", [])
    if "all" not in curr_perms and "parameters" not in curr_perms and session.get("role") not in ("owner", "administrator"):
        return jsonify({"success": False, "message": "Permission denied"}), 403

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
    file = request.files["file"]
    if not file.filename.endswith(".zip"):
        return jsonify({"success": False, "message": "Resource pack must be a .zip file"}), 400

    static_dir = os.path.join(BASE_DIR, "static")
    os.makedirs(static_dir, exist_ok=True)
    target_path = os.path.join(static_dir, "resourcepack.zip")
    file.save(target_path)

    sha1 = hashlib.sha1()
    with open(target_path, "rb") as f:
        while chunk := f.read(65536):
            sha1.update(chunk)
    pack_hash = sha1.hexdigest()
    pack_size = os.path.getsize(target_path)

    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
    host = request.host
    dl_url = f"{scheme}://{host}/static/resourcepack.zip"

    props_path = os.path.join(SERVER_DIR, "server.properties")
    if os.path.exists(props_path):
        try:
            updates = {
                "resource-pack": dl_url,
                "resource-pack-sha1": pack_hash
            }
            lines = []
            seen = set()
            with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and "=" in stripped:
                        k = stripped.split("=", 1)[0].strip()
                        if k in updates:
                            lines.append(f"{k}={updates[k]}\n")
                            seen.add(k)
                        else:
                            lines.append(line)
                    else:
                        lines.append(line)
            for k, v in updates.items():
                if k not in seen:
                    lines.append(f"{k}={v}\n")
            with open(props_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception:
            pass

    return jsonify({
        "success": True,
        "url": dl_url,
        "sha1": pack_hash,
        "size_formatted": format_file_size(pack_size),
        "message": "Resource pack uploaded, SHA-1 generated, and server.properties updated!"
    })

# ── Automated Tasks & Lifecycle Scheduler ───────────────────────────────────
TASKS_CONFIG_FILE = os.path.join(DATA_DIR, "automated_tasks.json")

def load_automated_tasks():
    default_tasks = {
        "daily_restart": {"enabled": False, "time": "04:00", "last_run": None},
        "daily_backup": {"enabled": False, "time": "05:00", "last_run": None},
        "idle_auto_stop": {"enabled": False, "timeout_minutes": 30, "idle_since": None}
    }
    if os.path.isfile(TASKS_CONFIG_FILE):
        try:
            with open(TASKS_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for k in ("daily_restart", "daily_backup", "idle_auto_stop"):
                        if k in data and isinstance(data[k], dict):
                            default_tasks[k].update(data[k])
                return default_tasks
        except Exception as e:
            app.logger.warning(f"Failed to load automated tasks: {e}")
    return default_tasks

def save_automated_tasks(tasks):
    try:
        os.makedirs(os.path.dirname(TASKS_CONFIG_FILE), exist_ok=True)
        tmp_file = f"{TASKS_CONFIG_FILE}.tmp.{os.getpid()}"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(tasks, f, indent=2)
        os.replace(tmp_file, TASKS_CONFIG_FILE)
    except Exception as e:
        app.logger.error(f"Failed to save automated tasks: {e}")

def _automated_tasks_worker():
    while True:
        try:
            time.sleep(30)
            tasks = load_automated_tasks()
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            current_hm = now.strftime("%H:%M")
            modified = False

            # 1. Daily Restart
            dr = tasks.get("daily_restart", {})
            if dr.get("enabled") and dr.get("time") == current_hm:
                if dr.get("last_run") != today_str:
                    dr["last_run"] = today_str
                    modified = True
                    send_mc_command("say [FiestaPanel] Scheduled daily restart initiated in 15 seconds...")
                    time.sleep(15)
                    send_mc_command("minecraft:save-all flush")
                    time.sleep(3)
                    server_runner.restart()

            # 2. Daily Backup
            db = tasks.get("daily_backup", {})
            if db.get("enabled") and db.get("time") == current_hm:
                if db.get("last_run") != today_str:
                    db["last_run"] = today_str
                    modified = True
                    backup_dir = get_backup_dir()
                    os.makedirs(backup_dir, exist_ok=True)
                    svc_prefix = (SERVICE_NAME or "server").lower().replace(" ", "-")
                    target_tar = os.path.join(backup_dir, f"{svc_prefix}_auto_backup_{now.strftime('%Y%m%d_%H%M%S')}.tar.gz")
                    send_mc_command("save-all")
                    time.sleep(2)
                    try:
                        def exclude_backups(tarinfo):
                            if tarinfo.name.endswith((".tar.gz", ".tar.zst", ".zip", ".tar.bz2", ".tar.xz")):
                                return None
                            parts = tarinfo.name.split("/")
                            if "backups" in parts or ".fiesta_server.pid" in parts:
                                return None
                            return tarinfo

                        with tarfile.open(target_tar, "w:gz") as tar:
                            tar.add(SERVER_DIR, arcname=os.path.basename(SERVER_DIR), filter=exclude_backups)
                        # Auto-sync to cloud if configured
                        c_cfg = cloud_backup_mgr.load_config()
                        if c_cfg.get("enabled") and c_cfg.get("auto_sync", True):
                            cloud_backup_mgr.start_async_upload(target_tar)
                    except Exception as err:
                        app.logger.error(f"Automated daily backup error: {err}")

            # 3. Idle Auto-Stop
            ias = tasks.get("idle_auto_stop", {})
            if ias.get("enabled"):
                p_info = ping_mc()
                is_online = bool(p_info.get("online"))
                p_count = int(p_info.get("players_online", 0))
                if is_online:
                    if p_count == 0:
                        if not ias.get("idle_since"):
                            ias["idle_since"] = time.time()
                            modified = True
                        else:
                            idle_mins = (time.time() - ias["idle_since"]) / 60
                            limit = int(ias.get("timeout_minutes", 30))
                            if idle_mins >= limit:
                                ias["idle_since"] = None
                                modified = True
                                send_mc_command("say [FiestaPanel] Server has been idle with 0 players. Shutting down to conserve resources...")
                                time.sleep(5)
                                server_runner.stop()
                    else:
                        if ias.get("idle_since") is not None:
                            ias["idle_since"] = None
                            modified = True
                else:
                    if ias.get("idle_since") is not None:
                        ias["idle_since"] = None
                        modified = True

            if modified:
                save_automated_tasks(tasks)
        except Exception:
            pass

threading.Thread(target=_automated_tasks_worker, daemon=True).start()

@app.route("/api/tasks", methods=["GET", "POST"])
@login_required
def api_tasks_route():
    tasks = load_automated_tasks()

    if request.method == "GET":
        idle_info = "Disabled"
        ias = tasks.get("idle_auto_stop", {})
        if ias.get("enabled"):
            if ias.get("idle_since"):
                mins = int((time.time() - ias["idle_since"]) / 60)
                limit = int(ias.get("timeout_minutes", 30))
                idle_info = f"Idle for {mins}m / {limit}m limit"
            else:
                idle_info = "Active (Monitoring players)"
        return jsonify({"success": True, "tasks": tasks, "idle_state": idle_info})

    curr_perms = session.get("permissions", [])
    if session.get("role") != "owner" and "all" not in curr_perms and "parameters" not in curr_perms:
        return jsonify({"success": False, "message": "Owner or Administrator permission required for automated tasks."}), 403

    data = request.get_json() or {}
    for key in ("daily_restart", "daily_backup", "idle_auto_stop"):
        if key in data and isinstance(data[key], dict):
            tasks.setdefault(key, {}).update(data[key])

    # If idle auto stop disabled, clear idle timer
    if not tasks.get("idle_auto_stop", {}).get("enabled"):
        tasks.setdefault("idle_auto_stop", {})["idle_since"] = None

    save_automated_tasks(tasks)
    return jsonify({"success": True, "message": "Automated task schedules updated successfully!", "tasks": tasks})

# ── Quick Actions ────────────────────────────────────────────────────────────
@app.route("/api/action", methods=["POST"])
@login_required
def api_action():
    curr_perms = session.get("permissions", [])
    role = session.get("role", "")
    is_admin = (role in ("owner", "administrator") or "all" in curr_perms)

    data = request.get_json() or {}
    action = data.get("action")
    param = data.get("param", "")

    if action == "restart":
        if not is_admin and "power" not in curr_perms:
            return jsonify({"success": False, "message": "Permission denied: Power permission required."}), 403
        if not server_runner.is_running():
            return jsonify({"success": False, "message": "Server is already stopped. Cannot restart server."}), 400
        def restart_worker():
            time.sleep(1)
            server_runner.restart()
        threading.Thread(target=restart_worker, daemon=True).start()
        return jsonify({"success": True, "message": "Server reboot sequence initiated!"})

    elif action == "save":
        if not is_admin and "parameters" not in curr_perms and "console" not in curr_perms and "backups" not in curr_perms:
            return jsonify({"success": False, "message": "Permission denied: Insufficient privileges."}), 403
        send_mc_command("save-all")
        return jsonify({"success": True, "message": "World data flushed and saved to disk (/save-all)!"})

    elif action in ("time", "weather", "difficulty"):
        if not is_admin and "parameters" not in curr_perms and "console" not in curr_perms:
            return jsonify({"success": False, "message": "Permission denied: Parameter permission required."}), 403
        if action == "time":
            send_mc_command(f"minecraft:time set {param or 'day'}")
            return jsonify({"success": True, "message": f"Time of day changed to {param}!"})
        elif action == "weather":
            send_mc_command(f"minecraft:weather {param or 'clear'}")
            return jsonify({"success": True, "message": f"Weather state set to {param}!"})
        elif action == "difficulty":
            send_mc_command(f"minecraft:difficulty {param or 'normal'}")
            return jsonify({"success": True, "message": f"Game difficulty set to {param}!"})

    elif action == "reload_plugin":
        if not is_admin and "parameters" not in curr_perms and "console" not in curr_perms:
            return jsonify({"success": False, "message": "Permission denied: Parameter or Console permission required."}), 403
        safe_param = re.sub(r'[^a-zA-Z0-9_\-]', '', param or "").strip()
        if not safe_param:
            return jsonify({"success": False, "message": "Plugin name required."}), 400
        if safe_param == "essentials":
            send_mc_command("ess reload")
        elif safe_param == "tab":
            send_mc_command("tab reload")
        elif safe_param == "deluxemenus":
            send_mc_command("dm reload")
        elif safe_param == "skinsrestorer":
            send_mc_command("sr reload")
        elif safe_param == "luckperms":
            send_mc_command("lp reload")
        elif safe_param == "geyser":
            send_mc_command("geyser reload")
        else:
            send_mc_command(f"{safe_param} reload")
        return jsonify({"success": True, "message": f"Reload command dispatched for {safe_param}!"})

    elif action == "gc":
        if not is_admin and "parameters" not in curr_perms and "console" not in curr_perms:
            return jsonify({"success": False, "message": "Permission denied: Parameter or Console permission required."}), 403
        send_mc_command("gc")
        return jsonify({"success": True, "message": "Garbage collector (/gc) triggered in Minecraft engine!"})

    return jsonify({"success": False, "message": "Unknown action"}), 400

# ── FiestaSMP Official Website News Studio & RSS CMS ─────────────────────────

@app.route("/admin/news-studio", methods=["GET"])
@app.route("/studio/news", methods=["GET"])
@login_required
def route_admin_news_studio():
    curr_perms = session.get("permissions", [])
    if session.get("role") not in ("owner", "admin", "administrator") and "all" not in curr_perms and "announcements" not in curr_perms:
        return render_template("login.html", error="Access Denied: Administrator privileges required for News Studio.", branding=load_branding()), 403
    return render_template("admin_news_studio.html", branding=load_branding())

@app.route("/assets/<path:filename>")
def route_website_assets(filename):
    website_assets_dir = os.path.join(website_news.repo_dir, "assets")
    return send_from_directory(website_assets_dir, filename)

@app.route("/server-icon.webp")
def route_server_icon_webp():
    icon_path = os.path.join(website_news.repo_dir, "server-icon.webp")
    if os.path.isfile(icon_path):
        return send_file(icon_path, mimetype="image/webp")
    return send_from_directory(os.path.join(BASE_DIR, "static"), "favicon.ico")

@app.route("/api/news/articles", methods=["GET"])
@login_required
def api_news_list_articles():
    return jsonify({"success": True, "articles": website_news.list_articles()})

@app.route("/api/news/articles", methods=["POST"])
@login_required
def api_news_save_article():
    curr_perms = session.get("permissions", [])
    if session.get("role") not in ("owner", "admin", "administrator") and "all" not in curr_perms and "announcements" not in curr_perms:
        return jsonify({"success": False, "error": "Permission denied"}), 403
    data = request.get_json(silent=True) or {}
    res = website_news.save_article(data)
    return jsonify(res)

@app.route("/api/news/articles/<slug>", methods=["GET"])
@login_required
def api_news_get_article(slug):
    art = website_news.get_article(slug)
    if not art:
        return jsonify({"success": False, "error": f"Article '{slug}' not found"}), 404
    return jsonify({"success": True, "article": art})

@app.route("/api/news/articles/<slug>", methods=["DELETE"])
@login_required
def api_news_delete_article(slug):
    curr_perms = session.get("permissions", [])
    if session.get("role") not in ("owner", "admin", "administrator") and "all" not in curr_perms and "announcements" not in curr_perms:
        return jsonify({"success": False, "error": "Permission denied"}), 403
    res = website_news.delete_article(slug)
    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code

@app.route("/api/news/media", methods=["GET"])
@login_required
def api_news_list_media():
    return jsonify({"success": True, "media": website_news.list_media()})

@app.route("/api/news/upload-media", methods=["POST"])
@login_required
def api_news_upload_media():
    curr_perms = session.get("permissions", [])
    if session.get("role") not in ("owner", "admin", "administrator") and "all" not in curr_perms and "announcements" not in curr_perms:
        return jsonify({"success": False, "error": "Permission denied"}), 403
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400
    file = request.files["file"]
    res = website_news.save_media_file(file)
    return jsonify(res)

@app.route("/api/news/build", methods=["POST"])
@login_required
def api_news_build():
    curr_perms = session.get("permissions", [])
    if session.get("role") not in ("owner", "admin", "administrator") and "all" not in curr_perms and "announcements" not in curr_perms:
        return jsonify({"success": False, "error": "Permission denied"}), 403
    res = website_news.rebuild()
    return jsonify({"success": res["success"], "output": res.get("stdout") or res.get("stderr") or "Hugo build completed.", "build": res})

@app.route("/api/news/publish", methods=["POST"])
@login_required
def api_news_publish():
    curr_perms = session.get("permissions", [])
    if session.get("role") not in ("owner", "admin", "administrator") and "all" not in curr_perms and "announcements" not in curr_perms:
        return jsonify({"success": False, "error": "Permission denied"}), 403
    data = request.get_json(silent=True) or {}
    msg = data.get("message")
    res = website_news.publish(msg)
    return jsonify(res)

@app.route("/api/news/deploy-neocities", methods=["POST"])
@login_required
def api_news_deploy_neocities():
    curr_perms = session.get("permissions", [])
    if session.get("role") not in ("owner", "admin", "administrator") and "all" not in curr_perms and "announcements" not in curr_perms:
        return jsonify({"success": False, "error": "Permission denied"}), 403
    res = website_news.deploy_to_neocities()
    return jsonify(res)

@app.route("/api/news/status", methods=["GET"])
@login_required
def api_news_status():
    return jsonify(website_news.get_status())

@app.route("/install.sh")
def route_install_script():
    install_path = os.path.join(BASE_DIR, "install.sh")
    if os.path.exists(install_path):
        return send_file(install_path, mimetype="text/x-shellscript")
    return "echo 'FiestaPanel: install.sh not found'; exit 1", 404, {"Content-Type": "text/plain"}

@app.route("/install.ps1")
def route_install_ps1():
    install_path = os.path.join(BASE_DIR, "install.ps1")
    if os.path.exists(install_path):
        return send_file(install_path, mimetype="text/plain")
    return "Write-Error 'FiestaPanel: install.ps1 not found'; exit 1", 404, {"Content-Type": "text/plain"}

@app.route("/install.bat")
def route_install_bat():
    install_path = os.path.join(BASE_DIR, "install.bat")
    if os.path.exists(install_path):
        return send_file(install_path, mimetype="text/plain")
    return "@echo off\r\necho FiestaPanel: install.bat not found\r\nexit /b 1", 404, {"Content-Type": "text/plain"}

if __name__ == "__main__":
    h = SERVER_CONFIG.get("host", "127.0.0.1")
    p = SERVER_CONFIG.get("port", 8080)
    app.run(host=h, port=p, debug=False)

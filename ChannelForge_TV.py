"""ChannelForge TV desktop viewer for the public creator directory."""
from __future__ import annotations

import gzip
import http.client
import http.server
import json
import os
import re
import hashlib
import logging
import sqlite3
import time
import shutil
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from linux_runtime import apply_linux_update, can_apply_linux_update, clean_linux_update_cache, restart_linux_app, linux_install_command, linux_release_asset, start_webview
from release_metadata import read_latest_release, version_tuple
from windows_distribution import clean_windows_update_cache, published_hash, verified_download, verified_windows_setup, windows_setup_asset, windows_update_cache

VERSION = "4.5.6"
VERSION_LABEL = f"Alpha Version {VERSION.removesuffix('.0')}"
DIRECTORY = "https://channelforge-directory.fresh-anorak.workers.dev/v1/catalog"
DIRECTORY_FALLBACK = "https://raw.githubusercontent.com/dethkorex/Public_ChannelForge/main/directory/catalog.json"
LOCAL_CREATOR = "http://127.0.0.1:8787"
UPDATE_DIR = (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "public-channelforge" / "updates"
              if sys.platform.startswith("linux") else windows_update_cache())
UPDATE_JOBS: dict[str, dict] = {}
UPDATE_LOCK = threading.RLock()
APP_WINDOW = None
STREAM_ERRORS = (OSError, http.client.HTTPException)
VIEWER_DISCONNECTED = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)
try:
    import urllib3
except ImportError:
    urllib3 = None
STREAM_POOL = None
STREAM_PROXY_POOLS = {}
if urllib3 and hasattr(urllib3.response.HTTPResponse, "read1"):
    STREAM_POOL = urllib3.PoolManager(num_pools=12, maxsize=6)
    STREAM_PROXY_POOLS = {
        protocol: urllib3.ProxyManager(proxy, num_pools=8, maxsize=6)
        for protocol, proxy in urllib.request.getproxies().items()
        if protocol in {"http", "https"} and proxy.startswith(("http://", "https://"))
    }
    STREAM_ERRORS += (urllib3.exceptions.HTTPError,)


def resource(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name


APP_TITLE = "ChannelForge TV"


def github_json() -> dict:
    return read_latest_release(f"ChannelForge-TV/{VERSION}")


def latest_tv_release() -> dict:
    payload = github_json()
    latest = str(payload.get("tag_name", "")).lstrip("v")
    assets = {str(item.get("name", "")): str(item.get("browser_download_url", "")) for item in payload.get("assets", [])}
    return {"current_version": VERSION, "latest_version": latest, "update_available": bool(latest) and version_tuple(latest) > version_tuple(VERSION),
            "preview": bool(latest) and version_tuple(VERSION) > version_tuple(latest),
            "release_url": str(payload.get("html_url", "")), "notes": str(payload.get("body", ""))[:6000], "assets": assets}


def approved_download(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}:
        raise ValueError("The update URL is not an approved GitHub address.")
    return url


GUIDE_CACHE = UPDATE_DIR.parent / "viewer-guide.sqlite3"
GUIDE_CACHE_LOCK = threading.Lock()
MOVIE_PROGRESS_LOCK = threading.Lock()


def movie_progress(change=None):
    """Persist resume history across WebView ports and concurrent viewer windows."""
    path = UPDATE_DIR.parent / "movie-progress.sqlite3"
    if change is None and not path.is_file():
        return []
    with MOVIE_PROGRESS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=2)) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS movies
                         (key TEXT PRIMARY KEY, changed INTEGER, record TEXT)""")
            if change is not None:
                movie = change["movie"]
                url = approved_content_url(movie["stream_url"])
                if movie.get("studio_id"):
                    key = f'{movie["studio_id"]}:{movie.get("id") or urllib.parse.urlsplit(url).path}'
                else:
                    key = url
                resume = float(change["resumeAt"])
                if not 0 < resume <= 14 * 24 * 3600 and not change.get("finished"):
                    raise ValueError("Invalid movie position.")
                changed = int(change["changed"])
                if not 0 < changed <= int(time.time() * 1000) + 60_000:
                    raise ValueError("Invalid progress time.")
                record = None if change.get("finished") else json.dumps({"movie": movie, "resumeAt": resume}, allow_nan=False)
                conn.execute("""INSERT INTO movies VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE
                             SET changed = excluded.changed, record = excluded.record
                             WHERE excluded.changed >= movies.changed""", (key, changed, record))
                conn.execute("DELETE FROM movies WHERE key IN (SELECT key FROM movies ORDER BY changed DESC LIMIT -1 OFFSET 40)")
            return [json.loads(record) for (record,) in conn.execute(
                "SELECT record FROM movies WHERE record IS NOT NULL ORDER BY changed DESC LIMIT 20")]


def guide_snapshot(url: str, body: bytes | None = None) -> bytes | None:
    """Keep bounded last-known public JSON across the viewer's ephemeral HTTP origins."""
    parts = urllib.parse.urlsplit(url)
    directory = url == DIRECTORY
    if not directory and (parts.path != "/api/public/channels" or parts.query != "guide=extended"):
        return None
    key = hashlib.sha256(url.encode()).hexdigest()
    now = int(time.time())
    if body is not None:
        if len(body) > 4 * 1024 * 1024:
            return None
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            return None
        if not isinstance(value, dict) or not isinstance(value.get("creators" if directory else "channels"), list):
            return None
        if directory and value.get("registrations_complete") is False:
            return None
    try:
        with GUIDE_CACHE_LOCK:
            GUIDE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(GUIDE_CACHE, timeout=1)) as conn, conn:
                conn.execute("CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, checked INTEGER, body BLOB)")
                if body is None:
                    row = conn.execute("SELECT body FROM snapshots WHERE id=? AND checked>?", (key, now-7*86400)).fetchone()
                    return row[0] if row else None
                conn.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)", (key, now, body))
                total = 0
                for index, (identifier, size, checked) in enumerate(conn.execute(
                        "SELECT id,length(body),checked FROM snapshots ORDER BY checked DESC,rowid DESC").fetchall()):
                    total += size
                    if total > 16*1024*1024 or index >= 128 or checked <= now-7*86400:
                        conn.execute("DELETE FROM snapshots WHERE id=?", (identifier,))
    except (OSError, sqlite3.Error) as exc:
        logging.warning("Guide snapshot unavailable (%s)", type(exc).__name__)
    return None


def approved_content_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname:
        return url
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == 8787:
        return url
    raise ValueError("ChannelForge TV only opens HTTPS creator media or the local Creator on port 8787.")


@contextmanager
def open_creator_stream(url: str, headers: dict):
    if STREAM_POOL is None:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=35) as response:
            yield response, response.geturl()
        return
    for _redirect in range(5):
        target = urllib.parse.urlsplit(url)
        proxy = None if urllib.request.proxy_bypass(target.hostname) else STREAM_PROXY_POOLS.get(target.scheme)
        pool = proxy or STREAM_POOL
        response = pool.request("GET", url, headers=headers, preload_content=False, redirect=False,
                                timeout=urllib3.Timeout(connect=6, read=25),
                                retries=urllib3.Retry(total=1, connect=1, read=0))
        try:
            location = response.headers.get("Location")
            if response.status in {301, 302, 303, 307, 308} and location:
                if response.headers.get("Content-Length") == "0":
                    response.read()
                url = approved_content_url(urllib.parse.urljoin(url, location))
                continue
            yield response, url
            return
        finally:
            if not response.closed:
                response.close()
            response.release_conn()
    raise ValueError("The creator returned too many stream redirects.")


def local_creator(base=None) -> dict:
    base = base or LOCAL_CREATOR
    headers = {"Accept": "application/json", "User-Agent": f"ChannelForge-TV/{VERSION}"}
    try:
        try:
            request = urllib.request.Request(f"{base}/api/local-creator", headers=headers)
            with urllib.request.urlopen(request, timeout=1) as response:
                status = json.load(response)
                server_version = response.headers.get("Server", "")
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            error.close()
            request = urllib.request.Request(f"{base}/api/status", headers=headers)
            with urllib.request.urlopen(request, timeout=2) as response:
                status = json.load(response)
                server_version = response.headers.get("Server", "")
        count = status.get("channel_count")
        if not isinstance(count, int):
            count = len(status.get("channels") or [])
        config = status.get("config") or {}
        name = (status.get("name") or (config.get("creator_profile") or {}).get("name")
                or (config.get("plex_profile") or {}).get("server_name") or "My ChannelForge Studio")
        installed = re.search(r"Public_ChannelForge/(\d+\.\d+\.\d+)", str(server_version))
        creator_version = str(status.get("version") or (installed.group(1) if installed else ""))
        compatible = bool(re.fullmatch(r"\d+\.\d+\.\d+", creator_version)
                          and tuple(map(int, creator_version.split("."))) >= (3, 5, 19))
    except Exception:
        return {"available": False}
    return {"available": True, "creator": {
        "id": "local-creator", "name": str(name),
        "description": f"{count} local channel{'s' if count != 1 else ''} • Creator connected",
        "guide_url": f"{base}/exports/guide.xml",
        "playlist_url": f"{base}/exports/channels.m3u",
        "status_url": f"{base}/api/public/channels",
        "vault_url": f"{base}/api/public/vault",
        "cinema_supported": compatible,
        "public_id": status.get("creator_id") or config.get("creator_id", ""),
        "public_playlist_url": (str(status.get("public_url") or config.get("public_url")).rstrip("/") + "/exports/channels.m3u"
                                if status.get("public_url") or config.get("public_url") else ""),
        "logo_url": "", "local": True,
    }}


def rewrite_stream_manifest(data: bytes, base_url: str) -> bytes:
    output = []
    for line in data.decode("utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            target = urllib.parse.urljoin(base_url, stripped)
            line = "/stream?url=" + urllib.parse.quote(target, safe="")
        elif 'URI="' in line:
            line = re.sub(r'URI="([^"]+)"', lambda match: 'URI="/stream?url=' + urllib.parse.quote(urllib.parse.urljoin(base_url, match.group(1)), safe="") + '"', line)
        output.append(line)
    return ("\n".join(output) + "\n").encode()


def start_tv_update() -> str:
    job_id = uuid.uuid4().hex
    with UPDATE_LOCK:
        UPDATE_JOBS[job_id] = {"status": "running", "stage": "checking", "downloaded": 0, "total": 0, "percent": 0}

    def report(stage: str, downloaded: int = 0, total: int = 0) -> None:
        with UPDATE_LOCK:
            UPDATE_JOBS[job_id].update({"stage": stage, "downloaded": downloaded, "total": total, "percent": round(downloaded / total * 100, 1) if total else 0})

    def worker() -> None:
        try:
            release = latest_tv_release()
            if not release["update_available"]:
                raise ValueError("ChannelForge TV is already up to date.")
            assets = release["assets"]
            linux = sys.platform.startswith("linux")
            version = release["latest_version"]
            if linux:
                asset_name = linux_release_asset(assets, version, Path(__file__).resolve().parent)
                checksum_name = "SHA256SUMS-Linux.txt"
            else:
                asset_name = windows_setup_asset(assets, "ChannelForge_TV_Setup.exe")
                checksum_name = "SHA256SUMS.txt"
            if asset_name not in assets or checksum_name not in assets:
                raise ValueError("The GitHub release is missing the TV app or its checksum.")
            report("checksum")
            checksum_request = urllib.request.Request(approved_download(assets[checksum_name]), headers={"User-Agent": f"ChannelForge-TV/{VERSION}"})
            with urllib.request.urlopen(checksum_request, timeout=30) as response:
                checksum_text = response.read(1_000_000).decode("utf-8", "replace")
            expected = published_hash(checksum_text, asset_name)

            def download_package(temporary):
                request = urllib.request.Request(approved_download(assets[asset_name]), headers={"User-Agent": f"ChannelForge-TV/{VERSION}"})
                downloaded = 0
                with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                    total = int(response.headers.get("Content-Length", "0") or 0)
                    if total > 200_000_000:
                        raise ValueError("The update file is unexpectedly large.")
                    while chunk := response.read(1024 * 1024):
                        downloaded += len(chunk)
                        if downloaded > 200_000_000:
                            raise ValueError("The update file exceeded the safety limit.")
                        output.write(chunk)
                        report("downloading", downloaded, total)

            if linux:
                destination = verified_download(UPDATE_DIR / asset_name, expected, download_package, report)
                result = {"version": version, "path": str(destination),
                          "command": linux_install_command(destination, Path(__file__).resolve().parent, expected), "sha256": expected,
                          "can_apply": can_apply_linux_update(destination, Path(__file__).resolve().parent)}
            else:
                destination = verified_windows_setup(UPDATE_DIR, version, expected, download_package, report)
                result = {"version": version, "path": str(destination)}
            with UPDATE_LOCK:
                UPDATE_JOBS[job_id].update({"status": "complete", "stage": "complete", "percent": 100, "result": result})
        except Exception as exc:
            with UPDATE_LOCK:
                UPDATE_JOBS[job_id].update({"status": "error", "stage": "error", "error": str(exc)})

    threading.Thread(target=worker, daemon=True, name=f"channel-forge-tv-update-{job_id[:6]}").start()
    return job_id


def launch_tv_update(job_id: str) -> None:
    with UPDATE_LOCK:
        result = UPDATE_JOBS.get(job_id, {}).get("result", {})
    path = Path(str(result.get("path", ""))).resolve()
    if not path.is_file() or path.parent != UPDATE_DIR.resolve():
        raise ValueError("No verified ChannelForge TV update is ready.")
    if sys.platform.startswith("linux"):
        apply_linux_update(path, result["sha256"], Path(__file__).resolve().parent)
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "try-restart", "public-channelforge.service"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        threading.Timer(1.0, restart_linux_app).start()
        return
    command = [str(path)]
    if getattr(sys, "frozen", False):
        command.extend(["--silent-update", "--target-exe", str(Path(sys.executable).resolve()), "--wait-pid", str(os.getpid())])
        try:
            request = urllib.request.Request("http://127.0.0.1:8787/api/update/prepare", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(request, timeout=2).close()
        except Exception:
            pass
    subprocess.Popen(command, cwd=str(path.parent), creationflags=0x08000000 if sys.platform == "win32" else 0)
    if APP_WINDOW is not None:
        threading.Timer(0.8, APP_WINDOW.destroy).start()


HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>ChannelForge TV</title>
<style>*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#2b0752,#060410 43%);color:#fff;font-family:Segoe UI,sans-serif;overflow-x:hidden}header{height:92px;display:flex;align-items:center;padding:0 4vw;border-bottom:2px solid #13e4ff;gap:28px}h1{color:#fff22b;letter-spacing:4px;margin:0}.tag{color:#4fffd0;border:1px solid #19e9ff;padding:5px 10px}.update{margin-left:auto;background:#15112c;color:white;border:1px solid #19e9ff;padding:12px 18px;border-radius:7px}.wrap{padding:28px 4vw}.creators,.live,.vault{display:flex;gap:14px;overflow-x:auto;padding:10px 6px 25px}.creator,.channel{min-width:260px;background:#16132c;border:1px solid #41376a;border-radius:9px;padding:16px;color:white}.creator:focus,.channel:focus{outline:4px solid #fff22b}.vault{min-height:410px;align-items:center;perspective:1000px;gap:0;padding-left:34px}.tape{position:relative;flex:0 0 210px;height:340px;margin-left:-28px;background:#0d0a1c;border:3px solid #14dcf5;border-left-width:15px;padding:10px;box-shadow:16px 10px 18px #000c,5px 0 #44244f inset;transition:transform .22s,filter .22s;color:white;transform:rotateY(-22deg) translateZ(-35px);transform-style:preserve-3d;filter:brightness(.7)}.tape:nth-child(even){transform:rotateY(18deg) translateZ(-25px)}.tape:focus,.tape:hover{outline:none;border-color:#efff1c;transform:rotateY(0) translateY(-10px) translateZ(95px) scale(1.08);filter:brightness(1);z-index:5}.tape img{width:172px;height:255px;object-fit:cover;background:linear-gradient(145deg,#3a2858,#130d24)}.tape b{display:block;padding-top:8px}.pink{color:#ff3eb8}.cyan{color:#28eaff}video{display:none;position:fixed;inset:0;width:100%;height:100%;background:#000;z-index:10}video.playing{display:block}.close{display:none;position:fixed;z-index:11;right:25px;top:20px;padding:12px 18px;background:#111d;color:white;border:1px solid white}.close.show{display:block}.empty{color:#a89abb;padding:25px}.update-panel{position:fixed;z-index:20;right:28px;top:108px;width:390px;padding:20px;border:2px solid #18e7ff;border-radius:12px;background:#0c1028;box-shadow:0 18px 60px #000}.update-panel h3{color:#fff22b;margin-top:0}.update-panel progress{width:100%;height:18px;accent-color:#4fffd0}.update-panel button{margin-top:12px;padding:10px 14px;background:#203659;color:white;border:1px solid #18e7ff;border-radius:6px}.update-panel button.primary{background:#9b3d99;border-color:#ff4dd2}</style></head><body class="viewer-shell">
<style>.categories{display:flex;gap:10px;margin:0 0 12px}.categories span{padding:7px 13px;border:1px solid #ff3eb8;border-radius:999px;color:#8ff;background:#0f1330}.vault-layout{display:grid;grid-template-columns:48% 52%;gap:24px;min-height:440px}.vault{display:flex;flex-direction:column;perspective:900px;gap:7px;height:440px;overflow-y:auto;padding:8px 18px 20px 8px}.tape,.tape:nth-child(even){display:grid;grid-template-columns:78px 1fr auto;grid-template-rows:1fr 1fr;align-items:center;flex:none;width:100%;height:72px;margin:0;padding:6px 12px 6px 5px;border:1px solid #855938;border-left:9px solid #14dcf5;background:linear-gradient(90deg,#392416,#17131a 70%);box-shadow:8px 5px 12px #0009;transform:rotateX(-5deg) translateZ(-15px);filter:brightness(.82);text-align:left}.tape:focus,.tape:hover{transform:rotateX(0) translateZ(65px) scale(1.025);border:3px solid #efff1c;border-left-width:9px;filter:none}.tape img{grid-row:1/3;width:66px;height:56px;object-fit:cover;object-position:20% center;margin-right:12px;border:1px solid #f3c55b}.tape b{padding:0;font-size:17px}.tape small{color:#c8b89f}.tape i{grid-column:3;grid-row:1/3;background:#66ff9e;color:#07130c;padding:7px 10px;font-style:normal;font-weight:800}.selection{height:440px;border:1px solid #12dff9;border-radius:18px;padding:18px;background:linear-gradient(145deg,#11123b,#070a1d);display:grid;grid-template-columns:210px 1fr;gap:22px;box-shadow:0 0 36px #00c8ff18 inset}.selection>img{width:210px;height:315px;object-fit:cover;border:2px solid #18dcf4;border-radius:9px;box-shadow:16px 18px 28px #000b}.selection h2{color:#fff22d;font-size:29px;margin-top:8px}.selection p{line-height:1.55;min-height:115px}.selection button{width:100%;padding:14px;background:#a94d06;color:#fff1a0;border:2px solid #ffa20c;border-radius:7px;font-weight:900}.selection small{display:block;color:#8d829f;margin-top:12px}header{position:sticky;top:0;z-index:8;background:#070817eF;backdrop-filter:blur(18px);box-shadow:0 12px 35px #0008}.brand-lockup{display:flex;align-items:center;gap:13px}.brand-lockup img{width:57px;height:57px;object-fit:contain;filter:drop-shadow(0 0 16px #13dfff66)}.brand-lockup h1{font-size:25px}.brand-lockup small{color:#57edff;letter-spacing:3px}.hero{position:relative;min-height:230px;display:flex;align-items:center;justify-content:space-between;overflow:hidden;margin-bottom:30px;padding:36px 42px;border:1px solid #652e9e;border-radius:22px;background:radial-gradient(circle at 78% 35%,#e12b8d55,transparent 25%),linear-gradient(120deg,#161149,#090a20 58%,#1d0929);box-shadow:0 25px 70px #0008}.hero::after{content:"";position:absolute;right:-70px;width:350px;height:350px;border:45px solid #11dcff1c;border-radius:50%;box-shadow:0 0 0 35px #fd2aa214}.eyebrow{color:#58f4ff;font-weight:900;letter-spacing:3px}.hero h2{font-size:46px;line-height:1.02;margin:10px 0;color:#fff}.hero p{max-width:650px;color:#bdc6dc}.signal-card{position:relative;z-index:1;display:grid;place-items:center;min-width:180px;height:130px;border:1px solid #ff42c8;border-radius:18px;background:#0b0924cc}.signal-card b{font-size:34px;color:#78ff9d}.signal-card span{color:#ffdc3f;letter-spacing:2px}.section-heading{display:flex;justify-content:space-between;align-items:end;margin-top:14px}.section-heading small{color:#8c96b8}.creator,.channel{background:linear-gradient(145deg,#1b1840,#101128);border-color:#403a71;box-shadow:0 12px 30px #0006;transition:.18s transform,.18s border-color}.creator:hover,.creator:focus,.channel:hover,.channel:focus{transform:translateY(-5px);border-color:#45eaff}.wrap{max-width:1600px;margin:auto}@media(max-width:900px){.vault-layout{grid-template-columns:1fr}.selection{display:none}.hero{padding:28px}.hero h2{font-size:34px}.signal-card{display:none}.brand-lockup small,.tag{display:none}}</style><header><div class="brand-lockup"><img src="/app-icon.png" alt=""><div><h1>CHANNELFORGE TV</h1><small>THE COMMUNITY DIAL</small></div></div><span class="tag">REWIND CINEMA</span><button id="update" class="update">__VERSION_LABEL__</button></header><aside id="update-panel" class="update-panel" hidden><h3>CHANNELFORGE TV UPDATE</h3><p id="update-message">Checking GitHub Releases…</p><progress id="update-progress" max="100" value="0"></progress><small id="update-bytes"></small><button id="restart-update" class="primary" hidden>OPEN VERIFIED UPDATE</button><button id="dismiss-update">NOT NOW</button></aside><main class="wrap"><section class="hero"><div><span class="eyebrow">COMMUNITY CHANNELS • ON DEMAND</span><h2>Turn the dial.<br>Find something unforgettable.</h2><p>Jump into live creator channels or browse every connected movie shelf from one cinematic TV interface.</p></div><div class="signal-card"><b>● LIVE</b><span>PUBLIC GUIDE</span></div></section><div class="section-heading"><h2>CREATOR STUDIOS</h2><small>Choose a studio to tune its channels and movies</small></div><div id="creators" class="creators"><span class="empty">Connecting to the public directory…</span></div><div class="section-heading"><h2 class="cyan">LIVE CHANNELS</h2><small>Select a channel to start watching</small></div><div id="live" class="live"></div><div class="section-heading"><h2 class="pink">REWIND CINEMA</h2><small>BROWSE THE MOVIE SHELF</small></div><nav class="categories"><span>CONTINUE WATCHING</span><span>80S BLOCKBUSTERS</span><span>90S ESSENTIALS</span><span>CREATOR PICKS</span></nav><div class="vault-layout"><div id="vault" class="vault"></div><aside id="selection" class="selection"><span class="empty">Choose a movie to see its details.</span></aside></div></main><video id="player" controls autoplay></video><button id="close" class="close">BACK TO REWIND CINEMA</button><script src="/hls.js"></script><script>
const $=s=>document.querySelector(s), esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let hls,vaultItems=[],vaultVisible=[],vaultRenderCount=0,vaultSearch='',vaultFilter='all',vaultGenre='all',vaultRating='all',vaultSort='title',allCreators=[],liveChannels=[],activeStudio='all',guideSearch='',selectedCreator=null,liveMode=false,guideWindowOffset=0,activeStreamUrl='',activeMedia=null,playerMini=false,favoriteChannels=readFavorites('cf-favorite-channels'),favoriteCreators=readFavorites('cf-favorite-creators');
async function creatorResponse(url, scan=false, signal){
    const response=await fetch('/proxy?url='+encodeURIComponent(url)+(scan?'&scan=1':''),{signal});
    if(!response.ok)throw Error(await response.text());
    return response;
}
async function json(url, scan=false, signal){return (await creatorResponse(url,scan,signal)).json()}
async function localJson(path,options={}){let r=await fetch(path,options),value=await r.json();if(!r.ok)throw Error(value.error||'Request failed');return value}
function renderReleaseNotes(container,value){
    container.replaceChildren();
    let list=null;
    for(const line of String(value||'Visit Updates for the newest release.').split(/\r?\n/)){
        if(!line.trim())continue;
        const bullet=/^(?:[-*]|•)\s+/.test(line), heading=/^#{1,6}\s+/.test(line);
        const row=document.createElement(bullet?'li':heading?'h4':'p');
        for(const part of line.replace(/^(?:#{1,6}|[-*]|•)\s+/,'').split(/(\*\*[^*]+\*\*)/)){
            if(part.startsWith('**')&&part.endsWith('**')){
                const highlight=document.createElement('strong');
                highlight.textContent=part.slice(2,-2);
                row.append(highlight);
            }else row.append(part);
        }
        if(bullet){
            if(!list){list=document.createElement('ul');container.append(list)}
            list.append(row);
        }else{
            list=null;
            container.append(row);
        }
    }
}
async function checkUpdate(onLaunch=false){
    const button=$('#update'), statuses=document.querySelectorAll('#home-update-state,#screen-update-status');
    const showStatus=message=>statuses.forEach(element=>element.textContent=message);
    button.title='Checking for updates…';
    showStatus('Checking for updates…');
    try{
        const release=await localJson('/api/update'), version=release.latest_version||release.current_version||'__VERSION__';
        if($('#home-release-version'))$('#home-release-version').textContent='v'+version;
        for(const notes of document.querySelectorAll('#home-changelog,#tv-release-notes')){
            renderReleaseNotes(notes,release.notes);
        }
        showStatus(release.update_available?'A new release is available.':release.preview?'You are running a preview build.':'You are running the newest public release.');
        button.title=release.update_available?'Update v'+version+' available':'System and updates';
        button.classList.toggle('update-available',Boolean(release.update_available));
        if(release.update_available&&(!onLaunch||confirm('ChannelForge TV v'+version+' is available. Download and verify it now?')))downloadUpdate();
    }catch(error){
        button.title='Could not check for updates';
        showStatus('Could not check for an update. Try again in a moment.');
        for(const notes of document.querySelectorAll('#home-changelog,#tv-release-notes')){
            if(notes.textContent.startsWith('Loading'))notes.textContent='';
        }
    }
}
async function downloadUpdate(){
    const panel=$('#update-panel'),message=$('#update-message'),bar=$('#update-progress'),bytes=$('#update-bytes');
    const action=$('#restart-update'),command=$('#update-command');
    panel.hidden=false;message.textContent='Preparing verified GitHub download…';bar.value=0;action.hidden=true;
    command.value='';command.hidden=true;
    try{
        const started=await localJson('/api/update/start',{method:'POST'});panel.dataset.job=started.job_id;
        while(true){
            const job=await localJson('/api/update/progress?id='+encodeURIComponent(started.job_id));
            bar.value=job.percent||0;
            message.textContent={checking:'Checking official release…',checksum:'Downloading checksum…',downloading:'Downloading ChannelForge TV…',verifying:'Verifying SHA-256…',complete:'Update verified and ready ✓',error:'Update failed'}[job.stage]||job.stage;
            bytes.textContent=job.total?Math.round(job.downloaded/1048576)+' MB of '+Math.round(job.total/1048576)+' MB':'';
            if(job.status==='error')throw Error(job.error||'Update failed');
            if(job.status==='complete'){
                panel.dataset.fallback=job.result?.command||'';command.value=job.result?.can_apply?'':panel.dataset.fallback;command.hidden=!command.value;
                action.textContent=command.value?'COPY INSTALL COMMAND':'INSTALL VERIFIED UPDATE';action.hidden=false;
                if(command.value)message.textContent='Run the verified install command in your terminal.';
                return;
            }
            await new Promise(resolve=>setTimeout(resolve,400));
        }
    }catch(error){message.textContent=error.message;bytes.textContent=''}
}
function parsePlaylist(text,creator){
    const channels=[];
    let pending=null;
    for(const raw of text.split(/\r?\n/)){
        const line=raw.trim();
        if(line.startsWith('#EXTINF:')){
            const id=(line.match(/tvg-id="([^"]+)"/)?.[1]||'').replace(/^public-channelforge\./,'');
            pending={id,name:line.slice(line.lastIndexOf(',')+1),number:line.match(/tvg-chno="([^"]+)"/)?.[1]||'',
                logo_url:line.match(/tvg-logo="([^"]+)"/)?.[1]||'',channel_rating:line.match(/tvg-rating="([^"]+)"/)?.[1]||'',
                channel_rating_rank:Number(line.match(/tvg-rating-rank="([1-9])"/)?.[1]||0),programs:[],online:true,state:'standby'};
        }else if(line&&!line.startsWith('#')&&pending){
            const address=creatorMedia(line,creator);
            if(address)channels.push({...pending,stream_url:address});
            pending=null;
        }
    }
    return channels;
}
function programAt(channel,moment=Date.now()){return(channel?.programs||[]).find(program=>Date.parse(program.start)<=moment&&moment<Date.parse(program.stop))}
const GUIDE_STEP=30*60*1000;
const GUIDE_AHEAD_STEPS=24;
const guideTime=new Intl.DateTimeFormat(undefined,{hour:'numeric',minute:'2-digit'});
function formatDuration(milliseconds){
    const seconds=Math.floor(Math.max(0,milliseconds)/1000);
    return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');
}
function tuneGuideChannel(channel){
    play(channel.stream_url,{kind:'live',channel,title:programAt(channel)?.title||channel.name});
}
let featuredGuideChannel=null, featuredGuideTuned=false, featuredGuideKey='';
function renderNowPlaying(channel,now,tuned){
    const preview=$('#live');
    featuredGuideChannel=channel;
    featuredGuideTuned=tuned;
    if(!channel){
        featuredGuideKey='';
        preview.innerHTML='<article class="guide-preview"><span><small>LIVE GUIDE</small><b>No channels to show</b><em>Try another studio or scan for channels.</em></span></article>';
        return;
    }
    const current=programAt(channel,now);
    const artwork=[...new Set([current?.backdrop_url,current?.series_poster_url,current?.poster_url].filter(Boolean))];
    const start=Date.parse(current?.start), stop=Date.parse(current?.stop);
    const duration=stop-start, elapsed=now-start;
    const progress=duration>0?Math.max(0,Math.min(100,100*elapsed/duration)):0;
    const key=JSON.stringify([channel.stream_url,channel.name,channel.creator_name,channel.number,current?.start,current?.stop,current?.title,current?.genres?.[0],current?.content_rating,tuned,artwork]);
    if(key!==featuredGuideKey){
        const tags=[current?.genres?.[0],current?.content_rating].filter(Boolean);
        preview.innerHTML='<article class="guide-preview">'+(artwork.length?'<img class="guide-backdrop" alt="" decoding="async">':'')
            +'<span><small><i class="guide-signal"></i>'+(tuned?'YOU ARE WATCHING':'NOW PLAYING')+'<mark>///</mark></small>'
            +'<b>'+esc(current?.title||channel.name)+'</b><em><span>'+esc(channel.creator_name||'ChannelForge Creator')+'</span><span>CH '+esc(channel.number||'—')+'</span>'
            +tags.map(tag=>'<span class="guide-tag">'+esc(tag)+'</span>').join('')+'</em>'
            +(duration>0?'<div class="guide-progress"><progress max="100" aria-label="Program progress"></progress><div><time></time><time>'+formatDuration(duration)+'</time></div></div>':'')
            +'</span></article>';
        const image=preview.querySelector('.guide-backdrop');
        if(image){
            let index=0;
            image.onerror=()=>{if(++index<artwork.length)image.src=artwork[index];else image.remove()};
            image.src=artwork[index];
        }
        featuredGuideKey=key;
    }
    if(duration>0){
        preview.querySelector('progress').value=progress;
        preview.querySelector('time').textContent=formatDuration(elapsed);
    }
}
setInterval(()=>{
    if(!document.hidden&&featuredGuideChannel&&document.body.classList.contains('guide-mode')){
        renderNowPlaying(featuredGuideChannel,Date.now(),featuredGuideTuned);
    }
},1000);
function channelRatingOrder(channel){
    const rank=channel.channel_rating_rank;
    return Number.isInteger(rank)&&rank>=1&&rank<=9?rank:10;
}
function channelArt(channel,program){
    return program?.backdrop_url||program?.series_poster_url||program?.poster_url||channel.logo_url||'';
}
function programSeries(program){
    return (program?.title||'').split(/\s+·\s+S\d+E\d+\b/i,1)[0];
}
function programProgress(program,now){
    const start=Date.parse(program?.start), duration=Date.parse(program?.stop)-start;
    return duration>0?Math.max(0,Math.min(100,100*(now-start)/duration)):0;
}
function programDetails(program){
    const title=String(program.title||'');
    const episode=title.match(/\bS\d+E\d+\b.*$/i)?.[0];
    if(episode)return episode;
    return [program.year,program.content_rating].filter(Boolean).join(' · ');
}
function programStartTime(program){
    const start=Date.parse(program.start);
    return Number.isFinite(start)?guideTime.format(start):'';
}
function renderCompactGuide(channels,now,moment,tuned){
    const guide=$('#tv-guide');
    const rows=document.createDocumentFragment();
    const focused=document.activeElement?.closest('.compact-guide-row');
    const focusedIndex=focused?[...focused.querySelectorAll('button')].indexOf(document.activeElement):-1;
    for(const [index,channel] of channels.entries()){
        const current=programAt(channel,moment);
        const upcoming=(channel.programs||[]).filter(program=>Date.parse(program.start)>moment);
        const programs=[current,...upcoming.slice(0,7)];
        const columnLabels=[guideWindowOffset?'At selected time':'On Now','Up Next','Later','Later 2','Later 3','Later 4','Later 5','Later 6'];
        const art=channelArt(channel,current), row=document.createElement('section');
        row.className='compact-guide-row'+(tuned?.stream_url===channel.stream_url?' tuned':'');
        row.dataset.channel=channel.stream_url;
        const favorite=favoriteChannels.has(channel.stream_url);
        row.innerHTML='<div class="compact-station"><span class="compact-channel-image">'+(art?'<img src="'+esc(art)+'" alt="" loading="lazy">':'')
            +'</span><span class="compact-station-copy" title="'+esc(channel.name)+'"><b>CH '+esc(channel.number||'—')+'</b><small>'+esc(channel.name)+'</small></span>'
            +'<button type="button" class="compact-favorite" aria-label="Favorite '+esc(channel.name)+'" aria-pressed="'+favorite+'">'+(favorite?'★':'☆')+'</button></div>'
            +columnLabels.map((label,index)=>{const program=programs[index];return program?'<button type="button" class="compact-program" data-program="'+index+'" title="'+esc(program.title)+'"><small>'
                +label+'</small><b>'+esc(programSeries(program))+'</b>'
                +(index===0&&!guideWindowOffset?'<span class="compact-air-time"><time>'+esc(programStartTime(program))+'</time><progress max="100" value="'+programProgress(program,now)+'" aria-label="Program progress"></progress></span>'
                    :'<time>'+esc([programStartTime(program),programDetails(program)].filter(Boolean).join(' · '))+'</time>')+'</button>':'<span class="compact-program"><small>'
                +label+'</small><time>—</time></span>'}).join('')
            +'<div class="compact-actions"><button type="button" aria-label="Watch '+esc(channel.name)+'">▶ Watch Live</button></div>';
        watchArtwork(row.querySelector('img'),channel.logo_url);
        const favoriteButton=row.querySelector('.compact-favorite'),watch=row.querySelector('.compact-actions button');
        watch.onclick=()=>tuneGuideChannel(channel);
        favoriteButton.onclick=()=>toggleFavorite('channel',channel.stream_url);
        row.querySelectorAll('[data-program]').forEach(button=>button.onclick=()=>showProgramInfo(channel,programs[Number(button.dataset.program)]));
        rows.append(row);
    }
    guide.replaceChildren(rows);
    if(focusedIndex>=0){
        [...guide.children].find(row=>row.dataset.channel===focused.dataset.channel)?.querySelectorAll('button')[focusedIndex]?.focus({preventScroll:true});
    }
    if(!channels.length)guide.innerHTML='<p class="guide-unavailable">No online channels match this view.</p>';
}
function renderLiveGuide(){
    const now=Date.now(), search=guideSearch.trim().toLowerCase(), filterBar=$('#studio-filters');
    const studios=[...new Set(liveChannels.map(channel=>channel.creator_name||'ChannelForge Creator'))];
    if(activeStudio!=='all'&&!studios.includes(activeStudio))activeStudio='all';
    filterBar.innerHTML=['all',...studios].map(studio=>'<button type="button" class="'+(activeStudio===studio?'active':'')+'" data-studio="'+esc(studio)+'">'+esc(studio==='all'?'ALL STUDIOS':studio)+'</button>').join('');
    filterBar.querySelectorAll('button').forEach(button=>button.onclick=()=>{
        activeStudio=button.dataset.studio;
        renderLiveGuide();
        [...filterBar.children].find(item=>item.dataset.studio===activeStudio)?.focus();
    });
    const channels=liveChannels.filter(channel=>(activeStudio==='all'||channel.creator_name===activeStudio)
        &&(!search||[channel.name,channel.creator_name,...(channel.programs||[]).map(program=>program.title)].join(' ').toLowerCase().includes(search)));
    const sortByRating=$('#guide-sort').value==='rating';
    if(sortByRating)channels.sort((left,right)=>channelRatingOrder(left)-channelRatingOrder(right));
    const tuned=liveMode&&(liveChannels.find(channel=>channel.stream_url===activeStreamUrl)||activeMedia?.channel);
    const featured=tuned||channels.find(channel=>programAt(channel,now))||channels[0];
    renderNowPlaying(featured,now,Boolean(tuned));
    const moment=guideWindowOffset?now+guideWindowOffset*GUIDE_STEP:now;
    $('#guide-window-label').textContent=guideWindowOffset?guideTime.format(moment):'On now';
    $('#guide-earlier').disabled=guideWindowOffset<=-1;
    $('#guide-later').disabled=guideWindowOffset>=GUIDE_AHEAD_STEPS;
    $('#compact-guide-head span:nth-child(2)').textContent=guideWindowOffset?guideTime.format(moment):'On Now';
    renderCompactGuide(channels,now,moment,tuned);
    renderFavorites();
}
function readFavorites(key){
    try {
        const favorites=JSON.parse(window.localStorage?.getItem(key)||'[]');
        return new Set(Array.isArray(favorites)?favorites:[]);
    } catch (_error) {
        return new Set();
    }
}
function saveFavorites(){
    try {
        window.localStorage?.setItem('cf-favorite-channels',JSON.stringify([...favoriteChannels]));
        window.localStorage?.setItem('cf-favorite-creators',JSON.stringify([...favoriteCreators]));
    } catch (_error) {
        // Keep favorites available in the current session when storage is disabled.
    }
}
function toggleFavorite(kind,key){let set=kind==='channel'?favoriteChannels:favoriteCreators;set.has(key)?set.delete(key):set.add(key);saveFavorites();renderLiveGuide()}
function renderFavorites(){
    const shelf=$('#favorites-list');
    if(!shelf)return;
    const channels=liveChannels.filter(channel=>favoriteChannels.has(channel.stream_url));
    const creators=allCreators.filter(creator=>favoriteCreators.has(creator.name));
    const channelCards=channels.map(channel=>{
        const program=programAt(channel), art=channelArt(channel,program);
        return '<button type="button" class="viewer-card" data-favorite-stream="'+esc(channel.stream_url)+'">'
            +'<span class="home-channel-image">'+(art?'<img src="'+esc(art)+'" alt="" loading="lazy">':'')+'</span>'
            +'<span class="home-channel-copy"><b>'+esc(channel.name)+'</b><small>CH '+esc(channel.number||'—')+' · '
            +esc(programSeries(program)||channel.creator_name||'Watch live')+'</small>'
            +homeProgress(program,Date.now())+'</span></button>';
    });
    const studioCards=creators.map(creator=>'<button type="button" class="viewer-card" data-favorite-creator="'
        +esc(creator.name)+'"><span class="home-channel-copy"><b>'+esc(creator.name)
        +'</b><small>Browse this studio’s channels</small></span></button>');
    shelf.innerHTML=(channelCards.length?channelCards.join(''):'<p class="viewer-empty">Save channels with the star in the guide.</p>')
        +(studioCards.length?'<h3>Favorite studios</h3>'+studioCards.join(''):'');
    shelf.querySelectorAll('img').forEach(image=>watchArtwork(image));
}
function showProgramInfo(channel,program){
    let dialog=$('#program-dialog'),start=new Date(program.start),stop=new Date(program.stop),onNow=start<=new Date()&&new Date()<stop;
    const artwork=[...new Set([program.poster_url,program.series_poster_url,program.backdrop_url,channel.logo_url,
        channel.creator_logo_url,'/app-icon.png'].filter(Boolean))];
    $('#program-dialog-body').innerHTML='<div class="program-dialog-layout"><img class="program-poster" decoding="async" alt="'+esc(program.title)+' artwork"><div><span class="eyebrow">'+(onNow?'● ON NOW':'UPCOMING')+' · CH '+esc(channel.number||'—')+'</span><h2>'+esc(program.title)+'</h2><p class="program-meta">'+start.toLocaleString([], {weekday:'short',hour:'numeric',minute:'2-digit'})+' – '+stop.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})+' · '+esc(channel.name)+'</p><p>'+esc(program.summary||program.description||'Program information supplied by the creator guide.')+'</p><div class="program-actions"><button id="program-watch" class="primary">'+(onNow?'WATCH NOW':'WATCH CHANNEL')+'</button><button id="favorite-channel">'+(favoriteChannels.has(channel.stream_url)?'★ FAVORITED':'☆ FAVORITE CHANNEL')+'</button><button id="favorite-creator">'+(favoriteCreators.has(channel.creator_name)?'★ CREATOR FAVORITED':'☆ FAVORITE CREATOR')+'</button></div></div></div>';
    const image=$('#program-dialog-body .program-poster');
    let imageIndex=0;
    image.onerror=()=>{if(++imageIndex<artwork.length)image.src=artwork[imageIndex];else image.hidden=true};
    image.src=artwork[0];
    $('#program-watch').onclick=()=>{dialog.close();play(channel.stream_url,{kind:'live',channel,title:programAt(channel)?.title||channel.name})};
    $('#favorite-channel').onclick=()=>{toggleFavorite('channel',channel.stream_url);showProgramInfo(channel,program)};
    $('#favorite-creator').onclick=()=>{toggleFavorite('creator',channel.creator_name);showProgramInfo(channel,program)};
    if(!dialog.open)dialog.showModal();
}
let directoryScan=null, pendingGuideRender=0;
const channelsByStudio=new Map();
const unreachableStudioOrigins=new Set();
function refreshGuideFromStudios(){
    const winners=new Map();
    for(const creator of allCreators){
        const updated=Number(creator.updated_at)||0;
        const name=String(creator.name||'').trim().toLowerCase();
        for(const channel of channelsByStudio.get(creator.playlist_url)||[]){
            if(!channel.stream_url||channel.online===false||['offline','reconnecting'].includes(channel.state))continue;
            const key=name&&/^[a-f0-9]{16}$/i.test(channel.id||'')?`channel:${name}/${channel.id}`:channel.stream_url;
            const score=creator.local?1:0;
            const previous=winners.get(key);
            if(!previous||score>previous.score||(score===previous.score&&updated>previous.updated)){
                winners.set(key,{channel,score,updated});
            }
        }
    }
    liveChannels=[...new Map([...winners.values()].map(({channel})=>[channel.stream_url,channel])).values()]
        .sort((a,b)=>String(a.number||'9999').localeCompare(String(b.number||'9999'),undefined,{numeric:true})||a.name.localeCompare(b.name));
    if(!pendingGuideRender)pendingGuideRender=requestAnimationFrame(()=>{
        pendingGuideRender=0;
        if(document.body.classList.contains('guide-mode'))renderLiveGuide();
        else renderFavorites();
        if(!$('#home-spotlight').hidden)renderHome();
    });
}
function sameStudio(local, remote){
    return local.playlist_url===remote.playlist_url||local.public_playlist_url===remote.playlist_url
        ||Boolean(local.public_id&&local.public_id===remote.id);
}
function creatorMedia(url, creator){
    if(!url)return '';
    try{
        const target=new URL(url,creator.status_url||creator.playlist_url);
        if(creator.local&&/^\/(?:api\/(?:(?:plex|local)\/image|public\/(?:program|movie)-art)$|user-assets\/|live\/|vault\/|broadcast\/)/.test(target.pathname)){
            return new URL(target.pathname+target.search,creator.status_url).href;
        }
        return ['http:','https:'].includes(target.protocol)?target.href:'';
    }catch(_error){}
    return '';
}
function preferredMediaAddress(source, media){
    const item=media?.kind==='live'?media.channel:media?.movie;
    if(!item?.studio_id||item.local_studio)return source;
    const creator=allCreators.find(entry=>!entry.local&&(entry.public_id||entry.id||entry.playlist_url)===item.studio_id);
    if(!creator)return source;
    try{
        const registered=new URL(creator.playlist_url);
        const [fallbackHost,relayHost]=__PLAYBACK_RELAY_ROUTE__.split('=');
        const relay=new URL(relayHost&&registered.hostname===fallbackHost?'https://'+relayHost:creator.playback_origin);
        if(source.protocol!=='https:'||source.origin!==registered.origin||relay.protocol!=='https:'
            ||relay.username||relay.password||relay.port||relay.pathname!=='/'||relay.search||relay.hash
            ||!/^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z][a-z0-9-]*$/i.test(relay.hostname))return source;
        return new URL(source.pathname+source.search,relay);
    }catch(_error){return source}
}
function creatorArtwork(url, creator){
    const address=creatorMedia(url,creator);
    return address?'/proxy?url='+encodeURIComponent(address):'';
}
function supportsCinema(creator){
    return Boolean(creator?.vault_url&&creator.cinema_supported===true&&!unreachableStudioOrigins.has(creator.playlist_url));
}
function adoptCreators(creators){
    creators=creators.filter(creator=>creator.state!=='offline'&&(creator.state||creator.online!==false));
    allCreators=creators;
    const cinemaCreators=creators.filter(supportsCinema);
    const current=cinemaCreators.find(creator=>creator.playlist_url===selectedCreator?.playlist_url||creator.id===selectedCreator?.id);
    const next=current||cinemaCreators[0]||null;
    if(next?.vault_url!==selectedCreator?.vault_url){
        vaultController?.abort();
        vaultPaging?.controller?.abort();
        vaultPaging=null;
        loadingMoreMovies=false;
        loadingVaultUrl='';
        selectedVaultUrl='';
        vaultItems=[];
        indexVault(vaultItems);
    }
    selectedCreator=next;
    const urls=new Set(creators.map(creator=>creator.playlist_url));
    for(const key of channelsByStudio.keys())if(!urls.has(key))channelsByStudio.delete(key);
    for(const key of unreachableStudioOrigins)if(!urls.has(key))unreachableStudioOrigins.delete(key);
    updateCinemaStudios();
    if(!selectedCreator?.vault_url){
        $('#vault').innerHTML='<span class="empty">No movie libraries are online right now.</span>';
        $('#selection').textContent='';
    }else if(!document.querySelector('.vault-layout').hidden&&!vaultItems.length)studio(selectedCreator);
    refreshGuideFromStudios();
}
function updateCinemaStudios(){
    const select=$('#cinema-studio-select');
    if(select){
        const options=allCreators.filter(supportsCinema).map(creator=>[creator.name,creator.playlist_url]);
        const rendered=[...select.options].map(option=>[option.text,option.value]);
        if(JSON.stringify(options)!==JSON.stringify(rendered))
            select.replaceChildren(...options.map(([name,url])=>new Option(name,url)));
        select.disabled=!options.length;
        select.hidden=options.length<2;
        select.value=selectedCreator?.playlist_url||'';
    }
}
function selectCinemaStudio(creator){
    if(!supportsCinema(creator))return;
    selectedCreator=creator;
    updateCinemaStudios();
    showScreen('cinema');
    studio(creator,true);
}
async function discoverDirectory(url){
    const controller=new AbortController();
    const deadline=setTimeout(()=>controller.abort(),6500);
    try {
        const payload=await json(url,true,controller.signal);
        if(!Array.isArray(payload.creators))throw Error('Invalid studio directory');
        return payload;
    } finally {
        clearTimeout(deadline);
    }
}
function mergeRegistrations(previous,updates){
    const byId=new Map(previous.map(c=>[c.id||c.playlist_url,c]));
    for(const c of updates)byId.set(c.id||c.playlist_url,c);
    return [...byId.values()];
}
async function publicDirectory(){
    const previous=allCreators.filter(c=>!c.local);
    const cached=()=>localJson('/guide-cache?url='+encodeURIComponent('__DIRECTORY__')).catch(()=>null);
    try {
        const live=await discoverDirectory('__DIRECTORY__');
        if(live.registrations_complete!==false)return live.creators;
        const known=previous.length?previous:(await cached())?.creators||[];
        return mergeRegistrations(known,live.creators);
    } catch (error) {
        if(previous.length)throw error;
        const snapshot=await cached();
        if(snapshot?.creators)return snapshot.creators.filter(c=>c.state!=='offline').map(c=>({...c,state:'reconnecting'}));
        return (await discoverDirectory('__DIRECTORY_FALLBACK__')).creators;
    }
}
async function readStudioGuide(url, asJson){
    const controller=new AbortController();
    const deadline=setTimeout(()=>controller.abort(),6500);
    try {
        const response=await creatorResponse(url,true,controller.signal);
        return await (asJson?response.json():response.text());
    } finally {
        clearTimeout(deadline);
    }
}
function studioGuideRows(creator,value){
    return value.channels.filter(channel=>channel.stream_url&&channel.online!==false&&
        !['offline','reconnecting'].includes(channel.state)&&!((channel.air_until_epoch_ms||Date.parse(channel.air_until))<=Date.now()))
        .map(channel=>({...channel,creator_name:creator.name,studio_id:creator.public_id||creator.id||creator.playlist_url,
            state:channel.state||(channel.online?'broadcasting':'standby'),
            stream_url:creatorMedia(channel.stream_url,creator),
            continuous_url:creatorMedia(channel.continuous_url,creator),
            logo_url:creatorArtwork(channel.logo_url,creator),creator_logo_url:creatorArtwork(creator.logo_url,creator),
            programs:(channel.programs||[]).map(program=>({...program,
                poster_url:creatorArtwork(program.poster_url,creator),
                series_poster_url:creatorArtwork(program.series_poster_url,creator),
                backdrop_url:creatorArtwork(program.backdrop_url,creator)}))
        })).filter(channel=>channel.stream_url);
}
async function scanStudio(creator){
    let channels;
    if(creator.status_url){
        try{
            const address=new URL(creator.status_url);
            address.searchParams.set('guide','extended');
            const value=await readStudioGuide(address.href,true);
            if(Array.isArray(value?.channels))channels=studioGuideRows(creator,value);
        }catch(_error){}
    }
    if(!channels){
        try{
            const playlist=await readStudioGuide(creator.playlist_url,false);
            if(!playlist.trimStart().startsWith('#EXTM3U'))throw Error('Invalid channel playlist');
            const previous=new Map((channelsByStudio.get(creator.playlist_url)||[]).map(row=>[row.id||row.stream_url,row]));
            channels=studioGuideRows(creator,{channels:parsePlaylist(playlist,creator)}).flatMap(row=>{
                const known=previous.get(row.id||row.stream_url);
                if(!known)return [row];
                if((known.air_until_epoch_ms||Date.parse(known.air_until))<=Date.now())return [];
                return [{...row,programs:known.programs,air_until:known.air_until,air_until_epoch_ms:known.air_until_epoch_ms,
                    continuous_url:known.stream_url===row.stream_url?known.continuous_url:row.continuous_url}];
            });
        }catch(_error){}
    }
    if(!creator.local){
        if(channels)unreachableStudioOrigins.delete(creator.playlist_url);
        else unreachableStudioOrigins.add(creator.playlist_url);
    }
    channels??=[];
    if(allCreators.some(c=>c.playlist_url===creator.playlist_url)){
        channelsByStudio.set(creator.playlist_url,channels);
        refreshGuideFromStudios();
    }
    return channels;
}
async function refreshDirectory(){
    const button=$('#scan-channels'), status=$('#scan-status');
    button.disabled=true;
    button.textContent='⌛ SCANNING…';
    status.textContent='Checking worldwide channels…';
    const checked=new Map();
    const check=creator=>{
        if(!checked.has(creator.playlist_url))checked.set(creator.playlist_url,scanStudio(creator));
        return checked.get(creator.playlist_url);
    };
    let local;
    const localLookup=localJson('/api/local-creator').then(value=>{
        local=value.available?value.creator:null;
        if(local){
            adoptCreators([local,...allCreators.filter(c=>!c.local&&!sameStudio(local,c))]);
            check(local);
        }
    }).catch(()=>{});
    let unavailable=false;
    try{
        const remotePromise=publicDirectory().catch(()=>{unavailable=true;return allCreators.filter(c=>!c.local)});
        const remote=await remotePromise;
        adoptCreators([...(local?[local]:[]),...remote.filter(c=>c.playlist_url&&(!local||!sameStudio(local,c)))]);
        refreshGuideFromStudios();
        const pending=allCreators.filter(c=>!c.local);
        let next=0;
        await Promise.all(Array.from({length:Math.min(8,pending.length)},async()=>{
            while(next<pending.length)await check(pending[next++]);
        }));
        await localLookup;
        if(local)await check(local);
        adoptCreators(allCreators);
        refreshGuideFromStudios();
        if(selectedCreator&&!document.querySelector('.vault-layout').hidden&&Date.now()-vaultLoadedAt>60_000)studio(selectedCreator,true);
        const count=liveChannels.length;
        status.textContent=(count+' online channel'+(count===1?'':'s'))+(unavailable?' • directory unavailable':' • scanned '+new Date().toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}));
        return liveChannels;
    }finally{
        button.disabled=false;
        button.textContent='↻ SCAN FOR CHANNELS';
    }
}
async function scanChannels(){
    if(!directoryScan)directoryScan=refreshDirectory().finally(()=>{directoryScan=null});
    return directoryScan;
}
async function load(){return scanChannels()}
let vaultController, loadingVaultUrl='', selectedVaultUrl='', vaultSearchTimer, selectedTape=null, vaultLoadedAt=0;
let vaultPaging, loadingMoreMovies=false, legacyVaultUrl='';
const movieIndex=new WeakMap();
const movieOrders=new Map();
function indexVault(items){
    movieOrders.clear();
    for(const item of items){
        const genres=(item.genres||[]).map(value=>String(value).toLowerCase());
        movieIndex.set(item,{
            genres, year:Number(item.year)||0, rating:String(item.content_rating||'Unrated'),
            search:[item.title,item.year,...genres,item.summary,item.library].join(' ').toLowerCase()
        });
    }
}
function filteredVault(){
    const terms=vaultSearch.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const genre=vaultGenre.toLowerCase();
    const bounds={ '60s':[1960,1970], '70s':[1970,1980], '80s':[1980,1990], '90s':[1990,2000], '2000s':[2000,2010], recent:[2010,Infinity] }[vaultFilter];
    if(!movieOrders.has(vaultSort)){
        const byTitle=(a,b)=>String(a.title).localeCompare(String(b.title));
        const byYear=movie=>movieIndex.get(movie).year;
        const compare=vaultSort==='newest'?(a,b)=>byYear(b)-byYear(a)||byTitle(a,b)
            :vaultSort==='oldest'?(a,b)=>(byYear(a)||9999)-(byYear(b)||9999)||byTitle(a,b):byTitle;
        movieOrders.set(vaultSort,[...vaultItems].sort(compare));
    }
    return movieOrders.get(vaultSort).filter(item=>{
        const {search,genres,year,rating}=movieIndex.get(item);
        return terms.every(term=>search.includes(term))&&(genre==='all'||genres.includes(genre))
            &&(vaultRating==='all'||rating===vaultRating)&&(!bounds||year>=bounds[0]&&year<bounds[1]);
    });
}
function applyVaultFilters(focusFirst=false){
    vaultVisible=Number.isInteger(vaultPaging?.total)?[...vaultItems]:filteredVault();
    const count=vaultPaging?.total??vaultVisible.length;
    $('#movie-count').textContent=count+(vaultPaging?.total==null&&Number.isInteger(vaultPaging?.next)?'+':'')
        +' movie'+(count===1?'':'s');
    document.querySelectorAll('[data-movie-filter]').forEach(button=>button.classList.toggle('active',button.dataset.movieFilter===vaultFilter));
    renderVaultPage(focusFirst);
}
function remoteMovieCatalog(creator){
    if(!creator?.vault_url||creator.local)return false;
    try{return !['127.0.0.1','localhost','[::1]'].includes(new URL(creator.vault_url).hostname)}
    catch(_error){return false}
}
function movieEndpoint(creator,offset){
    if(!remoteMovieCatalog(creator))return creator.vault_url;
    const url=new URL(creator.vault_url);
    for(const [key,value] of Object.entries({limit:24,offset,sort:vaultSort,q:vaultSearch.trim(),
        decade:vaultFilter,genre:vaultGenre==='all'?'':vaultGenre,rating:vaultRating==='all'?'':vaultRating})){
        url.searchParams.set(key,value);
    }
    return url.href;
}
function movieRecords(creator,value){
    const seen=new Set();
    if(!Array.isArray(value.items))return [];
    return value.items.filter(item=>{
        if(item?.type!=='movie')return false;
        const key=movieKey(item);
        if(seen.has(key))return false;
        seen.add(key);return true;
    })
        .map(item=>({...item,studio_id:creator.id||'',local_studio:!!creator.local,poster_url:creatorArtwork(item.poster_url,creator),
            backdrop_url:creatorArtwork(item.backdrop_url,creator),
            thumbnail_url:creatorArtwork(item.thumbnail_url,creator),stream_url:creatorMedia(item.stream_url,creator)}))
        .filter(item=>item.stream_url);
}
function movieKey(movie){
    const title=String(movie.title||'').normalize('NFKC').toLowerCase().match(/[\p{L}\p{N}_]+/gu)?.join(' ');
    return title?title+'\n'+String(movie.year||'').trim():movie.id||movie.stream_url;
}
async function fetchMoviePage(creator,offset,controller){
    const timeout=setTimeout(()=>controller.abort('Movie catalog timed out'),20000);
    try{return await json(movieEndpoint(creator,offset),false,controller.signal)}
    finally{clearTimeout(timeout)}
}
function changeMovieFilters(focusFirst=false){
    if(remoteMovieCatalog(selectedCreator)&&legacyVaultUrl!==selectedCreator.vault_url)studio(selectedCreator,true,true,focusFirst);
    else applyVaultFilters(focusFirst);
}
async function loadMoreMovies(){
    if(loadingMoreMovies||!Number.isInteger(vaultPaging?.next)||activeMedia?.cinema)return;
    const current=vaultPaging, controller=new AbortController();
    current.controller=controller;
    loadingMoreMovies=true;
    const shelf=$('#vault'), note=document.createElement('span');
    note.className='empty';
    note.textContent='Loading more movies…';
    shelf.append(note);
    try{
        const value=await fetchMoviePage(current.creator,current.next,controller);
        if(current!==vaultPaging)return;
        const seen=new Set(vaultItems.map(movieKey));
        const previousItems=vaultItems.length;
        for(const movie of movieRecords(current.creator,value)){
            const id=movieKey(movie);
            if(!seen.has(id)){seen.add(id);vaultItems.push(movie);vaultVisible.push(movie)}
        }
        current.next=value.next_offset;
        if(current.total==null){
            const scroll=shelf.scrollTop;
            indexVault(vaultItems);
            if(vaultItems.length!==previousItems||!shelf.querySelector('.cinema-movie'))applyVaultFilters();
            shelf.scrollTop=scroll;
        }else appendVaultBatch();
        note.remove();
    }catch(_error){
        if(current!==vaultPaging)return;
        const retry=document.createElement('button');
        retry.className='vault-retry';
        retry.textContent='Retry loading more movies';
        retry.onclick=()=>{retry.remove();loadMoreMovies()};
        note.replaceWith(retry);
    }finally{
        if(current===vaultPaging)loadingMoreMovies=false;
        note.remove();
    }
}
function tapeSpine(movie){
    const thumbnail=movie.backdrop_url||movie.thumbnail_url||movie.poster_url;
    const genres=(movie.genres||[]).map(value=>String(value).toLowerCase());
    const paper=genres.some(genre=>['history','war','western','drama'].includes(genre));
    const alternate=genres.some(genre=>['romance','comedy','family','animation'].includes(genre));
    return '<span class="cinema-shelf-case'+(paper?' paper-label':alternate?' violet-sleeve':'')+'" aria-hidden="true">'
        +'<span class="cinema-shelf-art">'+(thumbnail?'<img loading="lazy" decoding="async" src="'+esc(thumbnail)+'" alt="">':'')+'</span>'
        +'<span class="cinema-shelf-brand">CF</span><span class="cinema-shelf-copy"><b>'+esc(movie.title)
        +'</b><small>CHANNELFORGE HOME VIDEO</small></span>'
        +(movie.year?'<span class="cinema-movie-year">'+esc(movie.year)+'</span>':'')
        +'<span class="cinema-shelf-mark">VHS</span></span>';
}
function appendVaultBatch(amount=24){
    const box=$('#vault'), fragment=document.createDocumentFragment(), end=Math.min(vaultVisible.length,vaultRenderCount+amount);
    for(const x of vaultVisible.slice(vaultRenderCount,end)){
        const b=document.createElement('button');
        b.className='cinema-movie';
        b.setAttribute('aria-label','Select '+x.title+(x.year?' ('+x.year+')':''));
        b.innerHTML=tapeSpine(x);
        watchArtwork(b.querySelector('img'),x.poster_url||x.thumbnail_url);
        b.onclick=()=>{
            selectTape(x,b);
            $('#selected-play')?.focus({preventScroll:true});
            if(matchMedia('(max-width:760px)').matches)$('#selection').scrollIntoView({block:'nearest'});
        };
        fragment.append(b);
    }
    box.append(fragment);
    vaultRenderCount=end;
}
function renderVaultPage(focusFirst=false){
    const box=$('#vault');
    box.textContent='';
    box.scrollTop=0;
    vaultRenderCount=0;
    if(!vaultVisible.length){
        const filtered=vaultSearch||vaultFilter!=='all'||vaultGenre!=='all'||vaultRating!=='all';
        box.innerHTML='<span class="empty">'+(filtered?'No movies match these filters.':'No movies are available in this studio.')+'</span>';
        if(Number.isInteger(vaultPaging?.next)){
            const more=document.createElement('button');
            more.type='button';
            more.className='vault-retry';
            more.textContent='Search the next page';
            more.onclick=()=>{more.remove();loadMoreMovies()};
            box.append(more);
        }
        selectedTape=null;
        $('#selection').textContent='';
        return;
    }
    appendVaultBatch();
    selectedTape=null;
    $('#selection').innerHTML='<span class="empty">Select a movie to see its details.</span>';
    if(focusFirst)box.firstElementChild?.focus({preventScroll:true});
}
async function studio(c, force=false, filterChanged=false, focusFirst=false){
    if(!c?.vault_url||(loadingVaultUrl===c.vault_url&&!filterChanged))return;
    const updating=selectedVaultUrl===c.vault_url;
    if(updating&&!filterChanged&&(!force||vaultPaging||Date.now()-vaultLoadedAt<5000))return;
    vaultController?.abort();
    vaultPaging?.controller?.abort();
    vaultPaging=null;
    loadingMoreMovies=false;
    const controller=new AbortController();
    vaultController=controller;
    loadingVaultUrl=c.vault_url;
    if(!updating||filterChanged){
        if(!updating&&!filterChanged){
            vaultSearch='';
            vaultFilter=vaultGenre=vaultRating='all';
            vaultSort='title';
            $('#movie-search').value='';
        }
        selectedVaultUrl='';
        vaultItems=[];
        indexVault(vaultItems);
        selectedTape=null;
        $('#movie-count').textContent='';
        $('#selection').textContent='';
        $('#vault').innerHTML='<span class="empty">Loading movies and posters…</span>';
    }
    try{
        const value=await fetchMoviePage(c,0,controller);
        if(controller.signal.aborted)return;
        vaultItems=movieRecords(c,value);
        if(Number.isInteger(value.total))vaultPaging={creator:c,total:value.total,next:value.next_offset};
        else{
            legacyVaultUrl=c.vault_url;
            if(Number.isInteger(value.next_offset))vaultPaging={creator:c,total:null,next:value.next_offset};
            indexVault(vaultItems);
        }
        selectedVaultUrl=c.vault_url;
        vaultLoadedAt=Date.now();
        const genre=$('#movie-genre'),rating=$('#movie-rating');
        genre.innerHTML='<option value="all">ALL GENRES</option>'+(value.genres||[...new Set(vaultItems.flatMap(item=>item.genres||[]).filter(Boolean))].sort()).map(value=>'<option value="'+esc(value)+'">'+esc(value).toUpperCase()+'</option>').join('');
        rating.innerHTML='<option value="all">ALL RATINGS</option>'+(value.ratings||[...new Set(vaultItems.map(item=>item.content_rating||'Unrated'))].sort()).map(value=>'<option value="'+esc(value)+'">'+esc(value)+'</option>').join('');
        genre.value=vaultGenre;
        rating.value=vaultRating;
        if(!genre.value)genre.value=vaultGenre='all';
        if(!rating.value)rating.value=vaultRating='all';
        $('#movie-sort').value=vaultSort;
        applyVaultFilters(focusFirst);
    }catch(error){
        if(vaultController===controller&&(!controller.signal.aborted||controller.signal.reason)){
            $('#vault').innerHTML='<span class="empty">Could not load the studio movie catalog.</span><button type="button" class="vault-retry">Retry</button>';
            $('#vault .vault-retry').onclick=()=>studio(c,true,true);
        }
    }finally{
        if(vaultController===controller)loadingVaultUrl='';
    }
}
function hideFailedPoster(image,fallback=''){
    if(fallback&&image.getAttribute('src')!==fallback){image.src=fallback;return}
    image.remove();
}
function watchArtwork(image,fallback){
    if(!image)return;
    image.addEventListener('error',()=>hideFailedPoster(image,fallback));
    image.addEventListener('load',()=>image.classList.add('loaded'));
    if(image.complete&&image.naturalWidth>0)image.classList.add('loaded');
}
function cinemaCase(movie,poster){
    const title=esc(movie.title);
    return '<span class="cinema-case" aria-hidden="true">'
        +'<img class="cinema-case-shell" src="/cinema-cassette-case.png" alt="">'
        +'<span class="cinema-case-window">'+(poster?'<img src="'+esc(poster)+'" alt="" decoding="async">':'')+'</span>'
        +'<span class="cinema-case-spine-title">'+title+'</span><span class="cinema-case-title"><b>'+title+'</b></span>'
        +'</span>';
}
function selectTape(movie,tape){
    const previous=$('#vault [aria-current]');
    if(previous!==tape){previous?.removeAttribute('aria-current');tape?.setAttribute('aria-current','true')}
    if(selectedTape===movie&&$('#selected-play'))return;
    selectedTape=movie;
    const duration=Math.round((Number(movie.duration_ms)||0)/60000);
    const runtime=duration?((duration>=60?Math.floor(duration/60)+'h ':'')+(duration%60)+'m'):'';
    const tags=[movie.year,movie.content_rating,...(movie.genres||[]).slice(0,2),runtime].filter(Boolean)
        .map(label=>'<span>'+esc(label)+'</span>').join('');
    $('#selection').innerHTML=cinemaCase(movie,movie.poster_url||movie.thumbnail_url)
        +'<div class="cinema-details"><span class="eyebrow">SELECTED CASSETTE</span><h2>'+esc(movie.title)
        +'</h2><div class="cinema-tags">'+tags+'</div><p>'+esc(movie.summary||'No description supplied.')
        +'</p></div><button id="selected-play">▶ INSERT MOVIE</button>';
    watchArtwork($('#selection .cinema-case-window img'),movie.thumbnail_url);
    $('#selected-play').onclick=()=>playCinemaMovie(movie);
}
const movieHistoryKey='cf-movie-history';
let menuSoundContext, lastMenuSound=0;
function playMenuSound(){
    if(performance.now()-lastMenuSound<65)return;
    const Audio=window.AudioContext||window.webkitAudioContext;
    if(!Audio)return;
    lastMenuSound=performance.now();
    try{
        const context=menuSoundContext||=new Audio();
        const playTone=()=>{
            const now=context.currentTime, tone=context.createOscillator(), gain=context.createGain();
            tone.type='sine';
            tone.frequency.setValueAtTime(660,now);
            tone.frequency.exponentialRampToValueAtTime(420,now+.05);
            gain.gain.setValueAtTime(.0001,now);
            gain.gain.linearRampToValueAtTime(.023,now+.006);
            gain.gain.exponentialRampToValueAtTime(.0001,now+.06);
            tone.connect(gain).connect(context.destination);
            tone.start(now);tone.stop(now+.065);
        };
        if(context.state==='running')playTone();
        else context.resume().then(playTone,()=>{});
    }catch(_error){}
}
document.addEventListener('click',event=>{
    if(event.isTrusted&&event.target.closest('[data-screen], #menu-toggle, .cinema-movie, #selected-play, [data-open-diagnostics]'))playMenuSound();
});
document.addEventListener('keydown',event=>{
    if(!document.body.classList.contains('cinema-mode')||event.repeat&&event.key==='Enter')return;
    const tape=event.target.closest?.('.cinema-movie');
    if(tape&&['ArrowUp','ArrowDown','ArrowRight'].includes(event.key)){
        const next=event.key==='ArrowDown'?tape.nextElementSibling:event.key==='ArrowUp'?tape.previousElementSibling:
            tape.hasAttribute('aria-current')?$('#selected-play'):null;
        event.preventDefault();event.stopImmediatePropagation();
        if(next?.matches('button')){next.focus();playMenuSound();}
    }else if(event.key==='ArrowLeft'&&event.target.id==='selected-play'){
        event.preventDefault();event.stopImmediatePropagation();$('#vault [aria-current]')?.focus();playMenuSound();
    }
},true);
let lastMovieHistoryWrite=0;
let savedMovies=[];
const movieHistoryIdentity=movie=>movie.studio_id
    ? movie.studio_id+':'+(movie.id||new URL(movie.stream_url).pathname)
    : movie.stream_url;
function movieHistory(){
    try{
        const history=JSON.parse(localStorage.getItem(movieHistoryKey)||'[]');
        return savedMovies.length?savedMovies:Array.isArray(history)?history:[];
    }catch(_error){return savedMovies}
}
function rememberMovie(media=activeMedia,force=false){
    if(!media?.cinema||(!force&&Date.now()-lastMovieHistoryWrite<10000))return;
    const movie=media.movie, resumeAt=Number(media.resumeAt)||0;
    if(!movie?.stream_url||(!media.finished&&resumeAt<=0))return;
    const records=movieHistory().filter(record=>record.movie?.stream_url&&movieHistoryIdentity(record.movie)!==movieHistoryIdentity(movie));
    if(!media.finished)records.unshift({movie,resumeAt});
    savedMovies=records.slice(0,20);
    try{
        localStorage.setItem(movieHistoryKey,JSON.stringify(savedMovies));
    }catch(_error){}
    lastMovieHistoryWrite=Date.now();
    fetch('/api/movie-progress',{method:'POST',headers:{'Content-Type':'application/json','X-ChannelForge-Viewer':'movie-progress'},
        keepalive:true,body:JSON.stringify({movie,resumeAt,finished:!!media.finished,changed:lastMovieHistoryWrite})}).catch(()=>{});
}
function resumeCard(movie,resumeAt){
    const card=document.createElement('button');
    const art=movie.backdrop_url||movie.poster_url||movie.thumbnail_url;
    const duration=Number(movie.duration_ms)||0;
    const caption='Resume at '+formatDuration(resumeAt*1000);
    card.type='button';card.className='viewer-card';
    card.innerHTML='<span class="home-channel-image">'+(art?'<img src="'+esc(art)+'" alt="" loading="lazy">':'')+'</span>'
        +'<span class="home-channel-copy"><b>'+esc(movie.title)+'</b><small>'+caption
        +(duration>0?' / '+formatDuration(duration):'')+'</small>'
        +(duration>0?'<progress max="'+duration+'" value="'+Math.min(duration,resumeAt*1000)+'" aria-label="Playback progress"></progress>':'')+'</span>';
    watchArtwork(card.querySelector('img'),movie.poster_url||movie.thumbnail_url);
    card.onclick=()=>showWatchListOptions(movie,resumeAt,card);
    return card;
}
function drawContinueWatching(entries){
    const list=$('#continue-list');
    const visible=entries.filter(entry=>String(entry?.movie?.title||'').trim());
    list.replaceChildren(...visible.map(({movie,resumeAt})=>resumeCard(movie,resumeAt)));
    if(!visible.length)list.innerHTML='<p class="viewer-empty">Movies you start will appear here.</p>';
}
function resumeMovie(movie,resumeAt){
    showScreen('cinema');playCinemaMovie(movie,resumeAt);
}
function showWatchListOptions(movie,resumeAt,tape){
    playMenuSound();
    const dialog=document.createElement('dialog');
    dialog.className='watch-list-dialog';
    dialog.innerHTML='<h2></h2><p></p><div><button type="button">Continue watching</button>'
        +'<button type="button">Remove from watch list</button><button type="button">Cancel</button></div><p role="status"></p>';
    dialog.querySelector('h2').textContent=movie.title;
    dialog.querySelector('p').textContent='Resume at '+formatDuration(resumeAt*1000);
    dialog.setAttribute('aria-label',movie.title);
    const [resume,remove,cancel]=dialog.querySelectorAll('button');
    const status=dialog.querySelector('[role="status"]');
    resume.onclick=()=>{dialog.close();resumeMovie(movie,resumeAt)};
    cancel.onclick=()=>dialog.close();
    remove.onclick=async()=>{
        remove.disabled=true;
        status.textContent='Removing saved progress…';
        try{
            const identifier=movieHistoryIdentity(movie),changed=Date.now();
            await localJson('/api/movie-progress',{method:'POST',
                headers:{'Content-Type':'application/json','X-ChannelForge-Viewer':'movie-progress'},
                body:JSON.stringify({movie,resumeAt:0,finished:true,changed})});
            lastMovieHistoryWrite=changed;
            savedMovies=movieHistory().filter(entry=>entry.movie?.stream_url&&movieHistoryIdentity(entry.movie)!==identifier);
            try{localStorage.setItem(movieHistoryKey,JSON.stringify(savedMovies))}catch(_error){}
            dialog.close();
            await renderContinueWatching();
            $('#continue-list button')?.focus();
        }catch(error){
            status.textContent=error.message;
            remove.disabled=false;
        }
    };
    dialog.addEventListener('close',()=>{dialog.remove();if(tape.isConnected)tape.focus();},{once:true});
    document.body.append(dialog);dialog.showModal();
}
async function renderContinueWatching(){
    const valid=entry=>entry.movie?.stream_url&&Number(entry.resumeAt)>0;
    drawContinueWatching(movieHistory().filter(valid));
    const readSince=lastMovieHistoryWrite;
    try{
        const records=await localJson('/api/movie-progress');
        if(readSince!==lastMovieHistoryWrite)return;
        const local=movieHistory().filter(valid);
        const keys=new Set(records.map(entry=>movieHistoryIdentity(entry.movie)));
        savedMovies=[...records,...local.filter(entry=>!keys.has(movieHistoryIdentity(entry.movie)))].slice(0,20);
        drawContinueWatching(savedMovies);
    }catch(_error){}
}
function saveCurrentMovie(){
    if(!activeMedia?.cinema)return;
    const time=$('#player').readyState>0?$('#player').currentTime:0;
    if(time>0)activeMedia.resumeAt=time;
    rememberMovie(activeMedia,true);
}
window.addEventListener('pagehide',saveCurrentMovie);
function playCinemaMovie(movie,resumeAt=0){
    if(!document.documentElement.classList.contains('application-fullscreen')){
        movieOwnsFullscreen=true;
        setApplicationFullscreen(true);
    }
    const deferDecode=/Linux/i.test(navigator.platform)&&window.Hls?.isSupported();
    lastMovieHistoryWrite=0;
    const media={kind:'movie',title:movie.title,movie,cinema:true,transportPending:true,deferDecode,resumeAt};
    play(movie.stream_url,media);
    animateCinemaTape(true,movie,()=>{
        media.transportPending=false;
        if(activeMedia===media)media.startAfterTransport?.();
    });
}
let cinemaTapeTimer, cinemaTapeStage;
function clearCinemaTape(){
    clearTimeout(cinemaTapeTimer);
    if(cinemaTapeStage?.readyListener)$('#player').removeEventListener(cinemaTapeStage.readyEvent,cinemaTapeStage.readyListener);
    cinemaTapeStage?.remove();
    cinemaTapeStage=null;
}
function animateCinemaTape(inserting,movie,done){
    clearCinemaTape();
    if(matchMedia('(prefers-reduced-motion: reduce)').matches){done();return}
    const stage=document.createElement('div'), title=esc(movie.title);
    const cover=$('#selection .cinema-case-window img.loaded'), thumbnail=$('#vault [aria-current] img');
    const poster=cover?.naturalWidth?cover.currentSrc:
        thumbnail?.naturalWidth?thumbnail.currentSrc:movie.thumbnail_url||movie.poster_url;
    stage.id='cinema-tape-animation';
    stage.className=inserting?'inserting':'ejecting';
    stage.setAttribute('aria-hidden','true');
    stage.innerHTML='<div class="cinema-tape-deck"><div class="cinema-crt"><div class="cinema-glass"><span>Buffering movie…</span></div><i></i></div><span class="cinema-deck-brand">CHANNELFORGE HOME VIDEO</span>'
        +'<div class="cinema-tape-slot"></div><span class="cinema-deck-light"></span></div>'
        +'<div class="cinema-tape-prop"><div class="cinema-tape-face"><div class="cinema-tape-art">'
        +(poster?'<img src="'+esc(poster)+'" alt="">':'')+'</div><span class="cinema-tape-print">'
        +'<small>REWIND CINEMA</small><b>'+title+'</b><em>CF HOME VIDEO · VHS</em></span></div>'
        +'<div class="cinema-tape-front"><b>'+title+'</b><span>VHS</span></div>'
        +'<div class="cinema-tape-side"></div><div class="cinema-tape-side opposite"></div>'
        +'<div class="cinema-tape-front opposite"></div><div class="cinema-tape-base"></div></div>';
    stage.querySelector('img')?.addEventListener('error',event=>event.currentTarget.remove());
    const complete=()=>{
        if(cinemaTapeStage!==stage)return;
        clearCinemaTape();
        done();
    };
    const seated=()=>{
        if(cinemaTapeStage!==stage||stage.classList.contains('seated'))return;
        clearTimeout(cinemaTapeTimer);
        if(!inserting){complete();return}
        stage.classList.add('seated');
        const video=$('#player');
        const lightScreen=()=>{
            if(cinemaTapeStage!==stage||stage.classList.contains('screen-on'))return;
            stage.classList.add('screen-on');
            cinemaTapeTimer=setTimeout(complete,450);
        };
        stage.readyListener=lightScreen;
        stage.readyEvent=activeMedia?.deferDecode?'cinemamanifestready':'canplay';
        video.addEventListener(stage.readyEvent,lightScreen,{once:true});
        if(activeMedia?.deferDecode?activeMedia.manifestReady:video.readyState>=3)lightScreen();
    };
    stage.querySelector('.cinema-tape-prop').addEventListener('animationend',seated,{once:true});
    cinemaTapeStage=stage;
    document.body.append(stage);
    cinemaTapeTimer=setTimeout(seated,inserting?2000:1100);
}
function restoreCinemaWindow(){
    if(!movieOwnsFullscreen)return;
    movieOwnsFullscreen=false;
    setApplicationFullscreen(false);
}
function liveEdge(v){
    if(!liveMode||hls||!v.buffered.length)return;
    const edge=v.buffered.end(v.buffered.length-1);
    if(v.currentTime<edge-10)v.currentTime=edge-2;
}
function setBuffering(active,message='Buffering video…',loading=true){
    const overlay=$('#player-buffer');
    overlay.hidden=!active;
    if(!active)return;
    overlay.querySelector('.buffer-spinner').hidden=!loading;
    const transition=activeMedia?.transition;
    const atBoundary=loading&&transition&&Math.abs(Date.now()-transition.at)<12000;
    overlay.querySelector('b').textContent=atBoundary?'COMING UP NEXT · '+transition.title:message;
    overlay.querySelector('small').textContent=activeMedia?.channel?.name||activeMedia?.title||'ChannelForge TV';
}
function setPlayerState(message,error=false,canRetry=error){
    const state=$('#player-state');
    if(!state)return;
    state.textContent=message;
    state.classList.toggle('error',error);
    if(!error)return;
    if(activeMedia?.cinema&&!activeMedia.ejecting){clearCinemaTape();activeMedia.transportPending=false}
    clearTimeout(playbackTimer);playbackTimer=null;
    setBuffering(true,message,false);
    clearTimeout(playerControlsTimer);
    document.body.classList.remove('player-controls-hidden');
    if(canRetry&&activeStreamUrl){
        const retry=document.createElement('button');
        retry.type='button';
        retry.className='retry-stream';
        retry.textContent='Retry playback';
        retry.onclick=()=>{
            if(!liveMode)activeMedia.resumeAt=$('#player').currentTime||activeMedia.resumeAt||0;
            play(activeStreamUrl,activeMedia);
        };
        state.append(' ',retry);
    }
}
function showChannelOffline(){
    clearTimeout(liveBoundaryTimer);
    setPlayerState('This channel is offline.',true);
}
let playbackGeneration=0, liveRetryTimer, liveBoundaryTimer, liveRetries=0, movieRetries=0, playbackTimer, lastPlaybackTime=-1, playingSince=0;
let usingPreferredRelay=false, skipPreferredRelay=false;
let liveStallStarted=0, recentLiveStalls=[];
const newPlaybackTrace=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,'0')).join('');
async function refreshMovieRoute(movie){
    if(!movie?.studio_id||movie.local_studio)return '';
    let studio=allCreators.find(entry=>!entry.local&&entry.id===movie.studio_id);
    try{
        const directory=await discoverDirectory('__DIRECTORY__');
        studio=directory.creators.find(entry=>entry.id===movie.studio_id)||studio;
    }catch(_error){}
    if(!studio?.vault_url)return '';
    const vault=new URL(studio.vault_url);
    const suffix='/api/public/vault';
    const moviePath=new URL(movie.stream_url).pathname.match(/\/vault\/[a-f0-9]{24}\.m3u8$/i)?.[0];
    if(vault.protocol!=='https:'||!vault.pathname.endsWith(suffix)||!moviePath)return '';
    vault.pathname=vault.pathname.slice(0,-suffix.length)+moviePath;
    vault.search=vault.hash='';
    return vault.href;
}
function recoverMovie(refresh=false){
    if(liveMode||!activeStreamUrl||activeMedia?.ejecting)return false;
    if(fallbackFromPreferredRelay())return true;
    if(liveRetryTimer)return true;
    if(movieRetries>=2)return false;
    ++movieRetries;
    const generation=playbackGeneration;
    activeMedia.resumeAt=$('#player').currentTime||activeMedia.resumeAt||0;
    clearTimeout(playbackTimer);playbackTimer=null;
    setBuffering(true,'Reconnecting to the movie…');
    liveRetryTimer=setTimeout(async()=>{
        if(generation!==playbackGeneration||activeMedia?.ejecting)return;
        const url=(refresh||movieRetries>1)?await refreshMovieRoute(activeMedia.movie).catch(()=> ''):'';
        if(generation!==playbackGeneration||activeMedia?.ejecting)return;
        liveRetryTimer=null;
        if(url&&activeMedia.movie)activeMedia.movie={...activeMedia.movie,stream_url:url};
        play(url||activeStreamUrl,activeMedia,true);
    },1000*movieRetries);
    return true;
}
function armPlaybackDeadline(generation, delay){
    if(playbackTimer)return;
    playbackTimer=setTimeout(()=>{
        playbackTimer=null;
        if(generation!==playbackGeneration||!activeStreamUrl)return;
        if(!liveMode&&recoverMovie())return;
        if(liveMode&&liveRetries<2){
            advanceLive(true,'The studio did not deliver video in time.');
            return;
        }
        ++playbackGeneration;
        clearTimeout(liveRetryTimer);liveRetryTimer=null;
        clearTimeout(liveBoundaryTimer);
        const video=$('#player');
        if(hls){hls.destroy();hls=null}
        video.removeAttribute('src');video.load();
        setPlayerState('The studio did not deliver video in time.',true);
    },delay);
}
function showNativePlayback(){
    setPlayerState('Video decoding is unavailable in this window.',true,false);
    if(!/Linux/i.test(navigator.platform)||!window.pywebview?.api?.play_in_linux_player)return;
    const button=document.createElement('button');
    button.className='retry-stream';
    button.type='button';
    button.textContent='Open in Linux player';
    button.onclick=async()=>{
        button.disabled=true;
        try{
            const result=await window.pywebview.api.play_in_linux_player(activeStreamUrl,liveMode);
            if(result.error){button.disabled=false;button.textContent=result.error}
            else stopPlayer();
        }catch(_error){button.disabled=false;button.textContent='Try Linux player again'}
    };
    $('#player-state').append(' ',button);
}
async function refreshTunedChannel(channel,replacementOnly=false){
    if(!channel?.id)return null;
    const sameCreator=creator=>(creator.public_id||creator.id||creator.playlist_url)===channel.studio_id;
    let creator=allCreators.find(sameCreator);
    if(!creator?.local){
        try{
            const catalog=await discoverDirectory('__DIRECTORY__');
            creator=catalog.creators.find(sameCreator)||creator;
        }catch(_error){}
    }
    if(!creator)return null;
    if(replacementOnly){
        if(!creator.status_url)return null;
        const address=new URL(creator.status_url);
        if(address.origin===new URL(channel.stream_url).origin)return null;
        address.searchParams.set('guide','brief');
        const value=await readStudioGuide(address.href,true);
        const fresh=studioGuideRows(creator,value).find(item=>item.id===channel.id);
        return fresh?{...fresh,programs:channel.programs?.length?channel.programs:fresh.programs}:null;
    }
    return (await scanStudio(creator)).find(item=>item.id===channel.id)||null;
}
function advanceLive(failed=false, failureMessage='COULD NOT PLAY THIS CHANNEL'){
    if(!liveMode||!activeStreamUrl||liveRetryTimer)return;
    if(failed&&fallbackFromPreferredRelay())return;
    clearTimeout(liveBoundaryTimer);
    const generation=playbackGeneration;
    const channel=liveChannels.find(item=>item.stream_url===activeStreamUrl)||activeMedia.channel;
    if(failed)++liveRetries;
    if(failed&&liveRetries>2){setPlayerState(failureMessage,true);return}
    const current=programAt(channel,Date.now());
    const earlyEnd=!failed&&current&&current.start===activeMedia.programStart ? Date.parse(current.stop)-Date.now() : 0;
    setPlayerState(failed?'RECONNECTING…':'TUNING NEXT PROGRAM…');
    setBuffering(true,failed?'Reconnecting to the studio…':'Loading the next program…');
    liveRetryTimer=setTimeout(async()=>{
        if(generation!==playbackGeneration||!liveMode)return;
        let refreshed, deadline;
        if(failed){
            refreshed=await Promise.race([refreshTunedChannel(channel).catch(()=>null),new Promise(done=>{deadline=setTimeout(done,8000)})]);
            clearTimeout(deadline);
        }
        if(generation!==playbackGeneration||!liveMode)return;
        liveRetryTimer=null;
        const updated=refreshed||liveChannels.find(c=>channel?.id && c.id===channel.id && c.studio_id===channel.studio_id)||channel;
        const next=programAt(updated,Date.now());
        play(updated?.stream_url||activeStreamUrl,{kind:'live',channel:updated,title:next?.title||updated?.name||activeMedia.title},true);
    },Math.max(failed?1000*liveRetries:350,earlyEnd+200));
}
function fallbackFromPreferredRelay(){
    if(!usingPreferredRelay)return false;
    skipPreferredRelay=true;
    if(!liveMode)activeMedia.resumeAt=$('#player').currentTime||activeMedia.resumeAt||0;
    play(activeStreamUrl,activeMedia,true);
    return true;
}
function releaseLivePlayback(){
    if(!activeMedia?.liveDeparture)return;
    window.ChannelForgeLiveDeparture?.(activeMedia.liveDeparture,activeMedia.traceId);
    activeMedia.liveDeparture='';
}
addEventListener('pagehide',releaseLivePlayback);
function play(url,media=null,continuing=false){
    if(!continuing)releaseLivePlayback();
    clearTimeout(liveRetryTimer);liveRetryTimer=null;
    clearTimeout(liveBoundaryTimer);
    clearTimeout(playbackTimer);playbackTimer=null;
    const generation=++playbackGeneration,v=$('#player'),shell=ensurePlayerShell();
    const continuousUrl=media?.kind==='live'&&media.channel?.continuous_url;
    if(!continuing)skipPreferredRelay=false;
    const registeredUrl=new URL(continuousUrl||url,location.href);
    const streamUrl=skipPreferredRelay?registeredUrl:preferredMediaAddress(registeredUrl,media);
    usingPreferredRelay=streamUrl.origin!==registeredUrl.origin;
    const traceId=continuing&&activeMedia?.traceId||newPlaybackTrace();
    window.ChannelForgePlaybackStart?.(streamUrl,traceId,continuing,
        usingPreferredRelay?'preferred-relay':skipPreferredRelay?'registered-fallback':'registered-route');
    if(/^(?:\/studio\/[a-f0-9]{16})?\/(broadcast|live|vault)\//.test(streamUrl.pathname))streamUrl.searchParams.set('viewer',traceId);
    const source='/stream?url='+encodeURIComponent(streamUrl.href);
    if(hls){hls.destroy();hls=null}
    lastPlaybackTime=-1;playingSince=0;liveStallStarted=0;recentLiveStalls=[];
    if(!continuing){
        liveRetries=0;movieRetries=0;playerMini=false;setGuideAudio(false);document.body.classList.remove('has-mini-player','player-controls-hidden');
        document.body.append(shell);shell.className='mini-player-shell';v.classList.remove('mini');
    }
    activeStreamUrl=url;activeMedia=media||{kind:/\/live\//.test(url)?'live':'movie',title:'ChannelForge'};
    activeMedia.traceId=traceId;
    liveMode=activeMedia.kind==='live';
    activeMedia.liveDeparture=liveMode?streamUrl.href:'';
    const moviePath=!liveMode&&streamUrl.pathname.match(/^(\/studio\/[a-f0-9]{16})?\/vault\/([a-f0-9]{24})\.m3u8$/);
    activeMedia.activityUrl=moviePath?'/proxy?url='+encodeURIComponent(streamUrl.origin+(moviePath[1]||'')
        +'/api/public/origin?viewing='+moviePath[2]+'&viewer='+traceId):'';
    activeMedia.activityAt=0;
    if(liveMode){
        const current=programAt(activeMedia.channel,Date.now());
        activeMedia.programStart=current?.start;
        activeMedia.title=current?.title||activeMedia.title;
        if(current&&!continuousUrl){
            const remaining=Date.parse(current.stop)-Date.now();
            liveBoundaryTimer=setTimeout(()=>{
                if(generation===playbackGeneration)advanceLive();
            },Math.max(0,remaining)+1700);
        }
    }
    $('#close').hidden=liveMode;
    $('#close').classList.toggle('cinema-eject',!liveMode);
    $('#close').textContent='⏏  Eject movie';
    v.controls=!liveMode;updateTunedIndicator(activeMedia,'CONNECTING…');setBuffering(true,'Connecting to the studio…');showPlayerControls();renderLiveGuide();
    v.autoplay=!activeMedia.transportPending;
    v.classList.add('playing');$('#close').classList.toggle('show',!liveMode);
    v.removeAttribute('src');v.load();
    if(liveMode&&activeMedia.channel?.online===false&&(!activeMedia.channel.state||activeMedia.channel.state==='offline')){showChannelOffline();return}
    armPlaybackDeadline(generation,liveMode?(liveRetries?16000:12000):24000);
    const start=()=>{
        if(generation!==playbackGeneration||activeMedia.ejecting)return;
        if(activeMedia.transportPending){activeMedia.startAfterTransport=start;return}
        v.play().catch(error=>{
            if(generation===playbackGeneration)setPlayerState(error.message||'PRESS PLAY TO START',true);
        });
    };
    if(window.Hls&&Hls.isSupported()){
        const hostname=streamUrl.hostname;
        const local=['127.0.0.1','localhost','[::1]'].includes(hostname);
        const limitedStart=liveRetries>0||hostname.endsWith('.app.lhr.life');
        const liveBuffer=local?{liveSyncDurationCount:2,liveMaxLatencyDurationCount:8,maxBufferLength:8,maxMaxBufferLength:12}
            :{liveSyncDuration:12,liveMaxLatencyDuration:28,maxBufferLength:24,maxMaxBufferLength:35,
              abrEwmaDefaultEstimate:limitedStart?250000:1000000,abrBandWidthUpFactor:0.65,abrBandWidthFactor:0.8};
        const options=liveMode?{startPosition:-1,backBufferLength:2,startFragPrefetch:true,...liveBuffer}
            :{startPosition:activeMedia.resumeAt||0,backBufferLength:120,maxBufferLength:60};
        const player=new Hls({autoStartLoad:false,...options});hls=player;
        {
            const reportTransfer=(kind,stats,response)=>{
                if(generation!==playbackGeneration||!stats?.loading)return;
                const duration=stats.loading.end-stats.loading.start;
                if(Number.isFinite(duration)&&duration>=0){
                    window.ChannelForgePlaybackTransfer?.(kind,duration,stats.loaded||0,response);
                }
            };
            player.on(Hls.Events.FRAG_LOADED,(_event,data)=>reportTransfer('segment',data.frag?.stats,data.networkDetails));
            player.on(Hls.Events.MANIFEST_LOADED,(_event,data)=>reportTransfer('manifest',data.stats,data.networkDetails));
            player.on(Hls.Events.LEVEL_LOADED,(_event,data)=>reportTransfer('manifest',data.stats,data.networkDetails));
        }
        let movieRecovery=false;
        const primeLive=liveMode&&!local;
        const primeMovie=!liveMode&&Boolean(activeMedia.cinema);
        if(primeLive||primeMovie){
            v.autoplay=false;
            let started=false, fallback;
            const begin=()=>{
                if(started||generation!==playbackGeneration||hls!==player)return;
                started=true;clearTimeout(fallback);start();
            };
            player.on(Hls.Events.FRAG_BUFFERED,()=>{
                if(started||!v.buffered.length)return;
                const end=v.buffered.length-1;
                const seconds=v.buffered.end(end)-Math.max(v.currentTime,v.buffered.start(end));
                if(seconds>=3.5)begin();
                else if(!fallback)fallback=setTimeout(begin,2200);
            });
            player.on(Hls.Events.DESTROYING,()=>clearTimeout(fallback));
        }
        player.on(Hls.Events.MANIFEST_PARSED,()=>{
            if(generation!==playbackGeneration)return;
            player.startLevel=local?Math.max(0,player.levels.length-1):liveMode?-1:0;
            applyViewerQuality(player,true);
            const loadMedia=()=>{
                if(generation!==playbackGeneration||activeMedia.ejecting)return;
                if(activeMedia.deferDecode)player.attachMedia(v);
                player.startLoad(liveMode?-1:activeMedia.resumeAt||0);
                if(!primeLive&&!primeMovie)start();
            };
            if(activeMedia.deferDecode&&activeMedia.transportPending){
                // Let WebKitGTK paint the cleared deck before it creates the GStreamer video sink.
                activeMedia.startAfterTransport=()=>requestAnimationFrame(()=>requestAnimationFrame(()=>{
                    if(generation!==playbackGeneration||activeMedia.ejecting)return;
                    loadMedia();
                }));
                activeMedia.manifestReady=true;
                v.dispatchEvent(new Event('cinemamanifestready'));
            }else loadMedia();
        });
        player.on(Hls.Events.ERROR,(_event,data)=>{
            if(generation===playbackGeneration){
                const status=Number(data?.response?.code);
                const kind=data.frag?'segment':/^(manifest|level|audioTrack|subtitleTrack)Load/i.test(data.details||'')?'manifest':'other';
                window.dispatchEvent(new CustomEvent('cf-playback-stream-error',{detail:{status:Number.isInteger(status)?status:0,kind}}));
            }
            if(!data.fatal||generation!==playbackGeneration)return;
            if(liveMode){
                const status=Number(data.response?.code);
                if(data.details===Hls.ErrorDetails.MANIFEST_LOAD_ERROR&&(status===404||status===410)){
                    player.destroy();hls=null;
                    advanceLive(true,'The studio stream is unavailable.');return;
                }
                const stalled=data.details===Hls.ErrorDetails.FRAG_LOAD_TIMEOUT;
                const upstream=status>=500;
                advanceLive(true,upstream?'THE STUDIO COULD NOT START THIS CHANNEL':stalled?'VIDEO DOWNLOAD TIMED OUT · TRY AGAIN SHORTLY':undefined);
                return;
            }
            if(data.type===Hls.ErrorTypes.MEDIA_ERROR&&!movieRecovery){movieRecovery=true;player.recoverMediaError();return}
            const status=Number(data.response?.code||0);
            const retryableNetwork=data.type===Hls.ErrorTypes.NETWORK_ERROR&&(!status||[404,408,410,429].includes(status)||status>=500);
            if((retryableNetwork||data.type===Hls.ErrorTypes.MEDIA_ERROR)&&recoverMovie(status===404||status===410))return;
            setPlayerState('COULD NOT PLAY THIS MOVIE',true);
        });
        player.loadSource(source);if(!activeMedia.deferDecode)player.attachMedia(v);
    }else if(v.canPlayType('application/vnd.apple.mpegurl')){
        if(!liveMode&&activeMedia.resumeAt>0)v.addEventListener('loadedmetadata',()=>{
            if(generation===playbackGeneration)v.currentTime=activeMedia.resumeAt;
        },{once:true});
        v.src=source;start();
    }
    else showNativePlayback();
}
let volumeBeforeGuide=null;
function setGuideAudio(quiet){
    const video=$('#player');
    if(quiet&&volumeBeforeGuide===null){
        volumeBeforeGuide=video.volume;
        video.volume=volumeBeforeGuide*0.35;
    }else if(!quiet&&volumeBeforeGuide!==null){
        video.volume=volumeBeforeGuide;
        volumeBeforeGuide=null;
    }
}
function minimizePlayer(){
    if(!liveMode)return stopPlayer();
    playerMini=true;setGuideAudio(true);$('#player').classList.add('mini');
    document.body.classList.add('has-mini-player');
    showScreen('live');dockMiniPlayer();setPlayerState('MINI PLAYER · BROWSE THE GUIDE');
}
function stopPlayer(){
    window.dispatchEvent(new Event('cf-playback-stop'));
    releaseLivePlayback();
    saveCurrentMovie();
    playbackGeneration++;
    clearTimeout(liveRetryTimer);liveRetryTimer=null;
    clearTimeout(liveBoundaryTimer);
    clearTimeout(playbackTimer);playbackTimer=null;
    clearCinemaTape();
    restoreCinemaWindow();
    const shell=ensurePlayerShell();
    document.body.append(shell);shell.className='mini-player-shell';
    document.body.classList.remove('player-controls-hidden');
    liveMode=false;playerMini=false;setGuideAudio(false);activeStreamUrl='';activeMedia=null;
    const v=$('#player');v.classList.remove('playing','mini');v.pause();
    if(hls){hls.destroy();hls=null}
    v.removeAttribute('src');v.load();setBuffering(false);
    document.body.classList.remove('has-mini-player');$('#close').classList.remove('show');
    updateTunedIndicator(null);renderLiveGuide();
}
{
    const video=$('#player');
    video.addEventListener('seeking',()=>liveEdge(video));
    video.addEventListener('seeked',()=>{
        lastPlaybackTime=-1;
        playingSince=0;
    });
    video.addEventListener('timeupdate',()=>{
        if(video!==$('#player')||!activeStreamUrl||video.paused||video.seeking||video.currentTime<=lastPlaybackTime)return;
        const progressed=lastPlaybackTime>=0;
        lastPlaybackTime=video.currentTime;
        const now=performance.now();
        if(liveMode&&liveStallStarted){
            const stalledFor=now-liveStallStarted;
            liveStallStarted=0;
            if(stalledFor>=2000){
                recentLiveStalls=recentLiveStalls.filter(end=>now-end<30000);
                recentLiveStalls.push(now);
                if(recentLiveStalls.length>=3){advanceLive(true,'Live TV keeps buffering. Retry playback.');return}
            }
        }
        if(!playingSince)playingSince=now;
        else if(now-playingSince>=30000){
            if(liveMode)liveRetries=0;
            else movieRetries=0;
        }
        if(!liveMode){
            activeMedia.resumeAt=video.currentTime;
            if(activeMedia.cinema)rememberMovie();
            if(activeMedia.activityUrl&&now>=activeMedia.activityAt){
                activeMedia.activityAt=now+15000;
                fetch(activeMedia.activityUrl).catch(()=>{});
            }
        }
        if(liveMode&&progressed){
            const channel=liveChannels.find(channel=>channel.stream_url===activeStreamUrl)||activeMedia.channel;
            const program=programAt(channel);
            if(channel?.continuous_url&&program&&program.start!==activeMedia.programStart){
                activeMedia.programStart=program.start;activeMedia.title=program.title;activeMedia.nextAnnounced=false;
                updateTunedIndicator(activeMedia,'LIVE');
            }
            const duration=Date.parse(program?.stop)-Date.parse(program?.start);
            const left=Date.parse(program?.stop)-Date.now();
            const next=channel?.programs?.[channel.programs.indexOf(program)+1];
            if(!activeMedia.nextAnnounced&&program&&program.start===activeMedia.programStart&&next&&left<=Math.min(45000,duration/3)){
                activeMedia.nextAnnounced=true;
                activeMedia.transition={title:next.title,at:Date.parse(program.stop)};
                activeMedia.channel=channel;
                updateTunedIndicator(activeMedia,'LIVE');
                showPlayerControls();
            }
        }
        if(progressed&&playbackTimer){
            clearTimeout(playbackTimer);playbackTimer=null;
            setBuffering(false);showPlayerControls();
            setPlayerState(liveMode?'● LIVE · NO PAUSE OR REWIND':'PLAYING');
        }
    });
    video.addEventListener('ended',()=>{
        if(video!==$('#player'))return;
        if(liveMode&&Date.parse(activeMedia?.channel?.air_until)<=Date.now()+1000){
            clearTimeout(liveBoundaryTimer);clearTimeout(playbackTimer);playbackTimer=null;
            setBuffering(false);setPlayerState('Broadcast finished. Return to the guide.',true,false);
        }else if(liveMode)advanceLive(Boolean(activeMedia?.channel?.continuous_url));
        else if(activeMedia?.cinema){
            activeMedia.finished=true;
            closeVideoView();
        }
    });
    video.addEventListener('error',()=>{
        if(video!==$('#player')||!activeStreamUrl)return;
        if(video.error?.code===3&&/Linux/i.test(navigator.platform)){
            if(hls){hls.destroy();hls=null}
            showNativePlayback();
        }else if(!hls){
            if(liveMode)advanceLive(true);
            else if(video.error?.code!==2||!recoverMovie())setPlayerState('COULD NOT PLAY THIS MOVIE',true);
        }
    });
    video.addEventListener('waiting',()=>{
        if(video!==$('#player')||!activeStreamUrl||(!hls&&!video.hasAttribute('src')))return;
        if(liveMode&&lastPlaybackTime>=0&&!liveStallStarted)liveStallStarted=performance.now();
        playingSince=0;
        setPlayerState('BUFFERING…');setBuffering(true);armPlaybackDeadline(playbackGeneration,liveMode?(liveRetries?16000:10000):20000);
    });
    video.addEventListener('pause',()=>{
        playingSince=0;
        if(video===$('#player')&&liveMode&&!video.ended&&video.readyState>=2&&video.classList.contains('playing'))video.play().catch(()=>{});
    });
}
$('#scan-channels').onclick=()=>scanChannels();
$('#guide-search').oninput=event=>{guideSearch=event.target.value;renderLiveGuide()};
$('#guide-sort').onchange=renderLiveGuide;
$('#guide-earlier').onclick=()=>{guideWindowOffset=Math.max(-1,guideWindowOffset-1);renderLiveGuide()};
$('#guide-now').onclick=()=>{guideWindowOffset=0;renderLiveGuide()};
$('#guide-later').onclick=()=>{guideWindowOffset+=1;renderLiveGuide()};
$('#movie-search').oninput=event=>{vaultSearch=event.target.value;clearTimeout(vaultSearchTimer);vaultSearchTimer=setTimeout(changeMovieFilters,250)};
$('#movie-genre').onchange=event=>{vaultGenre=event.target.value;changeMovieFilters(true)};
$('#movie-rating').onchange=event=>{vaultRating=event.target.value;changeMovieFilters(true)};
$('#movie-sort').onchange=event=>{vaultSort=event.target.value;changeMovieFilters(true)};
document.querySelectorAll('[data-movie-filter]').forEach(button=>button.onclick=()=>{vaultFilter=button.dataset.movieFilter;changeMovieFilters(true)});
$('#vault').addEventListener('scroll',event=>{
    const shelf=event.currentTarget;
    if(shelf.scrollTop+shelf.clientHeight<shelf.scrollHeight-360)return;
    if(vaultRenderCount<vaultVisible.length)appendVaultBatch();
    else if(!shelf.querySelector('.vault-retry'))loadMoreMovies();
},{passive:true});
setInterval(()=>{if(!document.hidden)scanChannels()},60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)scanChannels()});
$('#close').onclick=closeVideoView;
$('#cinema-studio-select').onchange=event=>selectCinemaStudio(allCreators.find(creator=>creator.playlist_url===event.target.value));
let movieOwnsFullscreen=false, fullscreenRequest=Promise.resolve();
function reflectApplicationFullscreen(active){
    const button=$('#desktop-fullscreen');
    document.documentElement.classList.toggle('application-fullscreen',active);
    button.setAttribute('aria-pressed',String(active));
    button.title=active?'Exit fullscreen (F11)':'Enter fullscreen (F11)';
    button.setAttribute('aria-label',button.title);
}
async function setApplicationFullscreen(active){
    try{
        if(window.pywebview?.api?.set_tv_fullscreen){
            fullscreenRequest=fullscreenRequest.catch(()=>{}).then(async()=>{
                reflectApplicationFullscreen(await window.pywebview.api.set_tv_fullscreen(active));
            });
            await fullscreenRequest;
        }else if(!active&&document.fullscreenElement)await document.exitFullscreen?.();
        else if(active&&!document.fullscreenElement)await document.documentElement.requestFullscreen?.();
    }catch(_error){}
}
function toggleApplicationFullscreen(){
    movieOwnsFullscreen=false;
    setApplicationFullscreen(!document.documentElement.classList.contains('application-fullscreen'));
}
window.addEventListener('pywebviewready',async()=>{
    const fullscreen=window.pywebview?.api?.tv_fullscreen;
    if(fullscreen)reflectApplicationFullscreen(await fullscreen());
});
document.addEventListener('fullscreenchange',()=>reflectApplicationFullscreen(Boolean(document.fullscreenElement)));
$('#desktop-fullscreen').onclick=toggleApplicationFullscreen;
$('#home-display').onclick=toggleApplicationFullscreen;
document.addEventListener('keydown',event=>{
    if(event.key==='F11'&&!event.repeat){event.preventDefault();toggleApplicationFullscreen()}
});
$('#update').onclick=()=>checkUpdate(false);
$('#dismiss-update').onclick=()=>$('#update-panel').hidden=true;
$('#restart-update').onclick=async()=>{
    const command=$('#update-command'),message=$('#update-message');
    if(command.value){
        try{await navigator.clipboard.writeText(command.value);message.textContent='Install command copied.'}
        catch(_error){command.focus();command.select();message.textContent='Press Ctrl+C to copy the selected command.'}
        return;
    }
    $('#restart-update').disabled=true;
    message.textContent='Installing verified update…';
    try{
        await localJson('/api/update/launch?id='+encodeURIComponent($('#update-panel').dataset.job),{method:'POST'});
        message.textContent='Opening the verified update…';
    }catch(error){
        message.textContent=error.message;
        $('#restart-update').disabled=false;
        const fallback=$('#update-panel').dataset.fallback;
        if(fallback){command.value=fallback;command.hidden=false;$('#restart-update').textContent='COPY INSTALL COMMAND'}
    }
};
load();checkUpdate(true);</script></body></html>'''.replace("__VERSION__", VERSION).replace("__VERSION_LABEL__", VERSION_LABEL).replace("__DIRECTORY__", DIRECTORY).replace("__DIRECTORY_FALLBACK__", DIRECTORY_FALLBACK)

def configured_playback_relay() -> str:
    path = resource("assets/playback-relay.json")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    route = config.get("relay_route") if isinstance(config, dict) else None
    if isinstance(route, str) and re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+=[a-z0-9-]+(?:\.[a-z0-9-]+)+", route):
        return route
    return ""


HTML = HTML.replace("__PLAYBACK_RELAY_ROUTE__", json.dumps(configured_playback_relay()))


SIDEBAR_CSS = r'''.hamburger{margin:0;background:#11132f;border:1px solid #20e4ff;color:#fff;font-size:24px;line-height:1;padding:12px 14px;border-radius:10px}.app-sidebar{position:fixed;z-index:7;left:0;top:92px;bottom:0;width:235px;padding:24px 14px;background:#090a1def;border-right:1px solid #1eddfb55;backdrop-filter:blur(18px);transition:.2s width,.2s transform}.app-sidebar nav{display:grid;gap:8px}.app-sidebar nav>a{display:grid;grid-template-columns:32px 1fr;align-items:center;text-align:left;width:100%;padding:14px 12px;border:1px solid transparent;border-radius:10px;background:transparent;color:#c3cbe2;font-weight:800;text-decoration:none}.app-sidebar nav>a:hover,.app-sidebar nav>a:focus,.app-sidebar nav>a.active{background:#1a1741;border-color:#ff3eb8;color:#fff}.app-sidebar nav>a i{font-style:normal;font-size:20px;text-align:center}.app-sidebar .menu-label{white-space:nowrap;overflow:hidden}.sidebar-footer{position:absolute;left:18px;right:18px;bottom:22px;color:#7e89a8;font-size:11px}.wrap{margin-left:235px;transition:.2s margin-left}.menu-collapsed .app-sidebar{width:78px}.menu-collapsed .app-sidebar .menu-label,.menu-collapsed .sidebar-footer{display:none}.menu-collapsed .wrap{margin-left:78px}[data-section-block][hidden]{display:none!important}.section-placeholder{min-height:360px;display:grid;place-content:center;text-align:center;border:1px dashed #3c3a6d;border-radius:20px;background:#10112688;color:#9ca6c4;padding:35px}.section-placeholder b{font-size:25px;color:#fff}.section-placeholder span{max-width:480px;margin:9px auto}.screen-title h2{font-size:35px;margin-bottom:4px}.screen-title p{color:#929dbc}@media(max-width:900px){.app-sidebar{transform:translateX(-100%);width:235px}.menu-open .app-sidebar{transform:none}.wrap,.menu-collapsed .wrap{margin-left:0}.menu-collapsed .app-sidebar{width:235px}.menu-collapsed .app-sidebar .menu-label,.menu-collapsed .sidebar-footer{display:block}}'''
GUIDE_CSS = r'''.guide-toolbar{display:flex;align-items:center;gap:16px;margin:8px 0 18px;padding:16px 18px;border:1px solid #2c3e72;border-radius:13px;background:#10142c}.guide-toolbar button{padding:12px 18px;border:1px solid #22e7ff;border-radius:8px;background:#152748;color:#fff;font-weight:900}.guide-toolbar button:disabled{opacity:.6}.guide-toolbar span{color:#9ea9c7}.live{padding-bottom:14px}.channel{display:flex;align-items:center;gap:12px;text-align:left}.channel img{width:48px;height:48px;object-fit:contain}.channel span:last-child{display:grid;gap:5px}.channel small{color:#9da8c5}.channel-number{display:grid;place-items:center;width:48px;height:48px;border-radius:12px;background:linear-gradient(145deg,#38e3f0,#8c59ff);color:#07101c;font-size:19px;font-weight:900}.tv-guide{display:grid;gap:7px;overflow-x:auto;padding:4px 0 38px}.guide-unavailable{display:grid;place-items:center;padding:20px;color:#7f89a6}@media(max-width:900px){.guide-toolbar{align-items:flex-start;flex-direction:column}}'''
GUIDE_STAGE_CSS = r'''.guide-mode .wrap{padding-top:14px}.guide-mode .section-heading[data-section-block="live"]{display:none!important}.live{display:block;overflow:visible;padding:0}.guide-preview{display:grid;grid-template-columns:230px 1fr;width:100%;min-height:165px;margin:0 0 12px;padding:13px;border:1px solid #15def5;border-radius:14px;background:radial-gradient(circle at 88% 20%,#d42e9a2c,transparent 24%),linear-gradient(120deg,#111741,#090d24);color:#fff;text-align:left;box-shadow:0 18px 42px #0006}.guide-preview>img,.guide-preview>strong{width:205px;height:135px;object-fit:contain;border:2px solid #ff3eb8;background:linear-gradient(160deg,#63206d,#13213d)}.guide-preview>strong{display:grid;place-items:center;color:#fff;font-size:54px}.guide-preview>span{display:grid;align-content:center;gap:9px;padding:0 20px}.guide-preview small{color:#4fe9ff;font:bold 12px Consolas,monospace;letter-spacing:1px}.guide-preview b{color:#fff52d;font-size:27px}.guide-preview em{color:#aab8d5;font:normal 14px Consolas,monospace}.guide-preview i{display:block;width:min(650px,90%);height:7px;border:1px solid #766f76;border-radius:8px;background:#403e44;overflow:hidden}.guide-preview u{display:block;height:100%;background:#6dffad;text-decoration:none}.guide-toolbar{justify-content:flex-start;margin-bottom:12px;padding:12px 14px}.guide-toolbar label{display:flex;align-items:center;gap:8px;margin-left:auto;padding:8px 12px;border:1px solid #22e7ff;border-radius:8px;background:#0a1024}.guide-toolbar input{width:190px;border:0;outline:0;background:transparent;color:#fff}.studio-filters{display:flex;gap:10px;overflow:hidden;margin:0 0 8px;padding:0 0 10px;border-bottom:1px solid #ff3eb844}.studio-filters button{padding:9px 15px;border:1px solid #25dcf6;border-radius:7px;background:#101630;color:#75efff;font-weight:800}.studio-filters button.active{border-color:#fff32d;background:#39320a;color:#fff59a}.tv-guide{gap:5px;overflow-x:hidden;padding-bottom:8px;scrollbar-width:none}.tv-guide::-webkit-scrollbar{display:none}@media(max-width:900px){.guide-preview{grid-template-columns:1fr}.guide-preview>img,.guide-preview>strong{width:100%}.guide-toolbar label{margin-left:0}.studio-filters{overflow-x:auto}}'''
HOME_CSS = r'''.home-panels{display:grid;grid-template-columns:minmax(280px,.8fr) minmax(420px,1.2fr);gap:22px;margin-top:24px}.home-card{min-height:255px;padding:28px;border:1px solid #393275;border-radius:18px;background:linear-gradient(145deg,#121536,#0a0b20);box-shadow:0 20px 55px #0006}.home-card h3{margin:8px 0 12px;font-size:26px;color:#fff}.home-card p{color:#aeb8d2;line-height:1.65}.release-heading{display:flex;align-items:center;justify-content:space-between;gap:20px}.release-heading strong{padding:7px 11px;border:1px solid #22e7ff;border-radius:8px;color:#72f8d7}.home-changelog{max-height:210px;overflow:auto;white-space:pre-wrap;color:#c8d1e8;line-height:1.55;font:14px/1.55 Segoe UI,sans-serif}@media(max-width:900px){.home-panels{grid-template-columns:1fr}}'''
VAULT_CSS = r'''body,.wrap{max-width:100%;overflow-x:hidden}.categories{flex-wrap:wrap}.vault-layout{grid-template-columns:minmax(560px,1.18fr) minmax(420px,.82fr);gap:26px;min-height:620px}.vault{height:calc(100vh - 285px);min-height:620px;max-height:780px;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;padding:10px 14px 30px 5px;scrollbar-width:thin;scrollbar-color:#ff43bc #11132c;scroll-snap-type:y proximity}.vault::-webkit-scrollbar{width:10px}.vault::-webkit-scrollbar-track{background:#11132c;border-radius:10px}.vault::-webkit-scrollbar-thumb{background:linear-gradient(#15e4f5,#ff43bc);border:2px solid #11132c;border-radius:10px}.tape,.tape:nth-child(even){position:relative;display:grid;grid-template-columns:12px 88px minmax(0,1fr) auto;grid-template-rows:1fr 1fr;align-items:center;flex:none;width:100%;height:82px;margin:0;padding:5px 12px 5px 0;border:1px solid #735136;border-left:0;border-radius:4px;background:linear-gradient(180deg,#38251b 0 12%,#18161a 13% 86%,#4b2c1d 87%);box-shadow:10px 8px 16px #0009,inset 0 1px #d9a66b55;transform:perspective(900px) rotateX(-3deg);filter:brightness(.86);text-align:left;scroll-snap-align:start;transition:.16s transform,.16s filter,.16s border-color}.tape::before,.tape::after{content:"";position:absolute;right:112px;width:14px;height:14px;border:2px solid #756b62;border-radius:50%;background:#09090c;box-shadow:inset 0 0 0 3px #242128}.tape::before{top:12px}.tape::after{bottom:12px}.tape:hover,.tape:focus{z-index:2;transform:perspective(900px) rotateX(0) translateX(6px) scale(.995);filter:none;border-color:#efff1c;box-shadow:0 0 0 2px #efff1c,14px 10px 22px #000b}.tape-ridge{grid-row:1/3;width:12px;height:100%;background:repeating-linear-gradient(90deg,#13dbea 0 2px,#066a7c 2px 4px)}.tape img{grid-column:2;grid-row:1/3;width:78px;height:68px;margin:0 10px 0 5px;object-fit:cover;object-position:center;border:1px solid #f3c55b;background:#0a0b12}.tape-label{grid-column:3;grid-row:1/3;display:grid;align-content:center;min-width:0;gap:7px;padding:8px 70px 8px 12px;border:1px solid #886944;background:linear-gradient(90deg,#e4d9bf,#bfb095);color:#171316;box-shadow:inset 0 0 18px #745b3d55}.tape-label b{overflow:hidden;padding:0;font-size:17px;text-overflow:ellipsis;white-space:nowrap}.tape-label small{color:#604c3d;font-family:Consolas,monospace}.tape i{grid-column:4;grid-row:1/3;min-width:94px;padding:9px 11px;border:1px solid #7bffad;background:#4fe88b;color:#07130c;font-style:normal;font-weight:900;text-align:center}.selection{position:sticky;top:112px;height:calc(100vh - 285px);min-height:620px;max-height:780px;grid-template-columns:minmax(190px,240px) 1fr;overflow:hidden;padding:22px}.vhs-box{position:relative;align-self:start;padding:8px 8px 34px;border:2px solid #12dff9;border-left:18px solid #d52b81;border-radius:7px;background:#080b18;box-shadow:18px 18px 30px #000b;transform:perspective(850px) rotateY(5deg)}.vhs-box img{display:block;width:100%;height:min(46vh,420px);object-fit:cover;background:#12152c}.vhs-box span{position:absolute;left:12px;right:8px;bottom:9px;overflow:hidden;color:#8ff4ff;font:10px Consolas,monospace;letter-spacing:1px;white-space:nowrap}.selection h2{margin-bottom:10px}.selection p{max-height:250px;overflow:auto;padding-right:8px;scrollbar-width:thin;scrollbar-color:#20ddeb transparent}.selection button{background:linear-gradient(90deg,#b64a05,#dc7b0a);letter-spacing:1px}.selection button:hover,.selection button:focus{background:#ff9b19;color:#160b00}@media(max-width:1100px){.vault-layout{grid-template-columns:1fr}.selection{position:relative;top:auto;height:auto;min-height:520px}.vault{height:620px}.tape-label{padding-right:20px}.tape::before,.tape::after{display:none}}'''
TAPE_DETAIL_CSS = r'''.tape-label small{display:flex;align-items:center;justify-content:space-between;gap:10px}.tape-label small span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tape-label mark{flex:none;padding:2px 6px;border:1px solid #946734;border-radius:3px;background:#b65b23;color:#fff;font:10px Consolas,monospace}.tape:nth-child(4n+2) .tape-ridge{background:repeating-linear-gradient(90deg,#ff3aa9 0 2px,#7c164e 2px 4px)}.tape:nth-child(4n+3) .tape-ridge{background:repeating-linear-gradient(90deg,#a7ff28 0 2px,#4f7c13 2px 4px)}.tape:nth-child(4n) .tape-ridge{background:repeating-linear-gradient(90deg,#ffbd21 0 2px,#835a00 2px 4px)}'''
TV_OVERHAUL_CSS = r'''header{height:76px;padding:0 2vw;gap:16px}.brand-lockup img{width:46px;height:46px}.brand-lockup h1{font-size:21px}.brand-lockup small{font-size:10px}.update{padding:9px 13px}.app-sidebar{top:76px;width:210px;padding:14px 11px}.wrap{margin-left:210px;padding:18px 2.4vw}.menu-collapsed .wrap{margin-left:68px}.menu-collapsed .app-sidebar{width:68px}.nav-group{display:block;margin:12px 10px 3px;color:#566583;font:10px Consolas,monospace;letter-spacing:2px}.app-sidebar nav>a{padding:9px 8px}.tuned-channel{position:absolute;left:11px;right:11px;bottom:54px;display:grid;gap:4px;padding:12px;border:1px solid #33436c;border-radius:11px;background:#0d1328}.tuned-channel small{color:#4feaff;font:10px Consolas,monospace}.tuned-channel b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tuned-channel span{overflow:hidden;color:#8493b3;font-size:11px;text-overflow:ellipsis;white-space:nowrap}.tuned-channel.active{border-color:#5cffb0;box-shadow:0 0 22px #24f3a522}.menu-collapsed .tuned-channel,.menu-collapsed .nav-group{display:none}.guide-preview{grid-template-columns:180px 1fr;min-height:135px}.guide-preview>img,.guide-preview>strong{width:160px;height:108px}.guide-preview b{font-size:23px}.guide-toolbar{gap:8px;padding:9px 11px}.guide-toolbar button{padding:9px 11px}.guide-window-controls{display:flex;gap:5px}.tv-guide{max-height:calc(100vh - 385px);overflow-y:auto;scroll-behavior:smooth;scroll-snap-type:y proximity;scrollbar-width:thin;scrollbar-color:#1eddfb #0c1023}.player-hud{position:fixed;z-index:12;left:20px;top:18px;display:grid;gap:2px;max-width:55vw;padding:10px 14px;border:1px solid #2be9ff;border-radius:9px;background:#070b20dd;box-shadow:0 10px 35px #000}.player-hud span{color:#62ffad;font:bold 11px Consolas,monospace}.player-hud small{color:#a7b4d1}.player-hud small.error{color:#ff627b}video.mini{inset:auto 2vw auto auto;top:92px;width:min(46vw,720px);height:auto;aspect-ratio:16/9;border:2px solid #25e8ff;border-radius:14px;box-shadow:0 22px 60px #000;z-index:6}.has-mini-player .wrap{padding-top:min(27vw,405px)}.has-mini-player .player-hud{left:auto;right:calc(2vw + 16px);top:105px;z-index:7}.has-mini-player .close{top:105px;right:3vw;z-index:8;transform:translateY(45px)}.program-dialog{width:min(620px,calc(100% - 30px));padding:0;border:1px solid #24e8ff;border-radius:16px;background:linear-gradient(145deg,#11163b,#080a1d);color:#fff;box-shadow:0 25px 90px #000}.program-dialog::backdrop{background:#02030cbb;backdrop-filter:blur(8px)}.program-dialog-body{padding:28px}.program-dialog h2{color:#fff52d;font-size:30px}.program-meta{color:#56eaff}.program-actions{display:flex;flex-wrap:wrap;gap:9px;margin-top:22px}.program-actions button,.favorite-card{padding:11px 14px;border:1px solid #24e8ff;border-radius:8px;background:#151a3d;color:#fff}.program-close{position:absolute;right:12px;top:10px;border:0;background:transparent;color:#fff;font-size:25px}.favorites-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}.favorites-list h3{grid-column:1/-1}.favorite-card{display:grid;gap:5px;text-align:left}.favorite-card small{color:#8fa0c0}.movie-tools{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:12px}.movie-tools button{padding:7px 12px;border:1px solid #ff3eb8;border-radius:999px;background:#0f1330;color:#8ff}.movie-tools button.active{border-color:#fff52d;color:#fff52d}.movie-search{margin-left:auto;padding:9px 12px;border:1px solid #28eaff;border-radius:8px;background:#090d20;color:#fff}.vault-layout{grid-template-columns:minmax(500px,650px) minmax(440px,1fr);min-height:510px;justify-content:center}.vault{height:auto;min-height:500px;max-height:none;overflow:visible;padding:3px 0}.tape,.tape:nth-child(even){height:72px;max-width:650px;grid-template-columns:10px 76px minmax(0,1fr) 88px}.tape img{width:68px;height:58px}.tape-label{padding-right:46px}.tape::before,.tape::after{right:97px}.tape i{min-width:82px;padding:8px}.selection{top:94px;height:510px;min-height:510px;max-height:510px;grid-template-columns:minmax(165px,210px) 1fr}.vhs-box img{aspect-ratio:2/3;height:auto;max-height:360px;object-fit:cover}.vault-pager{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:10px}.vault-pager button{padding:9px 13px;border:1px solid #28eaff;border-radius:7px;background:#131836;color:#fff}.vault-pager button:disabled{opacity:.35}@media(max-width:1100px){.vault-layout{grid-template-columns:minmax(0,700px);justify-content:center}.selection{position:relative;top:auto;height:auto;max-height:none}.has-mini-player .wrap{padding-top:50vw}}'''
LIVE_ARTWORK_CSS = r'''.guide-preview{position:relative;isolation:isolate;overflow:hidden;grid-template-columns:minmax(250px,320px) 1fr;min-height:210px;padding:15px;background:linear-gradient(90deg,#101638 0 42%,#0a1028cc 72%,#0a102899),var(--guide-backdrop),linear-gradient(120deg,#111741,#090d24);background-position:center;background-size:cover}.guide-preview::after{content:"";position:absolute;z-index:-1;inset:0;background:linear-gradient(90deg,#0b1134 0 24%,#0b1134dd 43%,#070b1f35 78%),radial-gradient(circle at 92% 30%,#ff35b52e,transparent 35%)}.guide-preview>img,.guide-preview>strong{position:relative;width:100%;height:180px;object-fit:cover;border-radius:12px;box-shadow:0 0 24px #ff3eb855}.guide-preview>span{position:relative}.guide-preview small mark{margin-left:8px;background:transparent;color:#ff35c8}.guide-preview b{font-size:clamp(24px,2.25vw,38px)}.guide-preview em{font-size:16px}.guide-preview i{width:min(800px,94%)}.tv-guide{max-height:calc(100vh - 470px)}.program-dialog{width:min(780px,calc(100% - 30px))}.program-dialog-layout{display:grid;grid-template-columns:minmax(180px,260px) 1fr;gap:24px;align-items:start}.program-poster{width:100%;max-height:330px;aspect-ratio:16/10;object-fit:cover;border:2px solid #ff3eb8;border-radius:12px;box-shadow:0 0 28px #ff3eb844}@media(max-width:760px){.guide-preview{grid-template-columns:1fr}.program-dialog-layout{grid-template-columns:1fr}.program-poster{max-height:240px}}'''
FILTER_CSS = r'''.movie-tools{display:grid;grid-template-columns:auto minmax(240px,1fr) repeat(3,auto) auto;gap:9px;padding:11px;border:1px solid #243665;border-radius:13px;background:linear-gradient(135deg,#101630dd,#090b20dd);box-shadow:0 12px 30px #0005}.movie-decades{display:flex;gap:6px}.movie-search-wrap{display:flex;align-items:center;gap:8px;min-width:0;padding:0 12px;border:1px solid #28eaff;border-radius:8px;color:#58efff;background:#080d20}.movie-search{width:100%;min-width:0;margin:0;border:0;background:transparent}.movie-tools select{min-width:125px;padding:9px 28px 9px 10px;border:1px solid #ff3eb8;border-radius:8px;background:#10132c;color:#fff}.movie-tools #movie-count{align-self:center;white-space:nowrap;color:#65efcf;font:bold 12px Consolas,monospace}@media(max-width:1250px){.movie-tools{grid-template-columns:1fr 1fr repeat(2,auto)}.movie-decades{grid-column:1/-1}.movie-search-wrap{grid-column:1/3}}@media(max-width:760px){.movie-tools{display:flex}.movie-search-wrap{order:-1;width:100%}.movie-tools select{flex:1}.movie-decades{overflow-x:auto;width:100%}}'''
PLAYER_DOCK_CSS = r'''.live-stage{display:grid;grid-template-columns:minmax(300px,38%) minmax(0,1fr);gap:14px;align-items:stretch;margin:0 0 12px}.mini-player-slot{position:relative;isolation:isolate;min-height:210px;overflow:hidden;border:1px solid #20e8ff;border-radius:14px;background:#070b1c;box-shadow:0 18px 42px #0007}.mini-player-poster{position:absolute;inset:0;display:grid;place-content:center;gap:8px;background:radial-gradient(circle at 50% 35%,#ff39b744,transparent 42%),linear-gradient(145deg,#19164a,#080a1d);color:#55edff;text-align:center}.mini-player-poster img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;filter:saturate(.92) brightness(.72)}.mini-player-poster::after{content:"";position:absolute;inset:0;background:linear-gradient(0deg,#050717cc,transparent 60%)}.mini-player-poster strong,.mini-player-poster span{position:relative;z-index:1}.mini-player-poster strong{font-size:54px;color:#fff}.mini-player-poster span{font:bold 11px Consolas,monospace;letter-spacing:2px}.live-stage .live{min-width:0;padding:0}.live-stage .guide-preview{height:100%;min-height:210px;margin:0;grid-template-columns:1fr}.live-stage .guide-preview>img,.live-stage .guide-preview>strong{display:none}.mini-player-shell{display:contents}.mini-player-shell.guide-dock,.mini-player-shell video.mini{position:absolute!important;display:block;inset:0!important;width:100%!important;height:100%!important;aspect-ratio:16/9;object-fit:contain;border:0!important;border-radius:0!important;box-shadow:none!important}.mini-player-shell.guide-dock .player-hud{position:absolute;z-index:4;left:12px!important;right:12px!important;top:12px!important;bottom:auto!important;display:grid;width:auto;max-width:calc(100% - 24px);padding:9px 11px;pointer-events:none;transform:none}.mini-player-shell.guide-dock .player-hud-actions{display:none}.mini-player-shell.guide-dock .close{position:absolute;z-index:5;right:12px!important;top:auto!important;bottom:12px!important;transform:none!important;padding:8px 11px;background:#080b1ddd;backdrop-filter:blur(8px)}.mini-player-shell:not(.guide-dock) .player-hud{position:fixed;z-index:12;left:50%;right:auto;top:auto;bottom:32px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;width:min(1040px,calc(100vw - 80px));max-width:none;padding:18px 20px;border:1px solid #27eaff;border-radius:16px;background:linear-gradient(110deg,#080c22ed,#18103bed);box-shadow:0 22px 70px #000c,0 0 30px #18dfff1a;backdrop-filter:blur(18px);transform:translateX(-50%);transition:opacity .22s,transform .22s}.player-hud-copy{display:grid;gap:5px;min-width:0}.player-hud-copy span{color:#62ffad;font:bold 11px Consolas,monospace;letter-spacing:1.5px}.player-hud-copy b{color:#52eaff;font-size:14px}.player-hud-copy strong{overflow:hidden;color:#fff52d;font-size:clamp(19px,2vw,29px);text-overflow:ellipsis;white-space:nowrap}.player-hud-copy i{display:block;width:100%;height:5px;overflow:hidden;border-radius:5px;background:#3b425b}.player-hud-copy i u{display:block;height:100%;background:linear-gradient(90deg,#5cffae,#45ddff);text-decoration:none}.player-hud-copy small{color:#a9b6d2}.player-hud-copy em{color:#d2d8e8;font:normal 11px Consolas,monospace}.player-hud-actions{display:flex;align-items:center;gap:8px}.player-hud-actions button{min-width:76px;padding:12px 10px;border:1px solid #30e5ff;border-radius:10px;background:#111938;color:#fff;font-weight:900}.player-hud-actions button small{color:#8cefff;font-size:9px}.player-hud[hidden]{display:none!important}.player-controls-hidden .mini-player-shell:not(.guide-dock) .player-hud,.player-controls-hidden .mini-player-shell:not(.guide-dock) .close{opacity:0;pointer-events:none;transform:translate(-50%,18px)}.has-mini-player .wrap{padding-top:18px!important}@media(max-width:900px){.live-stage{grid-template-columns:1fr}.mini-player-slot{min-height:0;aspect-ratio:16/9}.mini-player-shell:not(.guide-dock) .player-hud{width:calc(100vw - 28px);grid-template-columns:1fr}.player-hud-actions{justify-content:center}}@media(prefers-reduced-motion:reduce){}'''
SIDEBAR_HTML = r'''<aside id="viewer-navigation" class="app-sidebar"><nav aria-label="Main navigation"><a class="active" href="?screen=home" data-screen="home"><i aria-hidden="true">⌂</i><span class="menu-label">Home</span></a><a href="?screen=live" data-screen="live"><i aria-hidden="true">●</i><span class="menu-label">Live TV Guide</span></a><a href="?screen=cinema" data-screen="cinema"><i aria-hidden="true">▣</i><span class="menu-label">Rewind Cinema</span></a><a href="?screen=continue" data-screen="continue"><i aria-hidden="true">▶</i><span class="menu-label">Continue Watching</span></a><a href="?screen=favorites" data-screen="favorites"><i aria-hidden="true">★</i><span class="menu-label">Favorites</span></a><a href="?screen=updates" data-screen="updates"><i aria-hidden="true">↻</i><span class="menu-label">Updates</span></a></nav><section id="tuned-channel" class="tuned-channel"><small>NOW WATCHING</small><b>Nothing tuned</b><span>Choose a live channel or movie</span></section></aside>'''
EXTRA_SCREENS = r'''
<section data-section-block="continue" hidden>
  <div class="viewer-page-heading"><h2>Continue Watching</h2><a href="?screen=cinema" data-screen="cinema">Browse Rewind Cinema ›</a></div>
  <div id="continue-list" class="viewer-collection"></div>
</section>
<section data-section-block="favorites" hidden>
  <div class="viewer-page-heading"><h2>Favorites</h2><a href="?screen=live" data-screen="live">Open Live TV Guide ›</a></div>
  <div id="favorites-list" class="viewer-collection"></div>
</section>
<section data-section-block="updates" hidden>
  <div class="viewer-page-heading"><h2>System</h2></div>
  <div class="viewer-system">
    <section class="viewer-system-card">
      <h3>App updates</h3><p id="screen-update-status" role="status">Checking for updates…</p>
      <button type="button" id="screen-check-update" class="update">Check for Update</button>
      <div id="tv-release-notes" class="home-changelog"></div>
    </section>
    <details class="playback-troubleshooting viewer-system-card">
      <summary>Playback diagnostics</summary>
      <p>Capture stream and player details after a playback issue.</p>
      <div id="playback-troubleshooting-controls"></div>
    </details>
  </div>
</section>'''
GUIDE_LAYOUT_CSS = r'''
.guide-mode main.wrap{padding:16px clamp(14px,1.6vw,30px) 26px}
.live-stage{grid-template-columns:minmax(220px,29%) minmax(0,1fr);gap:16px;min-height:174px;margin:0 0 12px}
.live-stage .mini-player-slot{min-height:174px;border-color:#21d8f6;border-radius:9px;background:#020407;box-shadow:inset 0 0 0 3px #091829,0 0 16px #00cce520}
.mini-player-poster{background:#020407;color:#8faec9;padding:14px}
.mini-player-poster span{color:#8faec9;font:600 11px/1.45 "Segoe UI",sans-serif;letter-spacing:.14em;text-align:center}
.live-stage #live{display:block;padding:0;min-width:0}
.live-stage .guide-preview{display:flex;min-height:174px;height:100%;align-items:center;padding:13px 24px 12px;border-radius:10px;text-align:left;cursor:default;
    background:radial-gradient(ellipse at 100% 0%,#7a18ab55,transparent 56%),linear-gradient(90deg,#080d23 20%,#131143e6 70%,#130b2f80),var(--guide-backdrop,none),linear-gradient(105deg,#0b122b,#201044);
    background-position:center,right,center 35%,center;background-size:cover}
.live-stage .guide-preview::after{background:linear-gradient(129deg,transparent 84%,#da54ff56 84.2% 85.2%,transparent 85.4% 90%,#38ddff45 90.2% 91.1%,transparent 91.3%)}
.live-stage .guide-preview>span{width:100%;min-width:0}
.guide-preview small{display:flex;align-items:center;gap:8px;letter-spacing:.1em}
.guide-preview .guide-signal{display:inline-block;margin:0;width:9px;height:9px;border-radius:50%;background:#32effc;box-shadow:0 0 11px #25d8ec}
.live-stage .guide-preview b{font-size:clamp(20px,2.05vw,34px);line-height:1.08;margin:7px 0 9px;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.guide-preview em{display:flex;align-items:center;flex-wrap:wrap;gap:8px 14px;font:500 13px "Segoe UI",sans-serif}
.guide-preview em>span+span:not(.guide-tag){border-left:1px solid #5282a9;padding-left:14px}
.guide-tag{border:1px solid #20e0fa;border-radius:5px;padding:3px 7px;color:#e6faff;font-size:10px;font-weight:700;text-transform:uppercase}
.guide-tag:last-child{border-color:#dcb84b}
.guide-progress{margin-top:11px;width:min(86%,980px)}
.guide-progress progress{display:block;width:100%;height:6px;border:0;border-radius:5px;overflow:hidden;background:#313b59;accent-color:#39f7bd}
.guide-progress progress::-webkit-progress-bar{background:#313b59}
.guide-progress progress::-webkit-progress-value{background:linear-gradient(90deg,#16e4ad,#68ffde)}
.guide-progress>div{display:flex;justify-content:space-between;margin-top:4px;color:#d9f5ff;font-size:11px;font-variant-numeric:tabular-nums}
.guide-mode .guide-toolbar{margin-bottom:10px;border-color:#1f6c9a;border-radius:9px;padding:7px 8px;gap:8px;min-height:44px}
.guide-mode .guide-toolbar button,.guide-mode .guide-toolbar input{min-height:31px;padding:5px 9px;font-size:12px}
.guide-mode .guide-toolbar input{width:clamp(120px,13vw,245px)}
.guide-toolbar #guide-now{box-shadow:0 0 12px #37dbff50,inset 0 0 9px #0cc7f838}
.guide-toolbar button:disabled{opacity:.45}
.guide-mode .studio-filters{position:relative;gap:8px;margin:0 0 12px;padding:0 0 8px;border-bottom:1px solid #734398;background:linear-gradient(130deg,transparent 94%,#952cff38 94% 96%,transparent 96% 97%,#e545ff44 97% 98.5%,transparent 98.5%)}
.guide-mode .studio-filters button{min-height:31px;padding:6px 12px;border-radius:6px}
.guide-mode .studio-filters .active{box-shadow:0 0 11px #ffe8354a,inset 0 0 6px #f5dc2232}
.player-buffer{position:fixed;inset:0;z-index:11;display:grid;place-content:center;justify-items:center;gap:9px;padding:30px;background:radial-gradient(ellipse at 50% 44%,#1a284bcf,#060817ee 70%);color:#edfaff;text-align:center;pointer-events:none}
.player-buffer b{font-size:clamp(15px,2vw,24px);font-weight:650}
.player-buffer small{color:#9fc4dc;font-size:13px}
.buffer-spinner{width:34px;height:34px;border:3px solid #315878;border-top-color:#30f8fb;border-right-color:#bf5bff;border-radius:50%;animation:buffer-spin .8s linear infinite}
@keyframes buffer-spin{to{transform:rotate(360deg)}}
.player-bug{position:fixed;z-index:11;top:18px;left:19px;display:flex;align-items:center;gap:8px;padding:7px 9px;border:1px solid #3bebfb;border-radius:7px;background:#0b1225cc;color:#c8f7fc;font-size:12px;font-weight:700;pointer-events:none}
.player-bug img{width:43px;height:34px;object-fit:contain}
#player-buffer[hidden],#player-bug[hidden],.buffer-spinner[hidden]{display:none!important}
.guide-dock .player-buffer{position:absolute}
.guide-dock .player-bug{display:none}
.guide-dock .player-hud{display:none!important}
.guide-dock .close{padding:5px 7px;font-size:10px}
@media(prefers-reduced-motion:reduce){.buffer-spinner{animation:none}}
@media(max-width:1100px){.guide-toolbar .guide-window-controls{order:3}.live-stage{grid-template-columns:minmax(205px,34%) minmax(0,1fr)}}
@media(max-width:760px){.live-stage{grid-template-columns:1fr}.live-stage .mini-player-slot{min-height:110px;max-height:210px}.guide-preview b{white-space:normal!important}#guide-board{--station-width:135px}}
'''
PLAYER_DOCK_JS = r'''
let playerShell=null,playerControlsTimer=0;
const viewerQualities=[
    {key:0,label:'HQ',width:1280,height:720,bitrate:4000000},
    {key:1000000,label:'Balanced',width:854,height:480,bitrate:1000000},
    {key:400000,label:'Data Saver',width:480,height:270,bitrate:400000}
];
let viewerQuality=0;
try{
    const saved=Number(localStorage.getItem('channelforge-quality-cap'));
    if(viewerQualities.some(quality=>quality.key===saved))viewerQuality=saved;
}catch(_error){}
function applyViewerQuality(player,starting=false){
    if(!player?.levels?.length)return;
    const quality=viewerQualities.find(option=>option.key===viewerQuality);
    const limit=player.levels.reduce((last,level,index)=>
        level.bitrate<=quality.bitrate&&(!level.height||level.height<=quality.height)&&(!level.width||level.width<=quality.width)?index:last,-1);
    const cap=Math.max(0,limit);
    player.autoLevelCapping=cap;
    if(starting&&player.startLevel>=0)player.startLevel=Math.min(player.startLevel,cap);
    else if(player.currentLevel>cap)player.nextAutoLevel=cap;
}
function qualityControl(){
    return '<label class="player-quality">Quality<select data-quality-cap aria-label="Playback quality">'
        +viewerQualities.map(({key,label,height})=>
            '<option value="'+key+'"'+(key===viewerQuality?' selected':'')+'>'+label+' · '+height+'p</option>').join('')
        +'</select></label>';
}
document.addEventListener('change',event=>{
    if(!event.target.matches('[data-quality-cap]'))return;
    viewerQuality=Number(event.target.value);
    try{localStorage.setItem('channelforge-quality-cap',String(viewerQuality))}catch(_error){}
    applyViewerQuality(hls);
});
function updateTunedIndicator(media,status='PLAYING'){
    const box=$('#tuned-channel'),hud=$('#player-hud'),bug=$('#player-bug');
    if(!media){
        box.classList.remove('active');
        box.innerHTML='<small>NOW WATCHING</small><b>Nothing tuned</b><span>Choose a live channel or movie</span>';
        hud.hidden=bug.hidden=true;
        return;
    }
    const channel=media.channel||{},program=programAt(channel),programs=channel.programs||[];
    const index=programs.indexOf(program),next=index>=0?programs[index+1]:null;
    const title=media.title||program?.title||channel.name||'ChannelForge';
    const stop=Date.parse(program?.stop),start=Date.parse(program?.start),duration=stop-start;
    const progress=duration>0?Math.max(0,Math.min(100,100*(Date.now()-start)/duration)):0;
    const live=media.kind==='live';
    const label=live?'CH '+(channel.number||'—'):'REWIND CINEMA';
    box.classList.add('active');
    box.innerHTML='<small>'+esc(label)+'</small><b>'+esc(channel.name||title)+'</b><span>'+esc(title)+'</span>';
    bug.hidden=!live;
    bug.innerHTML=(live&&channel.logo_url?'<img src="'+esc(channel.logo_url)+'" alt="">':'')+'<span>'+esc(label)+'</span>';
    bug.querySelector('img')?.addEventListener('error',event=>event.currentTarget.remove());
    hud.innerHTML='<div class="player-hud-copy"><span>'+esc(live?'● LIVE NOW · '+label:'▣ REWIND CINEMA')+'</span><b>'
        +esc(channel.name||title)+'</b><strong>'+esc(title)+'</strong><i><u style="width:'+progress+'%"></u></i><small id="player-state">'
        +esc(status)+'</small>'+(next?'<em>COMING UP NEXT · '+esc(next.title)+'</em>':'')+'</div>'
        +'<div class="player-hud-actions">'+qualityControl()
        +(live?'<button data-player-surf="-1" aria-label="Previous channel">▲<br><small>CHANNEL</small></button><button data-player-surf="1" aria-label="Next channel">▼<br><small>CHANNEL</small></button><button data-player-guide aria-label="Open live guide">☰<br><small>GUIDE</small></button>':'')+'</div>';
    hud.hidden=false;
}
function ensurePlayerShell(){if(playerShell)return playerShell;playerShell=document.createElement('section');playerShell.id='mini-player-shell';playerShell.className='mini-player-shell';let player=$('#player');player.parentNode.insertBefore(playerShell,player);playerShell.append(player,$('#player-buffer'),$('#player-bug'),$('#player-hud'),$('#close'));return playerShell}
function dockMiniPlayer(){
    const shell=ensurePlayerShell();
    document.body.classList.remove('player-controls-hidden');
    $('#mini-player-slot').append(shell);
    shell.className='mini-player-shell guide-dock';
}
function closeVideoView(){
    if(playerMini||!liveMode){
        const media=activeMedia;
        if(media?.cinema){
            if(media.ejecting)return;
            saveCurrentMovie();
            media.ejecting=true;
            $('#player').pause();
            animateCinemaTape(false,media.movie,async()=>{
                if(activeMedia!==media)return;
                stopPlayer();
                if(!document.body.classList.contains('cinema-mode'))showScreen('cinema');
                await fullscreenRequest.catch(()=>{});
                if(!activeMedia)$('#selected-play')?.focus({preventScroll:true});
            });
        }else stopPlayer();
    }
    else minimizePlayer();
}
function showPlayerControls(){document.body.classList.remove('player-controls-hidden');clearTimeout(playerControlsTimer);if(!playerMini&&!$('#player-state.error')&&$('#player-buffer')?.hidden)playerControlsTimer=setTimeout(()=>document.body.classList.add('player-controls-hidden'),3200)}
function surfChannel(direction){if(!liveChannels.length)return;let index=liveChannels.findIndex(channel=>channel.stream_url===activeStreamUrl);index=index<0?0:(index+direction+liveChannels.length)%liveChannels.length;let channel=liveChannels[index],program=programAt(channel);play(channel.stream_url,{kind:'live',channel,title:program?.title||channel.name})}
document.addEventListener('mousemove',showPlayerControls,{passive:true});document.addEventListener('keydown',showPlayerControls);document.addEventListener('click',event=>{let surf=event.target.closest('[data-player-surf]');if(surf)surfChannel(Number(surf.dataset.playerSurf)||1);if(event.target.closest('[data-player-guide]'))minimizePlayer()});
'''
CONTROLLER_JS = r'''
function tvFocusTargets(){let dialog=document.querySelector('dialog[open]'),scope=dialog||document;return[...scope.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])')].filter(element=>!element.hidden&&element.offsetParent!==null&&getComputedStyle(element).visibility!=='hidden')}
function tvMoveFocus(direction){if(liveMode&&$('#player').classList.contains('playing')&&!playerMini&&(direction==='up'||direction==='down')){surfChannel(direction==='up'?-1:1);return}let targets=tvFocusTargets(),current=targets.includes(document.activeElement)?document.activeElement:null;if(!targets.length)return;if(!current){targets[0].focus();return}let origin=current.getBoundingClientRect(),ox=origin.left+origin.width/2,oy=origin.top+origin.height/2,best=null,score=Infinity;for(let target of targets){if(target===current)continue;let rect=target.getBoundingClientRect(),dx=rect.left+rect.width/2-ox,dy=rect.top+rect.height/2-oy,primary=direction==='left'?-dx:direction==='right'?dx:direction==='up'?-dy:dy,secondary=direction==='left'||direction==='right'?Math.abs(dy):Math.abs(dx);if(primary<=4)continue;let candidate=primary+secondary*2.25;if(candidate<score){score=candidate;best=target}}if(best){best.focus({preventScroll:true});best.scrollIntoView({behavior:'smooth',block:'nearest',inline:'nearest'})}}
function tvBack(){let dialog=document.querySelector('dialog[open]');if(dialog){dialog.close();return}if($('#player').classList.contains('playing')){closeVideoView();return}if(!$('#viewer-dismiss').hidden){setNavigation(false);$('#menu-toggle').focus();return}showScreen('home');document.querySelector('[data-screen="home"]')?.focus()}
document.addEventListener('keydown',event=>{let editable=event.target.matches?.('input,textarea,select,[contenteditable="true"]');if(editable&&event.key!=='Escape')return;let direction={ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down'}[event.key];if(direction){event.preventDefault();tvMoveFocus(direction)}else if(event.key==='Escape'||(event.key.toLowerCase()==='b'&&activeMedia?.cinema)){event.preventDefault();tvBack()}});
let tvGamepadState={},tvGamepadRepeat=0,tvGamepadFrame=0;function pollTvGamepads(now){tvGamepadFrame=0;let pad=[...(navigator.getGamepads?.()||[])].find(Boolean);if(pad){let state={up:pad.buttons[12]?.pressed||pad.axes[1]<-.55,down:pad.buttons[13]?.pressed||pad.axes[1]>.55,left:pad.buttons[14]?.pressed||pad.axes[0]<-.55,right:pad.buttons[15]?.pressed||pad.axes[0]>.55,activate:pad.buttons[0]?.pressed,back:pad.buttons[1]?.pressed};for(let action of Object.keys(state)){let repeatable=['up','down','left','right'].includes(action),repeat=repeatable&&state[action]&&tvGamepadState[action]&&now-tvGamepadRepeat>190;if(state[action]&&(!tvGamepadState[action]||repeat)){showPlayerControls();if(repeatable){tvMoveFocus(action);tvGamepadRepeat=now}else if(action==='activate')document.activeElement?.click();else tvBack()}}tvGamepadState=state}else tvGamepadState={};startTvGamepads()}
function startTvGamepads(){
    if(!tvGamepadFrame&&!document.hidden&&Array.from(navigator.getGamepads?.()||[]).some(Boolean)){
        tvGamepadFrame=requestAnimationFrame(pollTvGamepads);
    }
}
window.addEventListener('gamepadconnected',startTvGamepads);
document.addEventListener('visibilitychange',startTvGamepads);
startTvGamepads();
'''
HOME_DETAILS = r'''
<section class="home-shelf" id="home-on-now" data-section-block="home">
  <div class="home-shelf-heading"><h2>On Now</h2><a href="?screen=live" data-screen="live">View All ›</a></div>
  <div class="home-shelf-items"></div>
</section>
<section class="home-shelf" id="home-rewind" data-section-block="home">
  <div class="home-shelf-heading"><h2>From Rewind Cinema</h2><a href="?screen=cinema" data-screen="cinema">View All ›</a></div>
  <div class="home-shelf-items"></div>
</section>
<section class="home-shelf home-personal" id="home-personal" data-section-block="home">
  <div class="home-shelf-heading"><h2>Continue Watching</h2><a href="?screen=continue" data-screen="continue">View All ›</a></div>
  <div class="home-shelf-items"></div>
</section>
<details class="home-release" data-section-block="home"><summary>Release notes <span id="home-release-version">v__VERSION__</span></summary>
  <p id="home-update-state"></p><div id="home-changelog" class="home-changelog">Loading changelog…</div>
  <button type="button" id="home-display">Fullscreen</button>
</details>'''.replace("__VERSION__", VERSION)
CINEMA_STAGE_HTML = r'''<section class="cinema-stage" data-section-block="cinema" hidden>
  <div class="cinema-hero"><h2><span>REWIND</span> <em>CINEMA</em></h2></div>
</section>'''
SCREEN_NAV_JS = r'''
let homeProgramKey='', homeMovieUrl=null, homeMovieRequest=null, homePersonalKey='', homeHistoryLoaded=false;
function renderHomePersonal(){
    const seen=new Set();
    const entries=movieHistory().filter(entry=>{
        const movie=entry?.movie;
        if(!movie?.stream_url||!String(movie.title||'').trim()||!(Number(entry.resumeAt)>0))return false;
        const key=movieKey(movie);
        if(seen.has(key))return false;
        seen.add(key);
        return true;
    }).slice(0,4);
    const key=JSON.stringify(entries.map(entry=>[entry.movie.stream_url,entry.resumeAt]));
    if(key===homePersonalKey)return;
    homePersonalKey=key;
    const panel=$('#home-personal');
    const shelf=panel.querySelector('.home-shelf-items');
    panel.classList.toggle('home-shelf-empty',!entries.length);
    shelf.replaceChildren();
    for(const {movie,resumeAt} of entries)shelf.append(resumeCard(movie,resumeAt));
}
function homeProgress(program,now){
    const start=Date.parse(program?.start), stop=Date.parse(program?.stop);
    return stop>start?'<progress aria-label="Program progress" max="100" data-start="'+start+'" data-stop="'+stop+'" value="'+programProgress(program,now)+'"></progress>':'';
}
setInterval(()=>{
    if(document.hidden||!document.body.classList.contains('home-mode'))return;
    const now=Date.now();
    document.querySelectorAll('.home-feature progress,#home-on-now progress').forEach(progress=>{
        const start=Number(progress.dataset.start),stop=Number(progress.dataset.stop);
        progress.value=Math.max(0,Math.min(100,100*(now-start)/(stop-start)));
    });
},15000);
function watchHomeChannel(channel){
    showScreen('live');
    tuneGuideChannel(channel);
}
function renderHome(){
    renderHomePersonal();
    if(!homeHistoryLoaded){
        homeHistoryLoaded=true;
        renderContinueWatching().then(renderHomePersonal);
    }
    const now=Date.now(), featured=liveChannels.slice(0,6).map(channel=>[channel,programAt(channel,now)]);
    const key=JSON.stringify(featured.map(([channel,program])=>[channel.stream_url,channel.name,program?.title,program?.start,program?.stop,channelArt(channel,program)]));
    const liveShelf=$('#home-on-now .home-shelf-items');
    if(key!==homeProgramKey){
        homeProgramKey=key;
        const pick=featured.find(([,program])=>program)||featured[0];
        const feature=$('#home-spotlight .home-feature');
        feature.classList.toggle('home-feature-empty',!pick);
        if(pick){
            const [channel,program]=pick, art=channelArt(channel,program);
            feature.innerHTML=(art?'<img class="home-feature-art" alt="" src="'+esc(art)+'">':'')
                +'<div class="home-feature-copy"><span class="home-live-label">LIVE NOW</span><h2 title="'+esc(program?.title||channel.name)+'">'+esc(programSeries(program)||channel.name)+'</h2><p>'
                +esc(channel.name)+' · CH '+esc(channel.number||'—')+'</p>'+homeProgress(program,now)
                +'<div class="home-feature-actions"><button type="button" class="home-watch">▶ Watch Live</button><button type="button" class="home-open-guide">Open Guide</button></div></div>';
            feature.querySelector('.home-watch').onclick=()=>watchHomeChannel(channel);
            watchArtwork(feature.querySelector('img'),channel.logo_url);
        }else{
            feature.innerHTML='<div class="home-feature-copy"><h2>Live TV</h2><p>No channels are online right now.</p>'
                +'<div class="home-feature-actions"><button type="button" class="home-open-guide">Open Guide</button><button type="button" class="home-open-cinema">Browse Rewind Cinema</button></div></div>';
            feature.querySelector('.home-open-cinema').onclick=()=>showScreen('cinema');
        }
        feature.querySelector('.home-open-guide').onclick=()=>showScreen('live');
        liveShelf.innerHTML=featured.map(([channel,program],index)=>{
            const art=channelArt(channel,program);
            return '<button type="button" data-home-channel="'+index+'"><span class="home-channel-image">'+(art?'<img src="'+esc(art)+'" alt="" loading="lazy">':'')+'</span>'
                +'<span class="home-channel-copy"><b>'+esc(channel.name)+'</b><small>'+esc(programSeries(program)||'CH '+(channel.number||'—'))+'</small>'+homeProgress(program,now)+'</span></button>';
        }).join('');
        $('#home-on-now').classList.toggle('home-shelf-empty',!featured.length);
        liveShelf.querySelectorAll('button').forEach(button=>{
            const channel=featured[Number(button.dataset.homeChannel)][0];
            watchArtwork(button.querySelector('img'),channel.logo_url);
            button.onclick=()=>watchHomeChannel(channel);
        });
    }
    const movies=$('#home-rewind .home-shelf-items'), creator=allCreators.find(supportsCinema);
    const address=creator?.vault_url||'';
    if(homeMovieUrl===address)return;
    homeMovieUrl=address;
    homeMovieRequest?.abort();
    $('#home-rewind h2').textContent='From Rewind Cinema';
    movies.innerHTML='';
    if(!creator)return;
    const controller=new AbortController();homeMovieRequest=controller;
    const url=new URL(address);url.searchParams.set('limit','6');url.searchParams.set('sort','added');
    const timer=setTimeout(()=>controller.abort(),8000);
    json(url.href,false,controller.signal).then(value=>{
        if(controller.signal.aborted||homeMovieUrl!==address)return;
        const picks=movieRecords(creator,value).slice(0,6);
        if(picks.length&&picks.every(movie=>Number(movie.added_at)>0))$('#home-rewind h2').textContent='Recently Added to Rewind';
        for(const movie of picks){
            const card=document.createElement('button'), cover=movie.poster_url||movie.thumbnail_url;
            const art=movie.backdrop_url||cover;
            card.type='button';card.className='home-movie';card.title=movie.title;
            card.innerHTML='<span class="home-movie-art" aria-hidden="true">'+esc(Array.from(movie.title)[0]||'')
                +(art?'<img src="'+esc(art)+'" alt="" loading="lazy">':'')
                +'</span><span class="home-movie-label"><b>'+esc(movie.title)+'</b><small aria-hidden="true">VHS</small></span>';
            const image=card.querySelector('img');
            if(image){
                const useActualAspect=()=>image.classList.toggle('portrait-art',image.naturalHeight>image.naturalWidth);
                image.addEventListener('load',useActualAspect);
                if(image.complete&&image.naturalWidth>0)useActualAspect();
                watchArtwork(image,cover);
            }
            card.onclick=async()=>{
                selectedCreator=creator;
                const loading=studio(creator);
                showScreen('cinema');
                await loading;
                if(creator===selectedCreator&&document.body.classList.contains('cinema-mode')){
                    selectTape(movie,null);
                    $('#selected-play')?.focus();
                }
            };
            movies.append(card);
        }
    }).catch(()=>{}).finally(()=>clearTimeout(timer));
}
const narrowNavigation=matchMedia('(max-width: 900px)');
function setNavigation(open){
    document.body.classList.toggle('menu-open',open);
    document.body.classList.toggle('menu-collapsed',!open);
    $('#menu-toggle').setAttribute('aria-expanded',String(open));
    const drawer=open&&narrowNavigation.matches;
    $('#viewer-dismiss').hidden=!drawer;
    $('main.wrap').inert=drawer;
}
function toggleNavigation(){
    setNavigation(!document.body.classList.contains('menu-open'));
}
function showScreen(name){
    if(!document.querySelector('[data-section-block="'+CSS.escape(name)+'"]'))name='home';
    const previousFocus=document.activeElement;
    if((liveMode&&name!=='live')||(activeMedia?.cinema&&name!=='cinema'))stopPlayer();
    document.querySelectorAll('[data-section-block]').forEach(block=>block.hidden=block.dataset.sectionBlock!==name);
    document.querySelectorAll('[data-screen]').forEach(button=>button.classList.toggle('active',button.dataset.screen===name));
    document.querySelectorAll('.app-sidebar nav [data-screen]').forEach(link=>{
        if(link.dataset.screen===name)link.setAttribute('aria-current','page');
        else link.removeAttribute('aria-current');
    });
    document.body.classList.toggle('guide-mode',name==='live');
    document.body.classList.toggle('home-mode',name==='home');
    document.body.classList.toggle('cinema-mode',name==='cinema');
    if(name==='home')renderHome();
    if(name==='live')renderLiveGuide();
    if(name==='continue')renderContinueWatching();
    if(name==='cinema'&&selectedCreator)studio(selectedCreator);
    const closeDrawer=narrowNavigation.matches&&document.body.classList.contains('menu-open');
    if(closeDrawer)setNavigation(false);
    window.scrollTo(0,0);
    document.querySelector('main.wrap').scrollTop=0;
    if(previousFocus?.closest('[data-section-block][hidden]')||(closeDrawer&&previousFocus?.closest('.app-sidebar'))){
        const sections=[...document.querySelectorAll('main [data-section-block]:not([hidden])')]
            .filter(section=>section.getClientRects().length);
        const landing=sections.map(section=>section.querySelector('h1,h2,h3'))
            .find(heading=>heading?.getClientRects().length)||sections[0];
        if(landing){landing.setAttribute('tabindex','-1');landing.focus({preventScroll:true})}
    }
}
'''

VIEWER_NAV_CSS = r'''
.app-sidebar .nav-group{color:#59f1ff;font:800 10px/1.3 system-ui,sans-serif;letter-spacing:.12em;
    border-bottom:1px solid #35dbf155;padding-bottom:5px;margin:8px 4px 2px}
.app-sidebar nav{gap:4px}
.app-sidebar nav>a{min-height:42px;grid-template-columns:27px minmax(0,1fr);gap:4px}
.app-sidebar nav>a[aria-current=page]{border-color:#e578ff;background:linear-gradient(100deg,#25204d,#401e58);
    box-shadow:inset 0 0 14px #ec36f72c,0 0 12px #dd4cff32}
.app-sidebar nav>a:focus-visible,.home-panels a:focus-visible,.screen-title a:focus-visible{
    outline:2px solid #61edff;outline-offset:2px}
.app-sidebar a .menu-label{text-overflow:ellipsis}
.menu-collapsed .app-sidebar nav>a{grid-template-columns:1fr;gap:0}
'''

HTML = HTML.replace("</style><header>", f"</style><style>{SIDEBAR_CSS}{GUIDE_CSS}{GUIDE_STAGE_CSS}{HOME_CSS}{VAULT_CSS}{TAPE_DETAIL_CSS}{TV_OVERHAUL_CSS}{LIVE_ARTWORK_CSS}{FILTER_CSS}{PLAYER_DOCK_CSS}{VIEWER_NAV_CSS}</style><header>")
HTML = HTML.replace('<small id="update-bytes"></small>', '<small id="update-bytes"></small><textarea id="update-command" aria-label="Install command" rows="4" style="width:100%;margin-top:10px" readonly hidden></textarea>', 1)
HTML = HTML.replace('<header><div class="brand-lockup">', '<header><button id="menu-toggle" type="button" class="hamburger" aria-label="Toggle navigation" aria-controls="viewer-navigation" aria-expanded="false"><span aria-hidden="true"></span></button><div class="brand-lockup">')
HTML = HTML.replace('<h1>CHANNELFORGE TV</h1>', '<h1>ChannelForge TV</h1>', 1)
HTML = HTML.replace('<span class="tag">REWIND CINEMA</span>', '<a class="tag" href="?screen=cinema" data-screen="cinema">▶ &nbsp; REWIND CINEMA</a>', 1)
HTML = HTML.replace('</header><aside id="update-panel"', f'</header><div id="viewer-dismiss" aria-hidden="true" hidden></div>{SIDEBAR_HTML}<aside id="update-panel"')
HTML = re.sub(r'<section class="hero">.*?</section>',
              '<section id="home-spotlight" data-section-block="home"><div class="home-feature"></div></section>', HTML, count=1)
HTML = HTML.replace('</section><div class="section-heading"><h2>CREATOR STUDIOS', f'</section>{HOME_DETAILS}<div class="section-heading"><h2>CREATOR STUDIOS', 1)
HTML = HTML.replace('<div class="section-heading"><h2>CREATOR STUDIOS', '<div class="section-heading" data-section-block="cinema" hidden><h2>CREATOR STUDIOS', 1)
HTML = HTML.replace('<div id="creators" class="creators">', '<div id="creators" class="creators" data-section-block="cinema" hidden>', 1)
HTML = HTML.replace('<div class="section-heading"><h2 class="cyan">LIVE CHANNELS', '<div class="section-heading" data-section-block="live" hidden><h2 class="cyan">LIVE CHANNELS', 1)
HTML = HTML.replace('<div id="live" class="live"></div>', '<section id="live-stage" class="live-stage" data-section-block="live" hidden><div id="mini-player-slot" class="mini-player-slot"><div id="mini-player-poster" class="mini-player-poster"><span>SELECT A CHANNEL FROM THE GUIDE</span></div></div><div id="live" class="live"></div></section><div class="guide-toolbar" data-section-block="live" hidden><button id="scan-channels">↻ SCAN CHANNELS</button><span id="scan-status">Channels scan automatically.</span><div class="guide-window-controls"><button id="guide-earlier">‹ EARLIER</button><button id="guide-now">NOW</button><button id="guide-later">LATER ›</button><b id="guide-window-label">ON NOW</b></div><label>⌕ <input id="guide-search" type="search" placeholder="Search guide"></label></div><div id="studio-filters" class="studio-filters" data-section-block="live" hidden></div><div id="guide-board" data-section-block="live" hidden><div id="tv-guide" class="tv-guide"></div></div>', 1)
HTML = HTML.replace('placeholder="Search guide"></label></div>', 'placeholder="Search guide"></label><select id="guide-sort" aria-label="Sort live channels"><option value="channel">Channel order</option><option value="rating">Rating · lowest first</option></select></div>', 1)
HTML = HTML.replace('<div id="tv-guide"', '<div id="compact-guide-head" class="compact-guide-head"><span>Channel</span><span>On Now</span><span>Up Next</span><span>Later</span><span>Later 2</span><span>Later 3</span><span>Later 4</span><span>Later 5</span><span>Later 6</span><span></span></div><div id="tv-guide"', 1)
HTML = HTML.replace('</head>', '<style>#guide-sort{max-width:100%;padding:8px 10px;color:#eaf7ff;background:#0a1024;border:1px solid #22e7ff;border-radius:8px;font:inherit}.guide-toolbar{flex-wrap:wrap}</style></head>', 1)
HTML = HTML.replace('<div class="section-heading"><h2 class="pink">REWIND CINEMA', '<div class="section-heading" data-section-block="cinema" hidden><h2 class="pink">REWIND CINEMA', 1)
HTML = HTML.replace('<div class="section-heading" data-section-block="cinema" hidden><h2>CREATOR STUDIOS</h2><small>Choose a studio to tune its channels and movies</small></div><div id="creators" class="creators" data-section-block="cinema" hidden><span class="empty">Connecting to the public directory…</span></div>', '', 1)
HTML = HTML.replace('<div class="section-heading" data-section-block="cinema" hidden><h2 class="pink">REWIND CINEMA</h2><small>BROWSE THE MOVIE SHELF</small></div>', CINEMA_STAGE_HTML, 1)
HTML = HTML.replace('<nav class="categories">', '<nav class="categories" data-section-block="cinema" hidden>', 1)
HTML = HTML.replace('<div class="vault-layout">', '<div class="vault-layout" data-section-block="cinema" hidden>', 1)
HTML = HTML.replace('<nav class="categories" data-section-block="cinema" hidden><span>CONTINUE WATCHING</span><span>80S BLOCKBUSTERS</span><span>90S ESSENTIALS</span><span>CREATOR PICKS</span></nav>', '<nav class="movie-tools" data-section-block="cinema" hidden><div class="movie-decades"><button class="active" data-movie-filter="all">ALL</button><button data-movie-filter="60s">60S</button><button data-movie-filter="70s">70S</button><button data-movie-filter="80s">80S</button><button data-movie-filter="90s">90S</button><button data-movie-filter="2000s">2000S</button><button data-movie-filter="recent">2010+</button></div><label class="movie-search-wrap">⌕<input id="movie-search" class="movie-search" type="search" placeholder="Search title, genre, description…"></label><select id="movie-genre" aria-label="Filter by genre"><option value="all">ALL GENRES</option></select><select id="movie-rating" aria-label="Filter by rating"><option value="all">ALL RATINGS</option></select><select id="movie-sort" aria-label="Sort movies"><option value="title">A–Z</option><option value="newest">NEWEST</option><option value="oldest">OLDEST</option></select><span id="movie-count"></span></nav>', 1)
HTML = HTML.replace('<div class="movie-decades">', '<select id="cinema-studio-select" aria-label="Movie studio" disabled hidden></select><div class="movie-decades">', 1)
HTML = HTML.replace('<button id="update" class="update">', '<button id="desktop-fullscreen" type="button" aria-pressed="false" aria-label="Enter fullscreen (F11)" title="Enter fullscreen (F11)">⛶</button><button id="update" class="update">', 1)
HTML = HTML.replace('</main><video id="player"', f'{EXTRA_SCREENS}</main><aside id="player-hud" class="player-hud" hidden></aside><aside id="player-bug" class="player-bug" hidden></aside><div id="player-buffer" class="player-buffer" role="status" aria-live="polite" hidden><span class="buffer-spinner" aria-hidden="true"></span><b></b><small></small></div><dialog id="program-dialog" class="program-dialog"><button class="program-close" onclick="this.closest(\'dialog\').close()">×</button><div id="program-dialog-body" class="program-dialog-body"></div></dialog><video id="player"', 1)
HTML = HTML.replace("let playbackGeneration=0", PLAYER_DOCK_JS + "\nlet playbackGeneration=0", 1)
HTML = HTML.replace("async function checkUpdate(onLaunch=false)", SCREEN_NAV_JS + "\nasync function checkUpdate(onLaunch=false)", 1)
HTML = HTML.replace("b.onclick=()=>studio(c)", "b.onclick=()=>{studio(c);showScreen('cinema')}", 1)
HTML = HTML.replace("$('#close').onclick", "ensurePlayerShell();$('#menu-toggle').onclick=toggleNavigation;$('#viewer-dismiss').onclick=()=>setNavigation(false);narrowNavigation.addEventListener('change',event=>setNavigation(!event.matches));document.addEventListener('click',event=>{const button=event.target.closest('[data-screen]');if(!button)return;event.preventDefault();const screen=button.dataset.screen;showScreen(screen)});$('#screen-check-update').onclick=()=>checkUpdate(false);$('#close').onclick", 1)
HTML = HTML.replace("$('#update').onclick=()=>checkUpdate(false)", "$('#update').onclick=()=>{showScreen('updates');checkUpdate(false)}", 1)
HTML = HTML.replace("load();checkUpdate(true);", CONTROLLER_JS + "\nload();showScreen(new URLSearchParams(location.search).get('screen')||'home');setNavigation(!narrowNavigation.matches);checkUpdate(true);", 1)
HTML = HTML.replace('</head>', '<style>.retry-stream{margin-left:7px;padding:4px 9px;color:#fff;background:#131838;border:1px solid #26dffa;border-radius:5px;cursor:pointer}</style></head>', 1)
HTML = HTML.replace("OPEN VERIFIED UPDATE", "RESTART & INSTALL UPDATE").replace("Opening the verified update…", "Restarting to install the verified update in place…")
HTML = HTML.replace("</head>", f"<style>{GUIDE_LAYOUT_CSS}</style></head>", 1)
HTML = HTML.replace("</head>", r'''<style>
body.viewer-shell{--viewer-header:clamp(62px,4.2vw,74px);--viewer-sidebar:clamp(187px,14vw,242px);background:#08131d}
body.viewer-shell.menu-collapsed{--viewer-sidebar:57px}
.viewer-shell>header{height:var(--viewer-header);gap:12px;border-bottom:1px solid #4de2f2;background:repeating-linear-gradient(133deg,transparent 0 3px,#24adce04 3px 5px),linear-gradient(110deg,#0a111e,#0b202b);box-shadow:none}
.viewer-shell .hamburger{display:grid;place-items:center;min-width:44px;height:42px;padding:0;border:2px solid #03dbea;color:#02e9f7;background:#091622;box-shadow:0 0 12px #00e8f629}
.viewer-shell .hamburger span{width:22px;height:3px;border-radius:2px;background:currentColor;box-shadow:0 -7px currentColor,0 7px currentColor}
.viewer-shell .brand-lockup{min-width:0;gap:10px;padding-left:12px;border-left:1px solid #65758e}
.viewer-shell .brand-lockup img{width:clamp(36px,3.4vw,58px);height:clamp(36px,3.4vw,58px);filter:none}
.viewer-shell .brand-lockup>div{min-width:0;padding-left:10px;border-left:1px solid #65758e}
.viewer-shell .brand-lockup h1{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#f7f8ff;font:800 italic clamp(17px,1.85vw,32px)/1.2 "Segoe UI",sans-serif;letter-spacing:0;text-transform:none}
.viewer-shell .brand-lockup small,.viewer-shell header .tag,.viewer-shell .tuned-channel{display:none}
.viewer-shell header .update{margin-left:auto;font-size:13px;line-height:1.15;font-weight:700;text-align:center}
.viewer-shell header .update-available{border-color:#f338de}
.viewer-shell #viewer-dismiss:not([hidden]){position:fixed;inset:var(--viewer-header) 0 0;z-index:7;background:#020712b3}
.viewer-shell :is(h1,h2,h3)[tabindex="-1"]:focus{outline:none}
.viewer-shell .app-sidebar{top:var(--viewer-header);width:var(--viewer-sidebar);padding:clamp(14px,2.4vw,40px) 10px 12px;overflow-y:auto;border-right:1px solid #215469;
    background:repeating-linear-gradient(135deg,transparent 0 4px,#248cc010 4px 5px),linear-gradient(155deg,#071117 62%,#101b35);backdrop-filter:none}
.viewer-shell .app-sidebar::after{content:"";position:absolute;pointer-events:none;inset:auto 0 0;height:clamp(84px,17vh,174px);opacity:.48;
    background:url("/app-icon.png") center 15% / min(56%,125px) auto no-repeat,
        linear-gradient(0deg,#08142500 12%,#101b35 94%),
        repeating-linear-gradient(0deg,#1682ba 0 1px,transparent 1px 19px),
        repeating-linear-gradient(114deg,#126e9c 0 1px,transparent 1px 22px)}
.viewer-shell .app-sidebar nav{position:relative;z-index:1}
.viewer-shell.menu-collapsed .app-sidebar::after{display:none}
.viewer-shell .app-sidebar nav{gap:4px}
.viewer-shell .app-sidebar nav>a{min-height:clamp(39px,2.9vw,49px);grid-template-columns:clamp(22px,2vw,31px) minmax(0,1fr);gap:7px;padding:7px 8px;border:0;border-left:3px solid transparent;border-bottom:1px solid #122832;border-radius:7px;color:#f4f6fc;font-size:clamp(13px,1vw,17px);font-weight:700}
.viewer-shell .app-sidebar nav>a i{font-size:1.65em;line-height:1;color:#acc3d9;text-shadow:none}
.viewer-shell .app-sidebar nav>a:hover,.viewer-shell .app-sidebar nav>a:focus-visible{background:#182335}
.viewer-shell .app-sidebar nav>a[aria-current=page]{background:linear-gradient(90deg,#113747,#10202c);border-left-color:#00ecff;box-shadow:inset 0 0 0 1px #235264}
.viewer-shell .app-sidebar nav:has(a:focus-visible)>a[aria-current=page]:not(:focus-visible){background:transparent;border-left-color:transparent;box-shadow:none}
.viewer-shell.menu-collapsed .app-sidebar nav>a{grid-template-columns:1fr;padding-inline:3px}
body.viewer-shell main.wrap{margin-left:var(--viewer-sidebar);min-width:0;max-width:none}
.viewer-shell.home-mode main.wrap{padding:10px clamp(9px,1vw,16px) 20px}
.viewer-shell.guide-mode main.wrap{padding:10px clamp(8px,1vw,16px) 18px}
.application-fullscreen body.viewer-shell>header{flex-basis:var(--viewer-header)}
@media(max-width:900px){
    body.viewer-shell{--viewer-header:58px}
    body.viewer-shell main.wrap{margin-left:0}
    .viewer-shell .app-sidebar{width:min(220px,84vw);z-index:8;box-shadow:10px 0 32px #0008}
    .viewer-shell.menu-open .app-sidebar{visibility:visible;transform:none}
}
@media(max-width:520px){
    .viewer-shell>header{gap:7px}
    .viewer-shell .brand-lockup{gap:5px;padding-left:4px;border:0}
    .viewer-shell .brand-lockup>div{padding-left:0;border:0}
    .viewer-shell .brand-lockup img{width:30px;height:30px}
    .viewer-shell #desktop-fullscreen{display:none}
    .viewer-shell header .update{font-size:11px;padding:6px;max-width:29%;flex-shrink:0;white-space:normal}
}
#home-spotlight{--hero-cut:20px;position:relative;isolation:isolate;display:grid;align-items:stretch;margin:0;padding:2px;
    min-height:clamp(183px,18.1vw,330px);background:linear-gradient(112deg,#43eaff,#0c80a7 53%,#5befff);
    clip-path:polygon(var(--hero-cut) 0,calc(100% - var(--hero-cut)) 0,100% var(--hero-cut),100% calc(100% - var(--hero-cut)),calc(100% - var(--hero-cut)) 100%,var(--hero-cut) 100%,0 calc(100% - var(--hero-cut)),0 var(--hero-cut))}
.home-feature{position:relative;isolation:isolate;display:grid;align-items:center;min-width:0;background:#0a101d;
    clip-path:polygon(calc(var(--hero-cut) - 1px) 0,calc(100% - var(--hero-cut) + 1px) 0,100% calc(var(--hero-cut) - 1px),100% calc(100% - var(--hero-cut) + 1px),calc(100% - var(--hero-cut) + 1px) 100%,calc(var(--hero-cut) - 1px) 100%,0 calc(100% - var(--hero-cut) + 1px),0 calc(var(--hero-cut) - 1px))}
#home-spotlight[hidden]{display:none}
.home-feature-art{position:absolute;inset:0 0 0 auto;width:83%;height:100%;object-fit:cover;object-position:center 36%;mask-image:linear-gradient(90deg,transparent,#000 28%);z-index:-2}
.home-feature::before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,#0a101d 3%,#0a101dfa 19%,#0a101dc9 31%,#0a101d28 58%,transparent 78%);z-index:-1}
.home-feature-empty::before{background:radial-gradient(ellipse at 88% 35%,#302455,transparent 70%),linear-gradient(140deg,#101e33,#101122)}
.home-feature-copy{padding:16px clamp(13px,2.8vw,42px);max-width:min(740px,77%);color:#f6f7fb}
.home-live-label{display:inline-block;margin-bottom:6px;padding:3px 8px;border:1px solid #f234e7;border-radius:9px;background:#310e36;color:#fff4fe;font:850 italic clamp(11px,1.05vw,16px)/1.15 "Segoe UI",sans-serif}
.home-feature-copy h2{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;margin:0;color:#f8f5ef;font:900 italic clamp(26px,3.4vw,52px)/1.03 "Arial Narrow","Segoe UI",sans-serif;letter-spacing:-.035em;text-shadow:0 2px 14px #080913}
.home-feature-copy p{margin:5px 0 13px;color:#a5b6d3;font:750 clamp(13px,1.2vw,18px)/1.2 "Segoe UI",sans-serif}
.home-feature-actions{display:flex;flex-wrap:wrap;gap:clamp(8px,1.4vw,20px);margin-top:clamp(8px,1.2vw,18px)}
.home-feature-copy button,.compact-actions button{padding:10px 13px;border:1px solid #37e8f7;background:#0d192b;border-radius:8px;color:#e9f6ff;font-weight:800}
.home-feature-copy button{min-height:44px;padding:8px clamp(12px,2vw,30px);border:2px solid #50647d;border-radius:12px;background:#131b2cca;color:white;font-size:clamp(13px,1.15vw,18px)}
.home-feature-copy .home-watch,.compact-actions button:last-child{border-color:#08e9f5;background:#08e9f5;color:#061525}
.home-feature-copy .home-watch{box-shadow:0 0 24px #00e5ff56}
.home-feature-copy progress,.home-channel-copy progress{display:block;width:min(405px,100%);height:8px;margin:0;border:0;border-radius:9px;background:#45566f;accent-color:#f338de;overflow:hidden}
.home-feature-copy progress::-webkit-progress-bar,.home-channel-copy progress::-webkit-progress-bar{background:#45566f}
.home-feature-copy progress::-webkit-progress-value,.home-channel-copy progress::-webkit-progress-value{border-radius:9px;background:#f338de}
.home-shelf{margin:0;padding:10px 4px 8px;border-bottom:1px solid #153343}.home-shelf-empty{display:none}
.home-shelf h2{display:flex;align-items:center;gap:13px;margin:0 0 12px;color:#f8f8fd;font:800 clamp(18px,1.5vw,25px)/1.15 "Segoe UI",sans-serif}
.home-shelf h2::before{content:"";width:6px;height:.85em;border-radius:4px;background:#f338de}
.home-shelf-items{display:grid;grid-auto-flow:column;grid-auto-columns:calc((100% - 24px)/3);gap:12px;overflow-x:auto;padding:4px 3px 11px;scroll-snap-type:x proximity;scrollbar-width:thin;scrollbar-color:#526682 #0b1424}
.home-shelf button{display:flex;min-width:0;min-height:clamp(66px,5.65vw,99px);overflow:hidden;padding:0;text-align:left;border:2px solid #465772;border-radius:12px;background:linear-gradient(122deg,#121a29,#0c1421);color:#eff8ff;scroll-snap-align:start}
.home-shelf button:is(:hover,:focus-visible){border-color:#e33cee}
.home-channel-image{flex:0 0 48%;min-width:0;background:linear-gradient(135deg,#264058,#17162b)}
.home-channel-image img{width:100%;height:100%;object-fit:cover;max-height:99px}
.home-channel-copy{min-width:0;display:grid;align-content:center;flex:1;gap:clamp(4px,.5vw,8px);padding:7px clamp(7px,1vw,16px)}
.home-channel-copy b,.home-channel-copy small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.home-channel-copy b{font-size:clamp(13px,1.21vw,18px)}.home-channel-copy small{color:#9fafd0;font-size:clamp(11px,1vw,15px);font-weight:700}
.home-channel-copy progress{height:6px;margin:.4em 0 0;width:100%}
.home-shelf-heading{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:6px 12px;margin-bottom:7px}
.home-shelf-heading h2{margin:0}
.home-shelf-heading a{color:#d9f5ff;font-size:13px;text-decoration:none}.home-shelf-heading a:hover{text-decoration:underline}
#home-rewind .home-shelf-items{grid-auto-columns:max(140px,calc((100% - 50px)/6));gap:10px}
#home-rewind .home-shelf-items:empty{display:none}
.home-shelf .home-movie{position:relative;display:grid;min-height:0;align-content:start;overflow:hidden;border:1px solid #d7a967;border-radius:7px;background:#16182a}
.home-movie-art{position:relative;display:grid;place-items:center;width:100%;aspect-ratio:2.15;overflow:hidden;background:linear-gradient(145deg,#342041,#1e172a);color:#c9aedc;font:italic 24px Georgia,serif}
.home-movie-art img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:center;opacity:0}
.home-movie-art img.portrait-art{object-fit:contain}
.home-movie-art img.loaded{opacity:1}
.home-movie-label{position:absolute;inset:auto 0 0;display:flex;align-items:end;justify-content:space-between;gap:6px;padding:10px 7px 6px;
    background:linear-gradient(transparent,#0c0d14bf)}
.home-movie b,.home-movie small{border:1px solid #eac893;border-radius:4px;line-height:1.15;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.home-movie b{padding:4px 6px;background:#f1d095;color:#19131b;font-size:clamp(12px,1.05vw,16px);font-weight:850;font-style:italic}
.home-movie small{flex:none;padding:2px 3px;background:#141013;color:#ffe1a8;font-size:10px;font-weight:800}
.home-shelf .home-movie:hover{border-color:#f338de}
@media(max-width:660px){#home-rewind .home-shelf-items{grid-auto-columns:calc((100% - 10px)/2)}#home-spotlight{--hero-cut:11px}.viewer-shell .app-sidebar::after{display:none}}
.home-release{margin:8px clamp(12px,1.5vw,25px);padding:10px 0;color:#92a6c2;font-size:14px}.home-release summary{cursor:pointer}.home-release summary span{margin-left:8px;color:#5ce4f1}
#home-personal .home-shelf-items{grid-auto-columns:clamp(190px,calc((100% - 36px)/4),300px)}
#home-personal .home-channel-image{flex-basis:40%;max-height:92px}
#home-personal button{border-width:1px}
@media(min-width:1220px){
    #home-on-now .home-shelf-items{grid-auto-columns:calc((100% - 48px)/5)}
    #home-on-now .home-channel-image{flex-basis:41%}
    #home-on-now .home-channel-copy b{font-size:clamp(13px,1vw,17px)}
}
.compact-program progress{display:block;accent-color:#41e3f2;height:7px}
.viewer-shell.guide-mode .live-stage{grid-template-columns:minmax(0,36%) minmax(0,1fr)}
.viewer-shell.guide-mode .live-stage,.viewer-shell.guide-mode .live-stage :is(.mini-player-slot,.guide-preview){min-height:clamp(104px,14.4vh,150px)}
.viewer-shell.guide-mode .live-stage .guide-preview{padding:6px clamp(10px,2vw,22px)}
.viewer-shell.guide-mode .live-stage .guide-preview b{margin:4px 0 6px;font-size:clamp(16px,1.8vw,27px)}
.viewer-shell .screen-title h2{font-size:clamp(22px,2vw,30px)}
.viewer-shell .section-placeholder{min-height:185px;padding:18px}
.viewer-shell .section-placeholder b{font-size:clamp(16px,1.4vw,21px)}
.viewer-shell.cinema-mode{--cinema-top-height:clamp(105px,16vh,166px)}
.viewer-shell.cinema-mode .cinema-hero{height:var(--cinema-top-height)}
.guide-mode .guide-toolbar{gap:9px;padding:8px 4px;background:transparent;border:0;border-top:1px solid #34435b;border-bottom:1px solid #34435b;border-radius:0}
.guide-mode .guide-toolbar button,.guide-mode #guide-sort{min-height:40px;font-size:clamp(12px,1vw,15px);text-transform:none}
.guide-mode .guide-toolbar label{flex:1 1 145px;max-width:none;min-width:115px;margin-left:0}.guide-toolbar input{width:100%;min-width:0}
#scan-status{font-size:12px;color:#9cbed3}
#guide-window-label{font-size:12px;color:#92d3e9;white-space:nowrap}
.guide-mode .studio-filters{margin-bottom:5px;border-bottom:0}.guide-mode .studio-filters button{font-size:clamp(12px,1vw,16px)}
#guide-board{border:0;background:transparent;box-shadow:none}
#guide-board{overflow-x:auto;scrollbar-color:#397991 #101b2b}
#guide-board>.compact-guide-head,#guide-board>.tv-guide{min-width:1250px}
.compact-guide-head,.compact-guide-row{display:grid;grid-template-columns:minmax(210px,1.6fr) repeat(8,minmax(0,1fr)) auto;gap:5px}
.compact-guide-head{align-items:center;margin:0 0 7px;padding:8px 9px;border-radius:12px;background:#1b2a40;color:#c6d6ee;font-size:clamp(13px,1.1vw,17px);font-weight:750}
.compact-guide-head span:first-child{padding-left:22px}
#guide-board #tv-guide{display:grid;gap:3px;border-radius:0;background:transparent;padding:2px;max-height:max(220px,calc(100dvh - var(--viewer-header) - 244px));overflow-y:auto;scroll-snap-type:none;scrollbar-width:thin;scrollbar-color:#20d7ee #0b1524}
.compact-guide-row{align-items:stretch;min-height:clamp(47px,6.24vh,62px);padding:3px 6px;border:1px solid #435672;border-radius:9px;background:#0b1422}
.compact-guide-row:is(:hover,:focus-within){border-color:#07edfa;box-shadow:0 0 0 1px #0fddeb}
.compact-guide-row.tuned .compact-station-copy{border-left-color:#00e9f6}
.compact-station,.compact-actions{display:flex;align-items:center;gap:clamp(5px,1vw,17px);min-width:0}
.compact-station{position:relative;gap:7px;border-right:1px solid #667893;padding-right:4px}
.compact-channel-image{display:block;flex:0 0 min(34%,135px);height:clamp(36px,4.8vh,49px);overflow:hidden;border-radius:8px;background:linear-gradient(130deg,#1f364f,#1d1735)}
.compact-channel-image img{width:100%;height:100%;object-fit:cover}
.compact-station-copy{display:grid;gap:3px;min-width:0;border-left:5px solid #ec4ad7;border-radius:4px;padding-left:clamp(4px,.75vw,10px)}
.compact-station b,.compact-program b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:clamp(13px,1.15vw,18px)}
.compact-station small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#b6cae8;font-size:clamp(11px,1vw,15px);line-height:1.1}
.compact-favorite{align-self:start;margin-left:auto;border:0;background:transparent;color:#c2b5d5;padding:0 2px;font-size:17px}
.compact-favorite[aria-pressed=true]{color:#ffe66d}
.compact-program{display:grid;align-content:center;gap:3px;min-width:0;padding:3px 7px;text-align:left;border:0;border-left:1px solid #314968;background:transparent;color:#e9f2ff}
.compact-program time{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#b7cce1;font-size:clamp(10px,.85vw,14px)}
.compact-air-time{display:flex;align-items:center;gap:7px;min-width:0}
.compact-air-time time{flex:none}
.compact-air-time progress{flex:1;min-width:0}
.compact-program small{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
.compact-program progress{margin:3px 0;width:95%;height:clamp(4px,.5vw,7px);border:0;border-radius:8px;overflow:hidden;background:#4f6078}
.compact-program progress::-webkit-progress-bar{background:#4f6078}.compact-program progress::-webkit-progress-value{background:#0cecf9;border-radius:8px}
.compact-actions{justify-content:flex-end}.compact-guide-head span:last-child,.compact-actions{min-width:clamp(88px,9vw,143px)}.compact-actions button:last-child{padding:7px 9px;white-space:nowrap;border:1px solid #7ad4ee;background:#111a28;color:#f4f8ff;font-size:clamp(12px,1vw,16px)}
.compact-actions button:is(:hover,:focus-visible){border-color:#08e9f5;background:#08e9f5;color:#061525}
.home-shelf button:focus-visible,.home-feature button:focus-visible,.compact-guide-row button:focus-visible{outline:2px solid #66f7ff;outline-offset:1px}
@media(max-width:590px){.viewer-shell.guide-mode .live-stage{grid-template-columns:1fr}.home-feature-copy h2{font-size:26px}}
@media(max-width:900px){.home-shelf-items{grid-auto-columns:calc((100% - 12px)/2)}.home-feature-copy{max-width:85%}}
@media(max-width:520px){.home-shelf-items{grid-auto-columns:76%}.home-feature-art{width:100%;opacity:.72}.home-feature-copy{max-width:100%}.home-feature::before{background:linear-gradient(90deg,#090f1ff2,#090f1f44),linear-gradient(0deg,#090f1fdb,transparent)}}
</style></head>''', 1)
HTML = HTML.replace("</head>", '<link rel="stylesheet" href="/cinema.css"></head>', 1)
HTML = HTML.replace("</body>", '<script src="/playback-diagnostics.js"></script></body>', 1)
HTML = HTML.replace('<span class="menu-label">Updates</span>', '<span class="menu-label">System</span>', 1)
HTML = HTML.replace('<strong>Updates</strong>', '<strong>System</strong>', 1)
HTML = HTML.replace('><h2>Updates</h2>', '><h2>System</h2>', 1)



class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import parse_qs, urlsplit
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/":
                return self.send(200, HTML.encode(), "text/html; charset=utf-8")
            if parsed.path == "/hls.js":
                return self.send(200, resource("web/hls.min.js").read_bytes(), "text/javascript")
            if parsed.path == "/playback-diagnostics.js":
                return self.send(200, resource("web/playback-diagnostics.js").read_bytes(), "text/javascript")
            if parsed.path == "/cinema.css":
                return self.send(200, resource("web/cinema.css").read_bytes(), "text/css; charset=utf-8")
            if parsed.path in {"/cinema-hero.webp", "/cinema-vhs-texture.webp"}:
                return self.send(200, resource("assets" + parsed.path).read_bytes(), "image/webp")
            if parsed.path == "/cinema-cassette-case.png":
                return self.send(200, resource("assets" + parsed.path).read_bytes(), "image/png",
                                 {"Cache-Control": "private, max-age=86400"})
            if parsed.path == "/app-icon.png":
                icon = "channel-forge-tv-icon.png"
                return self.send(200, resource(f"assets/{icon}").read_bytes(), "image/png")
            if parsed.path == "/api/update":
                value = latest_tv_release(); value.pop("assets", None)
                return self.send(200, json.dumps(value).encode(), "application/json")
            if parsed.path == "/api/local-creator":
                status = {"available": False}
                if not status["available"]:
                    status = local_creator()
                return self.send(200, json.dumps(status).encode(), "application/json")
            if parsed.path == "/api/movie-progress":
                return self.send(200, json.dumps(movie_progress()).encode(), "application/json")
            if parsed.path == "/api/update/progress":
                job_id = parse_qs(parsed.query).get("id", [""])[0]
                with UPDATE_LOCK:
                    value = UPDATE_JOBS.get(job_id)
                if not value:
                    raise ValueError("Update job was not found.")
                return self.send(200, json.dumps(value).encode(), "application/json")
            if parsed.path == "/guide-cache":
                url = approved_content_url(parse_qs(parsed.query).get("url", [""])[0])
                return self.send(200, guide_snapshot(url) or b"null", "application/json")
            if parsed.path == "/proxy":
                query = parse_qs(parsed.query)
                url = approved_content_url(query.get("url", [""])[0])
                request = urllib.request.Request(url, headers={"User-Agent": f"ChannelForge-TV/{VERSION}", "Accept-Encoding": "gzip"})
                timeout = 6 if query.get("scan") == ["1"] else 20
                limit = 64 * 1024 * 1024
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    if response.headers.get("Content-Encoding", "").lower() == "gzip":
                        with gzip.GzipFile(fileobj=response) as decoded:
                            body = decoded.read(limit + 1)
                    else:
                        body = response.read(limit + 1)
                    if len(body) > limit:
                        raise ValueError("Creator response exceeded 64 MB.")
                    if query.get("scan") == ["1"]:
                        guide_snapshot(url, body)
                    content_type = response.headers.get_content_type()
                    headers = {"Cache-Control": "private, max-age=300"} if response.status == 200 and content_type.startswith("image/") else None
                    return self.send(response.status, body, content_type, headers)
            if parsed.path == "/stream":
                url = approved_content_url(parse_qs(parsed.query).get("url", [""])[0])
                headers = {"Accept": "*/*", "User-Agent": f"ChannelForge-TV/{VERSION}"}
                if self.headers.get("Range"):
                    headers["Range"] = self.headers["Range"]
                with open_creator_stream(url, headers) as (response, final_url):
                    from diagnostic_metrics import playback_response_headers
                    extra = playback_response_headers(response.headers)
                    if response.status >= 400:
                        return self.send(response.status, json.dumps({"error": f"Creator returned HTTP {response.status}."}).encode(), "application/json", extra)
                    content_type = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip().lower()
                    if "mpegurl" in content_type or urlsplit(final_url).path.lower().endswith(".m3u8"):
                        manifest = response.read(2 * 1024 * 1024 + 1)
                        if len(manifest) > 2 * 1024 * 1024 or not manifest.decode("utf-8-sig").lstrip().startswith("#EXTM3U"):
                            raise ValueError("Creator returned an invalid HLS playlist.")
                        return self.send(response.status, rewrite_stream_manifest(manifest, final_url), "application/vnd.apple.mpegurl", extra)
                    extra["Accept-Ranges"] = response.headers.get("Accept-Ranges", "bytes")
                    if response.headers.get("Content-Range"):
                        extra["Content-Range"] = response.headers["Content-Range"]
                    return self.send_stream(response, content_type, extra)
            self.send_error(404)
        except VIEWER_DISCONNECTED:
            self.close_connection = True
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            self.send(code, json.dumps({"error": f"Creator returned HTTP {code}."}).encode(), "application/json")
        except Exception as exc:
            self.send(502, json.dumps({"error": str(exc)}).encode(), "application/json")

    def do_POST(self):
        from urllib.parse import parse_qs, urlsplit
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/api/movie-progress":
                if self.headers.get("X-ChannelForge-Viewer") != "movie-progress":
                    return self.send(403, b'{"error":"Open ChannelForge TV to save playback."}', "application/json")
                length = self.headers.get("Content-Length", "")
                if not length.isascii() or not length.isdecimal() or len(length) > 5 or not 0 < int(length) <= 32768:
                    raise ValueError("Invalid movie progress.")
                movie_progress(json.loads(self.rfile.read(int(length))))
                return self.send(200, b'{"ok":true}', "application/json")
            if parsed.path == "/api/update/start":
                return self.send(202, json.dumps({"job_id": start_tv_update()}).encode(), "application/json")
            if parsed.path == "/api/update/launch":
                job_id = parse_qs(parsed.query).get("id", [""])[0]
                launch_tv_update(job_id)
                return self.send(200, b'{"ok":true}', "application/json")
            self.send_error(404)
        except Exception as exc:
            self.send(400, json.dumps({"error": str(exc)}).encode(), "application/json")


    def send(self, status, body, content_type, extra_headers=None):
        try:
            self.send_content_headers(status, content_type, len(body), extra_headers or {})
            self.wfile.write(body)
        except VIEWER_DISCONNECTED:
            self.close_connection = True

    def send_content_headers(self, status, content_type, length, extra_headers):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", extra_headers.get("Cache-Control", "no-store"))
        for name, value in extra_headers.items():
            if value and name != "Cache-Control":
                self.send_header(name, value)
        self.end_headers()

    def send_stream(self, response, content_type, extra_headers):
        raw_length = response.headers.get("Content-Length", "")
        length = int(raw_length) if raw_length.isdecimal() else None
        remaining = 64 * 1024 * 1024
        if length is not None and length > remaining:
            raise ValueError("The media segment exceeded the 64 MB safety limit.")
        try:
            self.send_content_headers(response.status, content_type, length, extra_headers)
            while remaining:
                chunk = response.read1(min(16 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        except STREAM_ERRORS:
            self.close_connection = True

    def log_message(self, *_args):
        pass


class DesktopControls:
    def __init__(self, port: int, *, fullscreen=False):
        self._port = port
        self._player = None
        self._lock = threading.Lock()
        self._fullscreen = fullscreen
        self.health = {}

    def tv_fullscreen(self) -> bool:
        return self._fullscreen

    def remember_health(self, snapshot):
        if isinstance(snapshot, dict) and len(snapshot) < 80:
            self.health = snapshot

    def set_tv_fullscreen(self, active: bool) -> bool:
        with self._lock:
            if APP_WINDOW is not None and self._fullscreen != active:
                APP_WINDOW.toggle_fullscreen()
                self._fullscreen = active
            return self._fullscreen

    def play_in_linux_player(self, url: str, live: bool) -> dict:
        player = shutil.which("mpv") if sys.platform.startswith("linux") else None
        if not player:
            return {"error": "Install the current Linux package"}
        try:
            url = approved_content_url(url)
            if not re.fullmatch(r"/(?:live|vault)/[a-zA-Z0-9_-]+\.m3u8", urllib.parse.urlsplit(url).path):
                raise ValueError("The selection is not a ChannelForge stream.")
            args = [player, "--no-config", "--no-terminal", "--ytdl=no", "--network-timeout=12",
                    "--force-window=yes", "--title=ChannelForge TV"]
            if live:
                args.extend(["--input-default-bindings=no", "--osc=no"])
            source = f"http://127.0.0.1:{self._port}/stream?" + urllib.parse.urlencode({"url": url})
            with self._lock:
                self._stop_player()
                self._player = subprocess.Popen([*args, "--", source], stdin=subprocess.DEVNULL,
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, TypeError, ValueError) as exc:
            return {"error": str(exc)}
        return {"ok": True}

    def _stop_player(self):
        if self._player is not None and self._player.poll() is None:
            self._player.terminate()


def main():
    global APP_WINDOW
    import webview
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        threading.Thread(target=clean_windows_update_cache, args=(VERSION,), daemon=True).start()
    elif sys.platform.startswith("linux"):
        clean_linux_update_cache(UPDATE_DIR, VERSION)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="channel-forge-tv-server")
    thread.start()
    minimum = (760, 480) if sys.platform.startswith("linux") else (1100, 700)
    fullscreen = sys.platform == "win32" or sys.platform.startswith("linux")
    controls = DesktopControls(server.server_port, fullscreen=fullscreen)
    from public_diagnostics import PublicDiagnostics
    reports = PublicDiagnostics(UPDATE_DIR.parent, "tv", VERSION)
    reports.install_crash_handler()
    APP_WINDOW = webview.create_window(APP_TITLE, url=url, js_api=controls, fullscreen=fullscreen,
                                      width=1500, height=900, min_size=minimum, background_color="#080414")
    failure = None
    try:
        if sys.platform == "win32":
            webview.start(debug=False, icon=str(resource("assets/channel-forge-tv.ico")))
        else:
            start_webview(webview, application_id="channelforge-tv",
                          icon=resource("assets/channel-forge-tv-icon.png"))
    except Exception as error:
        failure = error
        raise
    finally:
        controls._stop_player()
        server.shutdown()
        if reports:
            reports.submit(controls.health, failure=failure)
        server.server_close()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()

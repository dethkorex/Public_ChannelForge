"""ChannelForge TV desktop viewer for the public creator directory."""
from __future__ import annotations

import gzip
import http.client
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

from linux_runtime import linux_install_command, linux_release_asset, start_webview
from windows_distribution import clean_windows_update_cache, published_hash, verified_download, verified_windows_setup, windows_setup_asset, windows_update_cache

VERSION = "3.5.14"
DIRECTORY = "https://channelforge-directory.fresh-anorak.workers.dev/v1/catalog"
DIRECTORY_FALLBACK = "https://raw.githubusercontent.com/dethkorex/Public_ChannelForge/main/directory/catalog.json"
LOCAL_CREATOR = "http://127.0.0.1:8787"
GITHUB = "https://api.github.com/repos/dethkorex/Public_ChannelForge/releases/latest"
UPDATE_DIR = (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "public-channelforge" / "updates"
              if sys.platform.startswith("linux") else windows_update_cache())
UPDATE_JOBS: dict[str, dict] = {}
UPDATE_LOCK = threading.RLock()
APP_WINDOW = None
STREAM_ERRORS = (OSError, http.client.HTTPException)
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


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.lstrip("v").split(".") if part.isdigit())


def github_json() -> dict:
    request = urllib.request.Request(GITHUB, headers={"Accept": "application/vnd.github+json", "User-Agent": f"ChannelForge-TV/{VERSION}"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def latest_tv_release() -> dict:
    payload = github_json()
    latest = str(payload.get("tag_name", "")).lstrip("v")
    assets = {str(item.get("name", "")): str(item.get("browser_download_url", "")) for item in payload.get("assets", [])}
    return {"current_version": VERSION, "latest_version": latest, "update_available": bool(latest) and version_tuple(latest) > version_tuple(VERSION), "release_url": str(payload.get("html_url", "")), "notes": str(payload.get("body", ""))[:6000], "assets": assets}


def approved_download(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}:
        raise ValueError("The update URL is not an approved GitHub address.")
    return url


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


def local_creator() -> dict:
    headers = {"Accept": "application/json", "User-Agent": f"ChannelForge-TV/{VERSION}"}
    try:
        try:
            request = urllib.request.Request(f"{LOCAL_CREATOR}/api/local-creator", headers=headers)
            with urllib.request.urlopen(request, timeout=1) as response:
                status = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            error.close()
            request = urllib.request.Request(f"{LOCAL_CREATOR}/api/status", headers=headers)
            with urllib.request.urlopen(request, timeout=2) as response:
                status = json.load(response)
        count = status.get("channel_count")
        if not isinstance(count, int):
            count = len(status.get("channels") or [])
        config = status.get("config") or {}
        name = (status.get("name") or (config.get("creator_profile") or {}).get("name")
                or (config.get("plex_profile") or {}).get("server_name") or "My ChannelForge Studio")
    except Exception:
        return {"available": False}
    return {"available": True, "creator": {
        "id": "local-creator", "name": str(name),
        "description": f"{count} local channel{'s' if count != 1 else ''} • Creator connected",
        "guide_url": f"{LOCAL_CREATOR}/exports/guide.xml",
        "playlist_url": f"{LOCAL_CREATOR}/exports/channels.m3u",
        "status_url": f"{LOCAL_CREATOR}/api/public/channels",
        "vault_url": f"{LOCAL_CREATOR}/api/public/vault",
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
                result = {"version": version, "path": str(destination), "command": linux_install_command(destination)}
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
    if sys.platform.startswith("linux"):
        raise ValueError("Use the verified package's install command to finish this Linux update.")
    with UPDATE_LOCK:
        result = UPDATE_JOBS.get(job_id, {}).get("result", {})
    path = Path(str(result.get("path", ""))).resolve()
    if not path.is_file() or path.parent != UPDATE_DIR.resolve():
        raise ValueError("No verified ChannelForge TV update is ready.")
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
<style>*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#2b0752,#060410 43%);color:#fff;font-family:Segoe UI,sans-serif;overflow-x:hidden}header{height:92px;display:flex;align-items:center;padding:0 4vw;border-bottom:2px solid #13e4ff;gap:28px}h1{color:#fff22b;letter-spacing:4px;margin:0}.tag{color:#4fffd0;border:1px solid #19e9ff;padding:5px 10px}.update{margin-left:auto;background:#15112c;color:white;border:1px solid #19e9ff;padding:12px 18px;border-radius:7px}.wrap{padding:28px 4vw}.creators,.live,.vault{display:flex;gap:14px;overflow-x:auto;padding:10px 6px 25px}.creator,.channel{min-width:260px;background:#16132c;border:1px solid #41376a;border-radius:9px;padding:16px;color:white}.creator:focus,.channel:focus{outline:4px solid #fff22b}.vault{min-height:410px;align-items:center;perspective:1000px;gap:0;padding-left:34px}.tape{position:relative;flex:0 0 210px;height:340px;margin-left:-28px;background:#0d0a1c;border:3px solid #14dcf5;border-left-width:15px;padding:10px;box-shadow:16px 10px 18px #000c,5px 0 #44244f inset;transition:transform .22s,filter .22s;color:white;transform:rotateY(-22deg) translateZ(-35px);transform-style:preserve-3d;filter:brightness(.7)}.tape:nth-child(even){transform:rotateY(18deg) translateZ(-25px)}.tape:focus,.tape:hover{outline:none;border-color:#efff1c;transform:rotateY(0) translateY(-10px) translateZ(95px) scale(1.08);filter:brightness(1);z-index:5}.tape img{width:172px;height:255px;object-fit:cover;background:linear-gradient(145deg,#3a2858,#130d24)}.tape b{display:block;padding-top:8px}.pink{color:#ff3eb8}.cyan{color:#28eaff}video{display:none;position:fixed;inset:0;width:100%;height:100%;background:#000;z-index:10}video.playing{display:block}.close{display:none;position:fixed;z-index:11;right:25px;top:20px;padding:12px 18px;background:#111d;color:white;border:1px solid white}.close.show{display:block}.empty{color:#a89abb;padding:25px}.update-panel{position:fixed;z-index:20;right:28px;top:108px;width:390px;padding:20px;border:2px solid #18e7ff;border-radius:12px;background:#0c1028;box-shadow:0 18px 60px #000}.update-panel h3{color:#fff22b;margin-top:0}.update-panel progress{width:100%;height:18px;accent-color:#4fffd0}.update-panel button{margin-top:12px;padding:10px 14px;background:#203659;color:white;border:1px solid #18e7ff;border-radius:6px}.update-panel button.primary{background:#9b3d99;border-color:#ff4dd2}</style></head><body>
<style>.categories{display:flex;gap:10px;margin:0 0 12px}.categories span{padding:7px 13px;border:1px solid #ff3eb8;border-radius:999px;color:#8ff;background:#0f1330}.vault-layout{display:grid;grid-template-columns:48% 52%;gap:24px;min-height:440px}.vault{display:flex;flex-direction:column;perspective:900px;gap:7px;height:440px;overflow-y:auto;padding:8px 18px 20px 8px}.tape,.tape:nth-child(even){display:grid;grid-template-columns:78px 1fr auto;grid-template-rows:1fr 1fr;align-items:center;flex:none;width:100%;height:72px;margin:0;padding:6px 12px 6px 5px;border:1px solid #855938;border-left:9px solid #14dcf5;background:linear-gradient(90deg,#392416,#17131a 70%);box-shadow:8px 5px 12px #0009;transform:rotateX(-5deg) translateZ(-15px);filter:brightness(.82);text-align:left}.tape:focus,.tape:hover{transform:rotateX(0) translateZ(65px) scale(1.025);border:3px solid #efff1c;border-left-width:9px;filter:none}.tape img{grid-row:1/3;width:66px;height:56px;object-fit:cover;object-position:20% center;margin-right:12px;border:1px solid #f3c55b}.tape b{padding:0;font-size:17px}.tape small{color:#c8b89f}.tape i{grid-column:3;grid-row:1/3;background:#66ff9e;color:#07130c;padding:7px 10px;font-style:normal;font-weight:800}.selection{height:440px;border:1px solid #12dff9;border-radius:18px;padding:18px;background:linear-gradient(145deg,#11123b,#070a1d);display:grid;grid-template-columns:210px 1fr;gap:22px;box-shadow:0 0 36px #00c8ff18 inset}.selection>img{width:210px;height:315px;object-fit:cover;border:2px solid #18dcf4;border-radius:9px;box-shadow:16px 18px 28px #000b}.selection h2{color:#fff22d;font-size:29px;margin-top:8px}.selection p{line-height:1.55;min-height:115px}.selection button{width:100%;padding:14px;background:#a94d06;color:#fff1a0;border:2px solid #ffa20c;border-radius:7px;font-weight:900}.selection small{display:block;color:#8d829f;margin-top:12px}body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:50;background:repeating-linear-gradient(0deg,#0000 0 3px,#0000001c 4px)}header{position:sticky;top:0;z-index:8;background:#070817eF;backdrop-filter:blur(18px);box-shadow:0 12px 35px #0008}.brand-lockup{display:flex;align-items:center;gap:13px}.brand-lockup img{width:57px;height:57px;object-fit:contain;filter:drop-shadow(0 0 16px #13dfff66)}.brand-lockup h1{font-size:25px}.brand-lockup small{color:#57edff;letter-spacing:3px}.hero{position:relative;min-height:230px;display:flex;align-items:center;justify-content:space-between;overflow:hidden;margin-bottom:30px;padding:36px 42px;border:1px solid #652e9e;border-radius:22px;background:radial-gradient(circle at 78% 35%,#e12b8d55,transparent 25%),linear-gradient(120deg,#161149,#090a20 58%,#1d0929);box-shadow:0 25px 70px #0008}.hero::after{content:"";position:absolute;right:-70px;width:350px;height:350px;border:45px solid #11dcff1c;border-radius:50%;box-shadow:0 0 0 35px #fd2aa214}.eyebrow{color:#58f4ff;font-weight:900;letter-spacing:3px}.hero h2{font-size:46px;line-height:1.02;margin:10px 0;color:#fff}.hero p{max-width:650px;color:#bdc6dc}.signal-card{position:relative;z-index:1;display:grid;place-items:center;min-width:180px;height:130px;border:1px solid #ff42c8;border-radius:18px;background:#0b0924cc}.signal-card b{font-size:34px;color:#78ff9d}.signal-card span{color:#ffdc3f;letter-spacing:2px}.section-heading{display:flex;justify-content:space-between;align-items:end;margin-top:14px}.section-heading small{color:#8c96b8}.creator,.channel{background:linear-gradient(145deg,#1b1840,#101128);border-color:#403a71;box-shadow:0 12px 30px #0006;transition:.18s transform,.18s border-color}.creator:hover,.creator:focus,.channel:hover,.channel:focus{transform:translateY(-5px);border-color:#45eaff}.wrap{max-width:1600px;margin:auto}@media(max-width:900px){.vault-layout{grid-template-columns:1fr}.selection{display:none}.hero{padding:28px}.hero h2{font-size:34px}.signal-card{display:none}.brand-lockup small,.tag{display:none}}</style><header><div class="brand-lockup"><img src="/app-icon.png" alt=""><div><h1>CHANNELFORGE TV</h1><small>THE COMMUNITY DIAL</small></div></div><span class="tag">REWIND CINEMA</span><button id="update" class="update">UPDATES • v__VERSION__</button></header><aside id="update-panel" class="update-panel" hidden><h3>CHANNELFORGE TV UPDATE</h3><p id="update-message">Checking GitHub Releases…</p><progress id="update-progress" max="100" value="0"></progress><small id="update-bytes"></small><button id="restart-update" class="primary" hidden>OPEN VERIFIED UPDATE</button><button id="dismiss-update">NOT NOW</button></aside><main class="wrap"><section class="hero"><div><span class="eyebrow">COMMUNITY CHANNELS • ON DEMAND</span><h2>Turn the dial.<br>Find something unforgettable.</h2><p>Jump into live creator channels or browse every connected movie shelf from one cinematic TV interface.</p></div><div class="signal-card"><b>● LIVE</b><span>PUBLIC GUIDE</span></div></section><div class="section-heading"><h2>CREATOR STUDIOS</h2><small>Choose a studio to tune its channels and movies</small></div><div id="creators" class="creators"><span class="empty">Connecting to the public directory…</span></div><div class="section-heading"><h2 class="cyan">LIVE CHANNELS</h2><small>Select a channel to start watching</small></div><div id="live" class="live"></div><div class="section-heading"><h2 class="pink">REWIND CINEMA</h2><small>BROWSE THE MOVIE SHELF</small></div><nav class="categories"><span>CONTINUE WATCHING</span><span>80S BLOCKBUSTERS</span><span>90S ESSENTIALS</span><span>CREATOR PICKS</span></nav><div class="vault-layout"><div id="vault" class="vault"></div><aside id="selection" class="selection"><span class="empty">Choose a movie to see its details.</span></aside></div></main><video id="player" controls autoplay></video><button id="close" class="close">BACK TO REWIND CINEMA</button><script src="/hls.js"></script><script>
const $=s=>document.querySelector(s), esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let hls,vaultItems=[],vaultVisible=[],vaultRenderCount=0,vaultSearch='',vaultFilter='all',vaultGenre='all',vaultRating='all',vaultSort='title',allCreators=[],liveChannels=[],activeStudio='all',guideSearch='',selectedCreator=null,liveMode=false,guideWindowOffset=0,activeStreamUrl='',activeMedia=null,playerMini=false,favoriteChannels=readFavorites('cf-favorite-channels'),favoriteCreators=readFavorites('cf-favorite-creators');
async function creatorResponse(url, scan=false, signal){
    const response=await fetch('/proxy?url='+encodeURIComponent(url)+(scan?'&scan=1':''),{signal});
    if(!response.ok)throw Error(await response.text());
    return response;
}
async function json(url, scan=false, signal){return (await creatorResponse(url,scan,signal)).json()}
async function localJson(path,options={}){let r=await fetch(path,options),value=await r.json();if(!r.ok)throw Error(value.error||'Request failed');return value}
async function checkUpdate(onLaunch=false){try{let r=await localJson('/api/update'),version=r.latest_version||r.current_version||'__VERSION__';if($('#home-release-version'))$('#home-release-version').textContent='v'+version;if($('#home-changelog'))$('#home-changelog').textContent=r.notes||'Playback, guide, discovery, and application reliability improvements.';if($('#home-update-state'))$('#home-update-state').textContent=r.update_available?'A verified update is available.':'You are running the newest public release.';if(!r.update_available){$('#update').textContent='UP TO DATE ✓ • v__VERSION__';return}$('#update').textContent='UPDATE v'+r.latest_version+' AVAILABLE';if(!onLaunch||confirm('ChannelForge TV v'+r.latest_version+' is available. Download and verify it now?'))downloadUpdate()}catch(e){$('#update').textContent='OFFLINE • v__VERSION__';if($('#home-update-state'))$('#home-update-state').textContent='Release check is offline. The installed application still works normally.';if($('#home-changelog'))$('#home-changelog').textContent='v__VERSION__\n• Restored Windows video playback\n• Added automatic channel scanning and offline filtering\n• Added a full live program guide\n• Rebuilt Rewind Cinema as a vertical VHS shelf'} }
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
                command.value=job.result?.command||'';command.hidden=!command.value;
                action.textContent=command.value?'COPY INSTALL COMMAND':'OPEN VERIFIED UPDATE';action.hidden=false;
                if(command.value)message.textContent='Run the verified install command in your terminal.';
                return;
            }
            await new Promise(resolve=>setTimeout(resolve,400));
        }
    }catch(error){message.textContent=error.message;bytes.textContent=''}
}
function parsePlaylist(text,creator){let result=[],pending=null;for(const line of text.split(/\r?\n/)){if(line.startsWith('#EXTINF:')){let name=line.slice(line.lastIndexOf(',')+1),id=(line.match(/tvg-id="([^"]+)"/)||[])[1]||'',number=(line.match(/tvg-chno="([^"]+)"/)||[])[1]||'',logo=(line.match(/tvg-logo="([^"]+)"/)||[])[1]||'';pending={id,name,number,logo_url:logo,programs:[],online:true,creator_name:creator.name}}else if(line&&!line.startsWith('#')&&pending){result.push({...pending,stream_url:new URL(line,creator.playlist_url).href});pending=null}}return result}
function programAt(channel,moment=Date.now()){return(channel?.programs||[]).find(program=>Date.parse(program.start)<=moment&&moment<Date.parse(program.stop))}
const GUIDE_STEP=30*60*1000;
const guideTime=new Intl.DateTimeFormat(undefined,{hour:'numeric',minute:'2-digit'});
function formatDuration(milliseconds){
    const seconds=Math.floor(Math.max(0,milliseconds)/1000);
    return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');
}
function tuneGuideChannel(channel){
    play(channel.stream_url,{kind:'live',channel,title:programAt(channel)?.title||channel.name});
}
function guideTiles(programs,start,stop){
    const tiles=[], target=(stop-start)*.044;
    for(const program of programs||[]){
        const begin=Math.max(start,Date.parse(program.start)), end=Math.min(stop,Date.parse(program.stop));
        if(!(end>begin))continue;
        const previous=tiles.at(-1);
        if(previous&&previous.end===begin&&previous.end-previous.begin<target&&end-begin<target){
            previous.end=end;
            previous.programs.push(program);
        }else{
            tiles.push({begin,end,programs:[program]});
        }
    }
    return tiles;
}
function showGuideTile(channel,tile){
    if(tile.programs.length===1)return showProgramInfo(channel,tile.programs[0]);
    const dialog=$('#program-dialog'), body=$('#program-dialog-body');
    body.innerHTML='<span class="eyebrow">CH '+esc(channel.number||'—')+'</span><h2>'+esc(channel.name)+'</h2><div class="short-programs"></div>';
    const list=body.querySelector('.short-programs');
    for(const program of tile.programs){
        const button=document.createElement('button');
        button.textContent=guideTime.format(Date.parse(program.start))+' · '+program.title;
        button.onclick=()=>showProgramInfo(channel,program);
        list.append(button);
    }
    if(!dialog.open)dialog.showModal();
}
function renderNowPlaying(channel,now,tuned){
    const preview=$('#live');
    if(!channel){
        preview.innerHTML='<article class="guide-preview"><span><small>LIVE GUIDE</small><b>No channels to show</b><em>Try another studio or scan for channels.</em></span></article>';
        return;
    }
    const current=programAt(channel,now);
    const poster=current?.poster_url||channel.logo_url||'';
    const backdrop=current?.backdrop_url||poster;
    const start=Date.parse(current?.start), stop=Date.parse(current?.stop);
    const duration=stop-start, elapsed=now-start;
    const progress=duration>0?Math.max(0,Math.min(100,100*elapsed/duration)):0;
    const tags=[current?.genres?.[0],current?.content_rating].filter(Boolean);
    preview.innerHTML='<article class="guide-preview"><span><small><i class="guide-signal"></i>'+(tuned?'YOU ARE WATCHING':'NOW PLAYING')+'<mark>///</mark></small>'
        +'<b>'+esc(current?.title||channel.name)+'</b><em><span>'+esc(channel.creator_name||'ChannelForge Creator')+'</span><span>CH '+esc(channel.number||'—')+'</span>'
        +tags.map(tag=>'<span class="guide-tag">'+esc(tag)+'</span>').join('')+'</em>'
        +(duration>0?'<div class="guide-progress"><progress max="100" value="'+progress+'"></progress><div><time>'+formatDuration(elapsed)+'</time><time>'+formatDuration(duration)+'</time></div></div>':'')
        +'</span></article>';
    if(backdrop){
        try{
            const address=new URL(backdrop,location.href);
            if(['http:','https:'].includes(address.protocol)){
                const safeAddress=address.href.replaceAll(String.fromCharCode(34),'%22').replaceAll(String.fromCharCode(92),'%5C');
                preview.firstElementChild.style.setProperty('--guide-backdrop','url("'+safeAddress+'")');
            }
        }catch(_error){}
    }
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
    const tuned=liveMode&&(liveChannels.find(channel=>channel.stream_url===activeStreamUrl)||activeMedia?.channel);
    const featured=tuned||channels.find(channel=>programAt(channel,now))||channels[0];
    renderNowPlaying(featured,now,Boolean(tuned));
    const steps=innerWidth<1100?4:8;
    const start=(Math.floor(now/GUIDE_STEP)+guideWindowOffset)*GUIDE_STEP, stop=start+steps*GUIDE_STEP;
    const board=$('#guide-board'), guide=$('#tv-guide');
    board.style.setProperty('--guide-steps',steps);
    $('#guide-window-label').textContent=guideWindowOffset?'GUIDE TIME':'ON NOW';
    $('#guide-earlier').disabled=guideWindowOffset<=-1;
    $('#guide-later').disabled=guideWindowOffset>=4;
    $('#guide-times').innerHTML='<div class="guide-channel-head"><span>CH</span><span>CHANNEL</span></div><div class="guide-ticks">'
        +Array.from({length:steps},(_,i)=>'<time>'+guideTime.format(start+i*GUIDE_STEP)+'</time>').join('')+'</div>';
    const rows=document.createDocumentFragment();
    for(const channel of channels){
        const row=document.createElement('section'), tiles=guideTiles(channel.programs,start,stop);
        row.className='guide-row'+(tuned&&channel.stream_url===tuned.stream_url?' tuned':'');
        row.innerHTML='<div class="guide-station"><button type="button" class="station-tune" aria-label="Watch '+esc(channel.name)+'"><strong>'+esc(channel.number||'—')+'</strong>'
            +(channel.logo_url?'<img src="'+esc(channel.logo_url)+'" alt="">':'<i class="station-icon" aria-hidden="true"></i>')
            +'<span><b>'+esc(channel.name)+'</b><small>'+esc(channel.creator_name||'ChannelForge Creator')+'</small></span></button><button type="button" class="station-favorite" aria-label="Favorite '+esc(channel.name)+'" aria-pressed="'+favoriteChannels.has(channel.stream_url)+'">'+(favoriteChannels.has(channel.stream_url)?'★':'☆')+'</button></div>'
            +'<div class="guide-programs">'+(tiles.length?tiles.map((tile,index)=>{
                const program=tile.programs[0], last=tile.programs.at(-1);
                const width=100*(tile.end-tile.begin)/(stop-start), left=100*(tile.begin-start)/(stop-start);
                const onNow=tile.begin<=now&&now<tile.end;
                const time=guideTime.format(Date.parse(program.start))+'–'+guideTime.format(Date.parse(last.stop));
                const title=tile.programs.length>1?program.title+' +'+(tile.programs.length-1):program.title;
                return '<button type="button" class="guide-program'+(onNow?' current':'')+'" data-program="'+index+'" title="'+esc(title+' · '+time)+'" style="left:'+left+'%;width:'+width+'%"><b>'+esc(title)+'</b><time>'+time+'</time></button>';
            }).join(''):'<span class="guide-unavailable">Schedule unavailable</span>')+'</div>';
        row.querySelector('.station-tune').onclick=()=>tuneGuideChannel(channel);
        row.querySelector('.station-favorite').onclick=event=>{
            const label=event.currentTarget.getAttribute('aria-label');
            toggleFavorite('channel',channel.stream_url);
            [...guide.querySelectorAll('.station-favorite')].find(button=>button.getAttribute('aria-label')===label)?.focus();
        };
        row.querySelectorAll('.guide-program').forEach((button,index)=>button.onclick=()=>showGuideTile(channel,tiles[index]));
        rows.append(row);
    }
    guide.replaceChildren(rows);
    if(!channels.length)guide.innerHTML='<p class="guide-unavailable">No online channels match this view.</p>';
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
function renderFavorites(){let screen=$('#favorites-list');if(!screen)return;let channels=liveChannels.filter(channel=>favoriteChannels.has(channel.stream_url)),creators=allCreators.filter(creator=>favoriteCreators.has(creator.name));screen.innerHTML=(channels.length?'<h3>Favorite channels</h3>'+channels.map(channel=>'<button class="favorite-card" data-favorite-stream="'+esc(channel.stream_url)+'"><b>CH '+esc(channel.number||'—')+' · '+esc(channel.name)+'</b><small>'+esc(channel.creator_name||'ChannelForge Creator')+'</small></button>').join(''):'<p class="empty">No favorite channels yet.</p>')+(creators.length?'<h3>Favorite creators</h3>'+creators.map(creator=>'<button class="favorite-card" data-favorite-creator="'+esc(creator.name)+'"><b>★ '+esc(creator.name)+'</b><small>'+esc(creator.description||'Creator studio')+'</small></button>').join(''):'')}
function showProgramInfo(channel,program){
    let dialog=$('#program-dialog'),start=new Date(program.start),stop=new Date(program.stop),onNow=start<=new Date()&&new Date()<stop,art=program.poster_url||program.backdrop_url||channel.logo_url||'';
    $('#program-dialog-body').innerHTML='<div class="program-dialog-layout">'+(art?'<img class="program-poster" src="'+esc(art)+'" alt="'+esc(program.title)+' artwork">':'')+'<div><span class="eyebrow">'+(onNow?'● ON NOW':'UPCOMING')+' · CH '+esc(channel.number||'—')+'</span><h2>'+esc(program.title)+'</h2><p class="program-meta">'+start.toLocaleString([], {weekday:'short',hour:'numeric',minute:'2-digit'})+' – '+stop.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})+' · '+esc(channel.name)+'</p><p>'+esc(program.summary||program.description||'Program information supplied by the creator guide.')+'</p><div class="program-actions"><button id="program-watch" class="primary">'+(onNow?'WATCH NOW':'WATCH CHANNEL')+'</button><button id="favorite-channel">'+(favoriteChannels.has(channel.stream_url)?'★ FAVORITED':'☆ FAVORITE CHANNEL')+'</button><button id="favorite-creator">'+(favoriteCreators.has(channel.creator_name)?'★ CREATOR FAVORITED':'☆ FAVORITE CREATOR')+'</button></div></div></div>';
    $('#program-watch').onclick=()=>{dialog.close();play(channel.stream_url,{kind:'live',channel,title:programAt(channel)?.title||channel.name})};
    $('#favorite-channel').onclick=()=>{toggleFavorite('channel',channel.stream_url);showProgramInfo(channel,program)};
    $('#favorite-creator').onclick=()=>{toggleFavorite('creator',channel.creator_name);showProgramInfo(channel,program)};
    if(!dialog.open)dialog.showModal();
}
let directoryScan=null, pendingGuideRender=0;
const channelsByStudio=new Map();
let creatorsSignature='';
function refreshGuideFromStudios(){
    const seen=new Set();
    liveChannels=allCreators.flatMap(creator=>channelsByStudio.get(creator.playlist_url)||[])
        .filter(channel=>channel.stream_url&&!seen.has(channel.stream_url)&&seen.add(channel.stream_url))
        .sort((a,b)=>String(a.number||'9999').localeCompare(String(b.number||'9999'),undefined,{numeric:true})||a.name.localeCompare(b.name));
    if(!pendingGuideRender)pendingGuideRender=requestAnimationFrame(()=>{
        pendingGuideRender=0;
        if(document.body.classList.contains('guide-mode'))renderLiveGuide();
        else renderFavorites();
    });
}
function sameStudio(local, remote){
    return local.playlist_url===remote.playlist_url||local.public_playlist_url===remote.playlist_url
        ||Boolean(local.public_id&&local.public_id===remote.id);
}
function creatorMedia(url, creator){
    if(!url||!creator.local)return url||'';
    try{
        const target=new URL(url);
        if(/^\/(?:api\/(?:plex|local)\/image$|user-assets\/|live\/|vault\/)/.test(target.pathname)){
            return new URL(target.pathname+target.search,creator.status_url).href;
        }
    }catch(_error){}
    return url;
}
function adoptCreators(creators){
    allCreators=creators;
    const current=creators.find(creator=>creator.playlist_url===selectedCreator?.playlist_url);
    const next=current||creators[0]||null;
    if(next?.vault_url!==selectedCreator?.vault_url){
        vaultController?.abort();
        selectedVaultUrl='';
        vaultItems=[];
    }
    selectedCreator=next;
    const urls=new Set(creators.map(creator=>creator.playlist_url));
    for(const key of channelsByStudio.keys())if(!urls.has(key))channelsByStudio.delete(key);
    const signature=JSON.stringify(creators.map(c=>[c.id,c.playlist_url,c.vault_url,c.name,c.description,c.logo_url,c.local]));
    if(signature!==creatorsSignature || !$('#creators').hasChildNodes()){
        creatorsSignature=signature;
        const list=$('#creators'),fragment=document.createDocumentFragment();
        list.textContent='';
        for(const c of creators){
            const b=document.createElement('button');
            b.className='creator';
            b.innerHTML=(c.logo_url?'<img loading="lazy" src="'+esc(c.logo_url)+'" style="width:46px;height:46px;object-fit:contain;float:left;margin-right:12px">':'')+'<b>'+esc(c.name)+'</b><br><small>'+esc(c.description)+(c.local?' • THIS PC':'')+'</small>';
            b.onclick=()=>{selectedCreator=c;showScreen('cinema');studio(c,true)};
            fragment.append(b);
        }
        if(!creators.length)list.innerHTML='<span class="empty">No public creator studios are online right now.</span>';
        else list.append(fragment);
    }
    if(!selectedCreator){
        $('#vault').innerHTML='<span class="empty">No movie libraries are online right now.</span>';
        $('#selection').textContent='';
    }else if(!document.querySelector('.vault-layout').hidden&&!vaultItems.length)studio(selectedCreator);
    refreshGuideFromStudios();
}
async function discoverDirectory(url){
    const controller=new AbortController();
    const deadline=setTimeout(()=>controller.abort(),6500);
    try {
        return (await json(url,true,controller.signal)).creators||[];
    } finally {
        clearTimeout(deadline);
    }
}
async function publicDirectory(){
    try {
        return await discoverDirectory('__DIRECTORY__');
    } catch (_error) {
        return discoverDirectory('__DIRECTORY_FALLBACK__');
    }
}
async function scanStudio(creator){
    let channels=[];
    const controller=new AbortController();
    const deadline=setTimeout(()=>controller.abort(),6500);
    try {
        if(creator.status_url){
            const address=new URL(creator.status_url);
            address.searchParams.set('guide','extended');
            const value=await json(address.href,true,controller.signal);
            channels=(value.channels||[]).filter(channel=>channel.online)
                .map(channel=>({...channel,creator_name:creator.name,
                    stream_url:creatorMedia(channel.stream_url,creator),
                    logo_url:creatorMedia(channel.logo_url,creator),
                    programs:(channel.programs||[]).map(program=>({...program,
                        poster_url:creatorMedia(program.poster_url,creator),
                        backdrop_url:creatorMedia(program.backdrop_url,creator)}))
                }));
        }else{
            const response=await creatorResponse(creator.playlist_url,true,controller.signal);
            channels=parsePlaylist(await response.text(),creator);
        }
    } catch (_error) {
        // Other studios remain available if this origin cannot be reached.
    } finally {
        clearTimeout(deadline);
    }
    channelsByStudio.set(creator.playlist_url,channels);
    refreshGuideFromStudios();
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
        const pending=allCreators.filter(c=>!c.local);
        let next=0;
        await Promise.all(Array.from({length:Math.min(8,pending.length)},async()=>{
            while(next<pending.length)await check(pending[next++]);
        }));
        await localLookup;
        if(local)await check(local);
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
    const bounds={ '80s':[1980,1990], '90s':[1990,2000], '2000s':[2000,2010], recent:[2010,Infinity] }[vaultFilter];
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
    vaultVisible=filteredVault();
    $('#movie-count').textContent=vaultVisible.length+' movie'+(vaultVisible.length===1?'':'s');
    document.querySelectorAll('[data-movie-filter]').forEach(button=>button.classList.toggle('active',button.dataset.movieFilter===vaultFilter));
    renderVaultPage(focusFirst);
}
function appendVaultBatch(amount=24){
    const box=$('#vault'), fragment=document.createDocumentFragment(), end=Math.min(vaultVisible.length,vaultRenderCount+amount);
    for(const x of vaultVisible.slice(vaultRenderCount,end)){
        const b=document.createElement('button');
        b.className='tape';
        const thumbnail=x.thumbnail_url||x.poster_url;
        b.innerHTML='<span class="tape-ridge"></span>'+(thumbnail?'<img loading="lazy" decoding="async" src="'+esc(thumbnail)+'" alt="">':'<span class="empty">&nbsp;</span>')+'<span class="tape-label"><b>'+esc(x.title)+'</b><small><span>CF HOME VIDEO • VHS / HI-FI</span><mark>'+esc(x.year||'N/A')+'</mark></small></span><i>INSERT ▷</i>';
        b.querySelector('img')?.addEventListener('error',event=>hideFailedPoster(event.currentTarget,x.poster_url));
        b.onfocus=b.onmouseenter=()=>selectTape(x);
        b.onclick=()=>play(x.stream_url,{kind:'movie',title:x.title,movie:x});
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
        box.innerHTML='<span class="empty">'+(vaultItems.length?'No movies match these filters.':'No movies are available in this studio.')+'</span>';
        selectedTape=null;
        $('#selection').textContent='';
        return;
    }
    appendVaultBatch();
    selectTape(vaultVisible[0]);
    if(focusFirst)box.firstElementChild?.focus({preventScroll:true});
}
function renderVaultBatch(){renderVaultPage(false)}
async function studio(c, force=false){
    if(!c?.vault_url||loadingVaultUrl===c.vault_url)return;
    const updating=selectedVaultUrl===c.vault_url;
    if(updating&&(!force||Date.now()-vaultLoadedAt<5000))return;
    vaultController?.abort();
    const controller=new AbortController();
    vaultController=controller;
    loadingVaultUrl=c.vault_url;
    if(!updating){
        selectedVaultUrl='';
        vaultItems=[];
        indexVault(vaultItems);
        selectedTape=null;
        $('#movie-count').textContent='';
        $('#selection').textContent='';
        $('#vault').innerHTML='<span class="empty">Loading movies and posters…</span>';
    }
    try{
        const value=await json(c.vault_url,false,controller.signal);
        if(controller.signal.aborted)return;
        vaultItems=Array.isArray(value.items)?value.items.filter(item=>item?.type==='movie')
            .map(item=>({...item,poster_url:creatorMedia(item.poster_url,c),
                thumbnail_url:creatorMedia(item.thumbnail_url,c),stream_url:creatorMedia(item.stream_url,c)})):[];
        indexVault(vaultItems);
        selectedVaultUrl=c.vault_url;
        vaultLoadedAt=Date.now();
        if(!updating){
            vaultSearch='';
            vaultFilter=vaultGenre=vaultRating='all';
            vaultSort='title';
            $('#movie-search').value='';
        }
        const genre=$('#movie-genre'),rating=$('#movie-rating');
        genre.innerHTML='<option value="all">ALL GENRES</option>'+[...new Set(vaultItems.flatMap(item=>item.genres||[]).filter(Boolean))].sort().map(value=>'<option value="'+esc(value)+'">'+esc(value).toUpperCase()+'</option>').join('');
        rating.innerHTML='<option value="all">ALL RATINGS</option>'+[...new Set(vaultItems.map(item=>item.content_rating||'Unrated'))].sort().map(value=>'<option value="'+esc(value)+'">'+esc(value)+'</option>').join('');
        genre.value=vaultGenre;
        rating.value=vaultRating;
        if(!genre.value)genre.value=vaultGenre='all';
        if(!rating.value)rating.value=vaultRating='all';
        $('#movie-sort').value=vaultSort;
        applyVaultFilters();
    }catch(error){
        if(!controller.signal.aborted&&!updating)$('#vault').innerHTML='<span class="empty">Could not load this studio: '+esc(error.message)+'</span>';
    }finally{
        if(vaultController===controller)loadingVaultUrl='';
    }
}
function hideFailedPoster(image,fallback=''){
    if(fallback&&image.getAttribute('src')!==fallback){image.src=fallback;return}
    image.style.visibility='hidden';
}
function selectTape(x){if(selectedTape===x)return;selectedTape=x;$('#selection').innerHTML='<div class="vhs-box">'+(x.poster_url?'<img decoding="async" src="'+esc(x.poster_url)+'" alt="'+esc(x.title)+' poster">':'<img alt="" style="visibility:hidden">')+'<span>CHANNELFORGE HOME VIDEO</span></div><div><span class="eyebrow">SELECTED CASSETTE</span><h2>'+esc(x.title)+'</h2><b class="pink">'+esc(x.year)+'</b> • '+esc(x.type)+'<p>'+esc(x.summary||'No description supplied.')+'</p><button id="selected-play">▷ INSERT MOVIE</button><small>Insert this movie to begin playback. Poster and details come from the connected creator library.</small></div>';$('#selection img')?.addEventListener('error',event=>hideFailedPoster(event.currentTarget));$('#selected-play').onclick=()=>play(x.stream_url,{kind:'movie',title:x.title,movie:x})}
function liveEdge(v){if(!liveMode)return;if(hls?.liveSyncPosition&&Math.abs(v.currentTime-hls.liveSyncPosition)>6)v.currentTime=hls.liveSyncPosition;else if(v.buffered.length&&v.currentTime<v.buffered.end(v.buffered.length-1)-8)v.currentTime=v.buffered.end(v.buffered.length-1)-2}
function setBuffering(active,message='Buffering video…',loading=true){
    const overlay=$('#player-buffer');
    overlay.hidden=!active;
    if(!active)return;
    overlay.querySelector('.buffer-spinner').hidden=!loading;
    overlay.querySelector('b').textContent=message;
    overlay.querySelector('small').textContent=activeMedia?.channel?.name||activeMedia?.title||'ChannelForge TV';
}
function setPlayerState(message,error=false,canRetry=error){
    const state=$('#player-state');
    if(!state)return;
    state.textContent=message;
    state.classList.toggle('error',error);
    if(!error)return;
    setBuffering(true,message,false);
    clearTimeout(playerControlsTimer);
    document.body.classList.remove('player-controls-hidden');
    if(canRetry&&activeStreamUrl){
        const retry=document.createElement('button');
        retry.type='button';
        retry.className='retry-stream';
        retry.textContent='Retry playback';
        retry.onclick=()=>play(activeStreamUrl,activeMedia);
        state.append(' ',retry);
    }
}
let playbackGeneration=0, liveRetryTimer, liveRetries=0;
function advanceLive(failed=false, failureMessage='COULD NOT PLAY THIS CHANNEL'){
    if(!liveMode||!activeStreamUrl||liveRetryTimer)return;
    if(failed&&++liveRetries>3){setPlayerState(failureMessage,true);return}
    const generation=playbackGeneration;
    const channel=liveChannels.find(item=>item.stream_url===activeStreamUrl)||activeMedia.channel;
    const current=programAt(channel,Date.now());
    const earlyEnd=!failed&&current&&current.start===activeMedia.programStart ? Date.parse(current.stop)-Date.now() : 0;
    setPlayerState(failed?'RECONNECTING…':'TUNING NEXT PROGRAM…');
    setBuffering(true,failed?'Reconnecting to the studio…':'Loading the next program…');
    liveRetryTimer=setTimeout(()=>{
        liveRetryTimer=null;
        if(generation!==playbackGeneration||!liveMode)return;
        const next=programAt(channel,Date.now());
        play(activeStreamUrl,{kind:'live',channel,title:next?.title||channel?.name||activeMedia.title},true);
    },Math.max(failed?1000*liveRetries:350,earlyEnd+200));
}
function play(url,media=null,continuing=false){
    clearTimeout(liveRetryTimer);liveRetryTimer=null;
    const generation=++playbackGeneration,v=$('#player'),shell=ensurePlayerShell();
    const source='/stream?url='+encodeURIComponent(url);
    if(!continuing){
        liveRetries=0;playerMini=false;document.body.classList.remove('has-mini-player','player-controls-hidden');
        document.body.append(shell);shell.className='mini-player-shell';v.classList.remove('mini');
    }
    activeStreamUrl=url;activeMedia=media||{kind:/\/live\//.test(url)?'live':'movie',title:'ChannelForge'};
    liveMode=activeMedia.kind==='live';
    if(liveMode){
        const current=programAt(activeMedia.channel,Date.now());
        activeMedia.programStart=current?.start;
        activeMedia.title=current?.title||activeMedia.title;
    }
    $('#close').textContent=playerMini?'✕ CLOSE PLAYER':liveMode?'⌄ MINIMIZE TO GUIDE':'✕ CLOSE MOVIE';
    v.controls=!liveMode;updateTunedIndicator(activeMedia,'CONNECTING…');setBuffering(true,'Connecting to the studio…');showPlayerControls();renderLiveGuide();
    if(hls){hls.destroy();hls=null}
    v.removeAttribute('src');v.load();
    const start=()=>{if(generation===playbackGeneration)v.play().catch(error=>{
        if(generation===playbackGeneration)setPlayerState(error.message||'PRESS PLAY TO START',true);
    })};
    if(window.Hls&&Hls.isSupported()){
        const options=liveMode?{startPosition:-1,liveSyncDurationCount:1,liveMaxLatencyDurationCount:3,maxLiveSyncPlaybackRate:1.35,backBufferLength:2,maxBufferLength:6,maxMaxBufferLength:10,startFragPrefetch:true,manifestLoadingTimeOut:10000,fragLoadingTimeOut:12000}:{startPosition:-1,backBufferLength:120,maxBufferLength:60};
        // Start on the smallest rendition. HLS can raise quality after measuring the connection.
        const player=new Hls({startLevel:0,...options});hls=player;
        let movieRecovery=false;
        player.on(Hls.Events.MANIFEST_PARSED,start);
        player.on(Hls.Events.ERROR,(_event,data)=>{
            if(!data.fatal||generation!==playbackGeneration)return;
            if(liveMode){
                const stalled=data.details===Hls.ErrorDetails.FRAG_LOAD_TIMEOUT;
                advanceLive(true,stalled?'VIDEO DOWNLOAD TIMED OUT · TRY AGAIN SHORTLY':undefined);
                return;
            }
            if(data.type===Hls.ErrorTypes.MEDIA_ERROR&&!movieRecovery){movieRecovery=true;player.recoverMediaError();return}
            setPlayerState('COULD NOT PLAY THIS MOVIE',true);
        });
        player.loadSource(source);player.attachMedia(v);
    }else if(v.canPlayType('application/vnd.apple.mpegurl')){v.src=source;start()}
    else setPlayerState('VIDEO ENGINE UNAVAILABLE. ON LINUX, INSTALL THE CURRENT CHANNELFORGE PACKAGE.',true,false);
    v.classList.add('playing');$('#close').classList.add('show');
}
function minimizePlayer(){if(!liveMode)return stopPlayer();playerMini=true;$('#player').classList.add('mini');document.body.classList.add('has-mini-player');$('#close').textContent='✕ CLOSE PLAYER';showScreen('live');setPlayerState('MINI PLAYER · BROWSE THE GUIDE')}
function stopPlayer(){liveMode=false;playerMini=false;activeStreamUrl='';activeMedia=null;let v=$('#player');v.classList.remove('playing','mini');v.pause();if(hls){hls.destroy();hls=null}v.removeAttribute('src');v.load();setBuffering(false);document.body.classList.remove('has-mini-player');$('#close').classList.remove('show');$('#close').textContent='⌄ MINIMIZE TO GUIDE';updateTunedIndicator(null);renderLiveGuide()}
$('#player').addEventListener('seeking',event=>liveEdge(event.currentTarget));
$('#player').addEventListener('timeupdate',event=>liveEdge(event.currentTarget));
$('#player').addEventListener('playing',()=>{setBuffering(false);showPlayerControls();liveRetries=0;setPlayerState(liveMode?'● LIVE · NO PAUSE OR REWIND':'PLAYING')});
$('#player').addEventListener('ended',()=>advanceLive());
$('#player').addEventListener('error',()=>{if(!hls&&activeStreamUrl){if(liveMode)advanceLive(true);else setPlayerState('COULD NOT PLAY THIS MOVIE',true)}});
$('#player').addEventListener('waiting',()=>{if(activeStreamUrl){setPlayerState('BUFFERING…');setBuffering(true)}});
$('#player').addEventListener('pause',event=>{const video=event.currentTarget;if(liveMode&&!video.ended&&video.readyState>=2&&video.classList.contains('playing'))video.play().catch(()=>{})});
$('#scan-channels').onclick=()=>scanChannels();
$('#guide-search').oninput=event=>{guideSearch=event.target.value;renderLiveGuide()};
$('#guide-earlier').onclick=()=>{guideWindowOffset=Math.max(-1,guideWindowOffset-1);renderLiveGuide()};
$('#guide-now').onclick=()=>{guideWindowOffset=0;renderLiveGuide()};
$('#guide-later').onclick=()=>{guideWindowOffset=Math.min(4,guideWindowOffset+1);renderLiveGuide()};
$('#movie-search').oninput=event=>{vaultSearch=event.target.value;clearTimeout(vaultSearchTimer);vaultSearchTimer=setTimeout(applyVaultFilters,90)};
$('#movie-genre').onchange=event=>{vaultGenre=event.target.value;applyVaultFilters(true)};
$('#movie-rating').onchange=event=>{vaultRating=event.target.value;applyVaultFilters(true)};
$('#movie-sort').onchange=event=>{vaultSort=event.target.value;applyVaultFilters(true)};
document.querySelectorAll('[data-movie-filter]').forEach(button=>button.onclick=()=>{vaultFilter=button.dataset.movieFilter;applyVaultFilters(true)});
setInterval(()=>{if(!document.hidden)scanChannels()},60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)scanChannels()});
$('#close').onclick=()=>playerMini?stopPlayer():minimizePlayer();
$('#update').onclick=()=>checkUpdate(false);
$('#dismiss-update').onclick=()=>$('#update-panel').hidden=true;
$('#restart-update').onclick=async()=>{
    const command=$('#update-command'),message=$('#update-message');
    if(command.value){
        try{await navigator.clipboard.writeText(command.value);message.textContent='Install command copied.'}
        catch(_error){command.focus();command.select();message.textContent='Press Ctrl+C to copy the selected command.'}
        return;
    }
    try{
        await localJson('/api/update/launch?id='+encodeURIComponent($('#update-panel').dataset.job),{method:'POST'});
        message.textContent='Opening the verified update…';
    }catch(error){message.textContent=error.message}
};
function demo(){let titles=['Neon Nights','The Last Arcade','Galaxy Rangers','Video Dreams','Summer Quest'],colors=['#bd205c','#a9560c','#069e9a','#792091','#d64a16'];$('#creators').innerHTML='<button class="creator"><b>RETRO WAVE STUDIO</b><br><small>Custom creator branding • online</small></button>';$('#live').innerHTML='<button class="channel">LIVE • SATURDAY MORNING</button><button class="channel">LIVE • SCI-FI AFTER DARK</button>';$('#vault').innerHTML='';titles.forEach((title,i)=>{let svg='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="'+colors[i]+'"/><stop offset="1" stop-color="#09142d"/></linearGradient></defs><rect width="400" height="600" fill="url(#g)"/><circle cx="90" cy="120" r="55" fill="#ffe43b" opacity=".8"/><text x="200" y="310" fill="white" font-size="38" font-family="sans-serif" font-weight="bold" text-anchor="middle">'+title+'</text></svg>');let x={title,year:1984+i,type:'movie',summary:'A creator-curated retro favorite with poster art and movie details loaded automatically from the connected library.',poster_url:svg,stream_url:''},b=document.createElement('button');b.className='tape';b.innerHTML='<img src="'+svg+'"><b>'+title+'</b><small>'+x.year+' • movie</small><i>PLAY ▶</i>';b.onfocus=()=>selectTape(x);b.onmouseenter=()=>selectTape(x);$('#vault').append(b);if(i===0)selectTape(x)});$('#vault').firstElementChild.focus()}if(location.search.includes('demo=1'))demo();else load();checkUpdate(true);</script></body></html>'''.replace("__VERSION__", VERSION).replace("__DIRECTORY__", DIRECTORY).replace("__DIRECTORY_FALLBACK__", DIRECTORY_FALLBACK).replace("__GITHUB__", GITHUB)


SIDEBAR_CSS = r'''.hamburger{margin:0;background:#11132f;border:1px solid #20e4ff;color:#fff;font-size:24px;line-height:1;padding:12px 14px;border-radius:10px}.app-sidebar{position:fixed;z-index:7;left:0;top:92px;bottom:0;width:235px;padding:24px 14px;background:#090a1def;border-right:1px solid #1eddfb55;backdrop-filter:blur(18px);transition:.2s width,.2s transform}.app-sidebar nav{display:grid;gap:8px}.app-sidebar nav>a{display:grid;grid-template-columns:32px 1fr;align-items:center;text-align:left;width:100%;padding:14px 12px;border:1px solid transparent;border-radius:10px;background:transparent;color:#c3cbe2;font-weight:800;text-decoration:none}.app-sidebar nav>a:hover,.app-sidebar nav>a:focus,.app-sidebar nav>a.active{background:#1a1741;border-color:#ff3eb8;color:#fff}.app-sidebar nav>a i{font-style:normal;font-size:20px;text-align:center}.app-sidebar .menu-label{white-space:nowrap;overflow:hidden}.sidebar-footer{position:absolute;left:18px;right:18px;bottom:22px;color:#7e89a8;font-size:11px}.wrap{margin-left:235px;transition:.2s margin-left}.menu-collapsed .app-sidebar{width:78px}.menu-collapsed .app-sidebar .menu-label,.menu-collapsed .sidebar-footer{display:none}.menu-collapsed .wrap{margin-left:78px}[data-section-block][hidden]{display:none!important}.section-placeholder{min-height:360px;display:grid;place-content:center;text-align:center;border:1px dashed #3c3a6d;border-radius:20px;background:#10112688;color:#9ca6c4;padding:35px}.section-placeholder b{font-size:25px;color:#fff}.section-placeholder span{max-width:480px;margin:9px auto}.screen-title h2{font-size:35px;margin-bottom:4px}.screen-title p{color:#929dbc}@media(max-width:900px){.app-sidebar{transform:translateX(-100%);width:235px}.menu-open .app-sidebar{transform:none}.wrap,.menu-collapsed .wrap{margin-left:0}.menu-collapsed .app-sidebar{width:235px}.menu-collapsed .app-sidebar .menu-label,.menu-collapsed .sidebar-footer{display:block}}'''
GUIDE_CSS = r'''.guide-toolbar{display:flex;align-items:center;gap:16px;margin:8px 0 18px;padding:16px 18px;border:1px solid #2c3e72;border-radius:13px;background:#10142c}.guide-toolbar button{padding:12px 18px;border:1px solid #22e7ff;border-radius:8px;background:#152748;color:#fff;font-weight:900}.guide-toolbar button:disabled{opacity:.6}.guide-toolbar span{color:#9ea9c7}.live{padding-bottom:14px}.channel{display:flex;align-items:center;gap:12px;text-align:left}.channel img{width:48px;height:48px;object-fit:contain}.channel span:last-child{display:grid;gap:5px}.channel small{color:#9da8c5}.channel-number{display:grid;place-items:center;width:48px;height:48px;border-radius:12px;background:linear-gradient(145deg,#38e3f0,#8c59ff);color:#07101c;font-size:19px;font-weight:900}.tv-guide{display:grid;gap:7px;overflow-x:auto;padding:4px 0 38px}.guide-row{display:grid;grid-template-columns:220px minmax(850px,1fr);min-height:105px;border:1px solid #29335b;background:#0c1023}.guide-station{display:flex;align-items:center;gap:12px;padding:14px;border:0;border-right:1px solid #29335b;background:#121a35;color:#fff;text-align:left}.guide-station img{width:58px;height:58px;object-fit:contain}.guide-station strong{display:grid;place-items:center;min-width:58px;height:58px;border-radius:15px;background:linear-gradient(145deg,#20e4ef,#a24aff);color:#07101c;font-size:21px}.guide-station span{display:grid;gap:6px}.guide-station small{color:#8e9abb}.guide-programs{display:flex;min-width:0;overflow:hidden}.guide-program{flex:1 0 230px;display:grid;align-content:center;gap:6px;padding:12px 16px;border:0;border-right:1px solid #29335b;background:#10162d;color:#fff;text-align:left}.guide-program.current{background:linear-gradient(135deg,#263558,#162844);box-shadow:inset 0 0 0 2px #57f5cc}.guide-program time{color:#5af4d1;font-size:12px}.guide-program small{color:#8f9ab7}.guide-unavailable{display:grid;place-items:center;padding:20px;color:#7f89a6}@media(max-width:900px){.guide-toolbar{align-items:flex-start;flex-direction:column}.guide-row{grid-template-columns:170px minmax(700px,1fr)}}'''
GUIDE_STAGE_CSS = r'''.guide-mode .wrap{padding-top:14px}.guide-mode .section-heading[data-section-block="live"]{display:none!important}.live{display:block;overflow:visible;padding:0}.guide-preview{display:grid;grid-template-columns:230px 1fr;width:100%;min-height:165px;margin:0 0 12px;padding:13px;border:1px solid #15def5;border-radius:14px;background:radial-gradient(circle at 88% 20%,#d42e9a2c,transparent 24%),linear-gradient(120deg,#111741,#090d24);color:#fff;text-align:left;box-shadow:0 18px 42px #0006}.guide-preview>img,.guide-preview>strong{width:205px;height:135px;object-fit:contain;border:2px solid #ff3eb8;background:linear-gradient(160deg,#63206d,#13213d)}.guide-preview>strong{display:grid;place-items:center;color:#fff;font-size:54px}.guide-preview>span{display:grid;align-content:center;gap:9px;padding:0 20px}.guide-preview small{color:#4fe9ff;font:bold 12px Consolas,monospace;letter-spacing:1px}.guide-preview b{color:#fff52d;font-size:27px}.guide-preview em{color:#aab8d5;font:normal 14px Consolas,monospace}.guide-preview i{display:block;width:min(650px,90%);height:7px;border:1px solid #766f76;border-radius:8px;background:#403e44;overflow:hidden}.guide-preview u{display:block;height:100%;background:#6dffad;text-decoration:none}.guide-toolbar{justify-content:flex-start;margin-bottom:12px;padding:12px 14px}.guide-toolbar label{display:flex;align-items:center;gap:8px;margin-left:auto;padding:8px 12px;border:1px solid #22e7ff;border-radius:8px;background:#0a1024}.guide-toolbar input{width:190px;border:0;outline:0;background:transparent;color:#fff}.studio-filters{display:flex;gap:10px;overflow:hidden;margin:0 0 8px;padding:0 0 10px;border-bottom:1px solid #ff3eb844}.studio-filters button{padding:9px 15px;border:1px solid #25dcf6;border-radius:7px;background:#101630;color:#75efff;font-weight:800}.studio-filters button.active{border-color:#fff32d;background:#39320a;color:#fff59a}.guide-times{display:grid;grid-template-columns:220px repeat(4,1fr);min-width:0;padding:0 0 7px;color:#36e9ff;font:bold 14px Consolas,monospace;text-align:center}.tv-guide{gap:5px;overflow-x:hidden;padding-bottom:8px;scrollbar-width:none}.tv-guide::-webkit-scrollbar{display:none}.guide-row{grid-template-columns:220px minmax(0,1fr);min-height:88px}.guide-station{padding:10px}.guide-station strong{color:#fff52d;background:transparent;font-size:26px}.guide-programs{width:100%;min-width:0;background:repeating-linear-gradient(90deg,#0000 0 calc(25% - 1px),#2d3c64 calc(25% - 1px) 25%)}.guide-program{flex-grow:0;min-width:0;padding:11px 14px;background:#111a35;overflow:hidden}.guide-program.current{border:3px solid #eaff18;background:linear-gradient(135deg,#5b4815,#272212);box-shadow:inset 0 0 22px #ffd40022}.guide-program b{font-size:15px;overflow:hidden;text-overflow:ellipsis}.guide-program time{order:2;color:#8ea8bf}.guide-program small{order:3}@media(max-width:900px){.guide-preview{grid-template-columns:1fr}.guide-preview>img,.guide-preview>strong{width:100%}.guide-toolbar label{margin-left:0}.studio-filters{overflow-x:auto}.guide-times{grid-template-columns:150px repeat(4,1fr)}.guide-row{grid-template-columns:150px minmax(0,1fr)}}'''
HOME_CSS = r'''.home-panels{display:grid;grid-template-columns:minmax(280px,.8fr) minmax(420px,1.2fr);gap:22px;margin-top:24px}.home-card{min-height:255px;padding:28px;border:1px solid #393275;border-radius:18px;background:linear-gradient(145deg,#121536,#0a0b20);box-shadow:0 20px 55px #0006}.home-card h3{margin:8px 0 12px;font-size:26px;color:#fff}.home-card p{color:#aeb8d2;line-height:1.65}.release-heading{display:flex;align-items:center;justify-content:space-between;gap:20px}.release-heading strong{padding:7px 11px;border:1px solid #22e7ff;border-radius:8px;color:#72f8d7}.home-changelog{max-height:210px;overflow:auto;white-space:pre-wrap;color:#c8d1e8;line-height:1.55;font:14px/1.55 Segoe UI,sans-serif}@media(max-width:900px){.home-panels{grid-template-columns:1fr}}'''
VAULT_CSS = r'''body,.wrap{max-width:100%;overflow-x:hidden}.categories{flex-wrap:wrap}.vault-layout{grid-template-columns:minmax(560px,1.18fr) minmax(420px,.82fr);gap:26px;min-height:620px}.vault{height:calc(100vh - 285px);min-height:620px;max-height:780px;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;padding:10px 14px 30px 5px;scrollbar-width:thin;scrollbar-color:#ff43bc #11132c;scroll-snap-type:y proximity}.vault::-webkit-scrollbar{width:10px}.vault::-webkit-scrollbar-track{background:#11132c;border-radius:10px}.vault::-webkit-scrollbar-thumb{background:linear-gradient(#15e4f5,#ff43bc);border:2px solid #11132c;border-radius:10px}.tape,.tape:nth-child(even){position:relative;display:grid;grid-template-columns:12px 88px minmax(0,1fr) auto;grid-template-rows:1fr 1fr;align-items:center;flex:none;width:100%;height:82px;margin:0;padding:5px 12px 5px 0;border:1px solid #735136;border-left:0;border-radius:4px;background:linear-gradient(180deg,#38251b 0 12%,#18161a 13% 86%,#4b2c1d 87%);box-shadow:10px 8px 16px #0009,inset 0 1px #d9a66b55;transform:perspective(900px) rotateX(-3deg);filter:brightness(.86);text-align:left;scroll-snap-align:start;transition:.16s transform,.16s filter,.16s border-color}.tape::before,.tape::after{content:"";position:absolute;right:112px;width:14px;height:14px;border:2px solid #756b62;border-radius:50%;background:#09090c;box-shadow:inset 0 0 0 3px #242128}.tape::before{top:12px}.tape::after{bottom:12px}.tape:hover,.tape:focus{z-index:2;transform:perspective(900px) rotateX(0) translateX(6px) scale(.995);filter:none;border-color:#efff1c;box-shadow:0 0 0 2px #efff1c,14px 10px 22px #000b}.tape-ridge{grid-row:1/3;width:12px;height:100%;background:repeating-linear-gradient(90deg,#13dbea 0 2px,#066a7c 2px 4px)}.tape img{grid-column:2;grid-row:1/3;width:78px;height:68px;margin:0 10px 0 5px;object-fit:cover;object-position:center;border:1px solid #f3c55b;background:#0a0b12}.tape-label{grid-column:3;grid-row:1/3;display:grid;align-content:center;min-width:0;gap:7px;padding:8px 70px 8px 12px;border:1px solid #886944;background:linear-gradient(90deg,#e4d9bf,#bfb095);color:#171316;box-shadow:inset 0 0 18px #745b3d55}.tape-label b{overflow:hidden;padding:0;font-size:17px;text-overflow:ellipsis;white-space:nowrap}.tape-label small{color:#604c3d;font-family:Consolas,monospace}.tape i{grid-column:4;grid-row:1/3;min-width:94px;padding:9px 11px;border:1px solid #7bffad;background:#4fe88b;color:#07130c;font-style:normal;font-weight:900;text-align:center}.selection{position:sticky;top:112px;height:calc(100vh - 285px);min-height:620px;max-height:780px;grid-template-columns:minmax(190px,240px) 1fr;overflow:hidden;padding:22px}.vhs-box{position:relative;align-self:start;padding:8px 8px 34px;border:2px solid #12dff9;border-left:18px solid #d52b81;border-radius:7px;background:#080b18;box-shadow:18px 18px 30px #000b;transform:perspective(850px) rotateY(5deg)}.vhs-box img{display:block;width:100%;height:min(46vh,420px);object-fit:cover;background:#12152c}.vhs-box span{position:absolute;left:12px;right:8px;bottom:9px;overflow:hidden;color:#8ff4ff;font:10px Consolas,monospace;letter-spacing:1px;white-space:nowrap}.selection h2{margin-bottom:10px}.selection p{max-height:250px;overflow:auto;padding-right:8px;scrollbar-width:thin;scrollbar-color:#20ddeb transparent}.selection button{background:linear-gradient(90deg,#b64a05,#dc7b0a);letter-spacing:1px}.selection button:hover,.selection button:focus{background:#ff9b19;color:#160b00}@media(max-width:1100px){.vault-layout{grid-template-columns:1fr}.selection{position:relative;top:auto;height:auto;min-height:520px}.vault{height:620px}.tape-label{padding-right:20px}.tape::before,.tape::after{display:none}}'''
TAPE_DETAIL_CSS = r'''.tape-label small{display:flex;align-items:center;justify-content:space-between;gap:10px}.tape-label small span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tape-label mark{flex:none;padding:2px 6px;border:1px solid #946734;border-radius:3px;background:#b65b23;color:#fff;font:10px Consolas,monospace}.tape:nth-child(4n+2) .tape-ridge{background:repeating-linear-gradient(90deg,#ff3aa9 0 2px,#7c164e 2px 4px)}.tape:nth-child(4n+3) .tape-ridge{background:repeating-linear-gradient(90deg,#a7ff28 0 2px,#4f7c13 2px 4px)}.tape:nth-child(4n) .tape-ridge{background:repeating-linear-gradient(90deg,#ffbd21 0 2px,#835a00 2px 4px)}'''
TV_OVERHAUL_CSS = r'''header{height:76px;padding:0 2vw;gap:16px}.brand-lockup img{width:46px;height:46px}.brand-lockup h1{font-size:21px}.brand-lockup small{font-size:10px}.update{padding:9px 13px}.app-sidebar{top:76px;width:210px;padding:14px 11px}.wrap{margin-left:210px;padding:18px 2.4vw}.menu-collapsed .wrap{margin-left:68px}.menu-collapsed .app-sidebar{width:68px}.nav-group{display:block;margin:12px 10px 3px;color:#566583;font:10px Consolas,monospace;letter-spacing:2px}.app-sidebar nav>a{padding:9px 8px}.tuned-channel{position:absolute;left:11px;right:11px;bottom:54px;display:grid;gap:4px;padding:12px;border:1px solid #33436c;border-radius:11px;background:#0d1328}.tuned-channel small{color:#4feaff;font:10px Consolas,monospace}.tuned-channel b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tuned-channel span{overflow:hidden;color:#8493b3;font-size:11px;text-overflow:ellipsis;white-space:nowrap}.tuned-channel.active{border-color:#5cffb0;box-shadow:0 0 22px #24f3a522}.menu-collapsed .tuned-channel,.menu-collapsed .nav-group{display:none}.guide-preview{grid-template-columns:180px 1fr;min-height:135px}.guide-preview>img,.guide-preview>strong{width:160px;height:108px}.guide-preview b{font-size:23px}.guide-toolbar{gap:8px;padding:9px 11px}.guide-toolbar button{padding:9px 11px}.guide-window-controls{display:flex;gap:5px}.guide-row{min-height:76px}.guide-station{position:relative;padding:0}.station-tune{display:flex;align-items:center;gap:9px;width:100%;height:100%;padding:8px 34px 8px 9px;border:0;background:transparent;color:#fff;text-align:left}.station-tune img{width:45px;height:45px;object-fit:contain}.station-tune strong{display:grid;place-items:center;min-width:44px;height:44px;color:#fff52d;font-size:22px}.station-tune span{display:grid;gap:4px}.station-tune small{color:#8392b1}.station-favorite{position:absolute;right:5px;top:5px;width:28px;height:28px;padding:0;border:0;background:transparent;color:#fff52d;font-size:19px}.guide-program{padding:8px 11px}.guide-program small{color:#56eaff;font:9px Consolas,monospace}.guide-row.tuned{border-color:#5cffb0;box-shadow:inset 4px 0 #5cffb0,0 0 18px #36f0a51c}.tv-guide{max-height:calc(100vh - 385px);overflow-y:auto;scroll-behavior:smooth;scroll-snap-type:y proximity;scrollbar-width:thin;scrollbar-color:#1eddfb #0c1023}.guide-row{scroll-snap-align:start}.player-hud{position:fixed;z-index:12;left:20px;top:18px;display:grid;gap:2px;max-width:55vw;padding:10px 14px;border:1px solid #2be9ff;border-radius:9px;background:#070b20dd;box-shadow:0 10px 35px #000}.player-hud span{color:#62ffad;font:bold 11px Consolas,monospace}.player-hud small{color:#a7b4d1}.player-hud small.error{color:#ff627b}video.mini{inset:auto 2vw auto auto;top:92px;width:min(46vw,720px);height:auto;aspect-ratio:16/9;border:2px solid #25e8ff;border-radius:14px;box-shadow:0 22px 60px #000;z-index:6}.has-mini-player .wrap{padding-top:min(27vw,405px)}.has-mini-player .player-hud{left:auto;right:calc(2vw + 16px);top:105px;z-index:7}.has-mini-player .close{top:105px;right:3vw;z-index:8;transform:translateY(45px)}.program-dialog{width:min(620px,calc(100% - 30px));padding:0;border:1px solid #24e8ff;border-radius:16px;background:linear-gradient(145deg,#11163b,#080a1d);color:#fff;box-shadow:0 25px 90px #000}.program-dialog::backdrop{background:#02030cbb;backdrop-filter:blur(8px)}.program-dialog-body{padding:28px}.program-dialog h2{color:#fff52d;font-size:30px}.program-meta{color:#56eaff}.program-actions{display:flex;flex-wrap:wrap;gap:9px;margin-top:22px}.program-actions button,.favorite-card{padding:11px 14px;border:1px solid #24e8ff;border-radius:8px;background:#151a3d;color:#fff}.program-close{position:absolute;right:12px;top:10px;border:0;background:transparent;color:#fff;font-size:25px}.favorites-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}.favorites-list h3{grid-column:1/-1}.favorite-card{display:grid;gap:5px;text-align:left}.favorite-card small{color:#8fa0c0}.movie-tools{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:12px}.movie-tools button{padding:7px 12px;border:1px solid #ff3eb8;border-radius:999px;background:#0f1330;color:#8ff}.movie-tools button.active{border-color:#fff52d;color:#fff52d}.movie-search{margin-left:auto;padding:9px 12px;border:1px solid #28eaff;border-radius:8px;background:#090d20;color:#fff}.vault-layout{grid-template-columns:minmax(500px,650px) minmax(440px,1fr);min-height:510px;justify-content:center}.vault{height:auto;min-height:500px;max-height:none;overflow:visible;padding:3px 0}.tape,.tape:nth-child(even){height:72px;max-width:650px;grid-template-columns:10px 76px minmax(0,1fr) 88px}.tape img{width:68px;height:58px}.tape-label{padding-right:46px}.tape::before,.tape::after{right:97px}.tape i{min-width:82px;padding:8px}.selection{top:94px;height:510px;min-height:510px;max-height:510px;grid-template-columns:minmax(165px,210px) 1fr}.vhs-box img{aspect-ratio:2/3;height:auto;max-height:360px;object-fit:cover}.vault-pager{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:10px}.vault-pager button{padding:9px 13px;border:1px solid #28eaff;border-radius:7px;background:#131836;color:#fff}.vault-pager button:disabled{opacity:.35}@media(max-width:1100px){.vault-layout{grid-template-columns:minmax(0,700px);justify-content:center}.selection{position:relative;top:auto;height:auto;max-height:none}.has-mini-player .wrap{padding-top:50vw}}'''
LIVE_ARTWORK_CSS = r'''.guide-preview{position:relative;isolation:isolate;overflow:hidden;grid-template-columns:minmax(250px,320px) 1fr;min-height:210px;padding:15px;background:linear-gradient(90deg,#101638 0 42%,#0a1028cc 72%,#0a102899),var(--guide-backdrop),linear-gradient(120deg,#111741,#090d24);background-position:center;background-size:cover}.guide-preview::after{content:"";position:absolute;z-index:-1;inset:0;background:linear-gradient(90deg,#0b1134 0 24%,#0b1134dd 43%,#070b1f35 78%),radial-gradient(circle at 92% 30%,#ff35b52e,transparent 35%)}.guide-preview>img,.guide-preview>strong{position:relative;width:100%;height:180px;object-fit:cover;border-radius:12px;box-shadow:0 0 24px #ff3eb855}.guide-preview>span{position:relative}.guide-preview small mark{margin-left:8px;background:transparent;color:#ff35c8}.guide-preview b{font-size:clamp(24px,2.25vw,38px)}.guide-preview em{font-size:16px}.guide-preview i{width:min(800px,94%)}.tv-guide{max-height:calc(100vh - 470px)}.program-dialog{width:min(780px,calc(100% - 30px))}.program-dialog-layout{display:grid;grid-template-columns:minmax(180px,260px) 1fr;gap:24px;align-items:start}.program-poster{width:100%;max-height:330px;aspect-ratio:16/10;object-fit:cover;border:2px solid #ff3eb8;border-radius:12px;box-shadow:0 0 28px #ff3eb844}@media(max-width:760px){.guide-preview{grid-template-columns:1fr}.program-dialog-layout{grid-template-columns:1fr}.program-poster{max-height:240px}}'''
FILTER_CSS = r'''.movie-tools{display:grid;grid-template-columns:auto minmax(240px,1fr) repeat(3,auto) auto;gap:9px;padding:11px;border:1px solid #243665;border-radius:13px;background:linear-gradient(135deg,#101630dd,#090b20dd);box-shadow:0 12px 30px #0005}.movie-decades{display:flex;gap:6px}.movie-search-wrap{display:flex;align-items:center;gap:8px;min-width:0;padding:0 12px;border:1px solid #28eaff;border-radius:8px;color:#58efff;background:#080d20}.movie-search{width:100%;min-width:0;margin:0;border:0;background:transparent}.movie-tools select{min-width:125px;padding:9px 28px 9px 10px;border:1px solid #ff3eb8;border-radius:8px;background:#10132c;color:#fff}.movie-tools #movie-count{align-self:center;white-space:nowrap;color:#65efcf;font:bold 12px Consolas,monospace}@media(max-width:1250px){.movie-tools{grid-template-columns:1fr 1fr repeat(2,auto)}.movie-decades{grid-column:1/-1}.movie-search-wrap{grid-column:1/3}}@media(max-width:760px){.movie-tools{display:flex}.movie-search-wrap{order:-1;width:100%}.movie-tools select{flex:1}.movie-decades{overflow-x:auto;width:100%}}'''
CINEMA_FIX_CSS = r'''[data-section-block="cinema"].vault-layout{grid-template-columns:minmax(500px,760px) minmax(440px,1fr);align-items:start;min-height:0;max-width:1500px;margin:0 auto}[data-section-block="cinema"] .vault{display:flex;height:calc(100vh - 285px);min-height:520px;max-height:720px;gap:6px;overflow-y:auto!important;overflow-x:hidden!important;padding:4px 10px 18px 2px;scrollbar-width:none;overscroll-behavior-y:contain;scroll-snap-type:none;perspective:none}[data-section-block="cinema"] .vault::-webkit-scrollbar{display:none}[data-section-block="cinema"] .tape,[data-section-block="cinema"] .tape:nth-child(even){box-sizing:border-box;display:grid;flex:0 0 68px;width:100%;min-width:0;max-width:none;height:68px!important;min-height:68px!important;max-height:68px!important;margin:0;overflow:hidden;grid-template-columns:10px 70px minmax(0,1fr) 88px;grid-template-rows:68px;padding:0;border:1px solid #765035;border-left:0;border-radius:4px;background:linear-gradient(180deg,#3d271a 0 8px,#171519 9px 59px,#4c2d1c 60px);box-shadow:6px 5px 12px #0009,inset 0 1px #d9a66b55;transform:none;filter:none;content-visibility:auto;contain:layout paint style;contain-intrinsic-size:auto 68px}[data-section-block="cinema"] .tape:hover,[data-section-block="cinema"] .tape:focus{z-index:2;overflow:hidden;transform:translateX(4px);border-color:#efff1c;box-shadow:0 0 0 2px #efff1c,8px 7px 15px #000b}[data-section-block="cinema"] .tape-ridge{grid-column:1;grid-row:1;width:10px;height:68px}[data-section-block="cinema"] .tape img{grid-column:2;grid-row:1;width:62px;height:56px;margin:6px 4px;object-fit:cover}[data-section-block="cinema"] .tape-label{grid-column:3;grid-row:1;align-self:center;height:56px;min-height:0;padding:6px 42px 6px 11px;gap:4px;overflow:hidden}[data-section-block="cinema"] .tape-label b{font-size:15px}[data-section-block="cinema"] .tape-label small{font-size:9px}[data-section-block="cinema"] .tape i{grid-column:4;grid-row:1;align-self:center;display:grid;place-content:center;width:82px;min-width:82px;height:46px;padding:4px;font-size:12px}[data-section-block="cinema"] .tape::before,[data-section-block="cinema"] .tape::after{right:96px;width:10px;height:10px}[data-section-block="cinema"] .tape::before{top:10px}[data-section-block="cinema"] .tape::after{bottom:10px}[data-section-block="cinema"] .selection{top:92px;height:calc(100vh - 285px);min-height:520px;max-height:720px}@media(max-width:1100px){[data-section-block="cinema"].vault-layout{grid-template-columns:minmax(0,760px)}[data-section-block="cinema"] .vault{height:560px;min-height:560px}[data-section-block="cinema"] .selection{position:relative;top:auto;height:auto;min-height:500px;max-height:none}}'''
PLAYER_DOCK_CSS = r'''.live-stage{display:grid;grid-template-columns:minmax(300px,38%) minmax(0,1fr);gap:14px;align-items:stretch;margin:0 0 12px}.mini-player-slot{position:relative;isolation:isolate;min-height:210px;overflow:hidden;border:1px solid #20e8ff;border-radius:14px;background:#070b1c;box-shadow:0 18px 42px #0007}.mini-player-poster{position:absolute;inset:0;display:grid;place-content:center;gap:8px;background:radial-gradient(circle at 50% 35%,#ff39b744,transparent 42%),linear-gradient(145deg,#19164a,#080a1d);color:#55edff;text-align:center}.mini-player-poster img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;filter:saturate(.92) brightness(.72)}.mini-player-poster::after{content:"";position:absolute;inset:0;background:linear-gradient(0deg,#050717cc,transparent 60%)}.mini-player-poster strong,.mini-player-poster span{position:relative;z-index:1}.mini-player-poster strong{font-size:54px;color:#fff}.mini-player-poster span{font:bold 11px Consolas,monospace;letter-spacing:2px}.live-stage .live{min-width:0;padding:0}.live-stage .guide-preview{height:100%;min-height:210px;margin:0;grid-template-columns:1fr}.live-stage .guide-preview>img,.live-stage .guide-preview>strong{display:none}.mini-player-shell{display:contents}.mini-player-shell.guide-dock,.mini-player-shell.section-dock{position:absolute;z-index:3;inset:0;display:block;overflow:hidden;border-radius:13px;background:#000}.mini-player-shell.section-dock{position:relative;inset:auto;width:min(620px,52vw);aspect-ratio:16/9;margin:0 0 18px auto;border:1px solid #20e8ff;box-shadow:0 18px 48px #0009}.mini-player-shell video.mini{position:absolute!important;display:block;inset:0!important;width:100%!important;height:100%!important;aspect-ratio:16/9;object-fit:contain;border:0!important;border-radius:0!important;box-shadow:none!important}.mini-player-shell.guide-dock .player-hud,.mini-player-shell.section-dock .player-hud{position:absolute;z-index:4;left:12px!important;right:12px!important;top:12px!important;bottom:auto!important;display:grid;width:auto;max-width:calc(100% - 24px);padding:9px 11px;pointer-events:none;transform:none}.mini-player-shell.guide-dock .player-hud-actions,.mini-player-shell.section-dock .player-hud-actions{display:none}.mini-player-shell.guide-dock .close,.mini-player-shell.section-dock .close{position:absolute;z-index:5;right:12px!important;top:auto!important;bottom:12px!important;transform:none!important;padding:8px 11px;background:#080b1ddd;backdrop-filter:blur(8px)}.mini-player-shell:not(.guide-dock):not(.section-dock) .player-hud{position:fixed;z-index:12;left:50%;right:auto;top:auto;bottom:32px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;width:min(1040px,calc(100vw - 80px));max-width:none;padding:18px 20px;border:1px solid #27eaff;border-radius:16px;background:linear-gradient(110deg,#080c22ed,#18103bed);box-shadow:0 22px 70px #000c,0 0 30px #18dfff1a;backdrop-filter:blur(18px);transform:translateX(-50%);transition:opacity .22s,transform .22s}.player-hud-copy{display:grid;gap:5px;min-width:0}.player-hud-copy span{color:#62ffad;font:bold 11px Consolas,monospace;letter-spacing:1.5px}.player-hud-copy b{color:#52eaff;font-size:14px}.player-hud-copy strong{overflow:hidden;color:#fff52d;font-size:clamp(19px,2vw,29px);text-overflow:ellipsis;white-space:nowrap}.player-hud-copy i{display:block;width:100%;height:5px;overflow:hidden;border-radius:5px;background:#3b425b}.player-hud-copy i u{display:block;height:100%;background:linear-gradient(90deg,#5cffae,#45ddff);text-decoration:none}.player-hud-copy small{color:#a9b6d2}.player-hud-copy em{color:#d2d8e8;font:normal 11px Consolas,monospace}.player-hud-actions{display:flex;align-items:center;gap:8px}.player-hud-actions button{min-width:76px;padding:12px 10px;border:1px solid #30e5ff;border-radius:10px;background:#111938;color:#fff;font-weight:900}.player-hud-actions button small{color:#8cefff;font-size:9px}.player-hud[hidden]{display:none!important}.player-controls-hidden .mini-player-shell:not(.guide-dock):not(.section-dock) .player-hud,.player-controls-hidden .mini-player-shell:not(.guide-dock):not(.section-dock) .close{opacity:0;pointer-events:none;transform:translate(-50%,18px)}.has-mini-player .wrap{padding-top:18px!important}.guide-preview,.guide-program,.tape{transition:transform .16s cubic-bezier(.2,.8,.2,1),border-color .16s,box-shadow .16s,filter .16s}.guide-program:focus-visible,.tape:focus-visible{outline:0;z-index:4;transform:translateY(-2px) scale(1.018);border-color:#efff1c!important;box-shadow:0 0 0 2px #ff3eb8,0 10px 28px #000a;filter:brightness(1.08)}@media(max-width:900px){.live-stage{grid-template-columns:1fr}.mini-player-slot{min-height:0;aspect-ratio:16/9}.mini-player-shell.section-dock{width:100%}.mini-player-shell:not(.guide-dock):not(.section-dock) .player-hud{width:calc(100vw - 28px);grid-template-columns:1fr}.player-hud-actions{justify-content:center}}@media(prefers-reduced-motion:reduce){.guide-preview,.guide-program,.tape{transition:none!important}}'''
SIDEBAR_HTML = r'''<aside class="app-sidebar"><nav><small class="nav-group">WATCH</small><a class="active" href="?screen=home" data-screen="home"><i>⌂</i><span class="menu-label">Home</span></a><a href="?screen=live" data-screen="live"><i>●</i><span class="menu-label">Live TV Guide</span></a><small class="nav-group">LIBRARY</small><a href="?screen=cinema" data-screen="cinema"><i>▣</i><span class="menu-label">Rewind Cinema</span></a><a href="?screen=continue" data-screen="continue"><i>▶</i><span class="menu-label">Continue Watching</span></a><a href="?screen=favorites" data-screen="favorites"><i>★</i><span class="menu-label">Favorites</span></a><small class="nav-group">SYSTEM</small><a href="?screen=updates" data-screen="updates"><i>↻</i><span class="menu-label">Updates</span></a></nav><section id="tuned-channel" class="tuned-channel"><small>NOW WATCHING</small><b>Nothing tuned</b><span>Choose a live channel or movie</span></section><div class="sidebar-footer">ChannelForge TV • v__VERSION__<br>Keyboard and remote friendly.</div></aside>'''
EXTRA_SCREENS = r'''<section data-section-block="continue" hidden><div class="screen-title"><span class="eyebrow">PICK UP WHERE YOU LEFT OFF</span><h2>Continue Watching</h2></div><div class="section-placeholder"><b>No unfinished movies yet</b><span>Playback history will appear here as you watch Rewind Cinema.</span></div></section><section data-section-block="favorites" hidden><div class="screen-title"><span class="eyebrow">YOUR COLLECTION</span><h2>Favorite channels &amp; creators</h2></div><div id="favorites-list" class="favorites-list"></div></section><section data-section-block="updates" hidden><div class="screen-title"><span class="eyebrow">APPLICATION</span><h2>Updates</h2><p>Downloads come only from the official GitHub release and are SHA-256 verified.</p></div><div class="section-placeholder"><b id="screen-update-status">Checking for updates…</b><span>Select Check Now to refresh release status.</span><button id="screen-check-update" class="update">CHECK NOW</button></div></section>'''
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
#guide-board{--station-width:clamp(156px,14.7vw,222px);--guide-steps:8;overflow:hidden;border:1px solid #28daef;border-radius:9px;background:#060c1c;box-shadow:0 14px 44px #0009,0 0 22px #0ddcff13}
#guide-board .guide-times,#guide-board .guide-row{display:grid;grid-template-columns:var(--station-width) minmax(0,1fr);gap:0;width:100%;min-width:0;padding:0}
#guide-board .guide-times{height:31px;border-bottom:1px solid #1ac9e7;margin:0;background:#070c1d;font:700 12px "Segoe UI",sans-serif;color:#4affff}
.guide-channel-head{display:grid;grid-template-columns:32px 1fr;align-items:center;padding:0 9px;text-align:left;border-right:1px solid #235575}
.guide-ticks{display:grid;grid-template-columns:repeat(var(--guide-steps),minmax(0,1fr));align-items:end;text-align:center}
.guide-ticks time{position:relative;padding-bottom:7px;white-space:nowrap}
.guide-ticks time::after{content:"";position:absolute;left:0;bottom:0;width:1px;height:5px;background:#159cb2}
#guide-board .tv-guide{height:auto;max-height:calc(100vh - 388px);min-height:54px;display:block;overflow:auto;padding:0;scrollbar-width:thin;scrollbar-color:#23788c #080e22;scroll-snap-type:none}
#guide-board .guide-row{height:51px;border:0;border-bottom:1px solid #235272;border-radius:0;box-shadow:none}
#guide-board .guide-row:last-child{border-bottom:0}
#guide-board .guide-row.tuned{background:#102024;box-shadow:inset 3px 0 #53f4b4}
#guide-board .guide-station{min-width:0;display:grid;grid-template-columns:minmax(0,1fr) 30px;align-items:center;background:#080f23;border:0;border-right:1px solid #295878;padding:0}
#guide-board .station-tune{display:grid;grid-template-columns:29px 24px minmax(0,1fr);gap:6px;align-items:center;height:100%;min-width:0;border:0;padding:3px 7px;border-radius:0;background:transparent;text-align:left}
#guide-board .station-tune strong{color:#55fcff;font-size:14px;text-align:center}
#guide-board .station-tune b,#guide-board .station-tune small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.25}
#guide-board .station-tune b{font-size:12px;color:#fff}
#guide-board .station-tune small{color:#9bc7e7;font-size:10px}
#guide-board .station-tune img{width:24px;height:24px;object-fit:contain}
.station-icon{width:20px;height:14px;border:1.3px solid #d8f3ff;border-radius:4px;position:relative}
.station-icon::before{content:"";position:absolute;left:5px;top:-6px;width:6px;height:6px;border-left:1px solid #d8f3ff;border-top:1px solid #d8f3ff;transform:rotate(45deg)}
#guide-board .station-favorite{align-self:center;position:static;min-width:0;width:27px;height:27px;padding:0;border:0;background:none;color:#ffe844;font-size:15px}
#guide-board .guide-programs{position:relative;display:block;min-width:0;background:repeating-linear-gradient(to right,#121d33 0 calc(100% / var(--guide-steps) - 1px),#245174 calc(100% / var(--guide-steps) - 1px) calc(100% / var(--guide-steps)))}
#guide-board .guide-program{position:absolute;top:1px;bottom:1px;display:grid;align-content:center;gap:3px;min-width:0;max-width:none;min-height:0;padding:3px 7px;border:1px solid #2c506f;border-radius:4px;background:linear-gradient(139deg,#16213c,#0b1125);overflow:hidden;text-align:left}
#guide-board .guide-program b,#guide-board .guide-program time{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.16}
#guide-board .guide-program b{font-weight:650;font-size:12px}
#guide-board .guide-program time{font-size:10px;color:#b6d8f2}
#guide-board .guide-program.current{z-index:1;border-color:#fff34b;background:linear-gradient(120deg,#494014,#151a23);box-shadow:inset 0 0 9px #dfc41344;color:#fff4ab}
#guide-board .guide-program:focus-visible,#guide-board button:focus-visible{z-index:2;outline:2px solid #fff556;outline-offset:-2px}
.short-programs{display:grid;gap:6px;max-height:320px;overflow:auto}
.short-programs button{text-align:left}
.player-buffer{position:fixed;inset:0;z-index:11;display:grid;place-content:center;justify-items:center;gap:9px;padding:30px;background:radial-gradient(ellipse at 50% 44%,#1a284bcf,#060817ee 70%);color:#edfaff;text-align:center;pointer-events:none}
.player-buffer b{font-size:clamp(15px,2vw,24px);font-weight:650}
.player-buffer small{color:#9fc4dc;font-size:13px}
.buffer-spinner{width:34px;height:34px;border:3px solid #315878;border-top-color:#30f8fb;border-right-color:#bf5bff;border-radius:50%;animation:buffer-spin .8s linear infinite}
@keyframes buffer-spin{to{transform:rotate(360deg)}}
.player-bug{position:fixed;z-index:11;top:18px;left:19px;display:flex;align-items:center;gap:8px;padding:7px 9px;border:1px solid #3bebfb;border-radius:7px;background:#0b1225cc;color:#c8f7fc;font-size:12px;font-weight:700;pointer-events:none}
.player-bug img{width:43px;height:34px;object-fit:contain}
.guide-dock .player-buffer,.section-dock .player-buffer{position:absolute}
.guide-dock .player-bug,.section-dock .player-bug{display:none}
.guide-dock .player-hud{display:none!important}
.guide-dock .close{padding:5px 7px;font-size:10px}
@media(prefers-reduced-motion:reduce){.buffer-spinner{animation:none}}
@media(max-width:1100px){.guide-toolbar .guide-window-controls{order:3}.live-stage{grid-template-columns:minmax(205px,34%) minmax(0,1fr)}}
@media(max-width:760px){.live-stage{grid-template-columns:1fr}.live-stage .mini-player-slot{min-height:110px;max-height:210px}.guide-preview b{white-space:normal!important}#guide-board{--station-width:135px}#guide-board .station-tune{grid-template-columns:22px minmax(0,1fr)}#guide-board .station-icon,#guide-board .station-tune img{display:none}#guide-board .guide-program{padding:3px}#guide-board .guide-program time{font-size:9px}}
'''
PLAYER_DOCK_JS = r'''
let playerShell=null,playerControlsTimer=0;
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
        +(live?'<div class="player-hud-actions"><button data-player-surf="-1" aria-label="Previous channel">▲<br><small>CHANNEL</small></button><button data-player-surf="1" aria-label="Next channel">▼<br><small>CHANNEL</small></button><button data-player-guide aria-label="Open live guide">☰<br><small>GUIDE</small></button></div>':'');
    hud.hidden=false;
}
function ensurePlayerShell(){if(playerShell)return playerShell;playerShell=document.createElement('section');playerShell.id='mini-player-shell';playerShell.className='mini-player-shell';let player=$('#player');player.parentNode.insertBefore(playerShell,player);playerShell.append(player,$('#player-buffer'),$('#player-bug'),$('#player-hud'),$('#close'));return playerShell}
function dockMiniPlayer(screen='live'){let shell=ensurePlayerShell(),slot=$('#mini-player-slot');document.body.classList.remove('player-controls-hidden');if(screen==='live'&&slot){slot.append(shell);shell.className='mini-player-shell guide-dock'}else{document.querySelector('main.wrap')?.prepend(shell);shell.className='mini-player-shell section-dock'}}
function showPlayerControls(){document.body.classList.remove('player-controls-hidden');clearTimeout(playerControlsTimer);if(!playerMini&&!$('#player-state.error')&&$('#player-buffer')?.hidden)playerControlsTimer=setTimeout(()=>document.body.classList.add('player-controls-hidden'),3200)}
function surfChannel(direction){if(!liveChannels.length)return;let index=liveChannels.findIndex(channel=>channel.stream_url===activeStreamUrl);index=index<0?0:(index+direction+liveChannels.length)%liveChannels.length;let channel=liveChannels[index],program=programAt(channel);play(channel.stream_url,{kind:'live',channel,title:program?.title||channel.name})}
document.addEventListener('mousemove',showPlayerControls,{passive:true});document.addEventListener('keydown',showPlayerControls);document.addEventListener('click',event=>{let surf=event.target.closest('[data-player-surf]');if(surf)surfChannel(Number(surf.dataset.playerSurf)||1);if(event.target.closest('[data-player-guide]'))minimizePlayer()});
'''
CONTROLLER_JS = r'''
function tvFocusTargets(){let dialog=document.querySelector('dialog[open]'),scope=dialog||document;return[...scope.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])')].filter(element=>!element.hidden&&element.offsetParent!==null&&getComputedStyle(element).visibility!=='hidden')}
function tvMoveFocus(direction){if($('#player').classList.contains('playing')&&!playerMini&&(direction==='up'||direction==='down')){surfChannel(direction==='up'?-1:1);return}let targets=tvFocusTargets(),current=targets.includes(document.activeElement)?document.activeElement:null;if(!targets.length)return;if(!current){targets[0].focus();return}let origin=current.getBoundingClientRect(),ox=origin.left+origin.width/2,oy=origin.top+origin.height/2,best=null,score=Infinity;for(let target of targets){if(target===current)continue;let rect=target.getBoundingClientRect(),dx=rect.left+rect.width/2-ox,dy=rect.top+rect.height/2-oy,primary=direction==='left'?-dx:direction==='right'?dx:direction==='up'?-dy:dy,secondary=direction==='left'||direction==='right'?Math.abs(dy):Math.abs(dx);if(primary<=4)continue;let candidate=primary+secondary*2.25;if(candidate<score){score=candidate;best=target}}if(best){best.focus({preventScroll:true});best.scrollIntoView({behavior:'smooth',block:'nearest',inline:'nearest'})}}
function tvBack(){let dialog=document.querySelector('dialog[open]');if(dialog){dialog.close();return}if($('#player').classList.contains('playing')){playerMini?stopPlayer():minimizePlayer();return}showScreen('home');document.querySelector('[data-screen="home"]')?.focus()}
document.addEventListener('keydown',event=>{let editable=event.target.matches?.('input,textarea,select,[contenteditable="true"]');if(editable&&event.key!=='Escape')return;let direction={ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down'}[event.key];if(direction){event.preventDefault();tvMoveFocus(direction)}else if(event.key==='Escape'){event.preventDefault();tvBack()}});
let tvGamepadState={},tvGamepadRepeat=0;function pollTvGamepads(now){let pad=[...(navigator.getGamepads?.()||[])].find(Boolean);if(pad){let state={up:pad.buttons[12]?.pressed||pad.axes[1]<-.55,down:pad.buttons[13]?.pressed||pad.axes[1]>.55,left:pad.buttons[14]?.pressed||pad.axes[0]<-.55,right:pad.buttons[15]?.pressed||pad.axes[0]>.55,activate:pad.buttons[0]?.pressed,back:pad.buttons[1]?.pressed};for(let action of Object.keys(state)){let repeatable=['up','down','left','right'].includes(action),repeat=repeatable&&state[action]&&tvGamepadState[action]&&now-tvGamepadRepeat>190;if(state[action]&&(!tvGamepadState[action]||repeat)){showPlayerControls();if(repeatable){tvMoveFocus(action);tvGamepadRepeat=now}else if(action==='activate')document.activeElement?.click();else tvBack()}}tvGamepadState=state}else tvGamepadState={};requestAnimationFrame(pollTvGamepads)}requestAnimationFrame(pollTvGamepads);
'''
HOME_DETAILS = r'''<section class="home-panels" data-section-block="home"><article class="home-card"><span class="eyebrow">WELCOME</span><h3>Your community television hub</h3><p>ChannelForge TV brings creator-made live channels and shared movie shelves into one remote-friendly application. Open Live TV to browse the automatic channel guide, or Rewind Cinema to explore movies from connected studios.</p></article><article class="home-card"><div class="release-heading"><div><span class="eyebrow">LATEST UPDATE</span><h3>Release notes</h3></div><strong id="home-release-version">v__VERSION__</strong></div><p id="home-update-state">Checking the official release…</p><pre id="home-changelog" class="home-changelog">Loading changelog…</pre></article></section>'''.replace("__VERSION__", VERSION)
HTML = HTML.replace("</style><header>", f"</style><style>{SIDEBAR_CSS}{GUIDE_CSS}{GUIDE_STAGE_CSS}{HOME_CSS}{VAULT_CSS}{TAPE_DETAIL_CSS}{TV_OVERHAUL_CSS}{LIVE_ARTWORK_CSS}{FILTER_CSS}{CINEMA_FIX_CSS}{PLAYER_DOCK_CSS}</style><header>")
HTML = HTML.replace('<small id="update-bytes"></small>', '<small id="update-bytes"></small><textarea id="update-command" aria-label="Install command" rows="4" style="width:100%;margin-top:10px" readonly hidden></textarea>', 1)
HTML = HTML.replace('<header><div class="brand-lockup">', '<header><button id="menu-toggle" class="hamburger" aria-label="Open navigation">☰</button><div class="brand-lockup">')
HTML = HTML.replace('</header><aside id="update-panel"', f'</header>{SIDEBAR_HTML.replace("__VERSION__", VERSION)}<aside id="update-panel"')
HTML = HTML.replace('<section class="hero">', '<section class="hero" data-section-block="home">', 1)
HTML = HTML.replace('COMMUNITY CHANNELS • ON DEMAND', 'WELCOME TO CHANNELFORGE TV', 1)
HTML = HTML.replace('Turn the dial.<br>Find something unforgettable.', 'Live channels.<br>One community dial.', 1)
HTML = HTML.replace('Jump into live creator channels or browse every connected movie shelf from one cinematic TV interface.', 'Watch public creator channels, follow a shared program guide, and browse connected movie collections without configuring Plex DVR.', 1)
HTML = HTML.replace('</section><div class="section-heading"><h2>CREATOR STUDIOS', f'</section>{HOME_DETAILS}<div class="section-heading"><h2>CREATOR STUDIOS', 1)
HTML = HTML.replace('<div class="section-heading"><h2>CREATOR STUDIOS', '<div class="section-heading" data-section-block="cinema" hidden><h2>CREATOR STUDIOS', 1)
HTML = HTML.replace('<div id="creators" class="creators">', '<div id="creators" class="creators" data-section-block="cinema" hidden>', 1)
HTML = HTML.replace('<div class="section-heading"><h2 class="cyan">LIVE CHANNELS', '<div class="section-heading" data-section-block="live" hidden><h2 class="cyan">LIVE CHANNELS', 1)
HTML = HTML.replace('<div id="live" class="live"></div>', '<section id="live-stage" class="live-stage" data-section-block="live" hidden><div id="mini-player-slot" class="mini-player-slot"><div id="mini-player-poster" class="mini-player-poster"><span>SELECT A CHANNEL FROM THE GUIDE</span></div></div><div id="live" class="live"></div></section><div class="guide-toolbar" data-section-block="live" hidden><button id="scan-channels">↻ SCAN CHANNELS</button><span id="scan-status">Channels scan automatically.</span><div class="guide-window-controls"><button id="guide-earlier">‹ EARLIER</button><button id="guide-now">NOW</button><button id="guide-later">LATER ›</button><b id="guide-window-label">ON NOW</b></div><label>⌕ <input id="guide-search" type="search" placeholder="Search guide"></label></div><div id="studio-filters" class="studio-filters" data-section-block="live" hidden></div><div id="guide-board" data-section-block="live" hidden><div id="guide-times" class="guide-times"></div><div id="tv-guide" class="tv-guide"></div></div>', 1)
HTML = HTML.replace('<div class="section-heading"><h2 class="pink">REWIND CINEMA', '<div class="section-heading" data-section-block="cinema" hidden><h2 class="pink">REWIND CINEMA', 1)
HTML = HTML.replace('<nav class="categories">', '<nav class="categories" data-section-block="cinema" hidden>', 1)
HTML = HTML.replace('<div class="vault-layout">', '<div class="vault-layout" data-section-block="cinema" hidden>', 1)
HTML = HTML.replace('<nav class="categories" data-section-block="cinema" hidden><span>CONTINUE WATCHING</span><span>80S BLOCKBUSTERS</span><span>90S ESSENTIALS</span><span>CREATOR PICKS</span></nav>', '<nav class="movie-tools" data-section-block="cinema" hidden><div class="movie-decades"><button class="active" data-movie-filter="all">ALL</button><button data-movie-filter="80s">80S</button><button data-movie-filter="90s">90S</button><button data-movie-filter="2000s">2000S</button><button data-movie-filter="recent">2010+</button></div><label class="movie-search-wrap">⌕<input id="movie-search" class="movie-search" type="search" placeholder="Search title, genre, description…"></label><select id="movie-genre" aria-label="Filter by genre"><option value="all">ALL GENRES</option></select><select id="movie-rating" aria-label="Filter by rating"><option value="all">ALL RATINGS</option></select><select id="movie-sort" aria-label="Sort movies"><option value="title">A–Z</option><option value="newest">NEWEST</option><option value="oldest">OLDEST</option></select><span id="movie-count"></span></nav>', 1)
HTML = HTML.replace('</main><video id="player"', f'{EXTRA_SCREENS}</main><aside id="player-hud" class="player-hud" hidden></aside><aside id="player-bug" class="player-bug" hidden></aside><div id="player-buffer" class="player-buffer" role="status" aria-live="polite" hidden><span class="buffer-spinner" aria-hidden="true"></span><b></b><small></small></div><dialog id="program-dialog" class="program-dialog"><button class="program-close" onclick="this.closest(\'dialog\').close()">×</button><div id="program-dialog-body" class="program-dialog-body"></div></dialog><video id="player"', 1)
HTML = HTML.replace("let playbackGeneration=0", PLAYER_DOCK_JS + "\nlet playbackGeneration=0", 1)
HTML = HTML.replace("function minimizePlayer(){if(!liveMode)return stopPlayer();playerMini=true;$('#player').classList.add('mini');document.body.classList.add('has-mini-player');$('#close').textContent='✕ CLOSE PLAYER';showScreen('live');setPlayerState('MINI PLAYER · BROWSE THE GUIDE')}", "function minimizePlayer(){if(!liveMode)return stopPlayer();playerMini=true;$('#player').classList.add('mini');document.body.classList.add('has-mini-player');$('#close').textContent='✕ CLOSE PLAYER';showScreen('live');dockMiniPlayer('live');setPlayerState('MINI PLAYER · BROWSE THE GUIDE')}", 1)
HTML = HTML.replace("function stopPlayer(){liveMode=false;", "function stopPlayer(){++playbackGeneration;clearTimeout(liveRetryTimer);liveRetryTimer=null;let shell=ensurePlayerShell();document.body.append(shell);shell.className='mini-player-shell';document.body.classList.remove('player-controls-hidden');liveMode=false;", 1)
HTML = HTML.replace("async function checkUpdate(onLaunch=false)", "function showScreen(name){document.querySelectorAll('[data-section-block]').forEach(block=>block.hidden=block.dataset.sectionBlock!==name);document.querySelectorAll('[data-screen]').forEach(button=>button.classList.toggle('active',button.dataset.screen===name));document.body.classList.toggle('guide-mode',name==='live');if(name==='live'&&innerWidth>900)document.body.classList.add('menu-collapsed');if(name==='live')renderLiveGuide();if(name==='cinema'&&selectedCreator)studio(selectedCreator);if(innerWidth<=900)document.body.classList.remove('menu-open');window.scrollTo(0,0)}\nasync function checkUpdate(onLaunch=false)", 1)
HTML = HTML.replace("b.onclick=()=>studio(c)", "b.onclick=()=>{studio(c);showScreen('cinema')}", 1)
HTML = HTML.replace("$('#close').onclick", "ensurePlayerShell();$('#vault').addEventListener('scroll',event=>{let box=event.currentTarget;if(box.scrollTop+box.clientHeight>box.scrollHeight-500&&vaultRenderCount<vaultVisible.length)appendVaultBatch()},{passive:true});$('#menu-toggle').onclick=()=>{if(innerWidth<=900)document.body.classList.toggle('menu-open');else document.body.classList.toggle('menu-collapsed')};document.querySelectorAll('[data-screen]').forEach(button=>button.onclick=event=>{event.preventDefault();const screen=button.dataset.screen;showScreen(screen);if(playerMini)dockMiniPlayer(screen);if(screen==='updates')checkUpdate(false)});$('#screen-check-update').onclick=()=>checkUpdate(false);$('#close').onclick", 1)
HTML = HTML.replace("$('#update').onclick=()=>checkUpdate(false)", "$('#update').onclick=()=>{showScreen('updates');checkUpdate(false)}", 1)
HTML = HTML.replace("if(location.search.includes('demo=1'))demo();else load();checkUpdate(true);", "if(location.search.includes('demo=1'))demo();else load();showScreen(new URLSearchParams(location.search).get('screen')||'home');checkUpdate(true);", 1)
HTML = HTML.replace("if(location.search.includes('demo=1'))", CONTROLLER_JS + "\nif(location.search.includes('demo=1'))", 1)
HTML = HTML.replace('</head>', '<style>.retry-stream{margin-left:7px;padding:4px 9px;color:#fff;background:#131838;border:1px solid #26dffa;border-radius:5px;cursor:pointer}</style></head>', 1)
HTML = HTML.replace("OPEN VERIFIED UPDATE", "RESTART & INSTALL UPDATE").replace("Opening the verified update…", "Restarting to install the verified update in place…")
HTML = HTML.replace("</head>", f"<style>{GUIDE_LAYOUT_CSS}</style></head>", 1)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import parse_qs, urlsplit
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/":
                return self.send(200, HTML.encode(), "text/html; charset=utf-8")
            if parsed.path == "/hls.js":
                return self.send(200, resource("web/hls.min.js").read_bytes(), "text/javascript")
            if parsed.path == "/app-icon.png":
                return self.send(200, resource("assets/channel-forge-tv.png").read_bytes(), "image/png")
            if parsed.path == "/api/update":
                value = latest_tv_release(); value.pop("assets", None)
                return self.send(200, json.dumps(value).encode(), "application/json")
            if parsed.path == "/api/local-creator":
                return self.send(200, json.dumps(local_creator()).encode(), "application/json")
            if parsed.path == "/api/update/progress":
                job_id = parse_qs(parsed.query).get("id", [""])[0]
                with UPDATE_LOCK:
                    value = UPDATE_JOBS.get(job_id)
                if not value:
                    raise ValueError("Update job was not found.")
                return self.send(200, json.dumps(value).encode(), "application/json")
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
                    return self.send(response.status, body, response.headers.get_content_type())
            if parsed.path == "/stream":
                url = approved_content_url(parse_qs(parsed.query).get("url", [""])[0])
                headers = {"Accept": "*/*", "User-Agent": f"ChannelForge-TV/{VERSION}"}
                if self.headers.get("Range"):
                    headers["Range"] = self.headers["Range"]
                with open_creator_stream(url, headers) as (response, final_url):
                    if response.status >= 400:
                        return self.send(response.status, json.dumps({"error": f"Creator returned HTTP {response.status}."}).encode(), "application/json")
                    content_type = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip().lower()
                    if "mpegurl" in content_type or urlsplit(final_url).path.lower().endswith(".m3u8"):
                        manifest = response.read(2 * 1024 * 1024 + 1)
                        if len(manifest) > 2 * 1024 * 1024 or not manifest.decode("utf-8-sig").lstrip().startswith("#EXTM3U"):
                            raise ValueError("Creator returned an invalid HLS playlist.")
                        return self.send(response.status, rewrite_stream_manifest(manifest, final_url), "application/vnd.apple.mpegurl")
                    extra = {"Accept-Ranges": response.headers.get("Accept-Ranges", "bytes")}
                    if response.headers.get("Content-Range"):
                        extra["Content-Range"] = response.headers["Content-Range"]
                    return self.send_stream(response, content_type, extra)
            self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
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
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def send_content_headers(self, status, content_type, length, extra_headers):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        for name, value in extra_headers.items():
            if value:
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


def main():
    global APP_WINDOW
    import webview
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        threading.Thread(target=clean_windows_update_cache, args=(VERSION,), daemon=True).start()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="channel-forge-tv-server")
    thread.start()
    minimum = (760, 480) if sys.platform.startswith("linux") else (1100, 700)
    APP_WINDOW = webview.create_window("ChannelForge TV", url=url, width=1500, height=900, min_size=minimum, background_color="#080414")
    try:
        start_webview(webview)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

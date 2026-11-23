"""ChannelForge TV desktop viewer for the public creator directory."""
from __future__ import annotations

import http.server
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

VERSION = "3.4.2"
DIRECTORY = "https://directory.channelforge.tv/v1/catalog"
LOCAL_CREATOR = "http://127.0.0.1:8787"
GITHUB = "https://api.github.com/repos/dethkorex/Public_ChannelForge/releases/latest"
UPDATE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ChannelForge_TV" / "updates"
UPDATE_JOBS: dict[str, dict] = {}
UPDATE_LOCK = threading.RLock()
APP_WINDOW = None


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
    return {"current_version": VERSION, "latest_version": latest, "update_available": bool(latest) and version_tuple(latest) > version_tuple(VERSION), "release_url": str(payload.get("html_url", "")), "assets": assets}


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


def local_creator() -> dict:
    request = urllib.request.Request(f"{LOCAL_CREATOR}/api/status", headers={"Accept": "application/json", "User-Agent": f"ChannelForge-TV/{VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            status = json.load(response)
    except Exception:
        return {"available": False}
    channels = status.get("channels", []) if isinstance(status, dict) else []
    config = status.get("config", {}) if isinstance(status, dict) else {}
    profile = config.get("creator_profile", {}) if isinstance(config, dict) else {}
    plex_profile = config.get("plex_profile", {}) if isinstance(config, dict) else {}
    name = profile.get("name") or plex_profile.get("server_name") or "My ChannelForge Studio"
    return {"available": True, "creator": {
        "id": "local-creator", "name": str(name),
        "description": f"{len(channels)} local channel{'s' if len(channels) != 1 else ''} • Creator connected",
        "playlist_url": f"{LOCAL_CREATOR}/exports/channels.m3u",
        "vault_url": f"{LOCAL_CREATOR}/api/public/vault",
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
            asset_name = "ChannelForge_TV_Setup.exe" if "ChannelForge_TV_Setup.exe" in assets else "ChannelForge_TV.exe"
            checksum_name = "SHA256SUMS.txt"
            if asset_name not in assets or checksum_name not in assets:
                raise ValueError("The GitHub release is missing the TV app or its checksum.")
            report("checksum")
            checksum_request = urllib.request.Request(approved_download(assets[checksum_name]), headers={"User-Agent": f"ChannelForge-TV/{VERSION}"})
            with urllib.request.urlopen(checksum_request, timeout=30) as response:
                checksum_text = response.read(1_000_000).decode("utf-8", "replace")
            expected = next((line.split()[0].lower() for line in checksum_text.splitlines() if line.strip().endswith(asset_name)), "")
            if len(expected) != 64:
                raise ValueError("The TV app checksum was not published correctly.")
            UPDATE_DIR.mkdir(parents=True, exist_ok=True)
            suffix = "Setup" if asset_name.endswith("_Setup.exe") else "Portable"
            destination = UPDATE_DIR / f"ChannelForge_TV-{suffix}-v{release['latest_version']}.exe"
            temporary = destination.with_suffix(".downloading")
            request = urllib.request.Request(approved_download(assets[asset_name]), headers={"User-Agent": f"ChannelForge-TV/{VERSION}"})
            digest, downloaded = hashlib.sha256(), 0
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                total = int(response.headers.get("Content-Length", "0") or 0)
                if total > 500_000_000:
                    raise ValueError("The update file is unexpectedly large.")
                while chunk := response.read(1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > 500_000_000:
                        raise ValueError("The update file exceeded the safety limit.")
                    output.write(chunk); digest.update(chunk); report("downloading", downloaded, total)
            report("verifying", downloaded, downloaded)
            if digest.hexdigest().lower() != expected:
                temporary.unlink(missing_ok=True)
                raise ValueError("The downloaded TV app failed SHA-256 verification.")
            temporary.replace(destination)
            with UPDATE_LOCK:
                UPDATE_JOBS[job_id].update({"status": "complete", "stage": "complete", "percent": 100, "result": {"version": release["latest_version"], "path": str(destination)}})
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
    subprocess.Popen([str(path)], cwd=str(path.parent))
    if APP_WINDOW is not None:
        threading.Timer(0.8, APP_WINDOW.destroy).start()


HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>ChannelForge TV</title>
<style>*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#2b0752,#060410 43%);color:#fff;font-family:Segoe UI,sans-serif;overflow-x:hidden}header{height:92px;display:flex;align-items:center;padding:0 4vw;border-bottom:2px solid #13e4ff;gap:28px}h1{color:#fff22b;letter-spacing:4px;margin:0}.tag{color:#4fffd0;border:1px solid #19e9ff;padding:5px 10px}.update{margin-left:auto;background:#15112c;color:white;border:1px solid #19e9ff;padding:12px 18px;border-radius:7px}.wrap{padding:28px 4vw}.creators,.live,.vault{display:flex;gap:14px;overflow-x:auto;padding:10px 6px 25px}.creator,.channel{min-width:260px;background:#16132c;border:1px solid #41376a;border-radius:9px;padding:16px;color:white}.creator:focus,.channel:focus{outline:4px solid #fff22b}.vault{min-height:410px;align-items:center;perspective:1000px;gap:0;padding-left:34px}.tape{position:relative;flex:0 0 210px;height:340px;margin-left:-28px;background:#0d0a1c;border:3px solid #14dcf5;border-left-width:15px;padding:10px;box-shadow:16px 10px 18px #000c,5px 0 #44244f inset;transition:transform .22s,filter .22s;color:white;transform:rotateY(-22deg) translateZ(-35px);transform-style:preserve-3d;filter:brightness(.7)}.tape:nth-child(even){transform:rotateY(18deg) translateZ(-25px)}.tape:focus,.tape:hover{outline:none;border-color:#efff1c;transform:rotateY(0) translateY(-10px) translateZ(95px) scale(1.08);filter:brightness(1);z-index:5}.tape img{width:172px;height:255px;object-fit:cover;background:linear-gradient(145deg,#3a2858,#130d24)}.tape b{display:block;padding-top:8px}.pink{color:#ff3eb8}.cyan{color:#28eaff}video{display:none;position:fixed;inset:0;width:100%;height:100%;background:#000;z-index:10}video.playing{display:block}.close{display:none;position:fixed;z-index:11;right:25px;top:20px;padding:12px 18px;background:#111d;color:white;border:1px solid white}.close.show{display:block}.empty{color:#a89abb;padding:25px}.update-panel{position:fixed;z-index:20;right:28px;top:108px;width:390px;padding:20px;border:2px solid #18e7ff;border-radius:12px;background:#0c1028;box-shadow:0 18px 60px #000}.update-panel h3{color:#fff22b;margin-top:0}.update-panel progress{width:100%;height:18px;accent-color:#4fffd0}.update-panel button{margin-top:12px;padding:10px 14px;background:#203659;color:white;border:1px solid #18e7ff;border-radius:6px}.update-panel button.primary{background:#9b3d99;border-color:#ff4dd2}</style></head><body>
<style>.categories{display:flex;gap:10px;margin:0 0 12px}.categories span{padding:7px 13px;border:1px solid #ff3eb8;border-radius:999px;color:#8ff;background:#0f1330}.vault-layout{display:grid;grid-template-columns:48% 52%;gap:24px;min-height:440px}.vault{display:flex;flex-direction:column;perspective:900px;gap:7px;height:440px;overflow-y:auto;padding:8px 18px 20px 8px}.tape,.tape:nth-child(even){display:grid;grid-template-columns:78px 1fr auto;grid-template-rows:1fr 1fr;align-items:center;flex:none;width:100%;height:72px;margin:0;padding:6px 12px 6px 5px;border:1px solid #855938;border-left:9px solid #14dcf5;background:linear-gradient(90deg,#392416,#17131a 70%);box-shadow:8px 5px 12px #0009;transform:rotateX(-5deg) translateZ(-15px);filter:brightness(.82);text-align:left}.tape:focus,.tape:hover{transform:rotateX(0) translateZ(65px) scale(1.025);border:3px solid #efff1c;border-left-width:9px;filter:none}.tape img{grid-row:1/3;width:66px;height:56px;object-fit:cover;object-position:20% center;margin-right:12px;border:1px solid #f3c55b}.tape b{padding:0;font-size:17px}.tape small{color:#c8b89f}.tape i{grid-column:3;grid-row:1/3;background:#66ff9e;color:#07130c;padding:7px 10px;font-style:normal;font-weight:800}.selection{height:440px;border:1px solid #12dff9;border-radius:18px;padding:18px;background:linear-gradient(145deg,#11123b,#070a1d);display:grid;grid-template-columns:210px 1fr;gap:22px;box-shadow:0 0 36px #00c8ff18 inset}.selection>img{width:210px;height:315px;object-fit:cover;border:2px solid #18dcf4;border-radius:9px;box-shadow:16px 18px 28px #000b}.selection h2{color:#fff22d;font-size:29px;margin-top:8px}.selection p{line-height:1.55;min-height:115px}.selection button{width:100%;padding:14px;background:#a94d06;color:#fff1a0;border:2px solid #ffa20c;border-radius:7px;font-weight:900}.selection small{display:block;color:#8d829f;margin-top:12px}body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:50;background:repeating-linear-gradient(0deg,#0000 0 3px,#0000001c 4px)}header{position:sticky;top:0;z-index:8;background:#070817eF;backdrop-filter:blur(18px);box-shadow:0 12px 35px #0008}.brand-lockup{display:flex;align-items:center;gap:13px}.brand-lockup img{width:57px;height:57px;object-fit:contain;filter:drop-shadow(0 0 16px #13dfff66)}.brand-lockup h1{font-size:25px}.brand-lockup small{color:#57edff;letter-spacing:3px}.hero{position:relative;min-height:230px;display:flex;align-items:center;justify-content:space-between;overflow:hidden;margin-bottom:30px;padding:36px 42px;border:1px solid #652e9e;border-radius:22px;background:radial-gradient(circle at 78% 35%,#e12b8d55,transparent 25%),linear-gradient(120deg,#161149,#090a20 58%,#1d0929);box-shadow:0 25px 70px #0008}.hero::after{content:"";position:absolute;right:-70px;width:350px;height:350px;border:45px solid #11dcff1c;border-radius:50%;box-shadow:0 0 0 35px #fd2aa214}.eyebrow{color:#58f4ff;font-weight:900;letter-spacing:3px}.hero h2{font-size:46px;line-height:1.02;margin:10px 0;color:#fff}.hero p{max-width:650px;color:#bdc6dc}.signal-card{position:relative;z-index:1;display:grid;place-items:center;min-width:180px;height:130px;border:1px solid #ff42c8;border-radius:18px;background:#0b0924cc}.signal-card b{font-size:34px;color:#78ff9d}.signal-card span{color:#ffdc3f;letter-spacing:2px}.section-heading{display:flex;justify-content:space-between;align-items:end;margin-top:14px}.section-heading small{color:#8c96b8}.creator,.channel{background:linear-gradient(145deg,#1b1840,#101128);border-color:#403a71;box-shadow:0 12px 30px #0006;transition:.18s transform,.18s border-color}.creator:hover,.creator:focus,.channel:hover,.channel:focus{transform:translateY(-5px);border-color:#45eaff}.wrap{max-width:1600px;margin:auto}@media(max-width:900px){.vault-layout{grid-template-columns:1fr}.selection{display:none}.hero{padding:28px}.hero h2{font-size:34px}.signal-card{display:none}.brand-lockup small,.tag{display:none}}</style><header><div class="brand-lockup"><img src="/app-icon.png" alt=""><div><h1>CHANNELFORGE TV</h1><small>THE COMMUNITY DIAL</small></div></div><span class="tag">REWIND CINEMA</span><button id="update" class="update">UPDATES • v__VERSION__</button></header><aside id="update-panel" class="update-panel" hidden><h3>CHANNELFORGE TV UPDATE</h3><p id="update-message">Checking GitHub Releases…</p><progress id="update-progress" max="100" value="0"></progress><small id="update-bytes"></small><button id="restart-update" class="primary" hidden>OPEN VERIFIED UPDATE</button><button id="dismiss-update">NOT NOW</button></aside><main class="wrap"><section class="hero"><div><span class="eyebrow">COMMUNITY CHANNELS • ON DEMAND</span><h2>Turn the dial.<br>Find something unforgettable.</h2><p>Jump into live creator channels or browse every connected movie shelf from one cinematic TV interface.</p></div><div class="signal-card"><b>● LIVE</b><span>PUBLIC GUIDE</span></div></section><div class="section-heading"><h2>CREATOR STUDIOS</h2><small>Choose a studio to tune its channels and movies</small></div><div id="creators" class="creators"><span class="empty">Connecting to the public directory…</span></div><div class="section-heading"><h2 class="cyan">LIVE CHANNELS</h2><small>Select a channel to start watching</small></div><div id="live" class="live"></div><div class="section-heading"><h2 class="pink">REWIND CINEMA</h2><small>BROWSE THE MOVIE SHELF</small></div><nav class="categories"><span>CONTINUE WATCHING</span><span>80S BLOCKBUSTERS</span><span>90S ESSENTIALS</span><span>CREATOR PICKS</span></nav><div class="vault-layout"><div id="vault" class="vault"></div><aside id="selection" class="selection"><span class="empty">Choose a movie to see its details.</span></aside></div></main><video id="player" controls autoplay></video><button id="close" class="close">BACK TO REWIND CINEMA</button><script src="/hls.js"></script><script>
const $=s=>document.querySelector(s), esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let hls;
async function json(url){let r=await fetch('/proxy?url='+encodeURIComponent(url));if(!r.ok)throw Error(await r.text());return r.json()}
async function localJson(path,options={}){let r=await fetch(path,options),value=await r.json();if(!r.ok)throw Error(value.error||'Request failed');return value}
async function checkUpdate(onLaunch=false){try{let r=await localJson('/api/update');if(!r.update_available){$('#update').textContent='UP TO DATE ✓ • v__VERSION__';return}$('#update').textContent='UPDATE v'+r.latest_version+' AVAILABLE';if(!onLaunch||confirm('ChannelForge TV v'+r.latest_version+' is available. Download and verify it now?'))downloadUpdate()}catch(e){$('#update').textContent='UPDATE CHECK FAILED • v__VERSION__'}}
async function downloadUpdate(){let panel=$('#update-panel'),message=$('#update-message'),bar=$('#update-progress'),bytes=$('#update-bytes');panel.hidden=false;message.textContent='Preparing verified GitHub download…';bar.value=0;$('#restart-update').hidden=true;try{let started=await localJson('/api/update/start',{method:'POST'});panel.dataset.job=started.job_id;while(true){let job=await localJson('/api/update/progress?id='+encodeURIComponent(started.job_id));bar.value=job.percent||0;message.textContent={checking:'Checking official release…',checksum:'Downloading checksum…',downloading:'Downloading ChannelForge TV…',verifying:'Verifying SHA-256…',complete:'Update verified and ready ✓',error:'Update failed'}[job.stage]||job.stage;bytes.textContent=job.total?Math.round(job.downloaded/1048576)+' MB of '+Math.round(job.total/1048576)+' MB':'';if(job.status==='error')throw Error(job.error||'Update failed');if(job.status==='complete'){$('#restart-update').hidden=false;return}await new Promise(resolve=>setTimeout(resolve,400))}}catch(e){message.textContent=e.message;bytes.textContent='The existing app was not changed.'}}
async function load(){let creators=[],directoryError='';try{let local=await localJson('/api/local-creator');if(local.available&&local.creator)creators.push(local.creator)}catch(_error){}try{let data=await json('__DIRECTORY__');for(const c of data.creators||[])if(!creators.some(existing=>existing.playlist_url===c.playlist_url))creators.push(c)}catch(error){directoryError=error.message}$('#creators').innerHTML='';for(const c of creators){let b=document.createElement('button');b.className='creator';b.innerHTML=(c.logo_url?'<img src="'+esc(c.logo_url)+'" style="width:46px;height:46px;object-fit:contain;float:left;margin-right:12px">':'')+'<b>'+esc(c.name)+'</b><br><small>'+esc(c.description)+(c.local?' • THIS PC':'')+'</small>';b.onclick=()=>studio(c);$('#creators').append(b)}if(creators.length)studio(creators[0]);else{$('#creators').innerHTML='<span class="empty">No Creator is connected. Open ChannelForge Creator on this PC to load your channels.'+(directoryError?'<br><small>Public directory is currently unavailable.</small>':'')+'</span>';$('#live').innerHTML='<span class="empty">Waiting for ChannelForge Creator…</span>'}}
async function studio(c){$('#live').innerHTML='<span class="empty">Loading live channels…</span>';$('#vault').innerHTML='<span class="empty">Loading movies and posters…</span>';try{let response=await fetch('/proxy?url='+encodeURIComponent(c.playlist_url));if(!response.ok)throw Error('The channel playlist could not be loaded.');let m=await response.text(),lines=m.split(/\r?\n/),name='',channelCount=0;$('#live').innerHTML='';for(const line of lines){if(line.startsWith('#EXTINF:'))name=line.slice(line.lastIndexOf(',')+1);else if(line&&!line.startsWith('#')&&name){let b=document.createElement('button');b.className='channel';b.textContent='LIVE • '+name;b.onclick=()=>play(line);$('#live').append(b);name='';channelCount++}}if(!channelCount)$('#live').innerHTML='<span class="empty">This Creator has no published channels yet.</span>';let v=await json(c.vault_url);$('#vault').innerHTML='';for(const x of v.items){let b=document.createElement('button');b.className='tape';b.innerHTML='<img loading="lazy" src="'+esc(x.poster_url)+'"><b>'+esc(x.title)+'</b><small>'+esc(x.year)+' • '+esc(x.type)+'</small><i>PLAY ▶</i>';b.onfocus=()=>selectTape(x);b.onmouseenter=()=>selectTape(x);b.onclick=()=>play(x.stream_url);$('#vault').append(b)}if(v.items.length){selectTape(v.items[0]);$('#vault').firstElementChild.focus()}else $('#vault').innerHTML='<span class="empty">This creator has not added movies to Rewind Cinema yet.</span>'}catch(e){$('#live').innerHTML='<span class="empty">Could not load channels: '+esc(e.message)+'</span>';$('#vault').innerHTML='<span class="empty">Could not load this studio: '+esc(e.message)+'</span>'}}
function selectTape(x){$('#selection').innerHTML='<img src="'+esc(x.poster_url)+'"><div><h2>'+esc(x.title)+'</h2><b class="pink">'+esc(x.year)+'</b> • '+esc(x.type)+'<p>'+esc(x.summary||'No description supplied.')+'</p><button id="selected-play">▶ PLAY MOVIE</button><small>Poster and movie data supplied automatically by the creator library.</small></div>';$('#selected-play').onclick=()=>play(x.stream_url)}
function play(url){let v=$('#player'),source='/stream?url='+encodeURIComponent(url);if(hls)hls.destroy();if(v.canPlayType('application/vnd.apple.mpegurl'))v.src=source;else if(Hls.isSupported()){hls=new Hls();hls.loadSource(source);hls.attachMedia(v)}v.classList.add('playing');$('#close').classList.add('show');v.play()}
$('#close').onclick=()=>{$('#player').pause();$('#player').classList.remove('playing');$('#close').classList.remove('show')};$('#update').onclick=()=>checkUpdate(false);$('#dismiss-update').onclick=()=>$('#update-panel').hidden=true;$('#restart-update').onclick=async()=>{await localJson('/api/update/launch?id='+encodeURIComponent($('#update-panel').dataset.job),{method:'POST'});$('#update-message').textContent='Opening the verified update…'};
function demo(){let titles=['Neon Nights','The Last Arcade','Galaxy Rangers','Video Dreams','Summer Quest'],colors=['#bd205c','#a9560c','#069e9a','#792091','#d64a16'];$('#creators').innerHTML='<button class="creator"><b>RETRO WAVE STUDIO</b><br><small>Custom creator branding • online</small></button>';$('#live').innerHTML='<button class="channel">LIVE • SATURDAY MORNING</button><button class="channel">LIVE • SCI-FI AFTER DARK</button>';$('#vault').innerHTML='';titles.forEach((title,i)=>{let svg='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="'+colors[i]+'"/><stop offset="1" stop-color="#09142d"/></linearGradient></defs><rect width="400" height="600" fill="url(#g)"/><circle cx="90" cy="120" r="55" fill="#ffe43b" opacity=".8"/><text x="200" y="310" fill="white" font-size="38" font-family="sans-serif" font-weight="bold" text-anchor="middle">'+title+'</text></svg>');let x={title,year:1984+i,type:'movie',summary:'A creator-curated retro favorite with poster art and movie details loaded automatically from the connected library.',poster_url:svg,stream_url:''},b=document.createElement('button');b.className='tape';b.innerHTML='<img src="'+svg+'"><b>'+title+'</b><small>'+x.year+' • movie</small><i>PLAY ▶</i>';b.onfocus=()=>selectTape(x);b.onmouseenter=()=>selectTape(x);$('#vault').append(b);if(i===0)selectTape(x)});$('#vault').firstElementChild.focus()}if(location.search.includes('demo=1'))demo();else load();checkUpdate(true);</script></body></html>'''.replace("__VERSION__", VERSION).replace("__DIRECTORY__", DIRECTORY).replace("__GITHUB__", GITHUB)


SIDEBAR_CSS = r'''.hamburger{margin:0;background:#11132f;border:1px solid #20e4ff;color:#fff;font-size:24px;line-height:1;padding:12px 14px;border-radius:10px}.app-sidebar{position:fixed;z-index:7;left:0;top:92px;bottom:0;width:235px;padding:24px 14px;background:#090a1def;border-right:1px solid #1eddfb55;backdrop-filter:blur(18px);transition:.2s width,.2s transform}.app-sidebar nav{display:grid;gap:8px}.app-sidebar button{display:grid;grid-template-columns:32px 1fr;align-items:center;text-align:left;width:100%;padding:14px 12px;border:1px solid transparent;border-radius:10px;background:transparent;color:#c3cbe2;font-weight:800}.app-sidebar button:hover,.app-sidebar button:focus,.app-sidebar button.active{background:#1a1741;border-color:#ff3eb8;color:#fff}.app-sidebar button i{font-style:normal;font-size:20px;text-align:center}.app-sidebar .menu-label{white-space:nowrap;overflow:hidden}.sidebar-footer{position:absolute;left:18px;right:18px;bottom:22px;color:#7e89a8;font-size:11px}.wrap{margin-left:235px;transition:.2s margin-left}.menu-collapsed .app-sidebar{width:78px}.menu-collapsed .app-sidebar .menu-label,.menu-collapsed .sidebar-footer{display:none}.menu-collapsed .wrap{margin-left:78px}[data-section-block][hidden]{display:none!important}.section-placeholder{min-height:360px;display:grid;place-content:center;text-align:center;border:1px dashed #3c3a6d;border-radius:20px;background:#10112688;color:#9ca6c4;padding:35px}.section-placeholder b{font-size:25px;color:#fff}.section-placeholder span{max-width:480px;margin:9px auto}.screen-title h2{font-size:35px;margin-bottom:4px}.screen-title p{color:#929dbc}@media(max-width:900px){.app-sidebar{transform:translateX(-100%);width:235px}.menu-open .app-sidebar{transform:none}.wrap,.menu-collapsed .wrap{margin-left:0}.menu-collapsed .app-sidebar{width:235px}.menu-collapsed .app-sidebar .menu-label,.menu-collapsed .sidebar-footer{display:block}}'''
SIDEBAR_HTML = r'''<aside class="app-sidebar"><nav><button class="active" data-screen="home"><i>⌂</i><span class="menu-label">Home</span></button><button data-screen="live"><i>●</i><span class="menu-label">Live TV</span></button><button data-screen="cinema"><i>▣</i><span class="menu-label">Rewind Cinema</span></button><button data-screen="continue"><i>▶</i><span class="menu-label">Continue Watching</span></button><button data-screen="favorites"><i>★</i><span class="menu-label">Favorites</span></button><button data-screen="updates"><i>↻</i><span class="menu-label">Updates</span></button></nav><div class="sidebar-footer">ChannelForge TV • v__VERSION__<br>Keyboard and remote friendly.</div></aside>'''
EXTRA_SCREENS = r'''<section data-section-block="continue" hidden><div class="screen-title"><span class="eyebrow">PICK UP WHERE YOU LEFT OFF</span><h2>Continue Watching</h2></div><div class="section-placeholder"><b>No unfinished movies yet</b><span>Playback history will appear here as you watch Rewind Cinema.</span></div></section><section data-section-block="favorites" hidden><div class="screen-title"><span class="eyebrow">YOUR COLLECTION</span><h2>Favorites</h2></div><div class="section-placeholder"><b>Your favorites shelf is ready</b><span>Favorite channels and movies will be stored locally on this device.</span></div></section><section data-section-block="updates" hidden><div class="screen-title"><span class="eyebrow">APPLICATION</span><h2>Updates</h2><p>Downloads come only from the official GitHub release and are SHA-256 verified.</p></div><div class="section-placeholder"><b id="screen-update-status">Checking for updates…</b><span>Select Check Now to refresh release status.</span><button id="screen-check-update" class="update">CHECK NOW</button></div></section>'''
HTML = HTML.replace("</style><header>", f"</style><style>{SIDEBAR_CSS}</style><header>")
HTML = HTML.replace('<header><div class="brand-lockup">', '<header><button id="menu-toggle" class="hamburger" aria-label="Open navigation">☰</button><div class="brand-lockup">')
HTML = HTML.replace('</header><aside id="update-panel"', f'</header>{SIDEBAR_HTML.replace("__VERSION__", VERSION)}<aside id="update-panel"')
HTML = HTML.replace('<section class="hero">', '<section class="hero" data-section-block="home">', 1)
HTML = HTML.replace('<div class="section-heading"><h2>CREATOR STUDIOS', '<div class="section-heading" data-section-block="home"><h2>CREATOR STUDIOS', 1)
HTML = HTML.replace('<div id="creators" class="creators">', '<div id="creators" class="creators" data-section-block="home">', 1)
HTML = HTML.replace('<div class="section-heading"><h2 class="cyan">LIVE CHANNELS', '<div class="section-heading" data-section-block="live" hidden><h2 class="cyan">LIVE CHANNELS', 1)
HTML = HTML.replace('<div id="live" class="live">', '<div id="live" class="live" data-section-block="live" hidden>', 1)
HTML = HTML.replace('<div class="section-heading"><h2 class="pink">REWIND CINEMA', '<div class="section-heading" data-section-block="cinema" hidden><h2 class="pink">REWIND CINEMA', 1)
HTML = HTML.replace('<nav class="categories">', '<nav class="categories" data-section-block="cinema" hidden>', 1)
HTML = HTML.replace('<div class="vault-layout">', '<div class="vault-layout" data-section-block="cinema" hidden>', 1)
HTML = HTML.replace('</main><video id="player"', f'{EXTRA_SCREENS}</main><video id="player"', 1)
HTML = HTML.replace("async function checkUpdate(onLaunch=false)", "function showScreen(name){document.querySelectorAll('[data-section-block]').forEach(block=>block.hidden=block.dataset.sectionBlock!==name);document.querySelectorAll('[data-screen]').forEach(button=>button.classList.toggle('active',button.dataset.screen===name));if(innerWidth<=900)document.body.classList.remove('menu-open');scrollTo({top:0,behavior:'smooth'})}\nasync function checkUpdate(onLaunch=false)", 1)
HTML = HTML.replace("b.onclick=()=>studio(c)", "b.onclick=()=>{studio(c);showScreen('live')}", 1)
HTML = HTML.replace("$('#close').onclick", "$('#menu-toggle').onclick=()=>{if(innerWidth<=900)document.body.classList.toggle('menu-open');else document.body.classList.toggle('menu-collapsed')};document.querySelectorAll('[data-screen]').forEach(button=>button.onclick=()=>{showScreen(button.dataset.screen);if(button.dataset.screen==='updates')checkUpdate(false)});$('#screen-check-update').onclick=()=>checkUpdate(false);$('#close').onclick", 1)
HTML = HTML.replace("$('#update').onclick=()=>checkUpdate(false)", "$('#update').onclick=()=>{showScreen('updates');checkUpdate(false)}", 1)
HTML = HTML.replace("if(location.search.includes('demo=1'))demo();else load();checkUpdate(true);", "if(location.search.includes('demo=1'))demo();else load();showScreen(new URLSearchParams(location.search).get('screen')||'home');checkUpdate(true);", 1)


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
                url = approved_content_url(parse_qs(parsed.query).get("url", [""])[0])
                request = urllib.request.Request(url, headers={"User-Agent": f"ChannelForge-TV/{VERSION}"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    return self.send(response.status, response.read(64 * 1024 * 1024), response.headers.get_content_type())
            if parsed.path == "/stream":
                url = approved_content_url(parse_qs(parsed.query).get("url", [""])[0])
                headers = {"Accept": "*/*", "User-Agent": f"ChannelForge-TV/{VERSION}"}
                if self.headers.get("Range"):
                    headers["Range"] = self.headers["Range"]
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=45) as response:
                    data = response.read(64 * 1024 * 1024 + 1)
                    if len(data) > 64 * 1024 * 1024:
                        raise ValueError("The media segment exceeded the 64 MB safety limit.")
                    content_type = response.headers.get_content_type()
                    final_url = response.geturl()
                    if data.lstrip().startswith(b"#EXTM3U") or "mpegurl" in content_type:
                        data = rewrite_stream_manifest(data, final_url)
                        content_type = "application/vnd.apple.mpegurl"
                    extra = {"Accept-Ranges": response.headers.get("Accept-Ranges", "bytes")}
                    if response.headers.get("Content-Range"):
                        extra["Content-Range"] = response.headers["Content-Range"]
                    return self.send(getattr(response, "status", 200), data, content_type, extra)
            self.send_error(404)
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
        self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        for name, value in (extra_headers or {}).items():
            if value:
                self.send_header(name, value)
        self.end_headers(); self.wfile.write(body)

    def log_message(self, *_args):
        pass


def main():
    global APP_WINDOW
    import webview
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="channel-forge-tv-server")
    thread.start()
    APP_WINDOW = webview.create_window("ChannelForge TV", url=url, width=1500, height=900, min_size=(1100, 700), background_color="#080414")
    try:
        webview.start(debug=False)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

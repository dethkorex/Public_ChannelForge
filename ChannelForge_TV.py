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

VERSION = "3.4.6"
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
        "guide_url": f"{LOCAL_CREATOR}/exports/guide.xml",
        "playlist_url": f"{LOCAL_CREATOR}/exports/channels.m3u",
        "status_url": f"{LOCAL_CREATOR}/api/public/channels",
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
    command = [str(path)]
    if getattr(sys, "frozen", False) and "setup" in path.name.lower():
        command.extend(["--silent-update", "--target-exe", str(Path(sys.executable).resolve()), "--wait-pid", str(os.getpid())])
    subprocess.Popen(command, cwd=str(path.parent), creationflags=0x08000000 if sys.platform == "win32" else 0)
    if APP_WINDOW is not None:
        threading.Timer(0.8, APP_WINDOW.destroy).start()


HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>ChannelForge TV</title>
<style>*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#2b0752,#060410 43%);color:#fff;font-family:Segoe UI,sans-serif;overflow-x:hidden}header{height:92px;display:flex;align-items:center;padding:0 4vw;border-bottom:2px solid #13e4ff;gap:28px}h1{color:#fff22b;letter-spacing:4px;margin:0}.tag{color:#4fffd0;border:1px solid #19e9ff;padding:5px 10px}.update{margin-left:auto;background:#15112c;color:white;border:1px solid #19e9ff;padding:12px 18px;border-radius:7px}.wrap{padding:28px 4vw}.creators,.live,.vault{display:flex;gap:14px;overflow-x:auto;padding:10px 6px 25px}.creator,.channel{min-width:260px;background:#16132c;border:1px solid #41376a;border-radius:9px;padding:16px;color:white}.creator:focus,.channel:focus{outline:4px solid #fff22b}.vault{min-height:410px;align-items:center;perspective:1000px;gap:0;padding-left:34px}.tape{position:relative;flex:0 0 210px;height:340px;margin-left:-28px;background:#0d0a1c;border:3px solid #14dcf5;border-left-width:15px;padding:10px;box-shadow:16px 10px 18px #000c,5px 0 #44244f inset;transition:transform .22s,filter .22s;color:white;transform:rotateY(-22deg) translateZ(-35px);transform-style:preserve-3d;filter:brightness(.7)}.tape:nth-child(even){transform:rotateY(18deg) translateZ(-25px)}.tape:focus,.tape:hover{outline:none;border-color:#efff1c;transform:rotateY(0) translateY(-10px) translateZ(95px) scale(1.08);filter:brightness(1);z-index:5}.tape img{width:172px;height:255px;object-fit:cover;background:linear-gradient(145deg,#3a2858,#130d24)}.tape b{display:block;padding-top:8px}.pink{color:#ff3eb8}.cyan{color:#28eaff}video{display:none;position:fixed;inset:0;width:100%;height:100%;background:#000;z-index:10}video.playing{display:block}.close{display:none;position:fixed;z-index:11;right:25px;top:20px;padding:12px 18px;background:#111d;color:white;border:1px solid white}.close.show{display:block}.empty{color:#a89abb;padding:25px}.update-panel{position:fixed;z-index:20;right:28px;top:108px;width:390px;padding:20px;border:2px solid #18e7ff;border-radius:12px;background:#0c1028;box-shadow:0 18px 60px #000}.update-panel h3{color:#fff22b;margin-top:0}.update-panel progress{width:100%;height:18px;accent-color:#4fffd0}.update-panel button{margin-top:12px;padding:10px 14px;background:#203659;color:white;border:1px solid #18e7ff;border-radius:6px}.update-panel button.primary{background:#9b3d99;border-color:#ff4dd2}</style></head><body>
<style>.categories{display:flex;gap:10px;margin:0 0 12px}.categories span{padding:7px 13px;border:1px solid #ff3eb8;border-radius:999px;color:#8ff;background:#0f1330}.vault-layout{display:grid;grid-template-columns:48% 52%;gap:24px;min-height:440px}.vault{display:flex;flex-direction:column;perspective:900px;gap:7px;height:440px;overflow-y:auto;padding:8px 18px 20px 8px}.tape,.tape:nth-child(even){display:grid;grid-template-columns:78px 1fr auto;grid-template-rows:1fr 1fr;align-items:center;flex:none;width:100%;height:72px;margin:0;padding:6px 12px 6px 5px;border:1px solid #855938;border-left:9px solid #14dcf5;background:linear-gradient(90deg,#392416,#17131a 70%);box-shadow:8px 5px 12px #0009;transform:rotateX(-5deg) translateZ(-15px);filter:brightness(.82);text-align:left}.tape:focus,.tape:hover{transform:rotateX(0) translateZ(65px) scale(1.025);border:3px solid #efff1c;border-left-width:9px;filter:none}.tape img{grid-row:1/3;width:66px;height:56px;object-fit:cover;object-position:20% center;margin-right:12px;border:1px solid #f3c55b}.tape b{padding:0;font-size:17px}.tape small{color:#c8b89f}.tape i{grid-column:3;grid-row:1/3;background:#66ff9e;color:#07130c;padding:7px 10px;font-style:normal;font-weight:800}.selection{height:440px;border:1px solid #12dff9;border-radius:18px;padding:18px;background:linear-gradient(145deg,#11123b,#070a1d);display:grid;grid-template-columns:210px 1fr;gap:22px;box-shadow:0 0 36px #00c8ff18 inset}.selection>img{width:210px;height:315px;object-fit:cover;border:2px solid #18dcf4;border-radius:9px;box-shadow:16px 18px 28px #000b}.selection h2{color:#fff22d;font-size:29px;margin-top:8px}.selection p{line-height:1.55;min-height:115px}.selection button{width:100%;padding:14px;background:#a94d06;color:#fff1a0;border:2px solid #ffa20c;border-radius:7px;font-weight:900}.selection small{display:block;color:#8d829f;margin-top:12px}body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:50;background:repeating-linear-gradient(0deg,#0000 0 3px,#0000001c 4px)}header{position:sticky;top:0;z-index:8;background:#070817eF;backdrop-filter:blur(18px);box-shadow:0 12px 35px #0008}.brand-lockup{display:flex;align-items:center;gap:13px}.brand-lockup img{width:57px;height:57px;object-fit:contain;filter:drop-shadow(0 0 16px #13dfff66)}.brand-lockup h1{font-size:25px}.brand-lockup small{color:#57edff;letter-spacing:3px}.hero{position:relative;min-height:230px;display:flex;align-items:center;justify-content:space-between;overflow:hidden;margin-bottom:30px;padding:36px 42px;border:1px solid #652e9e;border-radius:22px;background:radial-gradient(circle at 78% 35%,#e12b8d55,transparent 25%),linear-gradient(120deg,#161149,#090a20 58%,#1d0929);box-shadow:0 25px 70px #0008}.hero::after{content:"";position:absolute;right:-70px;width:350px;height:350px;border:45px solid #11dcff1c;border-radius:50%;box-shadow:0 0 0 35px #fd2aa214}.eyebrow{color:#58f4ff;font-weight:900;letter-spacing:3px}.hero h2{font-size:46px;line-height:1.02;margin:10px 0;color:#fff}.hero p{max-width:650px;color:#bdc6dc}.signal-card{position:relative;z-index:1;display:grid;place-items:center;min-width:180px;height:130px;border:1px solid #ff42c8;border-radius:18px;background:#0b0924cc}.signal-card b{font-size:34px;color:#78ff9d}.signal-card span{color:#ffdc3f;letter-spacing:2px}.section-heading{display:flex;justify-content:space-between;align-items:end;margin-top:14px}.section-heading small{color:#8c96b8}.creator,.channel{background:linear-gradient(145deg,#1b1840,#101128);border-color:#403a71;box-shadow:0 12px 30px #0006;transition:.18s transform,.18s border-color}.creator:hover,.creator:focus,.channel:hover,.channel:focus{transform:translateY(-5px);border-color:#45eaff}.wrap{max-width:1600px;margin:auto}@media(max-width:900px){.vault-layout{grid-template-columns:1fr}.selection{display:none}.hero{padding:28px}.hero h2{font-size:34px}.signal-card{display:none}.brand-lockup small,.tag{display:none}}</style><header><div class="brand-lockup"><img src="/app-icon.png" alt=""><div><h1>CHANNELFORGE TV</h1><small>THE COMMUNITY DIAL</small></div></div><span class="tag">REWIND CINEMA</span><button id="update" class="update">UPDATES • v__VERSION__</button></header><aside id="update-panel" class="update-panel" hidden><h3>CHANNELFORGE TV UPDATE</h3><p id="update-message">Checking GitHub Releases…</p><progress id="update-progress" max="100" value="0"></progress><small id="update-bytes"></small><button id="restart-update" class="primary" hidden>OPEN VERIFIED UPDATE</button><button id="dismiss-update">NOT NOW</button></aside><main class="wrap"><section class="hero"><div><span class="eyebrow">COMMUNITY CHANNELS • ON DEMAND</span><h2>Turn the dial.<br>Find something unforgettable.</h2><p>Jump into live creator channels or browse every connected movie shelf from one cinematic TV interface.</p></div><div class="signal-card"><b>● LIVE</b><span>PUBLIC GUIDE</span></div></section><div class="section-heading"><h2>CREATOR STUDIOS</h2><small>Choose a studio to tune its channels and movies</small></div><div id="creators" class="creators"><span class="empty">Connecting to the public directory…</span></div><div class="section-heading"><h2 class="cyan">LIVE CHANNELS</h2><small>Select a channel to start watching</small></div><div id="live" class="live"></div><div class="section-heading"><h2 class="pink">REWIND CINEMA</h2><small>BROWSE THE MOVIE SHELF</small></div><nav class="categories"><span>CONTINUE WATCHING</span><span>80S BLOCKBUSTERS</span><span>90S ESSENTIALS</span><span>CREATOR PICKS</span></nav><div class="vault-layout"><div id="vault" class="vault"></div><aside id="selection" class="selection"><span class="empty">Choose a movie to see its details.</span></aside></div></main><video id="player" controls autoplay></video><button id="close" class="close">BACK TO REWIND CINEMA</button><script src="/hls.js"></script><script>
const $=s=>document.querySelector(s), esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let hls,vaultItems=[],vaultRendered=0,studioLoadId=0,allCreators=[],liveChannels=[],activeStudio='all',guideSearch='',selectedCreator=null,loadedStudioId='',liveMode=false;
async function json(url){let r=await fetch('/proxy?url='+encodeURIComponent(url));if(!r.ok)throw Error(await r.text());return r.json()}
async function localJson(path,options={}){let r=await fetch(path,options),value=await r.json();if(!r.ok)throw Error(value.error||'Request failed');return value}
async function checkUpdate(onLaunch=false){try{let r=await localJson('/api/update'),version=r.latest_version||r.current_version||'__VERSION__';if($('#home-release-version'))$('#home-release-version').textContent='v'+version;if($('#home-changelog'))$('#home-changelog').textContent=r.notes||'Playback, guide, discovery, and application reliability improvements.';if($('#home-update-state'))$('#home-update-state').textContent=r.update_available?'A verified update is available.':'You are running the newest public release.';if(!r.update_available){$('#update').textContent='UP TO DATE ✓ • v__VERSION__';return}$('#update').textContent='UPDATE v'+r.latest_version+' AVAILABLE';if(!onLaunch||confirm('ChannelForge TV v'+r.latest_version+' is available. Download and verify it now?'))downloadUpdate()}catch(e){$('#update').textContent='OFFLINE • v__VERSION__';if($('#home-update-state'))$('#home-update-state').textContent='Release check is offline. The installed application still works normally.';if($('#home-changelog'))$('#home-changelog').textContent='v__VERSION__\n• Restored Windows video playback\n• Added automatic channel scanning and offline filtering\n• Added a full live program guide\n• Rebuilt Rewind Cinema as a vertical VHS shelf'} }
async function downloadUpdate(){let panel=$('#update-panel'),message=$('#update-message'),bar=$('#update-progress'),bytes=$('#update-bytes');panel.hidden=false;message.textContent='Preparing verified GitHub download…';bar.value=0;$('#restart-update').hidden=true;try{let started=await localJson('/api/update/start',{method:'POST'});panel.dataset.job=started.job_id;while(true){let job=await localJson('/api/update/progress?id='+encodeURIComponent(started.job_id));bar.value=job.percent||0;message.textContent={checking:'Checking official release…',checksum:'Downloading checksum…',downloading:'Downloading ChannelForge TV…',verifying:'Verifying SHA-256…',complete:'Update verified and ready ✓',error:'Update failed'}[job.stage]||job.stage;bytes.textContent=job.total?Math.round(job.downloaded/1048576)+' MB of '+Math.round(job.total/1048576)+' MB':'';if(job.status==='error')throw Error(job.error||'Update failed');if(job.status==='complete'){$('#restart-update').hidden=false;return}await new Promise(resolve=>setTimeout(resolve,400))}}catch(e){message.textContent=e.message;bytes.textContent='The existing app was not changed.'}}
function parsePlaylist(text,creator){let result=[],pending=null;for(const line of text.split(/\r?\n/)){if(line.startsWith('#EXTINF:')){let name=line.slice(line.lastIndexOf(',')+1),id=(line.match(/tvg-id="([^"]+)"/)||[])[1]||'',number=(line.match(/tvg-chno="([^"]+)"/)||[])[1]||'',logo=(line.match(/tvg-logo="([^"]+)"/)||[])[1]||'';pending={id,name,number,logo_url:logo,programs:[],online:true,creator_name:creator.name}}else if(line&&!line.startsWith('#')&&pending){result.push({...pending,stream_url:new URL(line,creator.playlist_url).href});pending=null}}return result}
function renderLiveGuide(){let hero=$('#live'),guide=$('#tv-guide'),filters=$('#studio-filters'),times=$('#guide-times'),now=Date.now(),studios=[...new Set(liveChannels.map(channel=>channel.creator_name||'ChannelForge Creator'))],term=guideSearch.trim().toLowerCase();filters.innerHTML=['all',...studios].map(studio=>'<button class="'+(activeStudio===studio?'active':'')+'" data-studio="'+esc(studio)+'">'+esc(studio==='all'?'ALL STUDIOS':studio)+'</button>').join('');filters.querySelectorAll('button').forEach(button=>button.onclick=()=>{activeStudio=button.dataset.studio;renderLiveGuide()});let channels=liveChannels.filter(channel=>(activeStudio==='all'||channel.creator_name===activeStudio)&&(!term||(channel.name+' '+channel.creator_name+' '+(channel.programs||[]).map(p=>p.title).join(' ')).toLowerCase().includes(term)));if(!channels.length){hero.innerHTML='<span class="empty">No online channels match this guide view. Keep Creator open, then scan again.</span>';guide.innerHTML='';times.innerHTML='';return}let featured=channels.find(channel=>(channel.programs||[]).some(p=>Date.parse(p.start)<=now&&now<Date.parse(p.stop)))||channels[0],current=(featured.programs||[]).find(p=>Date.parse(p.start)<=now&&now<Date.parse(p.stop)),progress=current?Math.max(0,Math.min(100,(now-Date.parse(current.start))/(Date.parse(current.stop)-Date.parse(current.start))*100)):0;hero.innerHTML='<button class="guide-preview">'+(featured.logo_url?'<img src="'+esc(featured.logo_url)+'" alt="">':'<strong>CF</strong>')+'<span><small>NOW PLAYING</small><b>'+esc(current?.title||featured.name)+'</b><em>'+esc(featured.creator_name||'ChannelForge Creator')+' • CH '+esc(featured.number||'—')+'</em><i><u style="width:'+progress+'%"></u></i></span></button>';hero.firstElementChild.onclick=()=>play(featured.stream_url);let halfHour=Math.floor(now/1800000)*1800000,windowEnd=halfHour+2*3600000;times.innerHTML='<span></span>'+[0,1,2,3].map(index=>'<time>'+new Date(halfHour+index*1800000).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})+'</time>').join('');guide.innerHTML='';for(const channel of channels){let row=document.createElement('section');row.className='guide-row';let programs=(channel.programs||[]).filter(p=>Date.parse(p.stop)>halfHour&&Date.parse(p.start)<windowEnd).slice(0,10);row.innerHTML='<button class="guide-station">'+(channel.logo_url?'<img src="'+esc(channel.logo_url)+'" alt="">':'<strong>'+esc(channel.number||'•')+'</strong>')+'<span><b>'+esc(channel.name)+'</b><small>'+esc(channel.creator_name||'ChannelForge Creator')+'</small></span></button><div class="guide-programs">'+(programs.length?programs.map(p=>{let start=Date.parse(p.start),stop=Date.parse(p.stop),current=start<=now&&now<stop,visibleStart=Math.max(start,halfHour),visibleStop=Math.min(stop,windowEnd),width=Math.max(8,(visibleStop-visibleStart)/(windowEnd-halfHour)*100);return '<button class="guide-program '+(current?'current':'')+'" style="flex-basis:'+width+'%"><b>'+esc(p.title)+'</b><time>'+new Date(start).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})+'–'+new Date(stop).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})+'</time></button>'}).join(''):'<span class="guide-unavailable">Schedule unavailable</span>')+'</div>';row.querySelector('.guide-station').onclick=()=>play(channel.stream_url);row.querySelectorAll('.guide-program').forEach(button=>button.onclick=()=>play(channel.stream_url));guide.append(row)}}
async function scanChannels(manual=false){let button=$('#scan-channels'),status=$('#scan-status');if(button){button.disabled=true;button.textContent='⌛ SCANNING…'}if(status)status.textContent='Checking every connected Creator…';let groups=await Promise.all(allCreators.map(async creator=>{try{if(creator.status_url){let value=await json(creator.status_url);return (value.channels||[]).filter(channel=>channel.online).map(channel=>({...channel,creator_name:creator.name}))}let response=await fetch('/proxy?url='+encodeURIComponent(creator.playlist_url));if(!response.ok)throw Error('offline');return parsePlaylist(await response.text(),creator)}catch(_error){return []}}));let seen=new Set();liveChannels=groups.flat().filter(channel=>channel.stream_url&&!seen.has(channel.stream_url)&&seen.add(channel.stream_url)).sort((a,b)=>String(a.number||'9999').localeCompare(String(b.number||'9999'),undefined,{numeric:true})||a.name.localeCompare(b.name));renderLiveGuide();if(status)status.textContent=liveChannels.length+' online channel'+(liveChannels.length===1?'':'s')+' • scanned '+new Date().toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});if(button){button.disabled=false;button.textContent='↻ SCAN FOR CHANNELS'}return liveChannels}
async function load(){let creators=[],directoryError='';try{let local=await localJson('/api/local-creator');if(local.available&&local.creator)creators.push(local.creator)}catch(_error){}try{let data=await json('__DIRECTORY__');for(const c of data.creators||[])if(!creators.some(existing=>existing.playlist_url===c.playlist_url))creators.push(c)}catch(error){directoryError=error.message}allCreators=creators;selectedCreator=creators[0]||null;$('#creators').innerHTML='';for(const c of creators){let b=document.createElement('button');b.className='creator';b.innerHTML=(c.logo_url?'<img src="'+esc(c.logo_url)+'" style="width:46px;height:46px;object-fit:contain;float:left;margin-right:12px">':'')+'<b>'+esc(c.name)+'</b><br><small>'+esc(c.description)+(c.local?' • THIS PC':'')+'</small>';b.onclick=()=>{selectedCreator=c;loadedStudioId='';studio(c);showScreen('cinema')};$('#creators').append(b)}if(creators.length){await scanChannels()}else{$('#creators').innerHTML='<span class="empty">No Creator is connected. Open ChannelForge Creator on this PC to load your channels.'+(directoryError?'<br><small>Public directory is currently unavailable.</small>':'')+'</span>';$('#live').innerHTML='<span class="empty">Waiting for ChannelForge Creator…</span>';$('#tv-guide').innerHTML=''}}
function renderVaultBatch(count=40){let box=$('#vault');if(!box||vaultRendered>=vaultItems.length)return;let fragment=document.createDocumentFragment(),end=Math.min(vaultRendered+count,vaultItems.length);for(;vaultRendered<end;vaultRendered++){let x=vaultItems[vaultRendered],b=document.createElement('button');b.className='tape';b.innerHTML='<span class="tape-ridge"></span><img loading="lazy" src="'+esc(x.poster_url)+'" alt=""><span class="tape-label"><b>'+esc(x.title)+'</b><small><span>CF HOME VIDEO • VHS / HI-FI</span><mark>'+esc(x.year||'N/A')+'</mark></small></span><i>INSERT ▷</i>';b.onfocus=()=>selectTape(x);b.onmouseenter=()=>selectTape(x);b.onclick=()=>play(x.stream_url);fragment.append(b)}box.append(fragment)}
async function studio(c){if(!c||loadedStudioId===c.id&&vaultItems.length)return;let loadId=++studioLoadId;$('#vault').innerHTML='<span class="empty">Loading movies and posters…</span>';try{let v=await json(c.vault_url);if(loadId!==studioLoadId)return;loadedStudioId=c.id;vaultItems=Array.isArray(v.items)?v.items:[];vaultRendered=0;$('#vault').innerHTML='';if(vaultItems.length){renderVaultBatch();selectTape(vaultItems[0]);$('#vault').firstElementChild.focus()}else $('#vault').innerHTML='<span class="empty">This creator has not added movies to Rewind Cinema yet.</span>'}catch(e){if(loadId!==studioLoadId)return;loadedStudioId='';$('#vault').innerHTML='<span class="empty">Could not load this studio: '+esc(e.message)+'</span>'}}
function selectTape(x){$('#selection').innerHTML='<div class="vhs-box"><img src="'+esc(x.poster_url)+'"><span>CHANNELFORGE HOME VIDEO</span></div><div><span class="eyebrow">SELECTED CASSETTE</span><h2>'+esc(x.title)+'</h2><b class="pink">'+esc(x.year)+'</b> • '+esc(x.type)+'<p>'+esc(x.summary||'No description supplied.')+'</p><button id="selected-play">▷ INSERT MOVIE</button><small>Insert this movie to begin playback. Poster and details come from the connected creator library.</small></div>';$('#selected-play').onclick=()=>play(x.stream_url)}
function liveEdge(v){if(!liveMode)return;if(hls?.liveSyncPosition&&Math.abs(v.currentTime-hls.liveSyncPosition)>6)v.currentTime=hls.liveSyncPosition;else if(v.buffered.length&&v.currentTime<v.buffered.end(v.buffered.length-1)-8)v.currentTime=v.buffered.end(v.buffered.length-1)-2}
function play(url){let v=$('#player'),source='/stream?url='+encodeURIComponent(url);liveMode=/\/live\/[^/?]+\.m3u8(?:$|\?)/.test(url);v.controls=!liveMode;v.pause();v.removeAttribute('src');v.load();if(hls){hls.destroy();hls=null}if(window.Hls&&Hls.isSupported()){let options=liveMode?{startPosition:-1,liveSyncDurationCount:2,maxLiveSyncPlaybackRate:1.2,backBufferLength:8,maxBufferLength:20}:{startPosition:-1,backBufferLength:120,maxBufferLength:60};hls=new Hls(options);hls.loadSource(source);hls.attachMedia(v);hls.on(Hls.Events.MANIFEST_PARSED,()=>v.play().catch(()=>{}));hls.on(Hls.Events.ERROR,(_event,data)=>{if(data.fatal&&data.type===Hls.ErrorTypes.MEDIA_ERROR)hls.recoverMediaError()})}else if(v.canPlayType('application/vnd.apple.mpegurl')){v.src=source;v.play().catch(()=>{})}v.classList.add('playing');$('#close').classList.add('show')}
$('#player').addEventListener('seeking',event=>liveEdge(event.currentTarget));$('#player').addEventListener('timeupdate',event=>liveEdge(event.currentTarget));$('#player').addEventListener('pause',event=>{if(liveMode&&event.currentTarget.classList.contains('playing'))event.currentTarget.play().catch(()=>{})});$('#vault').addEventListener('scroll',event=>{let box=event.currentTarget;if(box.scrollTop+box.clientHeight>=box.scrollHeight-500)renderVaultBatch()});$('#scan-channels').onclick=()=>scanChannels(true);$('#guide-search').oninput=event=>{guideSearch=event.target.value;renderLiveGuide()};setInterval(()=>{if(!document.hidden)scanChannels(false)},60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)scanChannels(false)});$('#close').onclick=()=>{liveMode=false;$('#player').classList.remove('playing');$('#player').pause();if(hls){hls.destroy();hls=null}$('#player').removeAttribute('src');$('#player').load();$('#close').classList.remove('show')};$('#update').onclick=()=>checkUpdate(false);$('#dismiss-update').onclick=()=>$('#update-panel').hidden=true;$('#restart-update').onclick=async()=>{await localJson('/api/update/launch?id='+encodeURIComponent($('#update-panel').dataset.job),{method:'POST'});$('#update-message').textContent='Opening the verified update…'};
function demo(){let titles=['Neon Nights','The Last Arcade','Galaxy Rangers','Video Dreams','Summer Quest'],colors=['#bd205c','#a9560c','#069e9a','#792091','#d64a16'];$('#creators').innerHTML='<button class="creator"><b>RETRO WAVE STUDIO</b><br><small>Custom creator branding • online</small></button>';$('#live').innerHTML='<button class="channel">LIVE • SATURDAY MORNING</button><button class="channel">LIVE • SCI-FI AFTER DARK</button>';$('#vault').innerHTML='';titles.forEach((title,i)=>{let svg='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="'+colors[i]+'"/><stop offset="1" stop-color="#09142d"/></linearGradient></defs><rect width="400" height="600" fill="url(#g)"/><circle cx="90" cy="120" r="55" fill="#ffe43b" opacity=".8"/><text x="200" y="310" fill="white" font-size="38" font-family="sans-serif" font-weight="bold" text-anchor="middle">'+title+'</text></svg>');let x={title,year:1984+i,type:'movie',summary:'A creator-curated retro favorite with poster art and movie details loaded automatically from the connected library.',poster_url:svg,stream_url:''},b=document.createElement('button');b.className='tape';b.innerHTML='<img src="'+svg+'"><b>'+title+'</b><small>'+x.year+' • movie</small><i>PLAY ▶</i>';b.onfocus=()=>selectTape(x);b.onmouseenter=()=>selectTape(x);$('#vault').append(b);if(i===0)selectTape(x)});$('#vault').firstElementChild.focus()}if(location.search.includes('demo=1'))demo();else load();checkUpdate(true);</script></body></html>'''.replace("__VERSION__", VERSION).replace("__DIRECTORY__", DIRECTORY).replace("__GITHUB__", GITHUB)


SIDEBAR_CSS = r'''.hamburger{margin:0;background:#11132f;border:1px solid #20e4ff;color:#fff;font-size:24px;line-height:1;padding:12px 14px;border-radius:10px}.app-sidebar{position:fixed;z-index:7;left:0;top:92px;bottom:0;width:235px;padding:24px 14px;background:#090a1def;border-right:1px solid #1eddfb55;backdrop-filter:blur(18px);transition:.2s width,.2s transform}.app-sidebar nav{display:grid;gap:8px}.app-sidebar button{display:grid;grid-template-columns:32px 1fr;align-items:center;text-align:left;width:100%;padding:14px 12px;border:1px solid transparent;border-radius:10px;background:transparent;color:#c3cbe2;font-weight:800}.app-sidebar button:hover,.app-sidebar button:focus,.app-sidebar button.active{background:#1a1741;border-color:#ff3eb8;color:#fff}.app-sidebar button i{font-style:normal;font-size:20px;text-align:center}.app-sidebar .menu-label{white-space:nowrap;overflow:hidden}.sidebar-footer{position:absolute;left:18px;right:18px;bottom:22px;color:#7e89a8;font-size:11px}.wrap{margin-left:235px;transition:.2s margin-left}.menu-collapsed .app-sidebar{width:78px}.menu-collapsed .app-sidebar .menu-label,.menu-collapsed .sidebar-footer{display:none}.menu-collapsed .wrap{margin-left:78px}[data-section-block][hidden]{display:none!important}.section-placeholder{min-height:360px;display:grid;place-content:center;text-align:center;border:1px dashed #3c3a6d;border-radius:20px;background:#10112688;color:#9ca6c4;padding:35px}.section-placeholder b{font-size:25px;color:#fff}.section-placeholder span{max-width:480px;margin:9px auto}.screen-title h2{font-size:35px;margin-bottom:4px}.screen-title p{color:#929dbc}@media(max-width:900px){.app-sidebar{transform:translateX(-100%);width:235px}.menu-open .app-sidebar{transform:none}.wrap,.menu-collapsed .wrap{margin-left:0}.menu-collapsed .app-sidebar{width:235px}.menu-collapsed .app-sidebar .menu-label,.menu-collapsed .sidebar-footer{display:block}}'''
GUIDE_CSS = r'''.guide-toolbar{display:flex;align-items:center;gap:16px;margin:8px 0 18px;padding:16px 18px;border:1px solid #2c3e72;border-radius:13px;background:#10142c}.guide-toolbar button{padding:12px 18px;border:1px solid #22e7ff;border-radius:8px;background:#152748;color:#fff;font-weight:900}.guide-toolbar button:disabled{opacity:.6}.guide-toolbar span{color:#9ea9c7}.live{padding-bottom:14px}.channel{display:flex;align-items:center;gap:12px;text-align:left}.channel img{width:48px;height:48px;object-fit:contain}.channel span:last-child{display:grid;gap:5px}.channel small{color:#9da8c5}.channel-number{display:grid;place-items:center;width:48px;height:48px;border-radius:12px;background:linear-gradient(145deg,#38e3f0,#8c59ff);color:#07101c;font-size:19px;font-weight:900}.tv-guide{display:grid;gap:7px;overflow-x:auto;padding:4px 0 38px}.guide-row{display:grid;grid-template-columns:220px minmax(850px,1fr);min-height:105px;border:1px solid #29335b;background:#0c1023}.guide-station{display:flex;align-items:center;gap:12px;padding:14px;border:0;border-right:1px solid #29335b;background:#121a35;color:#fff;text-align:left}.guide-station img{width:58px;height:58px;object-fit:contain}.guide-station strong{display:grid;place-items:center;min-width:58px;height:58px;border-radius:15px;background:linear-gradient(145deg,#20e4ef,#a24aff);color:#07101c;font-size:21px}.guide-station span{display:grid;gap:6px}.guide-station small{color:#8e9abb}.guide-programs{display:flex;min-width:0;overflow:hidden}.guide-program{flex:1 0 230px;display:grid;align-content:center;gap:6px;padding:12px 16px;border:0;border-right:1px solid #29335b;background:#10162d;color:#fff;text-align:left}.guide-program.current{background:linear-gradient(135deg,#263558,#162844);box-shadow:inset 0 0 0 2px #57f5cc}.guide-program time{color:#5af4d1;font-size:12px}.guide-program small{color:#8f9ab7}.guide-unavailable{display:grid;place-items:center;padding:20px;color:#7f89a6}@media(max-width:900px){.guide-toolbar{align-items:flex-start;flex-direction:column}.guide-row{grid-template-columns:170px minmax(700px,1fr)}}'''
GUIDE_STAGE_CSS = r'''.guide-mode .wrap{padding-top:14px}.guide-mode .section-heading[data-section-block="live"]{display:none!important}.live{display:block;overflow:visible;padding:0}.guide-preview{display:grid;grid-template-columns:230px 1fr;width:100%;min-height:165px;margin:0 0 12px;padding:13px;border:1px solid #15def5;border-radius:14px;background:radial-gradient(circle at 88% 20%,#d42e9a2c,transparent 24%),linear-gradient(120deg,#111741,#090d24);color:#fff;text-align:left;box-shadow:0 18px 42px #0006}.guide-preview>img,.guide-preview>strong{width:205px;height:135px;object-fit:contain;border:2px solid #ff3eb8;background:linear-gradient(160deg,#63206d,#13213d)}.guide-preview>strong{display:grid;place-items:center;color:#fff;font-size:54px}.guide-preview>span{display:grid;align-content:center;gap:9px;padding:0 20px}.guide-preview small{color:#4fe9ff;font:bold 12px Consolas,monospace;letter-spacing:1px}.guide-preview b{color:#fff52d;font-size:27px}.guide-preview em{color:#aab8d5;font:normal 14px Consolas,monospace}.guide-preview i{display:block;width:min(650px,90%);height:7px;border:1px solid #766f76;border-radius:8px;background:#403e44;overflow:hidden}.guide-preview u{display:block;height:100%;background:#6dffad;text-decoration:none}.guide-toolbar{justify-content:flex-start;margin-bottom:12px;padding:12px 14px}.guide-toolbar label{display:flex;align-items:center;gap:8px;margin-left:auto;padding:8px 12px;border:1px solid #22e7ff;border-radius:8px;background:#0a1024}.guide-toolbar input{width:190px;border:0;outline:0;background:transparent;color:#fff}.studio-filters{display:flex;gap:10px;overflow:hidden;margin:0 0 8px;padding:0 0 10px;border-bottom:1px solid #ff3eb844}.studio-filters button{padding:9px 15px;border:1px solid #25dcf6;border-radius:7px;background:#101630;color:#75efff;font-weight:800}.studio-filters button.active{border-color:#fff32d;background:#39320a;color:#fff59a}.guide-times{display:grid;grid-template-columns:220px repeat(4,1fr);min-width:0;padding:0 0 7px;color:#36e9ff;font:bold 14px Consolas,monospace;text-align:center}.tv-guide{gap:5px;overflow-x:hidden;padding-bottom:8px;scrollbar-width:none}.tv-guide::-webkit-scrollbar{display:none}.guide-row{grid-template-columns:220px minmax(0,1fr);min-height:88px}.guide-station{padding:10px}.guide-station strong{color:#fff52d;background:transparent;font-size:26px}.guide-programs{width:100%;min-width:0;background:repeating-linear-gradient(90deg,#0000 0 calc(25% - 1px),#2d3c64 calc(25% - 1px) 25%)}.guide-program{flex-grow:0;min-width:0;padding:11px 14px;background:#111a35;overflow:hidden}.guide-program.current{border:3px solid #eaff18;background:linear-gradient(135deg,#5b4815,#272212);box-shadow:inset 0 0 22px #ffd40022}.guide-program b{font-size:15px;overflow:hidden;text-overflow:ellipsis}.guide-program time{order:2;color:#8ea8bf}.guide-program small{order:3}@media(max-width:900px){.guide-preview{grid-template-columns:1fr}.guide-preview>img,.guide-preview>strong{width:100%}.guide-toolbar label{margin-left:0}.studio-filters{overflow-x:auto}.guide-times{grid-template-columns:150px repeat(4,1fr)}.guide-row{grid-template-columns:150px minmax(0,1fr)}}'''
HOME_CSS = r'''.home-panels{display:grid;grid-template-columns:minmax(280px,.8fr) minmax(420px,1.2fr);gap:22px;margin-top:24px}.home-card{min-height:255px;padding:28px;border:1px solid #393275;border-radius:18px;background:linear-gradient(145deg,#121536,#0a0b20);box-shadow:0 20px 55px #0006}.home-card h3{margin:8px 0 12px;font-size:26px;color:#fff}.home-card p{color:#aeb8d2;line-height:1.65}.release-heading{display:flex;align-items:center;justify-content:space-between;gap:20px}.release-heading strong{padding:7px 11px;border:1px solid #22e7ff;border-radius:8px;color:#72f8d7}.home-changelog{max-height:210px;overflow:auto;white-space:pre-wrap;color:#c8d1e8;line-height:1.55;font:14px/1.55 Segoe UI,sans-serif}@media(max-width:900px){.home-panels{grid-template-columns:1fr}}'''
VAULT_CSS = r'''body,.wrap{max-width:100%;overflow-x:hidden}.categories{flex-wrap:wrap}.vault-layout{grid-template-columns:minmax(560px,1.18fr) minmax(420px,.82fr);gap:26px;min-height:620px}.vault{height:calc(100vh - 285px);min-height:620px;max-height:780px;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;padding:10px 14px 30px 5px;scrollbar-width:thin;scrollbar-color:#ff43bc #11132c;scroll-snap-type:y proximity}.vault::-webkit-scrollbar{width:10px}.vault::-webkit-scrollbar-track{background:#11132c;border-radius:10px}.vault::-webkit-scrollbar-thumb{background:linear-gradient(#15e4f5,#ff43bc);border:2px solid #11132c;border-radius:10px}.tape,.tape:nth-child(even){position:relative;display:grid;grid-template-columns:12px 88px minmax(0,1fr) auto;grid-template-rows:1fr 1fr;align-items:center;flex:none;width:100%;height:82px;margin:0;padding:5px 12px 5px 0;border:1px solid #735136;border-left:0;border-radius:4px;background:linear-gradient(180deg,#38251b 0 12%,#18161a 13% 86%,#4b2c1d 87%);box-shadow:10px 8px 16px #0009,inset 0 1px #d9a66b55;transform:perspective(900px) rotateX(-3deg);filter:brightness(.86);text-align:left;scroll-snap-align:start;transition:.16s transform,.16s filter,.16s border-color}.tape::before,.tape::after{content:"";position:absolute;right:112px;width:14px;height:14px;border:2px solid #756b62;border-radius:50%;background:#09090c;box-shadow:inset 0 0 0 3px #242128}.tape::before{top:12px}.tape::after{bottom:12px}.tape:hover,.tape:focus{z-index:2;transform:perspective(900px) rotateX(0) translateX(6px) scale(.995);filter:none;border-color:#efff1c;box-shadow:0 0 0 2px #efff1c,14px 10px 22px #000b}.tape-ridge{grid-row:1/3;width:12px;height:100%;background:repeating-linear-gradient(90deg,#13dbea 0 2px,#066a7c 2px 4px)}.tape img{grid-column:2;grid-row:1/3;width:78px;height:68px;margin:0 10px 0 5px;object-fit:cover;object-position:center;border:1px solid #f3c55b;background:#0a0b12}.tape-label{grid-column:3;grid-row:1/3;display:grid;align-content:center;min-width:0;gap:7px;padding:8px 70px 8px 12px;border:1px solid #886944;background:linear-gradient(90deg,#e4d9bf,#bfb095);color:#171316;box-shadow:inset 0 0 18px #745b3d55}.tape-label b{overflow:hidden;padding:0;font-size:17px;text-overflow:ellipsis;white-space:nowrap}.tape-label small{color:#604c3d;font-family:Consolas,monospace}.tape i{grid-column:4;grid-row:1/3;min-width:94px;padding:9px 11px;border:1px solid #7bffad;background:#4fe88b;color:#07130c;font-style:normal;font-weight:900;text-align:center}.selection{position:sticky;top:112px;height:calc(100vh - 285px);min-height:620px;max-height:780px;grid-template-columns:minmax(190px,240px) 1fr;overflow:hidden;padding:22px}.vhs-box{position:relative;align-self:start;padding:8px 8px 34px;border:2px solid #12dff9;border-left:18px solid #d52b81;border-radius:7px;background:#080b18;box-shadow:18px 18px 30px #000b;transform:perspective(850px) rotateY(5deg)}.vhs-box img{display:block;width:100%;height:min(46vh,420px);object-fit:cover;background:#12152c}.vhs-box span{position:absolute;left:12px;right:8px;bottom:9px;overflow:hidden;color:#8ff4ff;font:10px Consolas,monospace;letter-spacing:1px;white-space:nowrap}.selection h2{margin-bottom:10px}.selection p{max-height:250px;overflow:auto;padding-right:8px;scrollbar-width:thin;scrollbar-color:#20ddeb transparent}.selection button{background:linear-gradient(90deg,#b64a05,#dc7b0a);letter-spacing:1px}.selection button:hover,.selection button:focus{background:#ff9b19;color:#160b00}@media(max-width:1100px){.vault-layout{grid-template-columns:1fr}.selection{position:relative;top:auto;height:auto;min-height:520px}.vault{height:620px}.tape-label{padding-right:20px}.tape::before,.tape::after{display:none}}'''
TAPE_DETAIL_CSS = r'''.tape-label small{display:flex;align-items:center;justify-content:space-between;gap:10px}.tape-label small span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tape-label mark{flex:none;padding:2px 6px;border:1px solid #946734;border-radius:3px;background:#b65b23;color:#fff;font:10px Consolas,monospace}.tape:nth-child(4n+2) .tape-ridge{background:repeating-linear-gradient(90deg,#ff3aa9 0 2px,#7c164e 2px 4px)}.tape:nth-child(4n+3) .tape-ridge{background:repeating-linear-gradient(90deg,#a7ff28 0 2px,#4f7c13 2px 4px)}.tape:nth-child(4n) .tape-ridge{background:repeating-linear-gradient(90deg,#ffbd21 0 2px,#835a00 2px 4px)}'''
SIDEBAR_HTML = r'''<aside class="app-sidebar"><nav><button class="active" data-screen="home"><i>⌂</i><span class="menu-label">Home</span></button><button data-screen="live"><i>●</i><span class="menu-label">Live TV</span></button><button data-screen="cinema"><i>▣</i><span class="menu-label">Rewind Cinema</span></button><button data-screen="continue"><i>▶</i><span class="menu-label">Continue Watching</span></button><button data-screen="favorites"><i>★</i><span class="menu-label">Favorites</span></button><button data-screen="updates"><i>↻</i><span class="menu-label">Updates</span></button></nav><div class="sidebar-footer">ChannelForge TV • v__VERSION__<br>Keyboard and remote friendly.</div></aside>'''
EXTRA_SCREENS = r'''<section data-section-block="continue" hidden><div class="screen-title"><span class="eyebrow">PICK UP WHERE YOU LEFT OFF</span><h2>Continue Watching</h2></div><div class="section-placeholder"><b>No unfinished movies yet</b><span>Playback history will appear here as you watch Rewind Cinema.</span></div></section><section data-section-block="favorites" hidden><div class="screen-title"><span class="eyebrow">YOUR COLLECTION</span><h2>Favorites</h2></div><div class="section-placeholder"><b>Your favorites shelf is ready</b><span>Favorite channels and movies will be stored locally on this device.</span></div></section><section data-section-block="updates" hidden><div class="screen-title"><span class="eyebrow">APPLICATION</span><h2>Updates</h2><p>Downloads come only from the official GitHub release and are SHA-256 verified.</p></div><div class="section-placeholder"><b id="screen-update-status">Checking for updates…</b><span>Select Check Now to refresh release status.</span><button id="screen-check-update" class="update">CHECK NOW</button></div></section>'''
HOME_DETAILS = r'''<section class="home-panels" data-section-block="home"><article class="home-card"><span class="eyebrow">WELCOME</span><h3>Your community television hub</h3><p>ChannelForge TV brings creator-made live channels and shared movie shelves into one remote-friendly application. Open Live TV to browse the automatic channel guide, or Rewind Cinema to explore movies from connected studios.</p></article><article class="home-card"><div class="release-heading"><div><span class="eyebrow">LATEST UPDATE</span><h3>Release notes</h3></div><strong id="home-release-version">v__VERSION__</strong></div><p id="home-update-state">Checking the official release…</p><pre id="home-changelog" class="home-changelog">Loading changelog…</pre></article></section>'''.replace("__VERSION__", VERSION)
HTML = HTML.replace("</style><header>", f"</style><style>{SIDEBAR_CSS}{GUIDE_CSS}{GUIDE_STAGE_CSS}{HOME_CSS}{VAULT_CSS}{TAPE_DETAIL_CSS}</style><header>")
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
HTML = HTML.replace('<div id="live" class="live"></div>', '<div id="live" class="live"></div><div class="guide-toolbar" data-section-block="live" hidden><button id="scan-channels">↻ SCAN FOR CHANNELS</button><span id="scan-status">Channels are scanned automatically.</span><label>⌕ <input id="guide-search" type="search" placeholder="Search guide"></label></div><div id="studio-filters" class="studio-filters" data-section-block="live" hidden></div><div id="guide-times" class="guide-times" data-section-block="live" hidden></div><div id="tv-guide" class="tv-guide" data-section-block="live" hidden></div>', 1)
HTML = HTML.replace('<div id="live" class="live">', '<div id="live" class="live" data-section-block="live" hidden>', 1)
HTML = HTML.replace('<div class="section-heading"><h2 class="pink">REWIND CINEMA', '<div class="section-heading" data-section-block="cinema" hidden><h2 class="pink">REWIND CINEMA', 1)
HTML = HTML.replace('<nav class="categories">', '<nav class="categories" data-section-block="cinema" hidden>', 1)
HTML = HTML.replace('<div class="vault-layout">', '<div class="vault-layout" data-section-block="cinema" hidden>', 1)
HTML = HTML.replace('</main><video id="player"', f'{EXTRA_SCREENS}</main><video id="player"', 1)
HTML = HTML.replace("async function checkUpdate(onLaunch=false)", "function showScreen(name){document.querySelectorAll('[data-section-block]').forEach(block=>block.hidden=block.dataset.sectionBlock!==name);document.querySelectorAll('[data-screen]').forEach(button=>button.classList.toggle('active',button.dataset.screen===name));document.body.classList.toggle('guide-mode',name==='live');if(name==='live'&&innerWidth>900)document.body.classList.add('menu-collapsed');if(name==='cinema'&&selectedCreator&&!vaultItems.length)studio(selectedCreator);if(innerWidth<=900)document.body.classList.remove('menu-open');scrollTo({top:0,behavior:'smooth'})}\nasync function checkUpdate(onLaunch=false)", 1)
HTML = HTML.replace("b.onclick=()=>studio(c)", "b.onclick=()=>{studio(c);showScreen('cinema')}", 1)
HTML = HTML.replace("$('#close').onclick", "$('#menu-toggle').onclick=()=>{if(innerWidth<=900)document.body.classList.toggle('menu-open');else document.body.classList.toggle('menu-collapsed')};document.querySelectorAll('[data-screen]').forEach(button=>button.onclick=()=>{showScreen(button.dataset.screen);if(button.dataset.screen==='updates')checkUpdate(false)});$('#screen-check-update').onclick=()=>checkUpdate(false);$('#close').onclick", 1)
HTML = HTML.replace("$('#update').onclick=()=>checkUpdate(false)", "$('#update').onclick=()=>{showScreen('updates');checkUpdate(false)}", 1)
HTML = HTML.replace("if(location.search.includes('demo=1'))demo();else load();checkUpdate(true);", "if(location.search.includes('demo=1'))demo();else load();showScreen(new URLSearchParams(location.search).get('screen')||'home');checkUpdate(true);", 1)
HTML = HTML.replace("OPEN VERIFIED UPDATE", "RESTART & INSTALL UPDATE").replace("Opening the verified update…", "Restarting to install the verified update in place…")


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

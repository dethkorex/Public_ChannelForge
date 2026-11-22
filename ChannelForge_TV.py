"""ChannelForge TV desktop viewer for the public creator directory."""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import urllib.request
from pathlib import Path

VERSION = "3.3.2"
DIRECTORY = "https://directory.channelforge.tv/v1/catalog"
GITHUB = "https://api.github.com/repos/dethkorex/Public_ChannelForge/releases/latest"


def resource(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name


HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>ChannelForge TV</title>
<style>*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#2b0752,#060410 43%);color:#fff;font-family:Segoe UI,sans-serif;overflow-x:hidden}header{height:92px;display:flex;align-items:center;padding:0 4vw;border-bottom:2px solid #13e4ff;gap:28px}h1{color:#fff22b;letter-spacing:4px;margin:0}.tag{color:#4fffd0;border:1px solid #19e9ff;padding:5px 10px}.update{margin-left:auto;background:#15112c;color:white;border:1px solid #19e9ff;padding:12px 18px;border-radius:7px}.wrap{padding:28px 4vw}.creators,.live,.vault{display:flex;gap:14px;overflow-x:auto;padding:10px 6px 25px}.creator,.channel{min-width:260px;background:#16132c;border:1px solid #41376a;border-radius:9px;padding:16px;color:white}.creator:focus,.channel:focus{outline:4px solid #fff22b}.vault{min-height:410px;align-items:center;perspective:1000px;gap:0;padding-left:34px}.tape{position:relative;flex:0 0 210px;height:340px;margin-left:-28px;background:#0d0a1c;border:3px solid #14dcf5;border-left-width:15px;padding:10px;box-shadow:16px 10px 18px #000c,5px 0 #44244f inset;transition:transform .22s,filter .22s;color:white;transform:rotateY(-22deg) translateZ(-35px);transform-style:preserve-3d;filter:brightness(.7)}.tape:nth-child(even){transform:rotateY(18deg) translateZ(-25px)}.tape:focus,.tape:hover{outline:none;border-color:#efff1c;transform:rotateY(0) translateY(-10px) translateZ(95px) scale(1.08);filter:brightness(1);z-index:5}.tape img{width:172px;height:255px;object-fit:cover;background:linear-gradient(145deg,#3a2858,#130d24)}.tape b{display:block;padding-top:8px}.pink{color:#ff3eb8}.cyan{color:#28eaff}video{display:none;position:fixed;inset:0;width:100%;height:100%;background:#000;z-index:10}video.playing{display:block}.close{display:none;position:fixed;z-index:11;right:25px;top:20px;padding:12px 18px;background:#111d;color:white;border:1px solid white}.close.show{display:block}.empty{color:#a89abb;padding:25px}</style></head><body>
<style>.categories{display:flex;gap:10px;margin:0 0 12px}.categories span{padding:7px 13px;border:1px solid #ff3eb8;border-radius:6px;color:#8ff}.vault-layout{display:grid;grid-template-columns:48% 52%;gap:24px;min-height:440px}.vault{display:flex;flex-direction:column;perspective:900px;gap:7px;height:440px;overflow-y:auto;padding:8px 18px 20px 8px}.tape,.tape:nth-child(even){display:grid;grid-template-columns:78px 1fr auto;grid-template-rows:1fr 1fr;align-items:center;flex:none;width:100%;height:72px;margin:0;padding:6px 12px 6px 5px;border:1px solid #855938;border-left:9px solid #14dcf5;background:linear-gradient(90deg,#392416,#17131a 70%);box-shadow:8px 5px 12px #0009;transform:rotateX(-5deg) translateZ(-15px);filter:brightness(.82);text-align:left}.tape:focus,.tape:hover{transform:rotateX(0) translateZ(65px) scale(1.025);border:3px solid #efff1c;border-left-width:9px;filter:none}.tape img{grid-row:1/3;width:66px;height:56px;object-fit:cover;object-position:20% center;margin-right:12px;border:1px solid #f3c55b}.tape b{padding:0;font-size:17px}.tape small{color:#c8b89f}.tape i{grid-column:3;grid-row:1/3;background:#66ff9e;color:#07130c;padding:7px 10px;font-style:normal;font-weight:800}.selection{height:440px;border:1px solid #12dff9;border-radius:12px;padding:18px;background:#0a0c25;display:grid;grid-template-columns:210px 1fr;gap:22px}.selection>img{width:210px;height:315px;object-fit:cover;border:2px solid #18dcf4;border-radius:7px}.selection h2{color:#fff22d;font-size:29px;margin-top:8px}.selection p{line-height:1.55;min-height:115px}.selection button{width:100%;padding:14px;background:#a94d06;color:#fff1a0;border:2px solid #ffa20c;border-radius:7px;font-weight:900}.selection small{display:block;color:#8d829f;margin-top:12px}@media(max-width:900px){.vault-layout{grid-template-columns:1fr}.selection{display:none}}</style><header><h1>CHANNELFORGE TV</h1><span class="tag">CREATOR VAULTS</span><button id="update" class="update">UPDATES • v__VERSION__</button></header><main class="wrap"><h2>CREATOR STUDIOS</h2><div id="creators" class="creators"><span class="empty">Connecting to the public directory…</span></div><h2 class="cyan">LIVE CHANNELS</h2><div id="live" class="live"></div><h2 class="pink">VHS VAULT <small>• FLICK THROUGH THE BOXES</small></h2><nav class="categories"><span>CONTINUE WATCHING</span><span>80S BLOCKBUSTERS</span><span>90S ESSENTIALS</span><span>CREATOR PICKS</span></nav><div class="vault-layout"><div id="vault" class="vault"></div><aside id="selection" class="selection"><span class="empty">Choose a tape to see its movie details.</span></aside></div></main><video id="player" controls autoplay></video><button id="close" class="close">BACK TO VAULT</button><script src="/hls.js"></script><script>
const $=s=>document.querySelector(s), esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let hls;
async function json(url){let r=await fetch('/proxy?url='+encodeURIComponent(url));if(!r.ok)throw Error(await r.text());return r.json()}
async function load(){try{let data=await json('__DIRECTORY__');$('#creators').innerHTML='';for(const c of data.creators){let b=document.createElement('button');b.className='creator';b.innerHTML=(c.logo_url?'<img src="'+esc(c.logo_url)+'" style="width:46px;height:46px;object-fit:contain;float:left;margin-right:12px">':'')+'<b>'+esc(c.name)+'</b><br><small>'+esc(c.description)+'</small>';b.onclick=()=>studio(c);$('#creators').append(b)}if(data.creators.length)studio(data.creators[0]);else $('#creators').innerHTML='<span class="empty">No creator studios are online right now.</span>'}catch(e){$('#creators').innerHTML='<span class="empty">Directory unavailable: '+esc(e.message)+'</span>'}}
async function studio(c){$('#live').innerHTML='<span class="empty">Loading live channels…</span>';$('#vault').innerHTML='<span class="empty">Loading tapes and posters…</span>';try{let m=await fetch('/proxy?url='+encodeURIComponent(c.playlist_url)).then(r=>r.text()),lines=m.split(/\r?\n/),name='';$('#live').innerHTML='';for(const line of lines){if(line.startsWith('#EXTINF:'))name=line.slice(line.lastIndexOf(',')+1);else if(line&&!line.startsWith('#')&&name){let b=document.createElement('button');b.className='channel';b.textContent='LIVE • '+name;b.onclick=()=>play(line);$('#live').append(b);name=''}}let v=await json(c.vault_url);$('#vault').innerHTML='';for(const x of v.items){let b=document.createElement('button');b.className='tape';b.innerHTML='<img loading="lazy" src="'+esc(x.poster_url)+'"><b>'+esc(x.title)+'</b><small>'+esc(x.year)+' • '+esc(x.type)+'</small><i>PLAY ▶</i>';b.onfocus=()=>selectTape(x);b.onmouseenter=()=>selectTape(x);b.onclick=()=>play(x.stream_url);$('#vault').append(b)}if(v.items.length){selectTape(v.items[0]);$('#vault').firstElementChild.focus()}else $('#vault').innerHTML='<span class="empty">This creator has not stocked the vault yet.</span>'}catch(e){$('#vault').innerHTML='<span class="empty">Could not load this studio: '+esc(e.message)+'</span>'}}
function selectTape(x){$('#selection').innerHTML='<img src="'+esc(x.poster_url)+'"><div><h2>'+esc(x.title)+'</h2><b class="pink">'+esc(x.year)+'</b> • '+esc(x.type)+'<p>'+esc(x.summary||'No description supplied.')+'</p><button id="selected-play">▶ PLAY TAPE</button><small>Poster and movie data supplied automatically by the creator library.</small></div>';$('#selected-play').onclick=()=>play(x.stream_url)}
function play(url){let v=$('#player');if(hls)hls.destroy();if(v.canPlayType('application/vnd.apple.mpegurl'))v.src=url;else if(Hls.isSupported()){hls=new Hls();hls.loadSource(url);hls.attachMedia(v)}v.classList.add('playing');$('#close').classList.add('show');v.play()}
$('#close').onclick=()=>{$('#player').pause();$('#player').classList.remove('playing');$('#close').classList.remove('show')};$('#update').onclick=async()=>{let r=await json('__GITHUB__'),latest=(r.tag_name||'').replace(/^v/,'');$('#update').textContent=latest==='__VERSION__'?'UP TO DATE ✓ • v__VERSION__':'UPDATE v'+latest+' AVAILABLE ✓';if(latest!=='__VERSION__')window.open(r.html_url)};
function demo(){let titles=['Neon Nights','The Last Arcade','Galaxy Rangers','Video Dreams','Summer Quest'],colors=['#bd205c','#a9560c','#069e9a','#792091','#d64a16'];$('#creators').innerHTML='<button class="creator"><b>RETRO WAVE STUDIO</b><br><small>Custom creator branding • online</small></button>';$('#live').innerHTML='<button class="channel">LIVE • SATURDAY MORNING</button><button class="channel">LIVE • SCI-FI AFTER DARK</button>';$('#vault').innerHTML='';titles.forEach((title,i)=>{let svg='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="'+colors[i]+'"/><stop offset="1" stop-color="#09142d"/></linearGradient></defs><rect width="400" height="600" fill="url(#g)"/><circle cx="90" cy="120" r="55" fill="#ffe43b" opacity=".8"/><text x="200" y="310" fill="white" font-size="38" font-family="sans-serif" font-weight="bold" text-anchor="middle">'+title+'</text></svg>');let x={title,year:1984+i,type:'movie',summary:'A creator-curated retro favorite with poster art and movie details loaded automatically from the connected library.',poster_url:svg,stream_url:''},b=document.createElement('button');b.className='tape';b.innerHTML='<img src="'+svg+'"><b>'+title+'</b><small>'+x.year+' • movie</small><i>PLAY ▶</i>';b.onfocus=()=>selectTape(x);b.onmouseenter=()=>selectTape(x);$('#vault').append(b);if(i===0)selectTape(x)});$('#vault').firstElementChild.focus()}location.search.includes('demo=1')?demo():load();</script></body></html>'''.replace("__VERSION__", VERSION).replace("__DIRECTORY__", DIRECTORY).replace("__GITHUB__", GITHUB)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import parse_qs, urlsplit
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/":
                return self.send(200, HTML.encode(), "text/html; charset=utf-8")
            if parsed.path == "/hls.js":
                return self.send(200, resource("web/hls.min.js").read_bytes(), "text/javascript")
            if parsed.path == "/proxy":
                url = parse_qs(parsed.query).get("url", [""])[0]
                if not url.startswith("https://"):
                    raise ValueError("Only public HTTPS endpoints are allowed.")
                request = urllib.request.Request(url, headers={"User-Agent": f"ChannelForge-TV/{VERSION}"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    return self.send(response.status, response.read(16 * 1024 * 1024), response.headers.get_content_type())
            self.send_error(404)
        except Exception as exc:
            self.send(502, json.dumps({"error": str(exc)}).encode(), "application/json")

    def send(self, status, body, content_type):
        self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_args):
        pass


def main():
    import webview
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="channel-forge-tv-server")
    thread.start()
    webview.create_window("ChannelForge TV", url=url, width=1500, height=900, min_size=(1100, 700), background_color="#080414")
    try:
        webview.start(debug=False)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

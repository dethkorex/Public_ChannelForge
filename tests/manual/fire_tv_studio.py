"""Isolated Fire TV QA studio. Run with Python and FFmpeg on PATH; see docs/alpha4-fire.md."""
import argparse
import json

import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def serve(port, flags, adaptive=False):
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as tmp:
        variants = {"economy": ("480x270", 160, 280000), "balanced": ("854x480", 470, 750000),
                    "hd": ("1280x720", 1200, 1800000)} if adaptive else {"default": ("640x360", None, 0)}
        for name, (size, kbps, peak) in variants.items():
            for index, frequency in enumerate((440, 880)):
                rate = ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k"] if kbps else []
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                                f"testsrc2=s={size}:r=24,drawbox=x=0:y=0:w=80:h=80:color={'red' if index else 'blue'}:t=fill",
                                "-f", "lavfi", "-i", f"sine=frequency={frequency}", "-t", "8",
                                "-c:v", "libx264", "-preset", "veryfast", "-g", "48", "-sc_threshold", "0", "-pix_fmt", "yuv420p",
                                *rate, "-c:a", "aac", "-b:a", "64k", "-ac", "2", "-ar", "48000", "-f", "hls", "-hls_time", "2",
                                "-hls_flags", "independent_segments", f"{name}-{index}.m3u8"], cwd=tmp, check=True)
        class Studio(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlsplit(self.path).path
                problems = set(flags.read_text().split()) if flags.exists() else set()
                now = int(time.time())
                epoch = now // 8 * 8
                route = "/new" if "rotate" in problems else "/old"
                if path in ("/v1/catalog", "/fallback"):
                    if "directory" in problems:
                        return self.send_error(503)
                    payload = {"creators": [{"id": "studio-4", "name": "Alpha QA Studio",
                        "playlist_url": base+route+"/playlist", "status_url": base+route+"/guide",
                        "vault_url": base+route+"/vault", "cinema_supported": True}]}
                elif not path.startswith(route+"/"):
                    return self.send_error(404)
                elif path.endswith("/guide"):
                    if "guide" in problems:
                        return self.send_error(503)
                    payload = {"channels": [{"name": "Alpha Motion", "number": "1", "id": "motion", "online": True,
                        "stream_url": base+route+"/master.m3u8", "continuous_url": base+route+"/master.m3u8",
                        "programs": [{"title":f"Demo {t//8%2+1}", "start_epoch_ms":t*1000,
                                      "stop_epoch_ms":(t+8)*1000} for t in range(epoch-8,epoch+96,8)]}]}
                elif path.endswith("/playlist"):
                    return self.send_error(503)
                elif path.endswith("/vault"):
                    payload={"items":[{"id":"film", "title":"Alpha Movie", "type":"movie","year":"2026","stream_url":base+route+"/movie.m3u8"}]}
                elif path.endswith(".m3u8"):
                    if "stream" in problems:
                        return self.send_error(503)
                    if adaptive and path.endswith(("/master.m3u8", "/movie.m3u8")):
                        leaf = path.rsplit("/", 1)[1]
                        lines = ["#EXTM3U", "#EXT-X-INDEPENDENT-SEGMENTS"]
                        for name in ("balanced", "economy", "hd"):
                            size, _, peak = variants[name]
                            lines.extend([f"#EXT-X-STREAM-INF:BANDWIDTH={peak},RESOLUTION={size}",
                                          f"{name}/index-{leaf}"])
                        return self.reply(("\n".join(lines)+"\n").encode(), "application/vnd.apple.mpegurl")
                    movie = path.endswith("movie.m3u8")
                    first = 0 if movie else now//2-16
                    lines=["#EXTM3U","#EXT-X-VERSION:3","#EXT-X-TARGETDURATION:2",f"#EXT-X-MEDIA-SEQUENCE:{first}",
                           "#EXT-X-INDEPENDENT-SEGMENTS",f"#EXT-X-DISCONTINUITY-SEQUENCE:{first//4}"]
                    for n in range(first,first+(32 if movie else 16)):
                        if n%4==0 and n!=first:lines.append("#EXT-X-DISCONTINUITY")
                        lines.extend(["#EXTINF:2.0,",f"{n}.ts"])
                    if movie:lines.append("#EXT-X-ENDLIST")
                    return self.reply(("\n".join(lines)+"\n").encode(),"application/vnd.apple.mpegurl")
                elif path.endswith(".ts"):
                    if "stream" in problems:return self.send_error(503)
                    n=int(path.split("/")[-1][:-3])
                    name = path.rsplit("/", 2)[-2] if adaptive else "default"
                    if name not in variants: return self.send_error(404)
                    data = (Path(tmp)/f"{name}-{n//4%2}{n%4}.ts").read_bytes()
                    return self.reply(data, "video/mp2t", slow="slow" in problems)
                else:return self.send_error(404)
                self.reply(json.dumps(payload).encode(),"application/json")

            def reply(self, data, mime, slow=False):
                self.send_response(200)
                self.send_header("Content-Type",mime); self.send_header("Content-Length",str(len(data)))
                self.end_headers()
                try:
                    if slow:
                        for start in range(0, len(data), 6000):
                            self.wfile.write(data[start:start + 6000])
                            time.sleep(.1)
                    else:
                        self.wfile.write(data)
                except (ConnectionResetError,BrokenPipeError):pass
        ThreadingHTTPServer(("127.0.0.1",port),Studio).serve_forever()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=19753)
    parser.add_argument("--flags",type=Path,required=True)
    parser.add_argument("--adaptive",action="store_true")
    args=parser.parse_args()
    serve(args.port,args.flags,args.adaptive)

"""Generate an opt-in, multi-Studio NGINX relay for an existing host."""

from __future__ import annotations

import argparse
from pathlib import Path
import re

HOSTNAME = r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}"
STUDIO_PATH = r"/studio/[a-f0-9]{16}"


def configuration(routes: dict[str, int], port: int, certificate: Path | None = None, key: Path | None = None,
                  https_origins: dict[str, str] | None = None, enrollment_port: int | None = None) -> str:
    if not routes or len(set(routes.values())) != len(routes) or not 1 <= port <= 65535:
        raise ValueError("Assign each Studio its own loopback port.")
    by_host: dict[str, list[tuple[str, int, int]]] = {}
    for index, (route, upstream) in enumerate(routes.items()):
        host, slash, slot = route.partition("/")
        prefix = slash + slot
        if (not re.fullmatch(HOSTNAME, host) or (prefix and not re.fullmatch(STUDIO_PATH, prefix))
                or type(upstream) is not int or not 1024 <= upstream <= 65535 or upstream == port):
            raise ValueError("Use a DNS hostname or Studio path and a distinct loopback origin port.")
        by_host.setdefault(host, []).append((prefix, upstream, index))
    if bool(certificate) != bool(key):
        raise ValueError("Provide both TLS file paths or terminate HTTPS in front of this relay.")
    if enrollment_port is not None and (
        len(by_host) != 1 or not 1024 <= enrollment_port <= 65535 or enrollment_port in (port, *routes.values())
    ):
        raise ValueError("Serve enrollment on one dedicated relay hostname and a distinct loopback port.")
    https_origins = https_origins or {}
    if any("/" in host or host not in routes or origin in by_host or not re.fullmatch(HOSTNAME, origin)
           for host, origin in https_origins.items()):
        raise ValueError("Assign each HTTPS origin to a configured relay hostname and use a distinct origin DNS name.")

    tls = ""
    listeners = (f"127.0.0.1:{port}",)
    if certificate and key:
        def path_value(path):
            value = str(path.resolve())
            if not re.fullmatch(r"/[a-zA-Z0-9_./-]+", value):
                raise ValueError("TLS paths must be absolute Unix paths without spaces or special characters.")
            return value

        tls = (f"ssl_certificate {path_value(certificate)};\n"
               f"    ssl_certificate_key {path_value(key)};\n"
               "    ssl_protocols TLSv1.2 TLSv1.3;")
        listeners = (f"{port} ssl", f"[::]:{port} ssl")

    default_listeners = "\n        ".join(f"listen {address} default_server;" for address in listeners)
    studio_listeners = "\n        ".join(f"listen {address};" for address in listeners)

    base = rf"""
worker_processes 1;
pid logs/nginx.pid;
error_log logs/error.log warn;
events {{ worker_connections 512; }}

http {{
    access_log off;
    server_tokens off;
    client_body_temp_path client_body;
    proxy_temp_path proxy_temp;
    fastcgi_temp_path fastcgi_temp;
    uwsgi_temp_path uwsgi_temp;
    scgi_temp_path scgi_temp;
    {tls}
    proxy_cache_path segments keys_zone=media:8m max_size=512m inactive=10m use_temp_path=off;
    map $uri $relay_slot {{
        default "";
        "~^({STUDIO_PATH})/" $1;
    }}
    limit_conn_zone "$server_name$relay_slot" zone=studio_connections:1m;
    map $upstream_http_content_type $wrong_segment_type {{
        default 1;
        ~^video/mp2t 0;
    }}
    server {{
        {default_listeners}
        server_name _;
        return 404;
    }}
"""
    upstreams, servers = [], []
    enrollment_location = ""
    if enrollment_port is not None:
        enrollment_location = f"""
        location = /internal/enroll {{
            if ($request_method != POST) {{ return 405; }}
            client_max_body_size 8k;
            proxy_pass http://127.0.0.1:{enrollment_port}/enroll;
            proxy_set_header Host localhost;
            proxy_set_header Authorization $http_authorization;
            proxy_set_header Connection "";
        }}
"""
    for host, studios in by_host.items():
        locations = []
        for prefix, port_number, index in studios:
            origin = None if prefix else https_origins.get(host)
            upstream_address = f"{origin}:443" if origin else f"127.0.0.1:{port_number}"
            upstreams.append(f"    upstream studio_{index} {{ server {upstream_address}; keepalive 16; }}\n")
            locations.append(studio_locations(index, prefix, origin))
        servers.append(rf"""
    server {{
        {studio_listeners}
        server_name {host};
        limit_conn studio_connections 96;
        limit_conn_status 503;
        proxy_http_version 1.1;
        proxy_pass_request_headers off;
        proxy_set_header Connection "";
        proxy_redirect off;
        proxy_connect_timeout 2s;
        proxy_read_timeout 18s;
        {enrollment_location}
        {''.join(locations)}
        location / {{
            if ($request_method !~ ^(GET|HEAD)$) {{ return 405; }}
            return 404;
        }}
    }}
""")
    return base + "".join(upstreams + servers) + "}\n"


def studio_locations(index: int, prefix: str, origin: str | None) -> str:
    upstream = f"{'https' if origin else 'http'}://studio_{index}"
    upstream_tls = (f"proxy_ssl_name {origin};\n"
                    "            proxy_ssl_server_name on;\n"
                    "            proxy_ssl_verify on;\n"
                    "            proxy_ssl_verify_depth 3;\n"
                    "            proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;" if origin else "")
    rewrite_origin = ("sub_filter_types application/json application/xml text/xml "
                      "application/vnd.apple.mpegurl application/x-mpegurl audio/x-mpegurl;\n"
                      "            sub_filter_once off;\n"
                      f"            sub_filter 'https://{origin}/' 'https://$server_name/';" if origin else "")
    return rf"""
        location ~ "^{prefix}/ffmpeg-stream/[a-f0-9]{{20}}/segment((economy|balanced|hd)_)?[0-9]+\.ts$" {{
            if ($request_method !~ ^(GET|HEAD)$) {{ return 405; }}
            proxy_set_header Host {origin or '$server_name'};
            proxy_set_header Connection "";
            {upstream_tls}
            proxy_pass {upstream};
            proxy_cache media;
            proxy_cache_key "$server_name|$uri";
            proxy_cache_lock on;
            proxy_cache_lock_timeout 19s;
            proxy_cache_lock_age 19s;
            proxy_cache_valid 200 1m;
            proxy_no_cache $wrong_segment_type;
            proxy_ignore_headers Cache-Control Expires;
            add_header X-ChannelForge-Cache $upstream_cache_status always;
        }}

        location ~ "^{prefix}/(api/public/(channels|vault|origin|program-art|movie-art)|api/(plex|local)/image|exports/(guide\.xml|channels\.m3u)|user-assets/[^/]+|(?:live|vault)/[A-Za-z0-9_-]+\.m3u8|broadcast/[A-Za-z0-9_-]+/(?:master|firetv|[a-f0-9]{{24}}/(?:economy|balanced|hd))\.m3u8|ffmpeg-stream/[A-Za-z0-9_-]+/(?:index(?:_(?:economy|balanced|hd))?\.m3u8|segment(?:(?:economy|balanced|hd)_)?[0-9]+\.ts)|hls-proxy/[A-Za-z0-9_-]+)$" {{
            if ($request_method !~ ^(GET|HEAD)$) {{ return 405; }}
            proxy_set_header Host {origin or '$server_name'};
            proxy_set_header Connection "";
            {upstream_tls}
            proxy_pass {upstream};
            {rewrite_origin}
        }}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--studio", required=True, action="append", metavar="HOSTNAME[/studio/SLOT]:PORT")
    parser.add_argument("--https-origin", action="append", default=[], metavar="HOSTNAME=ORIGIN_HOSTNAME")
    parser.add_argument("--listen-port", type=int, default=8080)
    parser.add_argument("--enrollment-port", type=int)
    parser.add_argument("--certificate", type=Path)
    parser.add_argument("--key", type=Path)
    args = parser.parse_args()
    routes = {}
    https_origins = {}
    try:
        for entry in args.studio:
            host, upstream = entry.rsplit(":", 1)
            if host in routes:
                raise ValueError("Specify each Studio hostname once.")
            routes[host] = int(upstream)
        for entry in args.https_origin:
            host, origin = entry.split("=", 1)
            if host in https_origins:
                raise ValueError("Specify each Studio HTTPS origin once.")
            https_origins[host] = origin
        text = configuration(routes, args.listen_port, args.certificate, args.key, https_origins, args.enrollment_port)
    except ValueError as error:
        parser.error(str(error))
    args.output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()

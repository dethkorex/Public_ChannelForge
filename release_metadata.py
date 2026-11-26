"""Read release data even when the GitHub API is unavailable."""
import http.client
import json
import re
import urllib.error
import urllib.request


REPOSITORY = "dethkorex/Public_ChannelForge"
RELEASES = f"https://github.com/{REPOSITORY}/releases"
SOURCES = (
    f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
    f"{RELEASES}/latest/download/ChannelForge-release.json",
)
RELEASE_VERSION = r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}(?:-(?:alpha|beta|rc)\.[0-9]{1,9})?"
PREVIEW_SOURCE = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=10"


def version_tuple(value: str) -> tuple:
    """Compare numeric releases, including files cached by older updaters."""
    match = re.fullmatch(r"v?([0-9]{1,9}(?:\.[0-9]{1,9}){1,3})(?:-(alpha|beta|rc)\.([0-9]{1,9}))?", value.strip())
    if not match:
        return ()
    parts = [int(part) for part in match[1].split(".")]
    parts.extend([0] * (4 - len(parts)))
    stage = {"alpha": 0, "beta": 1, "rc": 2, None: 3}[match[2]]
    return (*parts, stage, int(match[3] or 0))


def read_latest_release(user_agent: str) -> dict:
    preview = "-" in user_agent.rsplit("/", 1)[-1]
    for url in ((PREVIEW_SOURCE,) + SOURCES if preview else SOURCES):
        try:
            headers = {"Accept": "application/json", "User-Agent": user_agent}
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=6) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                continue
            payload = json.loads(raw)
            releases = payload if isinstance(payload, list) and url == PREVIEW_SOURCE else [payload]
            candidates = []
            for release in releases:
                if not isinstance(release, dict) or release.get("draft") or not isinstance(release.get("assets"), list) or not release["assets"]:
                    continue
                tag = str(release.get("tag_name", ""))
                if not re.fullmatch("v" + RELEASE_VERSION, tag):
                    continue
                if not preview and (release.get("prerelease") or "-" in tag):
                    continue
                candidates.append(release)
            if candidates:
                return max(candidates, key=lambda item: version_tuple(item["tag_name"]))
        except urllib.error.HTTPError as error:
            error.close()
        except (OSError, ValueError, http.client.HTTPException):
            continue
    raise ValueError("The update check is unavailable. Try again from Updates.")

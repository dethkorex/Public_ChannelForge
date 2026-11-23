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


def read_latest_release(user_agent: str) -> dict:
    for url in SOURCES:
        try:
            headers = {"Accept": "application/json", "User-Agent": user_agent}
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=6) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                continue
            release = json.loads(raw)
            if (isinstance(release, dict) and re.fullmatch(r"v\d+\.\d+\.\d+", str(release.get("tag_name")))
                    and isinstance(release.get("assets"), list) and release["assets"]
                    and not release.get("draft") and not release.get("prerelease")):
                return release
        except urllib.error.HTTPError as error:
            error.close()
        except (OSError, ValueError, http.client.HTTPException):
            continue
    raise ValueError("The update check is unavailable. Try again from Updates.")

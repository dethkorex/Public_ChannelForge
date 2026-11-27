"""Post the release notes to Discord after the verified public build is published."""

import argparse
import json
import os
from pathlib import Path
import re
import textwrap
import time
import urllib.error
import urllib.request


def messages(tag, repository, notes):
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Only stable release tags are announced.")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Expected a GitHub repository name.")
    first = notes.splitlines()[0] if notes else ""
    if re.fullmatch(r"# ChannelForge Alpha \d+\.\d+(?:\.\d+)?", first):
        title = f"**{first[2:]} is available**"
    else:
        title = f"**ChannelForge {tag} is available**"
    link = f"Downloads and full notes · <https://github.com/{repository}/releases/tag/{tag}>"
    # Leave room for the heading and link in each message, including UTF-16 pairs.
    limit = 1900 - len(title) - len(link)
    parts = []
    lines = [line for line in notes.splitlines() if line.strip() and not line.startswith("# ")]
    if not lines:
        raise ValueError("Write the public release notes before announcing the release.")
    current = ""
    for line in lines:
        if line.startswith("##"):
            line = f"**{line.lstrip('#').strip()}**"
        paragraphs = [line] if len(line.encode("utf-16-le")) // 2 <= limit else textwrap.wrap(line, width=limit // 2, replace_whitespace=False)
        for paragraph in paragraphs:
            next_part = f"{current}\n{paragraph}" if current else paragraph
            if len(next_part.encode("utf-16-le")) // 2 > limit:
                parts.append(current)
                current = paragraph
            else:
                current = next_part
    parts.append(current)
    return [f"{title}\n\n{part}" for part in parts[:-1]] + [f"{title}\n\n{parts[-1]}\n\n{link}"]


def post_message(url, message):
    body = json.dumps({"username": "ChannelForge Updates", "content": message,
                       "allowed_mentions": {"parse": []}}).encode()
    request = urllib.request.Request(url + "?wait=true", data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": "ChannelForge-Release/4"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                if not json.load(response).get("id"):
                    raise RuntimeError("Discord did not confirm the announcement.")
                return
        except urllib.error.HTTPError as error:
            with error:
                if error.code == 429 and attempt < 2:
                    retry_after = max(1, float(json.load(error).get("retry_after", 1)))
                    if retry_after <= 60:
                        time.sleep(retry_after)
                        continue
                raise RuntimeError(f"Discord returned HTTP {error.code}.") from None
        except urllib.error.URLError:
            raise RuntimeError("Could not reach the Discord webhook.") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the messages without making network requests.")
    args = parser.parse_args()
    notes = Path("RELEASE_NOTES.md").read_text(encoding="utf-8-sig")
    tag, repository = os.environ["GITHUB_REF_NAME"], os.environ["GITHUB_REPOSITORY"]
    announcements = messages(tag, repository, notes)
    summary = Path("docs/releases") / f"{tag}-announcement.md"
    if summary.is_file():
        message = summary.read_text(encoding="utf-8").strip()
        message += f"\n\n<https://github.com/{repository}/releases/tag/{tag}>"
        if len(message.encode("utf-16-le")) // 2 > 2000:
            raise ValueError("Keep the Discord announcement under 2,000 characters.")
        announcements.insert(0, message)
    if args.dry_run:
        print("\n\n".join(announcements))
        return
    url = os.environ.get("RELEASE_WEBHOOK", "")
    if not re.fullmatch(r"https://discord\.com/api/(?:v\d+/)?webhooks/\d+/[\w-]+", url):
        raise ValueError("Configure the Discord channel URL in CHANNELFORGE_DISCORD_WEBHOOK.")
    for message in announcements:
        post_message(url, message)


if __name__ == "__main__":
    main()

"""Measure presented frames for one loopback-only QA APK on Fire TV (adb over USB or LAN).

Install the baseline or candidate QA build before running. The Studio fixture must
be reachable on the device through adb reverse tcp:19753 tcp:19753.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET


PACKAGE = "tv.channelforge.viewer.alpha4qa"
ACTIVITY = PACKAGE + "/tv.channelforge.viewer.MainActivity"


def trial(adb, serial, flags, rotate):
    def shell(command):
        return subprocess.check_output([adb, "-s", serial, "shell", command], text=True,
                                       encoding="utf8", errors="replace", timeout=25)

    def locate(label):
        xml = shell("uiautomator dump /data/local/tmp/alpha4-fire-ui.xml >/dev/null && "
                    "cat /data/local/tmp/alpha4-fire-ui.xml")
        for node in ET.fromstring(xml).iter("node"):
            if label in node.get("content-desc", "") or label in node.get("text", ""):
                x, y, right, bottom = map(int, re.findall(r"\d+", node.get("bounds")))
                return f"input tap {(x + right) // 2} {(y + bottom) // 2}"
        raise RuntimeError("Missing control " + label)

    flags.write_text("")
    shell("am force-stop " + PACKAGE + " && am start -n " + ACTIVITY)
    time.sleep(1)
    shell(locate("Live TV Guide"))
    tune = locate("Press OK to watch.")
    start = time.monotonic()
    shell(tune)
    pid = shell("pidof " + PACKAGE).strip()

    def cpu_ticks():
        try:
            fields = shell(f"run-as {PACKAGE} cat /proc/{pid}/stat").split(") ")[1].split()
            return int(fields[11]) + int(fields[12])
        except (IndexError, ValueError, subprocess.SubprocessError):
            return None

    cpu_start = cpu_ticks()
    pss = []
    frames = set()
    first_seen = None
    surface = None
    rotated = False
    duration = 60 if rotate else 29
    try:
        while time.monotonic() - start < duration:
            if surface is None:
                surface = next((line for line in shell("dumpsys SurfaceFlinger --list").splitlines()
                                if line.startswith("SurfaceView") and PACKAGE in line), None)
            if surface:
                rows = shell(f"dumpsys SurfaceFlinger --latency '{surface}'").splitlines()
                timestamps = {int(row.split()[1]) for row in rows if len(row.split()) == 3
                              and 0 < int(row.split()[1]) < 2**62}
                if timestamps and first_seen is None:
                    first_seen = time.monotonic() - start
                frames.update(timestamps)
            elapsed = time.monotonic() - start
            if rotate and elapsed >= 7 and not rotated:
                flags.write_text("rotate")
                rotated = True
            if elapsed > 10 + 8 * len(pss):
                memory = shell("dumpsys meminfo " + PACKAGE)
                match = re.search(r"TOTAL:\s+(\d+)\s+TOTAL SWAP", memory)
                if match:
                    pss.append(int(match.group(1)))
            time.sleep(.22)
        elapsed = time.monotonic() - start
        cpu_end = cpu_ticks()
        device_time = float(shell("cat /proc/uptime").split()[0])
        points = sorted(frames)
        gaps = [(b - a) / 1e9 for a, b in zip(points, points[1:])]
        if points:
            gaps.append(max(0, device_time - points[-1] / 1e9))
        return dict(rotate=rotate, duration=elapsed, first_seen_seconds=first_seen, frames=len(points),
                    gaps_over_250ms=sum(gap > .25 for gap in gaps), worst_gap_seconds=max(gaps, default=0),
                    stalled_seconds=sum(gap - 1 / 24 for gap in gaps if gap > .25), pss_kib=pss,
                    process_cpu_ticks=None if None in (cpu_start, cpu_end) else cpu_end - cpu_start)
    finally:
        shell("input keyevent 4")
        flags.write_text("")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--flags", type=Path, required=True)
    parser.add_argument("--rotate", action="store_true")
    parser.add_argument("--trials", type=int, default=1)
    args = parser.parse_args()
    for _ in range(args.trials):
        print(json.dumps(trial(args.adb, args.serial, args.flags, args.rotate)), flush=True)

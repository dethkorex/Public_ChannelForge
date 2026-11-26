"""Build an isolated, loopback-only Fire TV test APK, with no release credentials."""
import argparse
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def build(gradle, output, ref=None):
    with tempfile.TemporaryDirectory(prefix="channelforge-fire-") as temporary:
        work = Path(temporary)
        prefix = Path("tv/firetv")
        if ref:
            archive = subprocess.check_output(["git", "archive", "--format=zip", ref, str(prefix)], cwd=ROOT)
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                bundle.extractall(work)
        else:
            shutil.copytree(ROOT / prefix, work / prefix, ignore=shutil.ignore_patterns(
                ".gradle", "build", "local.properties", "*.jks", "*.keystore"))
        project = work / prefix
        gradle_file = project / "app/build.gradle"
        text = gradle_file.read_text(encoding="utf8")
        substitutions = [
            ("applicationId 'tv.channelforge.viewer'", "applicationId 'tv.channelforge.viewer.alpha4qa'"),
            ("""'"' + directoryUrl + '"'""", """'"http://127.0.0.1:19753/v1/catalog"'"""),
            ("https://raw.githubusercontent.com/dethkorex/Public_ChannelForge/main/directory/catalog.json",
             "http://127.0.0.1:19753/fallback"),
        ]
        for old, new in substitutions:
            if text.count(old) != 1:
                raise ValueError("Cannot identify expected QA substitution: " + old)
            text = text.replace(old, new)
        gradle_file.write_text(text, encoding="utf8")
        manifest = project / "app/src/main/AndroidManifest.xml"
        manifest.write_text(manifest.read_text().replace('usesCleartextTraffic="false"', 'usesCleartextTraffic="true"'))
        subprocess.run([gradle, "-p", str(project), ":app:assembleDebug"], check=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / "app/build/outputs/apk/debug/app-debug.apk", output)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--gradle", default="gradle")
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--ref", help="e.g. v3.6.2, to build a baseline from Git")
    args = cli.parse_args()
    build(args.gradle, args.output, args.ref)

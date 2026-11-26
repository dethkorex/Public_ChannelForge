"""A private Creator module whose data paths always resolve inside a temporary studio."""
import importlib.util
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


_studio = tempfile.TemporaryDirectory(prefix="channelforge-test-studio-")
_spec = importlib.util.spec_from_file_location(
    "_channelforge_test_creator", Path(__file__).resolve().parents[1] / "Public_ChannelForge.py"
)
app = importlib.util.module_from_spec(_spec)
with patch.dict(os.environ, {"PUBLIC_CHANNELFORGE_HOME": _studio.name}):
    _spec.loader.exec_module(app)
if app.DATA_DIR != Path(_studio.name).resolve():
    raise RuntimeError("Refusing to run Creator tests outside the temporary studio.")

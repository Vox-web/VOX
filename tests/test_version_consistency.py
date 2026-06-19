"""
Block F — версии backend / frontend / service worker не должны расходиться.
"""

import re
from pathlib import Path

import version as vox_version

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"


def _read(p):
    return (FRONTEND / p).read_text(encoding="utf-8")


def test_backend_version_defined():
    assert re.match(r"^\d+\.\d+\.\d+$", vox_version.__version__)


def test_sw_version_matches_backend():
    m = re.search(r"SW_VERSION\s*=\s*'([\d.]+)'", _read("sw.js"))
    assert m, "SW_VERSION not found"
    assert m.group(1) == vox_version.__version__


def test_frontend_version_matches_backend():
    m = re.search(r"VOX_FRONTEND_VERSION\s*=\s*'([\d.]+)'", _read("pwa-install.js"))
    assert m, "VOX_FRONTEND_VERSION not found"
    assert m.group(1) == vox_version.__version__

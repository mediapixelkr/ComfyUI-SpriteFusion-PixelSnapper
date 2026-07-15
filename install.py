import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RELEASE_API = (
    "https://api.github.com/repos/mediapixelkr/"
    "ComfyUI-SpriteFusion-PixelSnapper/releases/latest"
)


def asset_name() -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    assets = {
        ("windows", "amd64"): "spritefusion-pixel-snapper-windows-x86_64.exe",
        ("windows", "x86_64"): "spritefusion-pixel-snapper-windows-x86_64.exe",
        ("linux", "x86_64"): "spritefusion-pixel-snapper-linux-x86_64",
        ("darwin", "x86_64"): "spritefusion-pixel-snapper-macos-x86_64",
        ("darwin", "arm64"): "spritefusion-pixel-snapper-macos-aarch64",
        ("darwin", "aarch64"): "spritefusion-pixel-snapper-macos-aarch64",
    }
    return assets.get((system, machine))


def download_release() -> Path:
    name = asset_name()
    if name is None:
        raise RuntimeError("No prebuilt binary is available for this platform")

    request = urllib.request.Request(
        RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ComfyUI"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    asset = next((item for item in release.get("assets", []) if item["name"] == name), None)
    if asset is None:
        raise RuntimeError(f"Release asset not found: {name}")

    executable = "spritefusion-pixel-snapper.exe" if os.name == "nt" else "spritefusion-pixel-snapper"
    destination = ROOT / "target" / "release" / executable
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".download")
    try:
        download = urllib.request.Request(
            asset["browser_download_url"], headers={"User-Agent": "ComfyUI"}
        )
        with urllib.request.urlopen(download, timeout=120) as response:
            temporary.write_bytes(response.read())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    if os.name != "nt":
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return destination


def build_from_source() -> Path:
    cargo = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")
    if not Path(cargo).is_file():
        raise RuntimeError(
            "Rust/Cargo is required because no compatible prebuilt release could be downloaded. "
            "Install Rust from https://rustup.rs/ and retry."
        )
    subprocess.run([cargo, "build", "--release"], cwd=ROOT, check=True)
    executable = "spritefusion-pixel-snapper.exe" if os.name == "nt" else "spritefusion-pixel-snapper"
    return ROOT / "target" / "release" / executable


try:
    binary = download_release()
    print(f"Downloaded SpriteFusion Pixel Snapper: {binary}", file=sys.stderr)
except Exception as download_error:
    print(f"Prebuilt binary unavailable ({download_error}); building from source.", file=sys.stderr)
    binary = build_from_source()
    print(f"Built SpriteFusion Pixel Snapper: {binary}", file=sys.stderr)

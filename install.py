import shutil
import subprocess
import sys
from pathlib import Path


if shutil.which("cargo") is None:
    raise SystemExit(
        "Rust/Cargo is required to build Pixel Snapper: https://rustup.rs/"
    )

subprocess.run(
    ["cargo", "build", "--release"],
    cwd=Path(__file__).resolve().parent,
    check=True,
)
print("SpriteFusion Pixel Snapper built successfully.", file=sys.stderr)

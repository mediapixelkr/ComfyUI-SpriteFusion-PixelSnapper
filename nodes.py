import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import torch
from PIL import Image


ROOT = Path(__file__).resolve().parent


def _binary_path() -> Path:
    override = os.environ.get("SPRITEFUSION_PIXEL_SNAPPER_BIN")
    names = ("spritefusion-pixel-snapper.exe", "spritefusion-pixel-snapper")
    candidates = ([Path(override)] if override else []) + [
        ROOT / "target" / profile / name
        for profile in ("release", "debug")
        for name in names
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "SpriteFusion Pixel Snapper binary not found. Run `cargo build --release` "
        "in the custom node folder, or set SPRITEFUSION_PIXEL_SNAPPER_BIN."
    )


def _to_pil(tensor: torch.Tensor) -> Image.Image:
    array = tensor.detach().cpu().clamp(0, 1).mul(255).round().byte().numpy()
    return Image.fromarray(array, "RGB")


def _to_tensor(image: Image.Image) -> torch.Tensor:
    array = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(array)


class SpriteFusionPixelSnapper:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "colors": ("INT", {"default": 16, "min": 1, "max": 256}),
                "pixel_size": (
                    "FLOAT",
                    {"default": 0.0, "min": 0.0, "max": 1024.0, "step": 0.1},
                ),
                "output_scale": (
                    "INT",
                    {"default": 1, "min": 1, "max": 64},
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "INT", "INT")
    RETURN_NAMES = ("image", "grid_width", "grid_height")
    FUNCTION = "snap"
    CATEGORY = "image/pixel art"

    def snap(self, image, colors, pixel_size, output_scale):
        binary = _binary_path()
        outputs = []
        dimensions = []

        with tempfile.TemporaryDirectory(prefix="comfyui-pixel-snapper-") as temp:
            temp = Path(temp)
            for index, frame in enumerate(image):
                input_path = temp / f"input-{index}.png"
                output_path = temp / f"output-{index}.png"
                _to_pil(frame).save(input_path)

                command = [str(binary), str(input_path), str(output_path), str(colors)]
                if pixel_size > 0:
                    command.extend(("--pixel-size", str(pixel_size)))

                result = subprocess.run(
                    command, capture_output=True, text=True, encoding="utf-8", errors="replace"
                )
                if result.returncode != 0 or not output_path.is_file():
                    detail = (result.stderr or result.stdout).strip()
                    raise RuntimeError(f"Pixel Snapper failed on batch item {index}: {detail}")

                with Image.open(output_path) as snapped:
                    snapped = snapped.convert("RGB")
                    dimensions.append(snapped.size)
                    if output_scale != 1:
                        snapped = snapped.resize(
                            (snapped.width * output_scale, snapped.height * output_scale),
                            Image.Resampling.NEAREST,
                        )
                    outputs.append(_to_tensor(snapped.copy()))

        shapes = {tuple(item.shape) for item in outputs}
        if len(shapes) != 1:
            raise RuntimeError(
                "Auto-detection produced different grid sizes within the batch. "
                "Set pixel_size explicitly or process the images individually."
            )
        grid_width, grid_height = dimensions[0]
        return (torch.stack(outputs), grid_width, grid_height)


NODE_CLASS_MAPPINGS = {"SpriteFusionPixelSnapper": SpriteFusionPixelSnapper}
NODE_DISPLAY_NAME_MAPPINGS = {
    "SpriteFusionPixelSnapper": "SpriteFusion Pixel Snapper"
}

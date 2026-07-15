import os
import math
import subprocess
import tempfile
from collections import Counter
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


def _parse_hex_color(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    if len(value) != 6:
        raise ValueError("key_color must use #RRGGBB or #RGB format")
    try:
        return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError as error:
        raise ValueError("key_color contains invalid hexadecimal digits") from error


def _apply_chroma_key(
    image: Image.Image, key_color: tuple[int, int, int], tolerance: int
) -> Image.Image:
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    key = np.asarray(key_color, dtype=np.int16)
    transparent = np.max(np.abs(rgb - key), axis=2) <= tolerance
    alpha = np.where(transparent, 0, 255).astype(np.uint8)
    rgba = np.dstack((rgb.astype(np.uint8), alpha))
    return Image.fromarray(rgba, "RGBA")


def _detect_background_color(image: Image.Image) -> tuple[int, int, int]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    height, width = rgb.shape[:2]
    patch_size = max(1, min(8, min(width, height) // 16))
    patches = (
        rgb[:patch_size, :patch_size],
        rgb[:patch_size, width - patch_size :],
        rgb[height - patch_size :, :patch_size],
        rgb[height - patch_size :, width - patch_size :],
    )
    counts = [Counter(map(tuple, patch.reshape(-1, 3))) for patch in patches]
    candidates = set().union(*(counter.keys() for counter in counts))
    return max(
        candidates,
        key=lambda color: (
            sum(color in counter for counter in counts),
            sum(counter[color] for counter in counts),
            color,
        ),
    )


def _format_color(color: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*color)


def _to_mask(image: Image.Image) -> torch.Tensor:
    if image.mode == "RGBA":
        alpha = np.asarray(image.getchannel("A"), dtype=np.float32) / 255.0
        return torch.from_numpy(1.0 - alpha)
    return torch.zeros((image.height, image.width), dtype=torch.float32)


def _crop_to_aspect(image: Image.Image, target_ratio: float) -> Image.Image:
    width, height = image.size
    if width / height > target_ratio:
        new_width = max(1, min(width, round(height * target_ratio)))
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    new_height = max(1, min(height, round(width / target_ratio)))
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


def _pad_to_aspect(image: Image.Image, target_ratio: float) -> Image.Image:
    width, height = image.size
    if width / height > target_ratio:
        new_width = width
        new_height = max(height, math.ceil(width / target_ratio))
    else:
        new_width = max(width, math.ceil(height * target_ratio))
        new_height = height

    padded = Image.new(image.mode, (new_width, new_height), image.getpixel((0, 0)))
    padded.paste(image, ((new_width - width) // 2, (new_height - height) // 2))
    return padded


def _apply_output_mode(
    image: Image.Image,
    mode: str,
    input_size: tuple[int, int],
    output_scale: int,
    exact_width: int,
    exact_height: int,
) -> tuple[Image.Image, tuple[int, int]]:
    input_width, input_height = input_size
    target_ratio = input_width / input_height

    if mode == "crop_to_input_aspect":
        image = _crop_to_aspect(image, target_ratio)
    elif mode == "pad_to_input_aspect":
        image = _pad_to_aspect(image, target_ratio)

    grid_size = image.size
    if mode == "exact_size":
        image = image.resize((exact_width, exact_height), Image.Resampling.NEAREST)
    elif output_scale != 1:
        image = image.resize(
            (image.width * output_scale, image.height * output_scale),
            Image.Resampling.NEAREST,
        )
    return image, grid_size


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
                "output_mode": (
                    [
                        "crop_to_input_aspect",
                        "detected",
                        "pad_to_input_aspect",
                        "exact_size",
                    ],
                    {"default": "crop_to_input_aspect"},
                ),
                "output_scale": (
                    "INT",
                    {"default": 1, "min": 1, "max": 64},
                ),
                "exact_width": (
                    "INT",
                    {"default": 256, "min": 1, "max": 16384},
                ),
                "exact_height": (
                    "INT",
                    {"default": 256, "min": 1, "max": 16384},
                ),
                "transparency": (["chroma_key", "none"],),
                "key_color": (
                    "STRING",
                    {"default": "auto"},
                ),
                "key_tolerance": (
                    "INT",
                    {"default": 32, "min": 0, "max": 255},
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "INT", "INT", "MASK", "STRING")
    RETURN_NAMES = (
        "image",
        "grid_width",
        "grid_height",
        "mask",
        "key_color_used",
    )
    FUNCTION = "snap"
    CATEGORY = "image/pixel art"

    def snap(
        self,
        image,
        colors,
        pixel_size,
        transparency,
        key_color,
        key_tolerance,
        output_mode,
        output_scale,
        exact_width,
        exact_height,
    ):
        binary = _binary_path()
        outputs = []
        masks = []
        dimensions = []
        key_colors = []

        with tempfile.TemporaryDirectory(prefix="comfyui-pixel-snapper-") as temp:
            temp = Path(temp)
            for index, frame in enumerate(image):
                input_path = temp / f"input-{index}.png"
                output_path = temp / f"output-{index}.png"
                input_image = _to_pil(frame)
                input_image.save(input_path)

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
                    if transparency == "chroma_key":
                        resolved_key = (
                            _detect_background_color(snapped)
                            if key_color.strip().lower() == "auto"
                            else _parse_hex_color(key_color)
                        )
                        snapped = _apply_chroma_key(
                            snapped, resolved_key, key_tolerance
                        )
                        key_colors.append(_format_color(resolved_key))
                    else:
                        key_colors.append("none")
                    snapped, grid_size = _apply_output_mode(
                        snapped,
                        output_mode,
                        input_image.size,
                        output_scale,
                        exact_width,
                        exact_height,
                    )
                    dimensions.append(grid_size)
                    masks.append(_to_mask(snapped))
                    outputs.append(_to_tensor(snapped.copy()))

        shapes = {tuple(item.shape) for item in outputs}
        if len(shapes) != 1:
            raise RuntimeError(
                "Auto-detection produced different grid sizes within the batch. "
                "Set pixel_size explicitly or process the images individually."
            )
        mask_shapes = {tuple(item.shape) for item in masks}
        if len(mask_shapes) != 1:
            raise RuntimeError("Transparency masks have different sizes within the batch.")
        grid_width, grid_height = dimensions[0]
        return (
            torch.stack(outputs),
            grid_width,
            grid_height,
            torch.stack(masks),
            ", ".join(key_colors),
        )


NODE_CLASS_MAPPINGS = {"SpriteFusionPixelSnapper": SpriteFusionPixelSnapper}
NODE_DISPLAY_NAME_MAPPINGS = {
    "SpriteFusionPixelSnapper": "SpriteFusion Pixel Snapper"
}

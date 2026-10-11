"""Image and metadata input helpers for the beam analyzer."""

from __future__ import annotations

import json
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image


SUPPORTED_SUFFIXES = {".tif", ".tiff", ".png", ".bmp", ".jpg", ".jpeg"}


def natural_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def read_image(path: str | Path) -> tuple[np.ndarray, float | None, dict[str, object]]:
    """Read the first frame as grayscale and return data, dtype maximum, metadata."""
    image_path = Path(path)
    with Image.open(image_path) as opened:
        metadata = _extract_metadata(opened)
        array = np.asarray(opened)

    if array.ndim == 3:
        rgb = array[..., :3].astype(float)
        array = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    if array.ndim != 2:
        raise ValueError(f"Unsupported image dimensions: {array.shape}")

    dtype_max: float | None = None
    if np.issubdtype(array.dtype, np.integer):
        dtype_max = float(np.iinfo(array.dtype).max)
    return np.asarray(array), dtype_max, metadata


def average_images(paths: list[str | Path]) -> np.ndarray:
    """Average multiple same-size images in floating point (for dark frames)."""
    if not paths:
        raise ValueError("No images were selected.")
    accumulator: np.ndarray | None = None
    expected_shape: tuple[int, ...] | None = None
    for path in paths:
        image, _, _ = read_image(path)
        if expected_shape is None:
            expected_shape = image.shape
            accumulator = np.zeros(image.shape, dtype=np.float64)
        elif image.shape != expected_shape:
            raise ValueError(f"Dark-frame image size mismatch: {image.shape} != {expected_shape}")
        accumulator += image.astype(np.float64, copy=False)
    assert accumulator is not None
    return accumulator / len(paths)


def _extract_metadata(opened: Image.Image) -> dict[str, object]:
    result: dict[str, object] = {}
    description = None
    try:
        description = opened.tag_v2.get(270)
    except Exception:
        description = opened.info.get("description")
    if isinstance(description, bytes):
        description = description.decode("utf-8", errors="replace")
    if isinstance(description, str):
        try:
            parsed = json.loads(description)
            if isinstance(parsed, dict):
                result.update(parsed)
        except json.JSONDecodeError:
            result["description"] = description
    return result


def _walk_values(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)


def metadata_number(metadata: dict[str, object], candidate_keys: tuple[str, ...]) -> float | None:
    candidates = {key.lower() for key in candidate_keys}
    for key, value in _walk_values(metadata):
        if str(key).lower() in candidates:
            try:
                number = float(value)
                if np.isfinite(number):
                    return number
            except (TypeError, ValueError):
                pass
    return None


def pixel_size_from_metadata(metadata: dict[str, object]) -> float | None:
    return metadata_number(
        metadata,
        (
            "pixel_size_p_float_um",
            "pixel_size_um",
            "pixelsize_um",
            "pixel_pitch_um",
        ),
    )


def z_from_metadata(metadata: dict[str, object]) -> float | None:
    return metadata_number(
        metadata,
        ("z_p_float_mm", "target_z_p_float_mm", "z_mm", "z_position_mm"),
    )


@dataclass
class ImageSet:
    paths: list[Path]
    source_label: str
    _temporary: tempfile.TemporaryDirectory[str] | None = None

    @classmethod
    def from_files(cls, files: list[str | Path]) -> "ImageSet":
        paths = [Path(item) for item in files if Path(item).suffix.lower() in SUPPORTED_SUFFIXES]
        return cls(sorted(paths, key=natural_key), "Selected images")

    @classmethod
    def from_folder(cls, folder: str | Path) -> "ImageSet":
        root = Path(folder)
        paths = [path for path in root.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES]
        return cls(sorted(paths, key=natural_key), root.name)

    @classmethod
    def from_zip(
        cls,
        archive: str | Path,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> "ImageSet":
        archive_path = Path(archive)
        temporary = tempfile.TemporaryDirectory(prefix="beam_d4sigma_")
        output = Path(temporary.name)
        paths: list[Path] = []
        try:
            with zipfile.ZipFile(archive_path) as zipped:
                members = [
                    member
                    for member in zipped.infolist()
                    if not member.is_dir()
                    and Path(member.filename).suffix.lower() in SUPPORTED_SUFFIXES
                ]
                total = len(members)
                if progress is not None:
                    progress(0, max(total, 1), "Reading ZIP directory")
                for index, member in enumerate(members):
                    display_name = Path(member.filename).name
                    if progress is not None:
                        progress(index, total, f"Extracting {display_name}")
                    # Never trust paths stored inside an archive. Use a flat,
                    # deterministic extraction name to prevent path traversal.
                    safe_name = f"{index:04d}_{display_name}"
                    destination = output / safe_name
                    with zipped.open(member) as source, destination.open("wb") as target:
                        while True:
                            block = source.read(1024 * 1024)
                            if not block:
                                break
                            target.write(block)
                    paths.append(destination)
                    if progress is not None:
                        progress(index + 1, total, f"Extracted {display_name}")
        except BaseException:
            temporary.cleanup()
            raise
        paths.sort(key=lambda item: natural_key(Path(item.name.split("_", 1)[-1])))
        return cls(paths, archive_path.name, temporary)

    def display_name(self, path: Path) -> str:
        name = path.name
        if self._temporary is not None and re.match(r"^\d{4}_", name):
            return name.split("_", 1)[1]
        return name

    def close(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

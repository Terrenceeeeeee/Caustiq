"""Unified angle-dependent D4-sigma datasets for image and CSV sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable

import numpy as np
import pandas as pd
from PIL import Image

from image_beam_core import (
    AnalysisSettings,
    BeamResult,
    analyze_beam,
    relative_xy_scan,
)
from image_io import (
    ImageSet,
    metadata_number,
    pixel_size_from_metadata,
    read_image,
    z_from_metadata,
)
from measurement_config import MeasurementConfig


ANGLE_MIN_DEG = -90
ANGLE_MAX_DEG = 90
ANGLE_STEP_DEG = 1
ANGLE_SCAN_DEGREES = np.arange(
    ANGLE_MIN_DEG,
    ANGLE_MAX_DEG + ANGLE_STEP_DEG,
    ANGLE_STEP_DEG,
    dtype=int,
)

SPIRICON_Z_COLUMN = "Z Locations(mm)"
SPIRICON_X_COLUMN = "Beam Widths X - D4 Sigma(μm)"
SPIRICON_Y_COLUMN = "Beam Widths Y - D4 Sigma(μm)"

MATRIX_X_PATTERN = re.compile(r"^D4sigma_X_at_relative_([+-]?\d+)deg_um$")
MATRIX_Y_PATTERN = re.compile(r"^D4sigma_Y_at_relative_([+-]?\d+)deg_um$")


@dataclass(frozen=True)
class AngleSeries:
    """One selected angle, aggregated to one row per distinct Z position."""

    angle_deg: int
    z_mm: np.ndarray
    wx_um: np.ndarray
    wy_um: np.ndarray
    z_stage_mm: np.ndarray
    frame_counts: np.ndarray
    filenames: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def count(self) -> int:
        return int(self.z_mm.size)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Z Locations(mm)": self.z_mm,
                "Stage Z(mm)": self.z_stage_mm,
                "Beam Widths X - D4 Sigma(μm)": self.wx_um,
                "Beam Widths Y - D4 Sigma(μm)": self.wy_um,
                "Relative Deviation(deg)": self.angle_deg,
                "Frames": self.frame_counts,
                "Files": self.filenames,
                "Warnings": self.warnings,
            }
        )


@dataclass(frozen=True)
class AngleDataset:
    """Widths for one or many relative deviation angles at multiple Z rows."""

    source_label: str
    source_kind: str
    angles_deg: np.ndarray
    z_fit_mm: np.ndarray
    z_stage_mm: np.ndarray
    wx_by_angle_um: np.ndarray
    wy_by_angle_um: np.ndarray
    filenames: tuple[str, ...]
    row_warnings: tuple[str, ...]
    z_basis: str
    preview_images: tuple[np.ndarray, ...] = ()
    preview_extents_um: tuple[tuple[float, float, float, float], ...] = ()

    def __post_init__(self) -> None:
        angles = np.asarray(self.angles_deg, dtype=int).reshape(-1)
        z_fit = np.asarray(self.z_fit_mm, dtype=float).reshape(-1)
        z_stage = np.asarray(self.z_stage_mm, dtype=float).reshape(-1)
        wx = np.asarray(self.wx_by_angle_um, dtype=float)
        wy = np.asarray(self.wy_by_angle_um, dtype=float)
        if wx.shape != wy.shape or wx.shape != (angles.size, z_fit.size):
            raise ValueError("Angle dataset matrix dimensions are inconsistent.")
        if z_stage.size != z_fit.size:
            raise ValueError("Stage-Z and fit-Z arrays must have the same length.")
        if len(self.filenames) != z_fit.size or len(self.row_warnings) != z_fit.size:
            raise ValueError("Filename/warning rows do not match the Z data.")
        if self.preview_images and len(self.preview_images) != z_fit.size:
            raise ValueError("Preview image rows do not match the Z data.")
        if self.preview_extents_um and len(self.preview_extents_um) != z_fit.size:
            raise ValueError("Preview extent rows do not match the Z data.")
        if bool(self.preview_images) != bool(self.preview_extents_um):
            raise ValueError("Preview images and physical extents must be supplied together.")
        if angles.size == 0 or z_fit.size < 3:
            raise ValueError("An angle dataset requires at least one angle and three Z rows.")
        if np.unique(angles).size != angles.size:
            raise ValueError("Angle values must be unique.")
        if not np.all(np.isfinite(z_fit)):
            raise ValueError("Fit Z contains missing or non-finite values.")
        if not np.all(np.isfinite(wx)) or not np.all(np.isfinite(wy)):
            raise ValueError("D4-sigma matrices contain missing or non-finite values.")
        if np.any(wx <= 0.0) or np.any(wy <= 0.0):
            raise ValueError("All D4-sigma widths must be positive.")

    @property
    def available_angles(self) -> tuple[int, ...]:
        return tuple(int(item) for item in self.angles_deg)

    @property
    def supports_angle_scan(self) -> bool:
        return self.angles_deg.size > 1

    @property
    def row_count(self) -> int:
        return int(self.z_fit_mm.size)

    @property
    def has_image_previews(self) -> bool:
        return bool(self.preview_images)

    def nearest_preview_index(self, z_mm: float) -> int:
        if not self.has_image_previews:
            raise ValueError("The current source does not contain image previews.")
        return int(np.argmin(np.abs(self.z_fit_mm - float(z_mm))))

    def nearest_available_angle(self, requested_deg: float) -> int:
        requested = int(np.clip(round(requested_deg), ANGLE_MIN_DEG, ANGLE_MAX_DEG))
        index = int(np.argmin(np.abs(self.angles_deg - requested)))
        return int(self.angles_deg[index])

    def series(self, angle_deg: float) -> AngleSeries:
        """Return the selected angle and average duplicate frames at equal Z."""
        angle = self.nearest_available_angle(angle_deg)
        angle_index = int(np.where(self.angles_deg == angle)[0][0])
        x_rows = self.wx_by_angle_um[angle_index]
        y_rows = self.wy_by_angle_um[angle_index]

        order = np.argsort(self.z_fit_mm)
        z_sorted = self.z_fit_mm[order]
        stage_sorted = self.z_stage_mm[order]
        x_sorted = x_rows[order]
        y_sorted = y_rows[order]
        files_sorted = [self.filenames[index] for index in order]
        warnings_sorted = [self.row_warnings[index] for index in order]

        # Stage controllers and TIFF metadata can differ by sub-micron floating
        # noise.  Nine decimals keeps real positions separate while grouping
        # repeated frames from the same commanded Z.
        keys = np.round(z_sorted, 9)
        unique_keys, first_indices, inverse = np.unique(
            keys, return_index=True, return_inverse=True
        )
        grouped_z = np.zeros(unique_keys.size, dtype=float)
        grouped_stage = np.full(unique_keys.size, np.nan, dtype=float)
        grouped_x = np.zeros(unique_keys.size, dtype=float)
        grouped_y = np.zeros(unique_keys.size, dtype=float)
        counts = np.zeros(unique_keys.size, dtype=int)
        grouped_files: list[str] = []
        grouped_warnings: list[str] = []

        for group in range(unique_keys.size):
            mask = inverse == group
            grouped_z[group] = float(np.mean(z_sorted[mask]))
            finite_stage = stage_sorted[mask][np.isfinite(stage_sorted[mask])]
            if finite_stage.size:
                grouped_stage[group] = float(np.mean(finite_stage))
            grouped_x[group] = float(np.mean(x_sorted[mask]))
            grouped_y[group] = float(np.mean(y_sorted[mask]))
            counts[group] = int(np.count_nonzero(mask))
            grouped_files.append("; ".join(files_sorted[index] for index in np.where(mask)[0]))
            warning_items = [warnings_sorted[index] for index in np.where(mask)[0] if warnings_sorted[index]]
            grouped_warnings.append(" | ".join(dict.fromkeys(warning_items)))

        final_order = np.argsort(grouped_z)
        return AngleSeries(
            angle_deg=angle,
            z_mm=grouped_z[final_order],
            wx_um=grouped_x[final_order],
            wy_um=grouped_y[final_order],
            z_stage_mm=grouped_stage[final_order],
            frame_counts=counts[final_order],
            filenames=tuple(grouped_files[index] for index in final_order),
            warnings=tuple(grouped_warnings[index] for index in final_order),
        )

    def all_angles_long_frame(self) -> pd.DataFrame:
        frames = [self.series(int(angle)).to_frame() for angle in self.angles_deg]
        return pd.concat(frames, ignore_index=True)


def _numeric(frame: pd.DataFrame, column: str) -> np.ndarray:
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
    if not np.all(np.isfinite(values)):
        bad = np.where(~np.isfinite(values))[0] + 2
        raise ValueError(f"Column {column!r} has missing/non-numeric rows: {bad.tolist()}")
    return values


def _find_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lookup = {item.casefold(): item for item in columns}
    for candidate in candidates:
        if candidate.casefold() in lookup:
            return lookup[candidate.casefold()]
    return None


def _stage_to_fit_z(
    stage_z_mm: np.ndarray,
    config: MeasurementConfig,
    apply_flange_offset: bool,
) -> tuple[np.ndarray, str]:
    stage = np.asarray(stage_z_mm, dtype=float)
    if not apply_flange_offset:
        return stage.copy(), "Stage Z from CSV"
    sign = int(np.sign(config.flange_offset_sign) or 1)
    return (
        stage + sign * config.camera_flange_distance_mm,
        f"Sensor Z = Stage Z {sign:+d} {config.camera_flange_distance_mm:g} mm",
    )


def _row_labels(frame: pd.DataFrame) -> tuple[str, ...]:
    filename_col = _find_column(list(frame.columns), ("filename", "file", "image"))
    if filename_col is None:
        return tuple(f"row {index + 1}" for index in range(len(frame)))
    return tuple(frame[filename_col].fillna("").astype(str))


def _row_warnings(frame: pd.DataFrame) -> tuple[str, ...]:
    warning_col = _find_column(list(frame.columns), ("warnings", "warning"))
    if warning_col is None:
        return tuple("" for _ in range(len(frame)))
    return tuple(frame[warning_col].fillna("").astype(str))


def _beam_preview(
    result: BeamResult,
    maximum_size_px: int = 512,
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Create a compact, display-only normalized preview around the beam."""
    mask_rows, mask_columns = np.nonzero(result.integration_mask)
    if mask_rows.size:
        y0 = int(mask_rows.min())
        y1 = int(mask_rows.max()) + 1
        x0 = int(mask_columns.min())
        x1 = int(mask_columns.max()) + 1
        pad_x = max(8, int(round(0.12 * max(x1 - x0, 1))))
        pad_y = max(8, int(round(0.12 * max(y1 - y0, 1))))
        x0 = max(0, x0 - pad_x)
        x1 = min(result.signal.shape[1], x1 + pad_x)
        y0 = max(0, y0 - pad_y)
        y1 = min(result.signal.shape[0], y1 + pad_y)
    else:
        y0, x0 = 0, 0
        y1, x1 = result.signal.shape

    cropped = np.clip(result.signal[y0:y1, x0:x1], 0.0, None)
    positive = cropped[cropped > 0.0]
    scale = float(np.percentile(positive, 99.9)) if positive.size else 1.0
    if not np.isfinite(scale) or scale <= 0.0:
        scale = float(np.max(cropped)) if cropped.size else 1.0
    normalized = np.clip(cropped / max(scale, np.finfo(float).eps), 0.0, 1.0)
    preview = np.rint(normalized * 255.0).astype(np.uint8)

    height, width = preview.shape
    resize_scale = min(1.0, maximum_size_px / max(height, width, 1))
    if resize_scale < 1.0:
        resized = (
            max(2, int(round(width * resize_scale))),
            max(2, int(round(height * resize_scale))),
        )
        preview = np.asarray(
            Image.fromarray(preview).resize(resized, Image.Resampling.BILINEAR)
        )

    pixel_um = result.pixel_size_um
    extent = (
        (x0 - result.centroid_x_px) * pixel_um,
        (x1 - 1 - result.centroid_x_px) * pixel_um,
        -(y1 - 1 - result.centroid_y_px) * pixel_um,
        -(y0 - result.centroid_y_px) * pixel_um,
    )
    # Flip the camera rows so +Y points upward in the displayed Cartesian axes.
    return np.flipud(preview), tuple(float(item) for item in extent)


def load_csv_dataset(
    csv_file: str | Path,
    config: MeasurementConfig,
    default_angle_deg: int = 0,
    apply_flange_offset: bool = True,
) -> AngleDataset:
    """Load compatible single-angle, long, or 181-angle matrix CSV data."""
    path = Path(csv_file).expanduser().resolve()
    frame = pd.read_csv(path, encoding="utf-8-sig")
    frame.columns = [str(item).strip() for item in frame.columns]
    if len(frame) < 3:
        raise ValueError("CSV must contain at least three measurement rows.")

    columns = list(frame.columns)
    if all(item in frame.columns for item in (SPIRICON_Z_COLUMN, SPIRICON_X_COLUMN, SPIRICON_Y_COLUMN)):
        angle_col = _find_column(columns, ("Relative Deviation(deg)", "relative_deviation_deg"))
        # A single-angle CSV without an explicit angle is the reference
        # measurement and therefore always represents zero relative deviation.
        # Keep the argument for API compatibility with earlier packages.
        angle = 0
        if angle_col is not None:
            unique_angles = pd.to_numeric(frame[angle_col], errors="coerce").dropna().unique()
            if unique_angles.size == 1:
                angle = int(round(float(unique_angles[0])))
        z = _numeric(frame, SPIRICON_Z_COLUMN)
        x = _numeric(frame, SPIRICON_X_COLUMN)
        y = _numeric(frame, SPIRICON_Y_COLUMN)
        return AngleDataset(
            source_label=path.name,
            source_kind="Single-angle D4sigma CSV",
            angles_deg=np.asarray([angle], dtype=int),
            z_fit_mm=z,
            z_stage_mm=np.full(z.size, np.nan),
            wx_by_angle_um=x.reshape(1, -1),
            wy_by_angle_um=y.reshape(1, -1),
            filenames=_row_labels(frame),
            row_warnings=_row_warnings(frame),
            z_basis="Lens-referenced Z from CSV",
        )

    matrix_x = {
        int(match.group(1)): column
        for column in columns
        if (match := MATRIX_X_PATTERN.match(column)) is not None
    }
    matrix_y = {
        int(match.group(1)): column
        for column in columns
        if (match := MATRIX_Y_PATTERN.match(column)) is not None
    }
    matrix_angles = sorted(set(matrix_x) & set(matrix_y))
    if matrix_angles:
        stage_col = _find_column(columns, ("z_stage_mm", "stage_z_mm"))
        sensor_col = _find_column(columns, ("z_sensor_assumed_mm", "z_sensor_mm"))
        direct_col = _find_column(columns, ("z_mm", "Z Location(mm)"))
        if sensor_col is not None:
            fit_z = _numeric(frame, sensor_col)
            stage_z = _numeric(frame, stage_col) if stage_col is not None else np.full(len(frame), np.nan)
            z_basis = "Sensor Z from CSV"
        elif stage_col is not None:
            stage_z = _numeric(frame, stage_col)
            fit_z, z_basis = _stage_to_fit_z(stage_z, config, apply_flange_offset)
        elif direct_col is not None:
            fit_z = _numeric(frame, direct_col)
            stage_z = np.full(len(frame), np.nan)
            z_basis = "Z from CSV"
        else:
            raise ValueError("Angle-matrix CSV has no usable Z column.")
        wx = np.vstack([_numeric(frame, matrix_x[angle]) for angle in matrix_angles])
        wy = np.vstack([_numeric(frame, matrix_y[angle]) for angle in matrix_angles])
        return AngleDataset(
            source_label=path.name,
            source_kind="Multi-angle matrix CSV",
            angles_deg=np.asarray(matrix_angles, dtype=int),
            z_fit_mm=fit_z,
            z_stage_mm=stage_z,
            wx_by_angle_um=wx,
            wy_by_angle_um=wy,
            filenames=_row_labels(frame),
            row_warnings=_row_warnings(frame),
            z_basis=z_basis,
        )

    angle_col = _find_column(columns, ("relative_deviation_deg", "Relative Deviation(deg)"))
    x_col = _find_column(columns, ("D4sigma_X_um", "D4sigma_selected_X_um", "beam_width_x_um"))
    y_col = _find_column(columns, ("D4sigma_Y_um", "D4sigma_selected_Y_um", "beam_width_y_um"))
    if x_col is None or y_col is None:
        raise ValueError(
            "Unsupported CSV. Expected supported single-angle columns, D4sigma_X/Y_um long-table "
            "columns, or D4sigma_X/Y_at_relative_<angle>deg_um matrix columns."
        )

    stage_col = _find_column(columns, ("z_stage_mm", "stage_z_mm"))
    sensor_col = _find_column(columns, ("z_sensor_assumed_mm", "z_sensor_mm"))
    direct_col = _find_column(columns, ("z_mm", "Z Location(mm)"))
    if stage_col is None and sensor_col is None and direct_col is None:
        raise ValueError("CSV has no usable Z column.")

    work = frame.copy()
    if angle_col is None:
        work["__angle"] = 0
        angle_col = "__angle"
    work[angle_col] = pd.to_numeric(work[angle_col], errors="coerce").round().astype("Int64")
    work = work.dropna(subset=[angle_col])
    work = work[work[angle_col].between(ANGLE_MIN_DEG, ANGLE_MAX_DEG)]
    angles = sorted(int(item) for item in work[angle_col].unique())
    if not angles:
        raise ValueError("No integer relative-deviation angles in -90..+90 were found.")

    key_col = _find_column(columns, ("image_index", "filename"))
    if key_col is None:
        key_col = "__row_key"
        work[key_col] = np.arange(len(work), dtype=int)
    base = work.drop_duplicates(subset=[key_col]).set_index(key_col)
    pivot_x = work.pivot_table(index=key_col, columns=angle_col, values=x_col, aggfunc="mean")
    pivot_y = work.pivot_table(index=key_col, columns=angle_col, values=y_col, aggfunc="mean")
    common_keys = pivot_x.index.intersection(pivot_y.index)
    common_angles = [angle for angle in angles if angle in pivot_x.columns and angle in pivot_y.columns]
    pivot_x = pivot_x.loc[common_keys, common_angles]
    pivot_y = pivot_y.loc[common_keys, common_angles]
    if pivot_x.isna().any().any() or pivot_y.isna().any().any():
        raise ValueError("Long-table CSV does not contain complete X/Y data for every image and angle.")
    base = base.loc[common_keys]

    if sensor_col is not None:
        fit_z = pd.to_numeric(base[sensor_col], errors="coerce").to_numpy(dtype=float)
        stage_z = (
            pd.to_numeric(base[stage_col], errors="coerce").to_numpy(dtype=float)
            if stage_col is not None
            else np.full(len(base), np.nan)
        )
        z_basis = "Sensor Z from CSV"
    elif stage_col is not None:
        stage_z = pd.to_numeric(base[stage_col], errors="coerce").to_numpy(dtype=float)
        fit_z, z_basis = _stage_to_fit_z(stage_z, config, apply_flange_offset)
    else:
        fit_z = pd.to_numeric(base[direct_col], errors="coerce").to_numpy(dtype=float)
        stage_z = np.full(len(base), np.nan)
        z_basis = "Z from CSV"
    if not np.all(np.isfinite(fit_z)):
        raise ValueError("Long-table CSV contains a missing/non-numeric Z value.")

    filenames = (
        tuple(base["filename"].fillna("").astype(str))
        if "filename" in base.columns
        else tuple(str(item) for item in common_keys)
    )
    warnings = (
        tuple(base["warnings"].fillna("").astype(str))
        if "warnings" in base.columns
        else tuple("" for _ in range(len(base)))
    )
    return AngleDataset(
        source_label=path.name,
        source_kind="Angle long-table CSV" if len(common_angles) > 1 else "Single-angle data CSV",
        angles_deg=np.asarray(common_angles, dtype=int),
        z_fit_mm=fit_z,
        z_stage_mm=stage_z,
        wx_by_angle_um=pivot_x.to_numpy(dtype=float).T,
        wy_by_angle_um=pivot_y.to_numpy(dtype=float).T,
        filenames=filenames,
        row_warnings=warnings,
        z_basis=z_basis,
    )


def analyze_image_set(
    image_set: ImageSet,
    config: MeasurementConfig,
    settings: AnalysisSettings,
    dark_frame: np.ndarray | None = None,
    auto_pixel_from_metadata: bool = True,
    apply_flange_offset: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
) -> AngleDataset:
    """Analyze an image sequence once and retain all 181 angular widths."""
    if not image_set.paths:
        raise ValueError("No supported beam images were found.")
    config.validate()
    settings.validate()
    total = len(image_set.paths)
    if total == config.measurement_points * config.frames_per_position:
        fallback_stage = np.repeat(
            np.linspace(
                config.measurement_start_mm,
                config.measurement_end_mm,
                config.measurement_points,
            ),
            config.frames_per_position,
        )
    else:
        fallback_stage = np.linspace(
            config.measurement_start_mm,
            config.measurement_end_mm,
            total,
        )

    z_stage_rows: list[float] = []
    z_fit_rows: list[float] = []
    x_rows: list[np.ndarray] = []
    y_rows: list[np.ndarray] = []
    filenames: list[str] = []
    warnings: list[str] = []
    preview_images: list[np.ndarray] = []
    preview_extents: list[tuple[float, float, float, float]] = []
    used_fallback = False

    for index, path in enumerate(image_set.paths):
        display_name = image_set.display_name(path)
        if progress is not None:
            progress(index, total, f"Analyzing {display_name}")
        image, dtype_max, metadata = read_image(path)
        metadata_pixel = pixel_size_from_metadata(metadata) if auto_pixel_from_metadata else None
        active_settings = AnalysisSettings(
            pixel_size_um=metadata_pixel or settings.pixel_size_um,
            magnification=settings.magnification,
            camera_angle_deg=settings.camera_angle_deg,
            background=settings.background,
            mode=settings.mode,
            aperture_sigma=settings.aperture_sigma,
            threshold_sigma=settings.threshold_sigma,
            core_fraction=settings.core_fraction,
            border_fraction=settings.border_fraction,
        )
        result = analyze_beam(
            image,
            active_settings,
            dtype_max=dtype_max,
            dark_frame=dark_frame,
        )
        stage_z = z_from_metadata(metadata)
        row_warning = list(result.warnings)
        if stage_z is None:
            stage_z = float(fallback_stage[index])
            used_fallback = True
            row_warning.append("Z missing in image metadata; assigned from configured start/end positions")
        stage_z = float(stage_z)
        fit_z, _ = _stage_to_fit_z(
            np.asarray([stage_z]), config, apply_flange_offset
        )
        x_scan, y_scan = relative_xy_scan(result, ANGLE_SCAN_DEGREES)
        preview, preview_extent = _beam_preview(result)

        z_stage_rows.append(stage_z)
        z_fit_rows.append(float(fit_z[0]))
        x_rows.append(x_scan)
        y_rows.append(y_scan)
        preview_images.append(preview)
        preview_extents.append(preview_extent)
        filenames.append(display_name)
        exposure = metadata_number(metadata, ("exposure_time_p_float_ms",))
        if exposure is not None:
            row_warning.append(f"Exposure {exposure:g} ms")
        warnings.append(" | ".join(row_warning))
        if progress is not None:
            progress(index + 1, total, f"Completed {filenames[-1]}")

    sign = int(np.sign(config.flange_offset_sign) or 1)
    if apply_flange_offset:
        z_basis = f"Sensor Z = Stage Z {sign:+d} {config.camera_flange_distance_mm:g} mm"
    else:
        z_basis = "Stage Z from image metadata/configuration"
    if used_fallback:
        z_basis += "; missing metadata Z assigned linearly from configuration"

    return AngleDataset(
        source_label=image_set.source_label,
        source_kind="Image sequence (181-angle D4σ)",
        angles_deg=ANGLE_SCAN_DEGREES.copy(),
        z_fit_mm=np.asarray(z_fit_rows, dtype=float),
        z_stage_mm=np.asarray(z_stage_rows, dtype=float),
        wx_by_angle_um=np.asarray(x_rows, dtype=float).T,
        wy_by_angle_um=np.asarray(y_rows, dtype=float).T,
        filenames=tuple(filenames),
        row_warnings=tuple(warnings),
        z_basis=z_basis,
        preview_images=tuple(preview_images),
        preview_extents_um=tuple(preview_extents),
    )

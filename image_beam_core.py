"""ISO 11146-style D4-sigma beam-width calculations.

The calculation in this module follows the second-moment definition.  The
quality of a real measurement still depends on camera linearity, calibration,
background acquisition and a suitable integration area.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
from scipy import ndimage


RELATIVE_DEVIATION_DEGREES = tuple(range(-90, 91))
# Backward-compatible constant name. Values now represent XY relative
# deviation angles, not absolute directions from the horizontal axis.
ANGLE_SCAN_DEGREES = RELATIVE_DEVIATION_DEGREES


class BeamAnalysisError(RuntimeError):
    """Raised when a meaningful beam moment cannot be calculated."""


@dataclass(frozen=True)
class AnalysisSettings:
    pixel_size_um: float = 3.45
    magnification: float = 1.0
    camera_angle_deg: float = 0.0
    background: str = "plane"  # "plane" or "constant"
    mode: str = "auto"  # "auto" or "roi"
    aperture_sigma: float = 3.0
    threshold_sigma: float = 5.0
    core_fraction: float = 0.08
    border_fraction: float = 0.08

    def validate(self) -> None:
        if not np.isfinite(self.pixel_size_um) or self.pixel_size_um <= 0:
            raise BeamAnalysisError("Pixel size must be greater than zero.")
        if not np.isfinite(self.magnification) or self.magnification <= 0:
            raise BeamAnalysisError("Camera imaging magnification must be greater than zero.")
        if not np.isfinite(self.camera_angle_deg):
            raise BeamAnalysisError("Camera orientation angle must be finite.")
        if self.background not in {"plane", "constant"}:
            raise BeamAnalysisError("Unknown background model.")
        if self.mode not in {"auto", "roi"}:
            raise BeamAnalysisError("Unknown integration mode.")
        if not 2.0 <= self.aperture_sigma <= 8.0:
            raise BeamAnalysisError("The automatic integration radius must be between 2 and 8 sigma.")
        if not 0.0 < self.core_fraction < 1.0:
            raise BeamAnalysisError("The core-threshold fraction must be between 0 and 1.")
        if self.threshold_sigma < 0:
            raise BeamAnalysisError("The noise threshold cannot be negative.")
        if not 0.01 <= self.border_fraction <= 0.25:
            raise BeamAnalysisError("The background-border fraction must be between 0.01 and 0.25.")


@dataclass
class BeamResult:
    centroid_x_px: float
    centroid_y_px: float
    covariance_px2: np.ndarray
    pixel_size_um: float
    sensor_pixel_size_um: float
    magnification: float
    camera_angle_deg: float
    background_level: float
    noise_sigma: float
    dark_frame_mean: float | None
    peak_raw: float
    peak_above_background: float
    saturation_fraction: float | None
    integration_mask: np.ndarray = field(repr=False)
    signal: np.ndarray = field(repr=False)
    roi: tuple[int, int, int, int]
    mode: str
    excluded_confident_fraction: float
    warnings: list[str]

    @property
    def d_x_um(self) -> float:
        return self.diameter_um(0.0)

    @property
    def d_y_um(self) -> float:
        return self.diameter_um(90.0)

    @property
    def principal(self) -> tuple[float, float, float]:
        """Return major/minor diameters and major-axis angle in lab coordinates."""
        major, minor, camera_angle = self.principal_camera
        return major, minor, normalize_axis_angle(camera_angle + self.camera_angle_deg)

    @property
    def principal_camera(self) -> tuple[float, float, float]:
        """Return major/minor diameters and angle in camera-image coordinates."""
        values, vectors = np.linalg.eigh(self.covariance_px2)
        if values[0] < 0:
            raise BeamAnalysisError("The second-moment matrix is not positive definite.")
        major_index = int(np.argmax(values))
        minor_index = 1 - major_index
        vector = vectors[:, major_index]
        angle = float(np.degrees(np.arctan2(vector[1], vector[0])))
        angle = normalize_axis_angle(angle)
        major = 4.0 * np.sqrt(values[major_index]) * self.pixel_size_um
        minor = 4.0 * np.sqrt(values[minor_index]) * self.pixel_size_um
        return float(major), float(minor), angle

    def diameter_um(self, angle_deg: float) -> float:
        # A lab-frame direction corresponds to this direction in the camera
        # frame.  The image covariance itself stays unrotated so overlays remain
        # registered to the source pixels.
        return directional_diameter_um(
            self.covariance_px2,
            angle_deg - self.camera_angle_deg,
            self.pixel_size_um,
        )

    def as_record(
        self,
        filename: str = "",
        z_mm: float | None = None,
        selected_relative_deviation_deg: float = 0.0,
        roi_origin_px: tuple[int, int] = (0, 0),
        flange_distance_mm: float | None = None,
        flange_offset_sign: int = 1,
        laser_id: str = "",
    ) -> dict[str, object]:
        major, minor, major_angle = self.principal
        selected_x, selected_y = relative_xy_diameters_um(
            self, selected_relative_deviation_deg
        )
        selected_x_angle, selected_y_angle = relative_axes_angles_deg(
            selected_relative_deviation_deg
        )
        zero_x, zero_y = relative_xy_diameters_um(self, 0.0)
        x0, y0, x1, y1 = self.roi
        sensor_z_mm = None
        if z_mm is not None and flange_distance_mm is not None:
            sensor_z_mm = z_mm + int(np.sign(flange_offset_sign) or 1) * flange_distance_mm
        return {
            "filename": filename,
            "laser_id": laser_id,
            "z_stage_mm": z_mm,
            "z_sensor_assumed_mm": sensor_z_mm,
            "sensor_pixel_size_um": self.sensor_pixel_size_um,
            "magnification": self.magnification,
            "effective_pixel_size_um": self.pixel_size_um,
            "camera_angle_deg": self.camera_angle_deg,
            "centroid_x_px": self.centroid_x_px,
            "centroid_y_px": self.centroid_y_px,
            "centroid_sensor_x_px": self.centroid_x_px + roi_origin_px[0],
            "centroid_sensor_y_px": self.centroid_y_px + roi_origin_px[1],
            "D4sigma_lab_horizontal_um": self.diameter_um(0.0),
            "D4sigma_lab_vertical_um": self.diameter_um(90.0),
            "D4sigma_X_relative_0deg_um": zero_x,
            "D4sigma_Y_relative_0deg_um": zero_y,
            "selected_relative_deviation_deg": selected_relative_deviation_deg,
            "selected_xy_rotation_ccw_deg": -selected_relative_deviation_deg,
            "selected_X_axis_from_horizontal_deg": selected_x_angle,
            "selected_X_axis_equivalent_deg": normalize_axis_angle(selected_x_angle),
            "selected_Y_axis_from_horizontal_deg": selected_y_angle,
            "selected_Y_axis_equivalent_deg": normalize_axis_angle(selected_y_angle),
            "D4sigma_selected_X_um": selected_x,
            "D4sigma_selected_Y_um": selected_y,
            "D4sigma_major_um": major,
            "D4sigma_minor_um": minor,
            "major_axis_angle_from_horizontal_deg": major_angle,
            "background_level": self.background_level,
            "noise_sigma": self.noise_sigma,
            "dark_frame_mean": self.dark_frame_mean,
            "peak_raw": self.peak_raw,
            "peak_above_background": self.peak_above_background,
            "saturation_fraction": self.saturation_fraction,
            "integration_mode": self.mode,
            "roi_x0": x0,
            "roi_y0": y0,
            "roi_x1": x1,
            "roi_y1": y1,
            "integration_pixels": int(np.count_nonzero(self.integration_mask)),
            "excluded_confident_fraction": self.excluded_confident_fraction,
            "warnings": " | ".join(self.warnings),
        }


def normalize_axis_angle(angle_deg: float) -> float:
    """Normalize an undirected-axis angle to [-90, 90)."""
    return float((angle_deg + 90.0) % 180.0 - 90.0)


def directional_diameter_um(
    covariance_px2: np.ndarray, angle_deg: float, pixel_size_um: float
) -> float:
    """D4-sigma diameter along an axis at ``angle_deg``.

    The angle is counter-clockwise from +X in a Cartesian coordinate system.
    Because this is an undirected measurement axis, theta and theta + 180 deg
    are identical; consequently -90 deg and +90 deg must return the same value.
    """
    theta = np.radians(float(angle_deg))
    direction = np.array([np.cos(theta), np.sin(theta)], dtype=float)
    variance = float(direction @ covariance_px2 @ direction)
    if variance < -1e-10:
        raise BeamAnalysisError("The second moment in the selected direction is negative.")
    return float(4.0 * np.sqrt(max(variance, 0.0)) * pixel_size_um)


def angle_scan_record(
    result: BeamResult,
    angles: Iterable[int] = ANGLE_SCAN_DEGREES,
) -> dict[str, float]:
    """Return one CSV-ready D4-sigma value for every requested integer angle."""
    return {
        f"D4sigma_{int(angle):+d}deg_um": result.diameter_um(float(angle))
        for angle in angles
    }


def orthogonal_diameters_um(
    result: BeamResult, x_axis_angle_deg: float
) -> tuple[float, float]:
    """Return D4-sigma sizes on rotated, mutually orthogonal X and Y axes.

    ``x_axis_angle_deg`` is the rotated X-axis direction in lab coordinates.
    The corresponding Y axis is ``x_axis_angle_deg + 90 degrees``.
    """
    x_angle = float(x_axis_angle_deg)
    return result.diameter_um(x_angle), result.diameter_um(x_angle + 90.0)


def relative_axes_angles_deg(relative_deviation_deg: float) -> tuple[float, float]:
    """Convert the user XY deviation angle to absolute lab-axis directions.

    At deviation 0 degrees, X is vertical (90 degrees from horizontal) and Y
    is horizontal (0 degrees). A negative deviation rotates the complete XY
    frame counter-clockwise. Therefore deviation -10 degrees maps to
    X=100 degrees and Y=10 degrees in Cartesian lab coordinates.
    """
    deviation = float(relative_deviation_deg)
    return 90.0 - deviation, -deviation


def relative_xy_diameters_um(
    result: BeamResult, relative_deviation_deg: float
) -> tuple[float, float]:
    """Return D4-sigma X/Y sizes using the relative-deviation convention."""
    x_angle, y_angle = relative_axes_angles_deg(relative_deviation_deg)
    return result.diameter_um(x_angle), result.diameter_um(y_angle)


def xy_angle_scan_record(
    result: BeamResult,
    angles: Iterable[int] = RELATIVE_DEVIATION_DEGREES,
) -> dict[str, float]:
    """Return paired X/Y sizes for every relative deviation angle."""
    record: dict[str, float] = {}
    for angle in angles:
        integer_angle = int(angle)
        x_size, y_size = relative_xy_diameters_um(result, integer_angle)
        record[f"D4sigma_X_at_relative_{integer_angle:+d}deg_um"] = x_size
        record[f"D4sigma_Y_at_relative_{integer_angle:+d}deg_um"] = y_size
    return record


def relative_xy_scan(
    result: BeamResult,
    deviations_deg: Iterable[float],
) -> tuple[np.ndarray, np.ndarray]:
    """Return X and Y arrays using one vectorized covariance projection."""
    deviations = np.asarray(list(deviations_deg), dtype=float)

    def diameters(angles_deg: np.ndarray) -> np.ndarray:
        theta = np.radians(angles_deg - result.camera_angle_deg)
        cos_theta = np.cos(theta)
        sin_theta = np.sin(theta)
        covariance = result.covariance_px2
        variance = (
            covariance[0, 0] * cos_theta**2
            + 2.0 * covariance[0, 1] * cos_theta * sin_theta
            + covariance[1, 1] * sin_theta**2
        )
        if np.any(variance < -1e-10):
            raise BeamAnalysisError("The second moment in the selected direction is negative.")
        return 4.0 * np.sqrt(np.maximum(variance, 0.0)) * result.pixel_size_um

    return diameters(90.0 - deviations), diameters(-deviations)


def _border_mask(shape: tuple[int, int], fraction: float) -> np.ndarray:
    height, width = shape
    border = max(4, int(round(min(height, width) * fraction)))
    border = min(border, max(4, min(height, width) // 3))
    mask = np.zeros(shape, dtype=bool)
    mask[:border, :] = True
    mask[-border:, :] = True
    mask[:, :border] = True
    mask[:, -border:] = True
    return mask


def _mad_sigma(values: np.ndarray) -> float:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return 0.0
    median = np.median(finite)
    sigma = 1.4826 * np.median(np.abs(finite - median))
    if sigma <= np.finfo(float).eps:
        sigma = float(np.std(finite))
    return float(max(sigma, np.finfo(float).eps))


def estimate_background(
    image: np.ndarray, method: str = "plane", border_fraction: float = 0.08
) -> tuple[np.ndarray, float, float]:
    """Estimate a constant or robust tilted-plane background from the border."""
    data = np.asarray(image, dtype=float)
    if data.ndim != 2:
        raise BeamAnalysisError("Background estimation supports only 2D grayscale images.")
    edge = _border_mask(data.shape, border_fraction)
    edge_y, edge_x = np.nonzero(edge)
    edge_values = data[edge]

    if method == "constant":
        level = float(np.median(edge_values))
        background = np.full(data.shape, level, dtype=float)
        noise = _mad_sigma(edge_values - level)
        return background, level, noise

    if method != "plane":
        raise BeamAnalysisError("The background model must be 'plane' or 'constant'.")

    # Use normalized coordinates to keep the least-squares system well scaled.
    x_edge = (edge_x - (data.shape[1] - 1) / 2.0) / max(data.shape[1], 1)
    y_edge = (edge_y - (data.shape[0] - 1) / 2.0) / max(data.shape[0], 1)
    design = np.column_stack((np.ones(edge_values.size), x_edge, y_edge))

    # Subsample very large borders, but preserve deterministic behavior.
    stride = max(1, edge_values.size // 100_000)
    fit_design = design[::stride]
    fit_values = edge_values[::stride]
    keep = np.ones(fit_values.size, dtype=bool)
    beta = np.array([np.median(fit_values), 0.0, 0.0], dtype=float)
    for _ in range(6):
        if np.count_nonzero(keep) < 20:
            break
        beta, *_ = np.linalg.lstsq(fit_design[keep], fit_values[keep], rcond=None)
        residual = fit_values - fit_design @ beta
        center = np.median(residual[keep])
        sigma = _mad_sigma(residual[keep])
        new_keep = np.abs(residual - center) <= 3.5 * sigma
        if np.array_equal(new_keep, keep):
            break
        keep = new_keep

    x_all = (
        np.arange(data.shape[1], dtype=float) - (data.shape[1] - 1) / 2.0
    ) / max(data.shape[1], 1)
    y_all = (
        np.arange(data.shape[0], dtype=float) - (data.shape[0] - 1) / 2.0
    ) / max(data.shape[0], 1)
    background = beta[0] + beta[1] * x_all[None, :] + beta[2] * y_all[:, None]
    residual_edge = edge_values - background[edge]
    center_residual = np.median(residual_edge)
    noise = _mad_sigma(residual_edge - center_residual)
    level = float(background[data.shape[0] // 2, data.shape[1] // 2])
    return background, level, noise


def _normalize_roi(
    roi: tuple[float, float, float, float] | None, shape: tuple[int, int]
) -> tuple[int, int, int, int]:
    height, width = shape
    if roi is None:
        return 0, 0, width, height
    xa, ya, xb, yb = roi
    x0 = max(0, min(width - 1, int(np.floor(min(xa, xb)))))
    y0 = max(0, min(height - 1, int(np.floor(min(ya, yb)))))
    x1 = max(x0 + 1, min(width, int(np.ceil(max(xa, xb)))))
    y1 = max(y0 + 1, min(height, int(np.ceil(max(ya, yb)))))
    if x1 - x0 < 5 or y1 - y0 < 5:
        raise BeamAnalysisError("The ROI is too small; at least 5 x 5 pixels are required.")
    return x0, y0, x1, y1


def _roi_mask(shape: tuple[int, int], roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    mask = np.zeros(shape, dtype=bool)
    mask[y0:y1, x0:x1] = True
    return mask


def _moments(
    signal: np.ndarray, mask: np.ndarray, permit_signed: bool = True
) -> tuple[float, float, float, np.ndarray]:
    rows, columns = np.nonzero(mask)
    samples = signal[rows, columns]
    if permit_signed:
        weights = samples
    else:
        weights = np.clip(samples, 0.0, None)
    total = float(np.sum(weights))
    positive_total = float(np.sum(np.clip(samples, 0.0, None)))
    if total <= max(np.finfo(float).eps, positive_total * 1e-6):
        raise BeamAnalysisError(
            "Insufficient integrated energy after background subtraction; check the ROI or background settings."
        )

    centroid_x = float(np.sum(weights * columns) / total)
    centroid_row = float(np.sum(weights * rows) / total)
    dx = columns - centroid_x
    # Cartesian +Y points upward, while image row indices point downward.
    dy = -(rows - centroid_row)
    covariance = np.array(
        [
            [np.sum(weights * dx * dx) / total, np.sum(weights * dx * dy) / total],
            [np.sum(weights * dx * dy) / total, np.sum(weights * dy * dy) / total],
        ],
        dtype=float,
    )
    covariance = (covariance + covariance.T) / 2.0
    eigenvalues = np.linalg.eigvalsh(covariance)
    if not np.all(np.isfinite(covariance)) or eigenvalues[0] <= 0:
        raise BeamAnalysisError(
            "The integration produced invalid second moments; the ROI may contain excessive background or negative residuals."
        )
    return total, centroid_x, centroid_row, covariance


def _peak_component(core_mask: np.ndarray, peak_index: tuple[int, int]) -> np.ndarray:
    labels, _ = ndimage.label(core_mask, structure=np.ones((3, 3), dtype=np.uint8))
    peak_label = int(labels[peak_index])
    if peak_label == 0:
        raise BeamAnalysisError("No beam core connected to the peak could be found.")
    return labels == peak_label


def _auto_integration_mask(
    signal: np.ndarray,
    allowed_mask: np.ndarray,
    noise_sigma: float,
    settings: AnalysisSettings,
) -> tuple[np.ndarray, float, float, np.ndarray]:
    allowed_values = np.where(allowed_mask, signal, -np.inf)
    peak_flat = int(np.argmax(allowed_values))
    peak_index = np.unravel_index(peak_flat, signal.shape)
    peak = float(signal[peak_index])
    if not np.isfinite(peak) or peak <= 0:
        raise BeamAnalysisError("The ROI contains no valid beam signal above the background.")

    threshold = max(settings.threshold_sigma * noise_sigma, settings.core_fraction * peak)
    core_mask = allowed_mask & (signal >= threshold)
    component = _peak_component(core_mask, peak_index)
    if np.count_nonzero(component) < 9:
        raise BeamAnalysisError("Too few beam-core pixels; lower the threshold or check the exposure.")

    _, cx, cy, covariance = _moments(signal, component, permit_signed=False)
    mask = component
    for _ in range(30):
        inverse = np.linalg.inv(covariance)
        half_width = settings.aperture_sigma * np.sqrt(max(covariance[0, 0], 0.0))
        half_height = settings.aperture_sigma * np.sqrt(max(covariance[1, 1], 0.0))
        x0 = max(0, int(np.floor(cx - half_width)) - 2)
        x1 = min(signal.shape[1], int(np.ceil(cx + half_width)) + 3)
        y0 = max(0, int(np.floor(cy - half_height)) - 2)
        y1 = min(signal.shape[0], int(np.ceil(cy + half_height)) + 3)
        x_coordinates = np.arange(x0, x1, dtype=float) - cx
        y_coordinates = -(np.arange(y0, y1, dtype=float) - cy)
        distance2 = (
            inverse[0, 0] * x_coordinates[None, :] ** 2
            + 2.0
            * inverse[0, 1]
            * y_coordinates[:, None]
            * x_coordinates[None, :]
            + inverse[1, 1] * y_coordinates[:, None] ** 2
        )
        new_mask = np.zeros(signal.shape, dtype=bool)
        new_mask[y0:y1, x0:x1] = allowed_mask[y0:y1, x0:x1] & (
            distance2 <= settings.aperture_sigma**2
        )
        if np.array_equal(new_mask, mask):
            break
        try:
            _, new_cx, new_cy, new_covariance = _moments(
                signal, new_mask, permit_signed=True
            )
        except BeamAnalysisError:
            _, new_cx, new_cy, new_covariance = _moments(
                signal, new_mask, permit_signed=False
            )
        change = max(
            abs(new_cx - cx),
            abs(new_cy - cy),
            float(np.max(np.abs(new_covariance - covariance)))
            / max(float(np.max(np.abs(covariance))), 1.0),
        )
        mask, cx, cy, covariance = new_mask, new_cx, new_cy, new_covariance
        if change < 1e-6:
            break
    return mask, cx, cy, covariance


def _touches_image_boundary(mask: np.ndarray) -> bool:
    return bool(mask[0, :].any() or mask[-1, :].any() or mask[:, 0].any() or mask[:, -1].any())


def analyze_beam(
    image: np.ndarray,
    settings: AnalysisSettings | None = None,
    roi: tuple[float, float, float, float] | None = None,
    dtype_max: float | None = None,
    dark_frame: np.ndarray | None = None,
) -> BeamResult:
    """Analyze a 2-D beam image using background-corrected second moments."""
    settings = settings or AnalysisSettings()
    settings.validate()
    original = np.asarray(image)
    if original.ndim != 2:
        raise BeamAnalysisError("Provide a 2D grayscale image.")
    if min(original.shape) < 16:
        raise BeamAnalysisError("The image is too small.")
    raw_data = original.astype(float, copy=False)
    if not np.all(np.isfinite(raw_data)):
        raise BeamAnalysisError("The image contains NaN or infinite values.")

    dark_mean: float | None = None
    if dark_frame is not None:
        dark = np.asarray(dark_frame, dtype=float)
        if dark.shape != raw_data.shape:
            raise BeamAnalysisError(
                f"Dark-frame size {dark.shape} does not match beam-image size {raw_data.shape}."
            )
        if not np.all(np.isfinite(dark)):
            raise BeamAnalysisError("The dark frame contains NaN or infinite values.")
        dark_mean = float(np.mean(dark))
        data = raw_data - dark
    else:
        data = raw_data

    normalized_roi = _normalize_roi(roi, data.shape)
    allowed = _roi_mask(data.shape, normalized_roi)
    background, background_level, noise_sigma = estimate_background(
        data, settings.background, settings.border_fraction
    )
    signal = data - background

    if settings.mode == "auto":
        integration_mask, cx, cy, covariance = _auto_integration_mask(
            signal, allowed, noise_sigma, settings
        )
    else:
        integration_mask = allowed
        try:
            _, cx, cy, covariance = _moments(signal, integration_mask, permit_signed=True)
        except BeamAnalysisError:
            _, cx, cy, covariance = _moments(signal, integration_mask, permit_signed=False)

    peak_raw = float(np.max(raw_data[allowed]))
    peak_above = float(np.max(signal[allowed]))
    saturation = None
    if dtype_max is not None and np.isfinite(dtype_max) and dtype_max > 0:
        saturation = peak_raw / float(dtype_max)

    confident_threshold = max(5.0 * noise_sigma, 0.01 * max(peak_above, 0.0))
    confident_weights = np.where(allowed & (signal > confident_threshold), signal, 0.0)
    confident_total = float(np.sum(confident_weights))
    outside = float(np.sum(np.where(~integration_mask, confident_weights, 0.0)))
    excluded_fraction = outside / confident_total if confident_total > 0 else 0.0

    warnings: list[str] = []
    if saturation is not None and saturation >= 0.98:
        warnings.append("Peak intensity is near saturation; second moments may be distorted")
    if _touches_image_boundary(integration_mask):
        warnings.append("The integration region touches the image boundary; the beam may be clipped")
    if settings.mode == "auto" and excluded_fraction > 0.05:
        warnings.append(
            f"{excluded_fraction:.1%} significant signal remains outside the automatic integration region (possible tail or stray light)"
        )
    x0, y0, x1, y1 = normalized_roi
    if cx < x0 or cx >= x1 or cy < y0 or cy >= y1:
        warnings.append("The calculated centroid lies outside the ROI; check background subtraction")

    return BeamResult(
        centroid_x_px=cx,
        centroid_y_px=cy,
        covariance_px2=covariance,
        pixel_size_um=settings.pixel_size_um / settings.magnification,
        sensor_pixel_size_um=settings.pixel_size_um,
        magnification=settings.magnification,
        camera_angle_deg=settings.camera_angle_deg,
        background_level=background_level,
        noise_sigma=noise_sigma,
        dark_frame_mean=dark_mean,
        peak_raw=peak_raw,
        peak_above_background=peak_above,
        saturation_fraction=saturation,
        integration_mask=integration_mask,
        signal=signal,
        roi=normalized_roi,
        mode=settings.mode,
        excluded_confident_fraction=excluded_fraction,
        warnings=warnings,
    )


def angle_scan(result: BeamResult, angles_deg: Iterable[float]) -> np.ndarray:
    return np.asarray([result.diameter_um(angle) for angle in angles_deg], dtype=float)

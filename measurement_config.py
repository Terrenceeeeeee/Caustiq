"""Measurement-system configuration used by the GUI and batch reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
from pathlib import Path


@dataclass
class MeasurementConfig:
    camera_model: str = "Example monochrome camera"
    camera_serial: str = "DEMO-CAMERA"
    roi_width_px: int = 1024
    roi_height_px: int = 1024
    roi_origin_x_px: int = 0
    roi_origin_y_px: int = 0
    pixel_format: str = "Mono16"
    sensor_pixel_size_um: float = 3.45
    black_level_setting: float = 2.0
    camera_flange_distance_mm: float = 10.0
    flange_offset_sign: int = 1
    laser_id: str = "DEMO-LASER"
    wavelength_nm: float = 1030.0
    power_w: float = 1.0
    repetition_rate_khz: float = 1.0
    measurement_start_mm: float = 360.0
    measurement_end_mm: float = 470.0
    measurement_points: int = 23
    laser_distance_mm: float = 1000.0
    initial_exposure_value: float = 1.0
    initial_exposure_unit: str = "ms (user value; TIFF metadata is also stored in ms)"
    frames_per_position: int = 1
    background_frames: int = 0
    magnification: float = 1.0
    camera_angle_deg: float = 0.0
    initial_relative_deviation_deg: float = 0.0

    @property
    def roi_origin(self) -> tuple[int, int]:
        return self.roi_origin_x_px, self.roi_origin_y_px

    @property
    def effective_pixel_size_um(self) -> float:
        if self.magnification <= 0:
            raise ValueError("Camera magnification must be greater than zero.")
        return self.sensor_pixel_size_um / self.magnification

    def validate(self) -> None:
        if self.roi_width_px <= 0 or self.roi_height_px <= 0:
            raise ValueError("ROI dimensions must be greater than zero.")
        if self.sensor_pixel_size_um <= 0:
            raise ValueError("Pixel size must be greater than zero.")
        if self.magnification <= 0:
            raise ValueError("Camera magnification must be greater than zero.")
        if self.wavelength_nm <= 0:
            raise ValueError("Wavelength must be greater than zero.")
        if self.measurement_points <= 2:
            raise ValueError("At least three measurement positions are required.")
        if self.frames_per_position <= 0 or self.background_frames < 0:
            raise ValueError("The frame-acquisition configuration is invalid.")
        if self.flange_offset_sign not in {-1, 1}:
            raise ValueError("The flange-distance sign must be +1 or -1.")

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "MeasurementConfig":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if (
            "initial_relative_deviation_deg" not in payload
            and "initial_axis_angle_deg" in payload
        ):
            payload["initial_relative_deviation_deg"] = payload["initial_axis_angle_deg"]
        allowed = {item.name for item in fields(cls)}
        config = cls(**{key: value for key, value in payload.items() if key in allowed})
        config.validate()
        return config


DEFAULT_CONFIG = MeasurementConfig()

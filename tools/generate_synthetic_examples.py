"""Recreate the public, entirely synthetic CSV examples deterministically."""

from __future__ import annotations

import csv
from math import cos, pi, sin, sqrt
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sample_data"
Z_VALUES = [360.0 + 5.0 * index for index in range(23)]
ANGLES = range(-90, 91)


def widths(z_mm: float, angle_deg: int) -> tuple[float, float]:
    radians = angle_deg * pi / 180.0
    shift = sin(radians)
    waist_x_um = 180.0 + 10.0 * shift
    waist_y_um = 175.0 - 8.0 * shift
    theta_x_mrad = 8.5 + 0.2 * shift
    theta_y_mrad = 9.5 - 0.1 * shift
    waist_x_z_mm = 398.0 + 4.0 * shift
    waist_y_z_mm = 410.0 - 3.0 * shift
    return (
        sqrt(waist_x_um**2 + theta_x_mrad**2 * (z_mm - waist_x_z_mm) ** 2),
        sqrt(waist_y_um**2 + theta_y_mrad**2 * (z_mm - waist_y_z_mm) ** 2),
    )


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with (OUT / "synthetic_single_angle.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Z Locations(mm)", "Beam Widths X - D4 Sigma(μm)", "Beam Widths Y - D4 Sigma(μm)"])
        for z_mm in Z_VALUES:
            wx, wy = widths(z_mm, 0)
            writer.writerow([f"{z_mm:.9f}", f"{wx:.12f}", f"{wy:.12f}"])

    with (OUT / "synthetic_angle_matrix.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["z_mm", *[f"D4sigma_X_at_relative_{angle:+d}deg_um" for angle in ANGLES],
                         *[f"D4sigma_Y_at_relative_{angle:+d}deg_um" for angle in ANGLES]])
        for z_mm in Z_VALUES:
            pairs = [widths(z_mm, angle) for angle in ANGLES]
            writer.writerow([f"{z_mm:.9f}", *[f"{wx:.12f}" for wx, _ in pairs],
                             *[f"{wy:.12f}" for _, wy in pairs]])

    with (OUT / "synthetic_angle_long.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["image_index", "z_mm", "relative_deviation_deg", "D4sigma_X_um", "D4sigma_Y_um"])
        for index, z_mm in enumerate(Z_VALUES):
            for angle in ANGLES:
                wx, wy = widths(z_mm, angle)
                writer.writerow([index, f"{z_mm:.9f}", angle, f"{wx:.12f}", f"{wy:.12f}"])


if __name__ == "__main__":
    main()

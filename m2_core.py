#!/usr/bin/env python3
"""M2 analysis for D4-sigma beam-width CSV data.

Widths are full D4-sigma diameters, divergence angles are full angles,
and the refractive index of air is taken as 1. Calculation details and
limitations are documented in docs/CALCULATION_PRINCIPLES.md.

Example:
    python m2_core.py sample_data/synthetic_single_angle.csv

Generated files:
    caustiq_output/caustiq_results.csv
    caustiq_output/caustiq_fit.png
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit


# ==========================================================
# 1. User parameters
# ==========================================================

CSV_FILE = Path(__file__).resolve().parent / "sample_data" / "synthetic_single_angle.csv"

# Vacuum wavelength, micrometres.
LASER_WAVELENGTH_UM = 1.030

# Illustrative optical settings only. Replace these with measured, calibrated
# values for a real setup. The simple dispersion correction is not for achromats.
F_CAL_MM = 400.0
BACK_FOCAL_LENGTH_CAL_MM = 397.7
CENTER_THICKNESS_MM = 3.4
CALIBRATION_WAVELENGTH_NM = 632.8
N_CAL = 1.51509
N_LAMBDA = 1.50709

# No real laser aperture-to-lens distance is assumed for public examples.
LASER_LOCATION_MM: float | None = None

# Illustrative automatic datum correction: -Zlens + ZCT. Enter measured
# values from the actual setup; do not tune them to force a desired result.
Z_LENS_MM = 100.0
Z_CT_MM = CENTER_THICKNESS_MM
Z01_REFERENCE_CORRECTION_MM = -Z_LENS_MM + Z_CT_MM

# Use the measured width at a sufficiently close focal-length sample.
# If no sample is this close to f_lambda, this program automatically evaluates
# the fitted curve at f_lambda and labels the result "Fitted".
FOCAL_SAMPLE_TOLERANCE_MM = 0.05
FORCE_FITTED_DIVERGENCE = False

SHOW_PLOT = True


X_COLUMN = "Beam Widths X - D4 Sigma(μm)"
Y_COLUMN = "Beam Widths Y - D4 Sigma(μm)"
Z_COLUMN = "Z Locations(mm)"


# ==========================================================
# 2. Result containers
# ==========================================================

@dataclass(frozen=True)
class FitResult:
    axis: str
    waist_width_um: float
    waist_location_mm: float
    divergence_full_mrad: float
    m2: float
    bpp_mm_mrad: float
    rayleigh_length_mm: float
    rmse_um: float
    r_squared: float


@dataclass(frozen=True)
class RealBeamResult:
    axis: str
    wf2_um: float
    focal_sample_z_mm: float | None
    focal_sample_delta_mm: float | None
    divergence_source: str
    divergence_full_mrad: float
    waist_width_um: float
    waist_location_principal_mm: float
    waist_location_display_mm: float
    rayleigh_length_mm: float
    m2: float
    bpp_mm_mrad: float


@dataclass(frozen=True)
class IsoSamplingCheck:
    axis: str
    total: int
    near_count: int
    far_left_count: int
    far_right_count: int

    @property
    def far_total(self) -> int:
        return self.far_left_count + self.far_right_count

    @property
    def iso_basic_pass(self) -> bool:
        # Guide: >=11 total, >=5 within +/-ZR, >=5 beyond +/-2ZR.
        return self.total >= 11 and self.near_count >= 5 and self.far_total >= 5

    @property
    def balanced_recommendation_pass(self) -> bool:
        # Additional balance diagnostic: >=3 far-side samples on each side.
        return self.far_left_count >= 3 and self.far_right_count >= 3


@dataclass(frozen=True)
class AnalysisConfig:
    """User-editable settings required for one beam analysis."""

    wavelength_um: float = LASER_WAVELENGTH_UM
    f_cal_mm: float = F_CAL_MM
    bfl_cal_mm: float = BACK_FOCAL_LENGTH_CAL_MM
    center_thickness_mm: float = CENTER_THICKNESS_MM
    calibration_wavelength_nm: float = CALIBRATION_WAVELENGTH_NM
    n_cal: float = N_CAL
    n_lambda: float = N_LAMBDA
    z_lens_mm: float = Z_LENS_MM
    laser_location_mm: float | None = LASER_LOCATION_MM
    focal_sample_tolerance_mm: float = FOCAL_SAMPLE_TOLERANCE_MM
    force_fitted_divergence: bool = FORCE_FITTED_DIVERGENCE
    automatic_mode: bool = True

    @property
    def z01_reference_correction_mm(self) -> float:
        if not self.automatic_mode:
            return 0.0
        return -self.z_lens_mm + self.center_thickness_mm

    def validate(self) -> None:
        positive = {
            "wavelength_um": self.wavelength_um,
            "f_cal_mm": self.f_cal_mm,
            "bfl_cal_mm": self.bfl_cal_mm,
            "center_thickness_mm": self.center_thickness_mm,
            "calibration_wavelength_nm": self.calibration_wavelength_nm,
            "n_cal": self.n_cal,
            "n_lambda": self.n_lambda,
            "z_lens_mm": self.z_lens_mm,
            "focal_sample_tolerance_mm": self.focal_sample_tolerance_mm,
        }
        bad = [name for name, value in positive.items() if not np.isfinite(value) or value <= 0.0]
        if bad:
            raise ValueError("Configuration values must be positive and finite: " + ", ".join(bad))
        if self.n_cal == 1.0 or self.n_lambda == 1.0:
            raise ValueError("Lens refractive indices must not equal 1.")
        if self.laser_location_mm is not None and not np.isfinite(self.laser_location_mm):
            raise ValueError("laser_location_mm must be finite or None.")


@dataclass(frozen=True)
class AnalysisResult:
    """Reusable calculation result consumed by both CLI and GUI front ends."""

    csv_file: Path
    config: AnalysisConfig
    z_mm: np.ndarray
    wx_um: np.ndarray
    wy_um: np.ndarray
    focal_length_mm: float
    fit_x: FitResult
    fit_y: FitResult
    real_x: RealBeamResult
    real_y: RealBeamResult
    iso_x: IsoSamplingCheck
    iso_y: IsoSamplingCheck

    @property
    def astigmatism_after_lens(self) -> float:
        return astigmatism(
            self.fit_x.waist_location_mm,
            self.fit_y.waist_location_mm,
            self.fit_x.rayleigh_length_mm,
            self.fit_y.rayleigh_length_mm,
        )

    @property
    def asymmetry_after_lens(self) -> float:
        return asymmetry(self.fit_x.waist_width_um, self.fit_y.waist_width_um)

    @property
    def astigmatism_real(self) -> float:
        return astigmatism(
            self.real_x.waist_location_display_mm,
            self.real_y.waist_location_display_mm,
            self.real_x.rayleigh_length_mm,
            self.real_y.rayleigh_length_mm,
        )

    @property
    def asymmetry_real(self) -> float:
        return asymmetry(self.real_x.waist_width_um, self.real_y.waist_width_um)


# ==========================================================
# 3. Input and validation
# ==========================================================

def read_measurements(csv_file: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read, normalize, validate, and sort beam-width CSV measurements."""
    try:
        frame = pd.read_csv(csv_file, encoding="utf-8-sig")
    except Exception as exc:
        raise RuntimeError(f"Error reading CSV file {csv_file}: {exc}") from exc

    frame.columns = [str(column).strip() for column in frame.columns]
    required = [X_COLUMN, Y_COLUMN, Z_COLUMN]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(
            "Missing required CSV column(s): "
            + ", ".join(missing)
            + f". Found: {list(frame.columns)}"
        )

    numeric = frame[required].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        bad_rows = (numeric.isna().any(axis=1)).to_numpy().nonzero()[0] + 2
        raise ValueError(f"Non-numeric or missing measurement(s) on CSV row(s): {bad_rows.tolist()}")

    wx = numeric[X_COLUMN].to_numpy(dtype=float)
    wy = numeric[Y_COLUMN].to_numpy(dtype=float)
    z = numeric[Z_COLUMN].to_numpy(dtype=float)

    if len(z) < 3 or np.unique(z).size < 3:
        raise ValueError("At least three distinct Z locations are required for the three-parameter fit.")
    if np.any(wx <= 0.0) or np.any(wy <= 0.0):
        raise ValueError("All D4-sigma beam widths must be positive.")
    if not np.all(np.isfinite(np.column_stack((z, wx, wy)))):
        raise ValueError("CSV contains an infinite measurement.")

    order = np.argsort(z)
    return z[order], wx[order], wy[order]


# ==========================================================
# 4. Hyperbolic propagation fit (Guide Equation 4)
#
# W(z)^2 = W02^2 + Theta2^2 * (z - Z02)^2
#
# With W in um and z in mm, the fitted numerical slope is in
# um/mm, which is numerically equal to mrad.
# ==========================================================

def beam_width_model(
    z_mm: np.ndarray | float,
    waist_width_um: float,
    divergence_full_mrad: float,
    waist_location_mm: float,
) -> np.ndarray:
    z_array = np.asarray(z_mm, dtype=float)
    return np.sqrt(
        waist_width_um**2
        + divergence_full_mrad**2 * (z_array - waist_location_mm) ** 2
    )


def _initial_guess(z_mm: np.ndarray, width_um: np.ndarray) -> tuple[float, float, float]:
    waist_index = int(np.argmin(width_um))
    waist_guess = float(width_um[waist_index])
    location_guess = float(z_mm[waist_index])

    distances = np.abs(z_mm - location_guess)
    usable = distances > 0.0
    slope_candidates = (width_um[usable] - waist_guess) / distances[usable]
    divergence_guess = float(max(np.max(slope_candidates), 1e-6))
    return waist_guess, divergence_guess, location_guess


def fit_beam(
    axis: str,
    z_mm: np.ndarray,
    width_um: np.ndarray,
    wavelength_um: float,
) -> FitResult:
    p0 = _initial_guess(z_mm, width_um)
    popt, _ = curve_fit(
        beam_width_model,
        z_mm,
        width_um,
        p0=p0,
        maxfev=100_000,
    )

    waist_width_um = abs(float(popt[0]))
    divergence_full_mrad = abs(float(popt[1]))
    waist_location_mm = float(popt[2])
    if waist_width_um == 0.0 or divergence_full_mrad == 0.0:
        raise RuntimeError(f"Degenerate {axis}-axis fit: zero waist width or divergence.")

    predicted = beam_width_model(
        z_mm, waist_width_um, divergence_full_mrad, waist_location_mm
    )
    residuals = width_um - predicted
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((width_um - np.mean(width_um)) ** 2))

    # Equation 5.  Convert the full divergence from mrad to rad.
    m2 = (
        np.pi
        * waist_width_um
        * (divergence_full_mrad / 1000.0)
        / (4.0 * wavelength_um)
    )

    # Equation 6.  Numerically, um == mm*mrad.
    bpp_mm_mrad = m2 * wavelength_um / np.pi

    # Equation 11.  um/mrad is numerically mm.
    rayleigh_length_mm = waist_width_um / divergence_full_mrad

    return FitResult(
        axis=axis,
        waist_width_um=waist_width_um,
        waist_location_mm=waist_location_mm,
        divergence_full_mrad=divergence_full_mrad,
        m2=float(m2),
        bpp_mm_mrad=float(bpp_mm_mrad),
        rayleigh_length_mm=float(rayleigh_length_mm),
        rmse_um=float(np.sqrt(np.mean(residuals**2))),
        r_squared=float(1.0 - ss_res / ss_tot) if ss_tot > 0.0 else float("nan"),
    )


# ==========================================================
# 5. Lens correction and translation to the real beam
# ==========================================================

def effective_focal_length_mm(
    f_cal_mm: float,
    n_cal: float,
    n_lambda: float,
) -> float:
    """Guide Equation 15; valid for a plano-convex lens, not an achromat."""
    if n_lambda == 1.0:
        raise ValueError("n_lambda must not equal 1.")
    return f_cal_mm * (n_cal - 1.0) / (n_lambda - 1.0)


def focal_plane_width(
    z_mm: np.ndarray,
    width_um: np.ndarray,
    fit: FitResult,
    focal_length_mm: float,
    tolerance_mm: float,
    force_fitted: bool,
) -> tuple[float, float | None, float | None, str]:
    """Return Wf2 using the actual focal sample, or the fit when unavailable."""
    index = int(np.argmin(np.abs(z_mm - focal_length_mm)))
    sample_z_mm = float(z_mm[index])
    delta_mm = sample_z_mm - focal_length_mm

    if not force_fitted and abs(delta_mm) <= tolerance_mm:
        return float(width_um[index]), sample_z_mm, delta_mm, "Actual focal sample"

    fitted_width_um = float(
        beam_width_model(
            focal_length_mm,
            fit.waist_width_um,
            fit.divergence_full_mrad,
            fit.waist_location_mm,
        )
    )
    return fitted_width_um, sample_z_mm, delta_mm, "Fitted at focal length"


def translate_to_real_beam(
    fit: FitResult,
    z_mm: np.ndarray,
    width_um: np.ndarray,
    focal_length_mm: float,
    wavelength_um: float,
    laser_location_mm: float | None,
    z01_reference_correction_mm: float,
    focal_sample_tolerance_mm: float,
    force_fitted_divergence: bool,
) -> RealBeamResult:
    wf2_um, sample_z_mm, delta_mm, source = focal_plane_width(
        z_mm=z_mm,
        width_um=width_um,
        fit=fit,
        focal_length_mm=focal_length_mm,
        tolerance_mm=focal_sample_tolerance_mm,
        force_fitted=force_fitted_divergence,
    )

    # Equation 7.  um/mm is numerically mrad.
    divergence_full_mrad = wf2_um / focal_length_mm

    # Equation 8.  The factor 1000 converts the mrad denominator to rad.
    waist_width_um = (
        4.0
        * wavelength_um
        * fit.m2
        / (np.pi * divergence_full_mrad)
        * 1000.0
    )

    # Equation 12.  um/mrad is numerically mm.
    rayleigh_length_mm = waist_width_um / divergence_full_mrad

    # Equation 9, before any automatic-system datum correction.
    z02_minus_f = fit.waist_location_mm - focal_length_mm
    waist_location_principal_mm = (
        focal_length_mm**2
        * z02_minus_f
        / (fit.rayleigh_length_mm**2 + z02_minus_f**2)
        + focal_length_mm
    )

    waist_location_reference_mm = (
        waist_location_principal_mm + z01_reference_correction_mm
    )
    if laser_location_mm is None:
        waist_location_display_mm = waist_location_reference_mm
    else:
        # Equation 10.  Positive is in front of the laser aperture; negative is
        # behind it (a virtual/inaccessible waist).
        waist_location_display_mm = laser_location_mm - waist_location_reference_mm

    return RealBeamResult(
        axis=fit.axis,
        wf2_um=float(wf2_um),
        focal_sample_z_mm=sample_z_mm,
        focal_sample_delta_mm=delta_mm,
        divergence_source=source,
        divergence_full_mrad=float(divergence_full_mrad),
        waist_width_um=float(waist_width_um),
        waist_location_principal_mm=float(waist_location_principal_mm),
        waist_location_display_mm=float(waist_location_display_mm),
        rayleigh_length_mm=float(rayleigh_length_mm),
        m2=fit.m2,
        bpp_mm_mrad=fit.bpp_mm_mrad,
    )


# ==========================================================
# 6. Ancillary results and ISO sampling checks
# ==========================================================

def astigmatism(
    waist_x_mm: float,
    waist_y_mm: float,
    rayleigh_x_mm: float,
    rayleigh_y_mm: float,
) -> float:
    """Guide Equation 13 (unsigned Ophir astigmatism)."""
    return abs(waist_x_mm - waist_y_mm) / ((rayleigh_x_mm + rayleigh_y_mm) / 2.0)


def asymmetry(width_x_um: float, width_y_um: float) -> float:
    """Guide Equation 14."""
    return max(width_x_um / width_y_um, width_y_um / width_x_um)


def check_iso_sampling(z_mm: np.ndarray, fit: FitResult) -> IsoSamplingCheck:
    offset = z_mm - fit.waist_location_mm
    zr = fit.rayleigh_length_mm
    return IsoSamplingCheck(
        axis=fit.axis,
        total=int(z_mm.size),
        near_count=int(np.count_nonzero(np.abs(offset) <= zr)),
        far_left_count=int(np.count_nonzero(offset <= -2.0 * zr)),
        far_right_count=int(np.count_nonzero(offset >= 2.0 * zr)),
    )


def analyze_file(csv_file: Path, config: AnalysisConfig | None = None) -> AnalysisResult:
    """Run the complete computation without printing, plotting, or exporting."""
    csv_path = Path(csv_file).expanduser().resolve()
    z_mm, wx_um, wy_um = read_measurements(csv_path)
    return analyze_arrays(
        z_mm,
        wx_um,
        wy_um,
        config=config,
        source_label=str(csv_path),
    )


def analyze_arrays(
    z_mm: np.ndarray,
    wx_um: np.ndarray,
    wy_um: np.ndarray,
    config: AnalysisConfig | None = None,
    source_label: str = "in-memory angle data",
) -> AnalysisResult:
    """Run the complete M2 computation for validated in-memory arrays.

    This is the bridge used by the unified GUI: image second moments are
    converted to X/Y D4-sigma arrays at the selected relative angle and then
    fitted without creating a temporary CSV file.
    """
    active_config = config if config is not None else AnalysisConfig()
    active_config.validate()

    z_array = np.asarray(z_mm, dtype=float).reshape(-1)
    x_array = np.asarray(wx_um, dtype=float).reshape(-1)
    y_array = np.asarray(wy_um, dtype=float).reshape(-1)
    if not (z_array.size == x_array.size == y_array.size):
        raise ValueError("Z, X, and Y arrays must have the same length.")
    if z_array.size < 3 or np.unique(z_array).size < 3:
        raise ValueError("At least three distinct Z locations are required for M2 fitting.")
    if not np.all(np.isfinite(np.column_stack((z_array, x_array, y_array)))):
        raise ValueError("Z or D4-sigma arrays contain NaN or infinity.")
    if np.any(x_array <= 0.0) or np.any(y_array <= 0.0):
        raise ValueError("All D4-sigma beam widths must be positive.")

    order = np.argsort(z_array)
    z_mm = z_array[order]
    wx_um = x_array[order]
    wy_um = y_array[order]
    focal_length_mm = effective_focal_length_mm(
        active_config.f_cal_mm,
        active_config.n_cal,
        active_config.n_lambda,
    )
    fit_x = fit_beam("X", z_mm, wx_um, active_config.wavelength_um)
    fit_y = fit_beam("Y", z_mm, wy_um, active_config.wavelength_um)
    real_x = translate_to_real_beam(
        fit_x,
        z_mm,
        wx_um,
        focal_length_mm,
        active_config.wavelength_um,
        active_config.laser_location_mm,
        active_config.z01_reference_correction_mm,
        active_config.focal_sample_tolerance_mm,
        active_config.force_fitted_divergence,
    )
    real_y = translate_to_real_beam(
        fit_y,
        z_mm,
        wy_um,
        focal_length_mm,
        active_config.wavelength_um,
        active_config.laser_location_mm,
        active_config.z01_reference_correction_mm,
        active_config.focal_sample_tolerance_mm,
        active_config.force_fitted_divergence,
    )
    return AnalysisResult(
        csv_file=Path(source_label),
        config=active_config,
        z_mm=z_mm,
        wx_um=wx_um,
        wy_um=wy_um,
        focal_length_mm=focal_length_mm,
        fit_x=fit_x,
        fit_y=fit_y,
        real_x=real_x,
        real_y=real_y,
        iso_x=check_iso_sampling(z_mm, fit_x),
        iso_y=check_iso_sampling(z_mm, fit_y),
    )


# ==========================================================
# 7. Reporting and export
# ==========================================================

def _print_axis_fit(fit: FitResult) -> None:
    print(f"--- {fit.axis} Direction: After Lens (subscript 2) ---")
    print(f"Waist Width W02          : {fit.waist_width_um:12.3f} um")
    print(f"Waist Location Z02       : {fit.waist_location_mm:12.3f} mm")
    print(f"Divergence Theta2 (full) : {fit.divergence_full_mrad:12.3f} mrad")
    print(f"M^2                       : {fit.m2:12.3f}")
    print(f"BPP                       : {fit.bpp_mm_mrad:12.3f} mm*mrad")
    print(f"Rayleigh Length ZR2       : {fit.rayleigh_length_mm:12.3f} mm")
    print(f"Fit RMSE                  : {fit.rmse_um:12.4f} um")
    print(f"Fit R^2                   : {fit.r_squared:12.8f}")
    print()


def _print_real_axis(real: RealBeamResult, laser_location_mm: float | None) -> None:
    print(f"--- {real.axis} Direction: Real Laser (subscript 1) ---")
    print(f"Wf2                       : {real.wf2_um:12.3f} um")
    if real.focal_sample_z_mm is not None and real.focal_sample_delta_mm is not None:
        print(f"Nearest focal sample Z    : {real.focal_sample_z_mm:12.3f} mm")
        print(f"Sample minus f_lambda     : {real.focal_sample_delta_mm:12.6f} mm")
    print(f"Divergence source         : {real.divergence_source}")
    print(f"Divergence Theta1 (full) : {real.divergence_full_mrad:12.3f} mrad")
    print(f"Waist Width W01           : {real.waist_width_um:12.3f} um")
    if laser_location_mm is None:
        location_label = "Waist Location Z01 (lens)"
    else:
        location_label = "Waist Location (laser ref)"
    print(f"{location_label:27s}: {real.waist_location_display_mm:12.3f} mm")
    print(f"Rayleigh Length ZR1       : {real.rayleigh_length_mm:12.3f} mm")
    print(f"M^2                       : {real.m2:12.3f}")
    print(f"BPP                       : {real.bpp_mm_mrad:12.3f} mm*mrad")
    print()


def print_report(
    csv_file: Path,
    z_mm: np.ndarray,
    wx_um: np.ndarray,
    wy_um: np.ndarray,
    focal_length_mm: float,
    fit_x: FitResult,
    fit_y: FitResult,
    real_x: RealBeamResult,
    real_y: RealBeamResult,
    iso_checks: Iterable[IsoSamplingCheck],
    laser_location_mm: float | None,
) -> None:
    ast2 = astigmatism(
        fit_x.waist_location_mm,
        fit_y.waist_location_mm,
        fit_x.rayleigh_length_mm,
        fit_y.rayleigh_length_mm,
    )
    asy2 = asymmetry(fit_x.waist_width_um, fit_y.waist_width_um)
    ast1 = astigmatism(
        real_x.waist_location_display_mm,
        real_y.waist_location_display_mm,
        real_x.rayleigh_length_mm,
        real_y.rayleigh_length_mm,
    )
    asy1 = asymmetry(real_x.waist_width_um, real_y.waist_width_um)

    print("=" * 76)
    print("Caustiq M2 Result - D4-sigma / Full-angle convention")
    print("=" * 76)
    print(f"CSV                       : {csv_file}")
    print(f"Samples                   : {z_mm.size}")
    print(f"Z range                   : {z_mm.min():.3f} to {z_mm.max():.3f} mm")
    print(f"Wavelength                : {LASER_WAVELENGTH_UM:.6f} um")
    print(f"Effective focal length    : {focal_length_mm:.6f} mm")
    print(f"Lens EFL @ calibration    : {F_CAL_MM:.3f} mm @ {CALIBRATION_WAVELENGTH_NM:.1f} nm")
    print(f"Lens BFL @ calibration    : {BACK_FOCAL_LENGTH_CAL_MM:.3f} mm")
    print(f"Lens center thickness     : {CENTER_THICKNESS_MM:.3f} mm")
    if laser_location_mm is not None:
        print(f"Laser location            : {laser_location_mm:.3f} mm")
        print(f"Z Lens                    : {Z_LENS_MM:.3f} mm")
        print(f"Z_CT (center thickness)   : {Z_CT_MM:.3f} mm")
        print(f"Equation 9 correction     : {Z01_REFERENCE_CORRECTION_MM:.3f} mm")
    print()

    _print_axis_fit(fit_x)
    _print_axis_fit(fit_y)
    print(f"After-lens Astigmatism    : {ast2:12.3f}")
    print(f"After-lens Asymmetry      : {asy2:12.3f}")
    print("-" * 76)
    print()

    _print_real_axis(real_x, laser_location_mm)
    _print_real_axis(real_y, laser_location_mm)
    print(f"Real-beam Astigmatism     : {ast1:12.3f}")
    print(f"Real-beam Asymmetry       : {asy1:12.3f}")
    print("-" * 76)

    # Match the screenshot's current-frame quantities (the final CSV row after
    # sorting by Z).
    print("Current frame (largest Z in CSV)")
    print(f"D4sigma X                 : {wx_um[-1]:12.3f} um")
    print(f"D4sigma Y                 : {wy_um[-1]:12.3f} um")
    print(f"Z Location                : {z_mm[-1]:12.3f} mm")
    print("-" * 76)

    print("ISO sampling distribution")
    for check in iso_checks:
        basic = "PASS" if check.iso_basic_pass else "FAIL"
        balanced = "PASS" if check.balanced_recommendation_pass else "WARN"
        print(
            f"{check.axis}: total={check.total}, near={check.near_count}, "
            f"far-left={check.far_left_count}, far-right={check.far_right_count}, "
            f"basic={basic}, balanced-far-field={balanced}"
        )
    print("=" * 76)


def results_table(
    fit_x: FitResult,
    fit_y: FitResult,
    real_x: RealBeamResult,
    real_y: RealBeamResult,
) -> pd.DataFrame:
    rows: list[dict[str, str | float]] = []

    def add(space: str, axis: str, parameter: str, value: float, unit: str = "") -> None:
        rows.append(
            {
                "Space": space,
                "Axis": axis,
                "Parameter": parameter,
                "Value": value,
                "Unit": unit,
            }
        )

    for fit in (fit_x, fit_y):
        add("After Lens", fit.axis, "Divergence (full)", fit.divergence_full_mrad, "mrad")
        add("After Lens", fit.axis, "Waist Width", fit.waist_width_um, "um")
        add("After Lens", fit.axis, "Waist Location", fit.waist_location_mm, "mm")
        add("After Lens", fit.axis, "Rayleigh Length", fit.rayleigh_length_mm, "mm")
        add("After Lens", fit.axis, "M2", fit.m2)
        add("After Lens", fit.axis, "BPP", fit.bpp_mm_mrad, "mm*mrad")
        add("After Lens", fit.axis, "Fit RMSE", fit.rmse_um, "um")
        add("After Lens", fit.axis, "Fit R-squared", fit.r_squared)

    add(
        "After Lens",
        "XY",
        "Astigmatism",
        astigmatism(
            fit_x.waist_location_mm,
            fit_y.waist_location_mm,
            fit_x.rayleigh_length_mm,
            fit_y.rayleigh_length_mm,
        ),
    )
    add("After Lens", "XY", "Asymmetry", asymmetry(fit_x.waist_width_um, fit_y.waist_width_um))

    for real in (real_x, real_y):
        add("Real Laser", real.axis, "Divergence (full)", real.divergence_full_mrad, "mrad")
        add("Real Laser", real.axis, "Waist Width", real.waist_width_um, "um")
        add("Real Laser", real.axis, "Waist Location", real.waist_location_display_mm, "mm")
        add("Real Laser", real.axis, "Rayleigh Length", real.rayleigh_length_mm, "mm")
        add("Real Laser", real.axis, "M2", real.m2)
        add("Real Laser", real.axis, "BPP", real.bpp_mm_mrad, "mm*mrad")
        add("Real Laser", real.axis, "Wf2", real.wf2_um, "um")

    add(
        "Real Laser",
        "XY",
        "Astigmatism",
        astigmatism(
            real_x.waist_location_display_mm,
            real_y.waist_location_display_mm,
            real_x.rayleigh_length_mm,
            real_y.rayleigh_length_mm,
        ),
    )
    add("Real Laser", "XY", "Asymmetry", asymmetry(real_x.waist_width_um, real_y.waist_width_um))
    return pd.DataFrame(rows)


def save_plot(
    output_file: Path,
    z_mm: np.ndarray,
    wx_um: np.ndarray,
    wy_um: np.ndarray,
    fit_x: FitResult,
    fit_y: FitResult,
    focal_length_mm: float,
    show_plot: bool,
) -> None:
    z_fine = np.linspace(float(z_mm.min()), float(z_mm.max()), 600)
    fitted_x = beam_width_model(
        z_fine,
        fit_x.waist_width_um,
        fit_x.divergence_full_mrad,
        fit_x.waist_location_mm,
    )
    fitted_y = beam_width_model(
        z_fine,
        fit_y.waist_width_um,
        fit_y.divergence_full_mrad,
        fit_y.waist_location_mm,
    )
    residual_x = wx_um - beam_width_model(
        z_mm,
        fit_x.waist_width_um,
        fit_x.divergence_full_mrad,
        fit_x.waist_location_mm,
    )
    residual_y = wy_um - beam_width_model(
        z_mm,
        fit_y.waist_width_um,
        fit_y.divergence_full_mrad,
        fit_y.waist_location_mm,
    )

    figure, (axis_main, axis_residual) = plt.subplots(
        2,
        1,
        figsize=(10, 7.5),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
        constrained_layout=True,
    )
    axis_main.scatter(z_mm, wx_um, color="#d62728", marker="o", label="X data")
    axis_main.plot(z_fine, fitted_x, color="#d62728", label="X fit")
    axis_main.scatter(z_mm, wy_um, color="#1f77b4", marker="s", label="Y data")
    axis_main.plot(z_fine, fitted_y, color="#1f77b4", label="Y fit")
    axis_main.axvline(focal_length_mm, color="#555555", linestyle=":", label="f_lambda")
    axis_main.axvline(fit_x.waist_location_mm, color="#d62728", linestyle="--", alpha=0.55)
    axis_main.axvline(fit_y.waist_location_mm, color="#1f77b4", linestyle="--", alpha=0.55)
    axis_main.set_ylabel("D4-sigma beam width (um)")
    axis_main.set_title("Hyperbolic beam fit - full-angle convention")
    axis_main.grid(True, alpha=0.3)
    axis_main.legend(ncol=3)

    axis_residual.axhline(0.0, color="#555555", linewidth=1)
    axis_residual.scatter(z_mm, residual_x, color="#d62728", marker="o", label="X residual")
    axis_residual.scatter(z_mm, residual_y, color="#1f77b4", marker="s", label="Y residual")
    axis_residual.set_xlabel("Z position (mm)")
    axis_residual.set_ylabel("Residual (um)")
    axis_residual.grid(True, alpha=0.3)
    axis_residual.legend(ncol=2)

    figure.savefig(output_file, dpi=180)
    if show_plot:
        plt.show()
    plt.close(figure)


# ==========================================================
# 8. Main program
# ==========================================================

def run_analysis(
    csv_file: Path,
    output_dir: Path,
    show_plot: bool,
    force_fitted_divergence: bool,
) -> tuple[Path, Path]:
    config = AnalysisConfig(force_fitted_divergence=force_fitted_divergence)
    analysis = analyze_file(csv_file, config)
    print_report(
        analysis.csv_file,
        analysis.z_mm,
        analysis.wx_um,
        analysis.wy_um,
        analysis.focal_length_mm,
        analysis.fit_x,
        analysis.fit_y,
        analysis.real_x,
        analysis.real_y,
        [analysis.iso_x, analysis.iso_y],
        config.laser_location_mm,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    results_file = output_dir / "caustiq_results.csv"
    plot_file = output_dir / "caustiq_fit.png"
    results_table(
        analysis.fit_x,
        analysis.fit_y,
        analysis.real_x,
        analysis.real_y,
    ).to_csv(
        results_file, index=False, encoding="utf-8-sig"
    )
    save_plot(
        plot_file,
        analysis.z_mm,
        analysis.wx_um,
        analysis.wy_um,
        analysis.fit_x,
        analysis.fit_y,
        analysis.focal_length_mm,
        show_plot,
    )
    print(f"Results CSV saved          : {results_file}")
    print(f"Fit plot saved             : {plot_file}")
    return results_file, plot_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit D4-sigma caustic data and estimate real-beam parameters."
    )
    parser.add_argument(
        "csv_file",
        nargs="?",
        type=Path,
        default=CSV_FILE,
        help=f"Input CSV path (default: {CSV_FILE})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: <CSV directory>/caustiq_output)",
    )
    parser.add_argument(
        "--fitted-divergence",
        action="store_true",
        default=FORCE_FITTED_DIVERGENCE,
        help="Use the fitted width at f_lambda even when an actual focal sample exists.",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Save the plot without opening an interactive plot window.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_file = args.csv_file.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else csv_file.parent / "caustiq_output"
    )
    run_analysis(
        csv_file=csv_file,
        output_dir=output_dir,
        show_plot=SHOW_PLOT and not args.no_show,
        force_fitted_divergence=args.fitted_divergence,
    )


if __name__ == "__main__":
    main()

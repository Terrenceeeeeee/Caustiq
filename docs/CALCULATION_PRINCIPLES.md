# Calculation principles

Caustiq 2.4.2 reports a beam model fitted to measured values. It does not directly certify a physical beam or ISO 11146 compliance.

## Image measurements

For an image intensity distribution after background treatment, the intensity-weighted centroid is `(x̄, ȳ)`. Along a chosen axis `u`, the second central moment is `σᵤ² = Σ[Iᵢ (uᵢ − ū)²] / ΣIᵢ`. The reported D4σ beam width is `4σᵤ` in micrometres after applying the pixel calibration and magnification. The 181-angle scan rotates the X/Y projection axes from −90° through +90° in 1° steps and repeats the second-moment calculation. Saturation, clipping, background error, stray light, and inaccurate pixel calibration can bias the widths.

## Propagation fit and M²

For each axis, the measured D4σ widths `W(z)` are fitted to

`W(z)² = W₀² + Θ²(z − z₀)²`,

where `W₀` is the fitted waist width in µm, `z₀` its axial position in mm, and `Θ` the fitted **full-angle** divergence in mrad (numerically µm/mm). With wavelength `λ` in µm, the code uses

`M² = π W₀ Θ / (4000 λ)`, `BPP = M² λ / π` (mm·mrad), and `zR = W₀ / Θ` (mm).

The factor 4000 accounts for the full-angle and unit conventions. Results depend on the model and measurements; a good residual fit by itself does not establish calibration accuracy.

## Lens and coordinate reconstruction

The application optionally converts a fitted post-lens caustic to an estimated pre-lens beam using a calibrated effective focal length, focal-plane width, and a thin-lens-style model. It applies user-provided reference offsets and laser-aperture distance when available. A nominal example lens value in the source is **not** a substitute for the actual lens's calibration. Inspect the selected Z datum and the real-beam reconstruction mode before interpreting pre-lens values.

## Astigmatism and asymmetry

These are **ancillary values, not ISO-procedure metrics**. The application's unsigned astigmatism figure is `|z₀X − z₀Y| / [(zRX + zRY)/2]`; asymmetry is `max(W₀X/W₀Y, W₀Y/W₀X)`. The astigmatism convention follows an Ophir-Spiricon-defined figure of merit; other conventions may produce different numbers. Interpret it approximately as follows:

- Near 0: little on-axis astigmatism is indicated.
- Between 0 and 1: increasing astigmatism, while each waist remains within the other's Rayleigh range.
- Between 1 and 2: larger separation; the two Rayleigh ranges still overlap.
- Above 2: substantial separation with no Rayleigh-range overlap.

The boundaries are descriptive of this convention, not an independent diagnosis. The exact geometric statements can depend on unequal Rayleigh ranges; check the two waist positions and ranges directly when that distinction matters.

## Validation limits

The automated tests use synthetic exact-model widths and a small generated image sequence. They check software behavior, not an uncertainty budget, equipment traceability, all ISO 11146 sampling requirements, or safety. For scientific use, retain raw data, calibration records, fit residuals, and an independent cross-check.

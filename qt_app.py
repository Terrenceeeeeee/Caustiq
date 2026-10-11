#!/usr/bin/env python3
"""Apple-inspired, cross-platform PySide6 front end for Caustiq.

The numerical and input layers intentionally remain in the existing core
modules. This file owns presentation, window management, and user interaction.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.colors import LinearSegmentedColormap, PowerNorm
from matplotlib.figure import Figure
from matplotlib.projections import register_projection
from mpl_toolkits.mplot3d import Axes3D

from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStatusBar,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from angle_dataset import (
    ANGLE_MAX_DEG,
    ANGLE_MIN_DEG,
    AngleDataset,
    AngleSeries,
    analyze_image_set,
    load_csv_dataset,
)
from image_beam_core import AnalysisSettings
from image_io import ImageSet, SUPPORTED_SUFFIXES
from measurement_config import MeasurementConfig
import m2_core
from ui_theme import LIGHT, THEMES, style_sheet


APP_NAME = "Caustiq"
APP_VERSION = "2.4.2"
APP_SUBTITLE = "Beam Profiling & M² Analysis"


def configure_application_icon(app: QApplication) -> None:
    """Use one identity for application windows and Windows taskbar grouping."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Caustiq.Desktop")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("assets") / "caustiq.ico")))


LASER_FORMULAS: dict[str, dict[str, object]] = {
    "M²": {
        "formula": "M²<sub>a</sub> = π W<sub>02,a</sub> Θ<sub>2,a</sub> / (4 λ)",
        "variables": (
            ("a", "Selected transverse axis: X or Y", "—"),
            ("W₀₂,a", "Fitted after-lens D4σ waist diameter", "µm"),
            ("Θ₂,a", "Fitted after-lens full-angle divergence", "rad"),
            ("λ", "Laser vacuum wavelength", "µm"),
        ),
        "note": "The displayed Laser M² is invariant through the ideal lens and is obtained from the after-lens hyperbolic fit. The fitted divergence is converted from mrad to rad in this equation.",
    },
    "BPP": {
        "formula": "BPP<sub>a</sub> = M²<sub>a</sub> λ / π",
        "variables": (
            ("a", "Selected transverse axis: X or Y", "—"),
            ("M²ₐ", "Beam propagation ratio for the selected axis", "—"),
            ("λ", "Laser vacuum wavelength", "µm"),
        ),
        "note": "Unit conversion is applied so the result is reported in mm·mrad.",
    },
    "Divergence": {
        "formula": "Θ<sub>1,a</sub> = W<sub>f2,a</sub> / f<sub>λ</sub>",
        "variables": (
            ("a", "Selected transverse axis: X or Y", "—"),
            ("Θ₁,a", "Reconstructed real-laser full-angle divergence", "mrad"),
            ("Wf₂,a", "D4σ beam diameter at the wavelength-corrected focal plane", "µm"),
            ("fλ", "Effective focal length at the laser wavelength", "mm"),
        ),
        "note": "Numerically, µm/mm equals mrad. Wf₂ uses the measured focal sample when it is within tolerance; otherwise the fitted curve is evaluated at fλ.",
    },
    "Waist Width": {
        "formula": "W<sub>01,a</sub> = 4 λ M²<sub>a</sub> / (π Θ<sub>1,a</sub>)",
        "variables": (
            ("a", "Selected transverse axis: X or Y", "—"),
            ("W₀₁,a", "Reconstructed real-laser D4σ waist diameter", "µm"),
            ("λ", "Laser vacuum wavelength", "µm"),
            ("M²ₐ", "Beam propagation ratio for the selected axis", "—"),
            ("Θ₁,a", "Reconstructed real-laser full-angle divergence", "rad"),
        ),
        "note": "The implementation converts Θ₁ from mrad to rad before evaluating the equation.",
    },
    "Waist Location": {
        "formula": (
            "Δ<sub>a</sub> = Z<sub>02,a</sub> − f<sub>λ</sub><br>"
            "Z<sub>01,p,a</sub> = f<sub>λ</sub>² Δ<sub>a</sub>² / "
            "(Z<sub>R2,a</sub>² + Δ<sub>a</sub>²) + f<sub>λ</sub><br>"
            "Z<sub>01,r,a</sub> = Z<sub>01,p,a</sub> − Z<sub>lens</sub> + CT<br>"
            "Z<sub>01,display,a</sub> = L<sub>laser</sub> − Z<sub>01,r,a</sub>"
        ),
        "variables": (
            ("Z₀₂,a", "Fitted after-lens waist location", "mm"),
            ("fλ", "Effective focal length at the laser wavelength", "mm"),
            ("ZR₂,a", "After-lens Rayleigh length", "mm"),
            ("Z₀₁,p,a", "Real waist location from the lens principal-plane model", "mm"),
            ("Zlens", "Configured lens reference position", "mm"),
            ("CT", "Lens center thickness", "mm"),
            ("Llaser", "Configured laser location", "mm"),
        ),
        "note": "Automatic mode applies −Zlens + CT. If Laser Location is disabled, the reference-coordinate value Z₀₁,r is displayed directly.",
    },
    "Rayleigh Length": {
        "formula": "Z<sub>R1,a</sub> = W<sub>01,a</sub> / Θ<sub>1,a</sub>",
        "variables": (
            ("a", "Selected transverse axis: X or Y", "—"),
            ("ZR₁,a", "Real-laser Rayleigh length", "mm"),
            ("W₀₁,a", "Real-laser D4σ waist diameter", "µm"),
            ("Θ₁,a", "Real-laser full-angle divergence", "mrad"),
        ),
        "note": "Numerically, µm/mrad equals mm for the full-width convention used by Caustiq.",
    },
    "Astigmatism": {
        "formula": "A = |Z<sub>01,X</sub> − Z<sub>01,Y</sub>| / ((Z<sub>R1,X</sub> + Z<sub>R1,Y</sub>) / 2)",
        "variables": (
            ("Z₀₁,X/Y", "Displayed real-laser waist location for each axis", "mm"),
            ("ZR₁,X/Y", "Real-laser Rayleigh length for each axis", "mm"),
            ("A", "Unsigned normalized separation of the two waist locations", "—"),
        ),
        "note": "A value of 0 means the X and Y waist locations coincide.",
    },
    "Asymmetry": {
        "formula": "S = max(W<sub>01,X</sub> / W<sub>01,Y</sub>, W<sub>01,Y</sub> / W<sub>01,X</sub>)",
        "variables": (
            ("W₀₁,X/Y", "Real-laser D4σ waist diameter for each axis", "µm"),
            ("S", "Ratio of the larger waist diameter to the smaller one", "—"),
        ),
        "note": "S is always at least 1. A value of 1 represents equal X and Y waist diameters.",
    },
}


class ResponsiveAxes3D(Axes3D):
    """Axes3D variant that coalesces high-frequency pointer motion."""

    name = "caustiq_3d"
    # Matplotlib's software 3D renderer has to re-project every surface for
    # every pointer event.  A 30 Hz ceiling keeps the UI responsive without
    # making rotation feel stepped on normal displays.
    interaction_interval_s = 1.0 / 30.0

    def __init__(self, *args, **kwargs) -> None:
        self._last_interaction_draw = 0.0
        self._pending_motion_event = None
        super().__init__(*args, **kwargs)

    def apply_aspect(self, position=None) -> None:
        """Use the complete panel instead of Matplotlib's centered square."""
        if position is None:
            position = self.get_position(original=True)
        self._set_position(position, "active")

    def get_proj(self):
        projection = super().get_proj()
        # A full-panel viewport is rectangular. Compensate its pixel aspect
        # so resizing the dock does not squash the transverse beam profiles.
        projection[1, :] *= self.bbox.width / max(self.bbox.height, 1.0)
        return projection

    def _button_press(self, event) -> None:
        self._last_interaction_draw = 0.0
        self._pending_motion_event = None
        super()._button_press(event)

    def _on_move(self, event) -> None:
        if self.button_pressed and event.inaxes is self:
            now = time.perf_counter()
            if now - self._last_interaction_draw < self.interaction_interval_s:
                self._pending_motion_event = event
                return
            self._last_interaction_draw = now
            self._pending_motion_event = None
        super()._on_move(event)

    def _button_release(self, event) -> None:
        pending = self._pending_motion_event
        self._pending_motion_event = None
        if pending is not None and self.button_pressed:
            super()._on_move(pending)
        super()._button_release(event)


register_projection(ResponsiveAxes3D)


class ProcessingCancelled(Exception):
    """Raised cooperatively between image-analysis items."""


OSI_RAINBOW = LinearSegmentedColormap.from_list(
    "OSI Rainbow",
    (
        (0.00, "#000000"),
        (0.06, "#310044"),
        (0.16, "#73007D"),
        (0.30, "#1D2EB8"),
        (0.45, "#00A7D8"),
        (0.58, "#00A13A"),
        (0.72, "#B8D400"),
        (0.84, "#FFE000"),
        (0.93, "#F04400"),
        (1.00, "#FFFFFF"),
    ),
    N=256,
)


def padded_data_limits(
    values: np.ndarray,
    padding_fraction: float = 0.04,
    minimum_padding: float = 0.5,
) -> tuple[float, float]:
    data = np.asarray(values, dtype=float)
    data = data[np.isfinite(data)]
    if data.size == 0:
        raise ValueError("Cannot calculate plot limits from empty/non-finite data.")
    low, high = float(np.min(data)), float(np.max(data))
    padding = max(padding_fraction * max(high - low, 1.0), minimum_padding)
    return low - padding, high + padding


class PlotPanel(QWidget):
    """Opaque Qt host with Matplotlib controls available from right-click."""

    def __init__(self, figure: Figure, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PlotSurface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.canvas = FigureCanvasQTAgg(figure)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.canvas.setAccessibleName("Interactive measurement plot")
        self.canvas.setToolTip("Right-click for plot controls")
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.canvas, 1)
        self.navigation = NavigationToolbar2QT(self.canvas, self)
        self.navigation.setIconSize(QSize(16, 16))
        self.navigation.hide()
        self.canvas.customContextMenuRequested.connect(self._show_plot_menu)

    def _show_plot_menu(self, position) -> None:
        menu = QMenu(self)
        for action in self.navigation.actions():
            if action.isSeparator():
                if menu.actions() and not menu.actions()[-1].isSeparator():
                    menu.addSeparator()
            elif action.text().strip():
                menu.addAction(action)
        while menu.actions() and menu.actions()[-1].isSeparator():
            menu.removeAction(menu.actions()[-1])
        if menu.actions():
            menu.exec(self.canvas.mapToGlobal(position))


class MeasurementPrincipleView(QWidget):
    """Animated rays on the project's independently drawn optical schematic."""

    stageChanged = Signal(int)

    def __init__(self, image_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PlotSurface")
        self.setMinimumSize(760, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.pixmap = QPixmap(str(image_path))
        self.progress = 0.0
        self._stage = -1
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._advance)

    def start(self) -> None:
        if not self.timer.isActive():
            self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def _advance(self) -> None:
        self.progress = (self.progress + 0.004) % 1.0
        stage = min(int(self.progress * 5.0), 4)
        if stage != self._stage:
            self._stage = stage
            self.stageChanged.emit(stage)
        self.update()

    @staticmethod
    def _path_position(progress: float, points: tuple[tuple[float, float], ...]) -> tuple[float, float]:
        position = progress * (len(points) - 1)
        index = min(int(position), len(points) - 2)
        fraction = position - index
        x0, y0 = points[index]; x1, y1 = points[index + 1]
        return x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self.pixmap.isNull():
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Measurement principle image is unavailable")
            return

        scaled = self.pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF(
            0.5 * (self.width() - scaled.width()),
            0.5 * (self.height() - scaled.height()),
            float(scaled.width()),
            float(scaled.height()),
        )
        painter.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))

        def mapped(normal_x: float, normal_y: float) -> QPointF:
            return QPointF(target.left() + normal_x * target.width(), target.top() + normal_y * target.height())

        unit = max(target.height() / 600.0, 0.5)
        # The central ray travels from the laser aperture through the lens
        # and focused waist to the physical detector surface. The raster's
        # envelope is explanatory; no third-party image patch is needed.
        beam_path = ((0.150, 0.500), (0.300, 0.500), (0.500, 0.500), (0.680, 0.500), (0.920, 0.500))
        pulse_x, pulse_y = self._path_position(self.progress, beam_path)
        progress_position = self.progress * (len(beam_path) - 1)
        completed_segments = int(progress_position)
        animated_ray = QPainterPath(mapped(*beam_path[0]))
        for index in range(1, completed_segments + 1):
            animated_ray.lineTo(mapped(*beam_path[index]))
        animated_ray.lineTo(mapped(pulse_x, pulse_y))
        for width, color in (
            (12.0 * unit, QColor(255, 20, 12, 30)),
            (6.0 * unit, QColor(255, 32, 20, 92)),
            (2.2 * unit, QColor(255, 70, 42, 245)),
        ):
            pen = QPen(color, max(width, 1.0)); pen.setCapStyle(Qt.PenCapStyle.RoundCap); pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawPath(animated_ray)

        scan_fraction = 0.5 + 0.5 * float(np.sin(self.progress * 4.0 * np.pi))
        scan_x = 0.580 + 0.320 * scan_fraction
        scan_top = mapped(scan_x, 0.365); scan_bottom = mapped(scan_x, 0.655)
        painter.setPen(QPen(QColor(0, 190, 255, 205), max(1.4 * unit, 1.0), Qt.PenStyle.DashLine))
        painter.drawLine(scan_top, scan_bottom)


class MeasurementPrincipleDialog(QDialog):
    """Non-modal animated explanation of the M² measurement process."""

    STEPS = (
        "1. The laser beam propagates from its original waist",
        "2. The focusing lens transforms the beam",
        "3. A new waist and focus region form after the lens",
        "4. The detector samples D4σ beam sizes at multiple Z positions",
        "5. X/Y propagation fits yield the waist, divergence, and M²",
    )

    def __init__(self, image_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("M² Measurement Principle")
        self.setModal(False)
        self.resize(1220, 610)
        self.setMinimumSize(820, 470)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(9)
        title = QLabel("M² Measurement Principle")
        title.setObjectName("AppTitle")
        root.addWidget(title)
        self.principle_view = MeasurementPrincipleView(image_path, self)
        root.addWidget(self.principle_view, 1)
        self.step_label = QLabel(self.STEPS[0])
        self.step_label.setObjectName("FormulaValue")
        self.step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.step_label.setWordWrap(True)
        root.addWidget(self.step_label)
        explanation = QLabel(
            "The animated red ray shows light propagating from the laser, through the lens, to the detector; "
            "the cyan dashed line represents the detector scanning along Z. "
            "D4σ X/Y widths sampled at multiple positions are fitted to the hyperbolic propagation model."
        )
        explanation.setObjectName("SecondaryLabel")
        explanation.setAlignment(Qt.AlignmentFlag.AlignCenter)
        explanation.setWordWrap(True)
        root.addWidget(explanation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        root.addWidget(buttons)
        self.principle_view.stageChanged.connect(self._set_stage)

    def _set_stage(self, stage: int) -> None:
        self.step_label.setText(self.STEPS[int(np.clip(stage, 0, len(self.STEPS) - 1))])

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.principle_view.start()

    def hideEvent(self, event) -> None:
        self.principle_view.stop()
        super().hideEvent(event)


class FormulaDialog(QDialog):
    """Focused explanation of one computed Laser result."""

    def __init__(
        self,
        parameter_name: str,
        value_text: str,
        unit_text: str,
        formula_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        spec = LASER_FORMULAS[formula_key]
        self.setWindowTitle(f"{parameter_name} · Calculation Formula")
        self.setModal(True)
        self.resize(760, 500)
        self.setMinimumSize(620, 420)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)

        title = QLabel(parameter_name)
        title.setObjectName("AppTitle")
        root.addWidget(title)
        current = QLabel(f"Current result: {value_text}{(' ' + unit_text) if unit_text else ''}")
        current.setObjectName("FormulaValue")
        current.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(current)

        formula = QLabel(str(spec["formula"]))
        formula.setObjectName("FormulaBlock")
        formula.setTextFormat(Qt.TextFormat.RichText)
        formula.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        formula.setWordWrap(True)
        formula.setAccessibleName(f"Formula for {parameter_name}")
        root.addWidget(formula)

        variable_title = QLabel("Variables")
        variable_title.setObjectName("SectionTitle")
        root.addWidget(variable_title)
        variables = QTreeWidget()
        variables.setColumnCount(3)
        variables.setHeaderLabels(("Symbol", "Meaning", "Unit"))
        variables.setAlternatingRowColors(True)
        variables.setRootIsDecorated(False)
        variables.setUniformRowHeights(False)
        variables.setWordWrap(True)
        variables.setTextElideMode(Qt.TextElideMode.ElideNone)
        variables.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        variables.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        variables.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        variables.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        for symbol, meaning, unit in spec["variables"]:
            QTreeWidgetItem(variables, [str(symbol), str(meaning), str(unit)])
        root.addWidget(variables, 1)

        note = QLabel(str(spec["note"]))
        note.setObjectName("SecondaryLabel")
        note.setWordWrap(True)
        note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


class OpticsDialog(QDialog):
    """Focused editor for infrequently changed optical settings."""

    FIELD_SPECS = (
        ("wavelength_um", "Wavelength", "µm", 6),
        ("laser_location_mm", "Laser location", "mm", 6),
        ("f_cal_mm", "Calibration EFL", "mm", 6),
        ("bfl_cal_mm", "Calibration BFL", "mm", 6),
        ("center_thickness_mm", "Center thickness CT", "mm", 6),
        ("calibration_wavelength_nm", "Calibration wavelength", "nm", 6),
        ("n_cal", "n at calibration", "", 8),
        ("n_lambda", "n at laser wavelength", "", 8),
        ("z_lens_mm", "Z Lens", "mm", 6),
        ("focal_sample_tolerance_mm", "Focal sample tolerance", "mm", 6),
    )

    def __init__(self, config: m2_core.AnalysisConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Optics Settings")
        self.setModal(True)
        self.setMinimumWidth(500)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        title = QLabel("Optics Settings")
        title.setObjectName("AppTitle")
        root.addWidget(title)
        summary = QLabel(
            "Controls post-lens conversion, real-beam waist reconstruction, and coordinate translation."
        )
        summary.setObjectName("SecondaryLabel")
        summary.setWordWrap(True)
        root.addWidget(summary)
        group = QGroupBox("Optical parameters")
        form = QFormLayout(group)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        self.fields: dict[str, QDoubleSpinBox] = {}
        for key, label, unit, decimals in self.FIELD_SPECS:
            field = QDoubleSpinBox()
            field.setRange(0.0, 1_000_000.0)
            field.setDecimals(decimals)
            field.setValue(float(getattr(config, key) or 0.0))
            field.setSuffix(f" {unit}" if unit else "")
            field.setKeyboardTracking(False)
            field.setAccessibleName(label)
            self.fields[key] = field
            form.addRow(label, field)
        self.coordinate_mode = QComboBox()
        self.coordinate_mode.addItems(("Automatic", "Manual"))
        self.coordinate_mode.setCurrentText("Automatic" if config.automatic_mode else "Manual")
        form.addRow("Coordinate mode", self.coordinate_mode)
        self.force_fitted = QCheckBox("Force fitted focal divergence")
        self.force_fitted.setChecked(config.force_fitted_divergence)
        form.addRow("", self.force_fitted)
        root.addWidget(group)
        note = QLabel("Automatic coordinate correction: −Z Lens + CT.")
        note.setObjectName("SecondaryLabel")
        root.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.RestoreDefaults
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply and Refit")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self.restore_defaults
        )
        root.addWidget(buttons)

    def restore_defaults(self) -> None:
        defaults = m2_core.AnalysisConfig()
        for key, field in self.fields.items():
            field.setValue(float(getattr(defaults, key) or 0.0))
        self.coordinate_mode.setCurrentText("Automatic")
        self.force_fitted.setChecked(False)

    def analysis_config_value(self) -> m2_core.AnalysisConfig:
        values = {key: field.value() for key, field in self.fields.items()}
        laser = values.pop("laser_location_mm")
        result = m2_core.AnalysisConfig(
            **values,
            laser_location_mm=None if laser == 0.0 else laser,
            automatic_mode=self.coordinate_mode.currentText() == "Automatic",
            force_fitted_divergence=self.force_fitted.isChecked(),
        )
        result.validate()
        return result


class CaustiqWindow(QMainWindow):
    """Qt Widgets shell for beam-profile analysis and visualization."""

    PANEL_TITLES = {
        "setup": "Setup",
        "propagation": "Beam Propagation",
        "results": "Results",
        "beam_data": "Beam Data",
        "spot": "2D Beam · OSI Rainbow",
        "beam3d": "3D Beam",
    }

    def __init__(self, initial_source: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION} · {APP_SUBTITLE}")
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("assets") / "caustiq.ico")))
        self.resize(1600, 940)
        self.setMinimumSize(1180, 720)
        self.setDockNestingEnabled(True)
        self.setAnimated(False)
        config_path = Path(__file__).with_name("measurement_config.local.json")
        if not config_path.exists():
            config_path = Path(__file__).with_name("measurement_config.json")
        self.measurement_config = (
            MeasurementConfig.from_json(config_path)
            if config_path.exists()
            else MeasurementConfig()
        )
        self.analysis_config = m2_core.AnalysisConfig()
        self.dataset: AngleDataset | None = None
        self.current_series: AngleSeries | None = None
        self.current_m2: m2_core.AnalysisResult | None = None
        self.fit_cache: dict[
            tuple[int, m2_core.AnalysisConfig], m2_core.AnalysisResult | Exception
        ] = {}
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.cancel_event = threading.Event()
        self.worker_started_at = 0.0
        self.worker_has_progress = False
        self.selected_preview_index: int | None = None
        self.selected_point_artists: list[object] = []
        self.spot_image_artist = None
        self.spot_colorbar = None
        self.spot_x_center_line = None
        self.spot_y_center_line = None
        self._spot_extent: tuple[float, float, float, float] | None = None
        self._spot_scale_um_per_px: float | None = None
        self._spot_drag_state: tuple[float, float, tuple[float, float], tuple[float, float]] | None = None
        self.beam3d_dataset_token: int | None = None
        self.beam3d_point_artist = None
        self.last_plot_window = "propagation"
        self._syncing_angle = False
        self._angle_scan_frame_cache: pd.DataFrame | None = None
        self.settings_store = QSettings("Caustiq", "Beam Profiling & M² Analysis")
        self.theme_choice = str(self.settings_store.value("appearance", "System"))
        if self.theme_choice not in {"System", "Light", "Dark"}:
            self.theme_choice = "System"
        self.tokens = LIGHT
        self._build_actions()
        self._build_menu()
        self._build_toolbar()
        self._build_workspace()
        self._build_status_bar()
        self._restore_layout()
        self.apply_theme(self.theme_choice, announce=False)
        self._draw_empty_plots()
        self.angle_debounce = QTimer(self)
        self.angle_debounce.setSingleShot(True)
        self.angle_debounce.setInterval(110)
        self.angle_debounce.timeout.connect(self._update_current_angle)
        self.worker_timer = QTimer(self)
        self.worker_timer.setInterval(60)
        self.worker_timer.timeout.connect(self._poll_worker)
        if initial_source is not None:
            QTimer.singleShot(150, lambda: self.load_source_path(initial_source))

    # Actions, menus, and toolbar
    def _icon(self, standard: QStyle.StandardPixmap) -> QIcon:
        return self.style().standardIcon(standard)

    def _action(self, text: str, slot: Callable, *, shortcut: str | None = None,
                icon: QStyle.StandardPixmap | None = None, tooltip: str | None = None) -> QAction:
        action = QAction(self._icon(icon) if icon is not None else QIcon(), text, self)
        action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if tooltip:
            action.setToolTip(tooltip)
            action.setStatusTip(tooltip)
        return action

    def _build_actions(self) -> None:
        sp = QStyle.StandardPixmap
        self.open_csv_action = self._action("Open CSV…", self.open_csv, shortcut="Ctrl+O", icon=sp.SP_DialogOpenButton, tooltip="Open a single- or multi-angle D4σ CSV")
        self.open_images_action = self._action("Open Images…", self.open_images)
        self.open_folder_action = self._action("Open Image Folder…", self.open_folder, icon=sp.SP_DirOpenIcon)
        self.open_zip_action = self._action("Open Image ZIP…", self.open_zip, shortcut="Ctrl+Shift+O", icon=sp.SP_DriveHDIcon)
        self.hardware_source_action = self._action(
            "Hardware Source…",
            lambda: None,
            icon=sp.SP_ComputerIcon,
            tooltip="Reserved for future hardware connection and live testing",
        )
        self.hardware_source_action.setEnabled(False)
        self.refit_action = self._action("Refit", self.refit_current, shortcut="F5", icon=sp.SP_BrowserReload, tooltip="Refit the selected angle")
        self.max_ast_action = self._action("Max Ast", self.select_max_astigmatism, tooltip="Select the angle with maximum real-laser astigmatism")
        self.max_ast_action.setEnabled(False)
        self.scan_action = self._action("Build Full M² Angle Scan", self.compute_full_angle_scan)
        self.optics_action = self._action("Optics Settings…", self.open_optics_settings, icon=sp.SP_FileDialogDetailedView)
        self.export_current_action = self._action("Current Angle Z Data…", self.export_current_angle)
        self.export_all_action = self._action("All Angle D4σ Long Table…", self.export_all_angles)
        self.export_scan_action = self._action("M² Angle Scan…", self.export_m2_scan, shortcut="Ctrl+S", icon=sp.SP_DialogSaveButton)
        self.export_plot_action = self._action("Current Plot…", self.export_plot)
        self.exit_action = self._action("Exit", self.close, shortcut="Alt+F4")
        self.about_action = self._action("About Caustiq…", self.show_about)

    def _build_menu(self) -> None:
        bar = self.menuBar()
        source = bar.addMenu("&Source")
        source.addActions((self.open_csv_action, self.open_images_action, self.open_folder_action, self.open_zip_action))
        source.addSeparator(); source.addAction(self.hardware_source_action)
        source.addSeparator(); source.addAction(self.exit_action)
        analysis = bar.addMenu("&Analysis")
        analysis.addActions((self.refit_action, self.max_ast_action, self.scan_action))
        settings = bar.addMenu("&Settings")
        settings.addAction(self.optics_action)
        appearance = settings.addMenu("Appearance")
        group = QActionGroup(self); group.setExclusive(True)
        self.theme_actions: dict[str, QAction] = {}
        for name in ("System", "Light", "Dark"):
            action = appearance.addAction(name)
            action.setCheckable(True); action.setChecked(name == self.theme_choice)
            action.triggered.connect(lambda _checked=False, value=name: self.apply_theme(value))
            group.addAction(action); self.theme_actions[name] = action
        export = bar.addMenu("&Export")
        export.addActions((self.export_current_action, self.export_all_action, self.export_scan_action, self.export_plot_action))
        self.window_menu = bar.addMenu("&Window")
        self.window_menu.addSeparator()
        self.show_all_action = self.window_menu.addAction("Show All Panels")
        self.show_all_action.triggered.connect(self.show_all_docks)
        bar.addMenu("&Help").addAction(self.about_action)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Primary", self)
        toolbar.setObjectName("PrimaryToolbar")
        toolbar.setMovable(False); toolbar.setFloatable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setIconSize(QSize(17, 17))
        toolbar.addAction(self.open_csv_action); toolbar.addAction(self.open_zip_action); toolbar.addAction(self.hardware_source_action)
        toolbar.addSeparator(); toolbar.addAction(self.refit_action); toolbar.addAction(self.max_ast_action)
        toolbar.addSeparator(); toolbar.addAction(self.export_scan_action); toolbar.addAction(self.optics_action)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        brand = QWidget(); brand.setObjectName("ToolbarBrand")
        brand_layout = QVBoxLayout(brand); brand_layout.setContentsMargins(8, 0, 2, 0); brand_layout.setSpacing(0)
        title = QLabel("Caustiq"); title.setObjectName("AppTitle"); title.setAlignment(Qt.AlignmentFlag.AlignRight)
        subtitle = QLabel(f"v{APP_VERSION} · {APP_SUBTITLE}"); subtitle.setObjectName("BrandSubtitle"); subtitle.setAlignment(Qt.AlignmentFlag.AlignRight)
        brand_layout.addWidget(title); brand_layout.addWidget(subtitle)
        toolbar.addWidget(brand)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

    # Workspace and docks
    def _new_dock(self, key: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(self.PANEL_TITLES[key], self)
        dock.setObjectName(f"{key}_dock")
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setAccessibleName(f"{self.PANEL_TITLES[key]} panel")
        widget.setObjectName("DockSurface"); dock.setWidget(widget)
        dock.visibilityChanged.connect(lambda visible, name=key: self._dock_visibility_changed(name, visible))
        self.addDockWidget(area, dock)
        self.window_menu.insertAction(self.window_menu.actions()[-2], dock.toggleViewAction())
        return dock

    def _build_workspace(self) -> None:
        underlay = QWidget(); underlay.setObjectName("CentralUnderlay"); underlay.setFixedSize(0, 0)
        self.setCentralWidget(underlay)
        self.setup_dock = self._new_dock("setup", self._build_setup_panel(), Qt.DockWidgetArea.LeftDockWidgetArea)
        self.propagation_dock = self._new_dock("propagation", self._build_propagation_panel(), Qt.DockWidgetArea.RightDockWidgetArea)
        self.results_dock = self._new_dock("results", self._build_results_panel(), Qt.DockWidgetArea.RightDockWidgetArea)
        self.beam_data_dock = self._new_dock("beam_data", self._build_data_panel(), Qt.DockWidgetArea.BottomDockWidgetArea)
        self.spot_dock = self._new_dock("spot", self._build_spot_panel(), Qt.DockWidgetArea.BottomDockWidgetArea)
        self.beam3d_dock = self._new_dock("beam3d", self._build_3d_panel(), Qt.DockWidgetArea.BottomDockWidgetArea)
        self.docks = {"setup": self.setup_dock, "propagation": self.propagation_dock, "results": self.results_dock, "beam_data": self.beam_data_dock, "spot": self.spot_dock, "beam3d": self.beam3d_dock}
        self.splitDockWidget(self.propagation_dock, self.results_dock, Qt.Orientation.Horizontal)
        self.splitDockWidget(self.propagation_dock, self.beam_data_dock, Qt.Orientation.Vertical)
        self.splitDockWidget(self.beam_data_dock, self.spot_dock, Qt.Orientation.Horizontal)
        self.splitDockWidget(self.spot_dock, self.beam3d_dock, Qt.Orientation.Horizontal)
        self.spot_dock.hide(); self.beam3d_dock.hide()
        self.resizeDocks([self.setup_dock, self.propagation_dock, self.results_dock], [300, 800, 450], Qt.Orientation.Horizontal)

    def _build_setup_panel(self) -> QWidget:
        scroll = QScrollArea(); scroll.setObjectName("SidebarScroll"); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(); content.setObjectName("SidebarContent"); layout = QVBoxLayout(content); layout.setContentsMargins(12, 12, 12, 12); layout.setSpacing(12)
        source_group = QGroupBox("Data source"); source_layout = QVBoxLayout(source_group); source_layout.setSpacing(6)
        self.source_name = QLabel("No source loaded"); self.source_name.setObjectName("AppTitle"); self.source_name.setWordWrap(True)
        self.source_detail = QLabel("Open a CSV, image sequence, folder, or ZIP archive."); self.source_detail.setObjectName("SourceDetail"); self.source_detail.setWordWrap(True)
        source_layout.addWidget(self.source_name); source_layout.addWidget(self.source_detail)
        buttons = QHBoxLayout(); open_csv = QPushButton("CSV…"); open_csv.clicked.connect(self.open_csv); open_images = QPushButton("Images…"); open_images.clicked.connect(self.open_images)
        buttons.addWidget(open_csv); buttons.addWidget(open_images); source_layout.addLayout(buttons); layout.addWidget(source_group)
        angle_group = QGroupBox("XY relative deviation"); angle_layout = QVBoxLayout(angle_group); angle_layout.setSpacing(8)
        self.angle_value = QLabel("δ = +0°"); self.angle_value.setObjectName("AngleValue"); self.angle_value.setAlignment(Qt.AlignmentFlag.AlignCenter); angle_layout.addWidget(self.angle_value)
        controls = QHBoxLayout(); minus = QPushButton("−1°"); minus.clicked.connect(lambda: self.step_angle(-1))
        self.angle_spin = QSpinBox(); self.angle_spin.setRange(ANGLE_MIN_DEG, ANGLE_MAX_DEG); self.angle_spin.setSuffix("°"); self.angle_spin.setAlignment(Qt.AlignmentFlag.AlignCenter); self.angle_spin.setAccessibleName("XY relative deviation angle")
        plus = QPushButton("+1°"); plus.clicked.connect(lambda: self.step_angle(1))
        controls.addWidget(minus); controls.addWidget(self.angle_spin, 1); controls.addWidget(plus); angle_layout.addLayout(controls)
        self.angle_slider = QSlider(Qt.Orientation.Horizontal); self.angle_slider.setRange(ANGLE_MIN_DEG, ANGLE_MAX_DEG); self.angle_slider.setAccessibleName("XY relative deviation angle slider"); angle_layout.addWidget(self.angle_slider)
        self.axis_hint = QLabel("X = 90° · Y = 0°\nNegative δ rotates the XY pair counter-clockwise."); self.axis_hint.setObjectName("AxisHint"); self.axis_hint.setWordWrap(True); angle_layout.addWidget(self.axis_hint)
        self.auto_update = QCheckBox("Auto-update M²"); self.auto_update.setChecked(True); angle_layout.addWidget(self.auto_update)
        action_row = QHBoxLayout(); self.max_ast_button = QPushButton("Max Ast"); self.max_ast_button.setEnabled(False); self.max_ast_button.clicked.connect(self.select_max_astigmatism)
        refit = QPushButton("Refit Current Angle"); refit.setProperty("primary", True); refit.clicked.connect(self.refit_current)
        action_row.addWidget(self.max_ast_button); action_row.addWidget(refit, 1); angle_layout.addLayout(action_row); layout.addWidget(angle_group)
        info = QLabel("Image import uses the validated fixed D4σ settings. Optical parameters are available from Settings."); info.setObjectName("SecondaryLabel"); info.setWordWrap(True); layout.addWidget(info); layout.addStretch(1)
        scroll.setWidget(content)
        self.angle_spin.valueChanged.connect(self._angle_from_spin); self.angle_slider.valueChanged.connect(self._angle_from_slider); self.auto_update.toggled.connect(self._auto_update_changed)
        return scroll

    def _build_propagation_panel(self) -> QWidget:
        tabs = QTabWidget(); self.plot_tabs = tabs
        self.caustic_figure = Figure(figsize=(8.5, 6.5), dpi=100); grid = self.caustic_figure.add_gridspec(2, 1, height_ratios=(3, 1), hspace=0.26)
        self.caustic_axis = self.caustic_figure.add_subplot(grid[0]); self.residual_axis = self.caustic_figure.add_subplot(grid[1], sharex=self.caustic_axis)
        caustic_panel = PlotPanel(self.caustic_figure); self.caustic_canvas = caustic_panel.canvas; tabs.addTab(caustic_panel, "Current-angle Caustic")
        self.angle_figure = Figure(figsize=(8.5, 6.5), dpi=100); angle_grid = self.angle_figure.add_gridspec(2, 1, hspace=0.28)
        self.m2_angle_axis = self.angle_figure.add_subplot(angle_grid[0]); self.waist_angle_axis = self.angle_figure.add_subplot(angle_grid[1])
        angle_panel = PlotPanel(self.angle_figure); self.angle_canvas = angle_panel.canvas; tabs.addTab(angle_panel, "M² versus Angle")
        self.caustic_canvas.mpl_connect("button_press_event", self._on_caustic_click)
        self.caustic_canvas.mpl_connect("figure_enter_event", lambda _event: self._mark_plot("propagation")); self.angle_canvas.mpl_connect("figure_enter_event", lambda _event: self._mark_plot("propagation"))
        return tabs

    def _build_results_panel(self) -> QWidget:
        root = QWidget(); layout = QVBoxLayout(root); layout.setContentsMargins(8, 8, 8, 8); layout.setSpacing(7)
        row = QHBoxLayout(); row.addWidget(QLabel("Numeric display")); self.dec_radio = QRadioButton("DEC"); self.sci_radio = QRadioButton("SCI"); self.dec_radio.setChecked(True); self.dec_radio.toggled.connect(self.refresh_results)
        row.addWidget(self.dec_radio); row.addWidget(self.sci_radio); row.addStretch(1)
        self.principle_button = QPushButton("Test Principle"); self.principle_button.setToolTip("View the animated M² measurement principle"); self.principle_button.clicked.connect(self._show_measurement_principle); row.addWidget(self.principle_button); layout.addLayout(row)
        hint = QLabel("Double-click a Laser parameter to view its formula and variable definitions.")
        hint.setObjectName("SecondaryLabel"); hint.setWordWrap(True); layout.addWidget(hint)
        self.results_tree = QTreeWidget(); self.results_tree.setColumnCount(3); self.results_tree.setHeaderLabels(("Name", "Value", "Units")); self.results_tree.setAlternatingRowColors(True); self.results_tree.setUniformRowHeights(True)
        self.results_tree.setAccessibleName("Measurement results; double-click Laser parameters for formulas")
        self.results_tree.itemDoubleClicked.connect(self._show_result_formula)
        self.results_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive); self.results_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents); self.results_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.results_tree.setColumnWidth(0, 220)
        layout.addWidget(self.results_tree, 1); return root

    def _build_data_panel(self) -> QWidget:
        tabs = QTabWidget(); self.data_table = QTableWidget(0, 9)
        self.data_table.setHorizontalHeaderLabels(("No.", "Fit Z (mm)", "Stage Z (mm)", "δ (°)", "D4σ X (µm)", "D4σ Y (µm)", "Frames", "Image / File", "Quality warning"))
        self.data_table.setAlternatingRowColors(True); self.data_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.data_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.data_table.horizontalHeader()
        for col in range(7): header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch); header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        tabs.addTab(self.data_table, "Z-position Beam Data")
        self.diagnostics = QPlainTextEdit(); self.diagnostics.setReadOnly(True); self.diagnostics.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth); tabs.addTab(self.diagnostics, "ISO / Coordinate / Image Quality")
        return tabs

    def _build_spot_panel(self) -> QWidget:
        self.spot_figure = Figure(figsize=(7.2, 6.2), dpi=100, frameon=False); self.spot_axis = self.spot_figure.add_axes((0.0, 0.0, 1.0, 1.0))
        panel = PlotPanel(self.spot_figure); self.spot_canvas = panel.canvas
        self.spot_canvas.mpl_connect("scroll_event", self._on_2d_scroll)
        self.spot_canvas.mpl_connect("button_press_event", self._on_2d_drag_start)
        self.spot_canvas.mpl_connect("motion_notify_event", self._on_2d_drag_move)
        self.spot_canvas.mpl_connect("button_release_event", self._on_2d_drag_end)
        self.spot_canvas.mpl_connect("resize_event", self._on_2d_resize)
        self.spot_canvas.mpl_connect("figure_enter_event", lambda _event: self._mark_plot("spot")); return panel

    def _build_3d_panel(self) -> QWidget:
        self.beam3d_figure = Figure(figsize=(7.2, 6.2), dpi=100, frameon=False); self.beam3d_axis = self.beam3d_figure.add_axes((0.0, 0.0, 1.0, 1.0), projection=ResponsiveAxes3D.name)
        panel = PlotPanel(self.beam3d_figure); self.beam3d_canvas = panel.canvas
        self.beam3d_canvas.mpl_connect("scroll_event", self._on_3d_scroll)
        self.beam3d_canvas.mpl_connect("figure_enter_event", lambda _event: self._mark_plot("beam3d")); return panel

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self); self.setStatusBar(bar); self.status_dot = QLabel("●"); self.status_dot.setObjectName("StatusDot"); self.status_label = QLabel("Ready"); self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0); self.progress.setTextVisible(False); self.progress.setFixedWidth(230)
        self.cancel_button = QPushButton("Cancel"); self.cancel_button.setEnabled(False); self.cancel_button.clicked.connect(self.cancel_processing)
        bar.addWidget(self.status_dot); bar.addWidget(self.status_label, 1); bar.addPermanentWidget(self.progress); bar.addPermanentWidget(self.cancel_button)

    # Theme and presentation
    def _system_is_dark(self) -> bool:
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorScheme"):
            return hints.colorScheme() == Qt.ColorScheme.Dark
        return self.palette().window().color().lightness() < 128

    def apply_theme(self, choice: str, *, announce: bool = True) -> None:
        self.theme_choice = choice if choice in {"System", "Light", "Dark"} else "System"
        resolved = "Dark" if (self.theme_choice == "System" and self._system_is_dark()) else self.theme_choice
        if resolved == "System": resolved = "Light"
        self.tokens = THEMES[resolved]
        app = QApplication.instance()
        if app is not None: app.setStyleSheet(style_sheet(self.tokens))
        for name, action in self.theme_actions.items(): action.setChecked(name == self.theme_choice)
        self.settings_store.setValue("appearance", self.theme_choice)
        self._apply_plot_theme(draw=True)
        if announce: self._set_status(f"Appearance: {self.theme_choice}", "accent")

    def _style_figure(self, figure: Figure) -> None:
        t = self.tokens
        is_beam_view = figure in (getattr(self, "spot_figure", None), getattr(self, "beam3d_figure", None))
        background = "#000000" if is_beam_view else t.surface
        figure.set_facecolor(background)
        if is_beam_view:
            # With frameon=False Agg leaves transparent pixels outside the image,
            # letting the light Qt widget background show through.
            figure.set_frameon(True)
        for axis in figure.axes:
            axis.set_facecolor(background); axis.tick_params(colors=t.secondary, labelsize=9); axis.xaxis.label.set_color(t.secondary); axis.yaxis.label.set_color(t.secondary); axis.title.set_color(t.text)
            for spine in axis.spines.values(): spine.set_color(t.border_strong)
            for gridline in (*axis.get_xgridlines(), *axis.get_ygridlines()): gridline.set_color(t.plot_grid); gridline.set_alpha(0.45)
            legend = axis.get_legend()
            if legend:
                legend.get_frame().set_facecolor(t.surface); legend.get_frame().set_edgecolor(t.border)
                for label in legend.get_texts(): label.set_color(t.text)

    def _apply_plot_theme(self, *, draw: bool) -> None:
        for figure in (getattr(self, "caustic_figure", None), getattr(self, "angle_figure", None), getattr(self, "spot_figure", None), getattr(self, "beam3d_figure", None)):
            if figure is not None: self._style_figure(figure)
        if draw:
            for canvas in (getattr(self, "caustic_canvas", None), getattr(self, "angle_canvas", None), getattr(self, "spot_canvas", None), getattr(self, "beam3d_canvas", None)):
                if canvas is not None: canvas.draw_idle()

    def _draw_empty_plots(self) -> None:
        t = self.tokens
        for axis in (self.caustic_axis, self.residual_axis, self.m2_angle_axis, self.waist_angle_axis): axis.clear(); axis.grid(True, alpha=0.22)
        self.caustic_axis.text(0.5, 0.5, "Load a CSV or image sequence", ha="center", va="center", transform=self.caustic_axis.transAxes, color=t.secondary)
        self.caustic_axis.set_ylabel("D4σ width (µm)"); self.residual_axis.set_xlabel("Z position (mm)"); self.residual_axis.set_ylabel("Residual"); self.m2_angle_axis.set_ylabel("M²"); self.waist_angle_axis.set_xlabel("XY relative deviation δ (°)"); self.waist_angle_axis.set_ylabel("W02 (µm)")
        self.spot_axis.clear(); self.spot_axis.set_axis_off()
        self.beam3d_axis.clear(); self.beam3d_axis.set_axis_off(); self._apply_plot_theme(draw=True)

    def _dock_visibility_changed(self, key: str, visible: bool) -> None:
        if not visible: return
        if key == "beam3d": QTimer.singleShot(0, self._build_3d_beam)
        elif key == "spot" and self.selected_preview_index is not None: QTimer.singleShot(0, lambda: self._show_2d_preview(self.selected_preview_index, update_status=False))

    def show_all_docks(self) -> None:
        for dock in self.docks.values(): dock.show()

    def _restore_layout(self) -> None:
        geometry = self.settings_store.value("windowGeometry"); state = self.settings_store.value("windowState")
        if geometry: self.restoreGeometry(geometry)
        if state: self.restoreState(state)

    def closeEvent(self, event) -> None:
        self.cancel_event.set(); self.settings_store.setValue("windowGeometry", self.saveGeometry()); self.settings_store.setValue("windowState", self.saveState()); super().closeEvent(event)

    # Source loading
    def _fixed_image_settings(self) -> AnalysisSettings:
        cfg = self.measurement_config
        return AnalysisSettings(pixel_size_um=cfg.sensor_pixel_size_um, magnification=cfg.magnification, camera_angle_deg=cfg.camera_angle_deg)

    def open_csv(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Open D4σ / angle CSV", "", "CSV files (*.csv);;All files (*.*)")
        if filename: self._load_csv(Path(filename))

    def open_images(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(self, "Open beam image sequence", "", "Beam images (*.tif *.tiff *.png *.bmp *.jpg *.jpeg);;All files (*.*)")
        if filenames: self._load_image_set(ImageSet.from_files(filenames))

    def open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open beam image folder")
        if folder: self._load_image_set(ImageSet.from_folder(folder))

    def open_zip(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Open beam image ZIP", "", "ZIP archives (*.zip);;All files (*.*)")
        if filename: self._load_zip(Path(filename))

    def load_source_path(self, source: Path) -> None:
        path = Path(source).expanduser().resolve()
        if path.is_dir(): self._load_image_set(ImageSet.from_folder(path))
        elif path.suffix.lower() == ".csv": self._load_csv(path)
        elif path.suffix.lower() == ".zip": self._load_zip(path)
        elif path.suffix.lower() in SUPPORTED_SUFFIXES: self._load_image_set(ImageSet.from_files([path]))
        else: QMessageBox.critical(self, "Unsupported source", f"Cannot open:\n{path}")

    def _load_csv(self, path: Path) -> None:
        def loader(_progress) -> AngleDataset:
            return load_csv_dataset(path, self.measurement_config, default_angle_deg=0, apply_flange_offset=True)
        self._start_worker(f"Reading {path.name}", loader)

    def _load_zip(self, path: Path) -> None:
        settings = self._fixed_image_settings()
        def loader(progress) -> AngleDataset:
            image_set: ImageSet | None = None
            try:
                image_set = ImageSet.from_zip(path, progress=lambda done, total, name: progress(done, max(total * 5, 1), name))
                if len(image_set.paths) < 3: raise ValueError("The ZIP must contain at least three supported beam images/Z positions.")
                return analyze_image_set(image_set, config=self.measurement_config, settings=settings, dark_frame=None, auto_pixel_from_metadata=True, apply_flange_offset=True, progress=lambda done, total, name: progress(total + 4 * done, max(total * 5, 1), name))
            finally:
                if image_set is not None: image_set.close()
        self._start_worker(f"Opening ZIP {path.name}", loader)

    def _load_image_set(self, image_set: ImageSet) -> None:
        if len(image_set.paths) < 3:
            image_set.close(); QMessageBox.warning(self, "Not enough images", "At least three images/Z positions are required for M² fitting."); return
        settings = self._fixed_image_settings()
        def loader(progress) -> AngleDataset:
            try:
                return analyze_image_set(image_set, config=self.measurement_config, settings=settings, dark_frame=None, auto_pixel_from_metadata=True, apply_flange_offset=True, progress=progress)
            finally: image_set.close()
        self._start_worker(f"Analyzing {len(image_set.paths)} images from {image_set.source_label}", loader)

    def _start_worker(self, label: str, loader: Callable) -> None:
        if self.busy: QMessageBox.information(self, "Busy", "Please wait for the current source to finish loading."); return
        self.busy = True; self.cancel_event.clear(); self.worker_started_at = time.perf_counter(); self.worker_has_progress = False
        self.progress.setRange(0, 0); self.cancel_button.setEnabled(True); QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor); self._set_status(label, "accent")
        def progress(done: int, total: int, name: str) -> None:
            if self.cancel_event.is_set(): raise ProcessingCancelled("Processing cancelled by user.")
            self.worker_queue.put(("progress", (done, total, name)))
        def run() -> None:
            try: dataset = loader(progress)
            except ProcessingCancelled as exc: self.worker_queue.put(("cancelled", exc))
            except Exception as exc: self.worker_queue.put(("error", exc))
            else: self.worker_queue.put(("done", dataset))
        threading.Thread(target=run, daemon=True).start(); self.worker_timer.start()

    def _poll_worker(self) -> None:
        terminal = False
        while True:
            try: kind, payload = self.worker_queue.get_nowait()
            except queue.Empty: break
            if kind == "progress":
                done, total, name = payload
                if not self.worker_has_progress: self.progress.setRange(0, 100); self.worker_has_progress = True
                fraction = min(max(float(done) / max(float(total), 1.0), 0.0), 1.0); self.progress.setValue(round(100 * fraction)); elapsed = max(time.perf_counter() - self.worker_started_at, 0.0); timing = f"elapsed {elapsed:.0f}s"
                if 0.02 <= fraction < 1.0: timing += f" · about {elapsed * (1.0 - fraction) / fraction:.0f}s remaining"
                self._set_status(f"{name} · {100 * fraction:.0f}% · {timing}", "accent")
            elif kind == "done": terminal = True; self._finish_worker_ui(100); self._apply_dataset(payload)
            elif kind == "cancelled": terminal = True; self._finish_worker_ui(0); self._set_status("Image processing cancelled", "warning")
            elif kind == "error": terminal = True; self._finish_worker_ui(0); self._set_status("Source loading failed", "error"); QMessageBox.critical(self, "Source loading failed", str(payload))
        if terminal: self.worker_timer.stop()

    def _finish_worker_ui(self, progress_value: int) -> None:
        self.busy = False; self.progress.setRange(0, 100); self.progress.setValue(progress_value); self.cancel_button.setEnabled(False); QApplication.restoreOverrideCursor()

    def cancel_processing(self) -> None:
        if self.busy: self.cancel_event.set(); self.cancel_button.setEnabled(False); self._set_status("Cancelling after the current image…", "warning")

    def _apply_dataset(self, dataset: AngleDataset) -> None:
        self.dataset = dataset; self.fit_cache.clear(); self._angle_scan_frame_cache = None; self.selected_preview_index = None; self.selected_point_artists.clear(); self.beam3d_dataset_token = None; self._reset_image_views()
        self.source_name.setText(dataset.source_label)
        angle_text = f"{len(dataset.available_angles)} angles · {min(dataset.available_angles):+d}° to {max(dataset.available_angles):+d}°" if dataset.supports_angle_scan else f"single angle {dataset.available_angles[0]:+d}°"
        self.source_detail.setText(f"{dataset.source_kind} · {dataset.row_count} rows · {angle_text}\nZ: {dataset.z_basis}")
        selected = dataset.nearest_available_angle(self.angle_spin.value()); self._syncing_angle = True
        self.angle_spin.setRange(min(dataset.available_angles), max(dataset.available_angles)); self.angle_slider.setRange(min(dataset.available_angles), max(dataset.available_angles)); self.angle_spin.setValue(selected); self.angle_slider.setValue(selected); self._syncing_angle = False
        enabled = dataset.supports_angle_scan; self.angle_spin.setEnabled(enabled); self.angle_slider.setEnabled(enabled); self.max_ast_button.setEnabled(enabled); self.max_ast_action.setEnabled(enabled); self._update_angle_labels(selected)
        if dataset.has_image_previews:
            self.selected_preview_index = int(self._unique_preview_indices(dataset.z_fit_mm)[0])
            self.spot_dock.show(); self.beam3d_dock.show()
            QTimer.singleShot(0, self._arrange_image_docks)
        self._update_current_angle(force=True)
        if self.selected_preview_index is not None: self._show_2d_preview(self.selected_preview_index, update_status=False)
        QTimer.singleShot(25, self.compute_full_angle_scan)
        if self.beam3d_dock.isVisible(): QTimer.singleShot(50, self._build_3d_beam)

    def _arrange_image_docks(self) -> None:
        """Keep 2D and 3D visible together while allowing free dock resizing."""
        self.resizeDocks(
            [self.beam_data_dock, self.spot_dock, self.beam3d_dock],
            [420, 520, 520],
            Qt.Orientation.Horizontal,
        )
        self.resizeDocks(
            [self.propagation_dock, self.beam_data_dock],
            [610, 300],
            Qt.Orientation.Vertical,
        )

    # Angle selection and fitting
    def _update_angle_labels(self, angle: int) -> None:
        self.angle_value.setText(f"δ = {angle:+d}°")
        self.axis_hint.setText(
            f"X = {90-angle:+d}° · Y = {-angle:+d}°\n"
            "Negative δ rotates the XY pair counter-clockwise."
        )

    def _angle_from_spin(self, value: int) -> None:
        if self._syncing_angle:
            return
        self._syncing_angle = True
        self.angle_slider.setValue(value)
        self._syncing_angle = False
        self._angle_changed(value)

    def _angle_from_slider(self, value: int) -> None:
        if self._syncing_angle:
            return
        self._syncing_angle = True
        self.angle_spin.setValue(value)
        self._syncing_angle = False
        self._angle_changed(value)

    def _angle_changed(self, requested: int) -> None:
        angle = int(np.clip(requested, ANGLE_MIN_DEG, ANGLE_MAX_DEG))
        if self.dataset is not None:
            angle = self.dataset.nearest_available_angle(angle)
        if angle != self.angle_spin.value():
            self._syncing_angle = True
            self.angle_spin.setValue(angle)
            self.angle_slider.setValue(angle)
            self._syncing_angle = False
        self._update_angle_labels(angle)
        self._mark_current_angle()
        if self.auto_update.isChecked() and self.dataset is not None:
            self.angle_debounce.start()

    def _auto_update_changed(self, enabled: bool) -> None:
        if enabled and self.dataset is not None:
            self._update_current_angle()

    def step_angle(self, delta: int) -> None:
        if self.dataset is None:
            value = self.angle_spin.value() + delta
        elif self.dataset.supports_angle_scan:
            value = self.dataset.nearest_available_angle(self.angle_spin.value() + delta)
        else:
            value = self.dataset.available_angles[0]
        self.angle_spin.setValue(int(np.clip(value, ANGLE_MIN_DEG, ANGLE_MAX_DEG)))

    @staticmethod
    def _maximum_astigmatism_choice(values: list[tuple[int, float]]) -> tuple[int, float]:
        valid = [(int(a), float(v)) for a, v in values if np.isfinite(v)]
        if not valid:
            raise ValueError("No valid angle has a finite laser astigmatism result.")
        return max(valid, key=lambda item: item[1])

    def select_max_astigmatism(self) -> None:
        if self.dataset is None:
            QMessageBox.information(self, "No Data", "Load an image ZIP or multi-angle CSV first.")
            return
        if not self.dataset.supports_angle_scan:
            QMessageBox.information(self, "Single-angle Data", "Max Ast is available for multi-angle datasets.")
            return
        candidates: list[tuple[int, float]] = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.progress.setRange(0, 100)
        try:
            angles = self.dataset.available_angles
            for index, angle in enumerate(angles, start=1):
                QApplication.processEvents()
                try:
                    result = self._fit_angle(angle)
                except Exception:
                    pass
                else:
                    candidates.append((angle, result.astigmatism_real))
                self.progress.setValue(round(index * 100 / len(angles)))
                if index == 1 or index % 10 == 0 or index == len(angles):
                    self._set_status(f"Searching maximum laser astigmatism · {index}/{len(angles)}", "accent")
            angle, astigmatism = self._maximum_astigmatism_choice(candidates)
        except Exception as exc:
            QMessageBox.critical(self, "Max Ast search failed", str(exc))
            self._set_status(f"Max Ast search failed: {exc}", "error")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.angle_spin.setValue(angle)
        self._update_current_angle(force=True)
        self._set_status(f"Maximum real-laser astigmatism at δ={angle:+d}° · {astigmatism:.5f}", "success")

    def _fit_angle(
        self,
        angle: int,
        config: m2_core.AnalysisConfig | None = None,
    ) -> m2_core.AnalysisResult:
        if self.dataset is None:
            raise ValueError("No dataset is loaded.")
        active_config = config if config is not None else self.analysis_config
        selected = self.dataset.nearest_available_angle(angle)
        key = (selected, active_config)
        cached = self.fit_cache.get(key)
        if isinstance(cached, Exception):
            raise cached
        if cached is not None:
            return cached
        series = self.dataset.series(selected)
        try:
            result = m2_core.analyze_arrays(
                series.z_mm,
                series.wx_um,
                series.wy_um,
                config=active_config,
                source_label=f"{self.dataset.source_label} @ {selected:+d} deg",
            )
        except Exception as exc:
            self.fit_cache[key] = exc
            raise
        self.fit_cache[key] = result
        return result

    def refit_current(self) -> None:
        if self.dataset is None:
            QMessageBox.information(self, "No data", "Load a CSV or image sequence first.")
            return
        angle = self.angle_spin.value()
        self.fit_cache.pop((angle, self.analysis_config), None)
        self._angle_scan_frame_cache = None
        self._update_current_angle(force=True)

    def _update_current_angle(self, force: bool = False) -> None:
        if self.dataset is None:
            return
        angle = self.dataset.nearest_available_angle(self.angle_spin.value())
        self._syncing_angle = True
        self.angle_spin.setValue(angle); self.angle_slider.setValue(angle)
        self._syncing_angle = False
        self._update_angle_labels(angle)
        try:
            result = self._fit_angle(angle)
            series = self.dataset.series(angle)
        except Exception as exc:
            self.current_m2 = None
            self.current_series = None
            self._set_status(f"M² fit failed at {angle:+d}°: {exc}", "error")
            QMessageBox.critical(self, "M² fit failed", str(exc))
            return
        self.current_m2 = result
        self.current_series = series
        self._update_caustic_plot(result, angle)
        self.refresh_results()
        self._update_data_table(series)
        self._update_diagnostics(result, series)
        self._mark_current_angle()
        self._set_status(
            f"δ={angle:+d}° · M² X={result.fit_x.m2:.4f} · M² Y={result.fit_y.m2:.4f}",
            "warning" if result.fit_x.m2 < 1.0 or result.fit_y.m2 < 1.0 else "success",
        )

    # Plots and image views
    def _update_caustic_plot(self, result: m2_core.AnalysisResult, angle: int) -> None:
        t = self.tokens
        z = result.z_mm
        low = min(float(np.min(z)), result.fit_x.waist_location_mm, result.fit_y.waist_location_mm)
        high = max(float(np.max(z)), result.fit_x.waist_location_mm, result.fit_y.waist_location_mm)
        padding = max((high - low) * 0.05, 1.0)
        z_fine = np.linspace(low - padding, high + padding, 600)
        fit_x = m2_core.beam_width_model(z_fine, result.fit_x.waist_width_um, result.fit_x.divergence_full_mrad, result.fit_x.waist_location_mm)
        fit_y = m2_core.beam_width_model(z_fine, result.fit_y.waist_width_um, result.fit_y.divergence_full_mrad, result.fit_y.waist_location_mm)
        predicted_x = m2_core.beam_width_model(z, result.fit_x.waist_width_um, result.fit_x.divergence_full_mrad, result.fit_x.waist_location_mm)
        predicted_y = m2_core.beam_width_model(z, result.fit_y.waist_width_um, result.fit_y.divergence_full_mrad, result.fit_y.waist_location_mm)
        self.caustic_axis.clear(); self.residual_axis.clear(); self.selected_point_artists.clear()
        self.caustic_axis.scatter(z, result.wx_um, color=t.x_axis, marker="o", s=28, label="X measured", zorder=3)
        self.caustic_axis.scatter(z, result.wy_um, color=t.y_axis, marker="s", s=27, label="Y measured", zorder=3)
        self.caustic_axis.plot(z_fine, fit_x, color=t.x_axis, lw=1.7, label="X fit")
        self.caustic_axis.plot(z_fine, fit_y, color=t.y_axis, lw=1.7, label="Y fit")
        self.caustic_axis.set_ylabel("D4σ width (µm)")
        self.caustic_axis.set_title(f"Current-angle caustic · δ={angle:+d}°", fontsize=11)
        self.caustic_axis.grid(True, alpha=0.24); self.caustic_axis.legend(loc="best", fontsize=8, ncols=2)
        self.residual_axis.axhline(0.0, color=t.secondary, lw=0.9)
        self.residual_axis.scatter(z, result.wx_um - predicted_x, color=t.x_axis, marker="o", s=18, label="X")
        self.residual_axis.scatter(z, result.wy_um - predicted_y, color=t.y_axis, marker="s", s=18, label="Y")
        self.residual_axis.set_xlabel("Z position (mm)"); self.residual_axis.set_ylabel("Residual")
        self.residual_axis.grid(True, alpha=0.24)
        self.caustic_axis.tick_params(axis="x", labelbottom=True, pad=4)
        self.caustic_axis.set_xlim(*padded_data_limits(z, minimum_padding=0.5))
        self._style_figure(self.caustic_figure)
        self.caustic_figure.subplots_adjust(left=0.11, right=0.97, top=0.92, bottom=0.11, hspace=0.26)
        self.caustic_canvas.draw_idle()
        if self.selected_preview_index is not None:
            self._set_selected_caustic_point(self.selected_preview_index)

    def _mark_plot(self, key: str) -> None:
        self.last_plot_window = key

    def _on_caustic_click(self, event) -> None:
        if self.dataset is None or not self.dataset.has_image_previews or event.inaxes is not self.caustic_axis or event.xdata is None:
            return
        index = self.dataset.nearest_preview_index(float(event.xdata))
        self.selected_preview_index = index
        self._set_selected_caustic_point(index)
        self._show_2d_preview(index)

    def _set_selected_caustic_point(self, z_index: int) -> None:
        if self.dataset is None or self.current_series is None:
            return
        for artist in self.selected_point_artists:
            try:
                artist.remove()
            except ValueError:
                pass
        self.selected_point_artists.clear()
        z_value = self.dataset.z_fit_mm[int(z_index)]
        series_index = int(np.argmin(np.abs(self.current_series.z_mm - z_value)))
        self.selected_point_artists.extend(
            [
                self.caustic_axis.plot([self.current_series.z_mm[series_index]], [self.current_series.wx_um[series_index]], marker="x", ms=11, mew=2.2, color=self.tokens.text, linestyle="none", zorder=7)[0],
                self.caustic_axis.plot([self.current_series.z_mm[series_index]], [self.current_series.wy_um[series_index]], marker="x", ms=11, mew=2.2, color=self.tokens.text, linestyle="none", zorder=7)[0],
            ]
        )
        self.caustic_canvas.draw_idle()

    def _reset_image_views(self) -> None:
        self.spot_figure.clear(); self.spot_axis = self.spot_figure.add_axes((0.0, 0.0, 1.0, 1.0))
        self._spot_extent = None; self._spot_scale_um_per_px = None; self._spot_drag_state = None
        self.spot_canvas.unsetCursor()
        self.spot_image_artist = None; self.spot_colorbar = None; self.spot_x_center_line = None; self.spot_y_center_line = None
        self.spot_axis.set_axis_off(); self.spot_canvas.draw_idle()
        self.beam3d_point_artist = None
        self.beam3d_figure.clear(); self.beam3d_axis = self.beam3d_figure.add_axes((0.0, 0.0, 1.0, 1.0), projection=ResponsiveAxes3D.name)
        self.beam3d_axis.set_axis_off(); self.beam3d_canvas.draw_idle()

    def _show_2d_preview(self, index: int, *, update_status: bool = True) -> None:
        dataset = self.dataset
        if dataset is None or not dataset.has_image_previews:
            return
        index = int(np.clip(index, 0, dataset.row_count - 1)); self.selected_preview_index = index
        image = dataset.preview_images[index]
        extent = dataset.preview_extents_um[index]
        self.spot_figure.clear(); self.spot_axis = self.spot_figure.add_axes((0.0, 0.0, 1.0, 1.0))
        vmax = max(float(np.max(image)), 1.0)
        self.spot_image_artist = self.spot_axis.imshow(image, cmap=OSI_RAINBOW, origin="lower", extent=extent, norm=PowerNorm(gamma=0.65, vmin=0.0, vmax=vmax), interpolation="nearest", aspect="auto")
        self.spot_colorbar = None
        self.spot_x_center_line = self.spot_axis.axvline(0.0, color=self.tokens.x_axis, lw=0.9, alpha=0.92)
        self.spot_y_center_line = self.spot_axis.axhline(0.0, color=self.tokens.y_axis, lw=0.9, alpha=0.92)
        self._spot_extent = tuple(float(value) for value in extent)
        self._spot_scale_um_per_px = None; self._spot_drag_state = None
        self.spot_axis.set_axis_off(); self.spot_axis.margins(0)
        self._style_figure(self.spot_figure); self._reset_2d_view(draw=False); self.spot_canvas.draw_idle()
        if update_status: self._set_status("Selected 2D beam", "accent")

    def _apply_2d_view(self, center_x: float, center_y: float, scale: float) -> None:
        """Apply an isotropic, image-bounded 2D viewport."""
        if self._spot_extent is None:
            return
        x0, x1, y0, y1 = self._spot_extent
        full_width, full_height = x1 - x0, y1 - y0
        pixel_width = max(float(self.spot_axis.bbox.width), 1.0)
        pixel_height = max(float(self.spot_axis.bbox.height), 1.0)
        home_scale = min(full_width / pixel_width, full_height / pixel_height)
        active_scale = float(np.clip(scale, home_scale * 0.02, home_scale * 4.0))
        view_width, view_height = pixel_width * active_scale, pixel_height * active_scale
        half_width, half_height = 0.5 * view_width, 0.5 * view_height
        center_x = (
            0.5 * (x0 + x1)
            if view_width >= full_width
            else float(np.clip(center_x, x0 + half_width, x1 - half_width))
        )
        center_y = (
            0.5 * (y0 + y1)
            if view_height >= full_height
            else float(np.clip(center_y, y0 + half_height, y1 - half_height))
        )
        self.spot_axis.set_xlim(center_x - half_width, center_x + half_width)
        self.spot_axis.set_ylim(center_y - half_height, center_y + half_height)
        self._spot_scale_um_per_px = active_scale

    def _reset_2d_view(self, *, draw: bool = True) -> None:
        if self._spot_extent is None:
            return
        x0, x1, y0, y1 = self._spot_extent
        pixel_width = max(float(self.spot_axis.bbox.width), 1.0)
        pixel_height = max(float(self.spot_axis.bbox.height), 1.0)
        scale = min((x1 - x0) / pixel_width, (y1 - y0) / pixel_height)
        self._apply_2d_view(0.5 * (x0 + x1), 0.5 * (y0 + y1), scale)
        if draw:
            self.spot_canvas.draw_idle()

    def _on_2d_scroll(self, event) -> None:
        if event.inaxes is not self.spot_axis or self._spot_extent is None or event.button not in ("up", "down"):
            return
        current_scale = self._spot_scale_um_per_px
        if current_scale is None:
            self._reset_2d_view(draw=False); current_scale = self._spot_scale_um_per_px
        if current_scale is None:
            return
        new_scale = current_scale * (0.82 if event.button == "up" else 1.22)
        xlim, ylim = self.spot_axis.get_xlim(), self.spot_axis.get_ylim()
        anchor_x = float(event.xdata if event.xdata is not None else 0.5 * sum(xlim))
        anchor_y = float(event.ydata if event.ydata is not None else 0.5 * sum(ylim))
        ratio = new_scale / current_scale
        center_x = anchor_x + (0.5 * sum(xlim) - anchor_x) * ratio
        center_y = anchor_y + (0.5 * sum(ylim) - anchor_y) * ratio
        self._apply_2d_view(center_x, center_y, new_scale)
        self.spot_canvas.draw_idle()

    def _on_2d_drag_start(self, event) -> None:
        if event.inaxes is not self.spot_axis or self._spot_extent is None:
            return
        if event.dblclick and event.button in (1, 2):
            self._reset_2d_view(); return
        if event.button != 1:
            return
        self._spot_drag_state = (float(event.x), float(event.y), self.spot_axis.get_xlim(), self.spot_axis.get_ylim())
        self.spot_canvas.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _on_2d_drag_move(self, event) -> None:
        if self._spot_drag_state is None or self._spot_scale_um_per_px is None:
            return
        start_x, start_y, start_xlim, start_ylim = self._spot_drag_state
        delta_x = (float(event.x) - start_x) * self._spot_scale_um_per_px
        delta_y = (float(event.y) - start_y) * self._spot_scale_um_per_px
        center_x = 0.5 * sum(start_xlim) - delta_x
        center_y = 0.5 * sum(start_ylim) - delta_y
        self._apply_2d_view(center_x, center_y, self._spot_scale_um_per_px)
        self.spot_canvas.draw_idle()

    def _on_2d_drag_end(self, _event) -> None:
        if self._spot_drag_state is not None:
            self._spot_drag_state = None
            self.spot_canvas.unsetCursor()

    def _on_2d_resize(self, _event) -> None:
        if self._spot_extent is None or self._spot_scale_um_per_px is None:
            return
        xlim, ylim = self.spot_axis.get_xlim(), self.spot_axis.get_ylim()
        self._apply_2d_view(0.5 * sum(xlim), 0.5 * sum(ylim), self._spot_scale_um_per_px)
        self.spot_canvas.draw_idle()

    @staticmethod
    def _unique_preview_indices(z_values: np.ndarray) -> np.ndarray:
        _unique, indices = np.unique(np.round(np.asarray(z_values, dtype=float), 9), return_index=True)
        return indices[np.argsort(np.asarray(z_values)[indices])]

    @classmethod
    def _display_preview_indices(cls, z_values: np.ndarray, maximum: int = 7) -> np.ndarray:
        """Return an even, endpoint-preserving subset for interactive 3D display."""
        unique = cls._unique_preview_indices(z_values)
        if unique.size <= maximum:
            return unique
        positions = np.linspace(0, unique.size - 1, maximum)
        return unique[np.rint(positions).astype(int)]

    @staticmethod
    def _beam3d_image_colors(intensity: np.ndarray, *, endpoint: bool) -> np.ndarray:
        """Opaque image colors keep overlapping slices from washing out."""
        normalized = np.clip(np.asarray(intensity, dtype=float), 0.0, 1.0)
        colors = np.asarray(OSI_RAINBOW(np.power(normalized, 0.65)))
        colors[..., 3] = 1.0
        return colors

    @classmethod
    def _sample_3d_points(
        cls,
        preview: np.ndarray,
        extent: tuple[float, float, float, float],
        z_position: float,
        *,
        endpoint: bool,
        maximum_points: int = 1_400,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Sample a regular grid over the beam, not over empty sensor margins.

        The support filter only affects this preview; measurement data is never
        modified. Avoid row-major thinning, which produced stripes and holes.
        """
        from scipy.ndimage import gaussian_filter, map_coordinates

        height, width = preview.shape
        signal = np.clip(np.asarray(preview, dtype=float) / 255.0, 0.0, 1.0)
        support = gaussian_filter(signal, sigma=0.8)
        threshold = max(0.035 * float(support.max()), 0.02)
        support_rows, support_cols = np.nonzero(support >= threshold)
        if not support_rows.size:
            empty = np.empty(0, dtype=float)
            return empty, empty.copy(), empty.copy(), np.empty((0, 4)), empty.copy()
        grid_side = max(1, int(np.sqrt(maximum_points)))
        rows = np.linspace(support_rows.min(), support_rows.max(), min(height, grid_side))
        cols = np.linspace(support_cols.min(), support_cols.max(), min(width, grid_side))
        col_grid, row_grid = np.meshgrid(cols, rows)
        intensity = map_coordinates(signal, [row_grid, col_grid], order=1)
        sampled_support = map_coordinates(support, [row_grid, col_grid], order=1)
        x0, x1, y0, y1 = (float(value) for value in extent)
        x_grid = x0 + (col_grid + 0.5) * (x1 - x0) / width
        y_grid = y0 + (row_grid + 0.5) * (y1 - y0) / height
        candidates = np.flatnonzero((sampled_support >= threshold) & (intensity >= threshold))
        selected_intensity = intensity.ravel()[candidates]
        longitudinal = np.full(candidates.size, float(z_position), dtype=float)
        colors = cls._beam3d_image_colors(selected_intensity, endpoint=endpoint)
        # Uniform small pixels preserve the intensity profile; large bright dots
        # previously swelled the core and obscured the neighbouring samples.
        sizes = np.full(candidates.size, 6.25)
        return longitudinal, x_grid.ravel()[candidates], y_grid.ravel()[candidates], colors, sizes

    def _build_3d_beam(self) -> None:
        dataset = self.dataset
        if dataset is None or not dataset.has_image_previews:
            return
        if self.beam3d_dataset_token == id(dataset):
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor); self._set_status("Composing interactive 3D point cloud…", "accent")
        try:
            self.beam3d_figure.clear(); axis = self.beam3d_figure.add_axes((0.0, 0.0, 1.0, 1.0), projection=ResponsiveAxes3D.name); self.beam3d_axis = axis
            preview_indices = self._display_preview_indices(dataset.z_fit_mm)
            longitudinal_chunks: list[np.ndarray] = []
            x_chunks: list[np.ndarray] = []
            y_chunks: list[np.ndarray] = []
            color_chunks: list[np.ndarray] = []
            size_chunks: list[np.ndarray] = []
            for slice_number, index in enumerate(preview_indices):
                endpoint = slice_number in {0, len(preview_indices) - 1}
                preview = dataset.preview_images[int(index)]
                extent = dataset.preview_extents_um[int(index)]
                z_position = float(dataset.z_fit_mm[int(index)])
                longitudinal, x_points, y_points, colors, sizes = self._sample_3d_points(
                    preview, extent, z_position, endpoint=endpoint
                )
                longitudinal_chunks.append(longitudinal); x_chunks.append(x_points); y_chunks.append(y_points)
                color_chunks.append(colors); size_chunks.append(sizes)
            longitudinal = np.concatenate(longitudinal_chunks)
            x_points = np.concatenate(x_chunks); y_points = np.concatenate(y_chunks)
            colors = np.concatenate(color_chunks); sizes = np.concatenate(size_chunks)
            self.beam3d_point_artist = axis.scatter(
                longitudinal, x_points, y_points,
                c=colors, s=6.25, marker="o", linewidths=0, depthshade=False,
            )
            extents = np.asarray(dataset.preview_extents_um)
            x_limits = padded_data_limits(x_points if x_points.size else extents[:, :2].ravel(), minimum_padding=2.0)
            y_limits = padded_data_limits(y_points if y_points.size else extents[:, 2:].ravel(), minimum_padding=2.0)
            # Use the same physical scale on both transverse axes.
            transverse_span = max(np.ptp(x_limits), np.ptp(y_limits))
            axis.set_xlim(*padded_data_limits(dataset.z_fit_mm, minimum_padding=0.2))
            axis.set_ylim(np.mean(x_limits) - transverse_span / 2, np.mean(x_limits) + transverse_span / 2)
            axis.set_zlim(np.mean(y_limits) - transverse_span / 2, np.mean(y_limits) + transverse_span / 2)
            axis.set_proj_type("ortho")
            axis.set_box_aspect((5.5, 1.0, 1.0), zoom=1.2); axis.view_init(elev=12, azim=-45); axis.set_axis_off()
            self._style_figure(self.beam3d_figure); self.beam3d_canvas.draw_idle(); self.beam3d_dataset_token = id(dataset)
            self._set_status(f"3D point cloud ready · {longitudinal.size} points", "success")
        except Exception as exc:
            self.beam3d_dataset_token = None; self._set_status(f"3D beam display failed: {exc}", "error"); QMessageBox.critical(self, "3D beam display failed", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def _on_3d_scroll(self, event) -> None:
        if event.inaxes is not self.beam3d_axis or event.button not in ("up", "down"):
            return
        factor = 0.82 if event.button == "up" else 1.22
        def scaled(limits: tuple[float, float]) -> tuple[float, float]:
            center = 0.5 * (limits[0] + limits[1]); half = 0.5 * (limits[1] - limits[0]) * factor
            return center - half, center + half
        self.beam3d_axis.set_xlim(*scaled(self.beam3d_axis.get_xlim())); self.beam3d_axis.set_ylim(*scaled(self.beam3d_axis.get_ylim())); self.beam3d_axis.set_zlim(*scaled(self.beam3d_axis.get_zlim())); self.beam3d_canvas.draw_idle()

    # Full-angle scan
    def compute_full_angle_scan(self) -> None:
        if self.dataset is None:
            QMessageBox.information(self, "No data", "Load a CSV or image sequence first.")
            return
        started = time.perf_counter(); QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.progress.setRange(0, 100); self._set_status("Building M² angle scan…", "accent")
        try:
            rows = self._m2_scan_frame()
            self._angle_scan_frame_cache = rows
        except Exception as exc:
            self._set_status(f"Angle scan failed: {exc}", "error"); QMessageBox.critical(self, "M² angle scan failed", str(exc)); return
        finally:
            QApplication.restoreOverrideCursor()
        good = rows[rows["Fit Error"].eq("")]
        self.m2_angle_axis.clear(); self.waist_angle_axis.clear()
        if not good.empty:
            self.m2_angle_axis.plot(good["Angle (deg)"], good["M2 X"], color=self.tokens.x_axis, marker="o", ms=2.5, label="M² X")
            self.m2_angle_axis.plot(good["Angle (deg)"], good["M2 Y"], color=self.tokens.y_axis, marker="s", ms=2.5, label="M² Y")
            guide = self.m2_angle_axis.axhline(1.0, color=self.tokens.secondary, ls=":", lw=1); guide.set_gid("guide")
            self.waist_angle_axis.plot(good["Angle (deg)"], good["W02 X (um)"], color=self.tokens.x_axis, label="W02 X")
            self.waist_angle_axis.plot(good["Angle (deg)"], good["W02 Y (um)"], color=self.tokens.y_axis, label="W02 Y")
        for axis in (self.m2_angle_axis, self.waist_angle_axis):
            axis.grid(True, alpha=0.25); axis.legend(loc="best", fontsize=8)
        self.m2_angle_axis.set_ylabel("M²"); self.m2_angle_axis.set_title("Automatic M² refit at each available angle", fontsize=11)
        self.waist_angle_axis.set_xlabel("XY relative deviation δ (°)"); self.waist_angle_axis.set_ylabel("Post-lens W02 (µm)")
        self._mark_current_angle(draw=False); self._style_figure(self.angle_figure)
        self.angle_figure.subplots_adjust(left=0.11, right=0.97, top=0.92, bottom=0.11, hspace=0.30)
        self.angle_canvas.draw_idle()
        self.progress.setValue(100)
        self._set_status(f"M² angle scan ready · {len(good)}/{len(rows)} fits · {time.perf_counter() - started:.1f}s", "success" if len(good) == len(rows) else "warning")

    def _mark_current_angle(self, draw: bool = True) -> None:
        if not hasattr(self, "m2_angle_axis"):
            return
        for axis in (self.m2_angle_axis, self.waist_angle_axis):
            for line in list(axis.lines):
                if line.get_gid() == "current-angle": line.remove()
            if axis.lines:
                marker = axis.axvline(self.angle_spin.value(), color=self.tokens.text, ls="--", lw=1.0, alpha=0.65); marker.set_gid("current-angle")
        if draw: self.angle_canvas.draw_idle()

    def _m2_scan_frame(self) -> pd.DataFrame:
        if self.dataset is None:
            raise ValueError("No dataset is loaded.")
        rows: list[dict[str, object]] = []; angles = self.dataset.available_angles
        for index, angle in enumerate(angles, start=1):
            try:
                result = self._fit_angle(angle)
            except Exception as exc:
                rows.append({"Angle (deg)": angle, "Fit Error": str(exc)})
            else:
                rows.append({
                    "Angle (deg)": angle,
                    "M2 X": result.fit_x.m2, "M2 Y": result.fit_y.m2,
                    "BPP X (mm mrad)": result.real_x.bpp_mm_mrad, "BPP Y (mm mrad)": result.real_y.bpp_mm_mrad,
                    "Real Divergence X (mrad)": result.real_x.divergence_full_mrad, "Real Divergence Y (mrad)": result.real_y.divergence_full_mrad,
                    "W01 X (um)": result.real_x.waist_width_um, "W01 Y (um)": result.real_y.waist_width_um,
                    "Z01 X Display (mm)": result.real_x.waist_location_display_mm, "Z01 Y Display (mm)": result.real_y.waist_location_display_mm,
                    "Z01 X Principal (mm)": result.real_x.waist_location_principal_mm, "Z01 Y Principal (mm)": result.real_y.waist_location_principal_mm,
                    "ZR1 X (mm)": result.real_x.rayleigh_length_mm, "ZR1 Y (mm)": result.real_y.rayleigh_length_mm,
                    "Real Astigmatism": result.astigmatism_real, "Real Asymmetry": result.asymmetry_real,
                    "W02 X (um)": result.fit_x.waist_width_um, "W02 Y (um)": result.fit_y.waist_width_um,
                    "Z02 X (mm)": result.fit_x.waist_location_mm, "Z02 Y (mm)": result.fit_y.waist_location_mm,
                    "Theta2 X (mrad)": result.fit_x.divergence_full_mrad, "Theta2 Y (mrad)": result.fit_y.divergence_full_mrad,
                    "ZR2 X (mm)": result.fit_x.rayleigh_length_mm, "ZR2 Y (mm)": result.fit_y.rayleigh_length_mm,
                    "After-lens Astigmatism": result.astigmatism_after_lens, "After-lens Asymmetry": result.asymmetry_after_lens,
                    "Effective Focal Length (mm)": result.focal_length_mm,
                    "Wf2 X (um)": result.real_x.wf2_um, "Wf2 Y (um)": result.real_y.wf2_um,
                    "Wf2 Source X": result.real_x.divergence_source, "Wf2 Source Y": result.real_y.divergence_source,
                    "Fit RMSE X (um)": result.fit_x.rmse_um, "Fit RMSE Y (um)": result.fit_y.rmse_um,
                    "Fit R2 X": result.fit_x.r_squared, "Fit R2 Y": result.fit_y.r_squared,
                    "ISO X": result.iso_x.iso_basic_pass, "ISO Y": result.iso_y.iso_basic_pass,
                    "ISO Near X": result.iso_x.near_count, "ISO Near Y": result.iso_y.near_count,
                    "ISO Far-left X": result.iso_x.far_left_count, "ISO Far-left Y": result.iso_y.far_left_count,
                    "ISO Far-right X": result.iso_x.far_right_count, "ISO Far-right Y": result.iso_y.far_right_count,
                    "Fit Error": "",
                })
            self.progress.setValue(round(index * 100 / max(len(angles), 1)))
            if index == 1 or index % 5 == 0 or index == len(angles):
                self._set_status(f"Fitting M² angle {angle:+d}° · {index}/{len(angles)}", "accent"); QApplication.processEvents()
        return pd.DataFrame(rows)

    # Results and diagnostics
    def _number(self, value: float, decimals: int = 3) -> str:
        if not np.isfinite(value): return "—"
        return f"{value:.3e}" if self.sci_radio.isChecked() else f"{value:,.{decimals}f}"

    def _add_result(self, parent: QTreeWidgetItem, name: str, value: float, unit: str = "", *, decimals: int = 3, iso: bool = False, warning: bool = False, formula_key: str | None = None) -> None:
        item = QTreeWidgetItem(parent, [f"{name}  ISO" if iso else name, self._number(float(value), decimals), unit])
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if formula_key is not None:
            item.setData(0, Qt.ItemDataRole.UserRole, formula_key)
            item.setToolTip(0, "Double-click to view the calculation formula")
        if warning:
            for column in range(3): item.setForeground(column, QColor(self.tokens.error))
        elif iso:
            item.setForeground(0, QColor(self.tokens.accent))

    def _show_result_formula(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        formula_key = item.data(0, Qt.ItemDataRole.UserRole)
        if not formula_key or formula_key not in LASER_FORMULAS:
            return
        parameter_name = item.text(0).replace("  ISO", "")
        dialog = FormulaDialog(
            parameter_name,
            item.text(1),
            item.text(2),
            str(formula_key),
            self,
        )
        dialog.exec()

    def _show_measurement_principle(self) -> None:
        dialog = getattr(self, "principle_dialog", None)
        if dialog is None:
            image_path = Path(__file__).with_name("assets") / "measurement_principle.png"
            dialog = MeasurementPrincipleDialog(image_path, self)
            self.principle_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def refresh_results(self) -> None:
        result, series = self.current_m2, self.current_series
        if result is None or series is None: return
        self.results_tree.clear()
        def group(title: str, expanded: bool = True) -> QTreeWidgetItem:
            node = QTreeWidgetItem(self.results_tree, [title, "", ""]); node.setExpanded(expanded)
            font = node.font(0); font.setBold(True)
            for col in range(3): node.setFont(col, font)
            return node
        angle = group("Current Angle"); self._add_result(angle, "Relative Deviation δ", series.angle_deg, "deg", decimals=0); self._add_result(angle, "X Direction", 90-series.angle_deg, "deg", decimals=0); self._add_result(angle, "Y Direction", -series.angle_deg, "deg", decimals=0); self._add_result(angle, "Z Positions", series.count, decimals=0)
        frame = group("Frame (Quantitative)"); self._add_result(frame, "D4σ X", series.wx_um[-1], "µm"); self._add_result(frame, "D4σ Y", series.wy_um[-1], "µm"); self._add_result(frame, "Z Location", series.z_mm[-1], "mm")
        laser = group("Laser")
        self._add_result(laser, "M² X", result.fit_x.m2, iso=True, warning=result.fit_x.m2 < 1.0, formula_key="M²"); self._add_result(laser, "M² Y", result.fit_y.m2, iso=True, warning=result.fit_y.m2 < 1.0, formula_key="M²")
        for name, value, unit in (("BPP X", result.real_x.bpp_mm_mrad, "mm mrad"), ("BPP Y", result.real_y.bpp_mm_mrad, "mm mrad"), ("Divergence X", result.real_x.divergence_full_mrad, "mrad"), ("Divergence Y", result.real_y.divergence_full_mrad, "mrad"), ("Waist Width X", result.real_x.waist_width_um, "µm"), ("Waist Width Y", result.real_y.waist_width_um, "µm"), ("Waist Location X", result.real_x.waist_location_display_mm, "mm"), ("Waist Location Y", result.real_y.waist_location_display_mm, "mm"), ("Rayleigh Length X", result.real_x.rayleigh_length_mm, "mm"), ("Rayleigh Length Y", result.real_y.rayleigh_length_mm, "mm")):
            formula_key = name.rsplit(" ", 1)[0]
            self._add_result(laser, name, value, unit, iso=name not in {"BPP X", "BPP Y"}, formula_key=formula_key)
        self._add_result(laser, "Astigmatism", result.astigmatism_real, formula_key="Astigmatism"); self._add_result(laser, "Asymmetry", result.asymmetry_real, formula_key="Asymmetry")
        after = group("After Lens")
        for name, value, unit in (("Divergence X", result.fit_x.divergence_full_mrad, "mrad"), ("Divergence Y", result.fit_y.divergence_full_mrad, "mrad"), ("Waist Width X", result.fit_x.waist_width_um, "µm"), ("Waist Width Y", result.fit_y.waist_width_um, "µm"), ("Waist Location X", result.fit_x.waist_location_mm, "mm"), ("Waist Location Y", result.fit_y.waist_location_mm, "mm"), ("Rayleigh Length X", result.fit_x.rayleigh_length_mm, "mm"), ("Rayleigh Length Y", result.fit_y.rayleigh_length_mm, "mm")):
            self._add_result(after, name, value, unit, iso=True)
        self._add_result(after, "Astigmatism", result.astigmatism_after_lens); self._add_result(after, "Asymmetry", result.asymmetry_after_lens)
        stats = group("Statistics", False)
        self._add_result(stats, "Effective Focal Length", result.focal_length_mm, "mm", decimals=6); self._add_result(stats, "Wf2 X", result.real_x.wf2_um, "µm"); self._add_result(stats, "Wf2 Y", result.real_y.wf2_um, "µm"); self._add_result(stats, "Fit RMSE X", result.fit_x.rmse_um, "µm", decimals=4); self._add_result(stats, "Fit RMSE Y", result.fit_y.rmse_um, "µm", decimals=4); self._add_result(stats, "Fit R² X", result.fit_x.r_squared, decimals=8); self._add_result(stats, "Fit R² Y", result.fit_y.r_squared, decimals=8)

    def _update_data_table(self, series: AngleSeries) -> None:
        self.data_table.setRowCount(series.count)
        for row in range(series.count):
            stage = "" if not np.isfinite(series.z_stage_mm[row]) else f"{series.z_stage_mm[row]:.6f}"
            values = (str(row+1), f"{series.z_mm[row]:.6f}", stage, f"{series.angle_deg:+d}", f"{series.wx_um[row]:.6f}", f"{series.wy_um[row]:.6f}", str(int(series.frame_counts[row])), series.filenames[row], series.warnings[row])
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col < 7: item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if col == 8 and value: item.setForeground(QColor(self.tokens.warning))
                self.data_table.setItem(row, col, item)

    def _update_diagnostics(self, result: m2_core.AnalysisResult, series: AngleSeries) -> None:
        cfg = result.config
        lines = [f"Source: {self.dataset.source_label if self.dataset else ''}", f"Source type: {self.dataset.source_kind if self.dataset else ''}", f"Selected relative deviation: {series.angle_deg:+d} deg", f"Axis mapping: X={90-series.angle_deg:+d} deg, Y={-series.angle_deg:+d} deg", f"Z basis: {self.dataset.z_basis if self.dataset else ''}", "", "ISO 11146 sampling distribution", "--------------------------------"]
        for check in (result.iso_x, result.iso_y):
            lines.append(f"{check.axis}: total={check.total}, near={check.near_count}, far-left={check.far_left_count}, far-right={check.far_right_count}, basic={'PASS' if check.iso_basic_pass else 'FAIL'}, balanced={'PASS' if check.balanced_recommendation_pass else 'WARN'}")
        lines.extend(["", "Lens coordinate translation", "----------------------------------", f"Mode: {'Automatic' if cfg.automatic_mode else 'Manual'}", f"Z Lens = {cfg.z_lens_mm:.6f} mm", f"Center Thickness CT = {cfg.center_thickness_mm:.6f} mm", f"Equation 9 correction (-Zlens + CT) = {cfg.z01_reference_correction_mm:.6f} mm", f"Laser Location = {cfg.laser_location_mm if cfg.laser_location_mm is not None else 0.0:.6f} mm", f"Effective focal length = {result.focal_length_mm:.6f} mm", "", "Fit quality", "-----------", f"X: RMSE={result.fit_x.rmse_um:.6f} µm, R²={result.fit_x.r_squared:.9f}", f"Y: RMSE={result.fit_y.rmse_um:.6f} µm, R²={result.fit_y.r_squared:.9f}", f"X focal-width source: {result.real_x.divergence_source}", f"Y focal-width source: {result.real_y.divergence_source}"])
        warnings = [f"{series.filenames[i]}: {warning}" for i, warning in enumerate(series.warnings) if warning]
        if warnings: lines.extend(["", "Image/data warnings", "-------------------", *warnings])
        self.diagnostics.setPlainText("\n".join(lines))

    # Settings, export, and messages
    def open_optics_settings(self) -> None:
        dialog = OpticsDialog(self.analysis_config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.analysis_config = dialog.analysis_config_value()
        except Exception as exc:
            QMessageBox.critical(self, "Invalid Optical Parameter", str(exc))
            return
        self.fit_cache.clear(); self._angle_scan_frame_cache = None
        if self.dataset is not None:
            self._update_current_angle(force=True)
            QTimer.singleShot(20, self.compute_full_angle_scan)
        self._set_status("Optics settings applied", "success")

    def _safe_stem(self) -> str:
        source = self.dataset.source_label if self.dataset else "caustiq"
        stem = Path(source).stem or "caustiq"
        return "".join(char if char.isalnum() or char in "-_" else "_" for char in stem)

    def export_current_angle(self) -> None:
        if self.current_series is None:
            QMessageBox.information(self, "No data", "Load data before exporting."); return
        suggested = f"{self._safe_stem()}_angle_{self.current_series.angle_deg:+d}_z_data.csv"
        filename, _ = QFileDialog.getSaveFileName(self, "Export current-angle Z data", suggested, "CSV files (*.csv)")
        if filename:
            self.current_series.to_frame().to_csv(filename, index=False, encoding="utf-8-sig"); self._set_status(f"Exported current-angle data: {filename}", "success")

    def export_all_angles(self) -> None:
        if self.dataset is None:
            QMessageBox.information(self, "No data", "Load data before exporting."); return
        filename, _ = QFileDialog.getSaveFileName(self, "Export all-angle D4σ long table", f"{self._safe_stem()}_all_angles_d4sigma.csv", "CSV files (*.csv)")
        if filename:
            self.dataset.all_angles_long_frame().to_csv(filename, index=False, encoding="utf-8-sig"); self._set_status(f"Exported all-angle D4σ data: {filename}", "success")

    def export_m2_scan(self) -> None:
        if self.dataset is None:
            QMessageBox.information(self, "No data", "Load data before exporting."); return
        try:
            frame = self._angle_scan_frame_cache if self._angle_scan_frame_cache is not None else self._m2_scan_frame()
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc)); return
        filename, _ = QFileDialog.getSaveFileName(self, "Export M² angle scan", f"{self._safe_stem()}_m2_angle_scan.csv", "CSV files (*.csv)")
        if filename:
            frame.to_csv(filename, index=False, encoding="utf-8-sig"); self._set_status(f"Exported M² angle scan: {filename}", "success")

    def export_plot(self) -> None:
        if self.current_m2 is None:
            QMessageBox.information(self, "No plot", "Load and fit data before exporting."); return
        if self.last_plot_window == "spot": suffix, figure = "beam_2d", self.spot_figure
        elif self.last_plot_window == "beam3d": suffix, figure = "beam_3d", self.beam3d_figure
        elif self.plot_tabs.currentIndex() == 1: suffix, figure = "angle_scan", self.angle_figure
        else: suffix, figure = "caustic", self.caustic_figure
        filename, _ = QFileDialog.getSaveFileName(self, "Export selected plot", f"{self._safe_stem()}_{suffix}.png", "PNG image (*.png);;PDF (*.pdf);;SVG (*.svg)")
        if filename:
            figure.savefig(filename, dpi=220, bbox_inches="tight"); self._set_status(f"Exported plot: {filename}", "success")

    def _set_status(self, text: str, role: str) -> None:
        colors = {"accent": self.tokens.accent, "success": self.tokens.success, "warning": self.tokens.warning, "error": self.tokens.error, "secondary": self.tokens.secondary}
        self.status_label.setText(text); self.status_dot.setStyleSheet(f"color: {colors.get(role, self.tokens.secondary)}; background: transparent;")

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "About Caustiq",
            f"{APP_NAME} v{APP_VERSION} · {APP_SUBTITLE}\n"
            "Independent beam-propagation fitting and image-derived XY angle analysis.\n\n"
            "Angles: −90° to +90°, step 1°. At 0°, X is vertical and Y is horizontal.\n\n"
            "Every panel can be moved, docked, floated, hidden, and restored from the Window menu. Light, Dark, and System appearances use opaque measurement surfaces and preserve X/Y data colors.\n\n"
            "Authors: Terrence GUO · OpenAI Codex\n\n"
            "MIT-licensed project code; third-party components retain their own licenses.\n\n"
            "Not affiliated with, endorsed by, or certified by Ophir, Spiricon, or their affiliates. "
            "Third-party names identify reference products or data formats only. "
            "No metrology certification or non-infringement assurance is provided. See the manual's Legal Notice.",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the PySide6 Caustiq GUI.")
    parser.add_argument("source", nargs="?", type=Path, help="Optional CSV, image, folder, or ZIP")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = QApplication.instance() or QApplication(sys.argv)
    configure_application_icon(app)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setApplicationDisplayName(f"{APP_NAME} v{APP_VERSION} · {APP_SUBTITLE}")
    app.setOrganizationName("Caustiq")
    app.setStyle("Fusion")
    window = CaustiqWindow(args.source)
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()

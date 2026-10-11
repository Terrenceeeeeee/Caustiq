from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from matplotlib.colors import to_hex
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d.art3d import Path3DCollection
from PIL import Image
from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QApplication, QDialog, QDockWidget, QLabel


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from angle_dataset import ANGLE_SCAN_DEGREES, analyze_image_set, load_csv_dataset
from image_beam_core import (
    AnalysisSettings,
    analyze_beam,
    relative_axes_angles_deg,
    relative_xy_diameters_um,
    relative_xy_scan,
)
from image_io import ImageSet
from measurement_config import MeasurementConfig
import m2_core
from qt_app import APP_VERSION, LASER_FORMULAS, CaustiqWindow, ResponsiveAxes3D, padded_data_limits, configure_application_icon
from ui_theme import DARK


class UnifiedCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        settings_dir = PROJECT / ".test-cache" / "settings"
        settings_dir.mkdir(parents=True, exist_ok=True)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(settings_dir))
        cls.qt_app = QApplication.instance() or QApplication([])
        cls.qt_app.setStyle("Fusion")

    def test_application_icon_is_inherited_by_dialogs(self) -> None:
        previous = self.qt_app.windowIcon()
        dialog = None
        try:
            configure_application_icon(self.qt_app)
            self.assertFalse(self.qt_app.windowIcon().isNull())
            for size in (16, 32, 48, 256):
                self.assertFalse(self.qt_app.windowIcon().pixmap(size, size).isNull())
            dialog = QDialog()
            self.assertFalse(dialog.windowIcon().isNull())
            self.assertEqual(dialog.windowIcon().cacheKey(), self.qt_app.windowIcon().cacheKey())
        finally:
            if dialog is not None:
                dialog.close()
            self.qt_app.setWindowIcon(previous)

    def test_dark_theme_styles_plot_without_changing_series_identity(self) -> None:
        window = CaustiqWindow()
        window.tokens = DARK
        figure = Figure(figsize=(4, 3), dpi=80)
        axis = figure.add_subplot(111)
        line = axis.plot([0.0, 1.0], [0.0, 1.0], color=DARK.x_axis, label="X Fit")[0]
        axis.set_title("Dark theme")
        window._style_figure(figure)
        self.assertEqual(to_hex(figure.get_facecolor()), DARK.surface.lower())
        self.assertEqual(to_hex(axis.get_facecolor()), DARK.surface.lower())
        self.assertEqual(to_hex(line.get_color()), DARK.x_axis.lower())
        self.assertEqual(axis.title.get_color(), DARK.text)
        window.close()

    def test_caustic_x_limits_follow_current_z_data(self) -> None:
        lower, upper = padded_data_limits(np.asarray([360.0, 470.0]))
        self.assertAlmostEqual(lower, 355.6)
        self.assertAlmostEqual(upper, 474.4)

    def test_maximum_astigmatism_choice_ignores_non_finite_results(self) -> None:
        angle, value = CaustiqWindow._maximum_astigmatism_choice(
            [(-90, 0.12), (-45, float("nan")), (0, 0.31), (45, float("inf")), (90, 0.18)]
        )
        self.assertEqual(angle, 0)
        self.assertAlmostEqual(value, 0.31)

    def test_3d_colors_preserve_intensity_without_transparency(self) -> None:
        intensity = np.asarray([0.01, 0.25, 1.0])
        middle = CaustiqWindow._beam3d_image_colors(intensity, endpoint=False)
        endpoint = CaustiqWindow._beam3d_image_colors(intensity, endpoint=True)
        np.testing.assert_array_equal(middle[:, 3], 1.0)
        np.testing.assert_allclose(middle, endpoint)
        self.assertFalse(np.allclose(middle[0, :3], middle[-1, :3]))

    def test_relative_angle_convention(self) -> None:
        self.assertEqual(relative_axes_angles_deg(0), (90.0, 0.0))
        self.assertEqual(relative_axes_angles_deg(-10), (100.0, 10.0))
        self.assertEqual(relative_axes_angles_deg(10), (80.0, -10.0))

    def test_vector_angle_scan_matches_scalar_projection(self) -> None:
        yy, xx = np.indices((129, 129), dtype=float)
        image = (100.0 + 40000.0 * np.exp(-0.5 * (((xx - 63.0) / 9.0) ** 2 + ((yy - 66.0) / 14.0) ** 2))).astype(np.uint16)
        result = analyze_beam(image, AnalysisSettings())
        deviations = np.arange(-90, 91)
        vector_x, vector_y = relative_xy_scan(result, deviations)
        scalar = np.asarray([relative_xy_diameters_um(result, angle) for angle in deviations])
        np.testing.assert_allclose(vector_x, scalar[:, 0], rtol=0, atol=1e-10)
        np.testing.assert_allclose(vector_y, scalar[:, 1], rtol=0, atol=1e-10)

    def test_single_angle_sample_fits_expected_m2(self) -> None:
        dataset = load_csv_dataset(PROJECT / "sample_data" / "synthetic_single_angle.csv", MeasurementConfig())
        series = dataset.series(0)
        result = m2_core.analyze_arrays(series.z_mm, series.wx_um, series.wy_um)
        self.assertAlmostEqual(result.fit_x.m2, 1.1666594078, places=5)
        self.assertAlmostEqual(result.fit_y.m2, 1.2676936375, places=5)

    def test_matrix_and_long_sample_datasets_are_equivalent(self) -> None:
        config = MeasurementConfig()
        matrix = load_csv_dataset(PROJECT / "sample_data" / "synthetic_angle_matrix.csv", config)
        long_table = load_csv_dataset(PROJECT / "sample_data" / "synthetic_angle_long.csv", config)
        self.assertEqual(matrix.available_angles, tuple(range(-90, 91)))
        self.assertEqual(long_table.available_angles, tuple(range(-90, 91)))
        for angle in (-90, -37, 0, 41, 90):
            a, b = matrix.series(angle), long_table.series(angle)
            np.testing.assert_allclose(a.z_mm, b.z_mm, rtol=0, atol=1e-12)
            np.testing.assert_allclose(a.wx_um, b.wx_um, rtol=0, atol=1e-9)
            np.testing.assert_allclose(a.wy_um, b.wy_um, rtol=0, atol=1e-9)

    def test_sample_angle_scan_maximum_real_laser_astigmatism(self) -> None:
        dataset = load_csv_dataset(PROJECT / "sample_data" / "synthetic_angle_matrix.csv", MeasurementConfig())
        candidates = []
        for angle in dataset.available_angles:
            series = dataset.series(angle)
            result = m2_core.analyze_arrays(series.z_mm, series.wx_um, series.wy_um)
            candidates.append((angle, result.astigmatism_real))
        angle, value = CaustiqWindow._maximum_astigmatism_choice(candidates)
        self.assertIn(angle, dataset.available_angles)
        self.assertTrue(np.isfinite(value))
        self.assertGreater(value, 0.0)
        self.assertAlmostEqual(value, max(item[1] for item in candidates if np.isfinite(item[1])), places=10)

    def test_image_sequence_builds_181_angle_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            yy, xx = np.indices((129, 129), dtype=float)
            z_values = np.linspace(360.0, 470.0, 5)
            paths = []
            for index, z_value in enumerate(z_values):
                sigma_x = 7.0 + 0.7 * abs(index - 2)
                sigma_y = 9.0 + 0.5 * abs(index - 2)
                signal = 40000.0 * np.exp(-0.5 * (((xx - 64.0) / sigma_x) ** 2 + ((yy - 64.0) / sigma_y) ** 2))
                path = folder / f"beam_{index:02d}.tif"
                Image.fromarray(np.clip(signal + 200.0, 0, 65535).astype(np.uint16)).save(
                    path,
                    tiffinfo={270: json.dumps({"z_p_float_mm": float(z_value), "pixel_size_p_float_um": 3.45})},
                )
                paths.append(path)
            image_set = ImageSet.from_files(paths)
            try:
                dataset = analyze_image_set(image_set, MeasurementConfig(), AnalysisSettings())
            finally:
                image_set.close()
            np.testing.assert_array_equal(dataset.angles_deg, ANGLE_SCAN_DEGREES)
            self.assertEqual(dataset.wx_by_angle_um.shape, (181, 5))
            np.testing.assert_allclose(dataset.z_fit_mm, z_values + 10.0, atol=1e-12)
            self.assertTrue(dataset.has_image_previews)
            self.assertEqual(dataset.preview_images[0].dtype, np.uint8)
            window = CaustiqWindow()
            window.show()
            window._apply_dataset(dataset)
            self.qt_app.processEvents()
            self.assertTrue(window.spot_dock.isVisible())
            self.assertTrue(window.beam3d_dock.isVisible())
            self.assertNotIn(window.beam3d_dock, window.tabifiedDockWidgets(window.spot_dock))
            window._build_3d_beam()
            self.assertIsInstance(window.beam3d_axis, ResponsiveAxes3D)
            self.assertIsInstance(window.beam3d_point_artist, Path3DCollection)
            self.assertEqual(len(window.beam3d_axis.collections), 1)
            self.assertGreater(len(window.beam3d_point_artist._offsets3d[0]), 0)
            self.assertLessEqual(len(window.beam3d_point_artist._offsets3d[0]), len(z_values) * 1_400)
            self.assertFalse(window.spot_axis.axison)
            self.assertFalse(window.beam3d_axis.axison)
            self.assertEqual(window.spot_axis.get_position().bounds, (0.0, 0.0, 1.0, 1.0))
            self.assertEqual(window.beam3d_axis.get_position().bounds, (0.0, 0.0, 1.0, 1.0))
            self.assertIsNotNone(window.spot_x_center_line)
            self.assertIsNotNone(window.spot_y_center_line)
            self.assertEqual(tuple(window.spot_x_center_line.get_xdata()), (0.0, 0.0))
            self.assertEqual(tuple(window.spot_y_center_line.get_ydata()), (0.0, 0.0))
            view_ratio = np.ptp(window.spot_axis.get_xlim()) / np.ptp(window.spot_axis.get_ylim())
            pixel_ratio = window.spot_axis.bbox.width / window.spot_axis.bbox.height
            self.assertAlmostEqual(view_ratio, pixel_ratio, places=6)
            initial_scale = window._spot_scale_um_per_px
            x_center = float(np.mean(window.spot_axis.get_xlim()))
            y_center = float(np.mean(window.spot_axis.get_ylim()))
            window._on_2d_scroll(SimpleNamespace(inaxes=window.spot_axis, button="up", xdata=x_center, ydata=y_center))
            self.assertLess(window._spot_scale_um_per_px, initial_scale)
            # A resized dock can initially show black margins. Panning is
            # intentionally clamped until zoom puts the image outside view.
            x0, x1, y0, y1 = window._spot_extent
            for _ in range(12):
                if np.ptp(window.spot_axis.get_xlim()) < x1 - x0 and np.ptp(window.spot_axis.get_ylim()) < y1 - y0:
                    break
                window._on_2d_scroll(SimpleNamespace(inaxes=window.spot_axis, button="up", xdata=x_center, ydata=y_center))
            self.assertLess(np.ptp(window.spot_axis.get_xlim()), x1 - x0)
            self.assertLess(np.ptp(window.spot_axis.get_ylim()), y1 - y0)
            bbox = window.spot_axis.bbox
            before_pan = window.spot_axis.get_xlim()
            window._on_2d_drag_start(SimpleNamespace(inaxes=window.spot_axis, button=1, dblclick=False, x=bbox.x0 + bbox.width / 2, y=bbox.y0 + bbox.height / 2))
            window._on_2d_drag_move(SimpleNamespace(x=bbox.x0 + bbox.width / 2 + 12, y=bbox.y0 + bbox.height / 2))
            window._on_2d_drag_end(SimpleNamespace())
            self.assertNotEqual(window.spot_axis.get_xlim(), before_pan)
            window._on_2d_drag_start(SimpleNamespace(inaxes=window.spot_axis, button=2, dblclick=True))
            home_scale = min((x1 - x0) / bbox.width, (y1 - y0) / bbox.height)
            self.assertAlmostEqual(window._spot_scale_um_per_px, home_scale)
            initial_width = np.ptp(window.spot_axis.get_xlim())
            for _ in range(3):
                window._on_2d_scroll(SimpleNamespace(inaxes=window.spot_axis, button="down", xdata=x_center, ydata=y_center))
            self.assertGreater(window._spot_scale_um_per_px, home_scale)
            self.assertGreater(np.ptp(window.spot_axis.get_xlim()), initial_width)
            window._on_2d_drag_start(SimpleNamespace(inaxes=window.spot_axis, button=2, dblclick=True))
            self.assertAlmostEqual(window._spot_scale_um_per_px, home_scale)
            window.close()

    def test_qt_window_uses_six_native_docks_and_loads_sample(self) -> None:
        window = CaustiqWindow()
        dataset = load_csv_dataset(PROJECT / "sample_data" / "synthetic_single_angle.csv", MeasurementConfig())
        window._apply_dataset(dataset)
        self.qt_app.processEvents()
        self.assertEqual(set(window.docks), {"setup", "propagation", "results", "beam_data", "spot", "beam3d"})
        self.assertTrue(all(isinstance(dock, QDockWidget) for dock in window.docks.values()))
        expected_features = (
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.assertTrue(all(dock.features() & expected_features == expected_features for dock in window.docks.values()))
        self.assertTrue(all(dock.allowedAreas() == Qt.DockWidgetArea.AllDockWidgetAreas for dock in window.docks.values()))
        self.assertEqual(window.angle_spin.value(), 0)
        self.assertEqual(window.data_table.rowCount(), 23)
        self.assertEqual(window.results_tree.topLevelItemCount(), 5)
        self.assertIsNotNone(window.current_m2)
        self.assertEqual(window.windowTitle(), f"Caustiq v{APP_VERSION} · Beam Profiling & M² Analysis")
        self.assertEqual(APP_VERSION, "2.4.2")
        self.assertNotIn(window.beam3d_dock, window.tabifiedDockWidgets(window.spot_dock))
        self.assertTrue(any(label.text() == "Caustiq" for label in window.findChildren(QLabel)))
        self.assertEqual(window.principle_button.text(), "Test Principle")
        window._show_measurement_principle()
        self.qt_app.processEvents()
        self.assertTrue(window.principle_dialog.isVisible())
        self.assertFalse(window.principle_dialog.principle_view.pixmap.isNull())
        self.assertTrue(window.principle_dialog.principle_view.timer.isActive())
        window.principle_dialog.close()
        self.qt_app.processEvents()
        self.assertFalse(window.principle_dialog.principle_view.timer.isActive())
        self.assertGreaterEqual(window.caustic_figure.subplotpars.hspace, 0.26)
        self.assertFalse(window.hardware_source_action.isEnabled())
        laser = next(window.results_tree.topLevelItem(i) for i in range(window.results_tree.topLevelItemCount()) if window.results_tree.topLevelItem(i).text(0) == "Laser")
        self.assertEqual(laser.childCount(), 14)
        formula_keys = [laser.child(i).data(0, Qt.ItemDataRole.UserRole) for i in range(laser.childCount())]
        self.assertTrue(all(key in LASER_FORMULAS for key in formula_keys))
        plot_toolbars = window.findChildren(NavigationToolbar2QT)
        self.assertEqual(len(plot_toolbars), 4)
        self.assertTrue(all(toolbar.isHidden() for toolbar in plot_toolbars))
        window.apply_theme("Dark", announce=False)
        self.assertEqual(window.tokens.name, "Dark")
        self.assertEqual(to_hex(window.spot_figure.get_facecolor()), "#000000")
        self.assertEqual(to_hex(window.spot_axis.get_facecolor()), "#000000")
        window.apply_theme("Light", announce=False)
        window.beam3d_canvas.draw()
        pixels = np.asarray(window.beam3d_canvas.buffer_rgba())
        np.testing.assert_array_equal(pixels[0, 0], [0, 0, 0, 255])
        window.close()

    def test_unique_preview_indices_keep_one_frame_per_z(self) -> None:
        indices = CaustiqWindow._unique_preview_indices(np.asarray([410.0, 390.0, 410.0, 400.0, 390.0]))
        np.testing.assert_array_equal(indices, np.asarray([1, 3, 0]))

    def test_3d_display_indices_are_bounded_and_keep_endpoints(self) -> None:
        z_values = np.arange(100.0)
        indices = CaustiqWindow._display_preview_indices(z_values)
        self.assertEqual(indices.size, 7)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 99)
        self.assertEqual(np.unique(indices).size, indices.size)

    def test_3d_sampling_keeps_narrow_core_and_rejects_background(self) -> None:
        yy, xx = np.indices((512, 512))
        image = np.rint(255 * np.exp(-0.5 * (((xx - 211) / 5) ** 2 + ((yy - 299) / 8) ** 2))).astype(np.uint8)
        image[::12, ::12] = 2
        z, x, y, colors, sizes = CaustiqWindow._sample_3d_points(
            image, (-256, 256, -256, 256), 17.0, endpoint=False,
        )
        self.assertGreater(len(z), 400)
        self.assertLessEqual(len(z), 1_400)
        self.assertLess(np.ptp(x), 40)
        self.assertLess(np.ptp(y), 60)
        self.assertTrue(np.any(np.all(colors[:, :3] > 0.9, axis=1)))
        np.testing.assert_array_equal(z, 17.0)
        empty = CaustiqWindow._sample_3d_points(np.zeros((32, 32), dtype=np.uint8), (-1, 1, -1, 1), 0, endpoint=False)
        self.assertEqual(len(empty[0]), 0)


if __name__ == "__main__":
    unittest.main()

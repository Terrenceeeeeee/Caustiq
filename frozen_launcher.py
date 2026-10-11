"""Standalone Windows entry point and repeatable offline acceptance check."""
from pathlib import Path
import json
import os
import sys
import traceback


def self_test(destination):
    import numpy as np
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication
    import qt_app
    from angle_dataset import AngleDataset, load_csv_dataset
    from measurement_config import MeasurementConfig
    import m2_core

    root = Path(qt_app.__file__).parent
    app = QApplication.instance() or QApplication([])
    qt_app.configure_application_icon(app)
    assert not app.windowIcon().isNull()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(Path(destination).parent / 'test_settings'))
    dataset = load_csv_dataset(root/'sample_data'/'synthetic_single_angle.csv', MeasurementConfig())
    series = dataset.series(0)
    result = m2_core.analyze_arrays(series.z_mm, series.wx_um, series.wy_um)
    assert abs(result.fit_x.m2 - 1.1666594078) < 1e-5
    assert abs(result.fit_y.m2 - 1.2676936375) < 1e-5
    assert not QPixmap(str(root/'assets'/'measurement_principle.png')).isNull()
    assert not QPixmap(str(root/'assets'/'caustiq_icon.png')).isNull()
    window = qt_app.CaustiqWindow()
    window._apply_dataset(dataset)
    window.caustic_canvas.draw()
    yy, xx = np.indices((96, 96))
    previews = tuple(np.rint(255*np.exp(-0.5*(((xx-48)/(8+i))**2 + ((yy-48)/(10+i))**2))).astype(np.uint8) for i in range(5))
    z = np.arange(5, dtype=float)
    window.dataset = AngleDataset('self-test','images',np.array([0]),z,z,np.full((1,5),100.),np.full((1,5),120.),tuple('test' for _ in z),tuple('' for _ in z),'test',previews,tuple((-100.,100.,-100.,100.) for _ in z))
    window._show_2d_preview(2, update_status=False)
    window._build_3d_beam()
    window.spot_canvas.draw(); window.beam3d_canvas.draw()
    assert len(window.beam3d_point_artist._offsets3d[0]) > 0
    window._show_measurement_principle()
    window.principle_dialog.principle_view._advance()
    window.principle_dialog.close()
    report = {'version':qt_app.APP_VERSION,'frozen':bool(getattr(sys,'frozen',False)),
              'm2_x':result.fit_x.m2,'m2_y':result.fit_y.m2,
              'resource_image':True,'app_icon':True,'qt_render_2d_3d':True,'principle_animation':True,
              'executable':sys.executable,'resource_root':str(root)}
    Path(destination).write_text(json.dumps(report, indent=2), encoding='utf-8')
    window.close()


def main():
    data_root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Caustiq'
    data_root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('MPLCONFIGDIR', str(data_root/'matplotlib'))
    try:
        import matplotlib
        matplotlib.use('QtAgg')
        if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
            self_test(sys.argv[2])
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QApplication
        import qt_app
        args = qt_app.parse_args()
        app = QApplication(sys.argv)
        qt_app.configure_application_icon(app)
        app.setApplicationName(qt_app.APP_NAME)
        app.setApplicationVersion(qt_app.APP_VERSION)
        app.setOrganizationName('Caustiq')
        app.setStyle('Fusion')
        window = qt_app.CaustiqWindow(args.source)
        resource_root = Path(qt_app.__file__).parent
        guide = resource_root/'docs'/'CALCULATION_PRINCIPLES.md'
        help_menu = window.menuBar().actions()[-1].menu()
        guide_action = help_menu.addAction('Calculation Principles (English)')
        guide_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(guide))))
        guide_action.setEnabled(guide.is_file())
        window.show()
        raise SystemExit(app.exec())
    except SystemExit:
        raise
    except Exception:
        log = data_root/'startup_error.log'
        log.write_text(traceback.format_exc(), encoding='utf-8')
        if '--self-test' not in sys.argv:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication([])
            QMessageBox.critical(None, 'Caustiq startup failed', f'Error details: {log}\n\n{traceback.format_exc()[-1200:]}')
        raise SystemExit(1)


if __name__ == '__main__':
    main()

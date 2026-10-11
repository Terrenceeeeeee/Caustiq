"""Semantic theme tokens and Qt style sheets for the Caustiq UI.

The theme is deliberately solid and low-decoration. Measurement plots, image
views, and tables always use opaque surfaces; translucency is not required for
the interface to retain hierarchy or meaning.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeTokens:
    name: str
    window: str
    sidebar: str
    surface: str
    surface_alt: str
    surface_pressed: str
    text: str
    secondary: str
    disabled: str
    border: str
    border_strong: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    selection: str
    x_axis: str
    y_axis: str
    success: str
    warning: str
    error: str
    plot_grid: str


LIGHT = ThemeTokens(
    name="Light",
    window="#F3F3F3",
    sidebar="#F7F7F7",
    surface="#FFFFFF",
    surface_alt="#F5F5F5",
    surface_pressed="#EAEAEA",
    text="#1B1B1B",
    secondary="#5D5D5D",
    disabled="#9A9A9A",
    border="#E5E5E5",
    border_strong="#C8C8C8",
    accent="#0067C0",
    accent_hover="#005A9E",
    accent_pressed="#004578",
    accent_text="#FFFFFF",
    selection="#D8EBFA",
    x_axis="#C81E2A",
    y_axis="#0866CC",
    success="#187A3F",
    warning="#9A5B00",
    error="#B42318",
    plot_grid="#C7C7CC",
)


DARK = ThemeTokens(
    name="Dark",
    window="#202020",
    sidebar="#252525",
    surface="#2B2B2B",
    surface_alt="#333333",
    surface_pressed="#3D3D3D",
    text="#F5F5F5",
    secondary="#C7C7C7",
    disabled="#858585",
    border="#3D3D3D",
    border_strong="#5A5A5A",
    accent="#60CDFF",
    accent_hover="#76D6FF",
    accent_pressed="#4CC2FF",
    accent_text="#002B3B",
    selection="#164C63",
    x_axis="#FF6B70",
    y_axis="#65A9FF",
    success="#52CF86",
    warning="#FFB55B",
    error="#FF7770",
    plot_grid="#55555B",
)


THEMES = {"Light": LIGHT, "Dark": DARK}


def style_sheet(t: ThemeTokens) -> str:
    """Return the application QSS generated from semantic color roles."""
    return f"""
    * {{
        font-family: "Segoe UI Variable", "Segoe UI", "SF Pro Text", "Helvetica Neue", sans-serif;
        font-size: 13px;
        color: {t.text};
    }}
    QMainWindow, QDialog {{ background: {t.window}; }}
    QMainWindow::separator {{ background: {t.border}; width: 3px; height: 3px; }}
    QMainWindow::separator:hover {{ background: {t.accent}; }}
    QWidget#CentralUnderlay {{ background: {t.window}; }}
    QWidget#DockSurface, QWidget#PlotSurface {{ background: {t.surface}; }}
    QScrollArea#SidebarScroll, QScrollArea#SidebarScroll > QWidget > QWidget,
    QWidget#SidebarContent {{ background: {t.sidebar}; border: 0; }}
    QToolBar {{
        background: {t.surface};
        border: 0;
        border-bottom: 1px solid {t.border};
        spacing: 5px;
        padding: 8px 12px;
    }}
    QToolBar::separator {{ background: {t.border}; width: 1px; margin: 5px 7px; }}
    QMenuBar {{ background: {t.surface}; border-bottom: 1px solid {t.border}; }}
    QMenuBar::item {{ padding: 7px 10px; background: transparent; border-radius: 5px; }}
    QMenuBar::item:selected {{ background: {t.surface_alt}; }}
    QMenu {{ background: {t.surface}; border: 1px solid {t.border}; padding: 5px; }}
    QMenu::item {{ padding: 7px 30px 7px 10px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {t.selection}; }}
    QDockWidget {{
        background: {t.surface};
        border: 1px solid {t.border};
    }}
    QDockWidget::title {{
        background: {t.surface};
        border-bottom: 1px solid {t.border};
        padding: 9px 10px;
        font-weight: 600;
        text-align: left;
    }}
    QDockWidget::close-button, QDockWidget::float-button {{
        background: transparent;
        border: 0;
        border-radius: 4px;
        width: 20px;
        height: 20px;
        padding: 2px;
    }}
    QDockWidget::close-button:hover, QDockWidget::float-button:hover {{ background: {t.surface_pressed}; }}
    QDockWidget::close-button:pressed, QDockWidget::float-button:pressed {{ background: {t.border_strong}; }}
    QLabel#AppTitle {{ font-size: 15px; font-weight: 650; }}
    QLabel#BrandSubtitle {{ font-size: 11px; color: {t.secondary}; }}
    QLabel#FormulaValue {{ font-size: 14px; font-weight: 600; color: {t.accent}; }}
    QLabel#FormulaBlock {{
        font-size: 16px;
        background: {t.surface_alt};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 14px;
    }}
    QLabel#SectionTitle {{ font-size: 12px; font-weight: 650; color: {t.secondary}; }}
    QLabel#SecondaryLabel, QLabel#SourceDetail, QLabel#AxisHint {{ color: {t.secondary}; }}
    QLabel#AngleValue {{ font-size: 22px; font-weight: 650; }}
    QLabel#StatusDot {{ font-size: 16px; }}
    QGroupBox {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 8px;
        margin-top: 11px;
        padding: 12px 10px 10px 10px;
        font-weight: 600;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}
    QPushButton, QToolButton, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
        min-height: 28px;
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 6px;
        padding: 3px 9px;
        selection-background-color: {t.selection};
    }}
    QPushButton:hover, QToolButton:hover, QComboBox:hover, QSpinBox:hover,
    QDoubleSpinBox:hover, QLineEdit:hover {{ border-color: {t.accent}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {t.surface_pressed}; }}
    QPushButton:focus, QToolButton:focus, QComboBox:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QLineEdit:focus, QAbstractItemView:focus {{
        border: 2px solid {t.accent};
    }}
    QPushButton[primary="true"], QToolButton[primary="true"] {{
        color: {t.accent_text};
        background: {t.accent};
        border-color: {t.accent};
        font-weight: 600;
    }}
    QPushButton[primary="true"]:hover, QToolButton[primary="true"]:hover {{
        background: {t.accent_hover}; border-color: {t.accent_hover};
    }}
    QPushButton[primary="true"]:pressed, QToolButton[primary="true"]:pressed {{
        background: {t.accent_pressed}; border-color: {t.accent_pressed};
    }}
    QPushButton:disabled, QToolButton:disabled, QComboBox:disabled,
    QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {{
        color: {t.disabled}; background: {t.surface_alt}; border-color: {t.border};
    }}
    QCheckBox, QRadioButton {{ spacing: 7px; min-height: 24px; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
    QSlider::groove:horizontal {{ height: 4px; background: {t.surface_pressed}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 16px; margin: -6px 0; border-radius: 8px;
        background: {t.surface}; border: 2px solid {t.accent};
    }}
    QTabWidget::pane {{ background: {t.surface}; border: 0; border-top: 1px solid {t.border}; }}
    QTabBar::tab {{
        min-height: 24px; background: transparent; color: {t.secondary}; padding: 7px 13px;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{ color: {t.text}; border-bottom-color: {t.accent}; font-weight: 600; }}
    QTabBar::tab:hover:!selected {{ background: {t.surface_alt}; }}
    QTreeWidget, QTableWidget, QPlainTextEdit, QTextEdit {{
        alternate-background-color: {t.surface_alt};
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 7px;
        selection-background-color: {t.selection};
        selection-color: {t.text};
        gridline-color: {t.border};
    }}
    QHeaderView::section {{
        background: {t.surface_alt}; color: {t.secondary};
        border: 0; border-right: 1px solid {t.border}; border-bottom: 1px solid {t.border};
        padding: 7px 8px; font-weight: 600;
    }}
    QProgressBar {{
        min-height: 6px; max-height: 6px; border: 0;
        border-radius: 3px; background: {t.surface_pressed}; text-align: center;
    }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}
    QStatusBar {{ background: {t.surface}; border-top: 1px solid {t.border}; }}
    QStatusBar::item {{ border: 0; }}
    QScrollBar:vertical {{ width: 10px; background: transparent; margin: 2px; }}
    QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 2px; }}
    QScrollBar::handle {{ background: {t.border_strong}; border-radius: 4px; min-height: 28px; min-width: 28px; }}
    QScrollBar::handle:hover {{ background: {t.disabled}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QToolTip {{ color: {t.text}; background: {t.surface}; border: 1px solid {t.border_strong}; }}
    """

"""Native Qt presentation shared by the launcher and database manager only."""
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPalette
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QVBoxLayout

AUTHOR = "Автор: Vladislav_Shetkov"

DESKTOP_STYLE = """
QWidget {background:transparent;font-family:'Segoe UI';font-size:14px;color:#dde6f3;}
QMainWindow,QDialog,QWidget#desktopRoot {background:#10151f;}
QWidget#sidebar {background:#151d2a;border-right:1px solid #293449;}
QFrame#card {background:#192232;border:1px solid #2a374c;border-radius:12px;}
QLabel {background:transparent;border:0;}
QLabel#title {font-size:26px;font-weight:600;color:#f4f7fc;}
QLabel#sectionTitle {font-size:21px;font-weight:600;color:#f4f7fc;}
QLabel#subtitle,QLabel#muted {color:#93a6bf;}
QLabel#status {color:#a9c7f8;padding:8px 0;}
QLabel#error {background:#392633;color:#ffbdc8;border-radius:8px;padding:10px;}
QLabel#badge {background:#243650;color:#c0d9ff;border-radius:8px;padding:6px 10px;}
QLabel#badge[state="online"] {background:#18392f;color:#8fe0bc;}
QLabel#badge[state="offline"] {background:#302b35;color:#c1b8c8;}
QPushButton {background:#243248;border:1px solid #354762;border-radius:8px;padding:9px 14px;min-height:18px;}
QPushButton:hover {background:#304461;border-color:#50729f;}
QPushButton:pressed {background:#1d2d45;}
QPushButton:focus {border-color:#7eaaff;}
QPushButton:disabled {color:#66758c;background:#1b2432;border-color:#293449;}
QPushButton[role="primary"] {background:#3877e0;border-color:#4989f4;color:white;font-weight:600;}
QPushButton[role="primary"]:hover {background:#478af7;}
QPushButton[role="danger"] {color:#ffb8bd;border-color:#69404c;}
QPushButton[role="nav"] {text-align:left;background:transparent;border:1px solid transparent;padding:12px;}
QPushButton[role="nav"]:checked {background:#253d60;border-color:#34577f;color:#e4f0ff;font-weight:600;}
QPushButton[role="nav"]:hover {background:#223047;}
QPushButton[role="primary"]:disabled,QPushButton[role="danger"]:disabled {color:#66758c;background:#1b2432;border-color:#293449;}
QLineEdit,QTextEdit,QPlainTextEdit,QTextBrowser,QTreeWidget,QListWidget,QTableWidget,QComboBox,QSpinBox,QDoubleSpinBox {
 background:#141e2d;border:1px solid #324059;border-radius:8px;padding:8px;selection-background-color:#315d97;}
QLineEdit:focus,QTextEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus {border-color:#6d9fe8;}
QTableWidget {background:#151e2b;alternate-background-color:#192535;padding:0;gridline-color:#263448;}
QTableWidget::item {padding:8px;border:0;}
QTableWidget::item:selected {background:#28466a;color:#f1f6ff;}
QHeaderView {background:#1e2b3e;}
QHeaderView::section {background:#1e2b3e;color:#b6c8e0;border:0;border-right:1px solid #34455f;border-bottom:1px solid #34455f;padding:12px 10px;font-weight:600;}
QTabWidget::pane {border:0;background:transparent;}
QTabBar::tab {background:#1c293b;padding:10px 16px;border-radius:6px;margin-right:4px;}
QTabBar::tab:selected {background:#2d4f7a;}
QScrollArea {border:0;background:transparent;}
QScrollBar:vertical {background:#141e2d;width:12px;margin:2px;}
QScrollBar:horizontal {background:#141e2d;height:12px;margin:2px;}
QScrollBar::handle {background:#415571;border-radius:4px;min-width:28px;min-height:28px;}
QScrollBar::handle:hover {background:#5d789f;}
QScrollBar::add-line,QScrollBar::sub-line {width:0;height:0;}
QScrollBar::add-page,QScrollBar::sub-page {background:transparent;}
QCheckBox {spacing:8px;padding:5px 0;}
QCheckBox::indicator {width:18px;height:18px;}
QCheckBox::indicator:unchecked {background:#141e2d;border:1px solid #647994;border-radius:4px;}
QCheckBox::indicator:checked {background:#3877e0;border:1px solid #71a6ff;border-radius:4px;image:url("@ASSETS@/check.svg");}
QComboBox {padding-right:32px;}
QComboBox::drop-down {subcontrol-origin:padding;subcontrol-position:top right;width:28px;border:0;}
QComboBox::down-arrow {image:url("@ASSETS@/down.svg");width:12px;height:12px;}
QSpinBox,QDoubleSpinBox {padding-right:30px;}
QSpinBox::up-button,QDoubleSpinBox::up-button {subcontrol-origin:border;subcontrol-position:top right;width:26px;border:0;}
QSpinBox::down-button,QDoubleSpinBox::down-button {subcontrol-origin:border;subcontrol-position:bottom right;width:26px;border:0;}
QSpinBox::up-arrow,QDoubleSpinBox::up-arrow {image:url("@ASSETS@/up.svg");width:12px;height:12px;}
QSpinBox::down-arrow,QDoubleSpinBox::down-arrow {image:url("@ASSETS@/down.svg");width:12px;height:12px;}
QSplitter::handle {background:#2c3e56;width:5px;}
QToolTip {background:#24354d;color:#eef4ff;border:1px solid #4c6486;padding:6px;}
QMenu {background:#1c293b;border:1px solid #3c4d66;padding:5px;}
QMenu::item {padding:8px 18px;} QMenu::item:selected {background:#304d73;}
"""


def setup_desktop(window):
    # Fusion follows the supplied palette on Windows too, including arrows,
    # spin buttons and check marks that otherwise keep the light OS colours.
    QApplication.instance().setStyle("Fusion")
    palette = QPalette()
    for role, color in ((QPalette.Window, "#10151f"), (QPalette.WindowText, "#dde6f3"),
                        (QPalette.Base, "#141e2d"), (QPalette.AlternateBase, "#192535"),
                        (QPalette.Text, "#dde6f3"), (QPalette.Button, "#243248"),
                        (QPalette.ButtonText, "#dde6f3"), (QPalette.Highlight, "#315d97"),
                        (QPalette.HighlightedText, "#ffffff"), (QPalette.Light, "#52637c"),
                        (QPalette.Mid, "#354762"), (QPalette.Dark, "#10151f")):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#66758c"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#66758c"))
    window.setPalette(palette)
    assets = (Path(__file__).parent / "assets").as_posix().replace('"', '\\"')
    window.setStyleSheet(DESKTOP_STYLE.replace("@ASSETS@", assets))
    window.setFont(QFont("Segoe UI", 10))


def label(text, role="muted", wrap=True):
    value = QLabel(text)
    value.setObjectName(role)
    value.setWordWrap(wrap)
    value.setTextFormat(Qt.PlainText)
    return value


def heading(layout, title, subtitle):
    layout.addWidget(label(title, "sectionTitle"))
    layout.addWidget(label(subtitle, "subtitle"))


def card(parent_layout):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    parent_layout.addWidget(frame)
    return layout


def action(text, callback, layout, role=None, icon=None):
    item = QPushButton(text)
    if role:
        item.setProperty("role", role)
    if icon:
        item.setIcon(icon)
    item.clicked.connect(callback)
    layout.addWidget(item)
    return item


def open_folder(path):
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
        raise OSError("Не удалось открыть папку. Проверьте, что она существует и доступна.")

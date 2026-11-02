"""Obsidian presentation for the launcher, scoped away from other Qt windows."""
from functools import lru_cache
from pathlib import Path
from PySide6.QtCore import QByteArray, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QLayout, QSizePolicy, QWidget


STYLE = """
QWidget {background:transparent;color:#d7d4e0;font-family:'Segoe UI';font-size:14px;}
QMainWindow,QDialog,QWidget#launcherRoot,QWidget#overview,QWidget#desktopRoot {background:#12131b;}
QFrame#brandBar,QWidget#launcherSidebar {background:#181922;}
QFrame#brandBar {border-bottom:1px solid #343240;}
QWidget#launcherSidebar {border-right:1px solid #343240;}
QLabel {border:0;background:transparent;}
QLabel#brandMark {background:#403052;color:#bda0ff;border-radius:8px;font-size:18px;font-weight:700;}
QLabel#brandTitle {color:#f3f1f7;font-size:20px;font-weight:700;}
QLabel#pageTitle {color:#f3f1f7;font-size:32px;font-weight:700;}
QLabel#sectionTitle {color:#f3f1f7;font-size:21px;font-weight:700;}
QLabel#cardTitle {color:#f3f1f7;font-size:17px;font-weight:700;}
QLabel#backendTitle {color:#f3f1f7;font-size:22px;font-weight:700;}
QLabel#muted,QLabel#subtitle,QLabel#author,QLabel#path {color:#9995aa;}
QLabel#small,QLabel#path {font-size:12px;color:#9995aa;}
QLabel#footnote {font-size:11px;color:#777386;}
QLabel#badge {font-size:13px;background:#30283f;color:#bda0ff;border-radius:10px;padding:11px 18px;}
QLabel#badge[state="online"] {background:#1d3a31;color:#82d2af;}
QLabel#badge[state="offline"] {background:#392a31;color:#ea95a2;}
QLabel#backendDetails {font-size:12px;color:#9995aa;}
QLabel#backendDetails[state="online"] {color:#82d2af;}
QLabel#backendIcon {border-radius:16px;background:#403052;}
QLabel#backendIcon[state="online"] {background:#214438;}
QFrame#launcherCard {background:#1c1d27;border:1px solid #343240;border-radius:12px;}
QFrame#separator {background:#343240;border:0;min-height:1px;max-height:1px;}
QLabel#error {background:#392632;border:1px solid #69404c;border-radius:8px;color:#ffbdc8;padding:12px;}
QPushButton {background:#23232f;border:1px solid #343240;border-radius:8px;padding:9px 14px;min-height:20px;font-weight:400;}
QPushButton:hover {background:#30283f;border-color:#7751a3;}
QPushButton:pressed {background:#403052;}
QPushButton:focus {border-color:#bda0ff;}
QPushButton:disabled {color:#686475;background:#1c1d27;border-color:#2d2b37;}
QPushButton[role="primary"] {background:#403052;border-color:#7751a3;color:#f3f1f7;font-weight:400;}
QPushButton[role="primary"]:hover {background:#4c3863;border-color:#bda0ff;}
QPushButton[role="primary"]:disabled {background:#292332;border-color:#44384e;color:#777080;}
QPushButton[role="quiet"] {background:#181922;}
QPushButton[role="navigation"] {text-align:left;background:transparent;border:1px solid transparent;padding:11px 14px;}
QPushButton[role="navigation"]:hover {background:#23232f;}
QPushButton[role="navigation"]:checked {background:#30283f;border-color:#7751a3;color:#f3f1f7;}
QPushButton[role="subnav"] {background:transparent;border:1px solid transparent;color:#9995aa;}
QPushButton[role="subnav"]:hover {background:#23232f;color:#f3f1f7;}
QPushButton[role="subnav"]:checked {background:#30283f;border-color:#7751a3;color:#f3f1f7;}
QPushButton[role="danger"] {color:#eda4b2;}
QPushButton[role="danger"]:disabled {color:#686475;}
QLabel#status {color:#b4a1d1;padding:8px 0;}
QLineEdit,QTextEdit,QPlainTextEdit,QTextBrowser,QComboBox,QSpinBox,QDoubleSpinBox {
 background:#1c1d27;border:1px solid #343240;border-radius:8px;padding:9px;selection-background-color:#59416e;}
QLineEdit:focus,QTextEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus {border-color:#9975c3;}
QTableWidget {background:#181922;alternate-background-color:#1c1d27;border:1px solid #343240;border-radius:10px;padding:0;gridline-color:#343240;}
QTableWidget::item {padding:8px;border:0;}
QTableWidget::item:selected {background:#403052;color:#f3f1f7;}
QHeaderView {background:#23232f;}
QHeaderView::section {background:#23232f;color:#b6b0c3;border:0;border-right:1px solid #343240;border-bottom:1px solid #343240;padding:12px 10px;font-weight:600;}
QTabWidget::pane {border:0;background:transparent;}
QComboBox QAbstractItemView {background:#23232f;border:1px solid #7751a3;selection-background-color:#403052;}
QCheckBox {spacing:8px;padding:5px 0;}
QCheckBox::indicator {width:18px;height:18px;border-radius:4px;}
QCheckBox::indicator:unchecked {background:#1c1d27;border:1px solid #6e667d;}
QCheckBox::indicator:checked {background:#7751a3;border:1px solid #bda0ff;image:url("@ASSETS@/check.svg");}
QComboBox {padding-right:32px;}
QComboBox::drop-down {subcontrol-origin:padding;subcontrol-position:top right;width:28px;border:0;}
QComboBox::down-arrow {image:url("@ASSETS@/down.svg");width:12px;height:12px;}
QSpinBox,QDoubleSpinBox {padding-right:30px;}
QSpinBox::up-button,QDoubleSpinBox::up-button {subcontrol-origin:border;subcontrol-position:top right;width:26px;border:0;}
QSpinBox::down-button,QDoubleSpinBox::down-button {subcontrol-origin:border;subcontrol-position:bottom right;width:26px;border:0;}
QSpinBox::up-arrow,QDoubleSpinBox::up-arrow {image:url("@ASSETS@/up.svg");width:12px;height:12px;}
QSpinBox::down-arrow,QDoubleSpinBox::down-arrow {image:url("@ASSETS@/down.svg");width:12px;height:12px;}
QScrollArea {background:transparent;border:0;}
QScrollBar:horizontal {background:transparent;height:10px;margin:3px;}
QScrollBar::handle:horizontal {background:#484252;border-radius:3px;min-width:32px;}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal {width:0;}
QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal {background:transparent;}
QScrollBar:vertical {background:transparent;width:10px;margin:3px;}
QScrollBar::handle:vertical {background:#484252;border-radius:3px;min-height:32px;}
QScrollBar::handle:vertical:hover {background:#6f5b88;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}
QToolTip {background:#23232f;border:1px solid #7751a3;color:#f3f1f7;padding:6px;}
""".replace("@ASSETS@", (Path(__file__).parent / "assets").as_posix().replace('"', '\\"'))

PATHS = {
    "home": '<path d="M4 10l6-5 6 5v7H4z"/>',
    "database": '<rect x="4" y="4" width="12" height="13" rx="1"/><path d="M6 7h8M6 10h8M6 13h8"/>',
    "overlay": '<rect x="4" y="4" width="12" height="12"/><rect x="8" y="8" width="4" height="4"/>',
    "note": '<path d="M5 3h8l3 3v11H5zM12 3v4h4M7 10h6M7 13h6"/>',
    "settings": '<circle cx="10" cy="10" r="5"/><circle cx="10" cy="10" r="2"/><path d="M10 2v3M10 15v3M2 10h3M15 10h3M4 4l2 2M14 14l2 2M4 16l2-2M14 6l2-2"/>',
    "code": '<path d="M7 5L2 10l5 5M13 5l5 5-5 5M12 3L8 17"/>',
    "plugin": '<path d="M7 4v4H4v5h4v4h5v-4h4V8h-4V4z"/>',
    "folder": '<path d="M2 6h6l2 2h8v9H2zM2 6V4h6l2 2h8v2"/>',
    "power": '<path d="M10 2v8M5 5a7 7 0 1 0 10 0"/>',
    "check": '<path d="M4 10l4 4 8-9"/>',
    "play": '<path d="M7 4l9 6-9 6z" fill="currentColor" stroke="none"/>',
    "stop": '<rect x="5" y="5" width="10" height="10" fill="currentColor" stroke="none"/>',
    "right": '<path d="M3 10h13M11 5l5 5-5 5"/>',
}

DOCUMENT_STYLE = """
body{color:#d7d4e0;font-family:'Segoe UI';font-size:15px;}
p{margin-top:6px;margin-bottom:10px;}
h1,h2,h3,h4{color:#bda0ff;margin-top:18px;margin-bottom:10px;}
table{border-collapse:collapse;}
td,th{border:1px solid #51495e;padding:10px;}
th{background:#30283f;color:#f3f1f7;} a{color:#bda0ff;}
"""


@lru_cache(maxsize=64)
def icon(name, color="#b1aabc"):
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="' + color + '" color="' + color + '" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">' + PATHS[name] + '</svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def setup(window):
    from .desktop import setup_desktop
    setup_desktop(window)
    palette = window.palette()
    for role, color in ((QPalette.Window, "#12131b"), (QPalette.WindowText, "#d7d4e0"),
                        (QPalette.Base, "#1c1d27"), (QPalette.Text, "#d7d4e0"),
                        (QPalette.Button, "#23232f"), (QPalette.ButtonText, "#f3f1f7"),
                        (QPalette.Highlight, "#403052")):
        palette.setColor(role, QColor(color))
    window.setPalette(palette)
    window.setStyleSheet(STYLE)


def state(widget, value):
    if widget.property("state") != value:
        widget.setProperty("state", value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


class PathLabel(QLabel):
    """Long paths must never force a wider window or push actions offscreen."""
    def __init__(self, value=""):
        super().__init__()
        self.full_text = ""
        self.setObjectName("path")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.set_path(value)

    def set_path(self, value):
        self.full_text = str(value)
        self.setToolTip(self.full_text)
        self._elide()

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.ElideMiddle, max(40, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class CardGrid(QWidget):
    def __init__(self, cards, maximum_columns=3, parent=None):
        super().__init__(parent)
        # Qt can keep the old inherited style on cards polished by a layout's
        # heightForWidth query before reparenting. Give each grid the same local
        # theme so its very first frame cannot retain the manager's blue style.
        self.setStyleSheet(STYLE)
        self.cards = cards
        self.maximum_columns = maximum_columns
        self.columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(16)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.reflow(1000)

    def columns_for(self, width):
        return min(self.maximum_columns, len(self.cards), max(1, (width + 16) // 296))

    def reflow(self, width):
        columns = self.columns_for(width)
        if columns != self.columns:
            while self.grid.count():
                self.grid.takeAt(0)
            for col in range(self.maximum_columns):
                self.grid.setColumnStretch(col, 1 if col < columns else 0)
            for index, widget in enumerate(self.cards):
                self.grid.addWidget(widget, index // columns, index % columns)
            self.columns = columns
        self.setMinimumHeight(self.heightForWidth(width))
        self.updateGeometry()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        columns = self.columns_for(width)
        card_width = max(100, (width - (columns - 1) * 16) // columns)
        rows = []
        for start in range(0, len(self.cards), columns):
            rows.append(max(max(card.minimumHeight(), card.layout().totalHeightForWidth(card_width)) for card in self.cards[start:start + columns]))
        return sum(rows) + max(0, len(rows) - 1) * 16

    def minimumSizeHint(self):
        return QSize(0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow(event.size().width())


def separator():
    frame = QFrame()
    frame.setObjectName("separator")
    frame.setFixedHeight(1)
    return frame


class FlowLayout(QLayout):
    """Compact toolbars wrap without imposing a window-wide minimum width."""
    def __init__(self):
        super().__init__()
        self.items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(8)

    def addItem(self, item):
        self.items.append(item)

    def addStretch(self, *_):
        pass

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientations()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.arrange(QRect(0, 0, width, 0), False)

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        return size

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.arrange(rect, True)

    def arrange(self, rect, apply):
        x, y, height = rect.x(), rect.y(), 0
        for item in self.items:
            if item.isEmpty():
                continue
            size = item.sizeHint()
            if x > rect.x() and x + size.width() > rect.x() + rect.width():
                x, y, height = rect.x(), y + height + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(x, y, size.width(), size.height()))
            x += size.width() + self.spacing()
            height = max(height, size.height())
        return y + height - rect.y()

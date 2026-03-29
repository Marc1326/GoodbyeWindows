"""GoodbyeWindows — Windows Exporter GUI.

Wizard-style interface (6 pages):
  Page 1: Welcome + language selection
  Page 2: Source selection (MO2 / Vortex / Both) + auto-scan
  Page 3: Game selection (checkboxes, sizes, duplicate warnings)
  Page 4: Export options (with/without mods, size display, warnings)
  Page 5: Target selection (USB / external / internal / network)
  Page 6: Progress + done
"""

import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.i18n import tr, set_locale, detect_locale
from common.mo2_reader import format_size
from common.migration_format import COMPRESS_NONE, COMPRESS_LOW, COMPRESS_STRONG
from exporter.exporter import (
    ExportableGame,
    export_games,
    games_from_mo2,
    games_from_vortex,
)
from exporter.scanner import find_all_instances
from exporter.scanner_vortex import scan_all_vortex
from exporter.server import TransferServer


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

STYLESHEET = """
QWizard {
    background-color: #1e1e2e;
}
QWizardPage {
    background-color: #1e1e2e;
    color: #cdd6f4;
}
QLabel {
    color: #cdd6f4;
}
QLabel[class="title"] {
    font-size: 28px;
    font-weight: bold;
    color: #cba6f7;
}
QLabel[class="subtitle"] {
    font-size: 14px;
    color: #a6adc8;
}
QLabel[class="section"] {
    font-size: 13px;
    font-weight: bold;
    color: #89b4fa;
}
QLabel[class="warning"] {
    color: #f9e2af;
    padding: 8px;
    background-color: #45403d;
    border-radius: 6px;
    border-left: 3px solid #f9e2af;
}
QLabel[class="error"] {
    color: #f38ba8;
    padding: 8px;
    background-color: #3d2a33;
    border-radius: 6px;
    border-left: 3px solid #f38ba8;
}
QLabel[class="success"] {
    color: #a6e3a1;
    padding: 8px;
    background-color: #2a3d2e;
    border-radius: 6px;
    border-left: 3px solid #a6e3a1;
}
QLabel[class="info-value"] {
    font-size: 15px;
    font-weight: bold;
    color: #f5c2e7;
}
QRadioButton, QCheckBox {
    color: #cdd6f4;
    spacing: 8px;
    font-size: 13px;
}
QRadioButton::indicator, QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QGroupBox {
    border: 1px solid #45475a;
    border-radius: 8px;
    margin-top: 12px;
    padding: 16px 12px 12px 12px;
    color: #cdd6f4;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #89b4fa;
}
QTableWidget {
    background-color: #313244;
    color: #cdd6f4;
    gridline-color: #45475a;
    border: 1px solid #45475a;
    border-radius: 6px;
    selection-background-color: #45475a;
}
QTableWidget::item {
    padding: 4px 8px;
}
QHeaderView::section {
    background-color: #1e1e2e;
    color: #89b4fa;
    border: none;
    border-bottom: 2px solid #45475a;
    padding: 6px 8px;
    font-weight: bold;
}
QPushButton {
    background-color: #45475a;
    color: #cdd6f4;
    border: none;
    border-radius: 6px;
    padding: 8px 20px;
    font-size: 13px;
}
QPushButton:hover {
    background-color: #585b70;
}
QPushButton:pressed {
    background-color: #6c7086;
}
QPushButton[class="primary"] {
    background-color: #89b4fa;
    color: #1e1e2e;
    font-weight: bold;
}
QPushButton[class="primary"]:hover {
    background-color: #b4d0fb;
}
QLineEdit {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px;
}
QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 6px 10px;
}
QComboBox::drop-down {
    border: none;
}
QComboBox QAbstractItemView {
    background-color: #313244;
    color: #cdd6f4;
    selection-background-color: #45475a;
}
QProgressBar {
    border: none;
    border-radius: 8px;
    background-color: #313244;
    text-align: center;
    color: #cdd6f4;
    height: 24px;
}
QProgressBar::chunk {
    background-color: #89b4fa;
    border-radius: 8px;
}
"""


# ---------------------------------------------------------------------------
# Worker threads
# ---------------------------------------------------------------------------

class ScanWorker(QThread):
    """Background scanner for MO2 and/or Vortex."""
    finished = Signal(list, list)  # mo2_games, vortex_games

    def __init__(self, scan_mo2=True, scan_vortex=True):
        super().__init__()
        self.scan_mo2 = scan_mo2
        self.scan_vortex = scan_vortex

    def run(self):
        mo2_games: list[ExportableGame] = []
        vortex_games: list[ExportableGame] = []

        if self.scan_mo2:
            instances = find_all_instances()
            mo2_games = games_from_mo2(instances)

        if self.scan_vortex:
            for vi in scan_all_vortex():
                vortex_games.extend(games_from_vortex(vi))

        self.finished.emit(mo2_games, vortex_games)


class ExportWorker(QThread):
    """Background thread for export operations."""
    progress = Signal(str, int, int)  # status, current, total
    finished = Signal(str)             # result path
    error = Signal(str)

    def __init__(self, games, output_dir, include_mods, compression=COMPRESS_LOW):
        super().__init__()
        self.games = games
        self.output_dir = output_dir
        self.include_mods = include_mods
        self.compression = compression

    def run(self):
        try:
            path = export_games(
                self.games,
                Path(self.output_dir),
                include_mods=self.include_mods,
                compression=self.compression,
                progress=lambda s, c, t: self.progress.emit(s, c, t),
            )
            self.finished.emit(str(path))
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Page 1 — Welcome
# ---------------------------------------------------------------------------

class WelcomePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("")
        self.setSubTitle("")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 20)

        layout.addStretch(2)

        title = QLabel("GoodbyeWindows")
        title.setProperty("class", "title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        self.subtitle_label = QLabel(tr("app_subtitle"))
        self.subtitle_label.setProperty("class", "subtitle")
        self.subtitle_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.subtitle_label)

        layout.addSpacing(8)

        self.desc_label = QLabel(tr("exporter_welcome"))
        self.desc_label.setAlignment(Qt.AlignCenter)
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label)

        layout.addStretch(1)

        # Language selector
        lang_box = QHBoxLayout()
        lang_box.addStretch()
        self.lang_label = QLabel(tr("language") + ":")
        lang_box.addWidget(self.lang_label)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Deutsch", "de")
        self.lang_combo.addItem("Español", "es")
        self.lang_combo.addItem("Français", "fr")
        self.lang_combo.addItem("Italiano", "it")
        self.lang_combo.addItem("Português", "pt")
        self.lang_combo.addItem("Русский", "ru")
        idx = self.lang_combo.findData(detect_locale())
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.currentIndexChanged.connect(self._on_lang)
        lang_box.addWidget(self.lang_combo)
        lang_box.addStretch()
        layout.addLayout(lang_box)

        layout.addStretch(2)

    def _on_lang(self):
        loc = self.lang_combo.currentData()
        set_locale(loc)
        wizard = self.wizard()
        if wizard and hasattr(wizard, "retranslateAll"):
            wizard.retranslateAll()

    def retranslateUi(self):
        self.subtitle_label.setText(tr("app_subtitle"))
        self.desc_label.setText(tr("exporter_welcome"))
        self.lang_label.setText(tr("language") + ":")


# ---------------------------------------------------------------------------
# Page 2 — Source Selection
# ---------------------------------------------------------------------------

class SourcePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle(tr("source_select_title"))
        self.setSubTitle(tr("source_select_desc"))
        self._games: list[ExportableGame] = []
        self._scan_done = False
        self._worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)

        # Source radio buttons
        self.source_group = QGroupBox(tr("source_select_title"))
        source_layout = QVBoxLayout(self.source_group)

        self.btn_group = QButtonGroup(self)

        self.radio_mo2 = QRadioButton(tr("source_mo2"))
        self.radio_mo2.setChecked(True)
        self.btn_group.addButton(self.radio_mo2, 0)
        source_layout.addWidget(self.radio_mo2)
        self.desc_mo2 = QLabel(tr("source_mo2_desc"))
        self.desc_mo2.setWordWrap(True)
        self.desc_mo2.setStyleSheet("margin-left: 28px; color: #a6adc8; font-size: 11px;")
        source_layout.addWidget(self.desc_mo2)
        source_layout.addSpacing(6)

        self.radio_vortex = QRadioButton(tr("source_vortex"))
        self.btn_group.addButton(self.radio_vortex, 1)
        source_layout.addWidget(self.radio_vortex)
        self.desc_vortex = QLabel(tr("source_vortex_desc"))
        self.desc_vortex.setWordWrap(True)
        self.desc_vortex.setStyleSheet("margin-left: 28px; color: #a6adc8; font-size: 11px;")
        source_layout.addWidget(self.desc_vortex)
        source_layout.addSpacing(6)

        self.radio_both = QRadioButton(tr("source_both"))
        self.btn_group.addButton(self.radio_both, 2)
        source_layout.addWidget(self.radio_both)
        self.desc_both = QLabel(tr("source_both_desc"))
        self.desc_both.setWordWrap(True)
        self.desc_both.setStyleSheet("margin-left: 28px; color: #a6adc8; font-size: 11px;")
        source_layout.addWidget(self.desc_both)

        layout.addWidget(self.source_group)
        layout.addSpacing(12)

        # Scan status
        self.status_label = QLabel("")
        self.status_label.setProperty("class", "section")
        layout.addWidget(self.status_label)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        layout.addStretch()

    def retranslateUi(self):
        self.setTitle(tr("source_select_title"))
        self.setSubTitle(tr("source_select_desc"))
        self.source_group.setTitle(tr("source_select_title"))
        self.radio_mo2.setText(tr("source_mo2"))
        self.desc_mo2.setText(tr("source_mo2_desc"))
        self.radio_vortex.setText(tr("source_vortex"))
        self.desc_vortex.setText(tr("source_vortex_desc"))
        self.radio_both.setText(tr("source_both"))
        self.desc_both.setText(tr("source_both_desc"))

    def initializePage(self):
        self._start_scan()

    def _start_scan(self):
        self._scan_done = False
        self._games.clear()
        self.completeChanged.emit()

        mode = self.btn_group.checkedId()
        scan_mo2 = mode in (0, 2)
        scan_vortex = mode in (1, 2)

        self.status_label.setText(tr("scanning"))
        self.result_label.setText("")

        self._worker = ScanWorker(scan_mo2=scan_mo2, scan_vortex=scan_vortex)
        self._worker.finished.connect(self._on_scan_done)
        self.btn_group.buttonClicked.connect(self._on_source_changed)
        self._worker.start()

    def _on_source_changed(self):
        """Re-scan when user changes source selection."""
        if self._worker and self._worker.isRunning():
            self._worker.wait()
        self._start_scan()

    def _on_scan_done(self, mo2_games, vortex_games):
        self._games = mo2_games + vortex_games
        self._scan_done = True

        mo2_count = len(mo2_games)
        vortex_count = len(vortex_games)
        total = len(self._games)

        parts = []
        if mo2_count:
            parts.append(tr("scan_mo2_found", count=mo2_count))
        if vortex_count:
            parts.append(tr("scan_vortex_found", count=vortex_count))

        if total > 0:
            self.status_label.setText(tr("scan_done"))
            self.result_label.setText("\n".join(parts))
            self.result_label.setProperty("class", "success")
        else:
            self.status_label.setText(tr("scan_none_found"))
            self.result_label.setText(tr("scan_none_found_desc"))
            self.result_label.setProperty("class", "warning")

        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)
        self.completeChanged.emit()

    def isComplete(self):
        return self._scan_done and len(self._games) > 0

    @property
    def games(self) -> list[ExportableGame]:
        return self._games


# ---------------------------------------------------------------------------
# Page 3 — Game Selection
# ---------------------------------------------------------------------------

class GameSelectPage(QWizardPage):
    def __init__(self, source_page: SourcePage, parent=None):
        super().__init__(parent)
        self.source_page = source_page
        self.setTitle(tr("game_select_title"))
        self.setSubTitle(tr("game_select_desc"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)

        # Table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "", tr("scan_game"), tr("source_label"), tr("scan_mods"), tr("game_size"),
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(0, 40)
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QTableWidget.NoSelection)
        layout.addWidget(self.table)

        # Buttons + total
        bottom = QHBoxLayout()
        self.btn_all = QPushButton(tr("game_select_all"))
        self.btn_all.clicked.connect(lambda: self._set_all(True))
        bottom.addWidget(self.btn_all)
        self.btn_none = QPushButton(tr("game_deselect_all"))
        self.btn_none.clicked.connect(lambda: self._set_all(False))
        bottom.addWidget(self.btn_none)
        bottom.addStretch()
        self.total_label = QLabel("")
        self.total_label.setProperty("class", "info-value")
        bottom.addWidget(self.total_label)
        layout.addLayout(bottom)

        # Duplicate warning
        self.warn_label = QLabel("")
        self.warn_label.setProperty("class", "warning")
        self.warn_label.setWordWrap(True)
        self.warn_label.hide()
        layout.addWidget(self.warn_label)

        self._checkboxes: list[QCheckBox] = []

    def retranslateUi(self):
        self.setTitle(tr("game_select_title"))
        self.setSubTitle(tr("game_select_desc"))
        self.table.setHorizontalHeaderLabels([
            "", tr("scan_game"), tr("source_label"), tr("scan_mods"), tr("game_size"),
        ])
        self.btn_all.setText(tr("game_select_all"))
        self.btn_none.setText(tr("game_deselect_all"))

    def initializePage(self):
        games = self.source_page.games
        self._checkboxes.clear()
        self.table.setRowCount(len(games))

        for row, game in enumerate(games):
            # Checkbox
            cb = QCheckBox()
            cb.setChecked(game.selected)
            cb.stateChanged.connect(self._update_total)
            self._checkboxes.append(cb)
            widget = QWidget()
            hl = QHBoxLayout(widget)
            hl.addWidget(cb)
            hl.setAlignment(Qt.AlignCenter)
            hl.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 0, widget)

            # Game name + instance path
            if game.instance_path:
                self.table.setRowHeight(row, 46)
                name_widget = QWidget()
                name_widget.setStyleSheet("background: transparent;")
                name_layout = QVBoxLayout(name_widget)
                name_layout.setContentsMargins(6, 3, 6, 3)
                name_layout.setSpacing(0)
                game_label = QLabel(game.game_name)
                game_label.setStyleSheet("color: #cdd6f4; font-size: 13px; background: transparent;")
                name_layout.addWidget(game_label)
                path_label = QLabel(game.instance_path)
                path_label.setStyleSheet("color: #6c7086; font-size: 10px; background: transparent;")
                name_layout.addWidget(path_label)
                self.table.setCellWidget(row, 1, name_widget)
            else:
                self.table.setItem(row, 1, QTableWidgetItem(game.game_name))
            # Source
            self.table.setItem(row, 2, QTableWidgetItem(game.source))
            # Mods
            self.table.setItem(row, 3, QTableWidgetItem(str(game.mod_count)))
            # Size
            self.table.setItem(row, 4, QTableWidgetItem(format_size(game.total_size_bytes)))

        # Check for duplicates (same game from MO2 + Vortex)
        self._check_duplicates(games)
        self._update_total()

    def _check_duplicates(self, games):
        seen: dict[str, list[str]] = {}
        for g in games:
            slug = g.nexus_slug.lower()
            seen.setdefault(slug, []).append(g.source)
        dupes = [slug for slug, sources in seen.items() if len(sources) > 1]

        if dupes:
            self.warn_label.setText(tr("game_duplicate_warning"))
            self.warn_label.show()
        else:
            self.warn_label.hide()

    def _set_all(self, checked: bool):
        for cb in self._checkboxes:
            cb.setChecked(checked)

    def _update_total(self):
        games = self.source_page.games
        total = 0
        count = 0
        for i, cb in enumerate(self._checkboxes):
            if cb.isChecked() and i < len(games):
                games[i].selected = True
                total += games[i].total_size_bytes
                count += 1
            elif i < len(games):
                games[i].selected = False

        self.total_label.setText(
            f"{count} {tr('games_selected')}  —  {format_size(total)}"
        )
        self.completeChanged.emit()

    def isComplete(self):
        return any(cb.isChecked() for cb in self._checkboxes)

    @property
    def selected_games(self) -> list[ExportableGame]:
        games = self.source_page.games
        return [g for i, g in enumerate(games)
                if i < len(self._checkboxes) and self._checkboxes[i].isChecked()]


# ---------------------------------------------------------------------------
# Page 4 — Export Options
# ---------------------------------------------------------------------------

class ExportOptionsPage(QWizardPage):
    def __init__(self, game_page: GameSelectPage, parent=None):
        super().__init__(parent)
        self.game_page = game_page
        self.setTitle(tr("export_options_title"))
        self.setSubTitle(tr("export_options_desc"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)

        # Mode selection
        self.mode_group = QGroupBox(tr("export_mode_title"))
        mode_layout = QVBoxLayout(self.mode_group)

        self.btn_group = QButtonGroup(self)

        # Without mods (metadata only)
        self.radio_meta = QRadioButton(tr("export_mode_metadata"))
        self.btn_group.addButton(self.radio_meta, 0)
        mode_layout.addWidget(self.radio_meta)
        self.desc_meta = QLabel(tr("export_mode_metadata_desc"))
        self.desc_meta.setWordWrap(True)
        self.desc_meta.setStyleSheet("margin-left: 28px; color: #a6adc8; font-size: 11px;")
        mode_layout.addWidget(self.desc_meta)

        self.size_meta = QLabel("")
        self.size_meta.setStyleSheet("margin-left: 28px; font-weight: bold;")
        mode_layout.addWidget(self.size_meta)

        mode_layout.addSpacing(12)

        # With mods (full export)
        self.radio_full = QRadioButton(tr("export_mode_full"))
        self.radio_full.setChecked(True)
        self.btn_group.addButton(self.radio_full, 1)
        mode_layout.addWidget(self.radio_full)
        self.desc_full = QLabel(tr("export_mode_full_desc"))
        self.desc_full.setWordWrap(True)
        self.desc_full.setStyleSheet("margin-left: 28px; color: #a6adc8; font-size: 11px;")
        mode_layout.addWidget(self.desc_full)

        self.size_full = QLabel("")
        self.size_full.setStyleSheet("margin-left: 28px; font-weight: bold;")
        mode_layout.addWidget(self.size_full)

        # Compression selector (only for full export)
        self.compress_widget = QWidget()
        compress_layout = QHBoxLayout(self.compress_widget)
        compress_layout.setContentsMargins(28, 4, 0, 0)
        self.compress_label = QLabel(tr("compression_label") + ":")
        compress_layout.addWidget(self.compress_label)
        self.compress_combo = QComboBox()
        self.compress_combo.addItem(tr("compression_none"), COMPRESS_NONE)
        self.compress_combo.addItem(tr("compression_low"), COMPRESS_LOW)
        self.compress_combo.addItem(tr("compression_strong"), COMPRESS_STRONG)
        self.compress_combo.setCurrentIndex(1)  # Default: Low
        compress_layout.addWidget(self.compress_combo)
        compress_layout.addStretch()
        mode_layout.addWidget(self.compress_widget)

        self.btn_group.buttonClicked.connect(self._on_mode_changed)

        layout.addWidget(self.mode_group)
        layout.addSpacing(10)

        # Warnings
        self.warn_no_nexus = QLabel("")
        self.warn_no_nexus.setProperty("class", "warning")
        self.warn_no_nexus.setWordWrap(True)
        self.warn_no_nexus.hide()
        layout.addWidget(self.warn_no_nexus)

        self.warn_no_files = QLabel("")
        self.warn_no_files.setProperty("class", "error")
        self.warn_no_files.setWordWrap(True)
        self.warn_no_files.hide()
        layout.addWidget(self.warn_no_files)

        layout.addStretch()

    def _on_mode_changed(self):
        is_full = self.btn_group.checkedId() == 1
        self.compress_widget.setVisible(is_full)

    def retranslateUi(self):
        self.setTitle(tr("export_options_title"))
        self.setSubTitle(tr("export_options_desc"))
        self.mode_group.setTitle(tr("export_mode_title"))
        self.radio_meta.setText(tr("export_mode_metadata"))
        self.desc_meta.setText(tr("export_mode_metadata_desc"))
        self.radio_full.setText(tr("export_mode_full"))
        self.desc_full.setText(tr("export_mode_full_desc"))
        self.compress_label.setText(tr("compression_label") + ":")
        idx = self.compress_combo.currentIndex()
        self.compress_combo.clear()
        self.compress_combo.addItem(tr("compression_none"), COMPRESS_NONE)
        self.compress_combo.addItem(tr("compression_low"), COMPRESS_LOW)
        self.compress_combo.addItem(tr("compression_strong"), COMPRESS_STRONG)
        self.compress_combo.setCurrentIndex(idx)

    def initializePage(self):
        self._on_mode_changed()
        games = self.game_page.selected_games
        total_size = sum(g.total_size_bytes for g in games)
        total_mods = sum(g.mod_count for g in games)
        total_no_nexus = sum(g.without_nexus_id for g in games)
        games_no_files = [g for g in games if not g.has_mod_files]

        self.size_meta.setText(
            tr("export_size_metadata", size="~50 KB")
        )
        self.size_full.setText(
            tr("export_size_full", size=format_size(total_size))
        )

        # Warning: mods without Nexus ID
        if total_no_nexus > 0:
            self.warn_no_nexus.setText(
                tr("export_warning_no_nexus", count=total_no_nexus, total=total_mods)
            )
            self.warn_no_nexus.show()
        else:
            self.warn_no_nexus.hide()

        # Warning: games where mod files can't be found
        if games_no_files:
            names = ", ".join(g.game_name for g in games_no_files)
            self.warn_no_files.setText(
                tr("export_warning_no_files", games=names)
            )
            self.warn_no_files.show()
        else:
            self.warn_no_files.hide()

    @property
    def include_mods(self) -> bool:
        return self.btn_group.checkedId() == 1

    @property
    def compression(self) -> int:
        return self.compress_combo.currentData() or COMPRESS_LOW


# ---------------------------------------------------------------------------
# Page 5 — Target Selection
# ---------------------------------------------------------------------------

class TargetPage(QWizardPage):
    def __init__(self, game_page: GameSelectPage, options_page: ExportOptionsPage, parent=None):
        super().__init__(parent)
        self.game_page = game_page
        self.options_page = options_page
        self.setTitle(tr("target_title"))
        self.setSubTitle(tr("target_desc"))
        self._complete = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)

        # Target type
        self.target_group = QGroupBox(tr("target_where"))
        target_layout = QVBoxLayout(self.target_group)

        self.btn_group = QButtonGroup(self)

        self.radio_folder = QRadioButton(tr("target_folder"))
        self.radio_folder.setChecked(True)
        self.btn_group.addButton(self.radio_folder, 0)
        target_layout.addWidget(self.radio_folder)
        self.desc_folder = QLabel(tr("target_folder_desc"))
        self.desc_folder.setWordWrap(True)
        self.desc_folder.setStyleSheet("margin-left: 28px; color: #a6adc8; font-size: 11px;")
        target_layout.addWidget(self.desc_folder)

        target_layout.addSpacing(8)

        self.radio_network = QRadioButton(tr("export_mode_network"))
        self.btn_group.addButton(self.radio_network, 1)
        target_layout.addWidget(self.radio_network)
        self.desc_network = QLabel(tr("export_mode_network_desc"))
        self.desc_network.setWordWrap(True)
        self.desc_network.setStyleSheet("margin-left: 28px; color: #a6adc8; font-size: 11px;")
        target_layout.addWidget(self.desc_network)

        layout.addWidget(self.target_group)
        layout.addSpacing(12)

        # Path selection (folder mode)
        self.path_group = QGroupBox(tr("export_target"))
        path_layout = QHBoxLayout(self.path_group)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(tr("target_select_folder"))
        self.path_edit.textChanged.connect(self._on_path_changed)
        path_layout.addWidget(self.path_edit)
        self.btn_browse = QPushButton(tr("browse"))
        self.btn_browse.clicked.connect(self._browse)
        path_layout.addWidget(self.btn_browse)
        layout.addWidget(self.path_group)

        # Space info
        self.space_label = QLabel("")
        self.space_label.setWordWrap(True)
        layout.addWidget(self.space_label)

        self.btn_group.buttonClicked.connect(self._on_mode_changed)
        self._on_mode_changed()

        layout.addStretch()

    def retranslateUi(self):
        self.setTitle(tr("target_title"))
        self.setSubTitle(tr("target_desc"))
        self.target_group.setTitle(tr("target_where"))
        self.radio_folder.setText(tr("target_folder"))
        self.desc_folder.setText(tr("target_folder_desc"))
        self.radio_network.setText(tr("export_mode_network"))
        self.desc_network.setText(tr("export_mode_network_desc"))
        self.path_group.setTitle(tr("export_target"))
        self.path_edit.setPlaceholderText(tr("target_select_folder"))
        self.btn_browse.setText(tr("browse"))

    def _on_mode_changed(self):
        is_folder = self.btn_group.checkedId() == 0
        self.path_group.setVisible(is_folder)
        self.space_label.setVisible(is_folder)
        if not is_folder:
            self._complete = True
        else:
            self._complete = bool(self.path_edit.text().strip())
        self.completeChanged.emit()

    def _on_path_changed(self):
        path = self.path_edit.text().strip()
        self._complete = bool(path)

        if path:
            try:
                import shutil
                usage = shutil.disk_usage(path if Path(path).exists() else Path(path).anchor)
                available = format_size(usage.free)
                needed = format_size(
                    sum(g.total_size_bytes for g in self.game_page.selected_games)
                    if self.options_page.include_mods else 1024 * 50
                )
                self.space_label.setText(
                    tr("target_space_info", available=available, needed=needed)
                )
            except (OSError, ValueError):
                self.space_label.setText("")
        else:
            self.space_label.setText("")

        self.completeChanged.emit()

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, tr("target_select_folder"))
        if path:
            self.path_edit.setText(path)

    def isComplete(self):
        return self._complete

    @property
    def is_network(self) -> bool:
        return self.btn_group.checkedId() == 1

    @property
    def target_path(self) -> str:
        return self.path_edit.text().strip()


# ---------------------------------------------------------------------------
# Page 6 — Progress + Done
# ---------------------------------------------------------------------------

class ProgressPage(QWizardPage):
    def __init__(self, game_page, options_page, target_page, parent=None):
        super().__init__(parent)
        self.game_page = game_page
        self.options_page = options_page
        self.target_page = target_page
        self.setTitle(tr("exporter_step_progress"))
        self._complete = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)

        self.status_label = QLabel(tr("progress_reading"))
        self.status_label.setProperty("class", "section")
        layout.addWidget(self.status_label)

        layout.addSpacing(8)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #a6adc8; font-size: 11px;")
        layout.addWidget(self.detail_label)

        # Time / speed info
        self.time_label = QLabel("")
        self.time_label.setStyleSheet("color: #bac2de; font-size: 12px; margin-top: 4px;")
        layout.addWidget(self.time_label)

        layout.addSpacing(16)

        # Network mode widgets
        self.network_group = QGroupBox(tr("network_title"))
        net_layout = QVBoxLayout(self.network_group)
        self.net_info = QLabel()
        self.net_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        net_layout.addWidget(self.net_info)
        self.net_status = QLabel(tr("network_waiting"))
        net_layout.addWidget(self.net_status)
        layout.addWidget(self.network_group)
        self.network_group.hide()

        # Done section
        self.done_label = QLabel("")
        self.done_label.setProperty("class", "success")
        self.done_label.setWordWrap(True)
        self.done_label.hide()
        layout.addWidget(self.done_label)

        layout.addStretch()

        self._worker = None
        self._server = None

    def retranslateUi(self):
        self.setTitle(tr("exporter_step_progress"))
        self.network_group.setTitle(tr("network_title"))

    def initializePage(self):
        self._complete = False
        games = self.game_page.selected_games
        include_mods = self.options_page.include_mods

        if self.target_page.is_network:
            self._start_network(games, include_mods)
        else:
            compression = self.options_page.compression
            self._start_export(games, include_mods, self.target_page.target_path, compression)

    def _start_export(self, games, include_mods, target_path, compression=COMPRESS_LOW):
        self.network_group.hide()
        self.done_label.hide()
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.status_label.setText(tr("progress_reading"))
        self.detail_label.setText("")
        self.time_label.setText("")
        self._start_time = time.time()

        self._worker = ExportWorker(games, target_path, include_mods, compression)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _start_network(self, games, include_mods):
        self.progress_bar.hide()
        self.network_group.show()
        self.done_label.hide()

        # For network mode, use the first game's MO2 instance
        # (server currently supports single instance)
        first_mo2 = None
        for g in games:
            if g._mo2_instance is not None:
                first_mo2 = g._mo2_instance
                break

        if first_mo2 is None:
            self.status_label.setText(tr("error"))
            self.detail_label.setText(tr("network_vortex_unsupported"))
            return

        self._server = TransferServer(
            instance=first_mo2,
            on_event=self._on_network_event,
        )
        self._server.start()

        self.net_info.setText(
            f"<b>{tr('network_ip', ip=self._server.ip)}</b><br>"
            f"{tr('network_port', port=self._server.port)}<br>"
            f"<b style='font-size: 18px;'>{tr('network_pin', pin=self._server.pin)}</b>"
        )
        self.net_status.setText(tr("network_waiting"))
        self.status_label.setText(tr("network_instructions"))

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m:02d}:{s:02d}"
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"

    def _on_progress(self, status, current, total):
        if total > 0:
            pct = int(current / total * 100)
            self.progress_bar.setValue(pct)
        self.detail_label.setText(status)
        self.status_label.setText(
            tr("progress_size", current=format_size(current), total=format_size(total))
        )

        # Time display
        elapsed = time.time() - self._start_time
        if current > 0 and total > 0 and elapsed > 1:
            speed = current / elapsed
            remaining = (total - current) / speed if speed > 0 else 0
            self.time_label.setText(
                tr("progress_time",
                   elapsed=self._fmt_time(elapsed),
                   speed=format_size(int(speed)),
                   remaining=self._fmt_time(remaining))
            )
        elif elapsed > 1:
            self.time_label.setText(
                tr("progress_elapsed", elapsed=self._fmt_time(elapsed))
            )

    def _on_done(self, path):
        self._complete = True
        self.progress_bar.setValue(100)
        elapsed = time.time() - self._start_time
        self.status_label.setText(tr("progress_done"))
        self.detail_label.setText("")
        self.time_label.setText(tr("progress_total_time", time=self._fmt_time(elapsed)))
        self.done_label.setText(
            tr("done_full", path=path) + "\n\n" + tr("done_next_steps") + "\n"
            + tr("done_step1_full") + "\n" + tr("done_step2") + "\n" + tr("done_step3")
        )
        self.done_label.show()
        self.completeChanged.emit()

    def _on_error(self, error):
        self.status_label.setText(tr("error"))
        self.detail_label.setText("")
        self.done_label.setText(error)
        self.done_label.setProperty("class", "error")
        self.done_label.style().unpolish(self.done_label)
        self.done_label.style().polish(self.done_label)
        self.done_label.show()

    def _on_network_event(self, event, **kwargs):
        if event == "authenticated":
            self.net_status.setText(
                tr("network_connected", client=kwargs.get("client", ""))
            )
        elif event == "file_transfer":
            self.net_status.setText(
                tr("network_transferring",
                   file=f"{kwargs.get('mod', '')}/{kwargs.get('file', '')}")
            )
        elif event == "gbw_downloaded":
            self._complete = True
            self.net_status.setText(tr("progress_done"))
            self.completeChanged.emit()

    def isComplete(self):
        return self._complete

    def cleanupPage(self):
        if self._server:
            self._server.stop()
            self._server = None


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

class ExporterWizard(QWizard):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("exporter_title"))
        self.setMinimumSize(780, 580)
        self.setWizardStyle(QWizard.ModernStyle)

        # Pages
        self.welcome_page = WelcomePage()
        self.source_page = SourcePage()
        self.game_page = GameSelectPage(self.source_page)
        self.options_page = ExportOptionsPage(self.game_page)
        self.target_page = TargetPage(self.game_page, self.options_page)
        self.progress_page = ProgressPage(
            self.game_page, self.options_page, self.target_page
        )

        self.addPage(self.welcome_page)
        self.addPage(self.source_page)
        self.addPage(self.game_page)
        self.addPage(self.options_page)
        self.addPage(self.target_page)
        self.addPage(self.progress_page)

        # Mark last page as final
        self.progress_page.setFinalPage(True)

        # Button text
        self._retranslate_buttons()

    def _retranslate_buttons(self):
        self.setButtonText(QWizard.NextButton, tr("next") + "  →")
        self.setButtonText(QWizard.BackButton, "←  " + tr("back"))
        self.setButtonText(QWizard.CancelButton, tr("cancel"))
        self.setButtonText(QWizard.FinishButton, tr("close"))

    def retranslateAll(self):
        self.setWindowTitle(tr("exporter_title"))
        self._retranslate_buttons()
        for page_id in self.pageIds():
            page = self.page(page_id)
            if hasattr(page, "retranslateUi"):
                page.retranslateUi()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GoodbyeWindows")
    app.setApplicationVersion("1.0.0")
    app.setStyleSheet(STYLESHEET)

    wizard = ExporterWizard()
    wizard.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

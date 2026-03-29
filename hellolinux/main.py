"""HelloLinux — Linux Importer GUI.

Wizard-style interface (5 pages):
  Page 1: Welcome + language selection
  Page 2: Source selection (.gbw / folder / NTFS / network)
  Page 3: Preview (all discovered games, mod stats)
  Page 4: Configuration (target manager, game paths)
  Page 5: Progress + done
"""

import sys
import tempfile
from dataclasses import dataclass, field
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
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.i18n import tr, set_locale, detect_locale
from common.mo2_reader import format_size
from common.migration_format import (
    MigrationPackage,
    MigrationManifest,
    load_gbw,
    peek_gbw_manifest,
    gbw_has_mods,
    extract_mods_from_gbw,
    create_package_from_mo2,
)
from hellolinux.detector import (
    find_ntfs_partitions,
    NTFSPartition,
)
from hellolinux.client import TransferClient
from hellolinux.importer_anvil import import_to_anvil
from hellolinux.importer_amethyst import import_to_amethyst


# ---------------------------------------------------------------------------
# Stylesheet — Catppuccin Mocha with green accent
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
    color: #a6e3a1;
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
    background-color: #a6e3a1;
    color: #1e1e2e;
    font-weight: bold;
}
QPushButton[class="primary"]:hover {
    background-color: #c6f0c2;
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
    background-color: #a6e3a1;
    border-radius: 8px;
}
QScrollArea {
    border: none;
    background-color: #1e1e2e;
}
QTextEdit {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px;
}
"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ImportableGame:
    """A game discovered from a source, ready for import."""
    game_name: str
    package: MigrationPackage
    mod_source_dir: Path | None = None
    selected: bool = True
    game_path: str = ""

    @property
    def mod_count(self) -> int:
        return len([m for m in self.package.mods if not m.is_separator])

    @property
    def separator_count(self) -> int:
        return len([m for m in self.package.mods if m.is_separator])

    @property
    def with_nexus_id(self) -> int:
        return len([m for m in self.package.mods
                     if not m.is_separator and m.nexus_id > 0])

    @property
    def without_nexus_id(self) -> int:
        return self.mod_count - self.with_nexus_id

    @property
    def total_size(self) -> int:
        return self.package.manifest.total_size_bytes

    @property
    def has_mod_files(self) -> bool:
        return self.mod_source_dir is not None and self.mod_source_dir.is_dir()


# ---------------------------------------------------------------------------
# Worker threads
# ---------------------------------------------------------------------------

class NTFSScanWorker(QThread):
    finished = Signal(list)

    def run(self):
        partitions = find_ntfs_partitions()
        self.finished.emit(partitions)


class ImportWorker(QThread):
    progress = Signal(str, int, int)
    game_started = Signal(str)
    finished = Signal(list)  # list of (game_name, result_path)
    error = Signal(str)

    def __init__(self, games, target, parent=None):
        super().__init__(parent)
        self.games = games
        self.target = target

    def run(self):
        results = []
        try:
            for game in self.games:
                if not game.selected:
                    continue
                self.game_started.emit(game.game_name)

                if self.target == "anvil":
                    path = import_to_anvil(
                        game.package,
                        instance_name=game.game_name,
                        game_path=game.game_path,
                        mod_source_dir=game.mod_source_dir,
                        progress_callback=lambda s, c, t: self.progress.emit(s, c, t),
                    )
                else:
                    path = import_to_amethyst(
                        game.package,
                        profile_name="Default",
                        mod_source_dir=game.mod_source_dir,
                        progress_callback=lambda s, c, t: self.progress.emit(s, c, t),
                    )
                results.append((game.game_name, str(path)))

            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Page 1 — Welcome
# ---------------------------------------------------------------------------

class WelcomePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.addStretch(2)

        # Title
        self.title_label = QLabel("HelloLinux")
        self.title_label.setProperty("class", "title")
        self.title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title_label)

        layout.addSpacing(4)

        # Subtitle
        self.subtitle_label = QLabel(tr("app_subtitle"))
        self.subtitle_label.setProperty("class", "subtitle")
        self.subtitle_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.subtitle_label)

        layout.addSpacing(16)

        # Description
        self.desc_label = QLabel(tr("importer_welcome"))
        self.desc_label.setAlignment(Qt.AlignCenter)
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label)

        layout.addSpacing(24)

        # Language selector
        lang_box = QHBoxLayout()
        lang_box.addStretch()
        self.lang_label = QLabel(tr("language") + ":")
        lang_box.addWidget(self.lang_label)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Deutsch", "de")
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
        self.desc_label.setText(tr("importer_welcome"))
        self.lang_label.setText(tr("language") + ":")


# ---------------------------------------------------------------------------
# Page 2 — Source Selection
# ---------------------------------------------------------------------------

class SourcePage(QWizardPage):
    """Select import source: .gbw file, folder, NTFS, or network."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._games: list[ImportableGame] = []
        self._complete = False
        self._ntfs_partitions: list[NTFSPartition] = []
        self._ntfs_worker = None
        self._network_client: TransferClient | None = None

        layout = QVBoxLayout(self)

        # Source type radios
        self.btn_group = QButtonGroup(self)

        self.radio_file = QRadioButton(tr("source_file"))
        self.radio_file.setChecked(True)
        self.btn_group.addButton(self.radio_file, 0)
        layout.addWidget(self.radio_file)
        self.desc_file = QLabel(tr("source_file_desc"))
        self.desc_file.setWordWrap(True)
        self.desc_file.setStyleSheet("margin-left: 24px; color: #a6adc8;")
        layout.addWidget(self.desc_file)

        layout.addSpacing(6)

        self.radio_folder = QRadioButton(tr("source_folder"))
        self.btn_group.addButton(self.radio_folder, 1)
        layout.addWidget(self.radio_folder)
        self.desc_folder = QLabel(tr("source_folder_desc"))
        self.desc_folder.setWordWrap(True)
        self.desc_folder.setStyleSheet("margin-left: 24px; color: #a6adc8;")
        layout.addWidget(self.desc_folder)

        layout.addSpacing(6)

        self.radio_ntfs = QRadioButton(tr("source_ntfs"))
        self.btn_group.addButton(self.radio_ntfs, 2)
        layout.addWidget(self.radio_ntfs)
        self.desc_ntfs = QLabel(tr("source_ntfs_desc"))
        self.desc_ntfs.setWordWrap(True)
        self.desc_ntfs.setStyleSheet("margin-left: 24px; color: #a6adc8;")
        layout.addWidget(self.desc_ntfs)

        layout.addSpacing(6)

        self.radio_network = QRadioButton(tr("source_network"))
        self.btn_group.addButton(self.radio_network, 3)
        layout.addWidget(self.radio_network)
        self.desc_network = QLabel(tr("source_network_desc"))
        self.desc_network.setWordWrap(True)
        self.desc_network.setStyleSheet("margin-left: 24px; color: #a6adc8;")
        layout.addWidget(self.desc_network)

        layout.addSpacing(12)

        # --- Action area ---
        self.action_group = QGroupBox()
        action_layout = QVBoxLayout(self.action_group)

        # File/folder browse
        self.browse_widget = QWidget()
        browse_layout = QHBoxLayout(self.browse_widget)
        browse_layout.setContentsMargins(0, 0, 0, 0)
        self.path_edit = QLineEdit()
        browse_layout.addWidget(self.path_edit)
        self.btn_browse = QPushButton(tr("browse"))
        self.btn_browse.clicked.connect(self._browse)
        browse_layout.addWidget(self.btn_browse)
        action_layout.addWidget(self.browse_widget)

        # Network fields
        self.net_widget = QWidget()
        net_layout = QVBoxLayout(self.net_widget)
        net_layout.setContentsMargins(0, 0, 0, 0)
        ip_row = QHBoxLayout()
        self.ip_label = QLabel(tr("network_enter_ip"))
        ip_row.addWidget(self.ip_label)
        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("192.168.1.42")
        ip_row.addWidget(self.ip_edit)
        net_layout.addLayout(ip_row)
        pin_row = QHBoxLayout()
        self.pin_label = QLabel(tr("network_enter_pin"))
        pin_row.addWidget(self.pin_label)
        self.pin_edit = QLineEdit()
        self.pin_edit.setMaxLength(4)
        self.pin_edit.setFixedWidth(80)
        pin_row.addWidget(self.pin_edit)
        pin_row.addStretch()
        net_layout.addLayout(pin_row)
        self.btn_connect = QPushButton(tr("hl_connect"))
        self.btn_connect.clicked.connect(self._connect_network)
        net_layout.addWidget(self.btn_connect)
        action_layout.addWidget(self.net_widget)

        # NTFS scan
        self.ntfs_widget = QWidget()
        ntfs_layout = QVBoxLayout(self.ntfs_widget)
        ntfs_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_scan_ntfs = QPushButton(tr("hl_scan_ntfs"))
        self.btn_scan_ntfs.clicked.connect(self._scan_ntfs)
        ntfs_layout.addWidget(self.btn_scan_ntfs)
        self.ntfs_table = QTableWidget(0, 3)
        self.ntfs_table.setHorizontalHeaderLabels([
            tr("ntfs_partition"), tr("scan_game"), tr("scan_mods"),
        ])
        self.ntfs_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self.ntfs_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.ntfs_table.setSelectionMode(QTableWidget.MultiSelection)
        ntfs_layout.addWidget(self.ntfs_table)
        action_layout.addWidget(self.ntfs_widget)

        layout.addWidget(self.action_group)

        # Status
        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        layout.addStretch()

        self.btn_group.buttonClicked.connect(self._on_mode_changed)
        self._on_mode_changed()

    def _on_mode_changed(self):
        mode = self.btn_group.checkedId()
        self.browse_widget.setVisible(mode in (0, 1))
        self.net_widget.setVisible(mode == 3)
        self.ntfs_widget.setVisible(mode == 2)

    def _browse(self):
        mode = self.btn_group.checkedId()
        if mode == 0:
            path, _ = QFileDialog.getOpenFileName(
                self, tr("source_file"),
                str(Path.home()),
                "GoodbyeWindows (*.gbw);;All Files (*)",
            )
        else:
            path = QFileDialog.getExistingDirectory(self, tr("source_folder"))

        if path:
            self.path_edit.setText(path)
            self._load_source(path, mode)

    def _load_source(self, path: str, mode: int):
        self._games.clear()
        self._complete = False

        try:
            p = Path(path)
            if mode == 0:
                # Single .gbw file — check if mod files are embedded
                package = load_gbw(p)
                mod_source = None
                if gbw_has_mods(p):
                    tmp = Path(tempfile.mkdtemp(prefix="gbw_mods_"))
                    mod_source = extract_mods_from_gbw(p, tmp)
                self._games.append(ImportableGame(
                    game_name=package.manifest.game_name or "Unknown",
                    package=package,
                    mod_source_dir=mod_source,
                ))
            elif mode == 1:
                # Folder: check for multi-game or single-game export
                self._scan_export_folder(p)

            if self._games:
                self._complete = True
                count = len(self._games)
                if count == 1:
                    g = self._games[0]
                    self.status_label.setText(
                        f"{g.game_name} — {g.mod_count} mods"
                    )
                    self.status_label.setProperty("class", "success")
                else:
                    self.status_label.setText(
                        tr("hl_games_found", count=count)
                    )
                    self.status_label.setProperty("class", "success")
            else:
                self.status_label.setText(tr("hl_no_gbw_found"))
                self.status_label.setProperty("class", "warning")

        except Exception as e:
            self.status_label.setText(f"{tr('error')}: {e}")
            self.status_label.setProperty("class", "error")

        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.completeChanged.emit()

    def _load_gbw_file(self, gbw_path: Path):
        """Load a .gbw and extract mods if embedded."""
        package = load_gbw(gbw_path)
        mod_source = None
        if gbw_has_mods(gbw_path):
            tmp = Path(tempfile.mkdtemp(prefix="gbw_mods_"))
            mod_source = extract_mods_from_gbw(gbw_path, tmp)
        self._games.append(ImportableGame(
            game_name=package.manifest.game_name or gbw_path.stem,
            package=package,
            mod_source_dir=mod_source,
        ))

    def _scan_export_folder(self, folder: Path):
        """Scan a folder for .gbw files (new format) or migration.gbw (old format)."""
        # New format: folder/*.gbw (one file per game, mods inside)
        gbw_files = sorted(folder.glob("*.gbw"))
        if gbw_files:
            for gbw in gbw_files:
                self._load_gbw_file(gbw)
            return

        # Old format: folder/migration.gbw + mods/
        direct_gbw = folder / "migration.gbw"
        if direct_gbw.exists():
            package = load_gbw(direct_gbw)
            mods_dir = folder / "mods"
            self._games.append(ImportableGame(
                game_name=package.manifest.game_name or "Unknown",
                package=package,
                mod_source_dir=mods_dir if mods_dir.is_dir() else None,
            ))
            return

        # Old multi-game format: folder/*/migration.gbw
        for sub in sorted(folder.iterdir()):
            if not sub.is_dir():
                continue
            gbw = sub / "migration.gbw"
            if gbw.exists():
                package = load_gbw(gbw)
                mods_dir = sub / "mods"
                self._games.append(ImportableGame(
                    game_name=package.manifest.game_name or sub.name,
                    package=package,
                    mod_source_dir=mods_dir if mods_dir.is_dir() else None,
                ))

    def _scan_ntfs(self):
        self.ntfs_table.setRowCount(0)
        self.btn_scan_ntfs.setEnabled(False)
        self.status_label.setText(tr("ntfs_scanning"))
        self.status_label.setProperty("class", "")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self._ntfs_worker = NTFSScanWorker()
        self._ntfs_worker.finished.connect(self._on_ntfs_done)
        self._ntfs_worker.start()

    def _on_ntfs_done(self, partitions):
        self._ntfs_partitions = partitions
        self.btn_scan_ntfs.setEnabled(True)

        if not partitions:
            self.status_label.setText(tr("ntfs_none"))
            self.status_label.setProperty("class", "warning")
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
            return

        # Collect all MO2 instances across partitions
        rows = []
        for part in partitions:
            for inst in part.mo2_instances:
                rows.append((part, inst))

        if not rows:
            self.status_label.setText(
                tr("ntfs_found", count=len(partitions))
                + " — " + tr("hl_no_gbw_found")
            )
            self.status_label.setProperty("class", "warning")
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
            return

        self.status_label.setText(tr("ntfs_found", count=len(partitions)))
        self.status_label.setProperty("class", "success")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

        self.ntfs_table.setRowCount(len(rows))
        for row_idx, (part, inst) in enumerate(rows):
            self.ntfs_table.setItem(
                row_idx, 0, QTableWidgetItem(f"{part.device} ({part.mount_point})")
            )
            self.ntfs_table.setItem(
                row_idx, 1, QTableWidgetItem(inst.game_name or "Unknown")
            )
            self.ntfs_table.setItem(
                row_idx, 2, QTableWidgetItem(str(len(inst.mod_meta)))
            )

        self.ntfs_table.selectAll()
        self.ntfs_table.itemSelectionChanged.connect(self._on_ntfs_select)
        self._on_ntfs_select()

    def _on_ntfs_select(self):
        selected_rows = {idx.row() for idx in self.ntfs_table.selectionModel().selectedRows()}
        self._games.clear()

        idx = 0
        for part in self._ntfs_partitions:
            for inst in part.mo2_instances:
                if idx in selected_rows:
                    package = create_package_from_mo2(inst)
                    package.manifest.has_mod_files = True
                    self._games.append(ImportableGame(
                        game_name=inst.game_name or "Unknown",
                        package=package,
                        mod_source_dir=inst.mods_dir,
                    ))
                idx += 1

        self._complete = bool(self._games)
        self.completeChanged.emit()

    def _connect_network(self):
        ip = self.ip_edit.text().strip()
        pin = self.pin_edit.text().strip()
        if not ip or not pin:
            self.status_label.setText(tr("hl_ip_pin_required"))
            self.status_label.setProperty("class", "warning")
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
            return

        self.status_label.setText(tr("network_connecting"))
        self.status_label.setProperty("class", "")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

        client = TransferClient(ip)

        if not client.ping():
            self.status_label.setText(
                tr("network_failed", error="Server not reachable")
            )
            self.status_label.setProperty("class", "error")
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
            return

        if not client.authenticate(pin):
            self.status_label.setText(tr("network_failed", error="Wrong PIN"))
            self.status_label.setProperty("class", "error")
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
            return

        self._network_client = client

        # Download .gbw
        tmp = Path(tempfile.mkdtemp(prefix="gbw_"))
        gbw_path = client.download_gbw(tmp / "migration.gbw")
        package = load_gbw(gbw_path)

        self._games.clear()
        self._games.append(ImportableGame(
            game_name=package.manifest.game_name or "Unknown",
            package=package,
        ))

        self._complete = True
        self.status_label.setText(
            tr("network_connected", client=ip)
            + f" — {self._games[0].game_name}"
        )
        self.status_label.setProperty("class", "success")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.completeChanged.emit()

    def isComplete(self):
        return self._complete

    def get_games(self) -> list[ImportableGame]:
        return self._games

    def retranslateUi(self):
        self.radio_file.setText(tr("source_file"))
        self.desc_file.setText(tr("source_file_desc"))
        self.radio_folder.setText(tr("source_folder"))
        self.desc_folder.setText(tr("source_folder_desc"))
        self.radio_ntfs.setText(tr("source_ntfs"))
        self.desc_ntfs.setText(tr("source_ntfs_desc"))
        self.radio_network.setText(tr("source_network"))
        self.desc_network.setText(tr("source_network_desc"))
        self.btn_browse.setText(tr("browse"))
        self.ip_label.setText(tr("network_enter_ip"))
        self.pin_label.setText(tr("network_enter_pin"))
        self.btn_connect.setText(tr("hl_connect"))
        self.btn_scan_ntfs.setText(tr("hl_scan_ntfs"))
        self.ntfs_table.setHorizontalHeaderLabels([
            tr("ntfs_partition"), tr("scan_game"), tr("scan_mods"),
        ])


# ---------------------------------------------------------------------------
# Page 3 — Preview
# ---------------------------------------------------------------------------

class PreviewPage(QWizardPage):
    def __init__(self, source_page: SourcePage, parent=None):
        super().__init__(parent)
        self.source_page = source_page

        layout = QVBoxLayout(self)

        self.title_label = QLabel(tr("hl_preview_title"))
        self.title_label.setProperty("class", "section")
        layout.addWidget(self.title_label)

        self.desc_label = QLabel(tr("hl_preview_desc"))
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label)

        layout.addSpacing(8)

        # Games table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            tr("scan_game"), tr("scan_mods"), tr("game_size"),
            "Nexus ID", tr("hl_has_files"),
        ])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        # Summary
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        # Hint
        self.hint_label = QLabel()
        self.hint_label.setWordWrap(True)
        self.hint_label.setProperty("class", "warning")
        self.hint_label.setVisible(False)
        layout.addWidget(self.hint_label)

    def initializePage(self):
        games = self.source_page.get_games()
        self.table.setRowCount(len(games))

        total_mods = 0
        total_size = 0
        total_no_nexus = 0
        any_without_files = False

        for row, game in enumerate(games):
            self.table.setItem(row, 0, QTableWidgetItem(game.game_name))
            self.table.setItem(row, 1, QTableWidgetItem(str(game.mod_count)))
            self.table.setItem(row, 2, QTableWidgetItem(
                format_size(game.total_size)
            ))
            self.table.setItem(row, 3, QTableWidgetItem(
                f"{game.with_nexus_id} / {game.mod_count}"
            ))
            has_files = tr("yes") if game.has_mod_files else tr("no")
            self.table.setItem(row, 4, QTableWidgetItem(has_files))

            total_mods += game.mod_count
            total_size += game.total_size
            total_no_nexus += game.without_nexus_id
            if not game.has_mod_files:
                any_without_files = True

        lines = []
        if len(games) > 1:
            lines.append(tr("hl_multi_game_hint", count=len(games)))
        lines.append(f"{total_mods} mods — {format_size(total_size)}")
        self.summary_label.setText("\n".join(lines))

        # Warnings
        if any_without_files and total_no_nexus > 0:
            self.hint_label.setText(tr("hl_metadata_only", count=total_no_nexus))
            self.hint_label.setVisible(True)
        elif any_without_files:
            self.hint_label.setText(tr("hl_needs_download"))
            self.hint_label.setVisible(True)
        else:
            self.hint_label.setVisible(False)

    def retranslateUi(self):
        self.title_label.setText(tr("hl_preview_title"))
        self.desc_label.setText(tr("hl_preview_desc"))
        self.table.setHorizontalHeaderLabels([
            tr("scan_game"), tr("scan_mods"), tr("game_size"),
            "Nexus ID", tr("hl_has_files"),
        ])


# ---------------------------------------------------------------------------
# Page 4 — Configuration
# ---------------------------------------------------------------------------

class ConfigPage(QWizardPage):
    """Target manager + per-game configuration."""

    def __init__(self, source_page: SourcePage, parent=None):
        super().__init__(parent)
        self.source_page = source_page
        self._game_path_edits: list[tuple[QLabel, QLineEdit, QPushButton]] = []

        layout = QVBoxLayout(self)

        self.desc_label = QLabel(tr("hl_config_desc"))
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label)

        layout.addSpacing(8)

        # Target manager
        self.target_label = QLabel(tr("hl_target_manager"))
        self.target_label.setProperty("class", "section")
        layout.addWidget(self.target_label)

        self.btn_group = QButtonGroup(self)

        self.radio_anvil = QRadioButton(tr("target_anvil"))
        self.radio_anvil.setChecked(True)
        self.btn_group.addButton(self.radio_anvil, 0)
        layout.addWidget(self.radio_anvil)
        self.desc_anvil = QLabel(tr("target_anvil_desc"))
        self.desc_anvil.setWordWrap(True)
        self.desc_anvil.setStyleSheet("margin-left: 24px; color: #a6adc8;")
        layout.addWidget(self.desc_anvil)

        layout.addSpacing(4)

        self.radio_amethyst = QRadioButton(tr("target_amethyst"))
        self.btn_group.addButton(self.radio_amethyst, 1)
        layout.addWidget(self.radio_amethyst)
        self.desc_amethyst = QLabel(tr("target_amethyst_desc"))
        self.desc_amethyst.setWordWrap(True)
        self.desc_amethyst.setStyleSheet("margin-left: 24px; color: #a6adc8;")
        layout.addWidget(self.desc_amethyst)

        layout.addSpacing(12)

        # Per-game config section
        self.game_section_label = QLabel(tr("hl_game_config"))
        self.game_section_label.setProperty("class", "section")
        layout.addWidget(self.game_section_label)

        self.game_hint = QLabel(tr("hl_game_path_hint"))
        self.game_hint.setWordWrap(True)
        self.game_hint.setStyleSheet("color: #a6adc8; font-size: 12px;")
        layout.addWidget(self.game_hint)

        # Scrollable area for game paths
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 4, 0, 4)
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll)

        # Framework hint
        self.framework_hint = QLabel(tr("hl_framework_hint"))
        self.framework_hint.setWordWrap(True)
        self.framework_hint.setProperty("class", "success")
        layout.addWidget(self.framework_hint)

    def initializePage(self):
        games = self.source_page.get_games()

        # Clear previous game path widgets
        for label, edit, btn in self._game_path_edits:
            label.deleteLater()
            edit.deleteLater()
            btn.deleteLater()
        self._game_path_edits.clear()

        # Remove old items from scroll layout
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

        for game in games:
            row = QHBoxLayout()
            label = QLabel(f"{game.game_name}:")
            label.setMinimumWidth(150)
            row.addWidget(label)
            edit = QLineEdit()
            edit.setPlaceholderText(tr("target_game_path"))
            if game.game_path:
                edit.setText(game.game_path)
            row.addWidget(edit)
            btn = QPushButton(tr("browse"))
            btn.setFixedWidth(100)
            # Capture game reference for closure
            btn.clicked.connect(
                lambda checked, e=edit: self._browse_game_path(e)
            )
            row.addWidget(btn)

            container = QWidget()
            container.setLayout(row)
            self.scroll_layout.addWidget(container)
            self._game_path_edits.append((label, edit, btn))

        self.scroll_layout.addStretch()

        # Show framework hint only for Anvil
        self.btn_group.buttonClicked.connect(self._on_target_changed)
        self._on_target_changed()

    def _on_target_changed(self):
        is_anvil = self.btn_group.checkedId() == 0
        self.framework_hint.setVisible(is_anvil)

    def _browse_game_path(self, edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, tr("target_game_path"))
        if path:
            edit.setText(path)

    def validatePage(self):
        # Save game paths back to ImportableGame objects
        games = self.source_page.get_games()
        for i, game in enumerate(games):
            if i < len(self._game_path_edits):
                _, edit, _ = self._game_path_edits[i]
                game.game_path = edit.text().strip()
        return True

    def get_target(self) -> str:
        return "anvil" if self.btn_group.checkedId() == 0 else "amethyst"

    def retranslateUi(self):
        self.desc_label.setText(tr("hl_config_desc"))
        self.target_label.setText(tr("hl_target_manager"))
        self.radio_anvil.setText(tr("target_anvil"))
        self.desc_anvil.setText(tr("target_anvil_desc"))
        self.radio_amethyst.setText(tr("target_amethyst"))
        self.desc_amethyst.setText(tr("target_amethyst_desc"))
        self.game_section_label.setText(tr("hl_game_config"))
        self.game_hint.setText(tr("hl_game_path_hint"))
        self.framework_hint.setText(tr("hl_framework_hint"))
        for _, _, btn in self._game_path_edits:
            btn.setText(tr("browse"))


# ---------------------------------------------------------------------------
# Page 5 — Progress + Done
# ---------------------------------------------------------------------------

class ProgressPage(QWizardPage):
    def __init__(self, source_page: SourcePage, config_page: ConfigPage, parent=None):
        super().__init__(parent)
        self.source_page = source_page
        self.config_page = config_page
        self._complete = False
        self._results: list[tuple[str, str]] = []

        layout = QVBoxLayout(self)

        self.status_label = QLabel(tr("import_progress_creating"))
        self.status_label.setFont(QFont("", 12, QFont.Bold))
        layout.addWidget(self.status_label)

        layout.addSpacing(8)

        self.game_label = QLabel()
        layout.addWidget(self.game_label)

        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)

        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #a6adc8;")
        layout.addWidget(self.detail_label)

        layout.addSpacing(16)

        # Done area (hidden until import finishes)
        self.done_widget = QWidget()
        done_layout = QVBoxLayout(self.done_widget)
        done_layout.setContentsMargins(0, 0, 0, 0)

        self.done_label = QLabel(tr("import_done_title"))
        self.done_label.setProperty("class", "title")
        self.done_label.setAlignment(Qt.AlignCenter)
        done_layout.addWidget(self.done_label)

        done_layout.addSpacing(8)

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMaximumHeight(200)
        done_layout.addWidget(self.summary_text)

        self.framework_done_hint = QLabel(tr("hl_framework_done_hint"))
        self.framework_done_hint.setWordWrap(True)
        self.framework_done_hint.setProperty("class", "success")
        done_layout.addWidget(self.framework_done_hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_copy_links = QPushButton(tr("import_done_nexus_links"))
        self.btn_copy_links.clicked.connect(self._copy_nexus_links)
        btn_row.addWidget(self.btn_copy_links)
        btn_row.addStretch()
        done_layout.addLayout(btn_row)

        layout.addWidget(self.done_widget)
        self.done_widget.setVisible(False)

        layout.addStretch()

        self._worker = None

    def initializePage(self):
        self._complete = False
        self._results.clear()
        self.done_widget.setVisible(False)
        self.progress_bar.setValue(0)
        self.status_label.setText(tr("import_progress_creating"))
        self.game_label.setText("")
        self.detail_label.setText("")
        self.setFinalPage(False)

        games = self.source_page.get_games()
        target = self.config_page.get_target()

        self._worker = ImportWorker(games, target)
        self._worker.game_started.connect(self._on_game_started)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_game_started(self, game_name):
        self.game_label.setText(f"{game_name}...")

    def _on_progress(self, status, current, total):
        if total > 0:
            self.progress_bar.setValue(int(current / total * 100))
        self.detail_label.setText(status)

    def _on_done(self, results):
        self._results = results
        self._complete = True
        self.progress_bar.setValue(100)

        self.status_label.setVisible(False)
        self.game_label.setVisible(False)
        self.detail_label.setVisible(False)
        self.done_widget.setVisible(True)
        self.setFinalPage(True)

        # Build summary
        target = self.config_page.get_target()
        manager = tr("target_anvil") if target == "anvil" else tr("target_amethyst")
        games = self.source_page.get_games()

        total_mods = sum(g.mod_count for g in games)
        total_no_files = sum(
            1 for g in games for m in g.package.mods
            if not m.is_separator and m.nexus_id > 0 and not g.has_mod_files
        )

        lines = [
            tr("import_done_summary", imported=total_mods, missing=total_no_files),
            f"Manager: {manager}",
            "",
        ]
        for game_name, path in results:
            lines.append(f"  {game_name}: {path}")

        # Nexus links for mods without files
        if total_no_files > 0:
            lines.append("")
            for game in games:
                if not game.has_mod_files:
                    slug = game.package.manifest.nexus_game_slug
                    for m in game.package.mods:
                        if not m.is_separator and m.nexus_id > 0:
                            lines.append(
                                f"  {m.display_name}: "
                                f"https://www.nexusmods.com/{slug}/mods/{m.nexus_id}"
                            )

        self.summary_text.setText("\n".join(lines))

        # Show framework hint only for Anvil
        self.framework_done_hint.setVisible(target == "anvil")
        self.btn_copy_links.setVisible(total_no_files > 0)

        self.completeChanged.emit()

    def _on_error(self, error):
        self.status_label.setText(f"{tr('error')}: {error}")
        self.status_label.setProperty("class", "error")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.detail_label.setText("")

    def _copy_nexus_links(self):
        games = self.source_page.get_games()
        links = []
        for game in games:
            if not game.has_mod_files:
                slug = game.package.manifest.nexus_game_slug
                for m in game.package.mods:
                    if not m.is_separator and m.nexus_id > 0:
                        links.append(
                            f"https://www.nexusmods.com/{slug}/mods/{m.nexus_id}"
                        )

        clipboard = QApplication.clipboard()
        clipboard.setText("\n".join(links))
        self.btn_copy_links.setText(f"Copied {len(links)} links!")

    def isComplete(self):
        return self._complete

    def retranslateUi(self):
        if not self._complete:
            self.status_label.setText(tr("import_progress_creating"))
        self.done_label.setText(tr("import_done_title"))
        self.framework_done_hint.setText(tr("hl_framework_done_hint"))
        self.btn_copy_links.setText(tr("import_done_nexus_links"))


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

class ImporterWizard(QWizard):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("importer_title"))
        self.setMinimumSize(800, 600)
        self.setWizardStyle(QWizard.ModernStyle)

        self.welcome_page = WelcomePage()
        self.source_page = SourcePage()
        self.preview_page = PreviewPage(self.source_page)
        self.config_page = ConfigPage(self.source_page)
        self.progress_page = ProgressPage(self.source_page, self.config_page)

        self.addPage(self.welcome_page)
        self.addPage(self.source_page)
        self.addPage(self.preview_page)
        self.addPage(self.config_page)
        self.addPage(self.progress_page)

        self._retranslate_buttons()

    def _retranslate_buttons(self):
        self.setButtonText(QWizard.NextButton, tr("next") + "  \u2192")
        self.setButtonText(QWizard.BackButton, "\u2190  " + tr("back"))
        self.setButtonText(QWizard.CancelButton, tr("cancel"))
        self.setButtonText(QWizard.FinishButton, tr("close"))

    def retranslateAll(self):
        self.setWindowTitle(tr("importer_title"))
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
    app.setApplicationName("HelloLinux")
    app.setApplicationVersion("1.0.0")
    app.setStyleSheet(STYLESHEET)

    wizard = ImporterWizard()
    wizard.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

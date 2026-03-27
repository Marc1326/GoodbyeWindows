"""GoodbyeWindows — Linux Importer GUI.

Wizard-style interface:
  Step 1: Select source (file / folder / NTFS / network)
  Step 2: Preview mods
  Step 3: Select target (Anvil / Amethyst)
  Step 4: Progress
  Step 5: Done
"""

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.i18n import tr, set_locale, detect_locale
from common.mo2_reader import MO2Instance, format_size, scan_instance
from common.migration_format import (
    MigrationPackage,
    load_gbw,
    create_package_from_mo2,
)
from importer.detector import (
    find_ntfs_partitions,
    find_usb_drives,
    find_gbw_on_drive,
    find_export_folder,
    NTFSPartition,
)
from importer.client import TransferClient
from importer.importer_anvil import import_to_anvil, list_anvil_instances
from importer.importer_amethyst import import_to_amethyst, find_amethyst_games


# --- Workers ---

class NTFSScanWorker(QThread):
    finished = Signal(list)

    def run(self):
        partitions = find_ntfs_partitions()
        self.finished.emit(partitions)


class ImportWorker(QThread):
    progress = Signal(str, int, int)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, package, target, instance_name, game_path, mod_source_dir):
        super().__init__()
        self.package = package
        self.target = target
        self.instance_name = instance_name
        self.game_path = game_path
        self.mod_source_dir = mod_source_dir

    def run(self):
        try:
            if self.target == "anvil":
                path = import_to_anvil(
                    self.package,
                    self.instance_name,
                    self.game_path,
                    self.mod_source_dir,
                    progress_callback=lambda s, c, t: self.progress.emit(s, c, t),
                )
            else:
                path = import_to_amethyst(
                    self.package,
                    profile_name="Default",
                    mod_source_dir=self.mod_source_dir,
                    progress_callback=lambda s, c, t: self.progress.emit(s, c, t),
                )
            self.finished.emit(str(path))
        except Exception as e:
            self.error.emit(str(e))


class NetworkDownloadWorker(QThread):
    progress = Signal(str, int, int)
    finished = Signal(object)  # MigrationPackage
    error = Signal(str)

    def __init__(self, client, output_dir):
        super().__init__()
        self.client = client
        self.output_dir = output_dir

    def run(self):
        try:
            gbw_path = self.client.download_gbw(Path(self.output_dir) / "migration.gbw")
            package = load_gbw(gbw_path)
            self.finished.emit(package)
        except Exception as e:
            self.error.emit(str(e))


# --- Pages ---

class WelcomePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle(tr("app_name"))
        self.setSubTitle(tr("importer_welcome"))

        layout = QVBoxLayout(self)

        title = QLabel("GoodbyeWindows")
        title.setFont(QFont("", 24, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(tr("app_subtitle") + " — Importer")
        subtitle.setFont(QFont("", 12))
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(20)

        lang_layout = QHBoxLayout()
        lang_layout.addStretch()
        lang_label = QLabel(tr("language") + ":")
        lang_layout.addWidget(lang_label)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Deutsch", "de")
        idx = self.lang_combo.findData(detect_locale())
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.currentIndexChanged.connect(self._on_lang)
        lang_layout.addWidget(self.lang_combo)
        lang_layout.addStretch()
        layout.addLayout(lang_layout)

        layout.addStretch()

    def _on_lang(self):
        loc = self.lang_combo.currentData()
        set_locale(loc)
        QMessageBox.information(self, tr("info"),
            "Language will be applied on next start." if loc == "en"
            else "Sprache wird beim nächsten Start angewendet.")


class SourcePage(QWizardPage):
    """Select import source: .gbw file, folder, NTFS, or network."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle(tr("importer_step_source"))
        self.setSubTitle(tr("source_title"))
        self._package: MigrationPackage | None = None
        self._mod_source_dir: Path | None = None
        self._complete = False

        layout = QVBoxLayout(self)
        self.btn_group = QButtonGroup(self)

        # .gbw file
        self.radio_file = QRadioButton(tr("source_file"))
        self.radio_file.setChecked(True)
        self.btn_group.addButton(self.radio_file, 0)
        layout.addWidget(self.radio_file)
        desc = QLabel(tr("source_file_desc"))
        desc.setWordWrap(True)
        desc.setStyleSheet("margin-left: 24px; color: gray;")
        layout.addWidget(desc)

        layout.addSpacing(8)

        # Folder
        self.radio_folder = QRadioButton(tr("source_folder"))
        self.btn_group.addButton(self.radio_folder, 1)
        layout.addWidget(self.radio_folder)
        desc2 = QLabel(tr("source_folder_desc"))
        desc2.setWordWrap(True)
        desc2.setStyleSheet("margin-left: 24px; color: gray;")
        layout.addWidget(desc2)

        layout.addSpacing(8)

        # NTFS
        self.radio_ntfs = QRadioButton(tr("source_ntfs"))
        self.btn_group.addButton(self.radio_ntfs, 2)
        layout.addWidget(self.radio_ntfs)
        desc3 = QLabel(tr("source_ntfs_desc"))
        desc3.setWordWrap(True)
        desc3.setStyleSheet("margin-left: 24px; color: gray;")
        layout.addWidget(desc3)

        layout.addSpacing(8)

        # Network
        self.radio_network = QRadioButton(tr("source_network"))
        self.btn_group.addButton(self.radio_network, 3)
        layout.addWidget(self.radio_network)
        desc4 = QLabel(tr("source_network_desc"))
        desc4.setWordWrap(True)
        desc4.setStyleSheet("margin-left: 24px; color: gray;")
        layout.addWidget(desc4)

        layout.addSpacing(16)

        # Action area
        self.action_group = QGroupBox()
        action_layout = QVBoxLayout(self.action_group)

        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit()
        path_layout.addWidget(self.path_edit)
        self.btn_browse = QPushButton(tr("browse"))
        self.btn_browse.clicked.connect(self._browse)
        path_layout.addWidget(self.btn_browse)
        action_layout.addLayout(path_layout)

        # Network fields
        self.net_widget = QWidget()
        net_layout = QVBoxLayout(self.net_widget)
        net_layout.setContentsMargins(0, 0, 0, 0)
        ip_layout = QHBoxLayout()
        ip_layout.addWidget(QLabel(tr("network_enter_ip")))
        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("192.168.1.42")
        ip_layout.addWidget(self.ip_edit)
        net_layout.addLayout(ip_layout)
        pin_layout = QHBoxLayout()
        pin_layout.addWidget(QLabel(tr("network_enter_pin")))
        self.pin_edit = QLineEdit()
        self.pin_edit.setMaxLength(4)
        self.pin_edit.setFixedWidth(80)
        pin_layout.addWidget(self.pin_edit)
        pin_layout.addStretch()
        net_layout.addLayout(pin_layout)
        self.btn_connect = QPushButton(tr("network_connecting").replace("...", ""))
        self.btn_connect.clicked.connect(self._connect_network)
        net_layout.addWidget(self.btn_connect)
        action_layout.addWidget(self.net_widget)

        # NTFS scan
        self.ntfs_widget = QWidget()
        ntfs_layout = QVBoxLayout(self.ntfs_widget)
        ntfs_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_scan_ntfs = QPushButton(tr("ntfs_scanning").replace("...", ""))
        self.btn_scan_ntfs.clicked.connect(self._scan_ntfs)
        ntfs_layout.addWidget(self.btn_scan_ntfs)
        self.ntfs_table = QTableWidget(0, 4)
        self.ntfs_table.setHorizontalHeaderLabels([
            tr("ntfs_partition"), tr("ntfs_mount"), tr("ntfs_size"), tr("ntfs_mo2_found"),
        ])
        self.ntfs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.ntfs_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.ntfs_table.setSelectionMode(QTableWidget.SingleSelection)
        ntfs_layout.addWidget(self.ntfs_table)
        action_layout.addWidget(self.ntfs_widget)

        layout.addWidget(self.action_group)

        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        layout.addStretch()

        self.btn_group.buttonClicked.connect(self._on_mode_changed)
        self._on_mode_changed()

        self._ntfs_partitions: list[NTFSPartition] = []
        self._ntfs_worker = None

    def _on_mode_changed(self):
        mode = self.btn_group.checkedId()
        self.path_edit.setVisible(mode in (0, 1))
        self.btn_browse.setVisible(mode in (0, 1))
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
            self._try_load_source(path, mode)

    def _try_load_source(self, path: str, mode: int):
        try:
            if mode == 0:
                self._package = load_gbw(Path(path))
                self._mod_source_dir = None
                self._complete = True
                self.status_label.setText(
                    f"{tr('preview_mods_count', count=len(self._package.mods))} — "
                    f"{self._package.manifest.game_name}"
                )
            elif mode == 1:
                p = Path(path)
                gbw = p / "migration.gbw"
                if gbw.exists():
                    self._package = load_gbw(gbw)
                    mods_dir = p / "mods"
                    self._mod_source_dir = mods_dir if mods_dir.exists() else None
                    self._complete = True
                    self.status_label.setText(
                        f"{tr('preview_mods_count', count=len(self._package.mods))} — "
                        f"{self._package.manifest.game_name}"
                    )
                else:
                    self.status_label.setText("No migration.gbw found in folder.")
                    self._complete = False
        except Exception as e:
            self.status_label.setText(f"{tr('error')}: {e}")
            self._complete = False

        self.completeChanged.emit()

    def _scan_ntfs(self):
        self.ntfs_table.setRowCount(0)
        self.btn_scan_ntfs.setEnabled(False)
        self.status_label.setText(tr("ntfs_scanning"))
        self._ntfs_worker = NTFSScanWorker()
        self._ntfs_worker.finished.connect(self._on_ntfs_done)
        self._ntfs_worker.start()

    def _on_ntfs_done(self, partitions):
        self._ntfs_partitions = partitions
        self.btn_scan_ntfs.setEnabled(True)

        if not partitions:
            self.status_label.setText(tr("ntfs_none"))
            return

        self.status_label.setText(tr("ntfs_found", count=len(partitions)))

        # Flatten: show partitions and their MO2 instances
        rows = []
        for part in partitions:
            for inst in part.mo2_instances:
                rows.append((part, inst))

        if not rows:
            self.status_label.setText(
                tr("ntfs_found", count=len(partitions)) + " — No MO2 instances found."
            )
            return

        self.ntfs_table.setRowCount(len(rows))
        for row, (part, inst) in enumerate(rows):
            self.ntfs_table.setItem(row, 0, QTableWidgetItem(part.device))
            self.ntfs_table.setItem(row, 1, QTableWidgetItem(str(inst.path)))
            self.ntfs_table.setItem(row, 2, QTableWidgetItem(inst.game_name))
            self.ntfs_table.setItem(row, 3, QTableWidgetItem(str(inst.mod_count)))

        self.ntfs_table.selectRow(0)
        self.ntfs_table.itemSelectionChanged.connect(self._on_ntfs_select)
        self._on_ntfs_select()

    def _on_ntfs_select(self):
        rows_sel = self.ntfs_table.selectionModel().selectedRows()
        if not rows_sel:
            return

        row = rows_sel[0].row()
        # Find the corresponding instance
        idx = 0
        for part in self._ntfs_partitions:
            for inst in part.mo2_instances:
                if idx == row:
                    self._package = create_package_from_mo2(inst)
                    self._package.manifest.has_mod_files = True
                    self._mod_source_dir = inst.mods_dir
                    self._complete = True
                    self.completeChanged.emit()
                    return
                idx += 1

    def _connect_network(self):
        ip = self.ip_edit.text().strip()
        pin = self.pin_edit.text().strip()
        if not ip or not pin:
            QMessageBox.warning(self, tr("warning"), "IP and PIN required.")
            return

        self.status_label.setText(tr("network_connecting"))
        client = TransferClient(ip)

        if not client.ping():
            self.status_label.setText(tr("network_failed", error="Server not reachable"))
            return

        if not client.authenticate(pin):
            self.status_label.setText(tr("network_failed", error="Wrong PIN"))
            return

        self.status_label.setText(tr("network_connected", client=ip))

        # Download GBW
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="gbw_"))
        gbw_path = client.download_gbw(tmp / "migration.gbw")
        self._package = load_gbw(gbw_path)
        self._mod_source_dir = None  # Network mode: files downloaded later
        self._complete = True
        self._network_client = client

        self.status_label.setText(
            f"{tr('network_connected', client=ip)} — "
            f"{tr('preview_mods_count', count=len(self._package.mods))}"
        )
        self.completeChanged.emit()

    def isComplete(self):
        return self._complete

    def get_package(self) -> MigrationPackage | None:
        return self._package

    def get_mod_source_dir(self) -> Path | None:
        return self._mod_source_dir


class PreviewPage(QWizardPage):
    def __init__(self, source_page: SourcePage, parent=None):
        super().__init__(parent)
        self.source_page = source_page
        self.setTitle(tr("importer_step_preview"))

        layout = QVBoxLayout(self)
        self.info = QTextEdit()
        self.info.setReadOnly(True)
        layout.addWidget(self.info)

    def initializePage(self):
        package = self.source_page.get_package()
        if not package:
            self.info.setText("No package loaded.")
            return

        m = package.manifest
        mods = package.mods

        real_mods = [mod for mod in mods if not mod.is_separator]
        separators = [mod for mod in mods if mod.is_separator]
        with_nexus = [mod for mod in real_mods if mod.nexus_id > 0]
        without_nexus = [mod for mod in real_mods if mod.nexus_id <= 0]
        has_files = self.source_page.get_mod_source_dir() is not None

        lines = [
            f"<h3>{tr('import_preview_title')}</h3>",
            f"<b>{tr('preview_game', game=m.game_name)}</b>",
            f"{tr('preview_mods_count', count=len(real_mods))}",
            f"{tr('preview_separators_count', count=len(separators))}",
            f"{tr('preview_total_size', size=format_size(m.total_size_bytes))}",
            "",
            f"<b>{tr('preview_nexus_ids', count=len(with_nexus))}</b>",
            f"{tr('preview_no_nexus', count=len(without_nexus))}",
            "",
        ]

        if has_files:
            lines.append(f"<b>{tr('import_mods_available', count=len(real_mods))}</b>")
        else:
            lines.append(f"<b>{tr('import_mods_download', count=len(with_nexus))}</b>")
            if without_nexus:
                lines.append(f"{tr('import_mods_missing', count=len(without_nexus))}")

        if package.profiles:
            lines.append("")
            lines.append(f"<b>Profile:</b> {', '.join(p.name for p in package.profiles)}")

        self.info.setHtml("<br>".join(lines))


class TargetPage(QWizardPage):
    """Select target mod manager and configure paths."""

    def __init__(self, source_page: SourcePage, parent=None):
        super().__init__(parent)
        self.source_page = source_page
        self.setTitle(tr("importer_step_target"))
        self.setSubTitle(tr("target_title"))

        layout = QVBoxLayout(self)

        self.btn_group = QButtonGroup(self)

        # Anvil
        self.radio_anvil = QRadioButton(tr("target_anvil"))
        self.radio_anvil.setChecked(True)
        self.btn_group.addButton(self.radio_anvil, 0)
        layout.addWidget(self.radio_anvil)
        desc_a = QLabel(tr("target_anvil_desc"))
        desc_a.setWordWrap(True)
        desc_a.setStyleSheet("margin-left: 24px; color: gray;")
        layout.addWidget(desc_a)

        layout.addSpacing(10)

        # Amethyst
        self.radio_amethyst = QRadioButton(tr("target_amethyst"))
        self.btn_group.addButton(self.radio_amethyst, 1)
        layout.addWidget(self.radio_amethyst)
        desc_am = QLabel(tr("target_amethyst_desc"))
        desc_am.setWordWrap(True)
        desc_am.setStyleSheet("margin-left: 24px; color: gray;")
        layout.addWidget(desc_am)

        layout.addSpacing(20)

        # Instance name
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel(tr("target_instance_name")))
        self.name_edit = QLineEdit()
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        # Game path
        game_layout = QHBoxLayout()
        game_layout.addWidget(QLabel(tr("target_game_path")))
        self.game_path_edit = QLineEdit()
        game_layout.addWidget(self.game_path_edit)
        self.btn_game_browse = QPushButton(tr("browse"))
        self.btn_game_browse.clicked.connect(self._browse_game)
        game_layout.addWidget(self.btn_game_browse)
        layout.addLayout(game_layout)

        layout.addStretch()

    def initializePage(self):
        package = self.source_page.get_package()
        if package:
            self.name_edit.setText(package.manifest.game_name or "Imported")

    def _browse_game(self):
        path = QFileDialog.getExistingDirectory(self, tr("target_game_path"))
        if path:
            self.game_path_edit.setText(path)

    def get_target(self) -> str:
        return "anvil" if self.btn_group.checkedId() == 0 else "amethyst"

    def get_instance_name(self) -> str:
        return self.name_edit.text().strip() or "Imported"

    def get_game_path(self) -> str:
        return self.game_path_edit.text().strip()


class ProgressPage(QWizardPage):
    def __init__(self, source_page: SourcePage, target_page: TargetPage, parent=None):
        super().__init__(parent)
        self.source_page = source_page
        self.target_page = target_page
        self.setTitle(tr("importer_step_progress"))
        self._complete = False
        self._result_path = ""

        layout = QVBoxLayout(self)
        self.status_label = QLabel(tr("import_progress_creating"))
        layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)
        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)
        layout.addStretch()

        self._worker = None

    def initializePage(self):
        self._complete = False
        package = self.source_page.get_package()
        target = self.target_page.get_target()
        name = self.target_page.get_instance_name()
        game_path = self.target_page.get_game_path()
        mod_source = self.source_page.get_mod_source_dir()

        self.status_label.setText(tr("import_progress_creating"))
        self.progress_bar.setValue(0)

        self._worker = ImportWorker(package, target, name, game_path, mod_source)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, status, current, total):
        if total > 0:
            self.progress_bar.setValue(int(current / total * 100))
        self.detail_label.setText(tr("import_progress_copying", mod=status))
        self.status_label.setText(f"{current} / {total}")

    def _on_done(self, path):
        self._result_path = path
        self._complete = True
        self.progress_bar.setValue(100)
        self.status_label.setText(tr("import_progress_done"))
        self.detail_label.setText(path)
        self.completeChanged.emit()

    def _on_error(self, error):
        self.status_label.setText(f"{tr('error')}: {error}")

    def isComplete(self):
        return self._complete


class DonePage(QWizardPage):
    def __init__(self, source_page: SourcePage, target_page: TargetPage, progress_page: ProgressPage, parent=None):
        super().__init__(parent)
        self.source_page = source_page
        self.target_page = target_page
        self.progress_page = progress_page
        self.setTitle(tr("importer_step_done"))
        self.setFinalPage(True)

        layout = QVBoxLayout(self)

        self.done_label = QLabel()
        self.done_label.setFont(QFont("", 14, QFont.Bold))
        self.done_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.done_label)

        layout.addSpacing(20)

        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        layout.addWidget(self.summary)

        # Nexus links button
        self.btn_copy_links = QPushButton(tr("import_done_nexus_links"))
        self.btn_copy_links.clicked.connect(self._copy_nexus_links)
        layout.addWidget(self.btn_copy_links)

        layout.addStretch()

    def initializePage(self):
        self.done_label.setText(tr("import_done_title"))

        package = self.source_page.get_package()
        target = self.target_page.get_target()
        path = self.progress_page._result_path
        has_files = self.source_page.get_mod_source_dir() is not None

        mods = [m for m in package.mods if not m.is_separator]
        missing = [m for m in mods if m.nexus_id > 0] if not has_files else []

        manager = tr("target_anvil") if target == "anvil" else tr("target_amethyst")

        lines = [
            tr("import_done_summary", imported=len(mods), missing=len(missing)),
            "",
            f"Path: {path}",
            f"Manager: {manager}",
        ]

        if missing:
            lines.append("")
            lines.append(f"Nexus links ({len(missing)}):")
            slug = package.manifest.nexus_game_slug
            for m in missing[:20]:
                url = f"https://www.nexusmods.com/{slug}/mods/{m.nexus_id}"
                lines.append(f"  - {m.display_name}: {url}")
            if len(missing) > 20:
                lines.append(f"  ... +{len(missing) - 20} more")

        self.summary.setText("\n".join(lines))
        self.btn_copy_links.setVisible(bool(missing))

    def _copy_nexus_links(self):
        package = self.source_page.get_package()
        has_files = self.source_page.get_mod_source_dir() is not None
        if has_files:
            return

        slug = package.manifest.nexus_game_slug
        links = []
        for m in package.mods:
            if not m.is_separator and m.nexus_id > 0:
                links.append(f"https://www.nexusmods.com/{slug}/mods/{m.nexus_id}")

        clipboard = QApplication.clipboard()
        clipboard.setText("\n".join(links))
        self.btn_copy_links.setText(f"Copied {len(links)} links!")


class ImporterWizard(QWizard):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("importer_title"))
        self.setMinimumSize(750, 550)
        self.setWizardStyle(QWizard.ModernStyle)

        self.welcome_page = WelcomePage()
        self.source_page = SourcePage()
        self.preview_page = PreviewPage(self.source_page)
        self.target_page = TargetPage(self.source_page)
        self.progress_page = ProgressPage(self.source_page, self.target_page)
        self.done_page = DonePage(self.source_page, self.target_page, self.progress_page)

        self.addPage(self.welcome_page)
        self.addPage(self.source_page)
        self.addPage(self.preview_page)
        self.addPage(self.target_page)
        self.addPage(self.progress_page)
        self.addPage(self.done_page)

        self.setButtonText(QWizard.NextButton, tr("next"))
        self.setButtonText(QWizard.BackButton, tr("back"))
        self.setButtonText(QWizard.CancelButton, tr("cancel"))
        self.setButtonText(QWizard.FinishButton, tr("close"))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GoodbyeWindows")
    app.setApplicationVersion("1.0.0")

    wizard = ImporterWizard()
    wizard.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

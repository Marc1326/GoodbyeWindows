"""GoodbyeWindows — Windows Exporter GUI.

Wizard-style interface:
  Step 1: Scan for MO2 instances
  Step 2: Select instance + profile
  Step 3: Preview (mod count, size, Nexus IDs)
  Step 4: Choose export mode (metadata / full / network)
  Step 5: Progress
  Step 6: Done
"""

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
    QGroupBox,
    QComboBox,
)

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.i18n import tr, set_locale, detect_locale, available_locales
from common.mo2_reader import MO2Instance, format_size
from exporter.scanner import find_all_instances, scan_path
from exporter.exporter import export_metadata, export_full, get_export_summary
from exporter.server import TransferServer, get_local_ip


class ScanWorker(QThread):
    """Background thread for scanning MO2 instances."""
    finished = Signal(list)

    def run(self):
        instances = find_all_instances()
        self.finished.emit(instances)


class ExportWorker(QThread):
    """Background thread for export operations."""
    progress = Signal(str, int, int)  # status, current, total
    finished = Signal(str)  # result path
    error = Signal(str)

    def __init__(self, instance, mode, output_path):
        super().__init__()
        self.instance = instance
        self.mode = mode
        self.output_path = output_path

    def run(self):
        try:
            if self.mode == "metadata":
                path = export_metadata(self.instance, Path(self.output_path))
                self.finished.emit(str(path))
            elif self.mode == "full":
                path = export_full(
                    self.instance,
                    Path(self.output_path),
                    progress=lambda s, c, t: self.progress.emit(s, c, t),
                )
                self.finished.emit(str(path))
        except Exception as e:
            self.error.emit(str(e))


# --- Wizard Pages ---

class WelcomePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle(tr("app_name"))
        self.setSubTitle(tr("exporter_welcome"))

        layout = QVBoxLayout(self)

        # Logo / title
        title = QLabel("GoodbyeWindows")
        title.setFont(QFont("", 24, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(tr("app_subtitle"))
        subtitle.setFont(QFont("", 12))
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(20)

        # Language selector
        lang_layout = QHBoxLayout()
        lang_layout.addStretch()
        lang_label = QLabel(tr("language") + ":")
        lang_layout.addWidget(lang_label)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Deutsch", "de")
        current = detect_locale()
        idx = self.lang_combo.findData(current)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        lang_layout.addWidget(self.lang_combo)
        lang_layout.addStretch()
        layout.addLayout(lang_layout)

        layout.addStretch()

    def _on_lang_changed(self):
        loc = self.lang_combo.currentData()
        set_locale(loc)
        # Refresh would require rebuilding all pages — simplified for now
        QMessageBox.information(
            self,
            tr("info"),
            "Language will be applied on next start."
            if loc == "en" else
            "Sprache wird beim nächsten Start angewendet."
        )


class ScanPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle(tr("exporter_step_scan"))
        self.setSubTitle(tr("scan_title"))
        self._instances: list[MO2Instance] = []
        self._complete = False

        layout = QVBoxLayout(self)

        self.status_label = QLabel(tr("scan_title"))
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            tr("scan_path"), tr("scan_game"), tr("scan_mods"), tr("scan_profiles"),
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.btn_manual = QPushButton(tr("scan_add_manual"))
        self.btn_manual.clicked.connect(self._add_manual)
        btn_layout.addWidget(self.btn_manual)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self._worker = None

    def initializePage(self):
        self._start_scan()

    def _start_scan(self):
        self.table.setRowCount(0)
        self._instances.clear()
        self._complete = False
        self.status_label.setText(tr("scan_title"))

        self._worker = ScanWorker()
        self._worker.finished.connect(self._on_scan_done)
        self._worker.start()

    def _on_scan_done(self, instances):
        self._instances = instances
        self.table.setRowCount(len(instances))

        for row, inst in enumerate(instances):
            self.table.setItem(row, 0, QTableWidgetItem(str(inst.path)))
            self.table.setItem(row, 1, QTableWidgetItem(inst.game_name))
            self.table.setItem(row, 2, QTableWidgetItem(str(inst.mod_count)))
            self.table.setItem(row, 3, QTableWidgetItem(str(inst.profile_count)))

        if instances:
            self.status_label.setText(tr("scan_found", count=len(instances)))
            self.table.selectRow(0)
        else:
            self.status_label.setText(tr("scan_none"))

        self.completeChanged.emit()

    def _add_manual(self):
        folder = QFileDialog.getExistingDirectory(self, tr("scan_add_manual"))
        if not folder:
            return
        instance = scan_path(Path(folder))
        if instance is None:
            QMessageBox.warning(
                self, tr("warning"),
                f"No ModOrganizer.ini found in:\n{folder}"
            )
            return
        self._instances.append(instance)
        row = self.table.rowCount()
        self.table.setRowCount(row + 1)
        self.table.setItem(row, 0, QTableWidgetItem(str(instance.path)))
        self.table.setItem(row, 1, QTableWidgetItem(instance.game_name))
        self.table.setItem(row, 2, QTableWidgetItem(str(instance.mod_count)))
        self.table.setItem(row, 3, QTableWidgetItem(str(instance.profile_count)))
        self.table.selectRow(row)
        self.status_label.setText(tr("scan_found", count=len(self._instances)))
        self.completeChanged.emit()

    def _on_selection(self):
        self._complete = len(self.table.selectedItems()) > 0
        self.completeChanged.emit()

    def isComplete(self):
        return self._complete

    def selected_instance(self) -> MO2Instance | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self._instances[rows[0].row()]


class PreviewPage(QWizardPage):
    def __init__(self, scan_page: ScanPage, parent=None):
        super().__init__(parent)
        self.scan_page = scan_page
        self.setTitle(tr("exporter_step_preview"))

        layout = QVBoxLayout(self)
        self.info = QTextEdit()
        self.info.setReadOnly(True)
        layout.addWidget(self.info)

    def initializePage(self):
        instance = self.scan_page.selected_instance()
        if not instance:
            self.info.setText("No instance selected.")
            return

        summary = get_export_summary(instance)
        lines = [
            f"<h3>{tr('preview_title')}</h3>",
            f"<b>{tr('preview_game', game=summary['game_name'])}</b>",
            f"{tr('preview_mods_count', count=summary['total_mods'])}",
            f"{tr('preview_separators_count', count=summary['separators'])}",
            f"{tr('preview_total_size', size=format_size(summary['total_size_bytes']))}",
            "",
            f"<b>{tr('preview_nexus_ids', count=summary['with_nexus_id'])}</b>",
            f"{tr('preview_no_nexus', count=summary['without_nexus_id'])}",
            "",
            f"<b>{tr('scan_profiles')}:</b> {', '.join(summary['profiles'])}",
        ]
        self.info.setHtml("<br>".join(lines))


class ExportModePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle(tr("exporter_step_export"))
        self.setSubTitle(tr("export_mode_title"))

        layout = QVBoxLayout(self)

        self.btn_group = QButtonGroup(self)

        # Metadata only
        self.radio_meta = QRadioButton(tr("export_mode_metadata"))
        self.radio_meta.setChecked(True)
        self.btn_group.addButton(self.radio_meta, 0)
        layout.addWidget(self.radio_meta)
        desc_meta = QLabel(tr("export_mode_metadata_desc"))
        desc_meta.setWordWrap(True)
        desc_meta.setStyleSheet("margin-left: 24px; color: gray;")
        layout.addWidget(desc_meta)

        layout.addSpacing(10)

        # Full export
        self.radio_full = QRadioButton(tr("export_mode_full"))
        self.btn_group.addButton(self.radio_full, 1)
        layout.addWidget(self.radio_full)
        desc_full = QLabel(tr("export_mode_full_desc"))
        desc_full.setWordWrap(True)
        desc_full.setStyleSheet("margin-left: 24px; color: gray;")
        layout.addWidget(desc_full)

        layout.addSpacing(10)

        # Network
        self.radio_network = QRadioButton(tr("export_mode_network"))
        self.btn_group.addButton(self.radio_network, 2)
        layout.addWidget(self.radio_network)
        desc_net = QLabel(tr("export_mode_network_desc"))
        desc_net.setWordWrap(True)
        desc_net.setStyleSheet("margin-left: 24px; color: gray;")
        layout.addWidget(desc_net)

        layout.addSpacing(20)

        # Target path
        self.target_group = QGroupBox(tr("export_target"))
        target_layout = QHBoxLayout(self.target_group)
        self.target_edit = QLineEdit()
        target_layout.addWidget(self.target_edit)
        self.btn_browse = QPushButton(tr("browse"))
        self.btn_browse.clicked.connect(self._browse)
        target_layout.addWidget(self.btn_browse)
        layout.addWidget(self.target_group)

        self.btn_group.buttonClicked.connect(self._on_mode_changed)
        self._on_mode_changed()

        layout.addStretch()

    def _on_mode_changed(self):
        mode = self.btn_group.checkedId()
        self.target_group.setVisible(mode in (0, 1))

    def _browse(self):
        mode = self.btn_group.checkedId()
        if mode == 0:
            path, _ = QFileDialog.getSaveFileName(
                self, tr("export_target_file"),
                str(Path.home() / "migration.gbw"),
                "GoodbyeWindows (*.gbw)",
            )
        else:
            path = QFileDialog.getExistingDirectory(
                self, tr("export_target_folder"),
            )
        if path:
            self.target_edit.setText(path)

    def get_mode(self) -> str:
        """Return 'metadata', 'full', or 'network'."""
        return {0: "metadata", 1: "full", 2: "network"}[self.btn_group.checkedId()]

    def get_target(self) -> str:
        return self.target_edit.text()


class ProgressPage(QWizardPage):
    def __init__(self, scan_page: ScanPage, mode_page: ExportModePage, parent=None):
        super().__init__(parent)
        self.scan_page = scan_page
        self.mode_page = mode_page
        self.setTitle(tr("exporter_step_progress"))
        self._complete = False

        layout = QVBoxLayout(self)

        self.status_label = QLabel(tr("progress_reading"))
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        layout.addStretch()

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

        self._worker = None
        self._server = None
        self._result_path = ""

    def initializePage(self):
        self._complete = False
        instance = self.scan_page.selected_instance()
        mode = self.mode_page.get_mode()
        target = self.mode_page.get_target()

        if mode == "network":
            self._start_network(instance)
        else:
            self._start_export(instance, mode, target)

    def _start_export(self, instance, mode, target):
        self.network_group.hide()
        self.progress_bar.show()
        self.status_label.setText(tr("progress_reading"))
        self.progress_bar.setValue(0)

        self._worker = ExportWorker(instance, mode, target)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _start_network(self, instance):
        self.progress_bar.hide()
        self.network_group.show()

        self._server = TransferServer(
            instance=instance,
            on_event=self._on_network_event,
        )
        self._server.start()

        self.net_info.setText(
            f"<b>{tr('network_ip', ip=self._server.ip)}</b><br>"
            f"{tr('network_port', port=self._server.port)}<br>"
            f"<b>{tr('network_pin', pin=self._server.pin)}</b>"
        )
        self.net_status.setText(tr("network_waiting"))
        self.status_label.setText(tr("network_instructions"))

    def _on_progress(self, status, current, total):
        if total > 0:
            pct = int(current / total * 100)
            self.progress_bar.setValue(pct)
        self.detail_label.setText(tr("progress_copying", file=status))
        self.status_label.setText(
            tr("progress_size",
               current=format_size(current),
               total=format_size(total))
        )

    def _on_done(self, path):
        self._result_path = path
        self._complete = True
        self.progress_bar.setValue(100)
        self.status_label.setText(tr("progress_done"))
        self.detail_label.setText(path)
        self.completeChanged.emit()

    def _on_error(self, error):
        self.status_label.setText(f"{tr('error')}: {error}")
        self.detail_label.setText("")

    def _on_network_event(self, event, **kwargs):
        if event == "authenticated":
            self.net_status.setText(tr("network_connected", client=kwargs.get("client", "")))
        elif event == "file_transfer":
            self.net_status.setText(
                tr("network_transferring", file=f"{kwargs.get('mod', '')}/{kwargs.get('file', '')}")
            )
        elif event == "gbw_downloaded":
            self.net_status.setText(tr("progress_done"))

    def isComplete(self):
        return self._complete

    def cleanupPage(self):
        if self._server:
            self._server.stop()
            self._server = None


class DonePage(QWizardPage):
    def __init__(self, mode_page: ExportModePage, progress_page: ProgressPage, parent=None):
        super().__init__(parent)
        self.mode_page = mode_page
        self.progress_page = progress_page
        self.setTitle(tr("exporter_step_done"))
        self.setFinalPage(True)

        layout = QVBoxLayout(self)

        self.done_label = QLabel()
        self.done_label.setFont(QFont("", 14, QFont.Bold))
        self.done_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.done_label)

        layout.addSpacing(20)

        self.steps_label = QLabel()
        self.steps_label.setWordWrap(True)
        layout.addWidget(self.steps_label)

        layout.addStretch()

    def initializePage(self):
        mode = self.mode_page.get_mode()
        path = self.progress_page._result_path

        self.done_label.setText(tr("done_title"))

        if mode == "metadata":
            result = tr("done_metadata", path=path)
            step1 = tr("done_step1_metadata")
        elif mode == "full":
            result = tr("done_full", path=path)
            step1 = tr("done_step1_full")
        else:
            result = tr("network_title")
            step1 = ""

        steps = f"{result}\n\n{tr('done_next_steps')}\n{step1}\n{tr('done_step2')}\n{tr('done_step3')}"
        self.steps_label.setText(steps)


class ExporterWizard(QWizard):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("exporter_title"))
        self.setMinimumSize(700, 500)
        self.setWizardStyle(QWizard.ModernStyle)

        # Pages
        self.welcome_page = WelcomePage()
        self.scan_page = ScanPage()
        self.preview_page = PreviewPage(self.scan_page)
        self.mode_page = ExportModePage()
        self.progress_page = ProgressPage(self.scan_page, self.mode_page)
        self.done_page = DonePage(self.mode_page, self.progress_page)

        self.addPage(self.welcome_page)
        self.addPage(self.scan_page)
        self.addPage(self.preview_page)
        self.addPage(self.mode_page)
        self.addPage(self.progress_page)
        self.addPage(self.done_page)

        # Button text
        self.setButtonText(QWizard.NextButton, tr("next"))
        self.setButtonText(QWizard.BackButton, tr("back"))
        self.setButtonText(QWizard.CancelButton, tr("cancel"))
        self.setButtonText(QWizard.FinishButton, tr("close"))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GoodbyeWindows")
    app.setApplicationVersion("1.0.0")

    wizard = ExporterWizard()
    wizard.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

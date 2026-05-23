# -*- coding: utf-8 -*-
import sys, os, json, logging, socket, re, posixpath
from collections import defaultdict

from scp import SCPClient

from PySide6.QtWidgets import (QWidget, QApplication, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QTextEdit, QFileDialog,
    QMessageBox, QComboBox, QFrame, QProgressBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QDialog, QFormLayout, QListWidget, QListWidgetItem, QMenu)
from PySide6.QtCore import QThread, Signal, Qt, QEvent, QTimer
from PySide6.QtGui import QFont, QAction

from ssh_utils import (create_ssh_client, ssh_exec, exec_command, load_hosts)

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

LOG_NE_CONFIG_PATH = os.path.join(BASE_DIR, "config", "log_ne_config.json")
DESKTOP_DIR = os.path.join(os.path.expanduser("~"), "Desktop")

STYLESHEET = """
    LogViewer {
        background:#f5f5f5;
    }
    QLabel {
        color:#1a1a1a;
        font-size:12px;
    }
    QLineEdit {
        background:white;
        border:1px solid #e0e0e0;
        border-radius:8px;
        padding:7px 12px;
        font-size:13px;
    }
    QLineEdit:focus {
        border-color:#ff6900;
    }
    QComboBox {
        background:white;
        border:1px solid #e0e0e0;
        border-radius:8px;
        padding:7px 12px;
        font-size:13px;
        min-height:16px;
    }
    QComboBox:focus {
        border-color:#ff6900;
    }
    QComboBox::drop-down {
        border:none;
        width:22px;
    }
    QComboBox QAbstractItemView {
        background:white;
        border:1px solid #e0e0e0;
        border-radius:8px;
        selection-background-color:#fff3e6;
        selection-color:#1a1a1a;
        padding:4px;
    }
    QCheckBox {
        font-size:12px;
        color:#1a1a1a;
        spacing:6px;
    }
    QCheckBox::indicator {
        width:18px;
        height:18px;
        border:1px solid #d0d0d0;
        border-radius:4px;
        background:white;
    }
    QCheckBox::indicator:checked {
        background:#ff6900;
        border-color:#ff6900;
    }
    QTextEdit, QPlainTextEdit {
        background:#1e1e1e;
        border:1px solid #333;
        border-radius:6px;
        padding:6px;
        font-size:12px;
        color:#d4d4d4;
        font-family:Consolas,'Courier New',monospace;
    }
    QPushButton {
        font-size:12px;
    }
    QProgressBar {
        border:none;
        border-radius:5px;
        background:#e8e8e8;
        height:5px;
        text-align:center;
        font-size:10px;
        color:#999;
    }
    QProgressBar::chunk {
        background:#ff6900;
        border-radius:5px;
    }
    QTableWidget {
        background:white;
        border:1px solid #e0e0e0;
        border-radius:8px;
        gridline-color:#f0f0f0;
        font-size:12px;
    }
    QTableWidget::item {
        padding:4px 8px;
    }
    QHeaderView::section {
        background:#fafafa;
        border:none;
        border-bottom:1px solid #e0e0e0;
        padding:8px;
        font-weight:600;
        font-size:12px;
        color:#555;
    }
"""


def _load_ne_config():
    try:
        with open(LOG_NE_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


class ListWorker(QThread):
    """Connect to remote host and list log files from configured paths."""
    file_info = Signal(dict)
    error_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, host, port, user, pwd, ne_config):
        super().__init__()
        self.host = host
        self.port = int(port)
        self.user = user
        self.pwd = pwd
        self.ne_config = ne_config

    @staticmethod
    def _group_files(file_list):
        groups = defaultdict(list)
        for fp in file_list:
            basename = os.path.basename(fp)
            dirname = os.path.dirname(fp)
            if '.' in basename:
                name, ext = basename.rsplit('.', 1)
                ext = '.' + ext
            else:
                name = basename
                ext = ''
            stem = re.sub(r'\d+$', '', name)
            if not stem:
                stem = name
            groups[(dirname, stem, ext)].append(fp)
        return groups

    def run(self):
        client = None
        try:
            logger.info(f"ListWorker connecting to {self.host}:{self.port} user={self.user}")
            client = create_ssh_client(self.host, self.port, self.user, self.pwd, timeout=10)
            logger.info(f"ListWorker connected to {self.host}")

            def _resolve_entry(entry):
                if isinstance(entry, dict):
                    return entry["path"], entry.get("desc", "")
                return entry, ""

            files_to_check = self.ne_config.get("files", [])
            dirs_to_check = self.ne_config.get("directories", [])

            for entry in files_to_check:
                fpath, desc = _resolve_entry(entry)
                logger.debug(f"Checking file: {fpath}")
                if '*' in fpath or '?' in fpath:
                    out, err = ssh_exec(client,
                        f"ls -1 {fpath} 2>/dev/null || echo MISSING", timeout=15)
                    if out and out != "MISSING":
                        matches = [line.strip() for line in out.split("\n") if line.strip()]
                        groups = ListWorker._group_files(matches)
                        for (gdir, stem, ext), files in sorted(groups.items()):
                            pattern = f"{stem}*{ext}" if stem != os.path.basename(files[0]) else os.path.basename(files[0])
                            self.file_info.emit({
                                "path": gdir,
                                "name": pattern,
                                "size": len(files),
                                "type": "group",
                                "group_files": sorted(files),
                                "pattern_path": f"{gdir}/{pattern}",
                                "desc": desc
                            })
                    else:
                        self.file_info.emit({
                            "path": fpath,
                            "name": os.path.basename(fpath),
                            "size": 0,
                            "type": "file",
                            "missing": True,
                            "desc": desc
                        })
                else:
                    out, err = ssh_exec(client,
                        f"stat --format='%s' {fpath} 2>/dev/null || echo MISSING", timeout=15)
                    if out and out != "MISSING":
                        sz = 0
                        try:
                            sz = int(out.strip().split("\n")[0])
                        except:
                            pass
                        logger.debug(f"File found: {fpath} size={sz}")
                        self.file_info.emit({
                            "path": fpath,
                            "name": os.path.basename(fpath),
                            "size": sz,
                            "type": "file",
                            "desc": desc
                        })
                    else:
                        logger.warning(f"File missing: {fpath}")
                        self.file_info.emit({
                            "path": fpath,
                            "name": os.path.basename(fpath),
                            "size": 0,
                            "type": "file",
                            "missing": True,
                            "desc": desc
                        })

            for entry in dirs_to_check:
                dpath, desc = _resolve_entry(entry)
                logger.debug(f"Directory config entry: {dpath}")
                out, err = ssh_exec(client,
                    f"test -d {dpath} && echo OK || echo MISSING", timeout=15)
                if out == "OK":
                    logger.debug(f"Directory found: {dpath}")
                    self.file_info.emit({
                        "path": dpath,
                        "name": os.path.basename(dpath) + "/",
                        "size": 0,
                        "type": "directory",
                        "missing": False,
                        "is_config_dir": True,
                        "desc": desc
                    })
                else:
                    logger.warning(f"Directory missing: {dpath}")
                    self.file_info.emit({
                        "path": dpath,
                        "name": os.path.basename(dpath) + "/",
                        "size": 0,
                        "type": "directory",
                        "missing": True,
                        "desc": desc
                    })

        except Exception as e:
            logger.error(f"ListWorker error on {self.host}: {e}", exc_info=True)
            self.error_signal.emit(str(e))
        finally:
            if client:
                try:
                    client.close()
                    logger.debug("ListWorker SSH connection closed")
                except Exception as e:
                    logger.warning(f"ListWorker close error: {e}")
            logger.info("ListWorker finished")
            self.finished_signal.emit()


class TailWorker(QThread):
    """Tail a remote file in real-time using SSH."""
    data_received = Signal(str)
    error_occurred = Signal(str)
    disconnected = Signal()

    def __init__(self, host, port, user, pwd, remote_file):
        super().__init__()
        self.host = host
        self.port = int(port)
        self.user = user
        self.pwd = pwd
        self.remote_file = remote_file
        self._stop = False
        self._ssh = None
        self._chan = None

    def stop(self):
        self._stop = True
        try:
            if self._chan:
                self._chan.close()
        except:
            pass
        try:
            if self._ssh:
                self._ssh.close()
        except:
            pass

    def run(self):
        try:
            logger.info(f"TailWorker starting tail -f on {self.host}:{self.remote_file}")
            self._ssh = create_ssh_client(self.host, self.port, self.user, self.pwd, timeout=10)

            transport = self._ssh.get_transport()
            self._chan = transport.open_session()
            self._chan.settimeout(0.5)
            self._chan.exec_command(f"tail -n 500 -f {self.remote_file} 2>/dev/null")
            logger.debug(f"TailWorker channel opened for {self.remote_file}")

            buf = ""
            while not self._stop:
                try:
                    data = self._chan.recv(65536)
                    if not data:
                        logger.debug("TailWorker received EOF, file truncated or removed")
                        break
                    text = data.decode("utf-8", errors="replace")
                    buf += text
                    if "\n" in buf:
                        lines, buf = buf.rsplit("\n", 1)
                        if lines:
                            self.data_received.emit(lines)
                except socket.timeout:
                    continue
                except EOFError:
                    logger.debug("TailWorker EOFError")
                    break
                except Exception as e:
                    logger.warning(f"TailWorker recv error: {e}")
                    break

            if buf:
                self.data_received.emit(buf)
            logger.info(f"TailWorker stopped for {self.remote_file}")
        except Exception as e:
            if not self._stop:
                logger.error(f"TailWorker error on {self.host}:{self.remote_file}: {e}", exc_info=True)
                self.error_occurred.emit(str(e))
        finally:
            try:
                if self._chan:
                    self._chan.close()
            except:
                pass
            try:
                if self._ssh:
                    self._ssh.close()
                    logger.debug("TailWorker SSH connection closed")
            except:
                pass
            self.disconnected.emit()


class DownloadWorker(QThread):
    """Download a remote file via SCP."""
    progress = Signal(int)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, host, port, user, pwd, remote_path, local_path):
        super().__init__()
        self.host = host
        self.port = int(port)
        self.user = user
        self.pwd = pwd
        self.remote_path = remote_path
        self.local_path = local_path

    def run(self):
        try:
            logger.info(f"DownloadWorker starting: {self.host}:{self.remote_path} -> {self.local_path}")
            c = create_ssh_client(self.host, self.port, self.user, self.pwd, timeout=10)
            SCPClient(c.get_transport()).get(self.remote_path, self.local_path)
            c.close()
            logger.info(f"DownloadWorker done: {self.local_path}")
            self.done.emit(self.local_path)
        except Exception as e:
            logger.error(f"DownloadWorker error: {self.host}:{self.remote_path} - {e}", exc_info=True)
            self.error.emit(str(e))


def _format_size(sz):
    if sz >= 1024 * 1024:
        return f"{sz / (1024*1024):.1f} MB"
    elif sz >= 1024:
        return f"{sz / 1024:.1f} KB"
    return f"{sz} B"


class DirBrowseWorker(QThread):
    """List contents of a remote directory on-demand."""
    entry = Signal(dict)
    error = Signal(str)
    done_signal = Signal()

    def __init__(self, host, port, user, pwd, remote_path):
        super().__init__()
        self.host = host
        self.port = int(port)
        self.user = user
        self.pwd = pwd
        self.remote_path = remote_path.rstrip("/")

    def run(self):
        client = None
        try:
            logger.info(f"DirBrowseWorker listing {self.remote_path}")
            client = create_ssh_client(self.host, self.port, self.user, self.pwd, timeout=10)

            out_dirs, _ = ssh_exec(client,
                f"ls -1d {self.remote_path}/*/ 2>/dev/null | sed 's|/$||' || true", timeout=15)
            out_files, _ = ssh_exec(client,
                f"ls -1 {self.remote_path} 2>/dev/null", timeout=15)

            dirs = []
            if out_dirs:
                dirs = [line.strip() for line in out_dirs.split("\n") if line.strip()]

            all_files = []
            if out_files:
                all_files = [line.strip() for line in out_files.split("\n") if line.strip()]
            all_files = [f for f in all_files if f not in dirs]

            for d in sorted(dirs):
                self.entry.emit({
                    "path": d,
                    "name": os.path.basename(d) + "/",
                    "size": 0,
                    "type": "directory"
                })

            groups = ListWorker._group_files([posixpath.join(self.remote_path, f) for f in all_files])
            for (gdir, stem, ext), files in sorted(groups.items()):
                pattern = f"{stem}*{ext}" if stem != os.path.basename(files[0]) else os.path.basename(files[0])
                self.entry.emit({
                    "path": gdir,
                    "name": pattern,
                    "size": len(files),
                    "type": "group",
                    "group_files": sorted(files),
                    "pattern_path": f"{gdir}/{pattern}"
                })

        except Exception as e:
            logger.error(f"DirBrowseWorker error: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            if client:
                try:
                    client.close()
                except:
                    pass
            self.done_signal.emit()


class LogViewer(QWidget):
    def __init__(self):
        super().__init__()
        self._hosts = load_hosts()
        self._ne_configs = _load_ne_config()
        self._current_host_info = None
        self._tail_worker = None
        self._download_workers = []
        self._download_queue = []
        self._download_in_progress = False
        self._file_rows = []
        self._nav_stack = []
        self._connected = False
        self._download_dir = DESKTOP_DIR
        self._init_ui()

    def closeEvent(self, event):
        logger.info("LogViewer closing, cleaning up workers")
        self._stop_tail()
        for w in self._download_workers:
            if w.isRunning():
                logger.debug(f"Terminating download worker: {w.local_path}")
                w.terminate()
        logger.debug("LogViewer close complete")
        event.accept()

    def _card(self, title, content_widget):
        wrapper = QFrame()
        wrapper.setStyleSheet("QFrame#card{border:none; border-radius:12px; background:white;}")
        wrapper.setObjectName("card")
        v = QVBoxLayout(wrapper)
        v.setContentsMargins(16, 14, 16, 16)
        v.setSpacing(10)
        if title:
            lbl = QLabel(title)
            lbl.setStyleSheet("font-size:14px; font-weight:600; color:#1a1a1a; padding-bottom:2px;")
            v.addWidget(lbl)
        v.addWidget(content_widget)
        return wrapper

    def _secondary_btn(self, text):
        btn = QPushButton(text)
        btn.setStyleSheet(
            "QPushButton{background:#f5f5f5;color:#1a1a1a;border:1px solid #e0e0e0;"
            "border-radius:8px;padding:7px 16px;font-size:12px;}"
            "QPushButton:hover{background:#eee;}"
            "QPushButton:disabled{background:#fafafa;color:#ccc;}")
        return btn

    def _primary_btn(self, text, color="#0984e3"):
        btn = QPushButton(text)
        btn.setStyleSheet(
            f"QPushButton{{background:{color};color:white;border:none;"
            f"border-radius:8px;padding:8px 20px;font-size:13px;font-weight:600;}}"
            f"QPushButton:hover{{opacity:0.8;}}"
            f"QPushButton:disabled{{background:#ccc;color:white;}}")
        return btn

    def _init_ui(self):
        self.setWindowTitle("Log Viewer")
        self.resize(960, 720)
        self.setStyleSheet(STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── Top bar: Host + NE type + actions ──
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        top_bar.addWidget(QLabel("Host:"))
        self.host_cb = QComboBox()
        self.host_cb.setMinimumWidth(280)
        self.host_cb.setEditable(True)
        self.host_cb.setPlaceholderText("Select host")
        self.host_cb.setInsertPolicy(QComboBox.NoInsert)
        for h in self._hosts:
            display = f"{h.get('desc', h.get('name', ''))} \u2014 {h['host']}:{h.get('port',22)} ({h['user']})"
            self.host_cb.addItem(display, h)
        top_bar.addWidget(self.host_cb)

        manage_host_btn = QPushButton("Manage")
        manage_host_btn.setStyleSheet(
            "QPushButton{background:#f8f9fa;color:#636e72;border:1px solid #dfe6e9;"
            "border-radius:4px;padding:6px 12px;font-size:12px;}"
            "QPushButton:hover{background:#e8e8e8;}")
        manage_host_btn.clicked.connect(self._manage_hosts)
        top_bar.addWidget(manage_host_btn)
        self.host_cb.lineEdit().installEventFilter(self)

        top_bar.addWidget(QLabel("NE Type:"))
        self.ne_cb = QComboBox()
        self.ne_cb.setMinimumWidth(120)
        ne_list = list(self._ne_configs.keys())
        if ne_list:
            self.ne_cb.addItems(ne_list)
        top_bar.addWidget(self.ne_cb)

        self.match_label = QLabel()
        self.match_label.setStyleSheet("font-size:14px; font-weight:bold; padding:0 6px 0 2px;")
        top_bar.addWidget(self.match_label)

        self.host_cb.currentIndexChanged.connect(self._on_host_changed)
        self.ne_cb.currentIndexChanged.connect(self._on_ne_changed)

        self.connect_btn = self._primary_btn("Connect & Load", "#ff6900")
        self.connect_btn.clicked.connect(self._on_connect)
        top_bar.addWidget(self.connect_btn)

        self.refresh_btn = self._secondary_btn("Refresh")
        self.refresh_btn.clicked.connect(self._on_refresh)
        self.refresh_btn.setEnabled(False)
        top_bar.addWidget(self.refresh_btn)

        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-size:12px; color:#999;")
        top_bar.addWidget(self.status_label)
        top_bar.addStretch()

        layout.addLayout(top_bar)

        # ── File Table ──
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "Remote Path", "Description", "Filename", "Size", "Actions"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 40)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(5, 160)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        file_section = QVBoxLayout()
        file_section.setSpacing(6)

        file_toolbar = QHBoxLayout()
        self.select_all_cb = QCheckBox("Select All")
        self.select_all_cb.toggled.connect(self._on_select_all)
        file_toolbar.addWidget(self.select_all_cb)
        file_toolbar.addWidget(QLabel("  "))
        self.download_selected_btn = self._primary_btn("Download Selected", "#00b894")
        self.download_selected_btn.clicked.connect(self._on_download_selected)
        self.download_selected_btn.setEnabled(False)
        file_toolbar.addWidget(self.download_selected_btn)
        file_toolbar.addStretch()
        file_section.addLayout(file_toolbar)

        # ── Navigation bar ──
        nav_bar = QHBoxLayout()
        nav_bar.setSpacing(6)
        self.up_btn = QPushButton("\u2191 Up")
        self.up_btn.setStyleSheet(
            "QPushButton{background:#f0f3f5;color:#636e72;border:1px solid #dfe6e9;"
            "border-radius:4px;padding:4px 12px;font-size:11px;}"
            "QPushButton:hover{background:#e8e8e8;}"
            "QPushButton:disabled{background:#fafafa;color:#ccc;}")
        self.up_btn.clicked.connect(self._navigate_up)
        self.up_btn.setVisible(False)
        nav_bar.addWidget(self.up_btn)
        self.nav_label = QLabel("")
        self.nav_label.setStyleSheet("font-size:11px;color:#888;padding:4px 0;")
        nav_bar.addWidget(self.nav_label)
        nav_bar.addStretch()
        file_section.addLayout(nav_bar)

        file_section.addWidget(self.table)
        self.table.cellClicked.connect(self._on_table_clicked)

        file_card = QFrame()
        file_card.setObjectName("card")
        file_card.setStyleSheet("QFrame#card{border:none; border-radius:12px; background:white;}")
        file_card_v = QVBoxLayout(file_card)
        file_card_v.setContentsMargins(16, 14, 16, 16)
        file_card_v.setSpacing(10)
        file_card_v.addLayout(file_section)
        layout.addWidget(file_card, 3)

        # ── Log Viewer Panel ──
        viewer_card = QFrame()
        viewer_card.setObjectName("card")
        viewer_card.setStyleSheet("QFrame#card{border:none; border-radius:12px; background:white;}")
        viewer_v = QVBoxLayout(viewer_card)
        viewer_v.setContentsMargins(16, 10, 16, 16)
        viewer_v.setSpacing(8)

        viewer_top = QHBoxLayout()
        self.viewer_title = QLabel("Log Viewer")
        self.viewer_title.setStyleSheet("font-size:14px; font-weight:600; color:#1a1a1a;")
        viewer_top.addWidget(self.viewer_title)
        viewer_top.addStretch()

        self.viewer_status = QLabel("Idle")
        self.viewer_status.setStyleSheet("font-size:11px; color:#999;")
        viewer_top.addWidget(self.viewer_status)

        self.tail_btn = QPushButton("Start Watching")
        self.tail_btn.setStyleSheet(
            "QPushButton{background:#0984e3;color:white;border:none;"
            "border-radius:8px;padding:6px 16px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:#0873c4;}"
            "QPushButton:disabled{background:#ccc;color:white;}")
        self.tail_btn.clicked.connect(self._on_toggle_tail)
        self.tail_btn.setEnabled(False)
        viewer_top.addWidget(self.tail_btn)

        self.dl_viewer_btn = self._secondary_btn("Download")
        self.dl_viewer_btn.clicked.connect(self._on_download_viewing)
        self.dl_viewer_btn.setEnabled(False)
        viewer_top.addWidget(self.dl_viewer_btn)

        viewer_v.addLayout(viewer_top)

        self.viewer = QTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setFont(QFont("Consolas", 10))
        self.viewer.setMinimumHeight(180)
        self.viewer.setStyleSheet(
            "QTextEdit{background:#1e1e1e;border:1px solid #333;border-radius:6px;"
            "padding:6px;font-size:12px;color:#d4d4d4;font-family:Consolas,'Courier New',monospace;}")
        viewer_v.addWidget(self.viewer)

        clear_viewer_btn = self._secondary_btn("Clear")
        clear_viewer_btn.clicked.connect(self._on_clear_viewer)
        clear_row = QHBoxLayout()
        clear_row.addStretch()
        clear_row.addWidget(clear_viewer_btn)
        viewer_v.addLayout(clear_row)

        layout.addWidget(viewer_card, 2)

        # ── Progress ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(6)
        layout.addWidget(self.progress_bar)

        self._current_viewing = None
        self._init_done = False
        self._auto_connecting = False
        self._connect_timer = QTimer(self)
        self._connect_timer.setSingleShot(True)
        self._connect_timer.timeout.connect(self._do_auto_connect)
        QTimer.singleShot(200, self._auto_connect_first)

    def _on_select_all(self, checked):
        for row_data in self._file_rows:
            cb = row_data.get("cb")
            if cb:
                cb.setChecked(checked)

    def _on_host_changed(self, idx):
        if not self._init_done or idx < 0 or idx >= len(self._hosts):
            return
        h = self._hosts[idx]
        matched = self._auto_match_ne(h)
        if matched:
            ne_idx = self.ne_cb.findText(matched)
            if ne_idx >= 0 and ne_idx != self.ne_cb.currentIndex():
                self.ne_cb.blockSignals(True)
                self.ne_cb.setCurrentIndex(ne_idx)
                self.ne_cb.blockSignals(False)
        self._update_match_indicator()
        self._connect_timer.start(300)

    def _on_ne_changed(self, idx):
        if not self._init_done:
            return
        self._update_match_indicator()

    def _auto_match_ne(self, host_info):
        name = (host_info.get('name') or host_info.get('desc') or '').upper()
        for ne_name in self._ne_configs:
            if ne_name.upper() in name:
                return ne_name
        return None

    def _update_match_indicator(self):
        idx = self.host_cb.currentIndex()
        if idx < 0:
            self.match_label.setText("")
            return
        h = self._hosts[idx]
        ne_name = self.ne_cb.currentText()
        host_name = (h.get('name') or h.get('desc') or '').upper()
        if ne_name.upper() in host_name:
            self.match_label.setText("\u2713")
            self.match_label.setStyleSheet("color:#00b894; font-size:16px; font-weight:bold; padding:0 6px 0 2px;")
        else:
            self.match_label.setText("\u2717")
            self.match_label.setStyleSheet("color:#e74c3c; font-size:16px; font-weight:bold; padding:0 6px 0 2px;")

    def _auto_connect_first(self):
        if not self._hosts or self.ne_cb.count() == 0:
            self._init_done = True
            return
        self._init_done = True
        h = self._hosts[0]
        matched = self._auto_match_ne(h)
        if matched:
            ne_idx = self.ne_cb.findText(matched)
            if ne_idx >= 0:
                self.ne_cb.setCurrentIndex(ne_idx)
        self._update_match_indicator()
        self._auto_connecting = True
        self._on_connect()

    def _set_connection_status(self, text, color="#999"):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"font-size:12px; color:{color};")

    def _do_auto_connect(self):
        self._auto_connecting = True
        self._on_connect()

    def _on_connect(self):
        idx = self.host_cb.currentIndex()
        if idx < 0 or idx >= len(self._hosts):
            if not self._auto_connecting:
                QMessageBox.warning(self, "Warning", "Please select a valid host")
            return
        h = self._hosts[idx]
        ne_name = self.ne_cb.currentText()
        if ne_name not in self._ne_configs:
            if not self._auto_connecting:
                QMessageBox.warning(self, "Warning", f"NE type '{ne_name}' not configured")
            return

        logger.info(f"Connecting to {h['host']}:{h.get('port',22)} NE={ne_name}")
        self._current_host_info = h
        self._current_ne_name = ne_name
        self._connected = False
        self._nav_stack = []
        self._update_nav_ui()
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("Connecting...")
        self._set_connection_status("Connecting...", "#f39c12")
        self.refresh_btn.setEnabled(False)
        self._stop_tail()
        self._clear_table()

        self._list_worker = ListWorker(
            h["host"], h.get("port", 22), h["user"], h.get("pwd", ""),
            self._ne_configs[ne_name])
        self._list_worker.file_info.connect(self._on_file_info)
        self._list_worker.error_signal.connect(self._on_list_error)
        self._list_worker.finished_signal.connect(self._on_list_finished)
        self._list_worker.start()

    def _on_refresh(self):
        if self._current_host_info and self._current_ne_name:
            self._on_connect()

    def _on_file_info(self, info):
        row = self.table.rowCount()
        self.table.insertRow(row)

        is_missing = info.get("missing", False)
        is_group = info.get("type") == "group"
        is_dir = info.get("type") == "directory"

        cb = QCheckBox()
        cb_widget = QWidget()
        cb_layout = QHBoxLayout(cb_widget)
        cb_layout.setContentsMargins(0, 0, 0, 0)
        cb_layout.setAlignment(Qt.AlignCenter)
        cb_layout.addWidget(cb)
        self.table.setCellWidget(row, 0, cb_widget)

        name_item = QTableWidgetItem(info.get("name", ""))
        if is_missing:
            name_item.setForeground(Qt.gray)
        if is_group:
            name_item.setForeground(Qt.darkBlue)
        if is_dir:
            name_item.setForeground(Qt.darkMagenta)
            name_item.setToolTip("Click to enter directory")
        self.table.setItem(row, 3, name_item)

        desc = info.get("desc", "")
        if not desc:
            desc = "\u65e0"
        desc_item = QTableWidgetItem(desc)
        if is_missing:
            desc_item.setForeground(Qt.gray)
        self.table.setItem(row, 2, desc_item)

        if is_group:
            group_files = info.get("group_files", [])
            is_single = len(group_files) == 1
            if is_single:
                path_item = QTableWidgetItem(info.get("path", ""))
                self.table.setItem(row, 1, path_item)
            else:
                expand_btn = QPushButton(f"\u25be {info.get('path', '')}")
                expand_btn.setStyleSheet(
                    "QPushButton{background:transparent;border:none;text-align:left;"
                    "padding:4px 2px;font-size:12px;color:#0984e3;}"
                    "QPushButton:hover{color:#e17055;}")
                expand_btn.setCursor(Qt.PointingHandCursor)
                expand_btn.clicked.connect(lambda checked, gf=group_files, btn=expand_btn: self._open_group_menu(btn, gf))
                self.table.setCellWidget(row, 1, expand_btn)
        else:
            path_item = QTableWidgetItem(info.get("path", ""))
            if is_missing:
                path_item.setForeground(Qt.gray)
            self.table.setItem(row, 1, path_item)

        if is_group:
            size_str = f"{info.get('size', 0)} files"
        elif is_dir:
            size_str = "folder"
        else:
            size_str = _format_size(info.get("size", 0)) if not is_missing else "N/A"
        size_item = QTableWidgetItem(size_str)
        size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.table.setItem(row, 4, size_item)

        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(4, 2, 4, 2)
        actions_layout.setSpacing(4)

        view_btn = QPushButton("View")
        view_btn.setStyleSheet(
            "QPushButton{background:#0984e3;color:white;border:none;"
            "border-radius:4px;padding:4px 12px;font-size:11px;font-weight:600;}"
            "QPushButton:hover{background:#0873c4;}")
        dl_btn = QPushButton("Download")
        dl_btn.setStyleSheet(
            "QPushButton{background:#00b894;color:white;border:none;"
            "border-radius:4px;padding:4px 12px;font-size:11px;font-weight:600;}"
            "QPushButton:hover{background:#00a381;}")

        if is_missing:
            view_btn.setEnabled(False)
            dl_btn.setEnabled(False)
            cb.setEnabled(False)
        if is_dir:
            view_btn.setText("Enter")

        file_path = info.get("path", "")
        group_files = info.get("group_files", [])

        if is_group:
            is_single = len(group_files) == 1
            if is_single:
                single_path = group_files[0]
                view_btn.clicked.connect(lambda checked, p=single_path: self._on_view_file(p))
            else:
                view_btn.clicked.connect(lambda checked, gf=group_files, btn=view_btn: self._open_group_menu(btn, gf))
            dl_btn.clicked.connect(lambda checked, gf=group_files: self._on_download_group(gf))
        elif is_dir:
            view_btn.clicked.connect(lambda checked, p=file_path: self._navigate_to(p))
            dl_btn.clicked.connect(lambda checked, p=file_path, n=info.get("name", "file"): self._on_download_single(p, n))
        else:
            view_btn.clicked.connect(lambda checked, p=file_path: self._on_view_file(p))
            dl_btn.clicked.connect(lambda checked, p=file_path, n=info.get("name", "file"): self._on_download_single(p, n))

        actions_layout.addWidget(view_btn)
        actions_layout.addWidget(dl_btn)
        actions_layout.addStretch()
        self.table.setCellWidget(row, 5, actions_widget)

        self._file_rows.append({
            "cb": cb,
            "path": file_path,
            "name": info.get("name", ""),
            "desc": desc,
            "missing": is_missing,
            "is_group": is_group,
            "is_dir": is_dir,
            "group_files": group_files
        })

    def _on_list_error(self, msg):
        logger.error(f"File listing error: {msg}")
        self._set_connection_status("\u26a0 Disconnected", "#e74c3c")
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Connect & Load")
        was_auto = self._auto_connecting
        self._auto_connecting = False
        if not was_auto:
            QMessageBox.warning(self, "Connection Error", msg)

    def _on_list_finished(self):
        self._connected = True
        self._auto_connecting = False
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Connect & Load")
        self.refresh_btn.setEnabled(True)
        self.download_selected_btn.setEnabled(self.table.rowCount() > 0)
        rc = self.table.rowCount()
        logger.info(f"File listing complete: {rc} items from {self._current_ne_name} on {self._current_host_info['host']}")
        self._set_connection_status(f"\u25cf Connected  |  {rc} files from {self._current_ne_name}", "#00b894")
        self.viewer_title.setText(f"Log Viewer - {self._current_host_info['host']}")

    def _clear_table(self):
        self.table.setRowCount(0)
        self._file_rows = []
        self.download_selected_btn.setEnabled(False)

    def _on_table_clicked(self, row, col):
        if col != 1:
            return
        if row < 0 or row >= len(self._file_rows):
            return
        row_data = self._file_rows[row]
        if row_data.get("is_dir") and not row_data.get("missing") and row_data.get("path"):
            self._navigate_to(row_data["path"])

    def _navigate_to(self, remote_path):
        if not self._current_host_info:
            return
        remote_path = remote_path.rstrip("/")
        logger.info(f"Navigate into directory: {remote_path}")
        self._stop_tail()
        self._nav_stack.append(remote_path)
        self._update_nav_ui()
        self.connect_btn.setEnabled(False)
        self.status_label.setText(f"Loading {remote_path}...")
        self._clear_table()
        h = self._current_host_info
        self._browse_worker = DirBrowseWorker(
            h["host"], h.get("port", 22), h["user"], h.get("pwd", ""), remote_path)
        self._browse_worker.entry.connect(self._on_file_info)
        self._browse_worker.error.connect(self._on_list_error)
        self._browse_worker.done_signal.connect(self._on_browse_finished)
        self._browse_worker.start()

    def _navigate_up(self):
        if len(self._nav_stack) <= 1:
            self._nav_stack = []
            self.up_btn.setVisible(False)
            self.nav_label.setText("")
            self._on_refresh()
            return
        self._nav_stack.pop()
        parent = self._nav_stack[-1] if self._nav_stack else ""
        if parent:
            self._navigate_to(parent)
        else:
            self._nav_stack = []
            self.up_btn.setVisible(False)
            self.nav_label.setText("")
            self._on_refresh()

    def _update_nav_ui(self):
        if self._nav_stack:
            self.up_btn.setVisible(True)
            self.nav_label.setText(" > ".join(self._nav_stack))
        else:
            self.up_btn.setVisible(False)
            self.nav_label.setText("")

    def _on_browse_finished(self):
        self.connect_btn.setEnabled(True)
        rc = self.table.rowCount()
        self.download_selected_btn.setEnabled(rc > 0)
        path = self._nav_stack[-1] if self._nav_stack else ""
        logger.info(f"Browse finished: {rc} items in {path}")
        self.status_label.setText(f"{rc} item(s) in {path}")

    def _on_view_file(self, remote_path):
        if not self._current_host_info:
            return
        logger.info(f"View file: {remote_path}")
        self._stop_tail()
        self._current_viewing = remote_path
        self.viewer.clear()
        self.viewer_title.setText(f"Viewing: {remote_path}")
        self.viewer_status.setText("Connecting...")
        self.tail_btn.setEnabled(True)
        self.dl_viewer_btn.setEnabled(True)
        self._start_tail()

    def _open_group_menu(self, anchor, group_files):
        if not group_files or not self._current_host_info:
            return
        logger.debug(f"Opening group menu: {len(group_files)} files")
        sorted_files = sorted(group_files)
        total = len(sorted_files)

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:white;border:1px solid #ddd;border-radius:6px;padding:4px;}"
            "QMenu::item{padding:6px 20px;font-size:12px;}"
            "QMenu::item:selected{background:#e8f0fe;color:#1a1a1a;}")

        if total <= 20:
            for fp in sorted_files:
                fname = os.path.basename(fp)
                action = QAction(fname, self)
                action.setData(fp)
                menu.addAction(action)
        else:
            latest = sorted_files[-1]
            latest_action = QAction(f"View Latest - {os.path.basename(latest)}", self)
            latest_action.setData(latest)
            latest_action.setFont(QFont(self.font().family(), 12, QFont.Weight.Bold))
            menu.addAction(latest_action)
            menu.addSeparator()

            show_count = min(10, total)
            for fp in sorted_files[-show_count:]:
                fname = os.path.basename(fp)
                action = QAction(fname, self)
                action.setData(fp)
                menu.addAction(action)

            menu.addSeparator()
            all_action = QAction(f"Show all {total} files...", self)
            all_action.setData("__show_all__")
            menu.addAction(all_action)

        selected = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        if selected:
            data = selected.data()
            if data == "__show_all__":
                self._show_all_files_dialog(sorted_files)
            else:
                self._on_view_file(data)

    def _show_all_files_dialog(self, sorted_files):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"All {len(sorted_files)} files")
        dlg.resize(480, 500)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(6)

        search_input = QLineEdit()
        search_input.setPlaceholderText("Search filename...")
        layout.addWidget(search_input)

        list_widget = QListWidget()
        layout.addWidget(list_widget)

        def populate(filter_text=""):
            list_widget.clear()
            for fp in sorted_files:
                fname = os.path.basename(fp)
                if filter_text.lower() in fname.lower():
                    item = QListWidgetItem(fname)
                    item.setData(Qt.UserRole, fp)
                    list_widget.addItem(item)
            status_label.setText(f"{list_widget.count()} / {len(sorted_files)}")

        status_label = QLabel()
        layout.addWidget(status_label)
        populate()

        search_input.textChanged.connect(populate)
        list_widget.itemDoubleClicked.connect(lambda item: self._on_view_file(item.data(Qt.UserRole)) or dlg.accept())

        btn_row = QHBoxLayout()
        view_btn = QPushButton("View Selected")
        view_btn.clicked.connect(lambda: (self._on_view_file(list_widget.currentItem().data(Qt.UserRole)), dlg.accept()) if list_widget.currentItem() else None)
        dl_btn = QPushButton("Download Selected")
        dl_btn.clicked.connect(lambda: (self._on_download_single(list_widget.currentItem().data(Qt.UserRole), list_widget.currentItem().text()), dlg.accept()) if list_widget.currentItem() else None)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(view_btn)
        btn_row.addWidget(dl_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        dlg.exec()

    def _on_toggle_tail(self):
        if self._tail_worker and self._tail_worker.isRunning():
            self._stop_tail()
        else:
            self._start_tail()

    def _start_tail(self):
        if not self._current_host_info or not self._current_viewing:
            logger.warning("_start_tail called without host info or viewing path")
            return
        h = self._current_host_info
        self._stop_tail()
        logger.debug(f"Starting tail worker for {self._current_viewing}")
        self._tail_worker = TailWorker(
            h["host"], h.get("port", 22), h["user"], h.get("pwd", ""),
            self._current_viewing)
        self._tail_worker.data_received.connect(self._on_tail_data)
        self._tail_worker.error_occurred.connect(self._on_tail_error)
        self._tail_worker.disconnected.connect(self._on_tail_disconnected)
        self._tail_worker.start()
        self.tail_btn.setText("Stop Watching")
        self.viewer_status.setText("Watching...")

    def _stop_tail(self, worker=None):
        target = worker or self._tail_worker
        if target:
            logger.debug("Stopping tail worker")
            try:
                target.disconnected.disconnect(self._on_tail_disconnected)
            except (TypeError, RuntimeError):
                pass
            try:
                target.error_occurred.disconnect(self._on_tail_error)
            except (TypeError, RuntimeError):
                pass
            try:
                target.data_received.disconnect(self._on_tail_data)
            except (TypeError, RuntimeError):
                pass
            target.stop()
            if target.isRunning():
                target.wait(2000)
            if target is self._tail_worker:
                self._tail_worker = None
        self.tail_btn.setText("Start Watching")
        if not self._current_viewing:
            self.viewer_status.setText("Idle")
        else:
            self.viewer_status.setText("Stopped")

    def _on_tail_data(self, data):
        self.viewer.append(data)
        scrollbar = self.viewer.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_tail_error(self, msg):
        logger.error(f"Tail error: {msg}")
        self.viewer.append(f"[error] {msg}")
        self._stop_tail()

    def _on_tail_disconnected(self):
        if self.sender() is not self._tail_worker:
            return
        logger.debug("Tail worker disconnected")
        self._stop_tail()

    def _on_clear_viewer(self):
        logger.debug("Viewer cleared")
        self.viewer.clear()

    def _on_download_single(self, remote_path, filename):
        if not self._current_host_info:
            return
        local_dir = QFileDialog.getExistingDirectory(self, "Select download directory", DESKTOP_DIR)
        if not local_dir:
            return
        local_path = os.path.join(local_dir, filename)
        self._do_download(remote_path, local_path)

    def _on_download_group(self, group_files):
        if not group_files or not self._current_host_info:
            return
        logger.info(f"Downloading group: {len(group_files)} files")
        local_dir = QFileDialog.getExistingDirectory(self, "Select download directory", DESKTOP_DIR)
        if not local_dir:
            return
        for fp in group_files:
            local_path = os.path.join(local_dir, os.path.basename(fp))
            self._do_download(fp, local_path)

    def _on_download_selected(self):
        selected = [r for r in self._file_rows if r.get("cb") and r["cb"].isChecked() and not r.get("missing")]
        if not selected:
            QMessageBox.information(self, "Info", "No files selected")
            return
        logger.info(f"Download selected: {len(selected)} items")
        local_dir = QFileDialog.getExistingDirectory(self, "Select download directory", DESKTOP_DIR)
        if not local_dir:
            return
        for r in selected:
            if r.get("is_group") and r.get("group_files"):
                for fp in r["group_files"]:
                    local_path = os.path.join(local_dir, os.path.basename(fp))
                    self._do_download(fp, local_path)
            else:
                local_path = os.path.join(local_dir, r["name"])
                self._do_download(r["path"], local_path)

    def _on_download_viewing(self):
        if not self._current_viewing or not self._current_host_info:
            return
        local_dir = QFileDialog.getExistingDirectory(self, "Select download directory", DESKTOP_DIR)
        if not local_dir:
            return
        filename = os.path.basename(self._current_viewing)
        local_path = os.path.join(local_dir, filename)
        self._do_download(self._current_viewing, local_path)

    def _do_download(self, remote_path, local_path):
        if not self._current_host_info:
            return
        self._download_queue.append((remote_path, local_path))
        self._process_download_queue()

    def _process_download_queue(self):
        if self._download_in_progress or not self._download_queue:
            if not self._download_queue and not self._download_in_progress:
                self.progress_bar.setVisible(False)
            return
        self._download_in_progress = True
        remote_path, local_path = self._download_queue.pop(0)
        h = self._current_host_info
        logger.info(f"Download queued: {remote_path} -> {local_path}")
        w = DownloadWorker(
            h["host"], h.get("port", 22), h["user"], h.get("pwd", ""),
            remote_path, local_path)
        w.done.connect(lambda lp: self._on_download_done(lp))
        w.error.connect(lambda msg, rp=remote_path: self._on_download_error(msg, rp))
        self._download_workers.append(w)
        w.start()
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(f"Downloading {os.path.basename(remote_path)}...")

    def _on_download_done(self, local_path):
        logger.info(f"Download complete: {local_path}")
        self._download_in_progress = False
        self._process_download_queue()

    def _on_download_error(self, msg, remote_path):
        logger.error(f"Download failed: {remote_path} - {msg}")
        self._download_in_progress = False
        self._process_download_queue()

    def _manage_hosts(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Manage Hosts")
        dlg.resize(520, 380)
        layout = QVBoxLayout(dlg)
        host_list = QListWidget()
        host_list.setDragDropMode(QListWidget.InternalMove)
        host_list.setDefaultDropAction(Qt.MoveAction)
        layout.addWidget(QLabel("Saved Hosts:"))
        layout.addWidget(host_list)
        form = QFormLayout()
        form.setSpacing(6)
        ip_edit = QLineEdit(); ip_edit.setPlaceholderText("192.168.1.100")
        port_edit = QLineEdit("22"); port_edit.setFixedWidth(80)
        user_edit = QLineEdit("root")
        pwd_edit = QLineEdit(); pwd_edit.setEchoMode(QLineEdit.Password)
        desc_edit = QLineEdit(); desc_edit.setPlaceholderText("e.g. Beijing-Core-MGCF")
        form.addRow("IP:", ip_edit)
        p_row = QHBoxLayout(); p_row.addWidget(port_edit); p_row.addStretch()
        form.addRow("Port:", p_row)
        form.addRow("User:", user_edit)
        form.addRow("Password:", pwd_edit)
        form.addRow("Desc:", desc_edit)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add"); add_btn.setStyleSheet("QPushButton{background:#0984e3;color:white;padding:6px 16px;border:none;border-radius:4px;}")
        update_btn = QPushButton("Update"); update_btn.setStyleSheet("QPushButton{background:#f0f3f5;color:#333;border:1px solid #ccc;padding:6px 16px;border-radius:4px;}")
        del_btn = QPushButton("Delete"); del_btn.setStyleSheet("QPushButton{background:#e74c3c;color:white;padding:6px 16px;border:none;border-radius:4px;}")
        btn_row.addWidget(add_btn); btn_row.addWidget(update_btn); btn_row.addWidget(del_btn); btn_row.addStretch()
        layout.addLayout(btn_row)

        def refresh_list():
            host_list.clear()
            for h in self._hosts:
                item = QListWidgetItem(f"{h.get('desc', h.get('name', ''))} \u2014 {h['host']}:{h.get('port',22)} ({h['user']})")
                item.setData(Qt.UserRole, h.copy())
                host_list.addItem(item)

        def sync_hosts_order():
            new_order = []
            for i in range(host_list.count()):
                h = host_list.item(i).data(Qt.UserRole)
                new_order.append(h)
            self._hosts[:] = new_order
            self._save_hosts()
            self._reload_hosts()

        host_list.model().rowsMoved.connect(sync_hosts_order)

        def on_select():
            row_idx = host_list.currentRow()
            if 0 <= row_idx < len(self._hosts):
                h = self._hosts[row_idx]
                ip_edit.setText(h["host"]); port_edit.setText(str(h.get("port",22)))
                user_edit.setText(h["user"]); pwd_edit.setText(h.get("pwd",""))
                desc_edit.setText(h.get("desc",""))

        host_list.currentRowChanged.connect(on_select)
        refresh_list()

        def on_add():
            if not ip_edit.text().strip(): return
            host_ip = ip_edit.text().strip()
            logger.info(f"Adding host: {host_ip}")
            self._hosts.append(dict(host=host_ip, port=int(port_edit.text().strip() or "22"),
                user=user_edit.text().strip() or "root", pwd=pwd_edit.text(), name=desc_edit.text().strip() or host_ip, desc=desc_edit.text().strip()))
            self._save_hosts()
            refresh_list(); self._reload_hosts()
            ip_edit.clear(); pwd_edit.clear(); desc_edit.clear(); port_edit.setText("22"); user_edit.setText("root")

        add_btn.clicked.connect(on_add)

        def on_update():
            row_idx = host_list.currentRow()
            if row_idx < 0 or not ip_edit.text().strip(): return
            host_ip = ip_edit.text().strip()
            logger.info(f"Updating host [{row_idx}]: {self._hosts[row_idx]['host']} -> {host_ip}")
            self._hosts[row_idx] = dict(host=host_ip, port=int(port_edit.text().strip() or "22"),
                user=user_edit.text().strip() or "root", pwd=pwd_edit.text(), name=desc_edit.text().strip() or host_ip, desc=desc_edit.text().strip())
            self._save_hosts()
            refresh_list(); self._reload_hosts()

        update_btn.clicked.connect(on_update)

        def on_del():
            row_idx = host_list.currentRow()
            if row_idx < 0: return
            host_ip = self._hosts[row_idx]['host']
            if QMessageBox.question(dlg, "Confirm", f"Delete {host_ip}?") == QMessageBox.Yes:
                logger.info(f"Deleting host: {host_ip}")
                self._hosts.pop(row_idx); self._save_hosts(); refresh_list(); self._reload_hosts()

        del_btn.clicked.connect(on_del)
        dlg.exec()

    def _save_hosts(self):
        cfg = os.path.join(BASE_DIR, "config", "hosts.json")
        try:
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump(self._hosts, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved {len(self._hosts)} hosts to {cfg}")
        except Exception as e:
            logger.error(f"Failed to save hosts: {e}")
            QMessageBox.warning(self, "Save Failed", str(e))

    def _reload_hosts(self):
        self.host_cb.blockSignals(True)
        current = self.host_cb.currentData()
        self.host_cb.clear()
        for h in self._hosts:
            display = f"{h.get('desc', h.get('name', ''))} \u2014 {h['host']}:{h.get('port',22)} ({h['user']})"
            self.host_cb.addItem(display, h)
        if current:
            for i in range(self.host_cb.count()):
                if self.host_cb.itemData(i) == current:
                    self.host_cb.setCurrentIndex(i)
                    break
        self.host_cb.blockSignals(False)
        logger.debug(f"Reloaded {self.host_cb.count()} hosts into combo box")

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if obj == self.host_cb.lineEdit():
                self.host_cb.showPopup()
                return True
        return super().eventFilter(obj, event)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = QApplication(sys.argv)
    w = LogViewer()
    w.show()
    sys.exit(app.exec())

# -*- coding: utf-8 -*-
import sys, os, json, logging, time
from datetime import datetime

import paramiko
from scp import SCPClient

from PySide6.QtWidgets import (QWidget, QApplication, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QTextEdit, QFileDialog,
    QMessageBox, QGroupBox, QComboBox, QFormLayout, QFrame, QProgressBar)
from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtGui import QFont

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

XIAOMI_SS = """
    TcpdumpCapture {
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
        color:#1a1a1a;
    }
    QLineEdit:focus {
        border-color:#ff6900;
    }
    QLineEdit:disabled {
        background:#f5f5f5;
        color:#999;
    }
    QComboBox {
        background:white;
        border:1px solid #e0e0e0;
        border-radius:8px;
        padding:7px 12px;
        font-size:13px;
        color:#1a1a1a;
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
    QTextEdit {
        background:#fafafa;
        border:1px solid #e8e8e8;
        border-radius:8px;
        padding:8px;
        font-size:11px;
        color:#1a1a1a;
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
"""


def _load_hosts():
    cfg = os.path.join(BASE_DIR, "config", "hosts.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


class CaptureWorker(QThread):
    log = Signal(str)
    progress = Signal(int)
    done = Signal(str)

    def __init__(self, host, port, user, pwd, cmd, remote_path, local_path, duration, compress=True):
        super().__init__()
        self.host = host
        self.port = int(port)
        self.user = user
        self.pwd = pwd
        self.cmd = cmd
        self.remote_path = remote_path
        self.local_path = local_path
        self.duration = int(duration)
        self.compress = compress

    def _ssh(self):
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(self.host, self.port, self.user, self.pwd, timeout=10)
        return c

    def _ssh_exec(self, cmd, timeout=10):
        c = self._ssh()
        chan = c.get_transport().open_session()
        chan.settimeout(timeout)
        chan.exec_command(cmd)
        out = chan.makefile("rb", -1).read()
        err = chan.makefile_stderr("rb", -1).read().decode("utf-8", errors="replace").strip()
        c.close()
        for enc in ("utf-8", "gbk"):
            try:
                return out.decode(enc).strip(), err
            except UnicodeDecodeError:
                continue
        return out.decode("utf-8", errors="replace").strip(), err

    def _ssh_nohup(self, cmd):
        c = self._ssh()
        chan = c.get_transport().open_session()
        chan.exec_command(cmd)
        chan.close()
        c.close()

    def run(self):
        try:
            self.log.emit(f"[tcpdump] 执行: {self.cmd}")
            self._ssh_exec("mkdir -p /opt/tar")
            self._ssh_nohup(f"nohup {self.cmd} > /dev/null 2>&1 &")
            self.log.emit(f"[tcpdump] 开始抓包 {self.duration} 秒...")
            for i in range(self.duration):
                time.sleep(1)
                self.progress.emit(int((i + 1) * 100 / self.duration))
            self.log.emit(f"[tcpdump] 停止抓包")
            self._ssh_exec("killall tcpdump 2>/dev/null; pkill tcpdump 2>/dev/null; pgrep tcpdump | xargs -r kill 2>/dev/null; sleep 2")
            self.log.emit(f"[tcpdump] 抓包完成: {self.remote_path}")

            out, err = self._ssh_exec(f"test -f {self.remote_path} && echo OK || echo MISSING")
            if out != "OK":
                out2, _ = self._ssh_exec("which tcpdump")
                out3, _ = self._ssh_exec("ls -la /opt/tar/ 2>&1; tcpdump --version 2>&1")
                raise RuntimeError(f"文件不存在!\n检查 tcpdump: {out2}\n/opt/tar/: {out3}")

            dl_path = self.remote_path
            dl_local = self.local_path
            if self.compress:
                gz_path = self.remote_path + ".gz"
                self.log.emit(f"[压缩] gzip {self.remote_path}")
                self._ssh_exec(f"gzip -f {self.remote_path}", timeout=120)
                self.log.emit(f"[压缩] 完成: {gz_path}")
                dl_path = gz_path
                dl_local = self.local_path + ".gz"

            self.log.emit(f"[下载] 开始传输: {dl_path} -> {dl_local}")
            c = self._ssh()
            SCPClient(c.get_transport()).get(dl_path, dl_local)
            c.close()
            self._ssh_nohup(f"rm -f {dl_path}")
            self.log.emit(f"[完成] 文件已保存: {dl_local}")
            self.done.emit(dl_local)
        except Exception as e:
            self.log.emit(f"[错误] {e}")
            logger.exception("CaptureWorker error")


class TcpdumpCapture(QWidget):
    def __init__(self):
        super().__init__()
        self._hosts = _load_hosts()
        self._capture_filter = ""
        self._capture_out = ""
        self._init_ui()

    def _card(self, title, content_widget):
        wrapper = QFrame()
        wrapper.setStyleSheet(
            "QFrame#card{border:none; border-radius:12px; background:white;}")
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

    def _init_ui(self):
        self.setWindowTitle("远程抓包工具")
        self.resize(780, 700)
        self.setStyleSheet(XIAOMI_SS)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── Host Row ──
        host_row = QHBoxLayout()
        host_row.setSpacing(6)
        self.host_cb = QComboBox()
        self.host_cb.setMinimumWidth(220)
        for h in self._hosts:
            self.host_cb.addItem(f"{h['user']}@{h['host']}:{h.get('port',22)}", h)
        host_label = QLabel("主机")
        host_label.setStyleSheet("font-size:13px;font-weight:500;color:#1a1a1a;")
        host_row.addWidget(host_label)
        host_row.addWidget(self.host_cb)
        self.status_lbl = QLabel("⬤  未连接")
        self.status_lbl.setStyleSheet("font-size:12px;color:#999;")
        host_row.addWidget(self.status_lbl)
        host_row.addStretch()
        layout.addLayout(host_row)

        # ── Filter Card ──
        filter_widget = QWidget()
        f = QFormLayout(filter_widget)
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(8)
        f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.capture_proto = QComboBox()
        self.capture_proto.addItems(["any", "tcp", "udp", "icmp", "arp", "sip"])
        self.capture_proto.currentTextChanged.connect(self._gen_cmd)
        f.addRow("协议", self.capture_proto)

        ip_row = QHBoxLayout()
        ip_row.setSpacing(6)
        self.capture_src_ip = QLineEdit()
        self.capture_src_ip.setPlaceholderText("源 IP")
        self.capture_src_ip.setFixedWidth(160)
        self.capture_src_ip.textChanged.connect(self._gen_cmd)
        ip_row.addWidget(self.capture_src_ip)
        arrow = QLabel("→")
        arrow.setStyleSheet("color:#ccc;font-size:14px;")
        ip_row.addWidget(arrow)
        self.capture_dst_ip = QLineEdit()
        self.capture_dst_ip.setPlaceholderText("目标 IP")
        self.capture_dst_ip.setFixedWidth(160)
        self.capture_dst_ip.textChanged.connect(self._gen_cmd)
        ip_row.addWidget(self.capture_dst_ip)
        ip_row.addStretch()
        f.addRow("IP", ip_row)

        port_row = QHBoxLayout()
        port_row.setSpacing(6)
        self.capture_src_port = QLineEdit()
        self.capture_src_port.setPlaceholderText("源")
        self.capture_src_port.setFixedWidth(90)
        self.capture_src_port.textChanged.connect(self._gen_cmd)
        port_row.addWidget(self.capture_src_port)
        arrow2 = QLabel("→")
        arrow2.setStyleSheet("color:#ccc;font-size:14px;")
        port_row.addWidget(arrow2)
        self.capture_dst_port = QLineEdit()
        self.capture_dst_port.setPlaceholderText("目标")
        self.capture_dst_port.setFixedWidth(90)
        self.capture_dst_port.textChanged.connect(self._gen_cmd)
        port_row.addWidget(self.capture_dst_port)
        port_row.addStretch()
        f.addRow("端口", port_row)

        self.direction_cb = QCheckBox("区分方向")
        self.direction_cb.setChecked(False)
        self.direction_cb.toggled.connect(self._gen_cmd)
        f.addRow("", self.direction_cb)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#e0e0e0; border:none;")
        f.addRow("", sep)

        self.cmd_preview = QLineEdit()
        self.cmd_preview.setReadOnly(True)
        self.cmd_preview.setStyleSheet(
            "QLineEdit{background:#fafafa;border:1px solid #e0e0e0;border-radius:8px;"
            "padding:8px 12px;font-family:Menlo,'Consolas',monospace;font-size:11px;color:#1a1a1a;}")
        f.addRow("", self.cmd_preview)

        layout.addWidget(self._card("过滤条件", filter_widget))

        # ── Save Path Row ──
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        self.save_path = QLineEdit()
        self.save_path.setPlaceholderText("保存路径")
        self.save_path.setText(r"F:\BaiduNetdiskDownload\Bangladesh\ICX_BTCL\ANS")
        path_row.addWidget(self.save_path)
        browse_btn = self._secondary_btn("浏览")
        browse_btn.clicked.connect(self._on_browse)
        path_row.addWidget(browse_btn)
        self.compress_cb = QCheckBox("压缩")
        self.compress_cb.setChecked(False)
        path_row.addWidget(self.compress_cb)
        layout.addLayout(path_row)

        # ── Action Row ──
        action_row = QHBoxLayout()
        action_row.setSpacing(6)

        dur_label = QLabel("抓取时长")
        dur_label.setStyleSheet("font-size:12px;color:#1a1a1a;")
        action_row.addWidget(dur_label)
        self.duration_input = QLineEdit("30")
        self.duration_input.setFixedWidth(50)
        self.duration_input.setAlignment(Qt.AlignCenter)
        self.duration_input.textChanged.connect(self._gen_cmd)
        action_row.addWidget(self.duration_input)
        self.duration_unit = QComboBox()
        self.duration_unit.addItems(["秒", "分"])
        self.duration_unit.setFixedWidth(70)
        self.duration_unit.currentTextChanged.connect(self._gen_cmd)
        action_row.addWidget(self.duration_unit)

        action_row.addStretch()

        self.start_btn = QPushButton("开始抓包并下载")
        self.start_btn.setStyleSheet(
            "QPushButton{background:#ff6900;color:white;border:none;"
            "border-radius:8px;padding:8px 24px;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:#e55e00;}"
            "QPushButton:disabled{background:#f5d5c0;color:white;}")
        self.start_btn.clicked.connect(self._do_start)
        action_row.addWidget(self.start_btn)

        layout.addLayout(action_row)

        # ── Progress ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(6)
        layout.addWidget(self.progress_bar)

        # ── Log ──
        layout.addWidget(QLabel("日志"))
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFont(QFont("Menlo", 10) if "Menlo" in QFont().families() else QFont("Consolas", 10))
        self.log_box.setFixedHeight(150)
        self.log_box.setStyleSheet(
            "QTextEdit{background:#fafafa;border:1px solid #e0e0e0;border-radius:8px;"
            "padding:8px;font-size:11px;color:#1a1a1a;}")

        log_container = QFrame()
        log_container.setObjectName("card")
        log_container.setStyleSheet("QFrame#card{border:none; border-radius:12px; background:white;}")
        log_v = QVBoxLayout(log_container)
        log_v.setContentsMargins(12, 10, 12, 12)
        log_v.addWidget(self.log_box)
        layout.addWidget(log_container)

        self._gen_cmd()

    def _gen_cmd(self):
        parts = []
        proto = self.capture_proto.currentText()
        if proto != "any":
            parts.append(proto)
        src = self.capture_src_ip.text().strip()
        dst = self.capture_dst_ip.text().strip()
        src_port = self.capture_src_port.text().strip()
        dst_port = self.capture_dst_port.text().strip()

        if self.direction_cb.isChecked():
            if src:
                parts.append(f"src {src}")
            if dst:
                parts.append(f"dst {dst}")
            if src_port:
                parts.append(f"src port {src_port}")
            if dst_port:
                parts.append(f"dst port {dst_port}")
        else:
            if src:
                parts.append(f"host {src}")
            if dst:
                parts.append(f"host {dst}")
            if src_port:
                parts.append(f"port {src_port}")
            if dst_port:
                parts.append(f"port {dst_port}")
        self._capture_filter = " and ".join(parts) if parts else ""
        idx = self.host_cb.currentIndex()
        prefix = self._hosts[idx].get("name", "capture") if 0 <= idx < len(self._hosts) else "capture"
        out_name = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M')}.pcap"
        self._capture_out = out_name
        cmd = f"tcpdump -i any -w /opt/tar/{out_name} {self._capture_filter}"
        self.cmd_preview.setText(cmd)

    def _on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if d:
            self.save_path.setText(d)

    def _do_start(self):
        idx = self.host_cb.currentIndex()
        if idx < 0 or idx >= len(self._hosts):
            QMessageBox.warning(self, "提示", "请选择目标主机")
            return
        h = self._hosts[idx]
        save_dir = self.save_path.text().strip()
        if not save_dir:
            QMessageBox.warning(self, "提示", "请选择本地保存目录")
            return
        self._gen_cmd()
        raw_dur = self.duration_input.text().strip() or "30"
        unit = self.duration_unit.currentText()
        duration = str(int(raw_dur) * 60) if unit == "分" else raw_dur
        out_name = self._capture_out
        remote_path = f"/opt/tar/{out_name}"
        local_path = os.path.join(save_dir, out_name)
        filter_expr = self._capture_filter
        cmd = f"tcpdump -i any -w {remote_path} {filter_expr}"

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.log_box.append(f"[{datetime.now().strftime('%H:%M:%S')}] [开始] 连接 {h['host']}:{h.get('port',22)}")
        self.log_box.append(f"[{datetime.now().strftime('%H:%M:%S')}] [命令] {cmd}")
        self.log_box.append(f"[{datetime.now().strftime('%H:%M:%S')}] [远程] {remote_path}")
        self.log_box.append(f"[{datetime.now().strftime('%H:%M:%S')}] [本地] {local_path}")
        self.start_btn.setEnabled(False)

        self._worker = CaptureWorker(h["host"], h.get("port", 22), h["user"], h.get("pwd", ""),
                                      cmd, remote_path, local_path, duration, self.compress_cb.isChecked())
        self._worker.log.connect(self._on_log)
        self._worker.progress.connect(self.progress_bar.setValue)
        self._worker.done.connect(self._on_done)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.append(f"[{ts}] {msg}")

    def _on_done(self, local_path):
        self.progress_bar.setValue(100)
        self.status_lbl.setText("完成")
        self.status_lbl.setStyleSheet("color:#81c784;font-weight:bold;")
        QMessageBox.information(self, "完成", f"抓包文件已保存:\n{local_path}")

    def _on_worker_finished(self):
        self.start_btn.setEnabled(True)
        self._worker = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = QApplication(sys.argv)
    w = TcpdumpCapture()
    w.show()
    sys.exit(app.exec())

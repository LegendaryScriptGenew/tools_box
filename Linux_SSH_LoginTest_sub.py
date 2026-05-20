# -*- coding: utf-8 -*-
"""
SSH 登录测试工具 (PySide6)
"""
import sys, os, logging, threading
from datetime import datetime

import paramiko
import pandas as pd

from PySide6.QtWidgets import (QWidget, QApplication, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QFileDialog, QMessageBox)
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont

logger = logging.getLogger(__name__)


class SSHTester(QWidget):
    def __init__(self):
        super().__init__()
        self._init_logging()
        self.setWindowTitle("SSH 登录测试")
        self.resize(800, 600)
        self.setStyleSheet("""
            SSHTester { background:#f5f6fa; }
            QLineEdit { border:none; border-bottom:1px solid #dfe6e9; padding:6px 4px; background:transparent; font-size:13px; }
            QLineEdit:focus { border-bottom:2px solid #0984e3; }
            QTextEdit { border:1px solid #dfe6e9; border-radius:4px; background:white; font-size:13px; }
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 20, 24, 20)

        title = QLabel("SSH 登录测试")
        title.setStyleSheet("font-size:18px;font-weight:bold;color:#2d3436;")
        layout.addWidget(title)

        l1 = QHBoxLayout()
        l1.addWidget(QLabel("Excel 文件"))
        self.fe = QLineEdit()
        self.fe.setPlaceholderText("选择包含 host,port,username,password 的Excel文件")
        l1.addWidget(self.fe)
        btn = QPushButton("浏览")
        btn.setStyleSheet("QPushButton{background:#0984e3;color:white;padding:7px 20px;border:none;border-radius:4px;font-size:13px;}QPushButton:hover{background:#0873c4;}")
        btn.clicked.connect(lambda: self.fe.setText(
            QFileDialog.getOpenFileName(self, "选择Excel","","Excel (*.xlsx)")[0]))
        l1.addWidget(btn)
        layout.addLayout(l1)

        l2 = QHBoxLayout()
        l2.addWidget(QLabel("固定账号测试（选填，留空则用Excel每行账号）"))
        l2.addStretch()
        layout.addLayout(l2)

        l3 = QHBoxLayout()
        l3.addWidget(QLabel("用户名"))
        self.fixed_user = QLineEdit()
        self.fixed_user.setPlaceholderText("留空用Excel中的账号")
        l3.addWidget(self.fixed_user)
        l3.addWidget(QLabel("密码"))
        self.fixed_pwd = QLineEdit()
        self.fixed_pwd.setEchoMode(QLineEdit.Password)
        self.fixed_pwd.setPlaceholderText("留空用Excel中的密码")
        l3.addWidget(self.fixed_pwd)
        l3.addStretch()
        layout.addLayout(l3)

        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("开始测试")
        self.run_btn.setStyleSheet("QPushButton{background:#27ae60;color:white;font-weight:bold;padding:10px 36px;border:none;border-radius:4px;font-size:14px;}QPushButton:hover{background:#219a52;}QPushButton:disabled{background:#b2bec3;}")
        self.run_btn.clicked.connect(self._start)
        btn_row.addWidget(self.run_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 10))
        layout.addWidget(self.log)

    def _init_logging(self):
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(
            os.path.join(log_dir, f"SSH登录测试_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
            encoding="utf-8")
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(fh)
        logger.setLevel(logging.INFO)

    def _log(self, msg):
        logger.info(msg)
        self.log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        self.log.ensureCursorVisible()

    def _start(self):
        fp = self.fe.text()
        if not fp:
            QMessageBox.warning(self, "提示", "请选择Excel文件")
            return
        self.run_btn.setEnabled(False)
        self.run_btn.setText("测试中...")
        self.log.clear()
        self._log("开始SSH登录测试...")
        threading.Thread(target=self._run, args=(fp,), daemon=True).start()

    def _run(self, fp):
        try:
            df = pd.read_excel(fp)
            fixed_user = self.fixed_user.text().strip()
            fixed_pwd = self.fixed_pwd.text().strip()
            ok, fail = 0, 0
            for _, row in df.iterrows():
                host = str(row.iloc[0])
                port = int(row.iloc[1]) if len(row) > 1 and pd.notna(row.iloc[1]) else 22
                user = fixed_user or str(row.iloc[2]) if len(row) > 2 else ""
                pwd = fixed_pwd or str(row.iloc[3]) if len(row) > 3 else ""
                try:
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(host, port=port, username=user, password=pwd, timeout=10)
                    ssh.close()
                    self._log(f"✅ {host} 登录成功")
                    ok += 1
                except paramiko.AuthenticationException:
                    self._log(f"❌ {host} 密码错误")
                    fail += 1
                except Exception as e:
                    self._log(f"❌ {host} 连接失败: {e}")
                    fail += 1
            self._log(f"完成！成功 {ok}，失败 {fail}")
        except Exception as e:
            self._log(f"错误: {e}")
        finally:
            QTimer.singleShot(0, self._done)

    def _done(self):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("开始测试")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
    app = QApplication(sys.argv)
    w = SSHTester()
    w.show()
    sys.exit(app.exec())

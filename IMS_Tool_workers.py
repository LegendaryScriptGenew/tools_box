# -*- coding: utf-8 -*-
"""Background workers for IMS_Tool_sub.

This module intentionally contains no QWidget UI code. Keeping SSH/SCP
workers here makes the integrated IMS tool easier to extend without
touching the tab layout and interaction state in IMS_Tool_sub.py.
"""
import os
import time
import logging
import difflib
import posixpath
import re
from datetime import datetime
from collections import defaultdict

import paramiko
from scp import SCPClient
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


def _shell_quote(value):
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


# ═══════════════════════════════════════════════
#  Capture Workers
# ═══════════════════════════════════════════════

class KillWorker(QThread):
    def __init__(self, host, port, user, pwd, out_names):
        super().__init__()
        self.host = host; self.port = int(port); self.user = user; self.pwd = pwd; self.out_names = out_names
    def run(self):
        try:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(self.host, self.port, self.user, self.pwd, timeout=5)
            for name in self.out_names:
                c.exec_command(f"pkill -f {_shell_quote('tcpdump .*' + name)} 2>/dev/null || true")
            c.exec_command("sleep 1")
            c.close()
        except: pass

class CaptureWorker(QThread):
    log = Signal(str); progress = Signal(int); done = Signal(str); error = Signal(str)
    def __init__(self, host, port, user, pwd, cmd, remote_path, local_path, duration, compress=True):
        super().__init__()
        self.host = host; self.port = int(port); self.user = user; self.pwd = pwd
        self.cmd = cmd; self.remote_path = remote_path; self.local_path = local_path
        self.duration = int(duration); self.compress = compress
    def _ssh(self):
        c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(self.host, self.port, self.user, self.pwd, timeout=10); return c
    def run(self):
        try:
            self.log.emit(f"[tcpdump] Execute: {self.cmd}")
            self._ssh_exec("mkdir -p /opt/tar")
            self._ssh_nohup(f"nohup {self.cmd} > /dev/null 2>&1 &")
            self.log.emit(f"[tcpdump] Capturing {self.duration}s ...")
            for i in range(self.duration):
                time.sleep(1); self.progress.emit(int((i+1)*100/self.duration))
            self.log.emit("[tcpdump] Stopping capture")
            self._ssh_exec(f"pkill -f {_shell_quote('tcpdump .* -w ' + self.remote_path)} 2>/dev/null || true; sleep 2")
            out, _ = self._ssh_exec(f"test -f {self.remote_path} && echo OK || echo MISSING")
            if out != "OK":
                raise RuntimeError(f"File not found: {self.remote_path}")
            dl_path, dl_local = self.remote_path, self.local_path
            if self.compress:
                self.log.emit(f"[compress] gzip {self.remote_path}")
                self._ssh_exec(f"gzip -f {self.remote_path}", timeout=120)
                dl_path, dl_local = self.remote_path+".gz", self.local_path+".gz"
            self.log.emit(f"[download] {dl_path} -> {dl_local}")
            c = self._ssh(); SCPClient(c.get_transport()).get(dl_path, dl_local); c.close()
            self._ssh_nohup(f"rm -f {dl_path}")
            self.log.emit(f"[done] {dl_local}"); self.done.emit(dl_local)
        except Exception as e:
            self.log.emit(f"[error] {str(e)}"); self.error.emit(str(e)); logger.exception("CaptureWorker error")
    def _ssh_exec(self, cmd, timeout=10):
        c = self._ssh(); chan = c.get_transport().open_session(); chan.settimeout(timeout)
        chan.exec_command(cmd); out = chan.makefile("rb",-1).read(); err = chan.makefile_stderr("rb",-1).read().decode("utf-8",errors="replace").strip()
        c.close()
        for enc in ("utf-8","gbk"):
            try: return out.decode(enc).strip(), err
            except: continue
        return out.decode("utf-8",errors="replace").strip(), err
    def _ssh_nohup(self, cmd):
        c = self._ssh(); chan = c.get_transport().open_session(); chan.exec_command(cmd); chan.close(); c.close()

class CaptureStartWorker(QThread):
    started = Signal(); error = Signal(str)
    def __init__(self, host, port, user, pwd, cmd):
        super().__init__()
        self.host = host; self.port = int(port); self.user = user; self.pwd = pwd; self.cmd = cmd
    def _ssh(self):
        c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(self.host, self.port, self.user, self.pwd, timeout=10); return c
    def run(self):
        try:
            c = self._ssh(); chan = c.get_transport().open_session()
            chan.exec_command(f"mkdir -p /opt/tar && nohup {self.cmd} > /dev/null 2>&1 &")
            chan.close(); c.close(); self.started.emit()
        except Exception as e: self.error.emit(str(e))

class CaptureStopWorker(QThread):
    log = Signal(str); done = Signal(str); error = Signal(str)
    def __init__(self, host, port, user, pwd, remote_path, local_path, compress=True):
        super().__init__()
        self.host = host; self.port = int(port); self.user = user; self.pwd = pwd
        self.remote_path = remote_path; self.local_path = local_path; self.compress = compress
    def _ssh(self):
        c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(self.host, self.port, self.user, self.pwd, timeout=10); return c
    def run(self):
        try:
            self.log.emit("[tcpdump] Stopping capture")
            c = self._ssh(); chan = c.get_transport().open_session()
            chan.exec_command(f"pkill -f {_shell_quote('tcpdump .* -w ' + self.remote_path)} 2>/dev/null || true; sleep 2")
            chan.close(); c.close()
            c = self._ssh(); chan = c.get_transport().open_session()
            chan.exec_command(f"test -f {self.remote_path} && echo OK || echo MISSING")
            out = chan.makefile("rb",-1).read().decode().strip(); chan.close(); c.close()
            if out != "OK": raise RuntimeError(f"File not found: {self.remote_path}")
            dl_path, dl_local = self.remote_path, self.local_path
            if self.compress:
                self.log.emit(f"[compress] gzip {self.remote_path}")
                c = self._ssh(); chan = c.get_transport().open_session()
                chan.exec_command(f"gzip -f {self.remote_path}"); chan.close(); c.close()
                dl_path, dl_local = self.remote_path+".gz", self.local_path+".gz"
            self.log.emit(f"[download] {dl_path} -> {dl_local}")
            c = self._ssh(); SCPClient(c.get_transport()).get(dl_path, dl_local); c.close()
            c = self._ssh(); chan = c.get_transport().open_session()
            chan.exec_command(f"rm -f {dl_path}"); chan.close(); c.close()
            self.log.emit(f"[done] {dl_local}"); self.done.emit(dl_local)
        except Exception as e:
            self.log.emit(f"[error] {str(e)}"); self.error.emit(str(e))

class SBCMCaptureWorker(QThread):
    log = Signal(str); progress = Signal(int); done = Signal(str); error = Signal(str)
    def __init__(self, host, port, user, pwd, local_path, duration=None):
        super().__init__()
        self.host = host; self.port = int(port); self.user = user; self.pwd = pwd
        self.local_path = local_path; self.duration = int(duration) if duration is not None else None
        self._stop_requested = False; self._chan = None; self._ssh = None
    def request_stop(self): self._stop_requested = True
    def _recv_all(self, chan):
        data = b""
        while chan.recv_ready():
            chunk = chan.recv(4096)
            if not chunk: break
            data += chunk
        if data: self.log.emit(f"[SBCM] [recv] {data.decode('utf-8',errors='replace')[-500:]}")
        return data
    def _expect(self, chan, expected, timeout=30):
        data = b""; start = time.time()
        while time.time()-start < timeout:
            if chan.recv_ready():
                chunk = chan.recv(4096)
                if not chunk: break
                data += chunk
                txt = data.decode("utf-8",errors="replace")
                if expected in txt:
                    self.log.emit(f"[SBCM] [expect] '{expected}' matched")
                    return txt
            elif chan.exit_status_ready(): break
            else: time.sleep(0.1)
        txt = data.decode("utf-8",errors="replace")
        self.log.emit(f"[SBCM] [expect] '{expected}' timeout, recv={txt[-300:]}")
        raise TimeoutError(f"Expected '{expected}' not found. Got: {txt[-300:]}")
    def _send_cmd(self, chan, text, label=None):
        label = label or text.rstrip("\n")
        self.log.emit(f"[SBCM] [send] {label}"); chan.send(text)
    def _telnet_and_diagnose(self, chan):
        self._recv_all(chan)
        self._send_cmd(chan, "telnet 127.0.0.1\n")
        self._expect(chan, "[USERNAME]:"); self._send_cmd(chan, "admin\n", "admin (username)")
        self._expect(chan, "[PASSWORD]:"); self._send_cmd(chan, "admin\n", "admin (password)")
        self._expect(chan, "NuBiz>>"); self._send_cmd(chan, "cm diagnose\n"); self._expect(chan, "NuBiz$$")
    def _stop_capture(self, chan):
        self._send_cmd(chan, "debug dp 0x912\n")
        self._expect(chan, "<para1>"); self._send_cmd(chan, "\n","enter para1")
        self._expect(chan, "<para2>"); self._send_cmd(chan, "\n","enter para2")
        self._expect(chan, "<para3>"); self._send_cmd(chan, "\n","enter para3")
        self._expect(chan, "<para4>"); self._send_cmd(chan, "\n","enter para4")
        self._expect(chan, "End of Packet Capture", timeout=120)
    def run(self):
        try:
            self.log.emit(f"[SBCM] Connecting {self.host}:{self.port}")
            c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(self.host, self.port, self.user, self.pwd, timeout=10); self._ssh = c
            chan = c.invoke_shell(); chan.settimeout(30); self._chan = chan
            time.sleep(1); self._recv_all(chan)
            self._telnet_and_diagnose(chan)
            self._send_cmd(chan, "debug dp 0x911\n")
            self._expect(chan, "<para1>"); self._send_cmd(chan, "\n","enter para1")
            self._expect(chan, "<para2>"); self._send_cmd(chan, "\n","enter para2")
            self._expect(chan, "<para3>"); self._send_cmd(chan, "\n","enter para3")
            self._expect(chan, "<para4>"); self._send_cmd(chan, "\n","enter para4")
            self.log.emit("[SBCM] Capture started")
            self._capture_start = time.time(); last_keepalive = time.time()
            while True:
                if self.duration is not None:
                    elapsed = int(time.time()-self._capture_start)
                    if elapsed >= self.duration: break
                    self.progress.emit(int(elapsed*100/self.duration))
                else:
                    self.progress.emit(50)
                    if self._stop_requested: break
                if not chan.active: raise RuntimeError("SSH channel closed")
                if chan.recv_ready(): chan.recv(4096)
                if time.time()-last_keepalive >= 30:
                    chan.send("\n"); last_keepalive = time.time()
                    self.log.emit("[SBCM] keepalive sent")
                time.sleep(1)
            for attempt in range(2):
                try: self._stop_capture(chan); break
                except Exception:
                    if attempt == 0:
                        self.log.emit("[SBCM] Session lost, re-telnet...")
                        self._telnet_and_diagnose(chan)
                    else: raise
            self.log.emit("[SBCM] Capture stopped, waiting for flush..."); time.sleep(3)
            self._send_cmd(chan, "exit\n"); self._expect(chan, "Y/N")
            self._send_cmd(chan, "Y\n", "exit confirm Y"); time.sleep(2)
            self._recv_all(chan); chan.close(); c.close()
            self.log.emit("[SBCM] Locating pdump folder")
            c2 = paramiko.SSHClient(); c2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c2.connect(self.host, self.port, self.user, self.pwd, timeout=10)
            transport = c2.get_transport()
            remote_folder = ""
            for retry in range(10):
                s = transport.open_session(); s.exec_command("ls -td /mnt/hfs1/PROGRAM/pdump/pdump* 2>/dev/null | head -1")
                out = s.makefile("rb",-1).read(); s.close()
                remote_folder = out.decode("utf-8",errors="replace").strip()
                if remote_folder:
                    sc = transport.open_session(); sc.exec_command(f"ls -A {remote_folder} 2>/dev/null | head -5")
                    oc = sc.makefile("rb",-1).read().decode("utf-8",errors="replace").strip(); sc.close()
                    if oc: break
                time.sleep(2)
            if not remote_folder: raise RuntimeError("SBCM: pdump folder not found!")
            folder_name = remote_folder.rstrip("/").split("/")[-1]
            remote_tar = f"/opt/tar/{folder_name}.tar.gz"
            self.log.emit(f"[SBCM] Packing {remote_folder}")
            s2 = transport.open_session(); s2.exec_command(f"tar czf {remote_tar} -C /mnt/hfs1/PROGRAM/pdump {folder_name} && stat --format=%s {remote_tar}")
            o2 = s2.makefile("rb",-1).read().decode("utf-8",errors="replace").strip(); s2.close()
            if not o2 or o2=="0": raise RuntimeError(f"SBCM: packed file empty! ({remote_tar})")
            self.log.emit(f"[SBCM] Packed size: {o2} bytes")
            lp = self.local_path+".tar.gz"
            self.log.emit(f"[SBCM] Downloading {remote_tar} -> {lp}")
            SCPClient(c2.get_transport()).get(remote_tar, lp)
            s3 = transport.open_session(); s3.exec_command(f"rm -f {remote_tar}"); s3.close(); c2.close()
            self.progress.emit(100); self.log.emit(f"[done] {lp}"); self.done.emit(lp)
        except Exception as e:
            emsg = str(e); self.log.emit(f"[error] {emsg}"); self.error.emit(emsg); logger.exception("SBCMCaptureWorker error")
        finally:
            if self._chan:
                try: self._chan.close()
                except: pass
            if self._ssh:
                try: self._ssh.close()
                except: pass

# ═══════════════════════════════════════════════
#  Upgrade Workers (from IMS_NE_Upgrade)
# ═══════════════════════════════════════════════

class SSHWorker(QThread):
    log_signal = Signal(str); step_signal = Signal(str); finished_signal = Signal(str)
    config_diff_signal = Signal(list); kill_residual_signal = Signal(list, str)

    def __init__(self, host, port, username, password, ne_config, patch_local, parent=None):
        super().__init__(parent)
        self.host = host; self.port = port; self.username = username; self.password = password
        self.ne = ne_config; self.patch_local = patch_local
        self.sftp = None; self.ssh = None; self._stopped = False; self._kill_continue = None

    def stop(self): self._stopped = True
    def _log(self, msg): self.log_signal.emit(msg)
    def _step(self, msg): self.step_signal.emit(msg)
    def set_config_diff_result(self, r): self._config_diff_result = r
    def set_kill_decision(self, d): self._kill_continue = d

    def _exec(self, cmd, timeout=60, user=None, input_str=None):
        full_cmd = cmd
        if user and user != self.username:
            escaped = cmd.replace("'","'\"'\"'")
            full_cmd = f"su - {user} -c '{escaped}'"
        self._log(f"> {full_cmd}")
        stdin, stdout, stderr = self.ssh.exec_command(full_cmd, timeout=timeout)
        if input_str:
            self._log(f"  <<< send input: {input_str.strip()}")
            stdin.write(input_str); stdin.flush(); stdin.channel.shutdown_write()
        return self._read_result(stdin, stdout, stderr, timeout)

    def _exec_bg(self, cmd, user=None):
        full_cmd = cmd
        if user and user != self.username:
            escaped = cmd.replace("'","'\"'\"'")
            full_cmd = f"su - {user} -c '{escaped}'"
        self._log(f"> {full_cmd}")
        stdin, stdout, stderr = self.ssh.exec_command(full_cmd, timeout=5)
        stdin.close(); time.sleep(1)
        try: stdout.channel.close()
        except: pass
        try: stderr.channel.close()
        except: pass
        return 0, "", ""

    def _read_result(self, stdin, stdout, stderr, timeout):
        try: out = stdout.read().decode("utf-8",errors="replace").strip()
        except: out = ""
        try: err = stderr.read().decode("utf-8",errors="replace").strip()
        except: err = ""
        try: rc = stdout.channel.recv_exit_status()
        except: rc = -1
        if out:
            for l in out.split("\n"): self._log(f"  {l}")
        if err:
            for l in err.split("\n"): self._log(f"  ! {l}")
        return rc, out, err

    def _exec_script_user(self, user, commands, inputs=None):
        self._log(f"> [{user}] exec {len(commands)} commands")
        cwd = None
        for i, cmd in enumerate(commands):
            if self._stopped: return
            if cmd.startswith("WAIT"):
                sec = int(cmd.split()[1])
                for s in range(sec,0,-1):
                    if self._stopped: return
                    time.sleep(1)
                continue
            if cmd.startswith("cd "):
                cwd = cmd[3:].strip().strip('"').strip("'"); continue
            actual_cmd = cmd
            if cwd: actual_cmd = f"cd {cwd} && {cmd}"
            inp = inputs.get(str(i)) if inputs else None
            if actual_cmd.strip().endswith("&"):
                bg = actual_cmd.rstrip()[:-1].rstrip()+" </dev/null >>nohup.out 2>&1 &"
                rc, out, err = self._exec_bg(bg, user=user)
            else:
                rc, out, err = self._exec(actual_cmd, user=user, input_str=inp)
            if rc != 0: self._log(f"  ⚠ rc={rc}")
            else: self._log(f"  ✓ rc={rc}")

    def _check_remote_path(self, path, path_type="path"):
        qpath = _shell_quote(path)
        rc, out, _ = self._exec(f"ls -d {qpath} 2>/dev/null && echo EXISTS || echo NOTEXISTS")
        if out.strip().splitlines()[-1:] == ["EXISTS"]:
            self._exec(f"stat --format='%F size:%s bytes modified:%y' {qpath} 2>/dev/null || file {qpath}")
            return True
        self._log(f"  [warn] {path_type} not found: {path}")
        return False

    def _fmt_size(self, b):
        for u in ("B","KB","MB","GB"):
            if b<1024: return f"{b:.1f}{u}"
            b/=1024
        return f"{b:.1f}TB"

    def run(self):
        try:
            self._log("═"*50); self._log(f"IMS NE Upgrade Start"); self._log(f"Target: {self.host}:{self.port}")
            self._log(f"NE: {self.ne.get('description','?')}"); self._log(f"Patch: {os.path.basename(self.patch_local)} ({self._fmt_size(os.path.getsize(self.patch_local))})")
            self._log("═"*50)
            self._log(f"Connecting {self.host}:{self.port} ...")
            self.ssh = paramiko.SSHClient(); self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(self.host, port=self.port, username=self.username, password=self.password, timeout=15)
            self._log("✓ SSH connected"); self.sftp = self.ssh.open_sftp(); self._log("✓ SFTP opened")
            steps = [("stop",self._do_stop),("backup",self._do_backup),("upload",self._do_upload),
                     ("extract",self._do_extract),("post_extract",self._do_post_extract),
                     ("config_diff",self._do_config_diff),("chown",self._do_chown),
                     ("license",self._do_license),("start",self._do_start)]
            for sk, sf in steps:
                if self._stopped: break
                self._step(sk); sf(); self._log("")
            if not self._stopped:
                self._log("═"*50); self._log("✓ All steps completed"); self._log("═"*50)
                self.finished_signal.emit("success")
            else: self.finished_signal.emit("stopped")
        except paramiko.AuthenticationException:
            self._log("✗ Auth failed"); self.finished_signal.emit("error")
        except Exception as e:
            self._log(f"✗ {e}"); import traceback; self._log(traceback.format_exc()); self.finished_signal.emit("error")
        finally:
            if self.sftp: self.sftp.close()
            if self.ssh: self.ssh.close()

    def _do_stop(self):
        cfg = self.ne["stop"]; self._log("━━━ Step 1/9: Stop ━━━")
        if not cfg.get("commands") and not cfg.get("process_names"): return
        if cfg["method"]=="script":
            self._exec_script_user(cfg["user"], cfg["commands"], inputs=cfg.get("inputs"))
        elif cfg["method"]=="kill":
            for pname in cfg.get("process_names",[]):
                if self._stopped: return
                rc, out, _ = self._exec(f"ps -ef | grep '{pname}' | grep -v grep")
                lines = [l.strip() for l in out.split("\n") if l.strip()]
                if not lines: continue
                pids = []
                for l in lines:
                    parts = l.split(); pid = parts[1] if len(parts)>=2 else "?"
                    if pid!="?": pids.append(pid)
                if pids:
                    self._exec(f"kill -9 {' '.join(pids)}"); time.sleep(2)
                    rc2, out2, _ = self._exec(f"ps -ef | grep '{pname}' | grep -v grep")
                    remaining = [l.strip() for l in out2.split("\n") if l.strip()]
                    if remaining:
                        self.kill_residual_signal.emit(remaining, pname)
                        self._kill_continue = None
                        while self._kill_continue is None and not self._stopped: time.sleep(0.1)
                        if self._stopped or not self._kill_continue: return
        self._log("✓ Step 1 done")

    def _do_backup(self):
        cfg = self.ne["backup"]; self._log("━━━ Step 2/9: Backup ━━━")
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S"); self._backup_map = {}
        for item in cfg["items"]:
            if self._stopped: return
            src = posixpath.join(cfg["base_dir"], item["source"])
            backup_name = item["source"]+item["backup_suffix"].replace("{date}",date_str)
            dst = posixpath.join(cfg["base_dir"], backup_name)
            bu = item.get("user") or cfg["user"]
            self._log(f"  backup {src} -> {dst} as {bu}")
            if not self._check_remote_path(src, "backup source"):
                continue
            rc, _, _ = self._exec(f"cp -r {_shell_quote(src)} {_shell_quote(dst)}", timeout=120, user=bu)
            if rc==0 and item.get("remove_source",False):
                self._exec(f"rm -rf {_shell_quote(src)}", timeout=60, user="root")
            if rc == 0:
                self._check_remote_path(dst, "backup target")
            self._backup_map[item["source"]] = {"src":src,"dst":dst}
        self._log("✓ Step 2 done")

    def _do_upload(self):
        self._log("━━━ Step 3/9: Upload ━━━")
        tar_path = self.ne["patch"].get("tar_path","/opt/tar")
        remote_name = os.path.basename(self.patch_local)
        remote_path = posixpath.join(tar_path, remote_name)
        self._exec(f"mkdir -p {_shell_quote(tar_path)}")
        self._log("Uploading..."); start = time.time()
        last_pct = [0]
        def progress(t, total):
            if self._stopped: raise Exception("stopped")
            if total:
                pct = int(t / total * 100)
                if pct >= last_pct[0] + 10 or pct == 100:
                    elapsed = max(time.time() - start, 0.001)
                    self._log(f"  [{pct:3d}%] {self._fmt_size(t)}/{self._fmt_size(total)} {t / elapsed / 1024:.0f}KB/s")
                    last_pct[0] = pct
        self.sftp.put(self.patch_local, remote_path, callback=progress)
        self._uploaded_path = remote_path
        self._check_remote_path(remote_path, "uploaded patch")
        self._log(f"✓ Upload done ({time.time()-start:.1f}s)"); self._log("✓ Step 3 done")

    def _do_extract(self):
        self._log("━━━ Step 4/9: Extract ━━━")
        user = self.ne["patch"]["extract_user"]
        self._check_remote_path(self._uploaded_path, "uploaded patch")
        rc, _, _ = self._exec(f"cd /opt/tar && tar -xzf {_shell_quote(self._uploaded_path)} -C /", timeout=180, user=user)
        if rc!=0: raise RuntimeError(f"tar extract failed rc={rc}")
        self._log("✓ Step 4 done")

    def _do_post_extract(self):
        cfg = self.ne.get("post_extract"); self._log("━━━ Step 5/9: Post Extract ━━━")
        if not cfg: self._log("  skip"); return
        self._exec_script_user(cfg["user"], cfg["commands"]); self._log("✓ Step 5 done")

    def _do_config_diff(self):
        self._log("━━━ Step 6/9: Config Diff ━━━")
        config_files = self.ne.get("config_files",[])
        if not config_files: return
        base_dir = self.ne["backup"]["base_dir"]; file_items = []
        for cf in config_files:
            if self._stopped: return
            old_path = ""
            for sk, info in self._backup_map.items():
                sp = posixpath.join(base_dir, sk)
                if cf==sp or cf.startswith(sp+"/"): old_path = cf.replace(sp, info["dst"], 1); break
            if not old_path: continue
            old_found = True; new_found = True
            try:
                with self.sftp.open(old_path,"r") as f: old_c = f.read().decode("utf-8",errors="replace")
            except:
                old_c = ""; old_found = False
            try:
                with self.sftp.open(cf,"r") as f: new_c = f.read().decode("utf-8",errors="replace")
            except:
                new_c = ""; new_found = False
            if not new_found:
                self._log(f"  [warn] new config not found, skip: {cf}")
                continue
            if not old_found:
                self._log(f"  [warn] backup config not found, keep new config: {old_path}")
                file_items.append({"path":cf,"old_content":"","new_content":new_c,"hunks":[],"skip_diff":True})
                continue
            ol = old_c.splitlines(True); nl = new_c.splitlines(True)
            diff = list(difflib.unified_diff(ol, nl, fromfile="old", tofile="new", lineterm=""))
            hunks = self._parse_hunks(diff)
            file_items.append({"path":cf,"old_content":old_c,"new_content":new_c,"hunks":hunks})
        if not file_items: return
        skip_items = [it for it in file_items if it.get("skip_diff")]
        diff_items = [it for it in file_items if not it.get("skip_diff")]
        results = [{"path": it["path"], "merged_content": it["new_content"]} for it in skip_items]
        if diff_items:
            self.config_diff_signal.emit(diff_items)
            self._config_diff_result = None
            while self._config_diff_result is None and not self._stopped: time.sleep(0.1)
            if self._config_diff_result:
                results.extend(self._config_diff_result)
        if self._stopped: return
        for r in results:
            try:
                with self.sftp.open(r["path"],"w") as f: f.write(r["merged_content"])
                self._log(f"  wrote merged config: {r['path']}")
            except Exception as e:
                self._log(f"  [warn] failed writing {r['path']}: {e}")
        self._log("✓ Step 6 done")

    def _parse_hunks(self, diff_lines):
        hunks = []; cur = None
        for line in diff_lines:
            if line.startswith("@@"):
                if cur: hunks.append(cur)
                m = re.match(r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@(.*)', line)
                cur = {"old_start":int(m.group(1)) if m else 0,"new_start":int(m.group(3)) if m else 0,
                       "section":m.group(5).strip() if m and m.group(5) else "","old_lines":[],"new_lines":[],"lines":[]}
            elif line.startswith("---") or line.startswith("+++"): continue
            elif cur:
                cur["lines"].append(line)
                if line.startswith("-"): cur["old_lines"].append(line[1:])
                elif line.startswith("+"): cur["new_lines"].append(line[1:])
                else: cur["old_lines"].append(line[1:]); cur["new_lines"].append(line[1:])
        if cur: hunks.append(cur)
        return hunks

    def _do_chown(self):
        cfg = self.ne.get("chown"); self._log("━━━ Step 7/9: Chown ━━━")
        if not cfg: return
        for p in cfg["paths"]:
            if self._stopped: return
            rc, _, _ = self._exec(f"chown {cfg['user']}:{cfg['group']} {_shell_quote(p)}", user="root")
            if rc == 0:
                self._exec(f"ls -l {_shell_quote(p)} | awk '{{print $3\":\"$4}}'")
        self._log("✓ Step 7 done")

    def _do_license(self):
        cfg = self.ne.get("license",{}); self._log("━━━ Step 8/9: License ━━━")
        if not cfg.get("has_license"): return
        lp = cfg["file_path"]; base_dir = self.ne["backup"]["base_dir"]; old_lp = ""
        for sk, info in self._backup_map.items():
            sp = posixpath.join(base_dir, sk)
            if lp.startswith(sp+"/") or lp==sp: old_lp = lp.replace(sp, info["dst"], 1); break
        self._check_remote_path(lp, "license")
        if old_lp and self._check_remote_path(old_lp, "backup license"):
            self._exec(f"cp {_shell_quote(old_lp)} {_shell_quote(lp)}", user=cfg.get("user","root"))
            self._check_remote_path(lp, "restored license")
        elif not old_lp:
            self._log("  [warn] no matching license path found in backup map")
        self._log("✓ Step 8 done")

    def _do_start(self):
        cfg = self.ne["start"]; self._log("━━━ Step 9/9: Start ━━━")
        self._exec_script_user(cfg["user"], cfg["commands"])
        ck = cfg.get("check")
        if ck:
            time.sleep(ck.get("wait",0))
            rc, out, _ = self._exec(ck["command"], user=cfg["user"])
            if rc==0 and ck.get("expected","") in out: self._log("✓ Start OK")
            else: self._log("⚠ Start check failed")
        self._log("✓ Step 9 done")

# ═══════════════════════════════════════════════
#  Log Viewer Workers (from LogViewer)
# ═══════════════════════════════════════════════

class LogListWorker(QThread):
    log = Signal(str); file_info = Signal(dict); finished = Signal(); error = Signal(str)
    def __init__(self, host, port, user, pwd, ne_config):
        super().__init__()
        self.host = host; self.port = int(port); self.user = user; self.pwd = pwd; self.ne = ne_config

    @staticmethod
    def _group_files(file_list):
        groups = defaultdict(list)
        for fp in file_list:
            bn = os.path.basename(fp)
            dn = os.path.dirname(fp)
            if '.' in bn:
                name, ext = bn.rsplit('.', 1); ext = '.' + ext
            else:
                name = bn; ext = ''
            stem = re.sub(r'\d+$', '', name)
            if not stem: stem = name
            groups[(dn, stem, ext)].append(fp)
        return groups

    def _exec(self, c, cmd):
        stdin, stdout, stderr = c.exec_command(cmd, timeout=15)
        return stdout.read().decode("utf-8",errors="replace"), stderr.read().decode("utf-8",errors="replace")

    def run(self):
        try:
            c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(self.host, self.port, self.user, self.pwd, timeout=10)
            def _resolve_entry(entry):
                if isinstance(entry, dict): return entry["path"], entry.get("desc","")
                return str(entry), ""

            for entry in self.ne.get("files", []):
                fpath, desc = _resolve_entry(entry)
                if '*' in fpath or '?' in fpath:
                    out, _ = self._exec(c, f"ls -1 {fpath} 2>/dev/null || echo MISSING")
                    if out and out.strip() != "MISSING":
                        matches = [l.strip() for l in out.split("\n") if l.strip()]
                        groups = self._group_files(matches)
                        for (gdir, stem, ext), files in sorted(groups.items()):
                            pattern = f"{stem}*{ext}" if stem != os.path.basename(files[0]) else os.path.basename(files[0])
                            self.file_info.emit({
                                "path": gdir, "name": pattern, "size": len(files),
                                "type": "group", "group_files": sorted(files),
                                "pattern_path": f"{gdir}/{pattern}", "desc": desc
                            })
                    else:
                        self.file_info.emit({"path": fpath, "name": os.path.basename(fpath), "size": 0, "type": "file", "missing": True, "desc": desc})
                else:
                    out, _ = self._exec(c, f"stat --format='%s' {fpath} 2>/dev/null || echo MISSING")
                    if out and out.strip() != "MISSING":
                        try: sz = int(out.strip().split("\n")[0])
                        except: sz = 0
                        self.file_info.emit({"path": fpath, "name": os.path.basename(fpath), "size": sz, "type": "file", "desc": desc})
                    else:
                        self.file_info.emit({"path": fpath, "name": os.path.basename(fpath), "size": 0, "type": "file", "missing": True, "desc": desc})

            for entry in self.ne.get("directories", []):
                dpath, desc = _resolve_entry(entry)
                out, _ = self._exec(c, f"test -d {dpath} && echo OK || echo MISSING")
                if out and out.strip() == "OK":
                    self.file_info.emit({"path": dpath, "name": os.path.basename(dpath)+"/", "size": 0, "type": "directory", "desc": desc})
                else:
                    self.file_info.emit({"path": dpath, "name": os.path.basename(dpath)+"/", "size": 0, "type": "directory", "missing": True, "desc": desc})

            c.close()
            self.finished.emit()
        except Exception as e: self.error.emit(str(e))

class LogBrowseWorker(QThread):
    log = Signal(str); file_info = Signal(dict); finished = Signal(); error = Signal(str)
    def __init__(self, host, port, user, pwd, path):
        super().__init__()
        self.host = host; self.port = int(port); self.user = user; self.pwd = pwd; self.path = path
    def run(self):
        try:
            c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(self.host, self.port, self.user, self.pwd, timeout=5)
            qpath = _shell_quote(self.path.rstrip("/"))
            stdin, stdout, stderr = c.exec_command(f"find {qpath} -maxdepth 1 -mindepth 1 -type d -print 2>/dev/null | sort | head -200")
            dirs = [line.strip() for line in stdout.read().decode("utf-8",errors="replace").split("\n") if line.strip()]
            for d in dirs:
                self.file_info.emit({"path": d, "name": os.path.basename(d)+"/", "size": 0, "type": "directory", "desc": ""})
            stdin, stdout, stderr = c.exec_command(f"find {qpath} -maxdepth 1 -mindepth 1 -type f -print 2>/dev/null | sort | head -500")
            files = [line.strip() for line in stdout.read().decode("utf-8",errors="replace").split("\n") if line.strip()]
            groups = LogListWorker._group_files(files)
            for (gdir, stem, ext), group_files in sorted(groups.items()):
                pattern = f"{stem}*{ext}" if stem != os.path.basename(group_files[0]) else os.path.basename(group_files[0])
                self.file_info.emit({
                    "path": gdir, "name": pattern, "size": len(group_files),
                    "type": "group", "group_files": sorted(group_files),
                    "pattern_path": f"{gdir}/{pattern}", "desc": ""
                })
            c.close()
            self.finished.emit()
        except Exception as e: self.error.emit(str(e))

class LogViewerWorker(QThread):
    log = Signal(str); done = Signal()
    def __init__(self, host, port, user, pwd, remote_path, tail=False):
        super().__init__()
        self.host = host; self.port = int(port); self.user = user; self.pwd = pwd
        self.remote_path = remote_path; self.tail = tail; self._stop = False
        self._ssh = None; self._chan = None
    def stop(self):
        self._stop = True
        try:
            if self._chan: self._chan.close()
        except: pass
        try:
            if self._ssh: self._ssh.close()
        except: pass
    def run(self):
        try:
            c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(self.host, self.port, self.user, self.pwd, timeout=5); self._ssh = c
            qpath = _shell_quote(self.remote_path)
            if self.tail:
                chan = c.get_transport().open_session()
                self._chan = chan
                chan.exec_command(f"tail -n 500 -f {qpath} 2>/dev/null")
                while not self._stop:
                    if chan.recv_ready():
                        data = chan.recv(4096).decode("utf-8",errors="replace")
                        for line in data.split("\n"):
                            if line.strip(): self.log.emit(line)
                    else: time.sleep(0.1)
                chan.close()
            else:
                stdin, stdout, stderr = c.exec_command(f"tail -200 {qpath} 2>/dev/null")
                out = stdout.read().decode("utf-8",errors="replace")
                for line in out.split("\n"):
                    if line.strip(): self.log.emit(line)
            c.close()
            self.done.emit()
        except Exception as e: self.log.emit(f"Error: {e}"); self.done.emit()

class LogDownloadWorker(QThread):
    log = Signal(str); done = Signal(); error = Signal(str)
    def __init__(self, host, port, user, pwd, files, save_dir):
        super().__init__()
        self.host = host; self.port = int(port); self.user = user; self.pwd = pwd
        self.files = files; self.save_dir = save_dir
    def run(self):
        try:
            c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(self.host, self.port, self.user, self.pwd, timeout=5)
            for f in self.files:
                if isinstance(f, dict):
                    rp = f["remote_path"]; lp = os.path.join(self.save_dir, f["filename"])
                else:
                    rp = f; lp = os.path.join(self.save_dir, os.path.basename(f))
                self.log.emit(f"[download] {rp} -> {lp}")
                SCPClient(c.get_transport()).get(rp, lp, recursive=True)
            c.close(); self.done.emit()
        except Exception as e: self.error.emit(str(e))

class NEServiceWorker(QThread):
    log = Signal(str); service_finished = Signal(bool, str)
    def __init__(self, host, port, ssh_user, ssh_pwd, exec_user, method, commands, inputs, process_names, ne_type, action):
        super().__init__()
        self.host = host; self.port = int(port); self.ssh_user = ssh_user; self.ssh_pwd = ssh_pwd
        self.exec_user = exec_user; self.method = method; self.commands = commands; self.inputs = inputs
        self.process_names = process_names; self.ne_type = ne_type; self.action = action
        self._stop = False
    def stop(self): self._stop = True
    def run(self):
        try:
            c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(self.host, self.port, self.ssh_user, self.ssh_pwd, timeout=10)
            if self.method == "kill":
                for pn in self.process_names:
                    out = self._exec(c, f"ps -ef | grep '{pn}' | grep -v grep")
                    pids = []
                    for l in out.split("\n"):
                        parts = l.strip().split()
                        if len(parts) >= 2:
                            pids.append(parts[1])
                    if pids:
                        self._exec(c, f"kill -9 {' '.join(pids)}")
                        time.sleep(1)
                        self._exec(c, f"ps -ef | grep '{pn}' | grep -v grep | awk '{{print $2}}' | xargs -r kill -9 2>/dev/null; echo done")
            else:
                cwd = None
                for i, cmd in enumerate(self.commands):
                    if self._stop: break
                    if cmd.startswith("WAIT "):
                        sec = int(cmd.split()[1])
                        self.log.emit(f"  (wait {sec}s)")
                        time.sleep(sec)
                        continue
                    if cmd.startswith("cd "):
                        cwd = cmd[3:].strip()
                        self.log.emit(f"  (cd {cwd})")
                        continue
                    inp = self.inputs.get(str(i+1)) if self.inputs else None
                    actual = f"cd {cwd} && {cmd}" if cwd else cmd
                    if actual.strip().endswith("&"):
                        bg = actual.rstrip()[:-1].rstrip() + " </dev/null >>nohup.out 2>&1 &"
                        if self.exec_user and self.exec_user != self.ssh_user:
                            escaped = bg.replace("'","'\"'\"'")
                            bg = f"su - {self.exec_user} -c '{escaped}'"
                        self.log.emit(f"> {bg}")
                        c.exec_command(bg)
                    else:
                        self._exec(c, actual, inp)
            c.close()
            self.service_finished.emit(True, f"{self.ne_type} {self.action} completed")
        except Exception as e:
            self.service_finished.emit(False, str(e))
    def _exec(self, c, cmd, input_str=None):
        full_cmd = cmd
        if self.exec_user and self.exec_user != self.ssh_user:
            escaped = cmd.replace("'","'\"'\"'")
            full_cmd = f"su - {self.exec_user} -c '{escaped}'"
        self.log.emit(f"> {full_cmd}")
        stdin, stdout, stderr = c.exec_command(full_cmd, timeout=60)
        if input_str:
            stdin.write(input_str); stdin.flush(); stdin.channel.shutdown_write()
        try:
            out = stdout.read().decode("utf-8",errors="replace").strip()
            err = stderr.read().decode("utf-8",errors="replace").strip()
        except: out = ""; err = ""
        for l in out.split("\n"):
            if l.strip(): self.log.emit(f"  {l.strip()}")
        for l in err.split("\n"):
            if l.strip(): self.log.emit(f"  ! {l.strip()}")
        return out


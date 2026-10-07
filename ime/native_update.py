"""Linux native addon activation from an already verified desktop release."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from .updater import UpdateError, _atomic_json, _bounded_json, executable_in, subprocess_environment


class NativeUpgrade:
    def __init__(self, updater, record, *, prefix=None):
        self.updater, self.record = updater, record
        self.directory, self.package = updater._package(record)
        self.prefix = Path(prefix) if prefix is not None else Path.home() / ".local"
        self.source = self.package / "source"
        self.native = self.directory / "native"
        self.backup = updater.root / "native-backups" / uuid.uuid4().hex
        self.changed = []
        self.prior_state = None
        self.was_running = False

    def _run(self, command, *, timeout=180):
        log = self.updater.root / "native-update.log"
        with log.open("ab") as output:
            result = subprocess.run(command, env=subprocess_environment(), stdout=output,
                                    stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            raise UpdateError("原生输入法更新失败；详情：" + str(log))

    def prepare(self):
        if sys.platform != "linux" or self.record["target"] != "linux-x64":
            raise UpdateError("Native addon upgrades require Linux x64.")
        self.updater._record(self.record)
        if not (self.prefix / "lib/fcitx5/shuangsheng.so").is_file():
            raise UpdateError("The existing user-installed native addon was not found.")
        for command in ("cmake", "c++", "systemctl"):
            if not shutil.which(command):
                raise UpdateError(f"Native update needs {command}; the current installation is unchanged.")
        if not (self.source / "native/fcitx5/CMakeLists.txt").is_file():
            raise UpdateError("The release does not contain native addon sources.")
        self.was_running = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "shuangsheng.service"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if not self.was_running:
            raise UpdateError("Start shuangsheng.service before upgrading its native addon.")
        processes = subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True, check=True).stdout
        if any(" -m ime.native_voice" in line or " --native-voice " in line for line in processes.splitlines()):
            raise UpdateError("请先结束原生输入法录音，再安装更新。")
        build = self.directory / "native-build"
        executable = executable_in(self.package, "linux-x64")
        self._run(["cmake", "-S", str(self.source / "native/fcitx5"), "-B", str(build),
                   "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_INSTALL_PREFIX={self.native}",
                   "-DCMAKE_INSTALL_LIBDIR=lib", f"-DSHUANGSHENG_PROJECT_ROOT={self.source}",
                   f"-DSHUANGSHENG_VOICE_EXECUTABLE={executable}", "-DSHUANGSHENG_VOICE_FROZEN=ON"])
        self._run(["cmake", "--build", str(build), "-j2"], timeout=600)
        self._run(["ctest", "--test-dir", str(build), "--output-on-failure"])
        self._run(["cmake", "--install", str(build)])
        bridge = self.directory / "bridge-build"
        self._run(["cmake", "-S", str(self.source / "native/pinyin_bridge"), "-B", str(bridge),
                   "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_INSTALL_PREFIX={self.native}"])
        self._run(["cmake", "--build", str(bridge), "-j2"], timeout=600)
        self._run(["cmake", "--install", str(bridge)])
        for path in (self.native / "lib/fcitx5/shuangsheng.so", self.native / "bin/shuangsheng-pinyin-bridge"):
            if not path.is_file():
                raise UpdateError("The native build did not produce the expected files.")

    def _switch(self, relative):
        destination = self.prefix / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        saved = self.backup / relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            previous = {"kind": "symlink", "target": os.readlink(destination)}
        elif destination.is_file():
            shutil.copy2(destination, saved)
            previous = {"kind": "file"}
        elif destination.exists():
            raise UpdateError("Native destination is not a regular file or symlink.")
        else:
            previous = {"kind": "missing"}
        self.changed.append((relative, previous))
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex)
        try:
            temporary.symlink_to(self.native / relative)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _restart(self, expected=None):
        self._run(["systemctl", "--user", "restart", "shuangsheng.service"], timeout=30)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = subprocess.run(["systemctl", "--user", "show", "shuangsheng.service", "--property=MainPID", "--value"],
                                    capture_output=True, text=True)
            pid = result.stdout.strip()
            if pid.isascii() and pid.isdigit() and int(pid):
                try:
                    maps = Path(f"/proc/{pid}/maps").read_text(encoding="utf-8")
                    if expected is None or str(expected.resolve()) in maps:
                        return
                except OSError:
                    pass
            time.sleep(0.2)
        raise UpdateError("输入法未加载预期的新插件，正在恢复原版本。")

    def activate(self):
        self.backup.mkdir(parents=True, exist_ok=False)
        state_path = self.updater.root / "native.json"
        self.prior_state = _bounded_json(state_path) if state_path.exists() else None
        try:
            self._switch(Path("lib/fcitx5/shuangsheng.so"))
            self._switch(Path("bin/shuangsheng-pinyin-bridge"))
            _atomic_json(self.backup / "backup.json", {
                "entries": [{"path": str(path), **old} for path, old in self.changed],
                "prior_state": self.prior_state})
            self._restart(self.native / "lib/fcitx5/shuangsheng.so")
            _atomic_json(state_path, {"current": self.record, "backup": str(self.backup), "previous": self.prior_state})
        except Exception:
            self.rollback()
            raise

    def rollback(self):
        if not self.changed:
            return
        for relative, previous in reversed(self.changed):
            destination = self.prefix / relative
            temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex)
            try:
                if previous["kind"] == "missing":
                    destination.unlink(missing_ok=True)
                    continue
                if previous["kind"] == "symlink":
                    temporary.symlink_to(previous["target"])
                else:
                    shutil.copy2(self.backup / relative, temporary)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        self.changed.clear()
        state_path = self.updater.root / "native.json"
        if self.prior_state is None:
            state_path.unlink(missing_ok=True)
        else:
            _atomic_json(state_path, self.prior_state)
        if self.was_running:
            self._restart()

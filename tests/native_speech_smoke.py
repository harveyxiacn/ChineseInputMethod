"""End-to-end native dictation check using a virtual PipeWire sink.

Run: python3 tests/native_speech_smoke.py /path/to/mandarin-sample.wav
Requires PyGObject, pactl, pw-play, installed Shuangsheng and cached Whisper.
Restarts the keyboard temporarily; never records a physical microphone or
changes the default audio device. Restores the service environment on exit.
"""
import argparse
import os
from pathlib import Path
import subprocess
import time

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sample', type=Path)
    args = parser.parse_args()
    if not args.sample.is_file():
        parser.error('Supply an existing Mandarin WAV sample.')
    sink = f'shuangsheng_test_{os.getpid()}'
    module = None
    source_module = None
    path = None
    bus = None
    player = None
    previous = None
    manager = subprocess.check_output(['systemctl', '--user', 'show-environment'], text=True)
    for line in manager.splitlines():
        if line.startswith('IME_RECORD_TARGET='):
            previous = line.split('=', 1)[1]
    service = 'org.fcitx.Fcitx5'
    interface = 'org.fcitx.Fcitx.InputContext1'

    def call(object_path, iface, method, params=None):
        return bus.call_sync(service, object_path, iface, method, params, None,
                             Gio.DBusCallFlags.NO_AUTO_START, 5000, None).unpack()

    def key(symbol, modifiers=0):
        return call(path, interface, 'ProcessKeyEvent',
                    GLib.Variant('(uuubu)', (symbol, 0, modifiers, False, 0)))[0]

    try:
        module = subprocess.check_output(['pactl', 'load-module', 'module-null-sink',
                                           f'sink_name={sink}'], text=True).strip()
        source_module = subprocess.check_output(['pactl', 'load-module', 'module-remap-source',
                                                 f'master={sink}.monitor', f'source_name={sink}_source'], text=True).strip()
        subprocess.run(['systemctl', '--user', 'set-environment', f'IME_RECORD_TARGET={sink}_source'], check=True)
        subprocess.run(['systemctl', '--user', 'restart', 'shuangsheng.service'], check=True)
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        path, _ = call('/org/freedesktop/portal/inputmethod',
                       'org.fcitx.Fcitx.InputMethod1', 'CreateInputContext',
                       GLib.Variant('(a(ss))', ([('program', 'shuangsheng-voice-test'), ('display', 'shuangsheng-test:')],)))
        commits = []
        updates = []

        def received(_bus, _sender, _path, _iface, name, params, _data):
            if name == 'CommitString':
                commits.append(params.unpack()[0])
            else:
                updates.append((name, str(params.unpack())[:600]))
                del updates[:-12]

        bus.signal_subscribe(service, interface, None, path, None,
                             Gio.DBusSignalFlags.NONE, received, None)
        call(path, interface, 'SetCapability', GLib.Variant('(t)', (2 | 16 | (1 << 39),)))
        call(path, interface, 'FocusIn')
        subprocess.run(['fcitx5-remote', '-s', 'shuangsheng'], check=True)
        assert key(ord('m'), 4 | 8), 'Mandarin shortcut was not consumed'
        assert key(ord(' '), 4 | 8), 'Start recording shortcut was not consumed'
        time.sleep(1)
        player = subprocess.Popen(['pw-play', '--target', sink, str(args.sample.resolve())])
        player.wait(timeout=30)
        assert player.returncode == 0, 'Virtual fixture playback failed'
        time.sleep(0.2)
        assert key(ord(' '), 4 | 8), 'Stop recording shortcut was not consumed'
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not commits:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(.01)
        assert commits and any('\u4e00' <= char <= '\u9fff' for char in commits[-1]), f'No Chinese transcript committed; last input updates: {updates}'
        print('PASS: native hotkey → PipeWire capture → stop → Whisper → focused application commit')
        print(commits[-1])
    finally:
        if path and bus:
            try:
                call(path, interface, 'FocusOut')
                call(path, interface, 'DestroyIC')
            except GLib.Error:
                pass
        if player and player.poll() is None:
            player.terminate()
            player.wait(timeout=5)
        if previous is None:
            subprocess.run(['systemctl', '--user', 'unset-environment', 'IME_RECORD_TARGET'], check=True)
        else:
            subprocess.run(['systemctl', '--user', 'set-environment', f'IME_RECORD_TARGET={previous}'], check=True)
        subprocess.run(['systemctl', '--user', 'restart', 'shuangsheng.service'], check=True)
        if source_module:
            subprocess.run(['pactl', 'unload-module', source_module], check=True)
        if module:
            subprocess.run(['pactl', 'unload-module', module], check=True)


if __name__ == '__main__':
    main()

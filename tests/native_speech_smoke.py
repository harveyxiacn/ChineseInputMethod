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
import signal
import tempfile
import wave

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sample', type=Path)
    parser.add_argument('--repeat', type=int, default=1, choices=range(1, 11))
    outcome = parser.add_mutually_exclusive_group()
    outcome.add_argument('--fail-after-preview', action='store_true')
    outcome.add_argument('--cancel-after-preview', action='store_true')
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
    fixture = tempfile.TemporaryDirectory(prefix="shuangsheng-fixture-")
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
        previews = []

        def received(_bus, _sender, _path, _iface, name, params, _data):
            if name == 'CommitString':
                commits.append(params.unpack()[0])
            else:
                if name == 'UpdateFormattedPreedit':
                    text = ''.join(part[0] for part in params.unpack()[0])
                    if any('\u4e00' <= char <= '\u9fff' for char in text):
                        previews.append(text)
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
        with wave.open(str(args.sample), 'rb') as source:
            params = source.getparams()
            frames = source.readframes(source.getnframes())
        recording = Path(fixture.name) / 'long.wav'
        with wave.open(str(recording), 'wb') as destination:
            destination.setparams(params)
            for _ in range(args.repeat):
                destination.writeframes(frames)
        player = subprocess.Popen(['pw-play', '--target', sink, str(recording)])
        player.wait(timeout=115)
        assert player.returncode == 0, 'Virtual fixture playback failed'
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not previews:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(.01)
        assert previews, f'No live preview while recording: {updates}'
        assert not commits, 'Preview must not commit provisional text'
        if args.cancel_after_preview:
            assert key(0xFF1B), 'Escape must cancel dictation'
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                time.sleep(.01)
            assert not commits, 'Explicit cancellation must discard the preview'
            print('PASS: Escape discarded the preview without committing text')
            return
        if args.fail_after_preview:
            host = subprocess.check_output(['systemctl', '--user', 'show',
                                            'shuangsheng.service', '-p', 'MainPID', '--value'], text=True).strip()
            children = subprocess.check_output(['pgrep', '-P', host], text=True).split()
            workers = [int(pid) for pid in children if b'ime.native_voice' in
                       Path(f'/proc/{pid}/cmdline').read_bytes()]
            assert len(workers) == 1, workers
            os.killpg(workers[0], signal.SIGKILL)
        else:
            assert key(ord(' '), 4 | 8), 'Stop recording shortcut was not consumed'
        stopped_at = time.monotonic()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not commits:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(.01)
        assert commits and any('\u4e00' <= char <= '\u9fff' for char in commits[-1]), f'No Chinese transcript committed; last input updates: {updates}'
        print('PASS: native hotkey → PipeWire capture → live Whisper preedit → stop → focused application commit')
        assert len(commits) == 1, f'Duplicate commits: {commits}'
        if args.fail_after_preview:
            assert commits[-1] in previews, (commits, previews)
            print('PASS: worker crash preserved the recognized preview')
        print(f'Stop to commit: {time.monotonic() - stopped_at:.2f}s; previews: {len(previews)}')
        print(commits[-1])
    finally:
        fixture.cleanup()
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

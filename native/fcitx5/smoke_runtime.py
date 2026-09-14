#!/usr/bin/python3
"""Exercise the installed addon through a real Fcitx DBus input context.

Run in the desktop session with /usr/bin/python3 (python-gobject required).
Only this synthetic input context receives test text. No microphone is used.
"""
import subprocess
import time
import gi

gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
service = 'org.fcitx.Fcitx5'
interface = 'org.fcitx.Fcitx.InputContext1'

def call(path, iface, method, params=None):
    return bus.call_sync(service, path, iface, method, params, None,
                         Gio.DBusCallFlags.NONE, 5000, None).unpack()

# An unmatched display keeps this synthetic context outside the desktop focus
# group, so real application focus events cannot reset its composition.
path, _ = call('/org/freedesktop/portal/inputmethod',
               'org.fcitx.Fcitx.InputMethod1', 'CreateInputContext',
               GLib.Variant('(a(ss))', ([('program', 'shuangsheng-smoke'), ('display', 'shuangsheng-test:')],)))
commits = []
preedits = []

def signal(_bus, _sender, _path, _iface, name, params, _data):
    args = params.unpack()
    if name == 'CommitString':
        commits.append(args[0])
    elif name == 'UpdateFormattedPreedit':
        preedits.append(''.join(part[0] for part in args[0]))

subscription = bus.signal_subscribe(service, interface, None, path, None,
                                    Gio.DBusSignalFlags.NONE, signal, None)

def drain():
    until = time.monotonic() + 0.08
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.002)

def key(symbol, modifiers=0):
    return call(path, interface, 'ProcessKeyEvent',
                GLib.Variant('(uuubu)', (symbol, 0, modifiers, False, 0)))[0]

def type_text(text):
    for char in text:
        assert key(ord(char)), f'Unconsumed key: {char}'
    drain()

try:
    call(path, interface, 'SetCapability', GLib.Variant('(t)', (2 | 16,)))
    call(path, interface, 'FocusIn')
    subprocess.run(['fcitx5-remote', '-s', 'shuangsheng'], check=True)
    type_text('nihao')
    assert any(text.replace(' ', '') == 'nihao' for text in preedits), preedits
    key(ord(' ')); drain()
    assert commits[-1] == '你好', commits
    type_text('xiexie')
    key(ord('1')); drain()
    assert commits[-1] == '谢谢', commits
    assert key(ord('t'), 4 | 8), 'Script shortcut must be consumed'
    type_text('xiexie')
    key(ord(' ')); drain()
    assert commits[-1] == '謝謝', commits
    assert key(ord('t'), 4 | 8), 'Script shortcut must be consumed'
    type_text("xi'an")
    key(ord(' ')); drain()
    assert commits[-1] == '西安', commits
    # These are decoded as sentences; they are not entries in our bundled TSV.
    for pinyin, expected in [
        ('wojintianxiangquchaoshimaidongxi', '我今天想去超市买东西'),
        ('mingtianwomenyiqiquchifan', '明天我们一起去吃饭'),
        ('rengongzhineng', '人工智能'),
        ('zhonghuarenmingongheguo', '中华人民共和国'),
    ]:
        type_text(pinyin)
        key(ord(' ')); drain()
        assert commits[-1] == expected, (pinyin, commits)
    type_text('nihaoo')
    assert key(0xFF08), 'Backspace must update sentence decoding'
    key(ord(' ')); drain()
    assert commits[-1] == '你好', commits
    type_text('z')
    assert key(0xFF08), 'Backspace to empty must be consumed'
    assert not key(0xFF08), 'Backspace on empty must pass through'
    type_text('zzzzzzzz')
    key(0xFF0D); drain()
    assert commits[-1] == 'zzzzzzzz', commits
    type_text('rawtext')
    key(0xFF0D); drain()
    assert commits[-1] == 'rawtext', commits
    before = len(commits)
    type_text('nihao'); key(0xFF1B); drain()
    assert len(commits) == before
    call(path, interface, 'SetCapability', GLib.Variant('(t)', (2 | 16 | 8,)))
    assert not key(ord('n')), 'Password field must bypass pinyin'
    assert not key(ord(' '), 4 | 8), 'Password field must bypass dictation'
    print('PASS: real Fcitx preedit, candidate/number selection, both scripts, sentence decoding, editing, raw commit, cancellation, password bypass')
finally:
    call(path, interface, 'FocusOut')
    call(path, interface, 'DestroyIC')
    bus.signal_unsubscribe(subscription)

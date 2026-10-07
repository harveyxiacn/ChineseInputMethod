"""Exercise the newly built addon in a private D-Bus session and temporary profile.

No installed input method, real application, microphone, or user settings change.
Run with system Python (PyGObject) after building .cache/native-build.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def system_addon_directory():
    result = subprocess.run(['pkg-config', '--variable=libdir', 'Fcitx5Core'],
                            check=True, capture_output=True, text=True, timeout=10)
    if not result.stdout.strip():
        raise RuntimeError('Fcitx5Core did not report its system library directory')
    return Path(result.stdout.strip()) / 'fcitx5'


def outer(build_dir, voice_mode=None):
    build_dir = build_dir.resolve()
    if voice_mode is None and not (build_dir / 'shuangsheng.so').is_file():
        raise RuntimeError(f'Build the native addon before running its smoke test: {build_dir}')
    with tempfile.TemporaryDirectory(prefix='shuangsheng-native-') as directory:
        base=Path(directory)
        voice_environment={}
        if voice_mode:
            # Compile against an immutable staged tree without a virtualenv. The
            # stub consumes argv directly; no microphone or model is opened.
            stage=base/'staged source 测试'
            (stage/'ime/data').mkdir(parents=True)
            shutil.copyfile(ROOT/'ime/data/jyutping.tsv',stage/'ime/data/jyutping.tsv')
            executable=base/'voice runtime 测试'
            observed=base/'voice-launch.json'
            executable.write_text('#!/usr/bin/python3\nimport json, os, sys\n'
                                  'from pathlib import Path\n'
                                  "Path(os.environ['IME_TEST_VOICE_OBSERVED']).write_text(json.dumps({'argv':sys.argv,'cwd':os.getcwd()}))\n"
                                  "print('SHUANGSHENG_OK\\n打包语音成功\\nSHUANGSHENG_END',flush=True)\n",encoding='utf-8')
            executable.chmod(0o700)
            build_dir=base/'native build 测试'
            subprocess.run(['cmake','-S',str(ROOT/'native/fcitx5'),'-B',str(build_dir),
                            '-DBUILD_TESTING=OFF',f'-DSHUANGSHENG_PROJECT_ROOT={stage}',
                            f'-DSHUANGSHENG_VOICE_EXECUTABLE={executable}',
                            f'-DSHUANGSHENG_VOICE_FROZEN={"ON" if voice_mode=="frozen" else "OFF"}'],
                           check=True,timeout=60)
            subprocess.run(['cmake','--build',str(build_dir),'--target','shuangsheng','-j2'],check=True,timeout=120)
            voice_environment=dict(IME_TEST_VOICE_MODE=voice_mode,IME_TEST_VOICE_EXECUTABLE=str(executable),
                                   IME_TEST_VOICE_OBSERVED=str(observed),IME_TEST_VOICE_ROOT=str(stage))
        for kind,source in [('addon','shuangsheng-addon.conf'),('inputmethod','shuangsheng.conf')]:
            target=base/'data/fcitx5'/kind/'shuangsheng.conf';target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/'native/fcitx5'/source,target)
        profile=base/'config/fcitx5/profile';profile.parent.mkdir(parents=True)
        profile.write_text('[Groups/0]\nName=Default\nDefault Layout=us\nDefaultIM=shuangsheng\n\n[Groups/0/Items/0]\nName=keyboard-us\nLayout=\n\n[Groups/0/Items/1]\nName=shuangsheng\nLayout=\n\n[GroupOrder]\n0=Default\n')
        settings=base/'settings.json';settings.write_text('{}')
        environment=dict(os.environ, XDG_CONFIG_HOME=str(base/'config'),XDG_DATA_HOME=str(base/'data'),
                         FCITX_ADDON_DIRS=os.pathsep.join((str(build_dir), str(system_addon_directory()))),
                         IME_SETTINGS_PATH=str(settings),IME_LEXICON_PATH=str(base/'lexicon.json'),
                         DISPLAY='',WAYLAND_DISPLAY='',**voice_environment)
        for key in ('FCITX_CONFIG_HOME','FCITX_DATA_HOME','FCITX_CONFIG_DIRS','FCITX_DATA_DIRS'):
            environment.pop(key,None)
        return subprocess.run(['dbus-run-session','--',sys.executable,__file__,'--inner'],env=environment,timeout=90).returncode


def inner():
    import gi
    gi.require_version('Gio','2.0')
    from gi.repository import Gio,GLib
    log=tempfile.TemporaryFile()
    host=subprocess.Popen(['fcitx5','-D','--disable','all','--enable','dbus,dbusfrontend,keyboard,shuangsheng'],stdout=log,stderr=log)
    bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
    interface='org.fcitx.Fcitx.InputContext1';service='org.fcitx.Fcitx5';path=None
    def call(path,iface,method,params=None):
        return bus.call_sync(service,path,iface,method,params,None,Gio.DBusCallFlags.NO_AUTO_START,5000,None).unpack()
    try:
        for _ in range(100):
            try:
                path,_=call('/org/freedesktop/portal/inputmethod','org.fcitx.Fcitx.InputMethod1','CreateInputContext',GLib.Variant('(a(ss))',([('program','shuangsheng-isolated'),('display','isolated-test:')],)))
                break
            except GLib.Error:time.sleep(.05)
        if path is None:raise RuntimeError('Private Fcitx did not start')
        commits=[];preedit=[]
        def received(_b,_s,_p,_i,name,params,_data):
            if name=='CommitString':commits.append(params.unpack()[0])
            elif name=='UpdateFormattedPreedit':preedit.append(''.join(item[0] for item in params.unpack()[0]))
        subscription=bus.signal_subscribe(service,interface,None,path,None,Gio.DBusSignalFlags.NONE,received,None)
        def drain():
            until=time.monotonic()+.06
            while time.monotonic()<until:
                while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
                time.sleep(.002)
        def key(code,modifiers=0):
            result=call(path,interface,'ProcessKeyEvent',GLib.Variant('(uuubu)',(code,0,modifiers,False,0)))[0]
            drain();return result
        def write(text):
            for char in text:assert key(ord(char)),f'unconsumed {char!r} in {text}'
        def expect(text,query,finish=ord(' '),modifiers=0):
            write(query);key(finish,modifiers);assert commits[-1]==text,(query,commits[-3:])
        def configure(**changes):
            p=Path(os.environ['IME_SETTINGS_PATH']);data=json.loads(p.read_text());data.update(changes);p.write_text(json.dumps(data));time.sleep(.55)
        call(path,interface,'SetCapability',GLib.Variant('(t)',(2|16,)));call(path,interface,'FocusIn')
        subprocess.run(['fcitx5-remote','-s','shuangsheng'],check=True)
        expect('你好','nihao')
        expect('我今天想去超市买东西','wojintianxiangquchaoshimaidongxi')
        for text in ['Python3.12','test@example.com','v1.2.3','/usr/bin','foo_bar','hello']:
            expect(text,text,0xFF0D)
        expect('你好','nihao');key(ord(','));assert commits[-1]=='，'
        assert not key(ord('3'));assert not key(ord('.'))
        write('cancelme');before=len(commits);key(0xFF1B);assert len(commits)==before
        configure(input_scheme='shuangpin');expect('你好','nihc')
        configure(input_scheme='jyutping',script='traditional');expect('你好','nei5hou2')
        write('ngo5');key(ord('1'),8);assert commits[-1]=='我',commits[-3:]
        configure(input_scheme='pinyin',script='simplified')
        lex=Path(os.environ['IME_LEXICON_PATH'])
        data=json.loads(lex.read_text()) if lex.exists() else {'learn':[]}
        data['entries']=[{'id':'a','text':'测试签名','pinyin':'ceshiqianming','shortcut':'/sig','pinned':True}];lex.write_text(json.dumps(data));time.sleep(.55)
        expect('测试签名','/sig')
        configure(app_profiles={'shuangsheng-isolated':{'input_mode':'english'}})
        assert not key(ord('n')),'English app profile must bypass composition'
        configure(app_profiles={})
        if os.environ.get('IME_TEST_VOICE_MODE'):
            configure(language='yue',script='traditional')
            before=len(commits)
            assert key(ord(' '),4|8),'dictation hotkey not consumed'
            for _ in range(50):
                drain()
                if len(commits)>before:break
            assert len(commits)==before+1 and commits[-1]=='打包语音成功',commits[-3:]
            observed=json.loads(Path(os.environ['IME_TEST_VOICE_OBSERVED']).read_text())
            prefix=['--native-voice'] if os.environ['IME_TEST_VOICE_MODE']=='frozen' else ['-m','ime.native_voice']
            expected=[os.environ['IME_TEST_VOICE_EXECUTABLE'],*prefix,'--protocol','--language','yue','--script','traditional']
            assert observed['argv']==expected,observed
            assert observed['cwd']==os.environ['IME_TEST_VOICE_ROOT'],observed
            assert not Path(observed['cwd'],'.venv').exists()
            print(f'PASS: {os.environ["IME_TEST_VOICE_MODE"]} voice argv, staged cwd without .venv, language/script, framed commit')
        call(path,interface,'SetCapability',GLib.Variant('(t)',(2|16|8,)))
        assert not key(ord('n'));assert not key(ord(' '),4|8)
        call(path,interface,'FocusOut');call(path,interface,'DestroyIC');path=None
        bus.signal_unsubscribe(subscription)
        print('PASS: private native runtime: full sentences, mixed literals, punctuation, cancellation, Xiaohe, toned Jyutping, personal shortcut, app profile, sensitive bypass')
        return 0
    except Exception:
        log.seek(0);sys.stderr.write(log.read().decode(errors='replace')[-6000:]);raise
    finally:
        if path:
            try:call(path,interface,'DestroyIC')
            except Exception:pass
        host.terminate()
        try:host.wait(timeout=5)
        except subprocess.TimeoutExpired:host.kill();host.wait()
        log.close()


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT / '.cache/native-build')
    parser.add_argument('--voice-mode', choices=('python','frozen'), help='Build a staged addon and test its fake dictation runtime; no microphone or model')
    parser.add_argument('--inner', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    raise SystemExit(inner() if args.inner else outer(args.build_dir,args.voice_mode))

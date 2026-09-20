# Built by scripts/build_release.py on each target OS. Never cross-compile.
import os
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
extra = Path(os.environ['SHUANGSHENG_BUILD_METADATA'])
datas = [(str(root / 'ime' / 'data'), 'ime/data'),
         (str(root / 'static'), 'static'),
         (str(root / 'native' / 'rime'), 'native/rime'),
         (str(extra), 'release-metadata')]
# Respect explicit Tcl/Tk locations for portable developer Python installs.
# Standard GitHub Python distributions are collected by the built-in hook.
for variable, destination in [('TCL_LIBRARY', '_tcl_data'), ('TK_LIBRARY', '_tk_data')]:
    if os.environ.get(variable):
        datas.append((os.environ[variable], destination))
binaries, hiddenimports = [], ['scripts.install_rime', 'ime.model_setup', 'ime.server', 'yaml']
for module in ('faster_whisper', 'ctranslate2', 'av', 'onnxruntime', 'opencc'):
    package_data, package_binaries, package_imports = collect_all(module)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports
# pynput selects these modules dynamically; importing it during analysis may
# require an X display or macOS Accessibility permission, so name them directly.
backend = '_win32' if sys.platform == 'win32' else '_darwin' if sys.platform == 'darwin' else '_xorg'
hiddenimports += ['pynput.keyboard.' + backend, 'pynput.mouse.' + backend,
                  'pynput.keyboard._dummy', 'pynput.mouse._dummy']
if sys.platform == 'darwin':
    hiddenimports += ['HIServices', 'Quartz']
for distribution in ('faster-whisper', 'huggingface-hub', 'tokenizers', 'ctranslate2',
                     'onnxruntime', 'av', 'sounddevice', 'pynput', 'opencc-python-reimplemented'):
    datas += copy_metadata(distribution)
a = Analysis([str(root / 'scripts' / 'desktop_entry.py')], pathex=[str(root)],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=['torch', 'tensorflow', 'matplotlib', 'IPython', 'pytest'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Shuangsheng',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=sys.platform != 'darwin', target_arch=None, codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Shuangsheng')
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='Shuangsheng.app',
                 bundle_identifier='io.github.shuangsheng.inputmethod',
                 version=os.environ['SHUANGSHENG_VERSION'].removeprefix('v').split('-', 1)[0],
                 info_plist={
                     'NSMicrophoneUsageDescription': '双声 uses your microphone for local Chinese dictation. Audio stays on this Mac.',
                     'NSHighResolutionCapable': True,
                     'LSMinimumSystemVersion': '14.0',
                 })

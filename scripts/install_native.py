"""Build and install the Fcitx5 keyboard for the current Linux user.

Requires fcitx5, libime (with data), opencc, Boost headers, cmake, and a C++20 compiler. No root is needed
for this script. Installs user configuration and enables a desktop-session
service. The checkout and its .venv must remain at their current location.
"""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def write_config(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() != text:
        backup = path.with_name(path.name + '.before-shuangsheng')
        if not backup.exists():
            shutil.copy2(path, backup)
    path.write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--niri-environment', type=Path,
                        help='Existing niri config file containing an environment block')
    args = parser.parse_args()
    for command in ('fcitx5', 'cmake', 'c++', 'systemctl'):
        if not shutil.which(command):
            parser.error(f'Missing {command}; install native dependencies first.')
    home = Path.home()
    prefix = home / '.local'
    build = ROOT / '.cache/native-build'
    subprocess.run(['cmake', '-S', str(ROOT / 'native/fcitx5'), '-B', str(build),
                    '-DCMAKE_BUILD_TYPE=Release', f'-DCMAKE_INSTALL_PREFIX={prefix}',
                    '-DCMAKE_INSTALL_LIBDIR=lib'], check=True)
    subprocess.run(['cmake', '--build', str(build), '-j2'], check=True)
    subprocess.run(['ctest', '--test-dir', str(build), '--output-on-failure'], check=True)
    subprocess.run(['cmake', '--install', str(build)], check=True)
    config = home / '.config'
    profile = config / 'fcitx5/profile'
    if profile.exists() and 'Name=shuangsheng' not in profile.read_text():
        parser.error('An existing Fcitx5 profile was preserved. Add Shuangsheng using fcitx5-configtool, then rerun.')
    if not profile.exists():
        write_config(profile, '[Groups/0]\nName=Default\nDefault Layout=us\nDefaultIM=shuangsheng\n\n'
                     '[Groups/0/Items/0]\nName=keyboard-us\nLayout=\n\n'
                     '[Groups/0/Items/1]\nName=shuangsheng\nLayout=\n\n[GroupOrder]\n0=Default\n')
    environment = 'XMODIFIERS=@im=fcitx\nQT_IM_MODULE=fcitx\nSDL_IM_MODULE=fcitx\n'
    write_config(config / 'environment.d/70-shuangsheng.conf', environment)
    unit = ('[Unit]\nDescription=Shuangsheng Chinese keyboard (Fcitx5)\n'
            'PartOf=graphical-session.target\nAfter=graphical-session.target\n\n'
            '[Service]\nType=dbus\nBusName=org.fcitx.Fcitx5\n'
            f'Environment="FCITX_ADDON_DIRS={prefix}/lib/fcitx5:/usr/lib/fcitx5"\n'
            'ExecStart=/usr/bin/fcitx5 -D -r\nRestart=on-failure\nRestartSec=3\n'
            'TimeoutStopSec=5\n\n[Install]\nWantedBy=graphical-session.target\n')
    write_config(config / 'systemd/user/shuangsheng.service', unit)
    write_config(prefix / 'share/dbus-1/services/org.fcitx.Fcitx5.service',
                 '[D-BUS Service]\nName=org.fcitx.Fcitx5\nExec=/usr/bin/fcitx5\n'
                 'SystemdService=shuangsheng.service\n')
    if args.niri_environment:
        path = args.niri_environment.expanduser()
        original = path.read_text()
        marker = '// Shuangsheng input method'
        if marker not in original:
            if 'environment {' not in original:
                parser.error('The niri file must contain an environment block.')
            if any(key in original for key in ('XMODIFIERS', 'QT_IM_MODULE ', 'SDL_IM_MODULE')):
                parser.error('Existing input-method environment settings were preserved; merge them manually.')
            text = original.replace('environment {', 'environment {\n'
                                    '        // Shuangsheng input method\n'
                                    '        XMODIFIERS "@im=fcitx"\n'
                                    '        QT_IM_MODULE "fcitx"\n'
                                    '        SDL_IM_MODULE "fcitx"', 1)
            write_config(path, text)
            if subprocess.run(['niri', 'validate']).returncode:
                path.write_text(original)
                parser.error('niri validation failed; its configuration was restored.')
    subprocess.run(['systemctl', '--user', 'set-environment',
                    'XMODIFIERS=@im=fcitx', 'QT_IM_MODULE=fcitx', 'SDL_IM_MODULE=fcitx'], check=True)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', 'shuangsheng.service'], check=True)
    subprocess.run(['systemctl', '--user', 'restart', 'shuangsheng.service'], check=True)
    print('Installed. Use Ctrl+Space to switch between English and Shuangsheng.')
    print('Restart existing applications to pick up input-method settings. Log out/in if needed.')


if __name__ == '__main__':
    main()

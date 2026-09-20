#!/usr/bin/env bash
# Launch a standalone official WeChat executable/AppImage with Qt compatibility.
# Portable-packaged WeChat uses portable.env instead; see README.md.
set -euo pipefail
if (( $# == 0 )); then
    echo 'Usage: scripts/wechat_compat.sh /path/to/WeChat.AppImage [arguments...]' >&2
    exit 2
fi
exec env QT_QPA_PLATFORM=xcb QT_IM_MODULE=ibus IBUS_USE_PORTAL=1 XMODIFIERS=@im=fcitx "$@"

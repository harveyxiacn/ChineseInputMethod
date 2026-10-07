"""Install Shuangsheng into a Weasel, Squirrel, or Fcitx5-Rime user directory.

The OS frontend must already be installed. Run its Redeploy command afterwards.
No system files are changed. Existing files are backed up before replacement.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ID = "shuangsheng"


def current_platform() -> str:
    system = platform.system()
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(system, "unknown")


def windows_user_dir() -> Path:
    # Weasel allows an alternate user data directory in the current user's registry.
    if os.name == "nt":
        import winreg
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Rime\Weasel",
                                    0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, "RimeUserDir")
                    if isinstance(value, str) and value.strip():
                        return Path(os.path.expandvars(value)).expanduser()
            except OSError:
                pass
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ValueError("APPDATA is not set. Specify the Weasel user directory with --user-dir.")
    return Path(appdata) / "Rime"


def default_user_dir(target: str) -> Path:
    if target == "windows":
        return windows_user_dir()
    if target == "macos":
        return Path.home() / "Library" / "Rime"
    if target == "linux":
        return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "fcitx5/rime"
    raise ValueError("Unsupported OS. Specify --platform and --user-dir explicitly.")


def yaml_module():
    try:
        import yaml
    except ImportError as exc:
        raise ValueError("Missing PyYAML. Run: python -m pip install -r requirements-rime.txt") from exc
    return yaml


def load_config(text: str):
    yaml = yaml_module()

    class UniqueLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        # Reject authored duplicate keys rather than silently discard user settings.
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = loader.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise ValueError(f"Duplicate YAML key: {key!r}")
                seen.add(key)
            except TypeError as exc:
                raise ValueError("Complex YAML mapping keys are not supported.") from exc
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
    try:
        result = yaml.load(text, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"Cannot parse existing Rime configuration: {exc}") from exc
    if result is None:
        return {}
    if not isinstance(result, dict):
        raise ValueError("Rime configuration must be a YAML mapping; existing file was preserved.")
    return result


def merge_schema_config(original: bytes, schema_ids=None) -> bytes:
    """Append a Rime schema using the documented /+ patch operator.

    Keep all existing patch values, including schema lists, and refuse malformed
    input. Backups preserve original comments/formatting, which PyYAML normalizes.
    """
    try:
        text = original.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("default.custom.yaml must be UTF-8; existing file was preserved.") from exc
    config = load_config(text)
    patch = config.setdefault("patch", {})
    if patch is None:
        patch = config["patch"] = {}
    if not isinstance(patch, dict):
        raise ValueError("The default.custom.yaml patch must be a mapping.")
    requested = [SCHEMA_ID] if schema_ids is None else list(dict.fromkeys(schema_ids))
    present = set()
    for key, value in patch.items():
        if not isinstance(key, str):
            raise ValueError("Rime patch keys must be strings.")
        if key == "schema_list" or key.startswith("schema_list/"):
            items = value if isinstance(value, list) else [value]
            present.update(item["schema"] for item in items if isinstance(item, dict)
                           and isinstance(item.get("schema"), str))
    missing = [schema for schema in requested if schema not in present]
    if not missing:
        return original
    additions = patch.setdefault("schema_list/+", [])
    if not isinstance(additions, list):
        raise ValueError("Existing schema_list/+ must be a list; existing file was preserved.")
    additions.extend({"schema": schema} for schema in missing)
    rendered = yaml_module().safe_dump(config, allow_unicode=True, sort_keys=False)
    return rendered.encode("utf-8")


def dictionary_bytes(root: Path = ROOT) -> bytes:
    """Create an isolated dictionary reproducibly without fetching the network.

    Preserve all dictionary entries and upstream acknowledgments. Use an isolated
    copy of the bundled essay preset, preserving the frontend's existing essay.
    """
    source = (root / "ime/data/luna_pinyin.dict.yaml").read_text(encoding="utf-8")
    source, count = re.subn(r"(?m)^name: luna_pinyin$", "name: shuangsheng", source, count=1)
    if count != 1:
        raise ValueError("Bundled Luna Pinyin dictionary has an unexpected name header.")
    source, count = re.subn(r"(?m)^use_preset_vocabulary: true$",
                            "use_preset_vocabulary: true\nvocabulary: shuangsheng_essay", source, count=1)
    if count != 1:
        raise ValueError("Bundled Luna Pinyin dictionary has an unexpected preset header.")
    return ("# Generated by Shuangsheng scripts/install_rime.py; see shuangsheng-NOTICE.md.\n"
            + source).encode("utf-8")


def jyutping_dictionary_bytes(root: Path = ROOT) -> bytes:
    """Only explicit, licensed readings are used; validate before any writes."""
    body = []
    for line in (root / "ime/data/jyutping.tsv").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith('#'):
            continue
        fields = line.split('\t')
        if len(fields) != 3 or not fields[0] or not re.fullmatch(r'[a-z]+[1-6](?: [a-z]+[1-6])*', fields[1]):
            raise ValueError('Malformed bundled Jyutping dictionary row')
        weight = float(fields[2])
        if not math.isfinite(weight) or weight < 0:
            raise ValueError('Invalid bundled Jyutping dictionary weight')
        body.append(line)
    if not body:
        raise ValueError('Bundled Jyutping dictionary is empty')
    header = ('# Derived from CanCLID rime-cantonese; CC BY 4.0. See shuangsheng-NOTICE.cantonese.\n'
              '---\nname: shuangsheng_jyutping\nversion: "2026.10.07"\nsort: by_weight\n'
              'use_preset_vocabulary: false\ncolumns: [text, code, weight]\n...\n')
    return (header + '\n'.join(body) + '\n').encode('utf-8')


def atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".shuangsheng-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(user_dir: Path, *, root: Path = ROOT, dry_run: bool = False, schemes=("pinyin",)) -> dict:
    """Validate everything before writing; retain unique backups of changed files."""
    if (not isinstance(schemes, (tuple, list)) or not schemes or
            any(not isinstance(item, str) or item not in {'pinyin', 'shuangpin', 'jyutping'} for item in schemes)):
        raise ValueError('schemes must contain pinyin, shuangpin, or jyutping')
    schemes = list(dict.fromkeys(schemes))
    identifiers = [SCHEMA_ID if item == 'pinyin' else SCHEMA_ID + '_' + item for item in schemes]
    user_dir = user_dir.expanduser().absolute()
    custom = user_dir / "default.custom.yaml"
    if custom.is_symlink():
        raise ValueError(f"Refusing to replace a symlink: {custom}")
    original = custom.read_bytes() if custom.exists() else b""
    payload = {
        "shuangsheng.schema.yaml": (root / "native/rime/shuangsheng.schema.yaml").read_bytes(),
        "shuangsheng.dict.yaml": dictionary_bytes(root),
        "shuangsheng_essay.txt": (root / "native/rime/essay.txt").read_bytes(),
        "shuangsheng-LICENSE.essay": (root / "native/rime/LICENSE.essay").read_bytes(),
        "shuangsheng-LICENSE.LGPL-3.0": (root / "ime/data/LICENSE.rime").read_bytes(),
        "shuangsheng-LICENSE.GPL-3.0": (root / "ime/data/LICENSE.GPL-3.0").read_bytes(),
        "shuangsheng-NOTICE.md": (root / "native/rime/NOTICE.md").read_bytes(),
        "default.custom.yaml": merge_schema_config(original, identifiers),
    }
    for scheme in schemes:
        if scheme != 'pinyin':
            name = 'shuangsheng_' + scheme + '.schema.yaml'
            payload[name] = (root / 'native/rime' / name).read_bytes()
    if 'jyutping' in schemes:
        payload['shuangsheng_jyutping.dict.yaml'] = jyutping_dictionary_bytes(root)
        payload['shuangsheng-LICENSE.cantonese'] = (root / 'ime/data/LICENSE.cantonese').read_bytes()
        payload['shuangsheng-NOTICE.cantonese'] = (
            'Readings: CanCLID and rime-cantonese contributors, CC BY 4.0.\n'
            'Source: https://github.com/rime/rime-cantonese\n'
            'Commit: 259f0e48bba840c3a2e0d117539e96937f3d89bc\n'
            'Changes: YAML metadata removed, only explicit readings retained, duplicates merged.\n'
            'Ranking frequencies: Rime Developers essay, LGPL-3.0 (GPL-3.0 incorporated).\n'
            'See shuangsheng-NOTICE.md and accompanying licenses for essay provenance.\n'
        ).encode('utf-8')
    changes = {}
    for name, content in payload.items():
        path = user_dir / name
        if path.is_symlink():
            raise ValueError(f"Refusing to replace a symlink: {path}")
        before = path.read_bytes() if path.exists() else None
        if before != content:
            changes[path] = (before, content)
    result = {"user_dir": str(user_dir), "changed": [p.name for p in changes],
              "backups": [], "dictionary_sha256": hashlib.sha256(payload["shuangsheng.dict.yaml"]).hexdigest()}
    if dry_run or not changes:
        return result
    user_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    # Make every backup first. An error here leaves the active configuration intact.
    for path, (before, _) in changes.items():
        if before is not None:
            backup = path.with_name(path.name + ".before-shuangsheng-" + stamp)
            shutil.copy2(path, backup)
            result["backups"].append(str(backup))
    written = []
    try:
        for path, (_, content) in changes.items():
            atomic_write(path, content)
            written.append(path)
    except OSError:
        for path in reversed(written):
            before = changes[path][0]
            if before is None:
                path.unlink()
            else:
                atomic_write(path, before)
        raise
    return result


def main(argv=None) -> int:
    # Redirected Windows consoles otherwise use a legacy code page, including
    # CI and users invoking the installer from a non-Chinese terminal.
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("windows", "macos", "linux"), default=current_platform())
    parser.add_argument("--user-dir", type=Path, help="Override the OS frontend's Rime user directory")
    parser.add_argument("--schemes", nargs="+", choices=("pinyin", "shuangpin", "jyutping"), default=["pinyin"])
    parser.add_argument("--dry-run", action="store_true", help="Validate and list changes without writing")
    args = parser.parse_args(argv)
    try:
        user_dir = args.user_dir if args.user_dir is not None else default_user_dir(args.platform)
        result = install(user_dir, dry_run=args.dry_run, schemes=args.schemes)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(("Would install into: " if args.dry_run else "Installed into: ") + result["user_dir"])
    print("Changed: " + (", ".join(result["changed"]) or "none (already installed)"))
    for backup in result["backups"]:
        print("Backup: " + backup)
    if not args.dry_run:
        frontend = {"windows": "Weasel", "macos": "Squirrel", "linux": "Fcitx5-Rime"}.get(args.platform, "Rime")
        print(f"In {frontend}, run Redeploy, then Ctrl+` or F4 and select the installed Shuangsheng scheme.")
        print("Requires the native Rime frontend. Installation does not register a new OS keyboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

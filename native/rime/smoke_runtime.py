"""Run the real librime integration test in a disposable Rime user directory."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.install_rime import install


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--opencc-dir", type=Path, default=Path("/usr/share/opencc"))
    args = parser.parse_args()
    if not (args.opencc_dir / "t2s.json").is_file():
        parser.error("--opencc-dir must contain OpenCC's t2s.json and its dictionaries")
    with tempfile.TemporaryDirectory(prefix="shuangsheng-rime-") as directory:
        temporary = Path(directory)
        shared = temporary / "shared"
        user = temporary / "user"
        shared.mkdir()
        shutil.copytree(args.opencc_dir, shared / "opencc")
        (shared / "default.yaml").write_text(
            'config_version: "1"\nschema_list:\n  - schema: existing\n'
            'switcher:\n  caption: Test\n  hotkeys: [Control+grave]\n'
            'ascii_composer:\n  switch_key:\n    Shift_L: commit_code\n'
            'menu:\n  page_size: 9\n', encoding="utf-8")
        (shared / "existing.schema.yaml").write_text(
            'schema:\n  schema_id: existing\n  name: Existing test keyboard\n  version: "1"\n'
            'engine:\n  processors: [ascii_composer]\n'
            '  segmentors: [ascii_segmentor]\n  translators: [echo_translator]\n', encoding="utf-8")
        install(user, schemes=("pinyin", "shuangpin", "jyutping"))
        subprocess.run([str(args.executable.resolve()), str(user), str(shared)], check=True)
        print("Installed dictionary and schema validated using the native librime engine.")


if __name__ == "__main__":
    main()

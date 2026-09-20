"""Record native runtimes and collect the exact pinned PyAV build sources.

Network access occurs only in collect_native_sources, during release assembly.
The build recipe is archived as data and is never executed by this module.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ctypes
import ctypes.util
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_runtime_provenance() -> dict:
    import av
    import tkinter

    distribution = importlib.metadata.distribution("av")
    libraries = []
    codec = None
    for relative in distribution.files or ():
        name = relative.name.lower()
        if not (name.endswith((".dll", ".dylib")) or ".so" in name):
            continue
        path = Path(distribution.locate_file(relative))
        if not path.is_file():
            continue
        libraries.append({"file": str(relative), "sha256": sha256(path)})
        if re.match(r"(?:lib)?avcodec(?:[-.]|$)", name):
            codec = path
    if codec is None:
        raise RuntimeError("Cannot identify the bundled PyAV avcodec library for provenance")
    library = ctypes.CDLL(str(codec))
    reported = {}
    for suffix in ("license", "configuration"):
        function = getattr(library, "avcodec_" + suffix)
        function.restype = ctypes.c_char_p
        reported[suffix] = function().decode("utf-8")
    # Tcl() does not load Tk or require a graphical display.
    tcl = tkinter.Tcl()
    # Importing sounddevice calls Pa_Initialize(), which can block while probing
    # a machine's audio server. Query the version without opening audio devices.
    audio_distribution = importlib.metadata.distribution("sounddevice")
    if platform.system() == "Windows":
        architecture = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else f"{ctypes.sizeof(ctypes.c_void_p) * 8}bit"
        filename = f"libportaudio{architecture}" + ("-asio" if "SD_ENABLE_ASIO" in os.environ else "") + ".dll"
    else:
        filename = "libportaudio.dylib"
    portaudio_path = ctypes.util.find_library("portaudio")
    portaudio_path = portaudio_path or next((str(audio_distribution.locate_file(item)) for item in audio_distribution.files or ()
                                           if item.name == filename), None)
    if not portaudio_path:
        raise RuntimeError("Cannot identify the PortAudio runtime for provenance")
    portaudio = ctypes.CDLL(portaudio_path)
    portaudio.Pa_GetVersion.restype = ctypes.c_int
    portaudio.Pa_GetVersionText.restype = ctypes.c_char_p
    portaudio_version = portaudio.Pa_GetVersion()
    portaudio_description = portaudio.Pa_GetVersionText().decode("utf-8")
    return {
        "python": {"version": platform.python_version(),
                   "source": f"https://www.python.org/ftp/python/{platform.python_version()}/Python-{platform.python_version()}.tar.xz"},
        "tcl": {"version": tcl.call("info", "patchlevel"),
                "source_project": "https://github.com/tcltk/tcl"},
        "tk": {"abi_version": str(tkinter.TkVersion),
               "source_project": "https://github.com/tcltk/tk",
               "note": "Tk patch level is not inferred from its ABI version; no GUI was opened."},
        "portaudio": {"version_number": portaudio_version, "description": portaudio_description,
                      "source_project": "https://github.com/PortAudio/portaudio"},
        "pyav": {"version": av.__version__, "ffmpeg_version": av.ffmpeg_version_info,
                 "library_versions": av.library_versions,
                 "avcodec_reported_license": reported["license"],
                 "avcodec_configuration": reported["configuration"],
                 "binaries": libraries,
                 "license_note": "Runtime-reported FFmpeg license does not describe every bundled codec. See THIRD_PARTY.md and matching sources."},
    }


def download_verified(record, cache):
    filename = record.get("filename") or urllib.parse.urlparse(record["url"]).path.rsplit("/", 1)[-1]
    if not filename or Path(filename).name != filename:
        raise ValueError("Invalid source archive filename")
    # Prefix avoids collisions between projects with generic tag filenames.
    filename = record["name"] + "-" + filename
    path = cache / filename
    if path.is_file() and sha256(path) == record["sha256"]:
        return path
    failure = None
    for attempt in range(3):
        temporary = None
        try:
            request = urllib.request.Request(record["url"], headers={"User-Agent": "Shuangsheng-source-archive/1.0"})
            with urllib.request.urlopen(request, timeout=90) as source:
                with tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
                    temporary = Path(output.name)
                    shutil.copyfileobj(source, output)
            actual = sha256(temporary)
            if actual != record["sha256"]:
                raise ValueError(f"Source checksum mismatch for {record['name']}: {actual}")
            temporary.replace(path)
            return path
        except (OSError, ValueError) as exc:
            failure = exc
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"Cannot archive exact source for {record['name']}: {failure}") from failure


def collect_native_sources(destination: Path, cache: Path | None = None) -> dict:
    """Fetch verified sources plus exact recipe/patches; fail on missing archives.

    All packages in the upstream recipe are retained, including platform-specific
    build inputs and GPL codecs. This avoids pretending an LGPL runtime string
    describes the whole wheel. Installed pynput's matching LGPL source is added.
    """
    import av

    manifest = json.loads((ROOT / "packaging/pyav-sources.json").read_text(encoding="utf-8"))
    if av.__version__ != manifest["pyav_version"] or av.ffmpeg_version_info != manifest["ffmpeg_version"]:
        raise RuntimeError("PyAV/FFmpeg changed: update and review the pinned source manifest before releasing")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    cache = Path(cache) if cache is not None else ROOT / ".cache/third-party-sources"
    cache.mkdir(parents=True, exist_ok=True)
    records = list(manifest["sources"])
    pynput_version = importlib.metadata.version("pynput")
    with urllib.request.urlopen(f"https://pypi.org/pypi/pynput/{pynput_version}/json", timeout=60) as response:
        package = json.load(response)
    source = next(item for item in package["urls"] if item["packagetype"] == "sdist")
    records.append({"name": "pynput", "version": pynput_version, "url": source["url"],
                    "sha256": source["digests"]["sha256"], "filename": source["filename"]})
    with ThreadPoolExecutor(max_workers=4) as workers:
        archives = list(workers.map(lambda record: download_verified(record, cache), records))
    copied = []
    for record, archive in zip(records, archives):
        shutil.copy2(archive, destination / archive.name)
        copied.append(dict(record, archive=archive.name))
    manifest["sources"] = copied
    manifest["scope"] = "Exact upstream recipe source inputs, patches and PyAV/pynput source. System toolchain components are recorded separately, not claimed as a complete reproducible toolchain."
    (destination / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(ROOT / "packaging/THIRD_PARTY.md", destination / "THIRD_PARTY.md")
    shutil.copytree(ROOT / "packaging/licenses", destination / "licenses", dirs_exist_ok=True)
    return manifest

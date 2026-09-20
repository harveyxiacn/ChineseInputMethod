# Native runtime notices and corresponding source

Shuangsheng application source is under the project MIT license. Third-party
components retain their own licenses. A Python wheel's package license does not
necessarily cover all native libraries inside that wheel.

Release archives include the resolved Python package metadata and supplied
license files in `release-metadata/dependency-licenses/`. The native runtime
report records actual Python, Tcl, PortAudio, PyAV and FFmpeg versions, the
FFmpeg build configuration and SHA-256 hashes of the PyAV native binaries.

## PyAV and FFmpeg

This release pipeline pins PyAV **18.1.0**, whose official wheel recipe points to
the **8.1.2-1** PyAV FFmpeg build, based on FFmpeg **8.1.2**:

- [PyAV's exact vendor selection](https://github.com/PyAV-Org/PyAV/blob/7e3d950a8b72062502c1a60d672f8ca565313af5/scripts/ffmpeg-latest.json)
- [Exact build recipes and source hashes](https://github.com/PyAV-Org/pyav-ffmpeg/tree/a71bf9279f7a4659154b68ba6783e89be460bcd5)
- [FFmpeg upstream source archive](https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz)

The release's `third-party-sources/` directory contains the PyAV source, that
exact recipe with its patches, and every source archive listed in the recipe's
`scripts/pkg.py`. `source-manifest.json` records original URLs and verified
SHA-256 hashes. Source downloads with missing or mismatching hashes fail the
release build. Sources are included beside the application, with their original
copyright and license files inside each archive. The source bundles also retain
platform-specific build inputs that may not appear in every OS binary.

PyAV's wrapper is BSD-3-Clause. FFmpeg has LGPL/GPL licensing depending on its
components; this wheel also contains **GPL-licensed x264 and x265**. The recipe's
`patches/ffmpeg.patch` changes FFmpeg's license configuration checks for these
codecs. Consequently the native library's reported `LGPL version 3 or later`
string must not be read as an LGPL-only statement about the entire bundled
distribution. GPL and LGPL texts are supplied in `third-party-sources/licenses/`
(or the adjacent `licenses/` when reading this copy inside `third-party-sources/`); the exact upstream
sources and patches are retained. See [FFmpeg's own licensing description](https://ffmpeg.org/legal.html).

Shuangsheng does not add restrictions on modifying, rebuilding, reverse
engineering or replacing these libraries. The `source/` directory provides the
application and packaging code. FFmpeg and codec libraries remain separate
shared-library files in the application bundle. Preserve the applicable license
terms when redistributing modified or combined versions.

The upstream build recipe is the recipe used by PyAV, not a promise of bit-for-bit
reproduction: the upstream CI image, compiler and system libraries can vary.
Its `scripts/cibuildpkg.py` also retrieves GNU config scripts from a moving GCC
branch. Our source manifest makes this boundary explicit; it does not invent
unavailable compiler or system-runtime source identities.

## Python, Tcl/Tk and PortAudio

The release copies the Python runtime's own `LICENSE.txt`; the manifest links the
source release matching `platform.python_version()`. Tcl/Tk and PortAudio use
permissive licenses, supplied in `source/packaging/licenses/` and `third-party-sources/licenses/`
notices. The preserved texts were obtained from:

- [Tcl 8.6.16 license](https://github.com/tcltk/tcl/blob/core-8-6-16/license.terms)
- [Tk 8.6.16 license](https://github.com/tcltk/tk/blob/core-8-6-16/license.terms)
- [PortAudio 19.7.0 license and component notices](https://github.com/PortAudio/portaudio/blob/v19.7.0/LICENSE.txt)

These license-reference versions do not assert the runtime uses those exact
patch versions. The native manifest records Tcl's actual patch level and
PortAudio's actual version description. Tk's ABI version is recorded without
claiming a patch level from it. Python's platform distribution and sounddevice's
wheel provide these runtime binaries; the Python/wheel metadata identifies their
installed versions. Actual third-party component notices found in those
distributions are retained alongside these supplemental license texts.

## pynput and dictionaries

The source archive matching the installed LGPL-licensed pynput release is
downloaded from PyPI with its published SHA-256 and included in
`third-party-sources/`. Rime dictionary/Essay source and licenses are separately
included in `source/ime/data/` and `source/native/rime/`; their provenance is
documented in those directories. Whisper model weights are downloaded separately
at the user's request and are not included in the executable archive.

# Shuangsheng Rime dictionary

The installed `shuangsheng.dict.yaml` is generated reproducibly from the bundled
`ime/data/luna_pinyin.dict.yaml` from Rime Developers. Its original source header,
acknowledgments and dictionary records are preserved. Changes are limited to an
added provenance comment, renaming the dictionary to `shuangsheng`, and pointing
the preset vocabulary to `shuangsheng_essay`. The schema and dictionary do not
depend on a preinstalled Luna Pinyin schema or write to its user dictionaries.

Upstream: <https://github.com/rime/rime-luna-pinyin>

The dictionary is licensed under GNU LGPL version 3, including the incorporated
GPL version 3 terms. Copies accompany this installation as
`shuangsheng-LICENSE.LGPL-3.0` and `shuangsheng-LICENSE.GPL-3.0`.
The source dictionary and generation script are included in Shuangsheng source
and release archives. See `ime/data/ATTRIBUTION.md` for the bundled source's
retrieval date and upstream acknowledgments.

The bundled `essay.txt` (installed unchanged as `shuangsheng_essay.txt`) is the
Rime Developers' shared vocabulary and frequency data, under LGPL-3.0, with
its upstream license in `LICENSE.essay` (installed as `shuangsheng-LICENSE.essay`).
It was downloaded on 2026-09-20 from the fixed upstream commit:
<https://github.com/rime/rime-essay/tree/e2652ea18609a879eae3e87db5d25b7fbc1a4f93>.
The complete plaintext source is bundled; no download is needed during install.
SHA-256 of the bundled essay source:
`e62fb950175ff5c33bff4480a8d96f7b4fa83e9a9420d32a596877aa61257ead`.

`shuangsheng.schema.yaml` and the installer are original Shuangsheng project
files under the project's MIT license. Rime frontends and librime are separate
dependencies, distributed by their respective upstream projects.

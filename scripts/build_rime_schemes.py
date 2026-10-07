"""Reproducibly build authored Xiaohe/Jyutping schemas without network access."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ime.pinyin import normalize, shuangpin_code


def schema_bytes(root: Path = ROOT) -> dict[str, bytes]:
    import yaml
    base = yaml.safe_load((root / 'native/rime/shuangsheng.schema.yaml').read_text(encoding='utf-8'))
    syllables = set()
    for filename in ('starter.tsv', 'common.tsv', 'rime.tsv'):
        for line in (root / 'ime/data' / filename).read_text(encoding='utf-8').splitlines():
            if line and not line.startswith('#'):
                syllables.update(normalize(line.split('\t')[2]).split())
    mapped = {syllable: shuangpin_code(syllable) for syllable in syllables
              if len(shuangpin_code(syllable)) == 2}
    double = deepcopy(base)
    double['schema'].update(schema_id='shuangsheng_shuangpin', name='双声小鹤双拼',
                            description='Xiaohe double pinyin with sentence composition and a private learning dictionary.')
    # Rime's prism transforms dictionary full-pinyin syllables into input codes.
    # Protect each intermediate output so later anchored rules cannot remap it.
    double['speller']['algebra'] = [f'xform/^{syllable}$/X{code}X/' for syllable, code in sorted(mapped.items())]
    double['speller']['algebra'] += ['xform/^X(.*)X$/$1/', 'abbrev/^([a-z]).+$/$1/']
    double['translator'].update(prism='shuangsheng_shuangpin', user_dict='shuangsheng_shuangpin')
    double['translator'].pop('preedit_format', None)
    cantonese = deepcopy(base)
    cantonese['schema'].update(schema_id='shuangsheng_jyutping', name='双声粤拼',
                               description='Verified CanCLID Jyutping readings, numbered tones or toneless input; private learning dictionary.')
    cantonese['speller'].update(alphabet='abcdefghijklmnopqrstuvwxyz123456',
                                algebra=['derive/[1-6]//', 'abbrev/^([a-z]).+$/$1/'])
    cantonese['translator'].update(dictionary='shuangsheng_jyutping', prism='shuangsheng_jyutping',
                                   user_dict='shuangsheng_jyutping')
    cantonese['translator'].pop('preedit_format', None)
    # Keep unmodified digits for tones; Control+digit reaches Rime's selector
    # because speller ignores control-modified keys.
    cantonese['key_binder']['bindings'] += [
        {'when': 'has_menu', 'accept': f'Alt+{number}', 'send': f'Control+{number}'}
        for number in range(1, 10)]
    header = '# Authored Shuangsheng schema; MIT. See project LICENSE.\n'
    return {f'shuangsheng_{name}.schema.yaml':
            (header + yaml.safe_dump(schema, allow_unicode=True, sort_keys=False)).encode('utf-8')
            for name, schema in [('shuangpin', double), ('jyutping', cantonese)]}


if __name__ == '__main__':
    for filename, content in schema_bytes().items():
        output = ROOT / 'native/rime' / filename
        output.write_bytes(content)
        print(f'Wrote {output}')

"""Protocol failures, process recovery, and concurrent request isolation."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import sys
import tempfile
import time
import unittest

from ime.native_pinyin import _Bridge


@unittest.skipIf(os.name == 'nt', 'Optional native bridge fixture needs POSIX executable scripts')
class NativeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.bridge = None

    def tearDown(self):
        if self.bridge: self.bridge.close()
        self.directory.cleanup()

    def make_bridge(self, body, timeout=1):
        script = self.root / 'decoder'
        script.write_text('#!' + sys.executable + '\n' + body, encoding='utf-8')
        script.chmod(0o700)
        self.bridge = _Bridge(script, timeout=timeout)
        return self.bridge

    def test_timeout_discards_partial_response_and_restarts(self):
        marker = self.root / 'started'
        program = '''import pathlib,sys,time
marker=pathlib.Path(sys.argv[0]).with_name('started')
first=not marker.exists()
marker.write_text(str(int(marker.read_text())+1) if marker.exists() else '1')
for line in sys.stdin:
    text='旧' if first else '新'
    print(text.encode().hex()+'\\t'+b'xin'.hex(),flush=True)
    if first: time.sleep(2)
    print('.',flush=True)
'''
        bridge = self.make_bridge(program, timeout=0.15)
        self.assertEqual(bridge.decode('xin', 'pinyin', '', 9), [])
        self.assertIsNone(bridge.process)
        self.assertEqual(bridge.decode('xin', 'pinyin', '', 9), [{'text': '新', 'pinyin': 'xin'}])
        self.assertEqual(marker.read_text(), '2')

    def test_bad_protocol_kills_process(self):
        bridge = self.make_bridge("import sys\nfor line in sys.stdin:\n print('not-a-response',flush=True)\n print('.',flush=True)\n")
        self.assertEqual(bridge.decode('nihao', 'pinyin', '', 9), [])
        self.assertIsNone(bridge.process)

    def test_concurrent_requests_preserve_exact_text_and_newlines(self):
        program = '''import sys
for line in sys.stdin:
    query,scheme,context,count=line.rstrip('\\n').split('\\t')
    print(query+'\\t'+b'echo'.hex(),flush=True)
    print('.',flush=True)
'''
        bridge = self.make_bridge(program)
        queries = ['中文\n标点!?', 'ABC@x.example', 'hello', '123', 'nihao']
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda q: bridge.decode(q, 'pinyin', '前文\n内容', 9), queries))
        self.assertEqual([r[0]['text'] for r in results], queries)
        bridge.close(); self.assertIsNone(bridge.process)

    def test_lock_wait_is_bounded(self):
        bridge = self.make_bridge('import sys\n', timeout=0.05)
        with bridge.lock, ThreadPoolExecutor(max_workers=1) as pool:
            started = time.monotonic()
            result = pool.submit(bridge.decode, 'nihao', 'pinyin', '', 9).result(timeout=0.5)
            self.assertEqual(result, [])
            self.assertLess(time.monotonic() - started, 0.3)
            self.assertIsNone(bridge.process)

    def test_exited_process_starts_fresh(self):
        program = "import sys\nline=sys.stdin.readline()\nprint('e4bda0e5a5bd\\t6e692768616f',flush=True)\nprint('.',flush=True)\n"
        bridge = self.make_bridge(program)
        self.assertEqual(bridge.decode('nihao', 'pinyin', '', 9)[0]['text'], '你好')
        bridge.process.wait(timeout=1)
        self.assertEqual(bridge.decode('nihao', 'pinyin', '', 9)[0]['pinyin'], 'ni hao')


if __name__ == '__main__':
    unittest.main()

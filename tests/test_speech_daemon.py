import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from ime.speech import SpeechError
from ime.speech_daemon import SpeechWorker, SpeechDaemonClient, private_directory, receive_json, send_json, MAX_RESPONSE


@unittest.skipUnless(hasattr(socket,'SO_PEERCRED'),'Linux IPC only')
class SpeechDaemonTests(unittest.TestCase):
    def call(self, worker, request, audio=b''):
        client,server=socket.socketpair()
        thread=threading.Thread(target=worker.handle,args=(server,));thread.start()
        try:
            send_json(client,request)
            if audio:client.sendall(audio)
            client.shutdown(socket.SHUT_WR)
            return receive_json(client,MAX_RESPONSE)
        finally:
            client.close();thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    def test_worker_reuses_service_and_rejects_paths(self):
        service=Mock();service.transcribe.return_value={'text':'保留 123'}
        worker=SpeechWorker(service)
        for _ in range(2):
            result=self.call(worker,{'op':'transcribe','audio_bytes':3,'language':'auto','fast':True},b'abc')
            self.assertEqual(result,{'ok':True,'result':{'text':'保留 123'}})
        self.assertEqual(service.transcribe.call_count,2)
        bad=self.call(worker,{'op':'transcribe','audio_bytes':3,'model':'/etc/passwd'},b'abc')
        self.assertFalse(bad['ok']);self.assertEqual(service.transcribe.call_count,2)

    def test_invalid_lengths_types_and_truncation(self):
        worker=SpeechWorker(Mock())
        for request in [{'op':'transcribe','audio_bytes':True},{'op':'transcribe','audio_bytes':-1},
                        {'op':'transcribe','audio_bytes':30*1024*1024},{'op':'status','audio_bytes':1},
                        {'op':'transcribe','audio_bytes':3,'fast':'yes'}, {'op':'unknown','audio_bytes':0},
                        {'op':'transcribe','audio_bytes':3}]:
            self.assertFalse(self.call(worker,request)['ok'])
        worker.service.transcribe.assert_not_called()

    def test_private_runtime_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'worker'
            self.assertEqual(private_directory(target),target)
            self.assertEqual(target.stat().st_mode & 0o777,0o700)
            link=Path(directory)/'link';link.symlink_to(target)
            with self.assertRaises(SpeechError):private_directory(link)
            target.chmod(0o755)
            with self.assertRaises(SpeechError):private_directory(target)

    def test_status_does_not_spawn_and_hotword_list_normalized(self):
        client=SpeechDaemonClient()
        with patch.object(client,'_connection',return_value=None) as connect,patch('ime.speech_daemon.SpeechService') as service:
            service.return_value.status.return_value={'loaded':False}
            self.assertEqual(client.status(),{'loaded':False,'worker_running':False})
            connect.assert_called_once_with(False)
        with patch.object(client,'_request',return_value={'text':'双声'}) as request:
            client.transcribe(b'abc',hotwords=['双声','API'])
            self.assertEqual(request.call_args.kwargs['hotwords'],'双声, API')
        with self.assertRaises(SpeechError):client.transcribe(b'abc',hotwords=['valid',1])

    def test_status_polling_does_not_keep_idle_model_loaded(self):
        service = Mock()
        service.status.return_value = {'loaded': True}
        worker = SpeechWorker(service)
        worker.last_used = 10
        with patch('ime.speech_daemon.time.monotonic', return_value=20):
            self.assertTrue(self.call(worker, {'op': 'status'})['ok'])
            self.assertEqual(worker.last_used, 10)
            worker.unload_if_idle(5)
            service.unload.assert_called_once_with()
            self.assertEqual(worker.last_used, 20)
            worker.unload_if_idle(5)
            service.unload.assert_called_once_with()
        worker.last_used = 10
        service.unload.side_effect = SpeechError('busy', 429)
        with patch('ime.speech_daemon.time.monotonic', return_value=20):
            worker.unload_if_idle(5)
            self.assertEqual(worker.last_used, 10)

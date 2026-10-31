import tempfile
import unittest
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

from voicehub.recording import (Recordings, _wav_header, command, duration_text,
                                normalize)


class RecordingCommandTests(unittest.TestCase):
    def test_start_variants(self):
        for text in ('开始录音模式', '开始录音', '现在开始录音模式。', '帮我开始录音吧',
                     '进入录音模式', '打开录音', '录音模式', '我想开始录音'):
            self.assertEqual(command(text), 'start', text)

    def test_stop_and_confirm_variants(self):
        for text in ('停止录音模式', '结束录音', '停止录音。', '关闭录音模式',
                     '退出录音模式', '别录了', '停止录音并保存'):
            self.assertEqual(command(text), 'stop', text)
        for text in ('确认停止', '确认结束录音', '确定停止录音模式', '确认',
                     '确定', '确认停止并保存'):
            self.assertEqual(command(text), 'confirm', text)

    def test_mentions_and_questions_are_not_commands(self):
        for text in ('录音功能怎么用', '我想了解一下录音模式', '这个录音能保存多久',
                     '刚才开始录音了吗', '不要停止录音', '可以开始录音吗', '', '你好'):
            self.assertIsNone(command(text), text)

    def test_normalize_removes_asr_punctuation(self):
        self.assertEqual(normalize('开 始 录 音，模 式。'), '开始录音模式')
        self.assertEqual(normalize('停止录音模式！'), '停止录音模式')

    def test_duration_text(self):
        self.assertEqual(duration_text(0), '0秒')
        self.assertEqual(duration_text(59), '59秒')
        self.assertEqual(duration_text(60), '1分钟')
        self.assertEqual(duration_text(125), '2分5秒')
        self.assertEqual(duration_text(3600), '1小时')


class RecordingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.manager = Recordings(self.root)

    def tearDown(self):
        self.manager.close()
        self.tmp.cleanup()

    def test_voice_memo_roundtrip_writes_a_valid_wav(self):
        recording = self.manager.start('session-a', 'browser', 'voice')
        self.assertIsNone(recording.write(b'\x01\x00'*16000))
        metadata = self.manager.stop(recording.id, 'web')
        self.assertEqual(metadata['status'], 'stopped')
        self.assertEqual(metadata['reason'], 'web')
        self.assertEqual(metadata['duration_ms'], 1000)
        self.assertEqual(metadata['samples'], 16000)
        self.assertEqual(metadata['download_url'], f'/api/recordings/{recording.id}/audio')
        path = self.manager.audio_path(recording.id)
        self.assertTrue(path.is_file())
        with wave.open(str(path), 'rb') as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getframerate(), 16000)
            self.assertEqual(wav.getnframes(), 16000)
            self.assertEqual(wav.readframes(16000), b'\x01\x00'*16000)
        self.assertEqual(self.manager.get(recording.id)['id'], recording.id)
        self.assertEqual(len(self.manager.list()), 1)
        self.assertFalse(self.manager.active)

    def test_transcript_uses_one_line_per_pause_and_is_deleted_with_audio(self):
        recording = self.manager.start('session-t', 'browser', 'voice')
        self.assertEqual(recording.append_transcript('第一句话'), (1, '第一句话'))
        self.assertEqual(recording.append_transcript('第二句话。'), (2, '第二句话。'))
        self.assertIsNone(recording.append_transcript('  \n  '))
        active = self.manager.get(recording.id)
        self.assertTrue(active['transcript_available'])
        self.assertEqual(active['transcript_lines'], 2)
        transcript = self.manager.transcript(recording.id)
        self.assertEqual(transcript['text'], '第一句话\n第二句话。')
        self.assertEqual(transcript['count'], 2)
        transcript_path = recording.transcript_path
        self.assertTrue(transcript_path.is_file())
        metadata = self.manager.stop(recording.id, 'web')
        self.assertTrue(metadata['transcript_available'])
        self.assertEqual(metadata['transcript_url'],
                         f'/api/recordings/{recording.id}/transcript')
        self.assertEqual(self.manager.transcript(recording.id)['status'], 'stopped')
        self.manager.delete(recording.id)
        self.assertFalse(transcript_path.exists())
        self.assertIsNone(self.manager.transcript(recording.id))

    def test_duration_cap_writes_no_more_than_configured_seconds(self):
        manager = Recordings(self.root, max_seconds=1)
        recording = manager.start('session-b', 'device', 'voice')
        self.assertIsNone(recording.write(b'\0\0'*8000))
        self.assertEqual(recording.write(b'\0\0'*8000), 'limit')
        self.assertEqual(recording.write(b'\0\0'*8000), 'limit')
        self.assertEqual(recording.samples, 16000)
        manager.stop(recording.id, 'limit')
        metadata = manager.get(recording.id)
        self.assertEqual(metadata['duration_ms'], 1000)

    def test_active_file_cannot_be_downloaded_or_deleted(self):
        recording = self.manager.start('session-c', 'browser', 'web')
        self.assertIsNone(self.manager.audio_path(recording.id))
        with self.assertRaises(ValueError):
            self.manager.delete(recording.id)

    def test_delete_removes_audio_and_metadata(self):
        recording = self.manager.start('session-d', 'browser', 'web')
        recording.write(b'\0\0'*320)
        self.manager.stop(recording.id, 'web')
        path = self.manager.audio_path(recording.id)
        self.manager.delete(recording.id)
        self.assertFalse(path.exists())
        self.assertFalse(self.manager.metadata_path(recording.id).exists())
        self.assertIsNone(self.manager.get(recording.id))

    def test_disk_full_refuses_to_start(self):
        usage = type('Usage', (), {'free': 1})()
        with patch('voicehub.recording.shutil.disk_usage', return_value=usage):
            with self.assertRaises(ValueError):
                self.manager.start('session-e', 'browser', 'web')

    def test_crash_recovery_repairs_header_and_creates_sidecar(self):
        identity = uuid.uuid4().hex
        path = self.manager.root/f'{identity}.wav'
        path.write_bytes(_wav_header()+b'\0\0'*3200)
        (self.manager.root/f'{identity}.txt').write_text('崩溃前的一行\n', encoding='utf-8')
        recovered = Recordings(self.root)
        self.addCleanup(recovered.close)
        metadata = recovered.get(identity)
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata['status'], 'recovered')
        self.assertEqual(metadata['samples'], 3200)
        self.assertTrue(metadata['transcript_available'])
        self.assertEqual(metadata['transcript_lines'], 1)
        self.assertEqual(recovered.transcript(identity)['text'], '崩溃前的一行')
        with wave.open(str(path), 'rb') as wav:
            self.assertEqual(wav.getnframes(), 3200)

    def test_second_start_in_same_session_is_rejected(self):
        self.manager.start('session-f', 'browser', 'voice')
        with self.assertRaises(ValueError):
            self.manager.start('session-f', 'browser', 'voice')

    def test_invalid_identity_never_escapes_the_recording_directory(self):
        self.assertIsNone(self.manager.get('../settings'))
        self.assertIsNone(self.manager.audio_path('../settings'))
        with self.assertRaises(ValueError):
            self.manager.delete('../settings')

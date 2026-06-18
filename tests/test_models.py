"""Tests for app/models.py — VideoRecord serialization and slot hygiene.

Verifies that VideoRecord's __slots__ correctly separates public state (persisted
in JSON) from private state (_pending_subs, used only at runtime).
"""
import unittest

from app.models import VideoRecord


class TestVideoRecordCreation(unittest.TestCase):
    def test_basic_creation(self):
        r = VideoRecord('Hello', 'https://youtu.be/X', 'X', '/tmp/x.mp4', 1024, '2024-01-01')
        self.assertEqual(r.title, 'Hello')
        self.assertEqual(r.url, 'https://youtu.be/X')
        self.assertEqual(r.video_id, 'X')
        self.assertEqual(r.file_path, '/tmp/x.mp4')
        self.assertEqual(r.file_size, 1024)
        self.assertEqual(r.download_time, '2024-01-01')
        self.assertIsNone(r.telegram_file_id)
        self.assertEqual(r.media_type, 'video')
        self.assertIsNone(r._pending_subs)

    def test_creation_with_audio_media_type(self):
        r = VideoRecord('T', 'u', 'v', 'f', 1, 'd', media_type='audio')
        self.assertEqual(r.media_type, 'audio')

    def test_creation_with_telegram_file_id(self):
        r = VideoRecord('T', 'u', 'v', 'f', 1, 'd', telegram_file_id='ABC')
        self.assertEqual(r.telegram_file_id, 'ABC')

    def test_invalid_attribute_raises_due_to_slots(self):
        r = VideoRecord('T', 'u', 'v', 'f', 1, 'd')
        with self.assertRaises(AttributeError):
            r.not_a_slot = 'fail'


class TestVideoRecordSlots(unittest.TestCase):
    def test_pending_subs_in_slots(self):
        # Critical: _pending_subs must be in __slots__ so it can be set on the record
        self.assertIn('_pending_subs', VideoRecord.__slots__)

    def test_pending_subs_can_be_assigned(self):
        r = VideoRecord('T', 'u', 'v', 'f', 1, 'd')
        r._pending_subs = ['/tmp/sub1.srt', '/tmp/sub2.srt']
        self.assertEqual(r._pending_subs, ['/tmp/sub1.srt', '/tmp/sub2.srt'])

    def test_pending_subs_can_be_set_to_none(self):
        r = VideoRecord('T', 'u', 'v', 'f', 1, 'd')
        r._pending_subs = '/tmp/x.srt'
        r._pending_subs = None
        self.assertIsNone(r._pending_subs)


class TestVideoRecordSerialization(unittest.TestCase):
    def test_to_dict_excludes_pending_subs(self):
        r = VideoRecord('T', 'u', 'v', 'f', 1, 'd')
        r._pending_subs = ['should not appear in dict']
        d = r.to_dict()
        self.assertNotIn('_pending_subs', d)
        self.assertIn('title', d)
        self.assertEqual(d['title'], 'T')

    def test_to_dict_includes_all_public_fields(self):
        r = VideoRecord('Title', 'url', 'vid', 'path', 1024, 'time', media_type='audio',
                       telegram_file_id='FID')
        d = r.to_dict()
        for k in ('title', 'url', 'video_id', 'file_path', 'file_size',
                  'download_time', 'telegram_file_id', 'media_type'):
            self.assertIn(k, d)

    def test_from_dict_loads_public_fields(self):
        d = {
            'title': 'X', 'url': 'u', 'video_id': 'v', 'file_path': 'f',
            'file_size': 100, 'download_time': 'd',
            'telegram_file_id': None, 'media_type': 'video',
        }
        r = VideoRecord.from_dict(d)
        self.assertEqual(r.title, 'X')
        self.assertEqual(r.video_id, 'v')
        self.assertEqual(r.media_type, 'video')
        self.assertIsNone(r.telegram_file_id)
        # Pending subs was not saved — should default to None in the new record
        self.assertIsNone(r._pending_subs)

    def test_to_from_dict_roundtrip_preserves_public_fields(self):
        r = VideoRecord('Title', 'https://youtu.be/X', 'X', '/tmp/x.mp4', 2048,
                        '2024-01-01', media_type='audio', telegram_file_id='FID')
        d = r.to_dict()
        r2 = VideoRecord.from_dict(d)
        self.assertEqual(r.title, r2.title)
        self.assertEqual(r.url, r2.url)
        self.assertEqual(r.video_id, r2.video_id)
        self.assertEqual(r.file_path, r2.file_path)
        self.assertEqual(r.file_size, r2.file_size)
        self.assertEqual(r.media_type, r2.media_type)
        self.assertEqual(r.telegram_file_id, r2.telegram_file_id)

    def test_from_dict_with_minimal_public_fields_works(self):
        # Simulates JSON from older version that didn't have all fields
        minimal = {'title': 'X', 'url': 'u', 'video_id': 'v', 'file_path': 'f',
                   'file_size': 100, 'download_time': 'd'}
        r = VideoRecord.from_dict(minimal)
        self.assertEqual(r.title, 'X')
        self.assertEqual(r.media_type, 'video')
        self.assertIsNone(r.telegram_file_id)


if __name__ == '__main__':
    unittest.main()

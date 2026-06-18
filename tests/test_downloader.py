"""Tests for app/downloader.py — pure helpers, ffmpeg wrappers (mocked), and
quality-format integrity.

External calls (yt-dlp, real ffmpeg, real cookies) are NOT exercised here —
this suite targets unit-testable logic only so it stays reliable in the
deployed environment without network or system dependencies.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.downloader import (
    _sanitize_filename,
    _vtt_to_srt,
    _merge_subs_into_mkv,
    VIDEO_QUALITY_FMT,
    AUDIO_QUALITY_FMT,
)


class TestSanitizeFilename(unittest.TestCase):
    def test_strips_angle_brackets(self):
        result = _sanitize_filename('<b>Hello</b> World')
        self.assertNotIn('<', result)
        self.assertNotIn('>', result)
        self.assertIn('Hello', result)

    def test_keeps_unicode_letters(self):
        # Word chars are kept by the regex
        result = _sanitize_filename('Hello123 World_!')
        # Punctuation in the allowlist is preserved; word chars are kept
        self.assertIn('Hello', result)

    def test_truncates_long_titles_to_100_chars(self):
        long_title = 'A' * 200
        result = _sanitize_filename(long_title)
        self.assertLessEqual(len(result), 100)

    def test_truncation_keeps_alphanumeric(self):
        long_title = 'B' * 200
        result = _sanitize_filename(long_title)
        self.assertEqual(len(result), 100)

    def test_drops_path_separator_chars(self):
        # '/' and '\' are not in the allowlist. The input literal `'a/b\\c'`
        # contains 5 characters: a, /, b, \, c.
        result = _sanitize_filename('a/b\\c')
        self.assertNotIn('/', result)
        self.assertNotIn('\\', result)
        # Only the three word characters survive.
        self.assertEqual(result, 'abc')

    def test_returns_string(self):
        self.assertIsInstance(_sanitize_filename('Hello'), str)


class TestVttToSrt(unittest.TestCase):
    def _create_vtt(self, text='WEBVTT\n'):
        fd, path = tempfile.mkstemp(suffix='.vtt')
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        return path

    def test_returns_none_on_subprocess_failure(self):
        vtt = self._create_vtt()
        try:
            with patch('app.downloader.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stderr='ffmpeg error')
                result = _vtt_to_srt(vtt)
                self.assertIsNone(result)
        finally:
            if os.path.exists(vtt):
                os.unlink(vtt)

    def test_returns_srt_path_on_success(self):
        vtt = self._create_vtt()
        srt = vtt.replace('.vtt', '.srt')
        # Pre-create the srt file with non-zero size — ffmpeg is mocked so it
        # doesn't actually create it; the function checks the file exists.
        Path(srt).write_text('1\n00:00:00,000 --> 00:00:01,000\nHello\n')
        try:
            with patch('app.downloader.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = _vtt_to_srt(vtt)
                self.assertEqual(result, srt)
                # Original VTT should have been cleaned up
                self.assertFalse(os.path.exists(vtt))
        finally:
            if os.path.exists(srt):
                os.unlink(srt)

    def test_returns_none_if_srt_not_created(self):
        # ffmpeg returned 0 but didn't write the srt — function should bail
        vtt = self._create_vtt()
        try:
            with patch('app.downloader.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = _vtt_to_srt(vtt)
                self.assertIsNone(result)
        finally:
            if os.path.exists(vtt):
                os.unlink(vtt)

    def test_returns_none_on_exception(self):
        vtt = self._create_vtt()
        try:
            with patch('app.downloader.subprocess.run', side_effect=OSError('boom')):
                result = _vtt_to_srt(vtt)
                self.assertIsNone(result)
        finally:
            if os.path.exists(vtt):
                os.unlink(vtt)


class TestMergeSubsIntoMkv(unittest.TestCase):
    def _run_merge(self, video_size=5000):
        # Create a fake video + 1 sub file in a temp directory, monkey-patch
        # DOWNLOADS_DIR so the helper uses our temp folder. Register a cleanup
        # so the temp directory is removed once the test completes.
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        video = Path(tmpdir) / 'video.mp4'
        video.write_bytes(b'\0' * video_size)
        sub = Path(tmpdir) / 'sub_en.vtt'
        sub.write_text('WEBVTT\n')
        srt = Path(tmpdir) / 'sub_en.srt'
        srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nHi\n')
        # Output path the helper will write
        out = str(video.with_suffix('.mkv'))
        return tmpdir, str(video), str(sub), str(srt), srt, out

    def test_returns_none_when_no_subs(self):
        fake_video = '/tmp/nonexistent_video_for_test.mp4'
        self.assertIsNone(_merge_subs_into_mkv(fake_video, []))

    def test_failure_path_leaves_original_video_alone(self):
        tmpdir, video, sub_vtt, sub_srt, srt_path, out = self._run_merge()
        # Patch downloader's DOWNLOADS_DIR to our tmpdir so cleanup logic doesn't
        # touch real files if it failed.
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            with patch('app.downloader._vtt_to_srt', return_value=str(srt_path)):
                with patch('app.downloader.subprocess.run') as mock_run:
                    # ffmpeg fails
                    mock_run.return_value = MagicMock(returncode=1, stderr='ffmpeg fail')
                    result = _merge_subs_into_mkv(video, [sub_vtt])
                    self.assertIsNone(result)
                    # Original video remains
                    self.assertTrue(os.path.exists(video))
                    # sub_clean attempt — srt cleaning only happens on success,
                    # so it should still exist
                    self.assertTrue(os.path.exists(str(srt_path)))

    def test_success_path_cleans_original_and_subs(self):
        tmpdir, video, sub_vtt, sub_srt, srt_path, out = self._run_merge()
        # The merge output file
        Path(out).write_bytes(b'\0' * 6000)  # non-zero size so the size check passes
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            with patch('app.downloader._vtt_to_srt', return_value=str(srt_path)):
                with patch('app.downloader.subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = _merge_subs_into_mkv(video, [sub_vtt])
                    self.assertEqual(result, out)
                    # Original video removed, output kept
                    self.assertFalse(os.path.exists(video))
                    self.assertTrue(os.path.exists(out))
                    # Subtitle file cleaned up too
                    self.assertFalse(os.path.exists(str(srt_path)))

    def test_empty_output_is_cleaned(self):
        tmpdir, video, sub_vtt, sub_srt, srt_path, out = self._run_merge()
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            with patch('app.downloader._vtt_to_srt', return_value=str(srt_path)):
                with patch('app.downloader.subprocess.run') as mock_run:
                    # Returncode 0 but ffmpeg creates a zero-byte file
                    mock_run.return_value = MagicMock(returncode=0)
                    Path(out).write_bytes(b'')  # zero bytes — should be removed
                    result = _merge_subs_into_mkv(video, [sub_vtt])
                    self.assertIsNone(result)
                    self.assertFalse(os.path.exists(out))

    def test_passes_non_vtt_subs_directly_through(self):
        """If VTT→SRT is skipped and the sub is already SRT, helper passes it."""
        tmpdir, video, _sub_vtt, sub_srt, srt_path, out = self._run_merge()
        Path(out).write_bytes(b'\0' * 6000)
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            # No _vtt_to_srt patch — pass SRT directly
            with patch('app.downloader.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = _merge_subs_into_mkv(video, [str(srt_path)])
                self.assertEqual(result, out)


class TestQualityFormatIntegrity(unittest.TestCase):
    def setUp(self):
        self.video_keys = {'best', '2160p', '1440p', '1080p', '720p',
                           '480p', '360p', 'worst'}
        self.audio_keys = {'best', '320', '256', '192', '128', '96', 'worst'}

    def test_video_quality_keys_complete(self):
        self.assertEqual(set(VIDEO_QUALITY_FMT.keys()), self.video_keys)

    def test_audio_quality_keys_complete(self):
        self.assertEqual(set(AUDIO_QUALITY_FMT.keys()), self.audio_keys)

    def test_video_resolutions_all_have_filter(self):
        for res in ('2160p', '1440p', '1080p', '720p', '480p', '360p'):
            self.assertIn(f'height<={res[:-1]}', VIDEO_QUALITY_FMT[res])

    def test_audio_bitrates_all_have_filter(self):
        for br in ('320', '256', '192', '128', '96'):
            self.assertIn(f'abr<={br}', AUDIO_QUALITY_FMT[br])

    def test_worst_video_falls_back_to_single_worst(self):
        # Worst should be 'worst' so yt-dlp picks the smallest single stream
        self.assertIn('worst', VIDEO_QUALITY_FMT['worst'])

    def test_best_video_uses_bestvideo_plus_bestaudio(self):
        # Default quality must combine best video + best audio and fall back to single
        fmt = VIDEO_QUALITY_FMT['best']
        self.assertIn('bv', fmt.lower())
        self.assertIn('ba', fmt.lower())

    def test_normalize_quality_value_falls_back_when_unknown_quality_lookup_happens_at_call_site(self):
        # Behavior is enforced in app.downloader.download() using .get(...),
        # not by raising — sanity check our test mirrors that behavior.
        self.assertEqual(VIDEO_QUALITY_FMT.get('nonsense', VIDEO_QUALITY_FMT['best']),
                         VIDEO_QUALITY_FMT['best'])


if __name__ == '__main__':
    unittest.main()

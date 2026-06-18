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
    _is_proxy_transient_error,
    _opts_with_proxy,
    WARP_PROXY,
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
        # Ffmpeg is mocked but writes to a temp suffix; pre-create that file
        # so the size check passes.
        tmp_file = out + '.merge.tmp.mkv'
        Path(tmp_file).write_bytes(b'\0' * 6000)
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            with patch('app.downloader._vtt_to_srt', return_value=str(srt_path)):
                with patch('app.downloader.subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = _merge_subs_into_mkv(video, [sub_vtt])
                    self.assertEqual(result, out)
                    # Original video removed (mp4 -> mkv, so paths differ)
                    self.assertFalse(os.path.exists(video))
                    # Merged output (rename of tmp) kept
                    self.assertTrue(os.path.exists(out))
                    # Temp file removed by os.replace
                    self.assertFalse(os.path.exists(tmp_file))
                    # Subtitle file cleaned up too
                    self.assertFalse(os.path.exists(str(srt_path)))

    def test_empty_output_is_cleaned(self):
        tmpdir, video, sub_vtt, sub_srt, srt_path, out = self._run_merge()
        # Ffmpeg mock returns 0 but the temp file is zero bytes — verify
        # that _merge_subs_into_mkv bails and cleans up.
        tmp_file = out + '.merge.tmp.mkv'
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            with patch('app.downloader._vtt_to_srt', return_value=str(srt_path)):
                with patch('app.downloader.subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    Path(tmp_file).write_bytes(b'')
                    result = _merge_subs_into_mkv(video, [sub_vtt])
                    self.assertIsNone(result)
                    self.assertFalse(os.path.exists(tmp_file))
                    self.assertFalse(os.path.exists(out))

    def test_passes_non_vtt_subs_directly_through(self):
        """If VTT→SRT is skipped and the sub is already SRT, helper passes it."""
        tmpdir, video, _sub_vtt, sub_srt, srt_path, out = self._run_merge()
        tmp_file = out + '.merge.tmp.mkv'
        Path(tmp_file).write_bytes(b'\0' * 6000)
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            # No _vtt_to_srt patch — pass SRT directly
            with patch('app.downloader.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = _merge_subs_into_mkv(video, [str(srt_path)])
                self.assertEqual(result, out)
                self.assertTrue(os.path.exists(out))
                self.assertFalse(os.path.exists(tmp_file))

    def test_input_already_mkv_does_not_unlink_output(self):
        """Regression: when input is already MKV, mkv_file == video_file path.
        Function must write to a temp file, atomic-rename to mkv_file, and
        NOT also unlink that path (or we'd delete the merged result).
        """
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        video_mkv = Path(tmpdir) / 'video.mkv'
        video_mkv.write_bytes(b'\0' * 5000)
        srt_path = Path(tmpdir) / 'sub_en.srt'
        srt_path.write_text('1\n00:00:00,000 --> 00:00:01,000\nHi\n')
        tmp_file = str(video_mkv) + '.merge.tmp.mkv'
        Path(tmp_file).write_bytes(b'\0' * 6000)
        out = str(video_mkv)
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            with patch('app.downloader._vtt_to_srt', return_value=str(srt_path)):
                with patch('app.downloader.subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = _merge_subs_into_mkv(out, [str(srt_path)])
                    self.assertEqual(result, out)
                    # The merged file (renamed from tmp) still exists with content.
                    self.assertTrue(os.path.exists(out))
                    self.assertGreater(os.path.getsize(out), 0)
                    # Temp file is gone after os.replace.
                    self.assertFalse(os.path.exists(tmp_file))
                    self.assertFalse(os.path.exists(str(srt_path)))


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


class TestProxyTransientErrorDetection(unittest.TestCase):
    """Drive _is_proxy_transient_error — the classifier that decides
    when to retry yt-dlp without the Warp proxy.
    """

    def _exc(self, msg):
        return RuntimeError(msg)

    def test_connection_refused_is_transient(self):
        e = self._exc(
            'ERROR: [youtube] abc: Unable to download API page: '
            '[Errno 111] Connection refused (caused by TransportError(...))')
        self.assertTrue(_is_proxy_transient_error(e))

    def test_connection_reset_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(self._exc('Connection reset by peer')))

    def test_connection_aborted_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(self._exc('Connection aborted')))

    def test_timeout_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(self._exc('Read timed out')))

    def test_dns_failure_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(
            self._exc('Temporary failure in name resolution')))

    def test_dns_unknown_host_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(
            self._exc('Name or service not known')))

    def test_network_unreachable_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(self._exc('Network is unreachable')))

    def test_errno_104_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(self._exc('[Errno 104] reset')))

    def test_errno_110_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(self._exc('[Errno 110] timed out')))

    def test_video_unavailable_is_not_transient(self):
        # This is a YouTube-side error; dropping the proxy won't help.
        self.assertFalse(_is_proxy_transient_error(
            self._exc('Video unavailable')))

    def test_private_video_is_not_transient(self):
        self.assertFalse(_is_proxy_transient_error(
            self._exc('Private video. Sign in if you\'ve been granted access')))

    def test_http_403_is_not_transient(self):
        self.assertFalse(_is_proxy_transient_error(
            self._exc('HTTP Error 403: Forbidden')))

    def test_unrelated_exception_is_not_transient(self):
        self.assertFalse(_is_proxy_transient_error(ValueError('bad')))

    def test_case_insensitive_match(self):
        # String fragments are matched lowercased.
        self.assertTrue(_is_proxy_transient_error(self._exc('CONNECTION REFUSED')))
        self.assertTrue(_is_proxy_transient_error(self._exc('Connection Refused')))


class TestOptsWithProxy(unittest.TestCase):
    """Drive _opts_with_proxy — the helper that toggles the proxy opt."""

    def setUp(self):
        self.base = {'format': 'best', 'quiet': True, 'socket_timeout': 30}

    def test_with_proxy_true_adds_proxy(self):
        out = _opts_with_proxy(self.base, True)
        self.assertEqual(out['proxy'], WARP_PROXY)
        self.assertEqual(out['format'], 'best')
        self.assertEqual(out['quiet'], True)

    def test_with_proxy_false_strips_proxy(self):
        out = _opts_with_proxy(self.base, False)
        self.assertNotIn('proxy', out)
        self.assertEqual(out['format'], 'best')

    def test_does_not_mutate_input(self):
        out = _opts_with_proxy(self.base, True)
        self.assertNotIn('proxy', self.base)  # input dict untouched
        # Returned dict is a different object from the input
        self.assertIsNot(out, self.base)

    def test_overrides_existing_proxy(self):
        # If base already had a proxy, the helper overwrites it.
        out = _opts_with_proxy({**self.base, 'proxy': 'http://old:9999'}, True)
        self.assertEqual(out['proxy'], WARP_PROXY)


if __name__ == '__main__':
    unittest.main()

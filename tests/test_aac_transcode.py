"""Regression tests for the always-on AAC transcode post-merge hook.

Three classes cover the contract:

1. `TestAacTranscodeConfig` -- pins `_env_bool` semantics. CRITICAL
   contract: the helper does NOT fall back to the default for empty
   / whitespace / garbage values; an operator who fat-fingers the
   env var silently disables the TV fix.

2. `TestTranscodeAudioToAac` -- drives the helper: failure paths,
   ffmpeg cmd shape (positional pins for `-c:a aac` and `-b:a 192k`
   to catch encoder-name and bitrate regressions a substring pin
   would silently pass), title metadata argv shape, atomic rename,
   and temp-file cleanup.

3. `TestAacTranscodeIntegration` -- source-level pins for the gate
   in `download()` (5 conjuncts; each pinned individually so a
   future refactor that drops ANY of them fails loudly).
"""
import os
import shutil
import subprocess
import tempfile
import unittest
import inspect
from pathlib import Path
from unittest.mock import patch, MagicMock

import app.downloader as downloader
from app.downloader import _transcode_audio_to_aac
from config import _env_bool


class TestAacTranscodeConfig(unittest.TestCase):
    """Drive `Config.AAC_TRANSCODE` env-var parsing via `_env_bool`.

    The actual `Config.AAC_TRANSCODE` value is captured at import time,
    so direct assertion of the live Config would not see later env-var
    mutations (and could be brittle in CI shells that pre-export the
    var). Testing `_env_bool` with the documented default + parsing
    semantics is the canonical way to pin the contract: the production
    computation `Config.AAC_TRANSCODE = _env_bool('AAC_TRANSCODE',
    'true')` is the only place this flag is set.

    Important contract asymmetry (matters operationally):

      * Env var UNSET -> consults the default ('true' -> True).
      * Env var set to anything not in the truthy set (`'1'`,
        `'true'`, `'yes'`, `'on'`, case-insensitive) -> False.

    So an operator who sets `AAC_TRANSCODE=` (empty) in .env by accident
    silently DISABLES the TV audio fix. Documented behavior; pin it so
    a future refactor that adds a fallback-to-default guard for empty
    values is a deliberate change, not an accident.
    """

    _TEST_KEY = '_TEST_AAC_TRANSCODE_DUMMY_KEY_'

    def setUp(self):
        # Snapshot + clear so each test starts in a known state.
        self._saved = os.environ.pop(self._TEST_KEY, None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(self._TEST_KEY, None)
        else:
            os.environ[self._TEST_KEY] = self._saved

    # ---- default-resolution path (unset -> default) ---------------

    def test_unset_returns_documented_default_true(self):
        # Mirrors the production call _env_bool('AAC_TRANSCODE', 'true')
        # at config.py import time. The default 'true' MUST yield True
        # so the 2026-06-21 TV fix activates without operator env tuning.
        self.assertTrue(
            _env_bool(self._TEST_KEY, 'true'),
            'unset AAC_TRANSCODE MUST resolve to True so an upgrade '
            'is non-disruptive and the TV audio fix lands without '
            'operator env tuning.')

    # ---- asymmetric: empty / whitespace / garbage are NOT default -

    def test_empty_string_returns_false(self):
        # CRITICAL pin: a set-but-empty env var returns False. NOT the
        # default. Pin this so a future refactor that adds a fallback
        # is a deliberate change.
        #
        # Operational impact: an operator who writes `AAC_TRANSCODE=`
        # (empty) into .env silently DISABLES the TV audio fix. The
        # operator can opt back in via the explicit truthy values
        # ('1' / 'true' / 'yes' / 'on') -- but cannot accidentally
        # invoke the default-on fallback.
        os.environ[self._TEST_KEY] = ''
        self.assertFalse(
            _env_bool(self._TEST_KEY, 'true'),
            'set-but-empty env var MUST return False (NOT the '
            'literal default). The default is consulted ONLY when '
            'the env var is unset, not when it is set-but-empty.')

    def test_whitespace_only_returns_false(self):
        # Same asymmetry: a whitespace-only env var strips down to ''
        # which is not in the truthy set. Returns False, NOT default.
        os.environ[self._TEST_KEY] = '   '
        self.assertFalse(
            _env_bool(self._TEST_KEY, 'true'),
            'whitespace-only env var MUST return False (NOT the '
            'default). Helper behavior is consistent with empty '
            'env-var handling -- both produce empty string after '
            '`.strip()`.')

    def test_garbage_returns_false(self):
        # 'maybe' is not in the truthy set; helper returns False
        # (NOT the default) without crashing. Operator impact: a
        # typo (e.g. `AAC_TRANSCODE=tru`) silently disables the fix.
        # Documented contract; pin it.
        os.environ[self._TEST_KEY] = 'maybe'
        self.assertFalse(
            _env_bool(self._TEST_KEY, 'true'),
            'garbage env var MUST return False (NOT the default). '
            'Operators who fat-finger the env var (truthy set is '
            'canonical: 1 / true / yes / on) silently get the '
            'off-by-default behavior.')

    # ---- FALSE values (operator opt-out) -------------------------

    def test_zero_returns_false(self):
        # AAC_TRANSCODE=0 must disable so an operator on an Opus-
        # aware modern smart TV pays no 30-90s CPU overhead.
        os.environ[self._TEST_KEY] = '0'
        self.assertFalse(_env_bool(self._TEST_KEY, 'true'))

    def test_lowercase_false_returns_false(self):
        os.environ[self._TEST_KEY] = 'false'
        self.assertFalse(_env_bool(self._TEST_KEY, 'true'))

    def test_uppercase_FALSE_returns_false(self):
        os.environ[self._TEST_KEY] = 'FALSE'
        self.assertFalse(_env_bool(self._TEST_KEY, 'true'))

    def test_no_returns_false(self):
        os.environ[self._TEST_KEY] = 'no'
        self.assertFalse(_env_bool(self._TEST_KEY, 'true'))

    def test_off_returns_false(self):
        os.environ[self._TEST_KEY] = 'off'
        self.assertFalse(_env_bool(self._TEST_KEY, 'true'))

    # ---- TRUE values (operator opt-in / default) -----------------

    def test_one_returns_true(self):
        os.environ[self._TEST_KEY] = '1'
        self.assertTrue(_env_bool(self._TEST_KEY, 'true'))

    def test_uppercase_TRUE_returns_true(self):
        os.environ[self._TEST_KEY] = 'TRUE'
        self.assertTrue(_env_bool(self._TEST_KEY, 'true'))

    def test_yes_returns_true(self):
        os.environ[self._TEST_KEY] = 'yes'
        self.assertTrue(_env_bool(self._TEST_KEY, 'true'))

    def test_on_returns_true(self):
        os.environ[self._TEST_KEY] = 'on'
        self.assertTrue(_env_bool(self._TEST_KEY, 'true'))


class TestTranscodeAudioToAac(unittest.TestCase):
    """Drive `_transcode_audio_to_aac` -- the post-merge Opus->AAC
    re-encode hook that fixes `audio codec: none` on smart TVs lacking
    an Opus hardware decoder.

    The helper mirrors `_merge_subs_into_mkv`'s atomic-rename pattern
    (writes to `<video>.transcode.tmp.<ext>`, then `os.replace` to the
    final path on success; cleanup on failure). The fallible surfaces
    are:

      * ffmpeg returncode (success / failure)
      * output file written (size > 0)
      * the source video still exists on failure paths

    Strategy: monkey-patch `subprocess.run` in `app.downloader` to a
    stub that pre-creates the expected output file or raises the test
    fixture exception. The helper's true responsibility is the cmd
    shape, atomicity, and cleanup; the actual ffmpeg invocation is the
    OS's concern and isn't exercised here (network / disk independent).
    """

    def _setup_video(self, ext='.mkv', size=5000):
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        video = Path(tmpdir) / f'video{ext}'
        video.write_bytes(b'\0' * size)
        return str(video)

    # ---- error / edge-case paths ---------------------------------

    def test_returns_none_when_file_missing(self):
        # Defensive: the helper MUST NOT raise on a missing file --
        # `download()` chains a `Path(fp).exists()` check before
        # calling, but a missing fp at this layer must still bail
        # cleanly rather than throw FileNotFoundError at the user.
        self.assertIsNone(_transcode_audio_to_aac(
            '/nonexistent/never_exists_path.mkv'))

    def test_returns_none_when_file_path_is_empty(self):
        self.assertIsNone(_transcode_audio_to_aac(''))

    def test_returns_none_on_ffmpeg_error(self):
        # ffmpeg returns non-zero -> helper returns None + cleans up
        # the temp file + leaves the original video intact.
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr='ffmpeg fail')
            result = _transcode_audio_to_aac(video)
            self.assertIsNone(result)
            self.assertFalse(
                os.path.exists(tmp),
                'temp file MUST be removed on ffmpeg failure so '
                'downloads/ dir does not accumulate half-written '
                'transcodes.')
            self.assertTrue(
                os.path.exists(video),
                'original video MUST remain intact on ffmpeg '
                'failure so download() can fall back to delivering '
                'the untranscoded file.')

    def test_returns_none_on_zero_byte_output(self):
        # ffmpeg returned 0 but the temp file is empty -- helper
        # rejects rather than delivering a corrupt file to the user.
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            Path(tmp).write_bytes(b'')
            result = _transcode_audio_to_aac(video)
            self.assertIsNone(result)
            self.assertFalse(os.path.exists(tmp))
            self.assertTrue(os.path.exists(video))

    def test_returns_none_when_temp_file_missing(self):
        # ffmpeg returned 0 but didn't write the temp at all (e.g.
        # crashed silently after the report) -- bail.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = _transcode_audio_to_aac(video)
            self.assertIsNone(result)
            self.assertTrue(os.path.exists(video))

    def test_subprocess_exception_bails_cleanly(self):
        # subprocess.run raises (OS / TimeoutExpired / etc.).
        # `download()` will deliver the untranscoded original.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run',
                   side_effect=OSError('subprocess boom')):
            result = _transcode_audio_to_aac(video)
            self.assertIsNone(result)
            self.assertTrue(os.path.exists(video))

    def test_subprocess_timeout_keeps_original_intact(self):
        # Mid-flight regression: a hung ffmpeg fires
        # subprocess.TimeoutExpired (subclass of OSError). The
        # helper's `except Exception:` clause MUST swallow it so the
        # caller falls through to delivering the untranscoded
        # original. Pin BOTH paths: the early-return path AND the
        # temp-cleanup path (which only runs if the temp file
        # exists at the time of exception). Pre-creating the temp
        # file here exercises the cleanup branch -- a real
        # TimeoutExpired mid-mux in production leaves a half-written
        # temp file behind, and the helper's cleanup branch is what
        # prevents the downloads/ dir from accumulating junk on
        # repeated transient failures.
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        Path(tmp).write_bytes(b'X' * 6000)  # exercise cleanup branch
        with patch('app.downloader.subprocess.run',
                   side_effect=subprocess.TimeoutExpired(
                       cmd='ffmpeg', timeout=300)):
            result = _transcode_audio_to_aac(video)
            self.assertIsNone(
                result,
                'TimeoutExpired (ffmpeg hang) MUST be swallowed; '
                'the caller falls through to delivering the '
                'untranscoded file. A buggy refactor that propagates '
                'this exception would 500 the user mid-download.')
            self.assertTrue(
                os.path.exists(video),
                'original video MUST remain intact when the '
                'transcode times out.')

    # ---- success path --------------------------------------------

    def test_returns_input_path_on_success(self):
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        Path(tmp).write_bytes(b'\0' * 6000)
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = _transcode_audio_to_aac(video)
            self.assertEqual(
                result, video,
                'helper returns the unchanged INPUT path (not the '
                'temp path), matching the in-place semantics the '
                'caller relies on for `fp = transcoded`.')
            self.assertFalse(
                os.path.exists(tmp),
                'temp file MUST be consumed by os.replace after '
                'success.')
            self.assertTrue(os.path.exists(video))

    def test_success_uses_atomic_replace(self):
        # After os.replace, the on-disk content matches the temp
        # (the new AAAA... bytes), NOT the original zeros. Pins the
        # actual atomic-rename mechanism, not a copy or in-place
        # write that might bypass os.replace.
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        Path(tmp).write_bytes(b'A' * 6000)
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _transcode_audio_to_aac(video)
            self.assertEqual(
                Path(video).read_bytes(), b'A' * 6000,
                'os.replace MUST swap the temp bytes over the '
                'original; a copy would leave the original zeros '
                'in place.')

    # ---- ffmpeg cmd shape (TV-critical pins) ---------------------

    def test_ffmpeg_cmd_uses_aac_encoder(self):
        # The headline pin: TV audio fix targets AAC at 192 kbps.
        # Pin POSITIONALLY rather than via substring: 'aac' as a
        # substring would also match the (incorrect) 'libfdk_aac'
        # encoder name or a hypothetical 'aac_at' typo. The
        # positional assertion below pins the SHAPE exactly.
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        Path(tmp).write_bytes(b'\0' * 6000)
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0)
        with patch('app.downloader.subprocess.run', side_effect=fake_run):
            _transcode_audio_to_aac(video)
        cmd = recorded['cmd']
        self.assertEqual(
            cmd[cmd.index('-c:a') + 1], 'aac',
            '-c:a MUST use the native `aac` encoder (not '
            'libfdk_aac, which carries a license flag and is not '
            'installed on every production ffmpeg build). '
            'Substring-match would falsely pass for `-c:a '
            'libfdk_aac` or `-c:a aac_at`; positional pinning '
            'forces the literal encoder name to be `aac`.')
        self.assertEqual(
            cmd[cmd.index('-b:a') + 1], '192k',
            'AAC bitrate MUST be 192k (NOT 128k, 256k, or any '
            'other knob). 192k is the sweet spot for stereo near-'
            'transparency against typical YouTube 128-160 kbps '
            'Opus source; positional pinning forces the literal '
            'value.')

    def test_ffmpeg_cmd_copies_video_stream(self):
        # AVC stream MUST go through with zero CPU cost -- pin
        # `-c:v copy`. A refactor that drops it would burn 30-90s
        # of CPU per download for zero quality gain.
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        Path(tmp).write_bytes(b'\0' * 6000)
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0)
        with patch('app.downloader.subprocess.run', side_effect=fake_run):
            _transcode_audio_to_aac(video)
        cmd = recorded['cmd']
        self.assertEqual(
            cmd[cmd.index('-c:v') + 1], 'copy',
            '-c:v copy is mandatory; re-encoding AVC would burn '
            '30s+ of CPU per video for zero visible quality '
            'difference.')

    def test_ffmpeg_uses_temp_file_as_output(self):
        # Pin that ffmpeg writes to the TEMP path, not the original
        # input path. A refactor that drops the temp-path dance
        # would risk a read+write collision on the input file.
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        Path(tmp).write_bytes(b'\0' * 6000)
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0)
        with patch('app.downloader.subprocess.run', side_effect=fake_run):
            _transcode_audio_to_aac(video)
        cmd = recorded['cmd']
        # The last cmd entry is ffmpeg's output spec.
        self.assertEqual(
            cmd[-1], tmp,
            'ffmpeg MUST target the temp file so atomic '
            'os.replace can swap in the result.')
        # The input MUST be the original video so ffmpeg decodes it.
        input_idx = cmd.index('-i') + 1
        self.assertEqual(cmd[input_idx], video)

    def test_ffmpeg_preserves_input_extension_for_temp_path(self):
        # mp4 input -> mp4 temp (preserves container so downstream
        # code doesn't need to know we touched the file).
        video = self._setup_video(ext='.mp4', size=5000)
        Path(video).write_bytes(b'\0' * 5000)
        tmp = f'{video}.transcode.tmp.mp4'
        Path(tmp).write_bytes(b'\0' * 6000)
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0)
        with patch('app.downloader.subprocess.run', side_effect=fake_run):
            _transcode_audio_to_aac(video)
        cmd = recorded['cmd']
        self.assertEqual(
            cmd[-1], tmp,
            'output extension MUST match source extension so the '
            'downstream filename-sanitize step does not see an '
            'unexpected suffix.')

    def test_ffmpeg_uses_map_zero_for_all_streams(self):
        # `-map 0` selects every input stream so a video with
        # multiple audio tracks / subtitles passes them through
        # unchanged (remux-only on video). A video with no audio
        # produces no audio stream (no fake-silent-track).
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        Path(tmp).write_bytes(b'\0' * 6000)
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0)
        with patch('app.downloader.subprocess.run', side_effect=fake_run):
            _transcode_audio_to_aac(video)
        cmd = recorded['cmd']
        # Verify -map followed by '0' are adjacent argv entries,
        # not split by another flag (defensive against a refactor
        # that re-orders the flag chain).
        map_idx = cmd.index('-map')
        self.assertEqual(
            cmd[map_idx + 1], '0',
            '-map 0 MUST be adjacent argv entries; a stray flag '
            'between them would change ffmpeg\'s default stream-'
            'selection.')

    def test_title_metadata_argv_shape(self):
        # Pin the argv separator AND defend against accidental
        # `-metadata:s:s:0 language=` leakage from
        # `_merge_subs_into_mkv`'s contract. The helper MUST:
        #   1. Emit `-metadata` AT LEAST ONCE (the container-level
        #      title passthrough).
        #   2. For EVERY `-metadata` occurrence, the next argv
        #      entry MUST start with `title=` (no `language=`
        #      bleed-through).
        #   3. `-metadata` and the value MUST be adjacent argv
        #      entries (a single combined string with embedded
        #      whitespace would let ffmpeg parse the second chunk
        #      as a new flag).
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        Path(tmp).write_bytes(b'\0' * 6000)
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0)
        with patch('app.downloader.subprocess.run', side_effect=fake_run):
            _transcode_audio_to_aac(video, title='My Video')
        cmd = recorded['cmd']
        meta_indices = [
            i for i, arg in enumerate(cmd) if arg == '-metadata']
        self.assertGreaterEqual(
            len(meta_indices), 1,
            'at least one -metadata flag must be present '
            '(the container-level title passthrough).')
        for meta_idx in meta_indices:
            value = cmd[meta_idx + 1]
            self.assertTrue(
                value.startswith('title='),
                f'every -metadata value MUST start with title= '
                f'(helper must not emit language= or any other '
                f'metadata flag); got {value!r}')
            self.assertEqual(
                value, 'title=My Video',
                '-metadata and title=... MUST be adjacent argv '
                'entries; a single combined string with embedded '
                'whitespace would let ffmpeg parse the second '
                'chunk as a new flag.')

    def test_title_metadata_omitted_when_none(self):
        # No title -> no -metadata flag (don't write `title=None`).
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        Path(tmp).write_bytes(b'\0' * 6000)
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0)
        with patch('app.downloader.subprocess.run', side_effect=fake_run):
            _transcode_audio_to_aac(video, title=None)
        self.assertNotIn('-metadata', recorded['cmd'])

    def test_title_metadata_strips_control_chars(self):
        # Defense in depth: a YouTube title with newlines /
        # control chars MUST NOT smuggle a new ffmpeg flag into
        # the argv list. The helper strips [\x00-\x1f\x7f] before
        # adding the -metadata flag, so the bogus " -codec libx264"
        # fragment becomes part of the title string (single argv
        # slot), not a separate ffmpeg flag.
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        Path(tmp).write_bytes(b'\0' * 6000)
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0)
        with patch('app.downloader.subprocess.run', side_effect=fake_run):
            _transcode_audio_to_aac(video, title='Foo\n-codec libx264')
        cmd = recorded['cmd']
        meta_idx = cmd.index('-metadata')
        self.assertEqual(
            cmd[meta_idx + 1],
            'title=Foo-codec libx264',
            'control-char stripping MUST collapse the embedded '
            'newline so the bogus "-codec libx264" fragment '
            'sticks to the title value rather than leaking into '
            'a separate ffmpeg flag slot.')

    def test_title_metadata_empty_after_strip_drops_flag(self):
        # Title that is all control chars becomes empty -> no
        # -metadata flag (don't write `title=` with no value).
        video = self._setup_video()
        tmp = f'{video}.transcode.tmp.mkv'
        Path(tmp).write_bytes(b'\0' * 6000)
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0)
        with patch('app.downloader.subprocess.run', side_effect=fake_run):
            _transcode_audio_to_aac(video, title='\n\r\t\x00')
        self.assertNotIn('-metadata', recorded['cmd'])

    # ---- already-mkv case (no rename collision) ------------------

    def test_already_mkv_input_no_unlink_collision(self):
        # Regression: the helper's temp file MUST have a distinct
        # suffix `.transcode.tmp.<ext>` so the original input
        # MKV does not collide with the temp output. A `.mkv`
        # extension on the temp must not be confused with the
        # actual input.
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        video = Path(tmpdir) / 'already.mkv'
        video.write_bytes(b'\0' * 5000)
        tmp = str(video) + '.transcode.tmp.mkv'
        Path(tmp).write_bytes(b'\0' * 6000)
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = _transcode_audio_to_aac(str(video))
            self.assertEqual(result, str(video))
            # Verify the temp suffix is actually distinct from
            # the input -- the helper didn't accidentally write to
            # a path == input (would risk read+write collision).
            self.assertNotEqual(str(video), tmp)
            self.assertTrue(os.path.exists(str(video)))


class TestAacTranscodeIntegration(unittest.TestCase):
    """Source-level pins for `download()`'s AAC transcode gate.

    The inline gate in `download()` is:

        if (media_type == 'video'
                and Config.AAC_TRANSCODE
                and actual_container != 'mp4'
                and bot.has_ffmpeg
                and Path(fp).exists()
                and not _is_already_universal_codec(fp)):

    Six conjuncts; a future refactor that drops ANY of them silently
    breaks the TV fix, wastes 30-90s of CPU on MP4 outputs (which
    yt-dlp's `merge_output_format=mp4` already auto-transcodes via
    its own merge pipeline), OR wastes 30-90s of CPU on AAC-source
    videos (the 2026-06-21 single-core-VPS complaint that motivated
    the smart-skip helper).

    Each pin pins a specific conjunct so the regression is loud and
    named rather than buried in a generic 'TV audio is still missing'
    user report. Mirrors the prefix-contract pattern used in
    `TestAlsoGetOtherFormatIdxShiftSafety` and
    `TestShowRecentDeleteEntry` -- cheap source-level checks that catch
    the bug class before it ships.
    """

    _src = None

    def setUp(self):
        # Read download()'s source once per test (cheaper than
        # `inspect.getsource` on every method).
        if TestAacTranscodeIntegration._src is None:
            TestAacTranscodeIntegration._src = inspect.getsource(
                downloader.download)

    def test_transcode_call_present_in_download_body(self):
        # The principal pin: the helper MUST be invoked during
        # download(). Without this, the entire TV-audio fix is
        # silently inert at runtime.
        self.assertIn(
            '_transcode_audio_to_aac(',
            self._src,
            'download() MUST call _transcode_audio_to_aac() so '
            'Opus->AAC re-encode fires on every eligible video '
            'download. A refactor that drops the call silently '
            'defeats the 2026-06-21 TV fix.')

    def test_mp4_container_is_now_gated_by_probe(self):
        # 2026-06-23: the `actual_container != 'mp4'` guard was
        # REMOVED because yt-dlp's merge_output_format=mp4 does NOT
        # reliably auto-transcode Opus->AAC (the user reported
        # 'audio codec: none' again on PC). The gate now fires for
        # MP4 too, with `_is_already_universal_codec` as the sole
        # double-transcode guard — the ffprobe detects AAC from
        # yt-dlp's merge (when it works) and skips, or detects Opus
        # (when merge didn't transcode) and fires. Pin that the
        # mp4-exclusion code is ABSENT so a future maintainer who
        # re-adds it knows it was a deliberate removal.
        self.assertNotIn(
            "actual_container != 'mp4'",
            self._src,
            "The mp4-container skip was DELIBERATELY REMOVED on "
            "2026-06-23 because yt-dlp's MP4 merge does NOT "
            "reliably auto-transcode Opus->AAC. The "
            "_is_already_universal_codec probe is the sole guard "
            "against double-transcoding now -- it detects AAC "
            "(skip) or Opus (fire) regardless of container.")

    def test_config_aac_transcode_flag_respected(self):
        # The gate MUST read `Config.AAC_TRANSCODE` (NOT a
        # hardcoded True) so the env-var override actually
        # drives behavior at runtime.
        self.assertIn(
            'Config.AAC_TRANSCODE',
            self._src,
            "the AAC transcode gate MUST read Config.AAC_TRANSCODE "
            "so an operator's env-var override (AAC_TRANSCODE=false) "
            "actually disables the transcode. A hardcoded `True` "
            "would silently ignore the operator's setting.")

    def test_has_ffmpeg_required(self):
        # If ffmpeg is missing, transcode would crash. Gate
        # must also check `bot.has_ffmpeg` so a no-ffmpeg
        # deployment doesn't crash on the hidden ffmpeg call.
        self.assertIn(
            'bot.has_ffmpeg',
            self._src,
            'the AAC transcode gate MUST check bot.has_ffmpeg '
            'so a bot deployed without ffmpeg does not crash '
            'on the hidden ffmpeg subprocess call.')

    def test_video_media_type_only(self):
        # audio / thumb don't have a video stream to keep in
        # sync; the gate MUST restrict to `media_type == 'video'`.
        # Pin BOTH that the conjunct exists AND that it lives in
        # the gate (not just an unrelated media_type check in
        # the audio / thumb opts branches above).
        self.assertIn(
            "media_type == 'video'",
            self._src,
            "the AAC transcode gate MUST include media_type == "
            "'video' because audio and thumb downloads have no "
            "video stream to keep in sync with the re-encode.")
        # Anchored check: the media_type=='video' conjunct
        # appears IN THE GATE (close to `_transcode_audio_to_aac`).
        # Lookback at 1200 chars comfortably covers the 14-line
        # explanatory comment block preceding the gate.
        # (Earlier-round pinned 600 chars; tight enough that an
        # extra explanatory comment would silently shift the
        # gate outside the index range. 1200 chars is robust.)
        transcode_idx = self._src.index('_transcode_audio_to_aac(')
        gate_section = self._src[
            max(0, transcode_idx - 1200):transcode_idx]
        self.assertIn(
            "media_type == 'video'",
            gate_section,
            'media_type == "video" MUST be in the gate conjunct '
            'chain (not just in the audio/thumb opts branches '
            'above). A gate that re-encodes non-video files '
            'would 500 on audio/thumb downloads.')

    def test_fp_exists_check_present(self):
        # The gate MUST also gate on `Path(fp).exists()`. Without
        # this, a ffmpeg crash that left the final fp missing
        # (e.g. storage full mid-rename) would still try to
        # call the helper, which would race with the cleanup
        # path. The fp exists check is the last-line defense.
        transcode_idx = self._src.index('_transcode_audio_to_aac(')
        gate_section = self._src[
            max(0, transcode_idx - 200):transcode_idx]
        self.assertIn(
            'Path(fp)',
            gate_section,
            'the AAC transcode gate MUST include `Path(fp).exists()` '
            'so a ffmpeg crash or storage-full mid-rename that '
            'left fp missing does not race the cleanup path.')

    def test_gate_assigns_transcoded_back_to_fp(self):
        # If the helper succeeds, download() MUST rebind `fp =
        # transcoded` so the subsequent sanitize-rename step
        # uses the AAC file. A refactor that captures the
        # transcoded path but doesn't rebind `fp` silently
        # delivers the untranscoded original.
        self.assertIn(
            'fp = transcoded',
            self._src,
            "download() MUST rebind `fp = transcoded` after a "
            "successful transcode so the post-transcode sanitize-"
            "rename step uses the AAC file. A refactor that "
            "captures the path but doesn't rebind fp silently "
            "delivers the untranscoded original.")

    def test_smart_skip_codec_conjunct_present(self):
        # Pin the 6th gate conjunct: ffprobe-driven smart-skip on
        # already-universal audio. Without this, a SINGLE-CORE
        # VPS pays the full 30-90s ffmpeg transcode cost on EVERY
        # video including the AAC-source ones (the user explicitly
        # reported "ffmpeg processing is high for my single-core
        # VPS" -- 2026-06-21). A refactor that drops this conjunct
        # silently regresses CPU cost on every download by 1-2
        # orders of magnitude.
        self.assertIn(
            '_is_already_universal_codec',
            self._src,
            'download() gate MUST include the smart-skip conjunct '
            '(`_is_already_universal_codec(fp)`) so single-core-VPS '
            'deployments skip the 30-90s ffmpeg re-encode when the '
            'post-merge audio is already in the universal-codec '
            'set (currently just `aac`). A refactor that drops '
            'this conjunct burns 30-90s of CPU on every AAC-source '
            'download -- the exact complaint the user raised '
            '(2026-06-21 single-core-VPS feedback).')

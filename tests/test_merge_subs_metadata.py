"""Tests for the new video metadata + subtitle language behavior on the
MKV-subtitle-merge path.

Background: bug filed by the user where merged MKVs showed up with
`language=und` for every subtitle track in VLC / mpv / Plex. Also asked
us to surface video metadata when ffmpeg writes the merged MKV. This
file covers both surfaces end-to-end through ffmpeg's command-line shape
(the only thing we can actually unit-test without a real ffmpeg install).

External ffmpeg is mocked via `subprocess.run` patches, identical pattern
to TestMergeSubsIntoMkv in test_downloader.py.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.downloader import (
    _merge_subs_into_mkv,
    _extract_lang_from_filename,
)


class TestExtractLangFromFilename(unittest.TestCase):
    """_extract_lang_from_filename — yt-dlp subtitle filename convention parser.

    yt-dlp's documented subtitle filename pattern is `Title.<lang>.<ext>`
    where `<lang>` is one of:
      * ISO 639-1: `en`, `de`, `es` (2 lowercase letters)
      * ISO 639-2: 3-letter codes like `yue` (rare but yt-dlp uses them)
      * BCP 47: en-US, pt-BR, zh-Hant (region subtag)
    Returning '' for everything else lets the merge helper decide to omit
    the language flag rather than emit `language=` with a garbage value.
    """

    def test_iso_639_1_two_letter(self):
        self.assertEqual(_extract_lang_from_filename('Foo.en.srt'), 'en')
        self.assertEqual(_extract_lang_from_filename('Foo.es.vtt'), 'es')
        self.assertEqual(_extract_lang_from_filename('Foo.de.srt'), 'de')

    def test_iso_639_2_three_letter(self):
        self.assertEqual(_extract_lang_from_filename('Foo.yue.srt'), 'yue')

    def test_bcp47_with_region(self):
        # en-US, pt-BR, zh-Hant etc. are common in yt-dlp output. Lowercased
        # on output so the ffmpeg `language=` value is uniform.
        self.assertEqual(_extract_lang_from_filename('Foo.en-US.srt'), 'en-us')
        self.assertEqual(_extract_lang_from_filename('Foo.pt-BR.vtt'), 'pt-br')
        self.assertEqual(_extract_lang_from_filename('Foo.zh-Hant.srt'), 'zh-hant')

    def test_uppercase_input_lowercased(self):
        # yt-dlp lowercases lang segments in practice, but if a future version
        # ever capitalises, we still return lowercased so ffmpeg's `language=`
        # argument stays canonical.
        self.assertEqual(_extract_lang_from_filename('Foo.EN.srt'), 'en')

    def test_filename_without_lang_returns_empty(self):
        # Some videos only have language-neutral auto-subs and yt-dlp writes
        # `Title.srt` (or `Title.vtt`) with no language segment to parse.
        self.assertEqual(_extract_lang_from_filename('Foo.srt'), '')
        self.assertEqual(_extract_lang_from_filename('Foo.vtt'), '')

    def test_filename_with_arbitrary_5char_word_returns_empty(self):
        # `Foo.title.srt` parses incorrectly if we just split-and-trust. The
        # `^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$` regex rejects `title` (5 chars)
        # because it's too long for the primary tag. The contract is: only
        # well-formed lang tags come through, anything else is treated as
        # missing lang.
        self.assertEqual(_extract_lang_from_filename('Foo.title.srt'), '')

    def test_filename_with_numeric_segment_returns_empty(self):
        # `Foo.1080p.srt` must NOT be parsed as a lang tag. The regex requires
        # the primary tag be 2-3 letters only.
        self.assertEqual(_extract_lang_from_filename('Foo.1080p.srt'), '')

    def test_filename_with_dotted_stem_returns_last_qualifying_segment(self):
        # Some filenames embed the lang deeper (e.g. `Foo.title.en.srt`).
        # Our parser only reads the LAST `.`-segment, so we treat this as
        # the lang contract yt-dlp uses almost universally.
        self.assertEqual(_extract_lang_from_filename('Foo.title.en.srt'), 'en')

    def test_empty_path_returns_empty(self):
        self.assertEqual(_extract_lang_from_filename(''), '')

    def test_none_path_does_not_crash(self):
        # Defensive: callers might pass an empty list element. None shouldn't
        # reach here but we don't want it to crash the merge path.
        try:
            self.assertEqual(_extract_lang_from_filename(None), '')
        except (TypeError, AttributeError):
            self.fail('_extract_lang_from_filename should handle None gracefully')

    def test_path_with_no_extension_returns_empty(self):
        self.assertEqual(_extract_lang_from_filename('/some/Foo'), '')


class TestMergeSubsMetadata(unittest.TestCase):
    """ffmpeg cmd shape for the new title + subtitle-language kwargs.

    The merge helper builds an argv list for ffmpeg. The user reported that
    no subtitle language was being set (`language=und` everywhere). These
    tests assert on the final argv that ffmpeg receives so a regression in
    the helper or in the plumbed-through call site gets caught immediately.
    """

    def _setup_filesystem(self, num_subs=1):
        """Create a tempdir with video + N subtitle files; return their paths."""
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        video = Path(tmpdir) / 'video.mp4'
        video.write_bytes(b'\0' * 5000)
        srts = []
        for i in range(num_subs):
            srt = Path(tmpdir) / f'sub_{i}.srt'
            # Encode lang into filename so filename-based parse returns the right value.
            lang_codes = ['en', 'es']
            if i < len(lang_codes):
                srt = Path(tmpdir) / f'sub_{lang_codes[i]}.srt'
            srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nHi\n')
            srts.append(str(srt))
        out = str(video.with_suffix('.mkv'))
        # Pre-create the tmp file so the size check passes on merge success.
        Path(out + '.merge.tmp.mkv').write_bytes(b'\0' * 6000)
        return tmpdir, str(video), srts, out

    def _capture_merge_cmd(self, video, srts, **kwargs):
        """Run merge and return the argv list ffmpeg would receive."""
        with patch('app.downloader.DOWNLOADS_DIR', Path(os.path.dirname(video))):
            with patch('app.downloader._vtt_to_srt', side_effect=srts):
                with patch('app.downloader.subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    _merge_subs_into_mkv(video, srts, **kwargs)
                    return mock_run.call_args[0][0]

    # ---- title metadata -----------------------------------------

    def test_cmd_includes_title_metadata_when_title_provided(self):
        # The user explicitly asked for video metadata: when `title` is supplied,
        # ffmpeg's cmd must contain `-metadata title=...` so VLC / mpv / Plex
        # display a real MKV title instead of the filename.
        tmpdir, video, srts, out = self._setup_filesystem(num_subs=1)
        cmd = self._capture_merge_cmd(video, srts, title='My Talk Title')
        self.assertIn('-metadata', cmd)
        idx = cmd.index('-metadata')
        # The very next argv slot must be the title=value pair (NOT inline) so
        # the metaflag never gets reinterpreted by argv parsing if a quote
        # somehow sneaks into the title.
        self.assertEqual(cmd[idx + 1], 'title=My Talk Title')

    def test_cmd_omits_title_metadata_when_title_none(self):
        # Backwards-compat: existing call sites with no title kwarg keep
        # behaving exactly as before (no `-metadata` flag for the title).
        tmpdir, video, srts, out = self._setup_filesystem(num_subs=1)
        cmd = self._capture_merge_cmd(video, srts)
        self.assertNotIn('-metadata', cmd)

    def test_cmd_omits_title_metadata_when_title_empty(self):
        # Empty / whitespace-only title must NOT emit `-metadata title=`. The
        # `if safe_title` guard inside the helper is what enforces this.
        tmpdir, video, srts, out = self._setup_filesystem(num_subs=1)
        cmd = self._capture_merge_cmd(video, srts, title='')
        self.assertNotIn('-metadata', cmd)
        cmd = self._capture_merge_cmd(video, srts, title='   ')
        self.assertNotIn('-metadata', cmd)

    def test_cmd_strips_control_chars_from_title(self):
        # Defense-in-depth: a YouTube title containing control characters
        # (newline, carriage return, BEL etc.) MUST not have those bytes
        # reach ffmpeg's `-metadata title=` value, because:
        #   (a) ffmpeg rejects `\n` mid-metadata-value on some MKV
        #       muxer versions, and
        #   (b) VLC / mpv / Plex render those bytes as visible garbage
        #       in the title UI.
        # Note: with `subprocess.run(cmd_list, shell=False)` invocation
        # there is NO argv-spoofing risk from a literal `-codec` substring
        # embedded in the title — it's text inside one argv slot. So the
        # strip is about UI hygiene / mux robustness, not security. The
        # test asserts the control bytes themselves are gone, not that
        # arbitrary text was redacted.
        tmpdir, video, srts, out = self._setup_filesystem(num_subs=1)
        cmd = self._capture_merge_cmd(
            video, srts, title='Hello\n-codec libx264\r\x07World')
        idx = cmd.index('-metadata')
        title_value = cmd[idx + 1]
        self.assertNotIn('\n', title_value)
        self.assertNotIn('\r', title_value)
        self.assertNotIn('\x07', title_value)
        # The byte sequence `Hello-codec libx264World` (after the three
        # control chars were stripped) is the EXPECTED output. It's
        # acceptable to keep the literal `-codec` text because argv-list
        # mode guarantees it stays inside the title value.
        self.assertEqual(title_value, 'title=Hello-codec libx264World')

    # ---- subtitle language metadata -------------------------------

    def test_cmd_includes_subtitle_language_per_track(self):
        # The bug the user reported: merged MKV had `language=und` for every
        # subtitle track. When sub_languages is supplied (one entry per
        # subtitle), the cmd must contain `-metadata:s:s:<i> language=<lang>`
        # for each track.
        tmpdir, video, srts, out = self._setup_filesystem(num_subs=1)
        cmd = self._capture_merge_cmd(video, srts, sub_languages=['en'])
        self.assertIn('-metadata:s:s:0', cmd)
        idx = cmd.index('-metadata:s:s:0')
        self.assertEqual(cmd[idx + 1], 'language=en')

    def test_cmd_omits_subtitle_language_for_empty_string_entry(self):
        # An empty lang (e.g. yt-dlp saved the sub without a `.lang.` segment)
        # must NOT add `-metadata:s:s:<i> language=` (ffmpeg rejects `language=`
        # with nothing after the =). The inner `if lang` guard prevents it.
        tmpdir, video, srts, out = self._setup_filesystem(num_subs=1)
        cmd = self._capture_merge_cmd(video, srts, sub_languages=[''])
        for i, token in enumerate(cmd):
            if token.startswith('-metadata:s:s:'):
                self.assertNotEqual(
                    cmd[i + 1], 'language=',
                    'empty lang leaked into cmd as language=')

    def test_cmd_omits_subtitle_language_when_sub_languages_none(self):
        # Backwards-compat: existing callers that don't supply sub_languages
        # (default None) keep producing the original command shape.
        tmpdir, video, srts, out = self._setup_filesystem(num_subs=1)
        cmd = self._capture_merge_cmd(video, srts)
        for token in cmd:
            self.assertFalse(
                token.startswith('-metadata:s:s:'),
                'sub_languages=None must not add per-stream metadata flags')

    def test_cmd_per_index_languages_when_multiple_tracks(self):
        # Regression guard for the ordering invariant: lang[i] MUST map to
        # the i-th subtitle input. If a future refactor shuffles them (e.g.
        # to ['es', 'en'] when subs are [en.srt, es.srt]), users see tracks
        # mislabeled in their player. Lock the index-to-lang contract.
        tmpdir, video, srts, out = self._setup_filesystem(num_subs=2)
        cmd = self._capture_merge_cmd(
            video, srts, sub_languages=['en', 'es'])
        idx0 = cmd.index('-metadata:s:s:0')
        idx1 = cmd.index('-metadata:s:s:1')
        self.assertEqual(cmd[idx0 + 1], 'language=en')
        self.assertEqual(cmd[idx1 + 1], 'language=es')

    def test_cmd_metadata_sits_before_output_filename(self):
        # ffmpeg's option-parsing order matters: output-style options
        # (`-metadata ...`, `-c:s srt`, maps) must come BEFORE the output
        # filename, otherwise ffmpeg interprets them as input options.
        # Verify the contract so a future refactor that reorders the
        # helper doesn't accidentally move the metadata flags after
        # `tmp_file` in the argv list.
        tmpdir, video, srts, out = self._setup_filesystem(num_subs=1)
        cmd = self._capture_merge_cmd(
            video, srts, title='T', sub_languages=['en'])
        title_idx = cmd.index('title=T')
        lang_idx = cmd.index('language=en')
        # The output filename (the last arg in the helper's cmd) must come
        # AFTER both metadata args.
        output_filename = cmd[-1]
        self.assertTrue(output_filename.endswith('.mkv'))
        self.assertLess(title_idx, len(cmd) - 1)
        self.assertLess(lang_idx, len(cmd) - 1)


if __name__ == '__main__':
    unittest.main()

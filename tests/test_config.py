"""Tests for config.py — focus on USE_WARP (the new feature) so we cover the
default-false contract and the boolean parsing variants (true/false/1/0/yes/no).
"""
import importlib
import os
import unittest


def _reload_config(monkeypatch_environ):
    """Reload the config module after patching os.environ."""
    for k, v in monkeypatch_environ.items():
        os.environ[k] = v
    # Drop cached modules so we re-read os.environ on import
    import sys
    for mod_name in ('config',):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import config as cfg  # fresh import
    return cfg


def _drop_environ(*keys):
    for k in keys:
        os.environ.pop(k, None)


class TestUseWarpDefault(unittest.TestCase):
    """Contract: USE_WARP defaults to False when the env var is absent or empty."""

    def setUp(self):
        _drop_environ('USE_WARP')

    def test_default_is_false_when_unset(self):
        cfg = _reload_config({})
        self.assertIsInstance(cfg.Config.USE_WARP, bool)
        self.assertFalse(cfg.Config.USE_WARP)

    def test_default_is_false_when_empty(self):
        cfg = _reload_config({'USE_WARP': ''})
        self.assertIsInstance(cfg.Config.USE_WARP, bool)
        self.assertFalse(cfg.Config.USE_WARP)


class TestUseWarpTruthyValues(unittest.TestCase):
    """Truthy strings: 1 / true / True / TRUE / yes / Yes / YES / on."""

    def setUp(self):
        _drop_environ('USE_WARP')

    def _assert_true(self, val):
        cfg = _reload_config({'USE_WARP': val})
        self.assertTrue(cfg.Config.USE_WARP, f'expected USE_WARP={val!r} to be True')

    def test_one(self):
        self._assert_true('1')
    def test_true_lowercase(self):
        self._assert_true('true')
    def test_true_mixed_case(self):
        self._assert_true('True')
    def test_true_upper_case(self):
        self._assert_true('TRUE')
    def test_yes_lowercase(self):
        self._assert_true('yes')
    def test_yes_mixed_case(self):
        self._assert_true('Yes')
    def test_on_lowercase(self):
        self._assert_true('on')


class TestUseWarpFalsyValues(unittest.TestCase):
    """Everything else (including '0', 'false', 'no', 'off', garbage) is False."""

    def setUp(self):
        _drop_environ('USE_WARP')

    def _assert_false(self, val):
        cfg = _reload_config({'USE_WARP': val})
        self.assertFalse(cfg.Config.USE_WARP, f'expected USE_WARP={val!r} to be False')

    def test_zero(self):
        self._assert_false('0')
    def test_false_lowercase(self):
        self._assert_false('false')
    def test_false_mixed_case(self):
        self._assert_false('False')
    def test_no(self):
        self._assert_false('no')
    def test_off(self):
        self._assert_false('off')
    def test_garbage_string(self):
        self._assert_false('maybe')
    def test_whitespace_padded_true(self):
        # Stripped before parse, so ' true ' counts as True
        cfg = _reload_config({'USE_WARP': ' true '})
        self.assertTrue(cfg.Config.USE_WARP)


if __name__ == '__main__':
    unittest.main()

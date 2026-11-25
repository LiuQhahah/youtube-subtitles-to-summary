#!/usr/bin/env python3
"""Self-tests for vtt2txt.py and the language picker in download_subs.py.

No third-party dependencies and no network access, so this runs anywhere::

    python3 tests/test_parsers.py

The fixtures encode the two VTT shapes YouTube emits, including the exact
overlap pattern that makes token-extraction parsers lossy.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import vtt2txt  # noqa: E402
import download_subs  # noqa: E402


ROLLING_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.100 --> 00:00:01.855 align:start position:0%

AI <00:00:01.412><c>workloads</c>

00:00:01.855 --> 00:00:01.865 align:start position:0%
AI workloads


00:00:01.865 --> 00:00:03.334 align:start position:0%
AI workloads
can <00:00:02.107><c>run </c><00:00:02.349><c>tens </c><00:00:02.591><c>of </c><00:00:02.833><c>thousands </c><00:00:03.075><c>of </c><00:00:03.317><c>GPU</c>

00:00:03.334 --> 00:00:03.344 align:start position:0%
can run tens of thousands of GPU


00:00:03.344 --> 00:00:04.789 align:start position:0%
can run tens of thousands of GPU
instances <00:00:03.582><c>per </c><00:00:03.820><c>cluster, </c><00:00:04.058><c>and </c><00:00:04.296><c>at </c><00:00:04.534><c>that</c>
"""

PLAIN_VTT = """WEBVTT

00:00:00.100 --> 00:00:03.200
AI workloads can run tens of thousands of GPU instances per cluster.

00:00:03.200 --> 00:00:06.500
And at that scale, failures do occur.

00:00:06.500 --> 00:00:09.000
And at that scale, failures do occur.

00:00:09.000 --> 00:00:12.000
A managed provider has three &amp; only three choices.
"""


class TestRollingVtt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".en.vtt",
                                               delete=False, encoding="utf-8")
        self.tmp.write(ROLLING_VTT)
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_detected_as_rolling(self):
        mode, _ = vtt2txt.convert(self.tmp.name)
        self.assertEqual(mode, "rolling")

    def test_no_duplication_and_no_loss(self):
        _, text = vtt2txt.convert(self.tmp.name)
        # Every word appears exactly once, in order.
        expected = ("AI workloads can run tens of thousands of GPU instances "
                    "per cluster, and at that")
        self.assertEqual(text, expected)
        self.assertEqual(text.count("workloads"), 1)

    def test_untagged_words_survive(self):
        # 'can', 'instances', 'and', 'at', 'that' carry no <c> tag in the
        # fixture; a token-extraction parser drops all of them.
        _, text = vtt2txt.convert(self.tmp.name)
        for word in ("can", "instances", "and", "at", "that"):
            self.assertIn(word, text.split())


class TestPlainVtt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".en.vtt",
                                               delete=False, encoding="utf-8")
        self.tmp.write(PLAIN_VTT)
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_detected_as_plain(self):
        mode, _ = vtt2txt.convert(self.tmp.name)
        self.assertEqual(mode, "plain")

    def test_duplicate_cue_dropped(self):
        _, text = vtt2txt.convert(self.tmp.name)
        self.assertEqual(text.count("failures do occur"), 1)

    def test_entities_unescaped(self):
        _, text = vtt2txt.convert(self.tmp.name)
        self.assertIn("three & only three", text)
        self.assertNotIn("&amp;", text)


class TestLanguagePicker(unittest.TestCase):
    def test_prefers_orig(self):
        # en-orig avoids the translation endpoint that returns 429.
        self.assertEqual(download_subs.pick_language(["en", "en-orig"], "en"),
                         "en-orig")

    def test_plain_en_when_no_orig(self):
        self.assertEqual(download_subs.pick_language(["en"], "en"), "en")

    def test_regional_fallback(self):
        self.assertEqual(download_subs.pick_language(["en-US", "en-GB"], "en"),
                         "en-GB")

    def test_absent_language(self):
        self.assertIsNone(download_subs.pick_language(["de", "fr"], "en"))

    def test_empty(self):
        self.assertIsNone(download_subs.pick_language([], "en"))

    def test_script_variant(self):
        self.assertEqual(download_subs.pick_language(["zh-Hans"], "zh"),
                         "zh-Hans")


class TestOutputNaming(unittest.TestCase):
    def test_language_tag_preserved(self):
        dest = vtt2txt.default_dest("/x/vtt/abc123.en.vtt", "/x/text")
        self.assertEqual(dest, "/x/text/abc123.en.txt")

    def test_orig_tag_preserved(self):
        dest = vtt2txt.default_dest("/x/vtt/abc123.en-orig.vtt", "/x/text")
        self.assertEqual(dest, "/x/text/abc123.en-orig.txt")

    def test_vtt_subdir_uses_parent(self):
        dest = vtt2txt.default_dest("/x/vtt/abc123.en.vtt", None)
        self.assertEqual(dest, "/x/text/abc123.en.txt")

    def test_vtt_in_cwd_stays_in_cwd(self):
        # A file in the current directory must not write to its parent.
        dest = vtt2txt.default_dest("/tmp/work/abc123.en.vtt", None)
        self.assertEqual(dest, "/tmp/work/text/abc123.en.txt")


class TestHaveSub(unittest.TestCase):
    def test_matches_both_lang_variants(self):
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, "abc12345678.en.vtt"), "w").close()
            open(os.path.join(td, "def12345678.en-orig.vtt"), "w").close()
            self.assertTrue(download_subs.have_sub(td, "abc12345678"))
            self.assertTrue(download_subs.have_sub(td, "def12345678"))
            self.assertFalse(download_subs.have_sub(td, "zzz12345678"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

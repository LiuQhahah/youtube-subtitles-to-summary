#!/usr/bin/env python3
"""YouTube VTT -> clean plain text.

Two very different VTT shapes come out of YouTube, and mixing them up is the
single most common way to silently lose transcript content:

1. **Auto-generated captions** (``--write-auto-subs``) are *word-timestamped and
   rolling*. Every cue's last line is the full text of the current scroll
   window, and consecutive cues overlap heavily. A naive "strip tags, join
   lines" pass therefore duplicates text; a naive "take only the
   ``<ts><c>word</c>`` tokens" pass *drops every word YouTube did not wrap in a
   ``<c>`` element*. On a real 790-word KubeCon keynote, the token-only approach
   yields 660 words -- 130 words (16%) silently vanish. This module merges cues
   by word-level longest suffix/prefix overlap instead, which is lossless.

2. **Manual captions** (``--write-subs``) are plain cue blocks with no word
   timestamps and no overlap. Those are handled by the simple branch: strip
   tags, join, dedupe exact consecutive repeats.

Usage::

    python3 vtt2txt.py vtt/*.en.vtt                  # -> text/<videoId>.en.txt
    python3 vtt2txt.py -o out/ vtt/*.en.vtt          # explicit output dir
    python3 vtt2txt.py --stdout vtt/abc.en.vtt       # print instead of writing
    python3 vtt2txt.py --stats vtt/*.en.vtt          # word counts only
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys

HEADER_RE = re.compile(r"^(NOTE|Kind:|Language:|STYLE|REGION|WEBVTT|X-TIMESTAMP-MAP)")
TAG_RE = re.compile(r"<[^>]*>")
TIMING_RE = re.compile(r"-->")
# <00:00:01.412><c>word</c>  -- the word-level token form
WORD_TOKEN_RE = re.compile(r"<(?:\d+:)?\d{2}:\d{2}\.\d{3}>")
VTT_SUFFIX_RE = re.compile(r"\.vtt$", re.IGNORECASE)


def read_vtt(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read().replace("\r\n", "\n").replace("\r", "\n")


def split_cues(content: str) -> list[list[str]]:
    """Split a VTT file into cues, each a list of non-empty text lines."""
    cues: list[list[str]] = []
    for block in re.split(r"\n[ \t]*\n", content):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        lines = [ln for ln in lines if not HEADER_RE.match(ln.strip())]
        if not lines:
            continue
        # Drop the timing line (and an optional numeric cue id above it).
        if TIMING_RE.search(lines[0]):
            lines = lines[1:]
        elif len(lines) > 1 and TIMING_RE.search(lines[1]):
            lines = lines[2:]
        if lines:
            cues.append(lines)
    return cues


def is_rolling(cues: list[list[str]]) -> bool:
    """Auto-generated captions carry word-level <ts><c>..</c> tokens."""
    for lines in cues[:40]:
        if WORD_TOKEN_RE.search("\n".join(lines)):
            return True
    return False


def clean_line(line: str) -> str:
    line = TAG_RE.sub("", line)          # strips both <ts> and <c> wrappers
    line = html.unescape(line)
    return re.sub(r"\s+", " ", line).strip()


def merge_rolling(cues: list[list[str]]) -> str:
    """Lossless merge for word-timestamped rolling captions.

    Each cue's last line is the whole current scroll window. We append only the
    part of each new window that does not already end the accumulator, using the
    longest word-level suffix/prefix overlap.
    """
    acc: list[str] = []
    for lines in cues:
        for line in lines:
            new_words = clean_line(line).split()
            if not new_words:
                continue
            if not acc:
                acc = new_words
                continue
            limit = min(len(acc), len(new_words))
            overlap = 0
            for k in range(limit, 0, -1):
                if acc[-k:] == new_words[:k]:
                    overlap = k
                    break
            if overlap == len(new_words):
                continue                 # whole window already known
            acc.extend(new_words[overlap:])
    return finish(acc)


def merge_plain(cues: list[list[str]]) -> str:
    """Simple join for manual captions (no overlap, but tolerate repeats)."""
    acc: list[str] = []
    for lines in cues:
        for line in lines:
            new_words = clean_line(line).split()
            if not new_words:
                continue
            if acc and acc[-len(new_words):] == new_words:
                continue                 # exact consecutive duplicate cue
            acc.extend(new_words)
    return finish(acc)


def finish(words: list[str]) -> str:
    text = " ".join(words)
    text = re.sub(r"\s+([,.;:!?%)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def convert(path: str) -> tuple[str, str]:
    """Return (mode, text) for one VTT file."""
    cues = split_cues(read_vtt(path))
    if not cues:
        return "empty", ""
    if is_rolling(cues):
        return "rolling", merge_rolling(cues)
    return "plain", merge_plain(cues)


def default_dest(vtt_path: str, out_dir: str | None) -> str:
    """vtt/abc.en.vtt -> text/abc.en.txt (keeps the language tag)."""
    name = os.path.basename(vtt_path)
    stem = VTT_SUFFIX_RE.sub(".txt", name)
    base = os.path.dirname(os.path.dirname(os.path.abspath(vtt_path)))
    target_dir = out_dir if out_dir else os.path.join(base, "text")
    return os.path.join(target_dir, stem)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Convert YouTube VTT subtitles to clean text.")
    ap.add_argument("vtt", nargs="+", help="VTT file(s)")
    ap.add_argument("-o", "--out-dir", help="output directory (default: sibling text/)")
    ap.add_argument("--stdout", action="store_true", help="print to stdout, write nothing")
    ap.add_argument("--stats", action="store_true", help="print word counts only")
    args = ap.parse_args(argv)

    rc = 0
    for path in args.vtt:
        if not path.lower().endswith(".vtt"):
            continue
        if not os.path.isfile(path):
            print(f"skip (missing): {path}", file=sys.stderr)
            rc = 1
            continue
        mode, text = convert(path)
        words = len(text.split())
        if args.stdout:
            print(text)
            continue
        if args.stats:
            print(f"{path}\t{mode}\t{words}")
            continue
        dest = default_dest(path, args.out_dir)
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"{dest}: {words} words ({mode})")
        if words == 0:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

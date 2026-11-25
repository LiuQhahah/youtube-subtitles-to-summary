#!/usr/bin/env python3
"""Batch-download YouTube subtitles with the failure modes this domain actually has.

Why a script and not a documented one-liner: three things break in practice and
each needs real handling.

* **Bot detection.** Any request without cookies fails with
  ``Sign in to confirm you're not a bot``. ``--cookies-from-browser`` is not
  optional.
* **PO token enforcement.** Since 2025, auto-caption downloads additionally
  require a PO token, served by a local ``bgutil-ytdlp-pot-provider`` server on
  port 4416. Without it you get ``nsig extraction failed`` or HTTP 403, which
  looks like a network problem but is not.
* **HTTP 429.** Fetching more than a handful of captions in a row trips rate
  limiting. A fixed backoff (this script: 25 s between videos, 90 s on 429, up
  to 3 passes over whatever is still missing) is what makes a 35-video playlist
  finish instead of stalling at 15.

Usage::

    # one video
    python3 download_subs.py --url 'https://www.youtube.com/watch?v=ID'

    # a whole playlist (enumerates first, writes manifest.tsv)
    python3 download_subs.py --playlist 'https://www.youtube.com/playlist?list=PL...' \
        --out . --lang en

    # re-run only what is missing, custom pacing
    python3 download_subs.py --playlist '...' --sleep 30 --max-rounds 4
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

POT_ENDPOINT = "http://127.0.0.1:4416/"
RATE_LIMIT_RE = re.compile(r"HTTP Error 429|Too Many Requests", re.IGNORECASE)
BOT_RE = re.compile(r"Sign in to confirm|not a bot", re.IGNORECASE)
PO_TOKEN_RE = re.compile(r"nsig extraction failed|PO Token|403 Forbidden", re.IGNORECASE)


def run(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=False,
    )


def ytdlp_bin() -> str:
    return os.environ.get("YTDLP", "yt-dlp")


def check_pot_server(quiet: bool = False) -> bool:
    """The POT server answers 400 with a hint body when healthy; 5xx/refused = down."""
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen(POT_ENDPOINT, timeout=3)
        healthy = True                      # a 200 is also fine
    except urllib.error.HTTPError as exc:
        healthy = exc.code < 500            # 400 is the normal healthy response
    except Exception:
        healthy = False
    if not healthy and not quiet:
        print(
            "warning: no PO token server on :4416 -- auto-caption downloads will\n"
            "         fail with 'nsig extraction failed' or HTTP 403.\n"
            "         start it with:  node <bgutil>/server/build/main.js &\n"
            "         (see references/troubleshooting.md)",
            file=sys.stderr,
        )
    return healthy


def list_playlist(playlist_url: str, cookies_browser: str | None) -> list[dict]:
    cmd = [ytdlp_bin(), "--flat-playlist", "--no-update", "--ignore-errors",
           "--print", "%(id)s\t%(duration)s\t%(title)s", playlist_url]
    if cookies_browser:
        cmd[1:1] = ["--cookies-from-browser", cookies_browser]
    proc = run(cmd)
    rows: list[dict] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0]:
            continue
        vid, dur, title = parts[0], parts[1], "\t".join(parts[2:])
        try:
            duration = float(dur)
        except ValueError:
            duration = 0.0
        rows.append({"id": vid, "duration": duration, "title": title})
    return rows


def write_manifest(rows: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for i, row in enumerate(rows, 1):
            fh.write(f"{i:02d}\t{row['id']}\t{row['duration']:.1f}\t{row['title']}\n")


def have_sub(vtt_dir: str, vid: str) -> bool:
    if not os.path.isdir(vtt_dir):
        return False
    return any(f.startswith(vid + ".") and f.endswith(".vtt") for f in os.listdir(vtt_dir))


def probe_languages(vid: str, cookies_browser: str | None) -> tuple[list[str], dict]:
    """Return (available caption keys, metadata) for one video.

    Never guess language codes. YouTube's key set is per-video and irregular:
    a keynote may expose only ``en-orig`` while a normal upload exposes ``en``,
    and ``--list-subs`` output is not machine-parseable (it repeats the format
    list once per translation track). Asking for a key that does not exist
    burns a request and can return 429, so probe first via ``-J``.
    """
    cmd = [ytdlp_bin(), "--no-update", "--no-warnings", "-J", "--skip-download",
           f"https://www.youtube.com/watch?v={vid}"]
    if cookies_browser:
        cmd[1:1] = ["--cookies-from-browser", cookies_browser]
    proc = run(cmd)
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return [], {}
    try:
        import json

        info = json.loads(proc.stdout)
    except Exception:
        return [], {}
    keys = list((info.get("subtitles") or {}).keys())
    keys += [k for k in (info.get("automatic_captions") or {}) if k not in keys]
    return keys, info


def pick_language(available: list[str], want: str) -> str | None:
    """Choose the best available key for a requested language.

    Preference for ``want='en'``: ``en-orig`` first, then ``en``, then regional
    variants (``en-US``, ``en-GB``).

    ``-orig`` is preferred on purpose. When the video's spoken language *is* the
    requested one, ``en`` and ``en-orig`` carry the same content, but plain
    ``en`` goes through YouTube's translation path -- which is the endpoint that
    returns HTTP 429 under load. The ``-orig`` track is served directly. When
    the video is spoken in another language there is no ``en-orig`` at all, so
    ``en`` (a genuine translation) is picked as before.
    """
    if not available:
        return None
    base = want.split(".")[0].lower()
    lowered = {k.lower(): k for k in available}

    for candidate in (f"{base}-orig", base):
        if candidate in lowered:
            return lowered[candidate]

    derived = [k for k in available
               if k.lower().startswith(base + "-") or k.lower().startswith(base + "_")]
    if derived:
        derived.sort(key=lambda k: (len(k), k))
        return derived[0]
    return None


def download_one(vid: str, vtt_dir: str, want_lang: str,
                 cookies_browser: str | None) -> tuple[bool, str]:
    """Download the best matching caption track for one video."""
    available, _ = probe_languages(vid, cookies_browser)
    lang = pick_language(available, want_lang)
    if not lang:
        return False, f"no-{want_lang}-track"

    cmd = [
        ytdlp_bin(), "--no-update", "--no-warnings",
        "--write-auto-subs", "--write-subs",
        "--sub-langs", lang,
        "--skip-download",
        "-o", os.path.join(vtt_dir, "%(id)s.%(ext)s"),
        f"https://www.youtube.com/watch?v={vid}",
    ]
    if cookies_browser:
        cmd[1:1] = ["--cookies-from-browser", cookies_browser]
    proc = run(cmd)
    # Success is decided by the file on disk, not by the exit status: yt-dlp
    # exits non-zero and prints a scary traceback when *one* requested track
    # fails while another succeeded, and --write-subs will additionally attempt
    # manual tracks that may not exist. Check the artifact first.
    if have_sub(vtt_dir, vid):
        return True, f"ok ({lang})"

    blob = (proc.stdout or "") + (proc.stderr or "")
    if RATE_LIMIT_RE.search(blob):
        return False, "rate-limited"
    if BOT_RE.search(blob):
        return False, "bot-check"
    if PO_TOKEN_RE.search(blob):
        return False, "po-token"
    return False, "no-subs"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Download YouTube subtitles (playlist-aware).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="single video URL or bare video id")
    src.add_argument("--playlist", help="playlist URL")
    ap.add_argument("--out", default=".", help="working directory (default: .)")
    ap.add_argument("--lang", default="en", help="subtitle language(s), e.g. 'en' or 'en.*'")
    ap.add_argument("--cookies-from-browser", default="chrome",
                    help="browser to read cookies from (default: chrome; '' disables)")
    ap.add_argument("--sleep", type=float, default=25.0, help="seconds between videos")
    ap.add_argument("--backoff", type=float, default=90.0, help="seconds to wait after a 429")
    ap.add_argument("--max-rounds", type=int, default=3, help="retry passes over missing videos")
    ap.add_argument("--skip-pot-check", action="store_true")
    args = ap.parse_args(argv)

    workdir = os.path.abspath(args.out)
    vtt_dir = os.path.join(workdir, "vtt")
    os.makedirs(vtt_dir, exist_ok=True)
    cookies = args.cookies_from_browser or None

    if not args.skip_pot_check:
        check_pot_server()

    if args.playlist:
        print(f"enumerating playlist: {args.playlist}")
        rows = list_playlist(args.playlist, cookies)
        if not rows:
            print("error: playlist enumeration returned nothing "
                  "(try --cookies-from-browser chrome)", file=sys.stderr)
            return 1
        write_manifest(rows, os.path.join(workdir, "manifest.tsv"))
        print(f"wrote manifest.tsv ({len(rows)} videos)")
        ids = [r["id"] for r in rows]
    else:
        vid = args.url
        m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", vid)
        if m:
            vid = m.group(1)
        ids = [vid]

    pending = [v for v in ids if not have_sub(vtt_dir, v)]
    print(f"{len(ids) - len(pending)} already downloaded, {len(pending)} to fetch")

    for round_no in range(1, args.max_rounds + 1):
        if not pending:
            break
        print(f"\n--- round {round_no}/{args.max_rounds}: {len(pending)} videos ---")
        still: list[str] = []
        for i, vid in enumerate(pending, 1):
            ok, reason = download_one(vid, vtt_dir, args.lang, cookies)
            status = "ok" if ok else f"FAILED ({reason})"
            print(f"[{i}/{len(pending)}] {vid}: {status}")
            if not ok:
                still.append(vid)
                if reason == "rate-limited":
                    print(f"    429 hit -- backing off {args.backoff:.0f}s")
                    time.sleep(args.backoff)
                    continue
                if reason == "bot-check":
                    print("    cookies are missing or stale -- re-open YouTube in the browser")
                if reason == "po-token":
                    print("    PO token server is not running (port 4416)")
            if i < len(pending):
                time.sleep(args.sleep)
        pending = still

    total = len(ids)
    done = total - len(pending)
    print(f"\n{sub_status(done, total)}")
    if pending:
        print("still missing: " + ", ".join(pending))
        print("next: fix the reported cause, then re-run the same command "
              "(already-downloaded videos are skipped)")
        return 1
    return 0


def sub_status(done: int, total: int) -> str:
    return f"downloaded {done}/{total} subtitle files"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

# Pitfalls — the five things that break YouTube subtitle pipelines

Every item here was found by hitting it, on a real 35-video playlist. Symptoms
are quoted verbatim so you can grep for them.

---

## 1. Bot detection: cookies are not optional

**Symptom**

```
ERROR: [youtube] PjZ4kpLChpE: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies for the authentication.
```

**Cause.** YouTube rejects anonymous requests for many videos, including most
conference uploads. This is not a rate limit and retrying does not help.

**Fix.** Always pass `--cookies-from-browser chrome`. The browser must have an
active YouTube session. If cookies go stale you get the same error again —
re-open YouTube in that browser, then retry.

```bash
yt-dlp --cookies-from-browser chrome --write-auto-subs --sub-langs en --skip-download URL
```

**Note on Firefox/Safari.** `--cookies-from-browser firefox` works but is less
reliable on macOS because of keychain access; Chrome is the tested path.

---

## 2. PO token enforcement: 403 that looks like a network problem

**Symptom**

```
ERROR: [youtube] <id>: nsig extraction failed: Some formats may be missing
WARNING: [youtube] <id>: n challenge solving failed
ERROR: Unable to download video subtitles for 'en': HTTP Error 403: Forbidden
```

**Cause.** Since 2025, YouTube requires a PO (proof-of-origin) token even for
auto-generated captions. `yt-dlp` alone cannot produce one. The token comes from
a local provider server, `bgutil-ytdlp-pot-provider`, listening on port 4416.

This is the single most misdiagnosed failure: it looks like a CDN or network
problem, so people retry, change VPNs, or downgrade yt-dlp. None of that helps.

**Diagnosis.** Ask yt-dlp which providers it sees:

```bash
yt-dlp --verbose --simulate 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' 2>&1 \
  | grep -i 'PO Token Providers'
```

Healthy output includes `bgutil:http`:

```
[debug] [youtube] [pot] PO Token Providers: bgutil:http-2.0.2 (external), bgutil:script-node-2.0.2 (external)
```

If that line is missing entirely, the plugin is not installed for the Python
environment yt-dlp runs in. If it is present but downloads still 403, the server
process is not running.

**Check the server.** A `400` response is healthy:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4416/
```

`000` = connection refused (not running). `5xx` = running but broken.

**Start the server.**

```bash
git clone https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git
cd bgutil-ytdlp-pot-provider/server
npm install && npm run build
nohup node build/main.js > /tmp/pot_server.log 2>&1 &
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4416/   # expect 400
```

**Install the plugin into the right environment.** The trap: `yt-dlp` is often
installed via `pipx`, whose venv `site-packages` is read-only, so a plain
`pip install` fails with:

```
OSError: [Errno 1] Operation not permitted: '.../site-packages/yt_dlp_plugins'
```

Install to a writable directory and point `PYTHONPATH` at it:

```bash
~/.local/pipx/venvs/yt-dlp/bin/python -m pip install --target "$PWD/pylib" \
  bgutil-ytdlp-pot-provider
PYTHONPATH="$PWD/pylib" yt-dlp --version
```

Verify the provider line appears *with* `PYTHONPATH` set — that combination is
what the download script needs to run under.

**The server does not survive reboot.** It is a `nohup` background process.
After a restart, the health check returns `000` and every download fails until
you start it again.

---

## 3. HTTP 429: the pipeline stalls at ~40%

**Symptom**

```
ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests
```

**Cause.** Fetching captions for many videos back to back trips YouTube's rate
limiter. On a 35-video playlist, an unpaced loop completes 15 and then fails the
remaining 20 — and, importantly, it *keeps* failing on retry because the limiter
window has not reset.

**Fix.** Pace it, and retry only what is missing:

- 25 s between videos
- 90 s backoff when a 429 is seen
- up to 3 full passes over the still-missing set

This reaches 35/35. `scripts/download_subs.py` implements exactly this; the
defaults are tuned, so prefer changing `--sleep` over removing pacing.

**Why the retry structure matters.** A single pass that sleeps on 429 still
fails the videos it already skipped. Looping over only the missing IDs means
each pass makes forward progress and the total wait stays bounded.

---

## 4. Language keys are per-video, and `en` ≠ `en-orig`

**Symptoms**

```
ERROR: [youtube] <id>: Requested format is not available. Use --list-formats for a list of available formats
ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests
```

**Cause.** The caption key set differs per video:

- an English talk sometimes exposes **only** `en-orig` (no plain `en`)
- a talk in another language exposes `en` only as a *translation*
- regional variants (`en-US`, `en-GB`, `zh-Hans`) appear unpredictably

So `--sub-langs en` is not a safe constant.

**Why `en-orig` is not just a synonym for `en`.** For an English video where
both keys exist, `en` goes through YouTube's translation endpoint and returns
**429 under load**, while `en-orig` is served directly and succeeds. Verified by
requesting each key on the same video minutes apart: `en` → 429, `en-orig` → 200.

**Fix.** Probe, then request the exact key. Do not parse `--list-subs`:

its output repeats the format list once per translation track —

```
ab       Abkhazian    vtt, srt, ..., vtt, srt, ..., vtt, srt, ...
```

— and ends with a misleading `<id> has no subtitles` line even when auto
captions exist. Use `-J` instead:

```bash
yt-dlp -J --skip-download URL | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('manual:', list((d.get('subtitles') or {}).keys()))
print('auto:  ', [k for k in (d.get('automatic_captions') or {}) if k.startswith('en')])
"
```

Selection order in `scripts/download_subs.py`: `en-orig` → `en` → regional
variants.

---

## 5. Judge success by the artifact, not the exit code

**Symptom.** The script reports failure, but a valid `.vtt` file is on disk.

**Cause.** When you pass both `--write-subs` and `--write-auto-subs`, yt-dlp
tries manual tracks that may not exist, and exits non-zero with a traceback even
though the auto-caption track downloaded fine. Error text from the *failed*
track then gets misclassified as the reason the whole download failed — for
example a 403 on a nonexistent manual track looks like a PO token problem.

**Fix.** Check the output file first, classify errors only if it is absent:

```python
if have_sub(vtt_dir, vid):
    return True, f"ok ({lang})"
blob = proc.stdout + proc.stderr
if RATE_LIMIT_RE.search(blob): return False, "rate-limited"
...
```

This one ordering change was the difference between "0/1 downloaded" and "1/1
downloaded" on the same command.

# Troubleshooting

Symptom → cause → fix, in the order these failures actually show up.

---

## Preflight checklist

Run all four before a batch. Each catches a different class of failure, and all
four are cheap.

```bash
# 1. yt-dlp present and recent
yt-dlp --version                      # expect a date-like version, e.g. 2026.11.23

# 2. PO token provider registered
yt-dlp --verbose --simulate 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' 2>&1 \
  | grep -i 'PO Token Providers'      # must include bgutil:http

# 3. POT server alive (400 is healthy, 000 is down)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4416/

# 4. cookies present and fresh
yt-dlp --cookies-from-browser chrome --list-subs \
  'https://www.youtube.com/watch?v=dQw4w9WgXcQ' 2>&1 | tail -3
```

If step 4 prints `Sign in to confirm you're not a bot`, open YouTube in Chrome,
confirm you are signed in, then retry.

---

## `Sign in to confirm you're not a bot`

**Fix:** add `--cookies-from-browser chrome`.

If it persists, the cookie jar is stale or the browser session expired. Open
`https://www.youtube.com` in that browser, verify you are logged in, and retry.
On macOS, Chrome may prompt for keychain access the first time yt-dlp reads
cookies — allow it.

**Do not** work around this with `--extractor-args youtube:player_client=...`
tricks. They trade one failure for a less obvious one.

---

## `nsig extraction failed` / `HTTP Error 403: Forbidden`

**Cause:** missing PO token. See `references/pitfalls.md` §2 for the full
install path.

Minimal recovery:

```bash
# is the server up?
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4416/
# 400 -> up, 000 -> start it:
cd ~/bgutil-ytdlp-pot-provider/server && nohup node build/main.js > /tmp/pot_server.log 2>&1 &
```

Then confirm the plugin is visible to the yt-dlp you are actually running:

```bash
yt-dlp --verbose --simulate URL 2>&1 | grep -i 'PO Token Providers'
```

If the line is absent, the plugin is installed for a different Python. With a
pipx-installed yt-dlp, install into a writable target and use `PYTHONPATH`:

```bash
~/.local/pipx/venvs/yt-dlp/bin/python -m pip install --target "$PWD/pylib" \
  bgutil-ytdlp-pot-provider
PYTHONPATH="$PWD/pylib" yt-dlp --verbose --simulate URL 2>&1 | grep -i 'PO Token'
```

---

## `HTTP Error 429: Too Many Requests`

**Cause:** too many caption requests too fast.

**Fix:** pace and retry only missing items. `scripts/download_subs.py` does this
by default (25 s spacing, 90 s backoff, 3 passes). If you are scripting it
yourself:

```bash
for id in $IDS; do
  yt-dlp --cookies-from-browser chrome --write-auto-subs --sub-langs "$LANG" \
    --skip-download -o 'vtt/%(id)s.%(ext)s' "https://www.youtube.com/watch?v=$id"
  sleep 25
done
```

**Why 429 can follow a successful lookup.** If you probe a video's language keys
and then request a key that does not exist, the failed request can itself return
429 — so the error appears to be about rate limiting when the real problem is a
wrong language code. Probe with `-J`, pick an existing key.

**When `en` 429s but `en-orig` works:** prefer `en-orig`. See
`references/pitfalls.md` §4.

---

## `Requested format is not available`

Two different causes, easy to confuse:

1. **You asked for a subtitle language that does not exist** — e.g.
   `--sub-langs en` on a video that only has `en-orig`. Fix: probe keys with
   `-J` first.
2. **`--write-subs` on a video with no manual captions** — the manual track does
   not exist, so yt-dlp errors even though the auto track may download. Check
   the file on disk before treating this as fatal.

---

## `Unable to download video subtitles for 'xx'` but the file exists

Harmless. yt-dlp reports per-track failures while other tracks succeed. Judge by
the artifact: if `vtt/<id>.<lang>.vtt` exists and is non-trivial in size, the
download worked. This is why `download_subs.py` checks the file before
classifying errors.

---

## Transcript is much shorter than expected

**Diagnose by word count per minute.** Speech is 130–160 wpm.

```bash
python3 scripts/vtt2txt.py --stats vtt/*.vtt
```

| Observed | Likely cause |
|---|---|
| ~17% low, text reads fine | token-extraction parser dropped the untagged first word of every timed line — use the rolling merge |
| Far too high, stuttering | strip-and-join on rolling VTT — same fix |
| Near zero | wrong file, or a `json3`/`srv3` format parsed as VTT |
| ~60 words for a 20-min video | download truncated; re-download that video |

---

## Summary quality problems

| Symptom | Cause | Fix |
|---|---|---|
| Numbers not in the transcript | subagent hallucinated | instruct subagents to use only transcript values; spot-check every number |
| Speaker names garbled | ASR mishearing (`Kube Sarnotzer` → `Kubestronaut`) | cross-check against video description; fix obvious ASR errors |
| Titles truncated with `...` | YouTube truncates the display title | read the description's first line, which carries the full title |
| Summary longer than the transcript | over-generation | cap body at 200–350 Chinese characters |
| Some videos have no summary | batch silently dropped rows | verify `ls summaries/ | wc -l` equals manifest line count |

**On ASR errors:** auto-captions mangle proper nouns constantly. Cross-check
product and project names against the video description before publishing, and
when a term cannot be confirmed, keep the transcript's spelling and mark it as
uncertain rather than silently normalizing it.

---

## Duration and metadata

- **Duration should floor, not round.** A 891-second video is `14min`, not
  `15min`. Rounding up inflates perceived length and makes short talks look
  longer than they are.
- **Word counts** should be computed from the converted text, not from the VTT
  file size.
- **`manifest.tsv`** is the source of truth for numbering; keep `NN` in the same
  order as the playlist so index links stay stable across re-runs.

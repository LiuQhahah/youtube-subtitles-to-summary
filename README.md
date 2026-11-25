# youtube-subtitles-to-summary

A Claude Code / Codex skill that turns a YouTube video or an entire playlist
into clean transcripts plus one summary file per talk — without watching
anything.

```bash
npx skills add LiuQhahah/youtube-subtitles-to-summary -g
```

---

## Why another one?

There are a dozen YouTube transcript skills. Most of them break on the same five
things, and break *silently* — which is worse than failing. This one exists
because building a 35-talk conference archive hit all five in sequence:

| Failure | What happens | Naive pipeline |
|---|---|---|
| **Bot detection** | `Sign in to confirm you're not a bot` | fails every video |
| **PO token enforcement** | `nsig extraction failed` / 403, looks like a network problem | retries forever, blames CDN |
| **HTTP 429** | batch stalls at ~40% complete | no pacing, no resume |
| **Language-key irregularity** | `en` 429s while `en-orig` works; some videos have only `-orig` | hardcodes `--sub-langs en` |
| **Rolling word-level VTT** | transcript reads fine but is **16% short** | token extraction drops untagged words |

That last one is the reason this skill exists. On a 790-word keynote, the
token-extraction approach used by several published skills produces **660
words** — 130 words silently missing, in text that still reads fluently. A
summary built on it is confidently wrong, and nothing signals the loss.

This skill's parser merges cues by word-level longest suffix/prefix overlap
instead, which is lossless regardless of which words YouTube tagged.

---

## What you get

```
workdir/
  manifest.tsv          NN \t videoId \t duration \t title
  vtt/<id>.<lang>.vtt   raw captions
  text/<id>.<lang>.txt  clean transcript
  summaries/NN_<id>.md  one file per video
  INDEX.md              sortable table + topic navigation
  SUMMARIES.md          all bodies concatenated
```

One summary per video, not one giant document: each file is independently
linkable and its header carries duration, word count, and speaker, so a reader
can decide in two seconds whether the talk is worth reading.

---

## Requirements

| Requirement | Why |
|---|---|
| `yt-dlp` (recent) | caption download; must be new enough for PO token support |
| Python 3.9+ | scripts use only the standard library |
| Chrome logged into YouTube | cookies; without them every request hits the bot check |
| `bgutil-ytdlp-pot-provider` server on `:4416` | PO token; without it auto-caption downloads 403 |

The PO token requirement is the one people miss. Full setup in
[`references/pitfalls.md`](references/pitfalls.md) §2.

---

## Usage

### Check prerequisites first

```bash
yt-dlp --verbose --simulate 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' 2>&1 \
  | grep -i 'PO Token Providers'
```

Must show a `bgutil:http` provider. Then:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4416/
```

`400` is healthy. `000` means the server is not running.

### Whole playlist

```bash
python3 scripts/download_subs.py \
  --playlist 'https://www.youtube.com/playlist?list=PL...' \
  --out . --lang en
```

Writes `manifest.tsv` and downloads everything. Re-runnable: existing `.vtt`
files are skipped, so a failed batch resumes by re-running the same command.

Default pacing (25 s between videos, 90 s on 429, 3 passes) is what takes a
35-video playlist from 15/35 to 35/35. Tune it, don't remove it.

### One video

```bash
python3 scripts/download_subs.py --url 'https://www.youtube.com/watch?v=PjZ4kpLChpE' --out .
```

### Convert to text

```bash
python3 scripts/vtt2txt.py vtt/*.vtt          # -> text/*.txt
python3 scripts/vtt2txt.py --stats vtt/*.vtt  # word counts + detected format
```

### Then summarize

Dispatch parallel subagents, 10–12 videos each, each writing its own
`summaries/NN_<id>.md`. The template and quality bar are in
[`SKILL.md`](SKILL.md); a worked example is in
[`examples/walkthrough.md`](examples/walkthrough.md).

---

## Layout

| Path | Purpose |
|---|---|
| [`SKILL.md`](SKILL.md) | The skill itself — procedure, templates, verification |
| [`scripts/download_subs.py`](scripts/download_subs.py) | Playlist enumeration, language probing, paced download, error classification |
| [`scripts/vtt2txt.py`](scripts/vtt2txt.py) | VTT → text, rolling/plain auto-detect |
| [`references/pitfalls.md`](references/pitfalls.md) | The five failure modes, with verbatim symptoms |
| [`references/vtt-formats.md`](references/vtt-formats.md) | Rolling vs plain VTT; the measured 660 vs 790 word result |
| [`references/troubleshooting.md`](references/troubleshooting.md) | Symptom → cause → fix |
| [`examples/walkthrough.md`](examples/walkthrough.md) | One video end to end, including a bad-summary counterexample |

---

## Measured results

Built on the KubeCon NA keynote playlist (35 talks, 4.5 hours, 39,167 words):

| Metric | Value |
|---|---|
| Subtitles downloaded | 35/35 (unpaced: 15/35) |
| Words recovered vs token-extraction parser | +16.5% on a sampled talk |
| Per-video summaries | 35/35, median 322 Chinese characters |
| Broken transcript links | 0 |

---

## Verification before trusting output

```bash
# no suspiciously short transcripts
python3 scripts/vtt2txt.py --stats vtt/*.vtt | awk -F'\t' '$3 < 60 {print "SHORT:", $0}'

# every video has a summary
ls summaries/ | wc -l          # must equal manifest line count

# no broken links
grep -ho 'text/[A-Za-z0-9_.-]*\.txt' summaries/*.md | sort -u \
  | while read p; do [ -f "$p" ] || echo "BROKEN: $p"; done
```

---

## License

MIT. See [LICENSE](LICENSE).

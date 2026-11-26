---
name: youtube-subtitles-to-summary
description: "Summarize a YouTube video or an entire playlist from its subtitles: fetch the transcript, convert the rolling word-level VTT losslessly, and write one summary file per talk in the user's own language. Use when the user wants a YouTube summary, video notes, lecture or conference-talk digests, podcast recaps, or a readable transcript without watching. Handles the failure modes that break naive pipelines: bot detection (cookies required), PO token enforcement (bgutil server on :4416), HTTP 429 rate limiting (paced retries), per-video language-key irregularities (en vs en-orig), and the word-level rolling VTT format whose naive parse silently drops the first word of every timed line (~17% of words)."
license: MIT
metadata: {"author":"Qiang Liu","language":"en","category":"media","short-description":"YouTube video or playlist subtitles to per-talk summaries in the user's language","version":"3.0.0"}
---

# YouTube Subtitles → Summary

Turn a YouTube video or an entire playlist into clean transcripts plus one
summary file per talk, without watching anything.

The value of this skill is not "run yt-dlp". It is the five things that make a
naive pipeline fail, each of which cost a real debugging session to identify:

| Failure | Symptom | What naive pipelines do |
|---|---|---|
| Bot detection | `Sign in to confirm you're not a bot` | omit cookies, fail on every video |
| PO token enforcement | `nsig extraction failed`, HTTP 403 | blame the network, never retry correctly |
| HTTP 429 | `Unable to download video subtitles: HTTP Error 429` | fetch all videos in a loop, stall at ~40% |
| Language-key irregularity | `Requested format is not available`, or 429 on a track that exists | hardcode `--sub-langs en` |
| Rolling word-level VTT | transcript looks fine but is missing ~17% of words | strip tags and join, or keep only `<c>` tokens |

## When to use

- The user gives a YouTube URL or playlist and wants to know what is in it
- Conference replay archives (KubeCon, P99 CONF, re:Invent, PyCon...) to be
  triaged by topic
- A long interview, course, or podcast episode to be read instead of watched
- Any batch where the user wants **one summary file per video**, not one giant
  document

## When not to use

- The user wants the video/audio file itself — use `yt-dlp -f best` directly
- Live streams (captions are unstable mid-stream)
- Non-YouTube sources without a subtitle track (Vimeo, Twitch) — different flow

## Procedure

### 1. Preflight

```bash
yt-dlp --version
python3 --version          # 3.9+; this skill uses no third-party packages
```

Then confirm the PO token provider is registered. This is the check that
predicts whether auto-caption downloads will work at all:

```bash
yt-dlp --verbose --simulate 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' 2>&1 \
  | grep -i 'PO Token Providers'
```

Expected: a line containing `bgutil:http` (e.g. `bgutil:http-2.0.2 (external)`).
If the line is absent, no PO token will be generated and every auto-caption
download will fail with 403. See `references/troubleshooting.md` for install.

Confirm the local POT server is up:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4416/
```

**A `400` is healthy.** The server answers 400 with a hint body on a bare GET.
`000` (connection refused) or `5xx` means it is down.

`download_subs.py` runs this same probe itself and prints a warning when the
server is down — it does not abort, because manual-caption videos download fine
without a PO token and only the auto-caption ones 403. If you have already done
the check above and want to skip the duplicate probe, pass `--skip-pot-check`;
the flag exists only for that case.

### 2. Enumerate the playlist

```bash
python3 scripts/download_subs.py --playlist '<playlist_url>' --out . --lang en
```

This writes `manifest.tsv` (`NN \t videoId \t duration \t title`) and downloads
every subtitle. For a single video:

```bash
python3 scripts/download_subs.py --url '<video_url>' --out .
```

The script is re-runnable: videos whose `.vtt` already exists are skipped, so a
failed batch is resumed by running the same command again.

**Pacing defaults, and why they are not optional.** 25 s between videos, 90 s
backoff on 429, up to 3 passes over whatever is still missing. A 35-video
KubeCon playlist fails at 15/35 without this and reaches 35/35 with it. Tune
with `--sleep`, `--backoff`, `--max-rounds`; do not remove the pacing.

**Do not hardcode `--sub-langs en`.** The script probes each video's real
caption keys via `-J` and picks the best match, because the key set is
per-video: a keynote may expose only `en-orig`, a normal upload exposes `en`, and
a non-English talk exposes `en` only as a translation. Requesting a key that
does not exist burns a request and frequently returns 429. See
`references/pitfalls.md`.

### 3. Convert VTT to clean text

```bash
python3 scripts/vtt2txt.py vtt/*.vtt
```

Writes `text/<videoId>.<lang>.txt`. Then confirm the parser picked the right
branch before summarizing:

```bash
python3 scripts/vtt2txt.py --stats vtt/*.vtt
```

Check the format column, not the word count. `--stats` prints
`<path>\t<format>\t<count>`, where format is `rolling` when the overlap-merge
branch ran and `plain` when it did not. For an auto-caption file, `plain` means
the detector missed and you are about to summarize a lossy parse.

Word count alone cannot make this call: 660 words in 4 minutes is 165 wpm and
757 is 190 wpm — both are plausible speech rates, so a short-looking count may
just be a fast speaker. Use the count only to catch the catastrophic case (a
4-minute talk under ~60 words). See `references/vtt-formats.md`.

### 4. Summarize

How you fan this out depends on what your agent supports. The batching rules are
the same either way; only the dispatch mechanism differs.

**Batching rules (all paths):**

- 10–12 videos per batch, never more. Output quality degrades and responses get
  truncated past that.
- Each batch writes its own files directly, so no single response has to carry
  all the text.
- Each summarizer gets: the batch's rows from `manifest.tsv` (`NN \t videoId \t
  duration \t title` — four columns, no word count), the path to `text/`, the
  instruction to read each file **in full**, the exact output path and template,
  **the target language spelled out explicitly** (a subagent does not see the
  user's request and will otherwise default to the transcript's language), and an
  explicit prohibition on inventing numbers not present in the transcript.
  If you want word counts in the brief, join them in yourself from
  `vtt2txt.py --stats`; `manifest.tsv` does not carry that column.

**Path A — agent supports parallel subagents** (Claude Code, Codex, Cursor,
Gemini CLI, Copilot, OpenCode, and most of the others): dispatch one subagent per
batch concurrently. This is what the 35-talk archive used — 3 subagents × 12
videos, roughly 4 minutes wall clock.

**Path B — no subagent support, or one long-running agent:** process batches
sequentially in the same context, but write each summary to disk before starting
the next batch and re-read the transcript rather than recalling it. Slower, same
output. The per-batch file boundary is what keeps quality from decaying.

**Path C — scripting a non-agent LLM:** skip the agent entirely. Read
`text/*.txt`, and for each file call your model with the template from "Per-video
summary template" as the system prompt and the transcript as the user message.
Put the target language in that system prompt too — it is the one thing the model
cannot infer from a transcript in another language. Batch by 10–12 for cost, not
for context reasons.

Whatever the path, verify the batch count matches the manifest before assembling
the index — a silently dropped batch is the most common failure at this step.

### 5. Assemble the index

Write `INDEX.md` as a sortable table plus a topic navigation, and
`SUMMARIES.md` as the concatenation of all per-video bodies. Both are cheap to
generate from the summary files and much easier to skim than prose.

## Output layout

```
<workdir>/
  manifest.tsv          NN \t videoId \t duration \t title
  vtt/<videoId>.<lang>.vtt
  text/<videoId>.<lang>.txt
  summaries/NN_<videoId>.md
  INDEX.md
  SUMMARIES.md
```

One summary per video, filename `NN_<videoId>.md`, is deliberate: each file is
independently quotable and linkable, and its header carries enough metadata to
decide whether the talk is worth reading.

### Language: write the summary in the user's language

The summary language is **the language the user is talking to you in**, not a
fixed one, and not necessarily the language of the video. Ask if it is genuinely
ambiguous (the user writes in English but the videos are Chinese, or a
multilingual playlist); otherwise infer it from the request and go.

Do not translate the transcript first and summarize the translation. Read the
original transcript and write the summary directly in the target language — one
pass, not two. A translation step in the middle costs tokens and drifts.

Keep technical terms, version numbers, and product names in their original form
regardless of summary language; do not translate `io_uring`, `Karpenter`,
`containerd`, or `eBPF`.

If the user wants several languages, write one summary file per language and
suffix it: `NN_<videoId>.<lang>.md`.

### Per-video summary template

The template below is shown in Chinese because that is the most common case, but
every field is language-neutral — substitute the user's language, and note the
header says 原标题 / `<lang>`, not 英文标题 / `en`: the source video may not be
English. Filename stays `NN_<videoId>.md`; only the prose changes.

```markdown
# <Series> 演讲 #3：<标题，用目标语言>

> 原标题：<original title> by <speaker>（15min，约 2283 词）
> 演讲者：<speaker>, <org>
> 原始字幕：[text/<videoId>.<lang>.txt](text/<videoId>.<lang>.txt)

---

**主题与背景**：... **核心内容/技术亮点**：... **演讲者观点/结论**：...

---

*摘要由字幕全文分析生成，完整转录见 `text/<videoId>.<lang>.txt`。*
```

Target 200–350 characters of body for Chinese and Japanese; for languages
written in words rather than characters, target roughly 120–200 words, which
carries about the same information. The point is one tight paragraph, not a
character quota for its own sake.

## Verification

Run these before reporting done:

```bash
# 1. every video has a transcript and none is suspiciously short
python3 scripts/vtt2txt.py --stats vtt/*.vtt | awk -F'\t' '$3 < 60 {print "SHORT:", $0}'

# 2. every video has a summary
ls summaries/ | wc -l   # must equal the manifest line count

# 3. no broken transcript links inside summaries
links=$(grep -ho 'text/[A-Za-z0-9_.-]*\.txt' summaries/*.md | sort -u)
echo "unique transcript links: $(printf '%s\n' "$links" | grep -c .)"
[ -n "$links" ] || echo "NO LINKS FOUND: the link check is not actually running"
printf '%s\n' "$links" | while read p; do [ -f "$p" ] || echo "BROKEN: $p"; done
```

Check 3 must print a non-zero link count. A silent pass and a check that matched
nothing look identical from the outside — that is exactly how a broken link
check hides for months. Note the `.` inside the character class: real targets are
`text/<id>.<lang>.txt`, so a class of `[A-Za-z0-9_-]` matches nothing at all.

A word count under ~60 for a video longer than 30 s means the caption track is
wrong or the parse dropped content — investigate before summarizing, because a
summary of a truncated transcript is confidently wrong.

## Pitfalls

Full detail in `references/pitfalls.md` and `references/troubleshooting.md`.
The five that matter most:

1. **Cookies are mandatory.** `--cookies-from-browser chrome` (the browser must
   be logged into YouTube). No cookies → bot check → zero downloads.
2. **`en` vs `en-orig` is not cosmetic.** For an English talk, `en` is served
   through the translation path and returns 429 under load, while `en-orig` is
   served directly and succeeds. Prefer `-orig` when it exists.
3. **Never parse `--list-subs`.** Its output repeats the format list once per
   translation track and is not machine-readable. Use `-J` and read
   `subtitles` / `automatic_captions`.
4. **Judge success by the file, not the exit code.** yt-dlp exits non-zero and
   prints a traceback when one requested track fails while another succeeded.
   Check for the `.vtt` on disk first, then classify the error.
5. **Auto-caption VTT is a rolling window.** Each cue's last line is the whole
   current window; consecutive cues overlap. Merge by word-level longest
   suffix/prefix overlap. Stripping tags duplicates text; keeping only `<c>`
   tokens drops untagged words.

## Files

| Path | Purpose |
|---|---|
| `scripts/download_subs.py` | Playlist enumeration, language probing, paced download with 429/bot/PO-token classification |
| `scripts/vtt2txt.py` | VTT → text; detects rolling vs plain format and merges losslessly |
| `references/pitfalls.md` | The five failure modes, with symptoms and fixes |
| `references/vtt-formats.md` | Rolling vs plain VTT, why token extraction loses words |
| `references/troubleshooting.md` | PO token install, cookie refresh, 429 strategy |
| `examples/` | Real transcript excerpt and summary, for format reference |

## Attribution

The failure-mode knowledge here comes from building a 35-talk KubeCon keynote
archive end to end. The VTT parser replaces a token-extraction approach that
measurably lost 134 of 757 words on real auto-captions, 128 of them the untagged first word of a timed line.

# youtube-subtitles-to-summary

[![skills.sh](https://skills.sh/b/LiuQhahah/youtube-subtitles-to-summary)](https://skills.sh/LiuQhahah/youtube-subtitles-to-summary)

An agent skill that turns a YouTube video or an entire playlist into clean
transcripts plus one summary file per talk — without watching anything. The
summary is written in **your** language, not the video's, so an English talk
yields Chinese notes if that is what you asked for, and vice versa.

It is a plain [Agent Skills](https://skills.sh) package: `SKILL.md` plus
stdlib-only Python scripts, no platform-specific API. The `skills` CLI installs
it into any of its supported agents — Claude Code, Codex, Cursor, Gemini CLI,
GitHub Copilot, OpenCode, Cline, Windsurf, Zed, Amp, Goose, Continue, Qwen Code,
Kimi Code CLI, Warp and about 65 more.

```bash
npx skills add LiuQhahah/youtube-subtitles-to-summary -g
```

Install for specific agents instead of everything:

```bash
npx skills add LiuQhahah/youtube-subtitles-to-summary -g --agent claude-code --agent codex
npx skills add LiuQhahah/youtube-subtitles-to-summary -g --agent '*' -y   # all detected
```

Or skip the CLI entirely — it is just files. Clone anywhere and point your agent
at `SKILL.md`, or copy the directory into whatever skills path your agent reads
(`.claude/skills/`, `.codex/skills/`, `.cursor/rules/`, ...).

**Read the installer output, not just its last line.** `skills add` prints `Done!`
even when an individual agent failed — a permission error on an existing install
looks like this:

```
✗ youtube-subtitles-to-summary → Claude Code: EPERM: operation not permitted, unlink '.../LICENSE'
└  Done!  Review skills before use; they run with full agent permissions.
```

That is a failed install behind a success message. Check that each agent line
starts with `✓`, or install without `-g` into the project directory, which does
not touch protected user-level paths.

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
| **Rolling word-level VTT** | transcript reads fine but is **~17% short** | token extraction drops the first word of every timed line |

That last one is the reason this skill exists. On a 4-minute keynote, the
token-extraction approach used by several published skills drops **the first
word of every timed line** — 129 of 789 words on the `en` track, 134 of 757 on
the `en-orig` track this script actually downloads. The surviving text still
reads fluently. A summary built on it is confidently wrong, and nothing signals
the loss.

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
[`references/pitfalls.md`](skills/youtube-subtitles-to-summary/references/pitfalls.md) §2.

**On that last row, in plain terms.** The POT server is not something this
repository ships or maintains. It is [`Brainicism/bgutil-ytdlp-pot-provider`](https://github.com/Brainicism/bgutil-ytdlp-pot-provider),
an independent community project that you clone, build, and run locally. It is
the de facto standard for this and widely used, but it is third-party code
running on your machine with a listening port — review it yourself before
trusting it, the same way you would any dependency. If you would rather not run
it, the skill still works for videos whose captions download without a PO token;
you will just hit HTTP 403 on the ones that require one.

Nothing else here reaches the network except `yt-dlp` itself, fetching the
video's caption track from YouTube.

---

## Usage

All commands below run from the skill directory — the folder containing
`SKILL.md`. In this repo that is
[`skills/youtube-subtitles-to-summary/`](skills/youtube-subtitles-to-summary/);
after `npx skills add` it is wherever your agent installed the skill (for
Claude Code, `.claude/skills/youtube-subtitles-to-summary/`).

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

Batches of 10–12 videos, each batch writing its own `summaries/NN_<id>.md`.
Dispatch them as parallel subagents if your agent has them; otherwise run the
batches sequentially and write each file to disk before starting the next. The
template and quality bar are in [`SKILL.md`](skills/youtube-subtitles-to-summary/SKILL.md); a worked example is in
[`examples/walkthrough.md`](skills/youtube-subtitles-to-summary/examples/walkthrough.md).

---

## Layout

| Path | Purpose |
|---|---|
| [`SKILL.md`](skills/youtube-subtitles-to-summary/SKILL.md) | The skill itself — procedure, templates, verification |
| [`scripts/download_subs.py`](skills/youtube-subtitles-to-summary/scripts/download_subs.py) | Playlist enumeration, language probing, paced download, error classification |
| [`scripts/vtt2txt.py`](skills/youtube-subtitles-to-summary/scripts/vtt2txt.py) | VTT → text, rolling/plain auto-detect |
| [`references/pitfalls.md`](skills/youtube-subtitles-to-summary/references/pitfalls.md) | The five failure modes, with verbatim symptoms |
| [`references/vtt-formats.md`](skills/youtube-subtitles-to-summary/references/vtt-formats.md) | Rolling vs plain VTT; the measured 623 vs 757 word result and why the loss is systematic |
| [`references/troubleshooting.md`](skills/youtube-subtitles-to-summary/references/troubleshooting.md) | Symptom → cause → fix |
| [`examples/walkthrough.md`](skills/youtube-subtitles-to-summary/examples/walkthrough.md) | One video end to end, including a bad-summary counterexample |
| [`LICENSE`](LICENSE) | MIT, at the repo root so GitHub and directory tools detect it; the skill folder keeps its own copy so the license travels with an install |

---

## Measured results

Built on the KubeCon NA keynote playlist (35 talks, 4.5 hours, 39,167 words):

| Metric | Value |
|---|---|
| Subtitles downloaded | 35/35 (unpaced: 15/35) |
| Words recovered vs token-extraction parser | +21.5% on a sampled talk (757 vs 623, `en-orig` track) |
| Per-video summaries | 35/35, median 322 characters across the whole file (273 in the body paragraph that SKILL.md constrains); this archive was written in Chinese |
| Broken transcript links | 0 |

The recovery figure is a *rate relative to the lossy parser*, not a share of the
transcript: 134 of the 757 words are recovered, which is 21.5% more text than
token extraction produces and 17.7% of the finished transcript. Both denominators
are legitimate; they are not interchangeable.

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

## Platform support

This is a plain Agent Skills package — `SKILL.md` plus stdlib-only Python. No
platform-specific API, no bundler, no dependencies. The only capability that
varies is how step 4 fans out work:

| Agent capability | Path |
|---|---|
| parallel subagents | dispatch 10–12 videos per subagent (fastest) |
| single agent only | run batches sequentially, write each file before the next |
| no agent (scripting an LLM) | call the model per transcript with the template as system prompt |

Everything before step 4 — download, convert, verify — is plain `python3` and
behaves identically everywhere.

---

## License

MIT. See [LICENSE](skills/youtube-subtitles-to-summary/LICENSE).

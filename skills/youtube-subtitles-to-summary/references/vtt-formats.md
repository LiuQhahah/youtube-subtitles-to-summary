# VTT formats — why the parser matters more than it looks

YouTube emits two structurally different WebVTT files. Treating them the same
silently corrupts the transcript, and the corruption is invisible unless you
count words.

---

## Format A: rolling word-level captions (auto-generated)

Produced by `--write-auto-subs`. Every word carries its own timestamp, and cues
overlap heavily because each cue re-renders the current scroll window.

```vtt
WEBVTT
Kind: captions
Language: en

00:00:00.100 --> 00:00:01.855 align:start position:0%

AI <00:00:01.412><c>workloads</c>

00:00:01.855 --> 00:00:01.865 align:start position:0%
AI workloads


00:00:01.865 --> 00:00:03.334 align:start position:0%
AI workloads
can <00:00:02.107><c>run </c><00:00:02.349><c>tens </c><00:00:02.591><c>of </c><00:00:02.833><c>thousands </c><00:00:03.075><c>of </c><00:00:03.317><c>GPU</c>
```

Three properties matter:

1. **Each cue's last line is the entire current window**, not just the new
   words. Cue 3's last line contains "AI workloads" again.
2. **Consecutive cues overlap.** Lines repeat across cue boundaries.
3. **Not every word is wrapped in `<c>`.** In cue 3, the leading `can` has no
   `<c>` tag — only the words that are *new in this cue* get one.

### Two wrong parsers

**Wrong #1 — strip tags and join every line.**

```python
text = re.sub(r'<[^>]+>', '', content)
```

Result: heavy duplication. "AI workloads" appears once per cue in the window,
so a 757-word talk yields several thousand words of stutter.

**Wrong #2 — extract only `<ts><c>word</c>` tokens.**

```python
for m in re.finditer(r'<(\d+:\d+:\d+\.\d+)><c>(.*?)</c>', content):
    words.append(m.group(2))
```

This looks clean and is the approach shipped in several published skills. It is
**lossy**: property 3 means any word YouTube did not tag disappears, and the
untagged word is systematically the **first word of every timed line**. On the
`en` track of `PjZ4kpLChpE` (a 4-minute KubeCon keynote) this yields **660 words
instead of 789** — 129 words, 16.3% of the content, silently missing. On the
`en-orig` track of the same video, which is what `download_subs.py` selects, it
yields **623 instead of 757** — 134 words, 17.7%. Either way the result reads
fluently, which is what makes it dangerous: a summary built on it is confidently
wrong.

**The mechanism, precisely.** In a rolling auto-caption cue, the timed line looks
like this:

```
provider<00:00:08.600><c> has</c><00:00:08.800><c> three</c><00:00:09.100><c> choices.</c>
```

The first word sits *before* the first timestamp, outside any `<c>` element. The
regex above only captures `<ts><c>...</c>` pairs, so it never sees it. On
`PjZ4kpLChpE.en-orig.vtt`, all 127 timed lines have an untagged leading word —
127 of 127, no exceptions — giving 129 leading-word occurrences. Those account
for 128 of the 134 lost words. The remaining 6 (`operate`, `snapshot`, `next`,
`integer`, `bottleneck`, `algorithm`) sit on untagged display lines rather than
timed lines; the overlap merge recovers them because the same text reappears in
the next cue's window, and the token-only parse never sees them at all.

So the loss is not one word per line by arithmetic coincidence — it is
systematic, and 96% of it is the leading-word bug.

Measured, same input files:

| Parser | `en-orig` words | `en` words | Content |
|---|---|---|---|
| token extraction | 623 | 660 | drops the first word of every timed line |
| strip-and-join | thousands | thousands | massive duplication |
| rolling-window merge | 757 | 789 | complete, no duplication |

Which track you are on matters when comparing against other tools: published
skills request `en` directly, so their 660 is not comparable to a 757-word
`en-orig` transcript.

### The correct parse: word-level longest overlap merge

Walk cues in order, keep a flat word accumulator, and for each new line append
only the part that does not already end the accumulator:

```python
for line in lines:
    new_words = clean(line).split()
    limit = min(len(acc), len(new_words))
    overlap = 0
    for k in range(limit, 0, -1):
        if acc[-k:] == new_words[:k]:
            overlap = k
            break
    if overlap == len(new_words):
        continue            # window fully known already
    acc.extend(new_words[overlap:])
```

This is lossless regardless of whether words were `<c>`-tagged, because it reads
the plain text of the last line rather than the tagged tokens. The overlap
search handles both the "repeated window" and "partially new" cases.

**Detection.** Rolling format is identified by the presence of
`<HH:MM:SS.mmm>` tokens in the first cues. `scripts/vtt2txt.py` auto-detects and
dispatches.

---

## Format B: plain cue blocks (manual captions)

Produced by `--write-subs` when human-authored captions exist.

```vtt
WEBVTT

00:00:00.100 --> 00:00:03.200
AI workloads can run tens of thousands of GPU instances per cluster.

00:00:03.200 --> 00:00:06.500
And at that scale, failures do occur.
```

No word timestamps, no overlap, one sentence per cue. A simple join is correct
here; the only care needed is dropping exact consecutive duplicates, which
occasionally appear.

`scripts/vtt2txt.py` uses the plain branch when no word tokens are found.

---

## Sanity check: words per minute

Speech runs roughly **130–160 words per minute** for ordinary delivery and
**180–200 wpm** for conference keynotes. Use it to catch a catastrophic parse —
an empty or near-empty transcript — without reading the text:

```bash
python3 scripts/vtt2txt.py --stats vtt/*.vtt
```

A 4-minute talk should land somewhere between 520 and 800 words. The KubeCon
keynote above is 757 (`en-orig`) / 789 (`en`), i.e. ~190 wpm — fast but normal
for a keynote.

**The per-minute heuristic cannot tell you which parser ran.** 660 words in
4 minutes is 165 wpm, inside every plausible band, and so is 757. Both are
believable speech rates, so the number alone never distinguishes a lossy parse
from a fast speaker. Use it only to catch transcripts that are wildly short (a
4-minute talk reporting under ~60 words is broken). To actually verify the parser,
check the format column — `--stats` prints `rolling` when the overlap-merge branch
ran and `plain` when it did not — and for auto-captions `plain` means the
detector missed.

If you want a second opinion on the same file, compare two parsers and diff the
word lists: the token-extraction regex above will be short by exactly the number
of timed lines.

---

## Other VTT quirks worth knowing

- **`align:start position:0%`** appears on timing lines of auto captions; strip
  the whole timing line rather than parsing it.
- **Header lines** (`WEBVTT`, `Kind:`, `Language:`, `NOTE`, `STYLE`,
  `X-TIMESTAMP-MAP`) must be skipped, or they leak into the transcript as text.
- **HTML entities** (`&amp;`, `&#39;`) are common; run `html.unescape`.
- **`--sub-format vtt`** is worth forcing: without it yt-dlp may pick `srv3` or
  `json3`, which need different parsers.
- **Two files can exist per language** (`abc.en.vtt` and `abc.en-orig.vtt`).
  Keep the language tag in the output name so they do not collide:
  `text/abc.en.txt` vs `text/abc.en-orig.txt`.

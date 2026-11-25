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
so a 790-word talk yields several thousand words of stutter.

**Wrong #2 — extract only `<ts><c>word</c>` tokens.**

```python
for m in re.finditer(r'<(\d+:\d+:\d+\.\d+)><c>(.*?)</c>', content):
    words.append(m.group(2))
```

This looks clean and is the approach shipped in several published skills. It is
**lossy**: property 3 means any word YouTube did not tag disappears. On
`PjZ4kpLChpE` (a 4-minute KubeCon keynote) this yields **660 words instead of
790** — 130 words, 16.5% of the content, silently missing. The result reads
fluently, which is what makes it dangerous: a summary built on it is confidently
wrong.

Measured, same input file:

| Parser | Words | Content |
|---|---|---|
| token extraction | 660 | drops every untagged word |
| strip-and-join | thousands | massive duplication |
| rolling-window merge | 790 | complete, no duplication |

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

Speech runs roughly **130–160 words per minute**. Use it to catch a bad parse
without reading the text:

```bash
python3 scripts/vtt2txt.py --stats vtt/*.vtt
```

A 4-minute talk should land near 520–640 words at 130–160 wpm; the KubeCon
keynote above is 790 because keynote delivery is faster (~200 wpm). A 4-minute
talk reporting 660 when the same video should be 790 is inside the plausible
band — which is exactly why the per-minute heuristic alone is insufficient and
the parser choice matters. When in doubt, compare two parsers on one file and
diff the word lists.

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

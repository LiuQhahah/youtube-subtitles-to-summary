# Example — input and output for one video

A worked example so the expected shape is unambiguous. Video:
`https://www.youtube.com/watch?v=PjZ4kpLChpE` (4 min; 757 words on the
`en-orig` track the script selects, 789 on `en` — see the note in Stage 2).

---

## Stage 1 — download

```bash
python3 scripts/download_subs.py \
  --url 'https://www.youtube.com/watch?v=PjZ4kpLChpE' \
  --out .
```

Output:

```
0 already downloaded, 1 to fetch

--- round 1/3: 1 videos ---
[1/1] PjZ4kpLChpE: ok

downloaded 1/1 subtitle files
```

Note it picked `en-orig`, not `en` — `en` returns 429 on this video even though
the key exists.

```
vtt/PjZ4kpLChpE.en-orig.vtt
```

---

## Stage 2 — convert

```bash
python3 scripts/vtt2txt.py vtt/PjZ4kpLChpE.en-orig.vtt
```

```
text/PjZ4kpLChpE.en-orig.txt: 757 words (rolling)
```

`(rolling)` confirms the auto-detect chose the overlap-merge path.

**Which track matters for every number below.** This video exposes both `en` and
`en-orig`, and they are different transcripts:

| Track | Merged words | Token-extraction words |
|---|---|---|
| `en-orig` (what the script picks) | 757 | 623 |
| `en` (429s on this video) | 789 | 660 |

The walkthrough stays on `en-orig` throughout, so its reference count is **757**.
The widely quoted 660/789 pair belongs to the `en` track — keep it in mind only
when comparing against other published skills, which request `en` directly.

---

## Stage 3 — transcript excerpt

First 70 words of `text/PjZ4kpLChpE.en-orig.txt`, verbatim:

> AI workloads can spin up tens of thousands of GPU instances per cluster, and
> things break at that scale. When they do, a managed Kubernetes provider has
> three choices. One, they can fork it and keep the fix. Two, they can let their
> customers try and work around it. Or three, they can fix it upstream. At AWS,
> we operate some of the world's largest clusters in the world, and

Clean, no duplication, no gaps. A token-extraction parser run on the same VTT
loses 134 of these 757 words, and the loss has a precise shape: **every timed
line in an auto-caption cue begins with one untagged word**, and that word is
dropped. Of the 127 timed lines in this file, all 127 have an untagged leading
word — 128 of the 134 losses, with 6 more on untagged display lines. Two of them
land inside the excerpt above — `thousands` and `provider`:

```
provider<00:00:08.600><c> has</c><00:00:08.800><c> three</c><00:00:09.100><c> choices.</c>
thousands<00:00:02.300><c> of</c><00:00:02.400><c> GPU</c><00:00:02.800><c> instances</c>...
```

Note where the tag starts: after the first word, not before it. The regex in
`references/vtt-formats.md` only captures `<ts><c>...</c>` pairs, so those
leading words are never captured. The surviving text still reads fluently —
which is the whole danger.

---

## Stage 4 — summary file

`summaries/03_PjZ4kpLChpE.md`:

```markdown
# KubeCon NA 演讲 #3：超大规模集群的瓶颈如何修回上游——从 watch cache 到 containerd

> 英文标题：Engineering Kubernetes for the Next Era of Workloads by Mike Stefaniak, AWS（4min，约 757 词）
> 演讲者：Mike Stefaniak, AWS
> 原始字幕：[text/PjZ4kpLChpE.en-orig.txt](text/PjZ4kpLChpE.en-orig.txt)

---

**主题与背景**：AI 集群可达数万 GPU 实例，规模一大必出故障；托管厂商只有三条路——fork 私藏、让客户自救、修上游，AWS 选上游。**核心内容/技术亮点**：Kubernetes 按 5,000 节点设计，到 100,000 节点性能开始下滑。API server 的 watch cache 在构建快照全程持锁，百万 Pod 测试写等待超 0.5 秒，改惰性快照后降到 2 毫秒；HPA 加 selector store，读不再排在写后，最大集群 HPA worker 提升 4 倍；scheduler 每轮从零重算 volume 引用，改成只应用增量的 cache，调度稳定 300+ Pod/s。**演讲者观点/结论**：用户总会把集群推到极限，新瓶颈不断出现——"到时候上游见"。

---

*摘要由字幕全文分析生成，完整转录见 `text/PjZ4kpLChpE.en-orig.txt`。*
```

Properties worth copying:

- header carries duration and word count, so a reader can judge whether to read
- one paragraph, three bold labels (this one is condensed to 160 Chinese
  characters for readability; the shipped summary is 259, inside the 200–350 band
  SKILL.md asks for)
- technical terms untranslated (`watch cache`, `selector store`, `scheduler`)
- every number traceable to the transcript (`5,000`, `100,000`, `0.5`, `2ms`, `4x`, `300+`)
- link points at a file that exists

---

## Stage 5 — index row

```markdown
| 3 | 4min | Mike Stefaniak, AWS | 超大规模集群的瓶颈如何修回上游 | [Engineering Kubernetes for the Next Era of Workloads](summaries/03_PjZ4kpLChpE.md) | 757 |
```

---

## What a bad summary looks like

For contrast, the failure modes to reject:

```markdown
# 演讲 3

Mike Stefaniak 讲了 Kubernetes 的很多性能优化问题，包括 API server、
HPA 和 scheduler 等方面，还有一些关于 etcd、GPU 和镜像拉取的内容。
演讲很有启发性，值得一看。
```

Problems: no numbers, no specifics, no way to verify, no metadata, generic
praise. A reader learns nothing they could not have guessed from the title. This
is what you get when the transcript was truncated or the summarizer did not read
it — which is why the word-count verification step exists.

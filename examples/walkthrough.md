# Example — input and output for one video

A worked example so the expected shape is unambiguous. Video:
`https://www.youtube.com/watch?v=PjZ4kpLChpE` (4 min, 790 words).

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

`(rolling)` confirms the auto-detect chose the overlap-merge path. A
token-extraction parser on the same file yields 660 words.

---

## Stage 3 — transcript excerpt

First ~70 words of `text/PjZ4kpLChpE.en-orig.txt`:

> AI workloads can spin up tens of thousands of GPU instances per cluster, and
> things break at that scale. When they do, a managed Kubernetes provider has
> three choices. One, they can fork it and keep the fix. Two, they can let their
> customers try and work around it. Or three, they can fix it upstream. At AWS,
> we run some of the largest clusters in the world, and we choose to fix the
> problems we find at that scale upstream.

Clean, no duplication, no gaps. Compare with what a token-extraction parser
produces from the same VTT — the words `can`, `things`, `they`, and `and` are
absent there because YouTube did not wrap them in `<c>` tags.

---

## Stage 4 — summary file

`summaries/03_PjZ4kpLChpE.md`:

```markdown
# KubeCon NA 演讲 #3：超大规模集群的瓶颈如何修回上游——从 watch cache 到 containerd

> 英文标题：Engineering Kubernetes for the Next Era of Workloads by Mike Stefaniak, AWS（4min，约 790 词）
> 演讲者：Mike Stefaniak, AWS
> 原始字幕：[text/PjZ4kpLChpE.en.txt](text/PjZ4kpLChpE.en.txt)

---

**主题与背景**：AI 集群可达数万 GPU 实例，规模一大必出故障；托管厂商只有三条路——fork 私藏、让客户自救、修上游，AWS 选上游。**核心内容/技术亮点**：Kubernetes 按 5,000 节点设计，到 100,000 节点性能开始下滑。API server 的 watch cache 在构建快照全程持锁，百万 Pod 测试写等待超 0.5 秒，改惰性快照后降到 2 毫秒；HPA 加 selector store，读不再排在写后，最大集群 HPA worker 提升 4 倍；scheduler 每轮从零重算 volume 引用，改成只应用增量的 cache，调度稳定 300+ Pod/s。**演讲者观点/结论**：用户总会把集群推到极限，新瓶颈不断出现——"到时候上游见"。

---

*摘要由字幕全文分析生成，完整转录见 `text/PjZ4kpLChpE.en.txt`。*
```

Properties worth copying:

- header carries duration and word count, so a reader can judge whether to read
- one paragraph, three bold labels, ~200 Chinese characters
- technical terms untranslated (`watch cache`, `selector store`, `scheduler`)
- every number traceable to the transcript (`5,000`, `100,000`, `0.5`, `2ms`, `4x`, `300+`)
- link points at a file that exists

---

## Stage 5 — index row

```markdown
| 3 | 4min | Mike Stefaniak, AWS | 超大规模集群的瓶颈如何修回上游 | [Engineering Kubernetes for the Next Era of Workloads](summaries/03_PjZ4kpLChpE.md) | 790 |
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

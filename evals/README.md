# 检索评测

本目录的 32 篇语料完全合成；40 条问题覆盖精确词、英文技术词、改写、口语问法。
`public.jsonl` 与 `corpus/` 在首次提交后冻结，不按模型结果改题、改答案或改语料。
每题一个期望文档。Recall@k 表示前 k 条命中期望路径的题目比例；MRR（截断于 10）为首个正确排名倒数的均值，未命中记 0。

从仓库根目录运行基线：

```sh
uv run python evals/run.py --mode bm25 --output evals/public-bm25.json
```

每题预热一次，再重复计时三次；p50/p95 为所有计时样本的线性插值分位数，包含读文件、BM25、片段提取和可选重排，不含预热与模型加载。`load_ms` 单独记录初始化耗时。
模型初次下载需要网络，下载后推理在本地 CPU 执行。

私有集使用同一个脚本，传入 `--vault <vault> --questions <仓库外的问题.jsonl>`，加 `--summary-only` 避免输出问题明细。脚本只读取 vault，不做 Git 操作或写入。
问题格式：`{"id":"q1","scope":"personal","query":"问题","expected_paths":["personal/note.md"],"type":"paraphrase"}`。

公开集重排（默认模型，CPU 4 线程，batch size 8）：

```sh
uv run --extra rerank python evals/run.py --mode rerank --output evals/public-rerank.json
```

一次复现两组公开结果：

```sh
uv run --extra rerank python evals/compare.py
```

`--model` 可切换 fastembed 支持的交叉编码器；评测加载或推理失败时直接终止，不把回退结果记成重排成绩。题目和语料未改动，汇总中的 SHA-256 可校验题目一致。
MRR 在 JSON 中命名为 `mrr@10`，因为只检索前 10 条，不把第 11–30 名计为正确。

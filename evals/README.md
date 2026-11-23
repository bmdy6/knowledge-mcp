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

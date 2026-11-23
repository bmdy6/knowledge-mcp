---
title: RAG 召回与重排
scope: personal
type: article
status: review
---

# RAG 召回与重排

RAG 先召回候选片段，再排序并交给生成模型。重排只能改善候选内部的顺序，候选里没有答案时不能凭空补出证据。分别观察召回覆盖率、排序质量与最终引用准确性。

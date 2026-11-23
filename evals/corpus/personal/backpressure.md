---
title: 背压与有界队列
scope: personal
type: article
status: review
---

# 背压与有界队列

背压让生产速度服从下游消费能力。有界队列满时选择等待、拒绝或降级，而不是无限累积任务占满内存。只增加并发可能让慢依赖更加拥堵。

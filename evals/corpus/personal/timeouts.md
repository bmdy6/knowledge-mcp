---
title: 超时预算与重试退避
scope: personal
type: article
status: review
---

# 超时预算与重试退避

调用链需要总超时预算，各下游调用只使用剩余时间。重试仅适用于可恢复且幂等的失败，使用指数退避和随机抖动，避免多层重试放大请求。

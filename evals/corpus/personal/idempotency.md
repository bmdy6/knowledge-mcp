---
title: 消费幂等与重复消息
scope: personal
type: article
status: review
---

# 消费幂等与重复消息

消息可能重复投递。消费幂等使用业务唯一键和数据库唯一约束，在同一个事务中登记处理结果，防止重复扣款。重试不代表必须再次产生副作用。

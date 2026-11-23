---
title: 事务消息与 Outbox
scope: personal
type: article
status: review
---

# 事务消息与 Outbox

数据库事务提交和发送消息之间可能发生故障。Outbox 把业务记录与待发事件写在同一事务中，由后台任务发送并重试。消费者仍需幂等，不能承诺跨系统恰好一次。

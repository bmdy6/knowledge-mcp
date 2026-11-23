---
title: 熔断与故障隔离
scope: personal
type: article
status: review
---

# 熔断与故障隔离

熔断器在下游连续失败后暂时停止调用，返回降级结果，半开状态用少量请求探测恢复。隔离舱把不同依赖的资源分开，避免一个慢服务耗尽所有工作线程。

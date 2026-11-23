---
title: Agent 循环与停止条件
scope: personal
type: article
status: review
---

# Agent 循环与停止条件

Agent 根据目标观察状态，选择工具行动，再读取执行结果继续判断。停止条件应绑定可验证的任务结果和预算，不能仅根据模型声称完成。不可恢复失败应退出并留下诊断。

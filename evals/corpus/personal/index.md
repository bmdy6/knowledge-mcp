---
title: 索引与查询计划
scope: personal
type: article
status: review
---

# 索引与查询计划

数据库索引让查询避免遍历所有行。复合索引的列顺序应结合过滤条件与排序方式。用 EXPLAIN 查看扫描行数与执行计划；索引越多，写入维护成本和磁盘占用越高。

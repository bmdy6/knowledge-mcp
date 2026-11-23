---
title: 旁路缓存与一致性
scope: personal
type: article
status: review
---

# 旁路缓存与一致性

Cache Aside 读取先查缓存，未命中再访问数据库并回填。写入先提交数据库再删除缓存。并发读写仍可能短暂读到旧值，结合过期时间和业务一致性需求选择策略。

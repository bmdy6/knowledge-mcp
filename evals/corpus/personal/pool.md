---
title: 数据库连接池
scope: personal
type: article
status: review
---

# 数据库连接池

连接池复用数据库连接，减少反复建立连接的成本。池太小会排队等待，池太大可能耗尽数据库资源。观察等待时间、活跃连接数和慢查询，再确定上限。

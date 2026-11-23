---
title: 多租户授权与数据隔离
scope: personal
type: article
status: review
---

# 多租户授权与数据隔离

多租户系统必须从已认证身份取得租户归属，查询条件和写入检查都要限制 tenant_id。前端隐藏按钮不能代替服务端授权，客户端传来的租户编号不可信。

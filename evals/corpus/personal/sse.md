---
title: SSE 单向事件流
scope: personal
type: article
status: review
---

# SSE 单向事件流

Server-Sent Events 使用长连接把服务端事件推送给浏览器，适合生成进度和通知。客户端通过事件编号恢复中断后的流。双向频繁交互可选择 WebSocket，不应把网络重连当成任务重启。

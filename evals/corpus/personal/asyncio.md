---
title: Python asyncio 与阻塞
scope: personal
type: article
status: review
---

# Python asyncio 与阻塞

asyncio 事件循环在等待 I/O 时切换协程。async 函数中直接调用阻塞库会卡住事件循环，应使用异步驱动或在线程中执行阻塞操作。CPU 密集计算需要独立进程。

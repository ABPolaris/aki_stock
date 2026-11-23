---
type: fix
scope: plugin
packages:
  python@aki-stock: patch
---

将单只与批量行情查询改为后台任务，并给整次查询设置 20 秒总超时预算，避免慢查询阻塞 NeoBot 入站消息处理。

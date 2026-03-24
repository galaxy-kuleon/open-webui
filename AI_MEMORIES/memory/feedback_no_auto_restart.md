---
name: no-auto-restart-server
description: Never restart Open WebUI server unless user explicitly says "restart"
type: feedback
---

Do not restart the Open WebUI server unless the user explicitly tells you to restart.

**Why:** User wants full control over when the server restarts — auto-restarts can interrupt active work or testing.

**How to apply:** After code changes, only mention that a restart is needed. Do not execute `kill` + `nohup open-webui serve` unless the user says "restart server" or equivalent.

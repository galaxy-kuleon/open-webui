---
name: Frontend Build Output Directory
description: Frontend build must go to backend/open_webui/frontend/ not static/. User corrected this mistake 2026-03-22.
type: feedback
---

Frontend build output MUST be copied to `backend/open_webui/frontend/`, NOT `backend/open_webui/static/`.

**Why:** The user corrected this mistake on 2026-03-22. The server serves from `frontend/`, not `static/`. Copying to `static/` means the server never sees the updated frontend. The `.gitignore` confirms `frontend/` is the generated directory.

**How to apply:** After ANY frontend Svelte changes, always run:
```bash
bun run format && bun run lint && bun run check
bun run build && cp -rf build/* backend/open_webui/frontend/
```
Never skip the format/lint/check step. The user expects all three to pass before the build.

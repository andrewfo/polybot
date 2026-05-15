Audit the frontend against the backend and update React components so every backend feature is visually represented in the dashboard.

## Steps

### 1. Inventory backend capabilities
- Read `web/server.py` and list every API endpoint (`/api/*`), its response shape, and what data it provides
- Read `monitoring/health.py`, `monitoring/pnl.py`, `monitoring/notifications.py`, `monitoring/learning.py` and note any data structures exposed via the API
- Check WebSocket message types broadcast by `BotEngine._broadcast()`

### 2. Inventory frontend consumption
- Read every component in `frontend/src/components/` and `frontend/src/api.ts`
- For each API endpoint, note whether the frontend calls it and renders the data
- For each WebSocket message type, note whether the frontend handles it

### 3. Identify gaps
Build a table:

| Backend feature | Endpoint/WS | Frontend status | Gap |
|-----------------|-------------|-----------------|-----|

Mark each as: "rendered", "fetched but not rendered", or "not fetched".

### 4. Fix every gap
For each "not fetched" or "fetched but not rendered" item, update the frontend:
- Add the API call to `api.ts` if missing
- Add or update the appropriate component to render the data
- Follow the existing theme: navy (#0a1628, #0d1f3c), grey (#8899aa, #667788), white (#e0e8f0), accent (#4488cc)
- Match existing component patterns (polling intervals, card layouts, table styles)
- Keep it clean — no new dependencies unless absolutely necessary

Common things to check:
- **Health checks panel** on Dashboard: should show all 7 health check results with ok/warning/critical badges
- **P&L display**: daily, weekly, total returns; bankroll chart over time
- **On-chain flow signal**: visible in Analysis detail alongside other signal sources
- **Auto-stop status**: consecutive failure counts visible somewhere (activity feed or status bar)
- **Learning engine**: overrides, calibration data, skip analysis, recommendation history
- **Cost tracking**: LLM cost breakdown by model tier (cheap/frontier/sonar)

### 5. Verify
- Run `cd frontend && npm run build` to confirm no build errors
- Start the dev server and visually check each tab renders correctly
- Confirm polling intervals match spec: Dashboard 30s, Analysis 15s, Logs 5s

## Output
Report what was added/updated per component, and confirm the build succeeds.

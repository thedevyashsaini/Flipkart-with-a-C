# Smart Supply Chains - Resilient Logistics & Dynamic Supply Chain Optimization

## The Problem

Modern global supply chains manage millions of concurrent shipments across highly complex transportation networks. Critical transit disruptions (weather events, operational bottlenecks) are identified only AFTER delivery timelines are already compromised.

**Our Solution:** A scalable system that continuously analyzes transit data to preemptively detect disruptions and recommend optimized route adjustments before localized bottlenecks cascade into delays.

Demo Video: https://youtu.be/l8EAYMhw5us

---

## Core Philosophy

> We optimize for **system stability under uncertainty**, not per-shipment cost.

Instead of shortest/cheapest path routing, the system:
- Monitors **node pressure** (queue load + inbound - outbound)
- Tracks **edge stress** 
- Detects **regional instability**
- Makes decisions to maintain **global system stability**

### Example

A shipment from Delhi → Hyderabad:
- **WITHOUT C (baseline):** Picks Delhi→Hyderabad (cost=4), ignores congestion → can overload Hyderabad
- **WITH C:** Routes Delhi→Mumbai→Hyderabad, holds if congested → system stays stable

Result: slightly higher local cost, but predictable delivery and NO cascading failures.

---

## WITH_C Agent Algorithm

The WITH_C agent uses a pressure-aware, multi-signal decision algorithm:

### Step-by-Step Decision Flow

1. **Check if at destination:** If source == destination, return "arrive"

2. **Compute baseline path:** Use Dijkstra's algorithm reverse-search from destination to find cheapest path. Example: shipment at Delhi Warehouse (n011) going to Chennai Hub (n005) → baseline is n011 → n001 → n006 → n005

3. **Generate candidate hops:** From current node, find all possible next hops. Filter by extra cost limit based on priority:
   - CRITICAL: max 70% over baseline
   - HIGH: max 100% over baseline
   - MEDIUM: max 120% over baseline
   - LOW: max 140% over baseline

4. **For each candidate, compute pressure metrics:**
   ```
   current_pressure = (queue_load + 0.22 * holding_load) / capacity
   projected_pressure = (current + inbound - 0.35 * outbound) / capacity
   flow_trend = (inbound - outbound) / capacity
   ```

5. **Forecast risk 3-7 ticks ahead:**
   ```
   forecast_risk = projected_pressure + (flow_trend * horizon * 0.08)
   ```

6. **Apply risk penalty scoring:**
   ```
   score = (1.7 * forecast_risk) + 
          (0.9 * projected_pressure) + 
          (0.5 * current_pressure) + 
          overload_penalty + 
          warehouse_bonus
   ```
   Where:
   - `overload_penalty = 5 × max(0, forecast_risk - 1.0)`
   - `warehouse_bonus = -0.55` (warehouses get relief since they're designed to absorb load)

7. **Decision cascade:**
   - If baseline risk < 1.1: **TAKE BASELINE** (safe)
   - Else if ANY alternative risk < 1.1: **REROUTE** to cheapest safe one
   - Else if any candidate reduces risk by ≥0.08: **MITIGATE** with better option
   - Else: **MOVE anyway** (least risk forward)
   - HOLD only when NO path exists

### Real Example

7 shipments need to go from Hyderabad Airport (n026) to Delhi Hub (n001):
- Baseline route floods Hyderabad Hub → pressure climbs to 1.5+
- WITH_C notices high projected pressure at Hyderabad Hub
- Routes 3-4 shipments via Chennai (n005) → Bengaluru (n003) → Mumbai (n002) → Delhi
- Keeps system-wide pressure below 1.1, prevents cascading failures

---

## Architecture

### Tech Stack
- **Frontend:** Next.js + React Flow + shadcn/ui
- **Backend:** FastAPI (Python)
- **Live Storage:** Firestore (GCP)
- **LLM Explanations:** Gemini (Vertex AI)
- **Deployment:** Railway / Cloud Run

### Two Dashboards

1. **Supply Chain Admin** - Chain config, map, agent suggestions, event log, explain decisions
2. **Node Admin** - Per-node timeline, submit shipment events (created, arrived, dispatched, held, delivered)

### Live Flow
```
Node Admin (submit event) → REST API → Firestore → Compute Pressure → Agent Decision → Store Suggestion
```

---

## Key Features

- **Pressure-Aware Routing:** Considers system state, not just cost
- **Automatic Hold/Reroute:** Prevents overload before it happens
- **Flush Node Action:** Right-click to drain and disable nodes (emergencies)
- **Gemini Explanations:** "Why" button explains each decision
- **Scalable:** Stateless backend, managed Firestore

---

## Deployment

### Google Cloud (Recommended)

The system is designed for Google Cloud which handles auto-scaling natively:

**Backend:** Deploy to Cloud Run (auto-scales 0-1000+ instances)
```bash
gcloud run deploy flipcart-backend --source ./backend --platform managed --region asia-east1 --allow-unauthenticated
```

**Frontend:** Deploy to Cloud Run or Firebase Hosting
```bash
# Option 1: Cloud Run
gcloud run deploy flipcart-frontend --source ./frontend --platform managed --region asia-east1 --allow-unauthenticated

# Option 2: Firebase Hosting
cd frontend
npm run build
firebase init hosting
firebase deploy
```

**Environment Variables:**
- `FIREBASE_PROJECT_ID`
- `GEMINI_MODEL` = gemini-2.0-flash-lite
- `GEMINI_API_KEY`
- `FIREBASE_SERVICE_ACCOUNT_JSON`
- `TOKEN_SIGNING_SECRET`
- `SUPPLY_ADMIN_API_KEY`
- `NEXT_PUBLIC_API_BASE_URL` (frontend only)

### Docker (Alternative)

```bash
# Backend
docker build -t flipcart-backend ./backend
docker run -p 8080:8080 flipcart-backend

# Frontend
docker build -t flipcart-frontend ./frontend
docker run -p 3000:3000 flipcart-frontend
```

---

## Files

- `/backend` - FastAPI backend
- `/frontend` - Next.js dashboards
- `/AGENTS.md` - Original spec
- `/backend/app/agents/with_c.py` - Agent algorithm
- `/backend/app/data/world.json` - Default supply chain network

---

## KPI Comparison

| Metric | WITHOUT C | WITH C |
|--------|-----------|--------|
| Cost/shipment | Lower | +5-10% |
| Delivery | Unpredictable | Controlled |
| System stability | Poor | Strong |
| Cascading failures | Frequent | Avoided |
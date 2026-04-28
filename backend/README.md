# Backend

FastAPI server for Smart Supply Chains.

## Run Locally

```bash
cd backend
pip install -r requirements.txt  # or: uv sync
uvicorn app.main:app --reload --port 8080
```

## Run with Docker

```bash
docker build -t flipcart-backend ./backend
docker run -p 8080:8080 flipcart-backend
```

## Deploy to Google Cloud

```bash
# Deploy to Cloud Run (auto-scales 0-1000+ instances)
gcloud run deploy flipcart-backend --source . --platform managed --region asia-east1 --allow-unauthenticated
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FIREBASE_PROJECT_ID` | Yes | GCP project ID |
| `GEMINI_MODEL` | No | Default: gemini-2.0-flash-lite |
| `GEMINI_API_KEY` | Yes | Gemini API key |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Yes | GCP service account JSON |
| `TOKEN_SIGNING_SECRET` | Yes | Random string for JWT |
| `SUPPLY_ADMIN_API_KEY` | Yes | Admin access key |

## Endpoints

- `GET /world` - Supply chain network
- `GET /sim/state` - Simulation state
- `GET /sim/stream` - SSE simulation stream
- `POST /live/chains` - Create live chain
- `POST /live/events` - Submit shipment event
- `GET /live/chains/{id}/suggestions` - Agent suggestions
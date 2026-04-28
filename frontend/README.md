# Frontend

Next.js dashboards for Smart Supply Chains.

## Run Locally

```bash
cd frontend
npm install
npm run dev
```

Opens at http://localhost:3000

## Pages

- `/` - Study Zone (simulator comparison)
- `/supply-admin` - Supply Chain Admin (chain config, map, suggestions)
- `/node-admin` - Node Admin (submit events)

## Build

```bash
npm run build
```

## Deploy to Google Cloud

```bash
# Deploy to Cloud Run
gcloud run deploy flipcart-frontend --source . --platform managed --region asia-east1 --allow-unauthenticated
```

Or use Firebase Hosting:

```bash
cd frontend
npm run build
firebase init hosting
firebase deploy
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NEXT_PUBLIC_API_BASE_URL` | Yes | Backend URL |

## Docker

```bash
docker build -t flipcart-frontend ./frontend
docker run -p 3000:3000 flipcart-frontend
```
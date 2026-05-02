# Catalogue Generator

Web app that turns a Noise-style product PDF into a clean grid catalogue.

- **Frontend** (`frontend/`) — static HTML/CSS/JS, hosted on **Netlify**.
- **Backend** (`backend/`) — FastAPI wrapper around `catalogue_generator.py`, hosted on **Render**.
- **Original CLI** (`catalogue_generator.py`) — unchanged, still usable from the terminal.

## Local development

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend

Open `frontend/index.html` in a browser, or serve it:

```bash
cd frontend
python -m http.server 5500
```

`frontend/config.js` already points to `http://localhost:8000`.

## Deploy

### 1. Backend → Render

1. Push this repo to GitHub.
2. On Render: **New → Blueprint** and select the repo. It picks up `backend/render.yaml`.
3. Once deployed, copy the URL (e.g. `https://catalogue-generator-api.onrender.com`).
4. In the service's **Environment** tab set `ALLOWED_ORIGINS` to your Netlify URL (comma-separated if multiple).

> Free Render web services sleep after 15 min of inactivity. The first request after a sleep takes ~30s to wake.

### 2. Frontend → Netlify

1. Edit `frontend/config.js` and set `window.BACKEND_URL` to your Render URL.
2. On Netlify: **Add new site → Import from Git** and select the repo. `netlify.toml` at the root tells Netlify to publish `frontend/`.

That's it. No build step.

## API

`POST /generate` — multipart form:

| Field | Type | Default |
|---|---|---|
| `file` | PDF | required |
| `cols` | int | 4 |
| `rows_per_page` | int | 6 |
| `scale` | int | 5 |
| `skip_pages` | int | 1 |
| `divider` | int | 3 |

Returns the generated PDF (`application/pdf`) with `X-Product-Count` header.

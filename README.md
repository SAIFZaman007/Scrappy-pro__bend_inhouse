<div align="center">
  <h1>🕷️ Scrappy Pro API</h1>
  <p><strong>Bulk Product Data Collection API & Worker</strong></p>

  <p>
    <img src="https://img.shields.io/badge/Python-3.11+-blue.svg?logo=python&logoColor=white" alt="Python" />
    <img src="https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white" alt="FastAPI" />
    <img src="https://img.shields.io/badge/Playwright-1.48+-2EAD33.svg?logo=playwright&logoColor=white" alt="Playwright" />
    <img src="https://img.shields.io/badge/PostgreSQL-16.0+-336791.svg?logo=postgresql&logoColor=white" alt="Postgres" />
    <img src="https://img.shields.io/badge/Redis-5.2+-DC382D.svg?logo=redis&logoColor=white" alt="Redis" />
  </p>
</div>

---

## 📖 Overview

**Scrappy Pro** is an enterprise-grade bulk product data collection system. The backend is built with high performance and reliability in mind, utilizing asynchronous Python with **FastAPI**, **Playwright** (for handling JS-challenged scraping like Cloudflare), and **ARQ** (Redis queue) for background job processing. 

### Core Technologies
- **API Framework**: FastAPI, Pydantic v2
- **Database**: PostgreSQL (via asyncpg & SQLAlchemy 2.0)
- **Queue/Worker**: Redis + ARQ
- **Scraping Engine**: Playwright, Selectolax, httpx (with `brotli` & `zstandard` support)
- **Migrations**: Alembic

## 🏗️ Architecture

The backend consists of two main components that are meant to be deployed separately but share the same codebase:
1. **API Server**: Handles incoming HTTP requests, manages users, and enqueues scraping jobs.
2. **Worker**: A background process running ARQ that executes the heavy browser automation tasks via Playwright and stores the results.

---

## 🚀 Coolify Deployment Guide (In-Depth)

Scrappy Pro is optimized for deployment via [Coolify](https://coolify.io). Because the frontend and backend operate as separate apps in Coolify (and do not share a Docker network by default), it requires specific routing and configuration.

### Prerequisites in Coolify
1. A **PostgreSQL** database provisioned in Coolify (v16.0+).
2. A **Redis** instance provisioned in Coolify (v5.2+).
3. Domains configured for your frontend (e.g., `scrappy.example.com`) and backend (e.g., `api.scrappy.example.com`).

---

### Step 1: Deploying the Backend API
The Backend API serves HTTP requests and manages the database.

1. **Create Application:** Go to your Coolify dashboard and add a new resource -> **Application** -> **Based on your Git Repository**.
2. **Build Pack:** Choose **Docker**. 
3. **Base Directory:** Set the Base Directory to `/backend`.
4. **Dockerfile:** Coolify will auto-detect `/backend/Dockerfile`. This is correct.
5. **Domains:** Set the domain to your backend API domain (e.g., `https://api.scrappy.example.com`).
6. **Ports:** Set the container port to `8000`.
7. **Environment Variables:** 
   Add all necessary variables from `backend/.env.example`.
   - `REDIS_URL`: The internal connection string to your Coolify Redis instance (e.g., `redis://default:password@coolify-redis:6379/0`).
   - `DATABASE_URL`: The internal connection string to your Coolify Postgres DB (e.g., `postgresql://user:password@coolify-postgres:5432/db`).
   - `CORS_ORIGINS`: Add your frontend domain (e.g., `https://scrappy.example.com`).
   - `SECRET_KEY`: Generate a secure random string (e.g., `openssl rand -hex 32`).
   - `ENV`: Set to `production`.
8. **Deploy:** Hit deploy. The API container comes with a built-in health check on `/api/v1/health` to verify it booted successfully.

---

### Step 2: Deploying the Background Worker
The background worker executes heavy browser automation tasks via Playwright and stores the results. It must run alongside the API.

1. **Create Application:** Go to your Coolify dashboard and add *another* new resource -> **Application** -> **Based on your Git Repository**. Choose the same repository.
2. **Build Pack:** Choose **Docker**.
3. **Base Directory:** Set the Base Directory to `/backend`.
4. **Dockerfile (CRITICAL):** Change the Dockerfile path to `/backend/Dockerfile.worker`. The worker needs a different configuration because it doesn't open port 8000, which would cause Coolify's default healthchecks to infinitely restart the container.
5. **Domains:** **Do not assign a public domain** to the worker. It does not accept web traffic.
6. **Environment Variables:** Supply the **exact same Environment Variables** (`REDIS_URL`, `DATABASE_URL`, `SECRET_KEY`, etc.) as the API server.
7. **Deploy:** Hit deploy. The worker will automatically connect to Redis and begin waiting for jobs.

---

### Step 3: Deploying the Frontend
The frontend is a React application built with Vite, served via an Nginx reverse proxy.

1. **Create Application:** Add a new resource -> **Application** -> **Based on your Git Repository**.
2. **Build Pack:** Choose **Docker**.
3. **Base Directory:** Set the Base Directory to `/frontend`.
4. **Dockerfile:** Coolify will auto-detect `/frontend/Dockerfile`.
5. **Domains:** Set the domain to your frontend domain (e.g., `https://scrappy.example.com`).
6. **Ports:** Set the container port to `80`. Nginx serves the compiled static files here.
7. **Environment Variables:** You do **not** need to set `VITE_API_BASE` for production. Nginx handles the reverse proxying automatically.
8. **Critical Nginx Configuration**:
   The frontend uses Nginx to serve static files and proxy `/api` requests to the backend. Because Coolify changes container IPs on redeploy, the `frontend/nginx.conf` is configured to proxy via the public domain.
   - You must edit `frontend/nginx.conf` before deploying and ensure `set $backend_upstream` matches your actual Backend API domain (e.g., `set $backend_upstream https://api.scrappy.example.com;`).
   - The Nginx config uses `resolver 127.0.0.11` to handle dynamic IP changes from the edge proxy.
9. **Deploy:** Hit deploy.

---

### 💡 Troubleshooting Deployments
- **Zero Products Scraped / 502 Bad Gateway**: Ensure `brotli` and `zstandard` are installed in the Python environment, as HTTPX requires these to decode compressed payloads from Cloudflare. (These are already documented in `pyproject.toml`).
- **Frontend API Calls Failing**: Verify the frontend's `nginx.conf` has the correct `set $backend_upstream` domain and that `proxy_set_header Host` perfectly matches the backend's expected domain name. Coolify's edge proxy routes by Host header.

---

## 🛠️ Local Development

If you prefer to run things locally:

1. **Install uv** (or standard pip) and sync dependencies:
   ```bash
   uv sync
   ```
2. **Install Playwright Browsers**:
   ```bash
   playwright install chromium
   ```
3. **Database Setup**:
   ```bash
   alembic upgrade head
   ```
4. **Run API**:
   ```bash
   fastapi dev app/main.py
   ```
5. **Run Worker**:
   ```bash
   arq app.worker.WorkerSettings
   ```

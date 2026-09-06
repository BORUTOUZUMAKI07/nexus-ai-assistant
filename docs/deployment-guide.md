# Nexus AI Assistant — Deployment Guide

## Production Architecture Stack
- **Backend API**: Render / Railway (Docker or Python 3.11 web service)
- **Frontend App**: Vercel (Next.js 14 standalone output)
- **Primary Database**: Supabase PostgreSQL (or Render PostgreSQL)
- **Vector Storage**: Qdrant Cloud (Free 1GB cluster)
- **Cache & Rate Limiting**: Upstash Redis (Serverless)

---

## 1. Backend Deployment on Render

1. Connect your GitHub repository to [Render](https://render.com).
2. Create a new **Web Service**:
   - **Root Directory**: `backend`
   - **Environment**: `Docker` or `Python` (Build: `pip install -e .`, Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`)
   - **Environment Variables**: Add variables from `backend/.env.example`.
3. Set health check path to `/health`.

---

## 2. Frontend Deployment on Vercel

1. Connect your GitHub repository to [Vercel](https://vercel.com).
2. Set **Root Directory** to `frontend`.
3. Set Environment Variable:
   - `NEXT_PUBLIC_API_URL`: URL of your deployed backend service (e.g. `https://nexus-backend.onrender.com`).
4. Deploy!

---

## 3. Database & Migrations
To apply database schema migrations on your production database:
```bash
cd backend
alembic upgrade head
```
All 22 SQLModel tables will be initialized with indexes automatically.

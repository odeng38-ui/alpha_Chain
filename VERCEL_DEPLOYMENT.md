# Vercel deployment

Alpha Chain deploys as one Vercel Services project:

- `frontend`: public Next.js service
- `backend`: private FastAPI service, except for the protected Cron route
- PostgreSQL: separate Vercel Marketplace resource (Neon recommended)

Redis is not required by the current application code.

## 1. Create the project

Import this GitHub repository into Vercel and set **Framework Preset** to **Services**. The root `vercel.json` defines both services and injects the private backend URL into the frontend as `BACKEND_URL`.

## 2. Provision PostgreSQL

Add Neon from the Vercel Marketplace and connect it to this project. Confirm that its pooled connection string is exposed as `DATABASE_URL` for Preview and Production.

Run migrations before accepting traffic:

```powershell
cd backend
$env:DATABASE_URL = "<neon-postgresql-url>"
.venv\Scripts\python -m alembic upgrade head
```

Do not commit the connection string.

## 3. Configure environment variables

Set these for Preview and Production:

- `APP_ENV=production`
- `DATABASE_URL` (normally injected by Neon)
- `SECRET_KEY` (long random value)
- `ADMIN_API_KEY` (long random value)
- `CRON_SECRET` (long random value)
- `CRON_BATCH_SIZE=20`
- External provider keys required by enabled ingestion jobs

The application automatically disables APScheduler when Vercel's `VERCEL` variable is present.

## 4. Verify

1. `/` loads the frontend.
2. `/api/backend/ui/dashboard` returns JSON through the private binding.
3. Runtime logs show a successful database connection.
4. `/api/v1/cron/prices` returns `401` without the correct Bearer token.
5. Trigger Cron once and confirm `processed`, `remaining`, and `has_more` in the response.

Cron runs at 07:30 UTC (16:30 Asia/Seoul), Monday through Friday. On Hobby it processes one bounded batch per eligible day. Complete same-day collection for a large universe requires a more frequent Pro cron or a durable queue/workflow.

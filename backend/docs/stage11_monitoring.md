# Stage 11 operations monitoring

The API exposes two monitoring surfaces:

- `GET /metrics`: Prometheus text counters for request count, response status, and cumulative latency. Paths use FastAPI route templates to avoid one time series per entity ID.
- `GET /api/v1/admin/alerts`: administrator-authenticated JSON for failed collection jobs and source freshness.

Send `X-Admin-Key` when `ADMIN_API_KEY` is configured. The alerts endpoint reports:

- `critical` when any `collection_checkpoint` or `dart_sync_state` row has status `FAILED` or `ERROR`.
- `warning` when no job has failed but a source is stale or has no records.
- `ok` when jobs are healthy and all sources are within their freshness limits.

Default freshness limits are 3 days for daily prices, 7 days for filings, and 45 days for macro observations. These are calendar-day safety limits, so operators should interpret weekends and market holidays accordingly.

A basic Prometheus scrape target can point to `/metrics`. Alert automation should poll the admin endpoint and page on `critical`; `warning` is suitable for a non-paging notification.

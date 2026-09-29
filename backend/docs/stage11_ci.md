# Stage 11 CI pipeline

GitHub Actions runs `.github/workflows/ci.yml` for pushes to `main`, pull requests, and manual dispatches.

The pipeline has four gates:

1. Backend lint, a clean PostgreSQL migration to Alembic head, and the full pytest suite on Python 3.11.
2. Reproducible frontend dependency installation and a production Next.js build on Node 20.
3. Gitleaks scans the full Git history for committed secrets.
4. Backend and frontend Docker image builds after all application and security gates pass.

The workflow has read-only repository permissions, does not deploy, and does not require repository secrets. Test-only database credentials and an application secret are scoped to the backend job.

Protect `main` in GitHub and require these checks before merging:

- `Backend quality and migration`
- `Frontend build`
- `Secret scan`
- `Container build`

Deployment should be added as a separate environment-protected workflow after the hosting target and production secrets are selected.

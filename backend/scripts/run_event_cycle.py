"""One invocation, one cycle. Default is PostgreSQL READ ONLY plan; no API calls."""
import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ALLOWED_CLOUD_SSLMODES = frozenset({"require", "verify-ca", "verify-full"})
DIAGNOSTIC_STAGE = "startup"  # Constant labels only; never include connection details.


def validate_database_url(raw_url: str, *, allow_cloud: bool = False, approved_cloud_host: str | None = None):
    from sqlalchemy.engine import make_url

    if not raw_url:
        raise ValueError("DATABASE_URL is required")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Only PostgreSQL is supported")

    # 1. Local operational PostgreSQL policy (preserved)
    if (
        url.host in ("localhost", "127.0.0.1", "::1")
        and (url.port or 5432) == 5432
        and url.database == "alphachain_db"
    ):
        return url, "local"

    # 2. Cloud Neon PostgreSQL policy (strictly gated & TLS enforced)
    if allow_cloud:
        host = (url.host or "").lower()
        dbname = url.database or ""
        sslmode = (url.query.get("sslmode") or "").lower()
        expected_host = (approved_cloud_host or "").strip().lower()

        if not host.endswith(".neon.tech"):
            raise ValueError("Only approved .neon.tech hosts are allowed in cloud mode")
        if expected_host and host != expected_host:
            raise ValueError("Database host does not match APPROVED_NEON_HOST")
        if dbname != "neondb":
            raise ValueError("Only neondb database is allowed in cloud mode")
        if sslmode not in ALLOWED_CLOUD_SSLMODES:
            raise ValueError("Cloud connection requires TLS sslmode (require/verify-ca/verify-full)")
        return url, "cloud"

    raise ValueError("Only the approved local PostgreSQL alphachain_db:5432 is allowed")


def main(argv=None):
    global DIAGNOSTIC_STAGE
    DIAGNOSTIC_STAGE = "startup"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=False, help="Optional .env file path; falls back to process environment")
    parser.add_argument("--scope-file", required=True, help="JSON array of the existing 10 DART corp_codes")
    parser.add_argument("--execute", action="store_true", help="Requires separate explicit approval; writes operational DB")
    parser.add_argument("--cloud", action="store_true", help="Allow approved TLS Neon PostgreSQL host (neondb)")
    parser.add_argument("--cycle-id")
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat, default=datetime.now(ZoneInfo("Asia/Seoul")).date())
    parser.add_argument("--max-items", type=int, default=5)
    parser.add_argument("--max-api-calls", type=int, default=10)
    args = parser.parse_args(argv)

    from dotenv import dotenv_values
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import Session

    values = {}
    if args.env_file:
        values = dict(dotenv_values(args.env_file))

    raw_db_url = values.get("DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    allow_cloud = args.cloud or (os.environ.get("ALLOW_CLOUD_NEON_DB", "").lower() == "true")
    approved_host = values.get("APPROVED_NEON_HOST") or os.environ.get("APPROVED_NEON_HOST")

    DIAGNOSTIC_STAGE = "validate_db_config"
    url, env_mode = validate_database_url(
        raw_db_url,
        allow_cloud=allow_cloud,
        approved_cloud_host=approved_host,
    )

    if not 1 <= args.max_items <= 5 or not 1 <= args.max_api_calls <= 10:
        raise ValueError("Cycle bounds exceeded")

    os.environ["SCHEDULER_ENABLED"] = "false"
    os.environ["APP_ENV"] = "production" if env_mode == "cloud" else "development"
    os.environ["ALPHACHAIN_RUNTIME_MODE"] = "worker"
    os.environ["VERCEL_ENV"] = ""
    os.environ["DATABASE_URL"] = url.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False)

    from app.services.event_cycle_worker import BoundedDartAdapter, run_once, validate_scope
    from app.services.event_postgres_checkpoint import PostgresEventCheckpoint

    scope = json.loads(Path(args.scope_file).read_text(encoding="utf-8-sig"))
    validate_scope(scope)

    connect_args = {"connect_timeout": 15}
    if env_mode == "cloud":
        # Enforce full TLS server certificate & hostname verification in cloud mode
        connect_args["sslmode"] = "verify-full"
        # Set statement timeout & read-only explicitly after connect to support pooled/unpooled Neon
        engine = create_engine(url.set(drivername="postgresql+psycopg2"), connect_args=connect_args)
    else:
        connect_args["options"] = (
            "-c default_transaction_read_only=" + ("off" if args.execute else "on") + " -c statement_timeout=30000"
        )
        engine = create_engine(url.set(drivername="postgresql+psycopg2"), connect_args=connect_args)

    try:
        DIAGNOSTIC_STAGE = "db_connection"
        with Session(engine) as db:
            if env_mode == "cloud":
                if not args.execute:
                    db.execute(text("SET TRANSACTION READ ONLY"))
                db.execute(text("SET statement_timeout = '30000'"))
            DIAGNOSTIC_STAGE = "schema_inspection"
            tables = set(inspect(db.connection()).get_table_names())
            if not {"event_staging_item", "event_cycle_history"}.issubset(tables):
                raise ValueError("Existing staging schema is required; migrations are never automatic")
            if not args.execute:
                DIAGNOSTIC_STAGE = "read_only_check"
                if env_mode == "cloud":
                    assert db.execute(text("SHOW transaction_read_only")).scalar() == "on"
                else:
                    assert db.execute(text("SHOW transaction_read_only")).scalar() == "on"
                DIAGNOSTIC_STAGE = "checkpoint_summary"
                cp = PostgresEventCheckpoint(db, readonly=True)
                print(
                    json.dumps(
                        {
                            "mode": "read_only_plan",
                            "env_mode": env_mode,
                            "scope": scope,
                            "max_items": args.max_items,
                            "max_api_calls": args.max_api_calls,
                            "summary": cp.get_summary(),
                        },
                        ensure_ascii=True,
                        default=str,
                    )
                )
                return 0
            DIAGNOSTIC_STAGE = "execute_cycle"
            key = values.get("DART_API_KEY") or os.environ.get("DART_API_KEY")
            if not key:
                raise ValueError("DART_API_KEY is required")
            adapter = BoundedDartAdapter(key, args.max_api_calls)
            try:
                result = run_once(
                    db,
                    corp_codes=scope,
                    adapter=adapter,
                    end_date=args.end_date,
                    start_date=args.start_date,
                    cycle_id=args.cycle_id,
                    max_items=args.max_items,
                    max_api_calls=args.max_api_calls,
                )
                print(json.dumps(result, ensure_ascii=True))
                return 1 if result["status"] == "failed" else 0
            finally:
                adapter.client.close()
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Do not log connection strings, request query strings, or env file contents.
        orig = getattr(exc, "orig", None)
        sqlstate = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
        # Whitelist diagnostic metadata; never print exception messages or connection URLs.
        detail = {"status": "failed", "error_type": type(exc).__name__, "stage": DIAGNOSTIC_STAGE}
        if isinstance(sqlstate, str) and len(sqlstate) == 5 and sqlstate.isalnum():
            detail["sqlstate"] = sqlstate
        print(json.dumps(detail), file=sys.stderr)
        raise SystemExit(1)

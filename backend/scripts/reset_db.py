"""
Database & Cache Reset Utility.
Wipes all user, conversation, message, artifact, project, and RAG data
from PostgreSQL and flushes Redis cache keys to start fresh.

Usage:
  # Local database:
  uv run python scripts/reset_db.py

  # Hosted database (Supabase / Render):
  DATABASE_URL="postgresql+asyncpg://..." uv run python scripts/reset_db.py --confirm
"""
import asyncio
import os
import sys
from pathlib import Path

# Ensure repository root is on sys.path so 'backend.app' is importable
repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from backend.app.core.config import settings

# Tables in topological/safe order for truncate
TABLES_TO_TRUNCATE = [
    "audit_logs",
    "prompt_optimization_runs",
    "redteam_runs",
    "usage_records",
    "webhook_deliveries",
    "webhook_endpoints",
    "memories",
    "api_keys",
    "prompt_templates",
    "plans",
    "document_chunks",
    "files",
    "artifact_versions",
    "artifacts",
    "messages",
    "conversations",
    "projects",
    "org_invitations",
    "org_members",
    "organizations",
    "users",
]


async def reset_database(target_url: str):
    print(f"\n[Reset] Connecting to database: {target_url.split('@')[-1] if '@' in target_url else target_url}")
    
    # Ensure async driver is used
    if target_url.startswith("postgresql://"):
        target_url = target_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif target_url.startswith("postgres://"):
        target_url = target_url.replace("postgres://", "postgresql+asyncpg://", 1)
        
    engine = create_async_engine(
        target_url,
        connect_args={"statement_cache_size": 0, "max_cached_statement_lifetime": 0},
    )

    async with engine.begin() as conn:
        print("[Reset] Truncating all application tables with CASCADE...")
        for table in TABLES_TO_TRUNCATE:
            try:
                await conn.execute(text(f'TRUNCATE TABLE "{table}" CASCADE;'))
                print(f"  ✓ Truncated {table}")
            except Exception as e:
                # Table might not exist yet if fresh DB
                print(f"  - Skipped {table} ({e.__class__.__name__})")

        print("[Reset] Database tables truncated successfully!")

    await engine.dispose()


async def reset_redis(redis_url: str):
    if not redis_url:
        print("[Reset] No Redis URL provided, skipping cache flush.")
        return

    print(f"\n[Reset] Flushing Redis keys: {redis_url.split('@')[-1] if '@' in redis_url else redis_url}")
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url)
        # Flush DB or scan keys
        await r.flushdb()
        await r.aclose()
        print("[Reset] Redis cache flushed successfully!")
    except Exception as e:
        print(f"[Reset] Redis flush error (non-fatal): {e}")


async def main():
    db_url = os.environ.get("DATABASE_URL") or settings.DATABASE_URL
    redis_url = os.environ.get("REDIS_URL") or settings.REDIS_URL

    print("=" * 60)
    print(" NEXUS AI DATABASE RESET UTILITY")
    print("=" * 60)
    print(f"Target DB: {db_url.split('@')[-1] if '@' in db_url else db_url}")
    
    if "--confirm" not in sys.argv:
        print("\nWARNING: This will delete ALL records (users, messages, chats, files)!")
        print("To proceed, run with --confirm:")
        print("  uv run python scripts/reset_db.py --confirm")
        sys.exit(1)

    await reset_database(db_url)
    await reset_redis(redis_url)
    print("\n[SUCCESS] Environment completely reset and ready for a fresh start!")


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

import os
from typing import Optional

import asyncpg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/postgres")

app = FastAPI()
_pool: Optional[asyncpg.Pool] = None


@app.on_event("startup")
async def startup() -> None:
    """Create an asyncpg connection pool on startup."""
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL)


@app.on_event("shutdown")
async def shutdown() -> None:
    """Close the asyncpg connection pool on shutdown."""
    if _pool is not None:
        await _pool.close()


class Subscription(BaseModel):
    discord_id: int
    plan: str


@app.get("/api/subscription/{discord_id}")
async def get_subscription(discord_id: int) -> dict:
    if _pool is None:
        raise HTTPException(status_code=500, detail="Database pool not initialised")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT discord_id, plan FROM subscriptions WHERE discord_id=$1",
            discord_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return {"discord_id": row["discord_id"], "plan": row["plan"]}


@app.post("/api/subscription")
async def create_subscription(sub: Subscription) -> dict:
    if _pool is None:
        raise HTTPException(status_code=500, detail="Database pool not initialised")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO subscriptions (discord_id, plan)
            VALUES ($1, $2)
            ON CONFLICT (discord_id) DO UPDATE SET plan = EXCLUDED.plan
            RETURNING discord_id, plan
            """,
            sub.discord_id,
            sub.plan,
        )
        return {"discord_id": row["discord_id"], "plan": row["plan"]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.api:app", host="0.0.0.0", port=8000, reload=False)

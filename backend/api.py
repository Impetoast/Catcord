from __future__ import annotations

import os
from typing import Optional

import asyncpg
import requests
import stripe
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from urllib.parse import urlencode

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/postgres")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv(
    "DISCORD_REDIRECT_URI", "http://localhost:8000/auth/discord/callback"
)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

stripe.api_key = STRIPE_SECRET_KEY

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "dev"))
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


@app.get("/auth/discord")
async def auth_discord() -> RedirectResponse:
    if not DISCORD_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Missing Discord OAuth configuration")
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
    }
    url = "https://discord.com/api/oauth2/authorize?" + urlencode(params)
    return RedirectResponse(url)


@app.get("/auth/discord/callback")
async def auth_discord_callback(request: Request, code: str):
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token_res = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    token_res.raise_for_status()
    access_token = token_res.json()["access_token"]
    user_res = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    user_res.raise_for_status()
    user = user_res.json()
    request.session["discord_id"] = int(user["id"])
    request.session["username"] = user["username"]
    return RedirectResponse("/")


@app.get("/session")
async def session_info(request: Request) -> dict:
    if "discord_id" not in request.session:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "discord_id": request.session["discord_id"],
        "username": request.session["username"],
    }


@app.post("/checkout")
async def checkout(request: Request) -> dict:
    if "discord_id" not in request.session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        session = stripe.checkout.Session.create(
            success_url=os.getenv("STRIPE_SUCCESS_URL", "http://localhost:8000/"),
            cancel_url=os.getenv("STRIPE_CANCEL_URL", "http://localhost:8000/"),
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            metadata={
                "discord_id": str(request.session["discord_id"]),
                "plan": "default",
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"id": session.id, "url": session.url}


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request) -> dict:
    payload = (await request.body()).decode("utf-8")
    sig_header = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload")
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        discord_id = int(session["metadata"].get("discord_id", 0))
        plan = session["metadata"].get("plan", "default")
        if _pool is None:
            raise HTTPException(status_code=500, detail="Database pool not initialised")
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO subscriptions (discord_id, plan)
                VALUES ($1, $2)
                ON CONFLICT (discord_id) DO UPDATE SET plan = EXCLUDED.plan
                """,
                discord_id,
                plan,
            )
    return {"status": "success"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.api:app", host="0.0.0.0", port=8000, reload=False)

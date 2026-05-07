import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.routers import auth, ebay, health, images, intake, internal, pages, webhooks
from packages.config import configure_tracing, settings

# Surface app loggers (pricing/publisher/intake) at INFO so Round/Browse/etc.
# messages are visible in `docker compose logs`. uvicorn configures its own
# loggers separately, so this only affects our package logs.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Activate LangSmith tracing before any LangGraph graph is compiled
configure_tracing()

app = FastAPI(
    title="Multi-Agent Sales Assistant",
    version="0.0.1",
)

# CORS — let the Vercel frontend call the API directly so we don't rely on
# the Next.js rewrite proxy (which has its own short upstream timeout).
_allowed_origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- ROUTER REGISTRATION ---
# This tells FastAPI: "When a request comes in for '/health', hand it off to the
# code inside the health.router object."
# We do this for every feature module (auth, ebay, etc.) to keep our code organized.

app.include_router(health.router)  # Checks if the API is alive
app.include_router(auth.router)  # Handles Login / Signup
app.include_router(ebay.router)  # Handles eBay OAuth & Listings
app.include_router(intake.router)  # Handles the "Chat" and Image uploads
app.include_router(images.router)  # Handles image storage/retrieval
app.include_router(internal.router)  # Backend administrative tools
app.include_router(pages.router)  # Serves the static Frontend Files (Next.js Build)
app.include_router(webhooks.router)  # Listens for eBay Events

from fastapi import FastAPI

from apps.api.routers import auth, ebay, health, images, intake, internal, pages, webhooks
from packages.config import configure_tracing

# Activate LangSmith tracing before any LangGraph graph is compiled
configure_tracing()

app = FastAPI(
    title="Multi-Agent Sales Assistant",
    version="0.0.1",
)
# --- ROUTER REGISTRATION ---
# This tells FastAPI: "When a request comes in for '/health', hand it off to the 
# code inside the health.router object."
# We do this for every feature module (auth, ebay, etc.) to keep our code organized.

app.include_router(health.router) # Checks if the API is alive
app.include_router(auth.router)   # Handles Login / Signup
app.include_router(ebay.router)   # Handles eBay OAuth & Listings
app.include_router(intake.router) # Handles the "Chat" and Image uploads
app.include_router(images.router) # Handles image storage/retrieval
app.include_router(internal.router) # Backend administrative tools
app.include_router(pages.router)  # Serves the static Frontend Files (Next.js Build)
app.include_router(webhooks.router) # Listens for eBay Events

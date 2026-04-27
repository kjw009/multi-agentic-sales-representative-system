from fastapi import FastAPI

from apps.api.routers import auth, ebay, health

app = FastAPI(
    title="Multi-Agent Sales Assistant",
    version="0.0.1",
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(ebay.router)

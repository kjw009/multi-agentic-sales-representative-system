from fastapi import FastAPI

from apps.api.routers import auth, ebay, health, images, intake

app = FastAPI(
    title="Multi-Agent Sales Assistant",
    version="0.0.1",
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(ebay.router)
app.include_router(intake.router)
app.include_router(images.router)

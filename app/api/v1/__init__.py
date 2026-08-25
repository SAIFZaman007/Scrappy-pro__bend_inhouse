from fastapi import APIRouter

from app.api.v1 import auth, catalog, health, jobs, analytics

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(catalog.router)
api_router.include_router(jobs.router)
api_router.include_router(analytics.router)

"""FastAPI Application Entrypoint for Enterprise Telco Agentic System."""

import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from src.core.config import settings
from src.core.logging import configure_logging, get_logger
from src.api.routes import chat, network, hitl, mlops, rag, metrics

configure_logging(settings.log_level)
logger = get_logger("enterprise.telco.api")

app = FastAPI(
    title="Enterprise Telco Agentic RAG & ML/NLP Customer Support System",
    description="Production-grade autonomous hybrid agentic architecture across OpenStack and Google Cloud Platform.",
    version="1.0.0",
)

# Cross-Origin Resource Sharing (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files and Templates
BASE_DIR = Path(__file__).resolve().parent.parent
static_dir = BASE_DIR / "frontend" / "static"
templates_dir = BASE_DIR / "frontend" / "templates"

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(templates_dir))

# Include API Routers
API_V1_PREFIX = "/api/v1"
app.include_router(chat.router, prefix=API_V1_PREFIX)
app.include_router(network.router, prefix=API_V1_PREFIX)
app.include_router(hitl.router, prefix=API_V1_PREFIX)
app.include_router(mlops.router, prefix=API_V1_PREFIX)
app.include_router(rag.router, prefix=API_V1_PREFIX)
app.include_router(metrics.router, prefix=API_V1_PREFIX)


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Serve the interactive Telco Command Center web console."""
    return templates.TemplateResponse(request=request, name="index.html", context={"env": settings.environment})


@app.get(f"{API_V1_PREFIX}/health")
def health_check():
    """System health and readiness probe."""
    return {
        "status": "HEALTHY",
        "environment": settings.environment,
        "openstack_onprem_connected": settings.openstack_enabled,
        "gcp_connected": settings.gcp_enabled,
        "active_llm_baseline": "llama-3.1-70b-vllm",
        "active_llm_frontier": settings.gcp_vertex_ai_model,
        "cpni_enclave_status": "ACTIVE",
    }

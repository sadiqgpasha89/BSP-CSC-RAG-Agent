"""FastAPI Application Entrypoint for Enterprise Telco Agentic System."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.api.routes import chat, hitl, metrics, mlops, network, rag
from src.core.config import settings
from src.core.logging import configure_logging, get_logger

configure_logging(settings.log_level)
logger = get_logger("enterprise.telco.api")

app = FastAPI(
    title="Enterprise Telco Agentic RAG & ML/NLP Customer Support System",
    description="Production-grade autonomous hybrid agentic architecture across OpenStack and Google Cloud Platform.",
    version="1.0.0",
)

# Cross-Origin Resource Sharing: an explicit allow-list. The previous wildcard origin combined with
# allow_credentials=True lets any website drive the API with a visitor's credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
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


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the full error server-side; never reflect internals (paths, SQL, stack text) to the caller."""
    logger.error("Unhandled API error", path=request.url.path, error=repr(exc))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Serve the interactive Telco Command Center web console."""
    return templates.TemplateResponse(request=request, name="index.html", context={"env": settings.environment})


@app.get(f"{API_V1_PREFIX}/health")
def health_check():
    """System health and readiness probe, reflecting actual configuration and loaded assets."""
    from src.rag.bm25_engine import bm25_engine
    from src.rag.vector_store import vector_store

    knowledge_ready = vector_store._is_indexed and bm25_engine._is_indexed
    return {
        "status": "HEALTHY" if knowledge_ready else "DEGRADED",
        "environment": settings.environment,
        "openstack_onprem_connected": settings.openstack_enabled,
        "gcp_connected": settings.gcp_enabled,
        "active_llm_baseline": "llama-3.1-70b-vllm",
        "active_llm_frontier": settings.gcp_vertex_ai_model,
        "cpni_enclave_status": "ACTIVE" if settings.cpni_enclave_enabled else "DISABLED",
        "knowledge_base_indexed": knowledge_ready,
        "indexed_chunks": len(vector_store.chunks),
    }

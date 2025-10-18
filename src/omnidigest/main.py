from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from loguru import logger

from .config.settings import AppSettings, get_settings
from .pipeline.run_digest import run_digest_pipeline

app = FastAPI(title="OmniDigest")


def get_app_settings() -> AppSettings:
    return get_settings()


@app.get("/health")
def health(settings: AppSettings = Depends(get_app_settings)) -> dict[str, str]:
    logger.debug("Health check requested for env {}", settings.app_env)
    return {"status": "ok", "environment": settings.app_env}


@app.post("/digest/run")
def run_digest(domain: str = Query(..., description="Domain slug to run")) -> JSONResponse:
    try:
        logger.info("Digest run requested for domain {}", domain)
        result = run_digest_pipeline(domain)
        return JSONResponse({"domain": domain, "output_files": result.output_files})
    except ValueError as exc:
        logger.exception("Digest run failed: {}", exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - safety net
        logger.exception("Unexpected error running digest: {}", exc)
        raise HTTPException(status_code=500, detail="Digest run failed") from exc

"""Public no-auth endpoints for serving the Terms of Service and Privacy
Policy documents.

The .md files in repo/docs/ are baked into the API image and served
verbatim. The frontend's /terms + /privacy pages fetch them and render
client-side (light markdown → HTML).

Why server-rendered: keeps the canonical text on the server (one place
to update), and lets us serve the same text both inline in the dashboard
re-acceptance modal and on the public /terms page.

Endpoints:
  GET /api/legal/tos       Returns {version, body_markdown}
  GET /api/legal/privacy   Returns {version, body_markdown}
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from core import auth as core_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/legal", tags=["legal"])

# docs/ ships in the image at /app/docs/ (added by Dockerfile or already
# present via the repo bind in dev). The path is resolved relative to the
# repo root regardless of which subdir the api server runs from.
_REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR   = _REPO_ROOT / "docs"


class LegalDocument(BaseModel):
    version:       str
    body_markdown: str


def _serve_doc(filename: str, version: Optional[str] = None) -> LegalDocument:
    path = DOCS_DIR / filename
    if not path.exists():
        logger.error(f"Legal doc missing: {path}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="That document is temporarily unavailable.",
        )
    try:
        body = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to read legal doc {path}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load the document.",
        )
    return LegalDocument(
        version       = version or core_auth.CURRENT_TOS_VERSION,
        body_markdown = body,
    )


@router.get("/tos", response_model=LegalDocument)
async def get_tos():
    """Terms of Service — current version."""
    return _serve_doc("TERMS_OF_SERVICE.md")


@router.get("/privacy", response_model=LegalDocument)
async def get_privacy():
    """Privacy Policy — current version (tracked alongside ToS)."""
    return _serve_doc("PRIVACY_POLICY.md")

"""The bill files behind the seeded demo cases.

``flask seed`` creates documents whose raw file is still held; this puts the matching dataset
file in the bucket under ``demo/``, and ``flask reset-demo`` empties that prefix first.
"""

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.case import CaseDocument
from app.services import storage_service
from app.synthetic_data import load_documents

logger = logging.getLogger(__name__)


def upload_seed_documents(session: Session, data_dir: Path) -> int:
    """Store the dataset file for every seeded document whose raw file is still held.

    Returns how many were uploaded. A document with no matching dataset file (the seed
    fabricates bills when the dataset is absent) is skipped.
    """
    try:
        by_hash = {doc.sha256: doc for doc in load_documents(data_dir)}
    except FileNotFoundError:
        return 0
    uploaded = 0
    held = session.execute(
        select(CaseDocument).where(
            CaseDocument.storage_key.like(f"{storage_service.DEMO_PREFIX}%"),
            CaseDocument.raw_deleted_at.is_(None),
        )
    ).scalars()
    for document in held:
        source = by_hash.get(document.sha256)
        if source is None or document.storage_key is None:
            continue
        storage_service.put_object(
            document.storage_key, source.path.read_bytes(), document.mime_type
        )
        uploaded += 1
    return uploaded


def clear_demo_files() -> int:
    """Delete every object under the ``demo/`` prefix and return how many."""
    return storage_service.delete_prefix(storage_service.DEMO_PREFIX)

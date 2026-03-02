import hashlib
import json
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.idempotency import IdempotencyKey


def _hash_payload(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def check_idempotency(
    db: AsyncSession,
    scope: str,
    key: Optional[str],
    payload: dict,
) -> Tuple[Optional[IdempotencyKey], Optional[dict]]:
    if not settings.ENABLE_IDEMPOTENCY or not key:
        return None, None

    request_hash = _hash_payload(payload)
    res = await db.execute(select(IdempotencyKey).where(IdempotencyKey.scope == scope, IdempotencyKey.key == key))
    existing = res.scalar_one_or_none()
    if not existing:
        record = IdempotencyKey(scope=scope, key=key, request_hash=request_hash)
        db.add(record)
        await db.flush()
        return record, None

    if existing.request_hash != request_hash:
        raise HTTPException(status_code=409, detail="Idempotency key reused with different payload")
    if existing.response_json:
        return existing, json.loads(existing.response_json)
    return existing, None


async def store_idempotency_response(db: AsyncSession, record: Optional[IdempotencyKey], response: dict) -> None:
    if not settings.ENABLE_IDEMPOTENCY or not record:
        return
    record.response_json = json.dumps(response, separators=(",", ":"), ensure_ascii=True)
    await db.flush()

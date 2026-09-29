"""Idempotency-Key handling for create endpoints (api-design.md §1.7, database-design.md §6.B).

- Same key + same body, already completed → the original status and the *current* resource,
  with header ``Idempotent-Replayed: true``. Nothing is created twice.
- Same key + different body → 422 IDEMPOTENCY_KEY_MISMATCH.
- Same key still processing → 409 IDEMPOTENCY_IN_PROGRESS.
- A 4xx result is replayed as the same error; a 5xx marks the key ``failed`` so it may be retried.
Only a pointer to the created resource is stored, never the response body.
"""

import hashlib
import json

from flask import g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.api import APIError
from app.extensions import db
from app.models import IdempotencyRecord
from app.models.base import utcnow
from app.models.enums import IdempotencyStatus

HEADER = "Idempotency-Key"
MAX_KEY_LENGTH = 64


def _request_hash(body):
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{request.method} {request.path}\n{canonical}".encode("utf-8")).hexdigest()


def _respond(body, status, replayed=False):
    response = jsonify(body)
    response.status_code = status
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return response


def run_idempotent(*, resource_type, body, create, replay, required):
    """Execute ``create()`` at most once per (user, Idempotency-Key).

    create()        -> (resource_id, response_body, status); adds objects to the session, does not commit.
    replay(res_id)  -> response_body for an already-created resource.
    required        -> when True a missing header is rejected with 428.
    """
    key = request.headers.get(HEADER, "").strip()
    if not key:
        if required:
            raise APIError(428, "IDEMPOTENCY_KEY_REQUIRED", f"{HEADER} header is required")
        resource_id, response_body, status = create()
        db.session.commit()
        return _respond(response_body, status)
    if len(key) > MAX_KEY_LENGTH:
        raise APIError(400, "VALIDATION_ERROR", f"{HEADER} must be at most {MAX_KEY_LENGTH} characters")

    user = g.current_user
    request_hash = _request_hash(body)
    record = db.session.execute(
        select(IdempotencyRecord).filter_by(user_id=user.id, idempotency_key=key)
    ).scalar_one_or_none()

    if record is not None and record.is_expired:
        db.session.delete(record)
        db.session.flush()
        record = None

    if record is not None:
        if record.request_hash != request_hash:
            raise APIError(422, "IDEMPOTENCY_KEY_MISMATCH", f"{HEADER} was already used with a different request")
        if record.status == IdempotencyStatus.PROCESSING:
            raise APIError(409, "IDEMPOTENCY_IN_PROGRESS", "The same request is still being processed")
        if record.status == IdempotencyStatus.COMPLETED:
            if record.error_code:
                raise APIError(record.response_status, record.error_code, "This request already failed; send a new request")
            return _respond(replay(record.resource_id), record.response_status, replayed=True)
        record.status = IdempotencyStatus.PROCESSING  # previous attempt failed with 5xx: retry
    else:
        record = IdempotencyRecord(
            user_id=user.id,
            idempotency_key=key,
            http_method=request.method,
            endpoint=request.path[:255],
            request_hash=request_hash,
            status=IdempotencyStatus.PROCESSING,
        )
        db.session.add(record)
    try:
        db.session.commit()  # claim the key before doing the work
    except IntegrityError:
        db.session.rollback()  # a concurrent request inserted the same key first
        raise APIError(409, "IDEMPOTENCY_IN_PROGRESS", "The same request is still being processed") from None
    record_id = record.id

    try:
        resource_id, response_body, status = create()
        record.status = IdempotencyStatus.COMPLETED
        record.response_status = status
        record.resource_type = resource_type
        record.resource_id = str(resource_id)
        record.completed_at = utcnow()
        db.session.commit()  # business rows and the completed key commit together
        return _respond(response_body, status)
    except APIError as exc:
        db.session.rollback()
        _finish(record_id, IdempotencyStatus.COMPLETED, exc.status, exc.code)
        raise
    except Exception:
        db.session.rollback()
        _finish(record_id, IdempotencyStatus.FAILED, 500, None)
        raise


def _finish(record_id, status, response_status, error_code):
    record = db.session.get(IdempotencyRecord, record_id)
    if record is None:
        return
    record.status = status
    record.response_status = response_status
    record.error_code = error_code
    record.completed_at = utcnow()
    db.session.commit()

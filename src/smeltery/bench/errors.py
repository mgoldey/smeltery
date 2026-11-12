"""One error shape for the whole Bench API: `{"reason": "<sentence>"}` (the `Problem` model).

Routes raise `HTTPException`, FastAPI raises `RequestValidationError`, and a bug raises anything: all three land here
and leave as a `Problem`, so a client reads one shape. A validation failure also carries the structured list under
`errors`, for a client that wants to point at a field. `security.py` builds the same body for the requests it refuses,
and `describe` documents `Problem` as every route's default response in the OpenAPI schema.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

PROBLEM_REF = {"$ref": "#/components/schemas/Problem"}
_JSON = {"application/json": {"schema": PROBLEM_REF}}


def problem(status: int, reason: str, *, headers: dict[str, str] | None = None, **extra: Any) -> JSONResponse:
    return JSONResponse({"reason": reason, **extra}, status_code=status, headers=headers)


def validation_reason(errors: list[dict[str, Any]]) -> str:
    """`"name: Field required; query.n: Input should be a valid integer"` from FastAPI's error list."""
    parts = []
    for e in errors:
        loc = ".".join(str(x) for x in e.get("loc", ()) if x != "body")
        msg = str(e.get("msg", "invalid"))
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "the request is invalid"


async def _http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail
    reason = detail if isinstance(detail, str) and detail else _phrase(exc.status_code)
    if not isinstance(detail, str) and detail:
        reason = str(detail)
    return problem(exc.status_code, reason, headers=getattr(exc, "headers", None))


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = jsonable_encoder(exc.errors(), custom_encoder={BaseException: str})
    return problem(422, validation_reason(errors), errors=errors)


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error in %s %s", request.method, request.url.path)
    return problem(500, "the server hit an unexpected error; the traceback is in the terminal running `smeltery bench`")


def _phrase(status: int) -> str:
    from http import HTTPStatus

    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return f"error {status}"


def describe(schema: dict[str, Any]) -> dict[str, Any]:
    """Make `Problem` every operation's `default` response and its 422, in place; drop FastAPI's own 422 models."""
    for item in schema.get("paths", {}).values():
        for op in item.values():
            if not isinstance(op, dict) or "responses" not in op:
                continue
            responses = op["responses"]
            r422 = responses.get("422")
            if r422 is not None and "HTTPValidationError" in str(r422):
                responses["422"] = {"description": "Validation Error", "content": _JSON}
            responses.setdefault("default", {"description": "Error", "content": _JSON})
    comps = schema.get("components", {}).get("schemas", {})
    for name in ("HTTPValidationError", "ValidationError"):
        comps.pop(name, None)
    return schema


def install_errors(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)

    plain = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = describe(plain())
        return app.openapi_schema

    app.openapi = openapi  # ty: ignore[invalid-assignment]

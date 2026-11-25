"""Cognito authentication and authorization for the OpsFusion API.

Lambda Function URLs do not provide a native Cognito JWT authorizer. The
Function URL is therefore only the transport boundary; protected FastAPI
routes validate Cognito ID tokens and apply the configured administrator-group
policy before entering the simulator or workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from threading import RLock
from typing import Any
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import Depends, HTTPException, Request


class AuthConfigurationError(RuntimeError):
    """Raised when the deployed Cognito configuration is incomplete."""


class InvalidCognitoToken(ValueError):
    """Raised when an ID token cannot be trusted."""


@dataclass(frozen=True, slots=True)
class CognitoSettings:
    user_pool_id: str
    client_id: str
    issuer: str
    admin_group: str


@dataclass(frozen=True, slots=True)
class Principal:
    """The explicit identity contract used by browser-facing routes."""

    subject: str
    username: str
    email: str
    groups: frozenset[str]
    claims: dict[str, Any]

    @property
    def is_admin(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub": self.subject,
            "username": self.username,
            "email": self.email,
            "groups": sorted(self.groups),
            "is_admin": self.is_admin,
        }


@dataclass(frozen=True, slots=True)
class _AdminPrincipal(Principal):
    admin_group: str = "opsfusion-admin"

    @property
    def is_admin(self) -> bool:
        return self.admin_group in self.groups


_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_lock = RLock()
_JWKS_TTL_SECONDS = 3600


def auth_required() -> bool:
    return os.environ.get("OPSFUSION_AUTH_REQUIRED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def cognito_settings() -> CognitoSettings:
    """Read the current deployment configuration at request time."""

    user_pool_id = os.environ.get("OPSFUSION_COGNITO_USER_POOL_ID", "").strip()
    client_id = os.environ.get("OPSFUSION_COGNITO_CLIENT_ID", "").strip()
    issuer = os.environ.get("OPSFUSION_COGNITO_ISSUER", "").strip().rstrip("/")
    admin_group = os.environ.get("OPSFUSION_ADMIN_GROUP", "opsfusion-admin").strip()
    missing = [
        name
        for name, value in (
            ("OPSFUSION_COGNITO_USER_POOL_ID", user_pool_id),
            ("OPSFUSION_COGNITO_CLIENT_ID", client_id),
            ("OPSFUSION_COGNITO_ISSUER", issuer),
            ("OPSFUSION_ADMIN_GROUP", admin_group),
        )
        if not value
    ]
    if missing:
        raise AuthConfigurationError("missing Cognito configuration: " + ", ".join(missing))
    return CognitoSettings(user_pool_id, client_id, issuer, admin_group)


def _fetch_jwks(issuer: str) -> dict[str, Any]:
    now = time.monotonic()
    with _jwks_lock:
        cached = _jwks_cache.get(issuer)
        if cached and cached[0] > now:
            return cached[1]

    request = UrlRequest(
        f"{issuer}/.well-known/jwks.json",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - issuer is deployment configuration.
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # pragma: no cover - network failures are cloud-boundary behavior.
        raise InvalidCognitoToken("could not retrieve Cognito signing keys") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
        raise InvalidCognitoToken("Cognito signing-key response is malformed")

    with _jwks_lock:
        _jwks_cache[issuer] = (now + _JWKS_TTL_SECONDS, payload)
    return payload


def _groups(value: Any) -> frozenset[str]:
    if isinstance(value, list):
        return frozenset(str(item).strip() for item in value if str(item).strip())
    if not isinstance(value, str):
        return frozenset()
    normalized = value.strip()
    if not normalized:
        return frozenset()
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return _groups(parsed)
    return frozenset(
        item.strip(" []'\"")
        for item in normalized.replace(",", " ").split()
        if item.strip(" []'\"")
    )


def _principal_from_claims(claims: dict[str, Any], *, admin_group: str) -> Principal:
    subject = str(claims.get("sub") or "").strip()
    username = str(claims.get("cognito:username") or claims.get("username") or "").strip()
    email = str(claims.get("email") or "").strip()
    if not subject or not username:
        raise InvalidCognitoToken("Cognito token is missing the subject or username")
    groups = _groups(claims.get("cognito:groups", claims.get("groups")))
    # Administrative status is derived from the verified token claim, never
    # from a request body or a browser-provided flag.
    return _AdminPrincipal(
        subject=subject,
        username=username,
        email=email,
        groups=groups,
        claims=dict(claims),
        admin_group=admin_group,
    )


def _local_principal() -> Principal:
    """Provide a clearly synthetic principal for local, auth-disabled tests."""

    return _AdminPrincipal(
        subject="local-operator",
        username="local-operator",
        email="local-operator@synthetic.invalid",
        groups=frozenset({"opsfusion-admin"}),
        claims={"local": True},
        admin_group="opsfusion-admin",
    )


def validate_cognito_id_token(token: str) -> Principal:
    """Validate a Cognito ID token against issuer, audience, expiry, and JWKS."""

    settings = cognito_settings()
    try:
        import jwt
        from jwt import InvalidTokenError
    except ImportError as exc:  # pragma: no cover - enforced by deployment requirements.
        raise AuthConfigurationError("PyJWT is required for Cognito authentication") from exc

    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise InvalidCognitoToken("unsupported Cognito token signing algorithm")
        jwks = _fetch_jwks(settings.issuer)
        jwk = next(
            (
                item
                for item in jwks["keys"]
                if isinstance(item, dict) and item.get("kid") == header["kid"]
            ),
            None,
        )
        if jwk is None:
            # Retry once after key rotation invalidates the cached JWKS.
            with _jwks_lock:
                _jwks_cache.pop(settings.issuer, None)
            jwks = _fetch_jwks(settings.issuer)
            jwk = next(
                (
                    item
                    for item in jwks["keys"]
                    if isinstance(item, dict) and item.get("kid") == header["kid"]
                ),
                None,
            )
        if jwk is None:
            raise InvalidCognitoToken("Cognito token signing key was not found")
        key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=settings.client_id,
            issuer=settings.issuer,
            options={"require": ["exp", "iat", "iss", "sub", "aud"]},
        )
        if claims.get("token_use") != "id":
            raise InvalidCognitoToken("a Cognito ID token is required")
        if not isinstance(claims, dict):
            raise InvalidCognitoToken("Cognito token claims are malformed")
        return _principal_from_claims(claims, admin_group=settings.admin_group)
    except InvalidCognitoToken:
        raise
    except InvalidTokenError as exc:
        raise InvalidCognitoToken("invalid Cognito ID token") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidCognitoToken("invalid Cognito ID token") from exc


def get_current_principal(request: Request) -> Principal:
    """Require a valid Cognito bearer token."""

    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="Bearer Cognito ID token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return validate_cognito_id_token(token.strip())
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except InvalidCognitoToken as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_http_auth(request: Request) -> Principal | None:
    """Protect deployed HTTP routes while keeping local tests convenient."""

    if not auth_required():
        return None
    return get_current_principal(request)


def require_admin(principal: Principal | None = Depends(require_http_auth)) -> Principal:
    """Require the configured Cognito administrator group."""

    if not auth_required():
        return _local_principal()
    if principal is None or not principal.is_admin:
        raise HTTPException(status_code=403, detail="only Cognito users in the admin group may perform this action")
    return principal

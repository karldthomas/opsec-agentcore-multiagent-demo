"""Public Cognito login and admin-only user-management routes."""

from __future__ import annotations

from functools import lru_cache
import os
import re
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import Principal, auth_required, cognito_settings, require_admin, require_http_auth


router = APIRouter(tags=["auth"])
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChallengeRequest(BaseModel):
    username: str
    new_password: str
    session: str


class CreateUserRequest(BaseModel):
    email: str
    temporary_password: str | None = None


@lru_cache(maxsize=1)
def _cognito_client() -> Any:
    return boto3.client("cognito-idp", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def _token_response(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("IdToken") or not result.get("AccessToken"):
        raise HTTPException(status_code=502, detail="Cognito returned an incomplete authentication result")
    return {
        "access_token": result["AccessToken"],
        "id_token": result["IdToken"],
        "refresh_token": result.get("RefreshToken"),
        "expires_in": result.get("ExpiresIn"),
        "token_type": result.get("TokenType", "Bearer"),
    }


def _auth_response(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("AuthenticationResult")
    if isinstance(result, dict):
        return {"status": "authenticated", **_token_response(result)}
    challenge = response.get("ChallengeName")
    session = response.get("Session")
    if challenge and session:
        return {
            "status": "challenge_required",
            "challenge": {
                "name": challenge,
                "session": session,
                "parameters": response.get("ChallengeParameters", {}),
            },
        }
    raise HTTPException(status_code=502, detail="Cognito did not return tokens or a supported challenge")


def _cognito_error(exc: ClientError) -> HTTPException:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    if code in {"NotAuthorizedException", "UserNotFoundException", "PasswordResetRequiredException"}:
        return HTTPException(status_code=401, detail="Cognito authentication failed")
    if code in {"UsernameExistsException"}:
        return HTTPException(status_code=409, detail="a user with that email already exists")
    if code in {"InvalidPasswordException", "InvalidParameterException"}:
        return HTTPException(status_code=400, detail="Cognito rejected the supplied user data")
    return HTTPException(status_code=502, detail="Cognito request failed")


@router.post("/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    settings = cognito_settings()
    if not payload.username.strip() or not payload.password:
        raise HTTPException(status_code=422, detail="username and password are required")
    try:
        response = _cognito_client().initiate_auth(
            ClientId=settings.client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": payload.username.strip(), "PASSWORD": payload.password},
        )
    except ClientError as exc:
        raise _cognito_error(exc) from exc
    except BotoCoreError as exc:
        raise HTTPException(status_code=503, detail="Cognito is unavailable") from exc
    return _auth_response(response)


@router.post("/auth/respond-to-challenge")
def respond_to_challenge(payload: PasswordChallengeRequest) -> dict[str, Any]:
    settings = cognito_settings()
    if not payload.username.strip() or not payload.new_password or not payload.session:
        raise HTTPException(status_code=422, detail="username, new_password, and session are required")
    try:
        response = _cognito_client().respond_to_auth_challenge(
            ClientId=settings.client_id,
            ChallengeName="NEW_PASSWORD_REQUIRED",
            Session=payload.session,
            ChallengeResponses={
                "USERNAME": payload.username.strip(),
                "NEW_PASSWORD": payload.new_password,
            },
        )
    except ClientError as exc:
        raise _cognito_error(exc) from exc
    except BotoCoreError as exc:
        raise HTTPException(status_code=503, detail="Cognito is unavailable") from exc
    return _auth_response(response)


@router.get("/auth/me")
def me(principal: Principal | None = Depends(require_http_auth)) -> dict[str, Any]:
    if principal is None and not auth_required():
        from .auth import _local_principal

        principal = _local_principal()
    if principal is None:
        raise HTTPException(status_code=401, detail="Bearer Cognito ID token required")
    return {"auth_required": auth_required(), **principal.to_dict()}


@router.post("/admin/users", status_code=201)
def create_user(
    payload: CreateUserRequest,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    settings = cognito_settings()
    email = payload.email.strip().lower()
    if not _EMAIL_PATTERN.fullmatch(email):
        raise HTTPException(status_code=422, detail="a valid email is required")
    user_attributes = [{"Name": "email", "Value": email}]
    request: dict[str, Any] = {
        "UserPoolId": settings.user_pool_id,
        "Username": email,
        "UserAttributes": user_attributes,
        "DesiredDeliveryMediums": ["EMAIL"],
    }
    if payload.temporary_password:
        request["TemporaryPassword"] = payload.temporary_password
    try:
        response = _cognito_client().admin_create_user(**request)
    except ClientError as exc:
        raise _cognito_error(exc) from exc
    except BotoCoreError as exc:
        raise HTTPException(status_code=503, detail="Cognito is unavailable") from exc
    user = response.get("User", {})
    return {
        "status": "INVITED",
        "username": user.get("Username", email),
        "email": email,
        "created_by": principal.username,
    }

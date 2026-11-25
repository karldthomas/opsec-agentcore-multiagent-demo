from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api.app import app
from apps.api.auth import _principal_from_claims, require_admin


def test_local_mode_returns_a_synthetic_admin_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPSFUSION_AUTH_REQUIRED", raising=False)
    response = TestClient(app).get("/auth/me")
    assert response.status_code == 200
    assert response.json()["auth_required"] is False
    assert response.json()["is_admin"] is True


def test_deployed_mode_blocks_unauthenticated_api_and_admin_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPSFUSION_AUTH_REQUIRED", "true")
    client = TestClient(app)
    assert client.get("/auth/me").status_code == 401
    assert client.get("/api/scenarios").status_code == 401
    assert client.post("/admin/users", json={"email": "operator@example.com"}).status_code == 401


def test_admin_status_comes_from_the_verified_group_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPSFUSION_AUTH_REQUIRED", "true")
    admin = _principal_from_claims(
        {"sub": "sub-admin", "cognito:username": "admin@example.com", "email": "admin@example.com", "cognito:groups": ["opsfusion-admin"]},
        admin_group="opsfusion-admin",
    )
    operator = _principal_from_claims(
        {"sub": "sub-operator", "cognito:username": "operator@example.com", "email": "operator@example.com", "cognito:groups": ["opsfusion-operator"]},
        admin_group="opsfusion-admin",
    )
    assert admin.is_admin is True
    assert operator.is_admin is False
    assert require_admin(principal=admin).username == "admin@example.com"
    with pytest.raises(HTTPException) as error:
        require_admin(principal=operator)
    assert error.value.status_code == 403


def test_application_stack_configures_invite_only_cognito_and_admin_iam() -> None:
    stack = (Path(__file__).parents[1] / "infrastructure" / "cloudformation" / "stacks" / "06-application.yaml").read_text()
    assert "Type: AWS::Cognito::UserPool" in stack
    assert "AllowAdminCreateUserOnly: true" in stack
    assert "GroupName: opsfusion-admin" in stack
    assert "OPSFUSION_AUTH_REQUIRED: 'true'" in stack
    assert "cognito-idp:AdminCreateUser" in stack

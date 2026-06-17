"""Tests — Authentication and API key management."""
import uuid
import pytest
from tests.conftest import AUTH_API


class TestAuth:
    def test_health(self, api):
        r = api.get(api.url("/api/v1/prismrag/health"))
        assert r.status_code == 200
        assert r.json().get("status") == "ok"

    def test_register_invalid_email(self, api):
        r = api.post(api.url(f"{AUTH_API}/register"), json={
            "email": "not-an-email",
            "password": "ValidPass!1",
            "name": "Test",
        })
        assert r.status_code == 422

    def test_register_short_password(self, api):
        r = api.post(api.url(f"{AUTH_API}/register"), json={
            "email": f"test-{uuid.uuid4().hex[:6]}@qa.io",
            "password": "abc",
            "name": "Test",
        })
        assert r.status_code == 422

    def test_login_wrong_password(self, api, qa_credentials, auth_token):
        r = api.post(api.url(f"{AUTH_API}/login"), json={
            "email":    qa_credentials["email"],
            "password": "WrongPassword!99",
        })
        assert r.status_code == 401

    def test_login_unknown_email(self, api):
        r = api.post(api.url(f"{AUTH_API}/login"), json={
            "email":    "nobody@nowhere.io",
            "password": "SomePass!1",
        })
        assert r.status_code == 401

    def test_me_authenticated(self, authed_api):
        r = authed_api.get(authed_api.url(f"{AUTH_API}/me"))
        assert r.status_code == 200
        data = r.json()
        assert "id" in data
        assert "email" in data
        assert "plan" in data

    def test_me_unauthenticated(self, api):
        r = api.get(api.url(f"{AUTH_API}/me"), headers={"Authorization": "Bearer fake.token.here"})
        assert r.status_code == 401

    def test_create_api_key(self, authed_api):
        r = authed_api.post(authed_api.url(f"{AUTH_API}/api-keys"), json={"name": "QA test key"})
        assert r.status_code in (200, 201)
        data = r.json()
        assert "raw_key" in data
        assert data["raw_key"].startswith("prk_")

    def test_list_api_keys(self, authed_api):
        r = authed_api.get(authed_api.url(f"{AUTH_API}/api-keys"))
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_api_key_auth(self, authed_api, api):
        r = authed_api.post(authed_api.url(f"{AUTH_API}/api-keys"), json={"name": "key-auth-test"})
        raw = r.json()["raw_key"]
        r2 = api.get(api.url(f"{AUTH_API}/me"), headers={"Authorization": f"Bearer {raw}"})
        assert r2.status_code == 200

    def test_revoke_api_key(self, authed_api):
        r = authed_api.post(authed_api.url(f"{AUTH_API}/api-keys"), json={"name": "revoke-me"})
        key_id = r.json()["id"]
        r2 = authed_api.delete(authed_api.url(f"{AUTH_API}/api-keys/{key_id}"))
        assert r2.status_code in (200, 204)

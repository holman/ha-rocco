"""Replays the Clerk sign-in sequence seen in the app capture over mocked HTTP."""

import base64
import json
import time

import pytest

from custom_components.rocco.api import RoccoApi, RoccoApiError
from custom_components.rocco.const import API_URL, CLERK_BASE

P = {"_is_native": "true"}


def _jwt(exp: float) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.sig"


async def test_sign_in_flow(aioclient_mock, hass) -> None:
    aioclient_mock.get(f"{CLERK_BASE}/v1/client", params=P, json={"response": {"object": "client"}},
                       headers={"Authorization": "client-anon"})
    aioclient_mock.post(f"{CLERK_BASE}/v1/client/sign_ins", params=P, json={"response": {
        "id": "sia_1", "status": "needs_first_factor",
        "supported_first_factors": [{"strategy": "reset_password_email_code", "email_address_id": "x"},
                                    {"strategy": "email_code", "email_address_id": "idn_1"}]}})
    aioclient_mock.post(f"{CLERK_BASE}/v1/client/sign_ins/sia_1/prepare_first_factor", params=P,
                        json={"response": {"id": "sia_1", "status": "needs_first_factor"}})
    aioclient_mock.post(f"{CLERK_BASE}/v1/client/sign_ins/sia_1/attempt_first_factor", params=P,
                        json={"response": {"id": "sia_1", "status": "complete", "created_session_id": "sess_1"}},
                        headers={"Authorization": "client-signed-in"})
    aioclient_mock.post(f"{CLERK_BASE}/v1/client/sessions/sess_1/tokens", params=P,
                        json={"object": "token", "jwt": _jwt(time.time() + 60)})
    aioclient_mock.post(API_URL, json={"data": {"login": {"ID": "u1", "Fridges": []}}})

    rotated = []
    session = aioclient_mock.create_session(hass.loop)
    api = RoccoApi(session, None, "DEV", on_client_token=rotated.append)
    assert await api.start_sign_in("me@example.com") == "sia_1"
    assert await api.finish_sign_in("sia_1", "123456") == "client-signed-in"
    await session.close()
    assert rotated == ["client-anon", "client-signed-in"]

    calls = aioclient_mock.mock_calls
    # sign_in request was made with the anonymous client token and the email
    assert calls[1][2] == {"identifier": "me@example.com"}
    assert calls[1][3]["Authorization"] == "client-anon"
    assert calls[2][2] == {"strategy": "email_code", "email_address_id": "idn_1"}
    assert calls[3][2] == {"strategy": "email_code", "code": "123456"}
    # GraphQL login used the freshly minted session JWT
    assert calls[5][3]["Authorization"].startswith("Bearer eyJ")
    assert calls[5][2]["operationName"] == "login"


async def test_wrong_code_surfaces_clerk_error(aioclient_mock, hass) -> None:
    aioclient_mock.post(f"{CLERK_BASE}/v1/client/sign_ins/sia_1/attempt_first_factor", params=P, status=422,
                        json={"errors": [{"code": "form_code_incorrect", "message": "Incorrect code"}]})
    session = aioclient_mock.create_session(hass.loop)
    api = RoccoApi(session, "client-x", "DEV")
    with pytest.raises(RoccoApiError) as err:
        await api.finish_sign_in("sia_1", "000000")
    await session.close()
    assert err.value.code == "form_code_incorrect"

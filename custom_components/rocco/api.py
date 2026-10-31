"""Async client for the Rocco cloud API.

Pure aiohttp with no Home Assistant imports, so it can be exercised standalone
(see scripts/check.py).

Auth: Rocco uses Clerk. Sign-in is email + a one-time code. That yields a
long-lived Clerk *client* token, from which a 60-second session JWT is minted
and sent as a Bearer token to the GraphQL API.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from typing import Any

import aiohttp

from .const import API_URL, CLERK_API_VERSION, CLERK_BASE, UNIT_F, ZONES

USER_AGENT = "HomeAssistant-Rocco/0.1"


class RoccoError(Exception):
    """Base error."""


class RoccoAuthError(RoccoError):
    """Credentials are invalid or the session was revoked."""


class RoccoApiError(RoccoError):
    """The API returned an error."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


# --- GraphQL documents, copied from the app's own requests -------------------

_FRIDGE_STATE = """
fragment FridgeData on FridgeShadow {
  State {
    Desired { ...FridgeState }
    Reported { ...FridgeState }
  }
}
fragment FridgeState on FridgeShadowState {
  UnitTemperature
  UpperZoneSetTempC
  LowerZoneSetTempC
  UpperZoneSetTempF
  LowerZoneSetTempF
  DoorStatus
  DoorLockEngaged
  Lamp2On
  Lamp2Brightness
  CompressorControlMode
}
"""

M_LOGIN = """
mutation login { login { ID Fridges { ID } } }
"""

Q_ME = """
query me {
  me {
    ID
    Fridges { ID Color IOTID Name CompletedLearningMode SightState }
  }
}
"""

Q_REQUIRES_UPDATE = """
query fridgeRequiresUpdate($id: ID!) { fridgeRequiresUpdate(id: $id) }
"""

Q_CONTENTS = """
query fridgeContents($fridge: ID!) {
  fridge(id: $fridge) {
    Name
    Inventory {
      racks {
        num
        title
        items {
          ID
          DataProvider
          product { ID title subTitle image ContainerKind category }
        }
      }
    }
    Alert { Title Message }
  }
}
"""

M_LAMP = (
    """
mutation updateLamp2($fridge: String!, $on: Boolean!) {
  updateLamp2(fridge: $fridge, on: $on) { ...FridgeData }
}
"""
    + _FRIDGE_STATE
)

M_TEMP = (
    """
mutation updateFridgeTemp($fridge: String!, $zone: FridgeTempZone!, $temp: Float!, $unit: UnitTemperature!) {
  updateFridgeTemp(fridge: $fridge, zone: $zone, temp: $temp, unit: $unit) { ...FridgeData }
}
"""
    + _FRIDGE_STATE
)

M_MODE = (
    """
mutation updateCompressorControlMode($fridge: String!, $mode: CompressorControlMode!) {
  updateCompressorControlMode(fridge: $fridge, mode: $mode) { ...FridgeData }
}
"""
    + _FRIDGE_STATE
)


def _jwt_exp(token: str) -> float:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError):
        return time.time() + 30


class RoccoApi:
    """Talks to Clerk for auth and to the Rocco GraphQL API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_token: str | None,
        device_id: str,
        on_client_token: Callable[[str], None] | None = None,
    ) -> None:
        self._http = session
        self._client_token = (client_token or "").strip() or None
        self._device_id = device_id
        self._on_client_token = on_client_token
        self._session_id: str | None = None
        self._jwt: str | None = None
        self._jwt_exp = 0.0

    # --- auth ---------------------------------------------------------------

    @property
    def client_token(self) -> str | None:
        return self._client_token

    async def _clerk(self, method: str, path: str, form: dict[str, str] | None = None) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
            "x-mobile": "1",
            "x-native-device-id": self._device_id,
            "clerk-api-version": CLERK_API_VERSION,
        }
        if self._client_token:
            headers["Authorization"] = self._client_token
        async with self._http.request(
            method, f"{CLERK_BASE}{path}", params={"_is_native": "true"}, headers=headers, data=form
        ) as resp:
            # Clerk issues and rotates the client token via this header.
            rotated = resp.headers.get("Authorization")
            if rotated and rotated != self._client_token:
                self._client_token = rotated
                if self._on_client_token:
                    self._on_client_token(rotated)
            data = await resp.json(content_type=None)
            if resp.status < 400:
                return data
        errors = (data or {}).get("errors") or [{}]
        code = errors[0].get("code")
        message = errors[0].get("long_message") or errors[0].get("message") or f"HTTP {resp.status}"
        if resp.status in (401, 403) or code in ("authentication_invalid", "resource_not_found", "session_not_found"):
            raise RoccoAuthError(message)
        raise RoccoApiError(message, code)

    # --- sign in (email + one-time code) --------------------------------------

    async def start_sign_in(self, email: str) -> str:
        """Email a one-time code. Returns the sign-in attempt id."""
        if not self._client_token:
            await self._clerk("GET", "/v1/client")
        data = await self._clerk("POST", "/v1/client/sign_ins", {"identifier": email.strip()})
        attempt = data.get("response") or data
        factor = next(
            (f for f in attempt.get("supported_first_factors") or [] if f.get("strategy") == "email_code"),
            None,
        )
        if factor is None:
            raise RoccoApiError("This Rocco account can't sign in with an email code", "no_email_code")
        await self._clerk(
            "POST",
            f"/v1/client/sign_ins/{attempt['id']}/prepare_first_factor",
            {"strategy": "email_code", "email_address_id": factor["email_address_id"]},
        )
        return attempt["id"]

    async def finish_sign_in(self, sign_in_id: str, code: str) -> str:
        """Verify the emailed code. Returns the long-lived client token."""
        data = await self._clerk(
            "POST",
            f"/v1/client/sign_ins/{sign_in_id}/attempt_first_factor",
            {"strategy": "email_code", "code": code.strip()},
        )
        attempt = data.get("response") or data
        if attempt.get("status") != "complete" or not attempt.get("created_session_id"):
            raise RoccoApiError("Sign-in did not complete", attempt.get("status"))
        self._session_id = attempt["created_session_id"]
        self._jwt = None
        # The app calls this right after signing in.
        await self._gql("login", M_LOGIN)
        assert self._client_token
        return self._client_token

    async def _find_session(self) -> str:
        data = await self._clerk("GET", "/v1/client")
        client = data.get("response") or data
        active = [s for s in client.get("sessions") or [] if s.get("status") == "active"]
        sid = client.get("last_active_session_id")
        if sid and any(s.get("id") == sid for s in active):
            return sid
        if active:
            return active[0]["id"]
        raise RoccoAuthError("No active Rocco session for this token")

    async def _token(self) -> str:
        if self._jwt and time.time() < self._jwt_exp - 10:
            return self._jwt
        if not self._client_token:
            raise RoccoAuthError("Not signed in")
        if not self._session_id:
            self._session_id = await self._find_session()
        try:
            data = await self._clerk("POST", f"/v1/client/sessions/{self._session_id}/tokens")
        except RoccoAuthError:
            # Session may have been replaced; look it up once more.
            self._session_id = await self._find_session()
            data = await self._clerk("POST", f"/v1/client/sessions/{self._session_id}/tokens")
        self._jwt = data["jwt"]
        self._jwt_exp = _jwt_exp(self._jwt)
        return self._jwt

    # --- graphql ------------------------------------------------------------

    async def _gql(self, op: str, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in (1, 2):
            token = await self._token()
            async with self._http.post(
                API_URL,
                json={"operationName": op, "query": query, "variables": variables or {}},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            ) as resp:
                if resp.status == 401 and attempt == 1:
                    self._jwt = None
                    continue
                if resp.status in (401, 403):
                    raise RoccoAuthError(f"API rejected the session ({resp.status})")
                if resp.status >= 400:
                    raise RoccoApiError(f"{op} returned HTTP {resp.status}")
                payload = await resp.json(content_type=None)
            if payload.get("errors"):
                msg = "; ".join(e.get("message", "?") for e in payload["errors"])
                raise RoccoApiError(f"{op}: {msg}")
            return payload.get("data") or {}
        raise RoccoAuthError("Could not authenticate")

    # --- reads --------------------------------------------------------------

    async def get_account(self) -> dict[str, Any]:
        """Return {'ID': user id, 'Fridges': [...]}."""
        return (await self._gql("me", Q_ME))["me"]

    async def get_contents(self, fridge_id: str) -> dict[str, Any]:
        return (await self._gql("fridgeContents", Q_CONTENTS, {"fridge": fridge_id}))["fridge"]

    async def requires_update(self, fridge_id: str) -> bool:
        return bool((await self._gql("fridgeRequiresUpdate", Q_REQUIRES_UPDATE, {"id": fridge_id}))["fridgeRequiresUpdate"])

    # --- writes (take the fridge's IOTID, return the device shadow) ----------

    async def set_lamp(self, iot_id: str, on: bool) -> dict[str, Any]:
        return (await self._gql("updateLamp2", M_LAMP, {"fridge": iot_id, "on": on}))["updateLamp2"]

    async def set_temp(self, iot_id: str, zone: str, temp: float, unit: str = UNIT_F) -> dict[str, Any]:
        data = await self._gql(
            "updateFridgeTemp",
            M_TEMP,
            {"fridge": iot_id, "zone": ZONES[zone]["enum"], "temp": temp, "unit": unit},
        )
        return data["updateFridgeTemp"]

    async def set_mode(self, iot_id: str, mode: str) -> dict[str, Any]:
        data = await self._gql("updateCompressorControlMode", M_MODE, {"fridge": iot_id, "mode": mode})
        return data["updateCompressorControlMode"]

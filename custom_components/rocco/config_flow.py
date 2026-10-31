"""Config flow for Rocco: email, then the one-time code Rocco emails you."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

import re

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession, async_get_clientsession

from .api import RoccoApi, RoccoApiError, RoccoAuthError, RoccoError
from .const import (
    COGNITO_POOL_ID, CONF_CLIENT_TOKEN, CONF_CODE, CONF_DEVICE_ID, CONF_EMAIL, CONF_IDENTITY_ID, DOMAIN,
)
from .iot import RoccoIot, RoccoIotError, create_identity

_IDENTITY_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d:[0-9a-f-]{36}$")

# Clerk error codes -> our error keys
_EMAIL_ERRORS = {"form_identifier_not_found": "unknown_email", "form_param_format_invalid": "invalid_email"}
_CODE_ERRORS = {"form_code_incorrect": "invalid_code", "verification_expired": "code_expired",
                "verification_failed": "code_expired"}


class RoccoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Sign in to Rocco."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> RoccoOptionsFlow:
        return RoccoOptionsFlow()

    def __init__(self) -> None:
        self._api: RoccoApi | None = None
        self._email = ""
        self._sign_in_id = ""
        self._device_id = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await self._send_code(user_input[CONF_EMAIL])) is None:
                return await self.async_step_code()
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_EMAIL): str}), {CONF_EMAIL: self._email}
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._email = entry_data.get(CONF_EMAIL, "")
        self._device_id = entry_data.get(CONF_DEVICE_ID, "")
        return await self.async_step_user()

    async def _send_code(self, email: str) -> str | None:
        self._email = email.strip()
        self._device_id = self._device_id or str(uuid.uuid4()).upper()
        # Own session: Clerk sets cookies we don't want shared with the rest of HA.
        self._api = RoccoApi(async_create_clientsession(self.hass), None, self._device_id)
        try:
            self._sign_in_id = await self._api.start_sign_in(self._email)
        except RoccoApiError as err:
            return _EMAIL_ERRORS.get(err.code or "", "cannot_sign_in")
        except RoccoError:
            return "cannot_connect"
        return None

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and self._api:
            try:
                token = await self._api.finish_sign_in(self._sign_in_id, user_input[CONF_CODE])
                account = await self._api.get_account()
            except RoccoApiError as err:
                errors["base"] = _CODE_ERRORS.get(err.code or "", "cannot_sign_in")
            except RoccoAuthError:
                errors["base"] = "cannot_sign_in"
            except RoccoError:
                errors["base"] = "cannot_connect"
            else:
                data = {CONF_EMAIL: self._email, CONF_CLIENT_TOKEN: token, CONF_DEVICE_ID: self._device_id}
                await self.async_set_unique_id(account["ID"])
                if self.source == SOURCE_REAUTH:
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(self._get_reauth_entry(), data=data)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=self._email, data=data)
        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            description_placeholders={"email": self._email},
            errors=errors,
        )


class RoccoOptionsFlow(OptionsFlow):
    """Live updates via AWS IoT. Try to set it up automatically; fall back to pasting."""

    def __init__(self) -> None:
        self._auto_error: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["automatic", "manual"])

    async def async_step_automatic(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Mint our own identity and check it can read the user's fridge."""
        try:
            identity = await create_identity(async_get_clientsession(self.hass), COGNITO_POOL_ID)
        except RoccoIotError:
            self._auto_error = "automatic_failed"
        except (TimeoutError, aiohttp.ClientError, OSError):
            self._auto_error = "cannot_connect"
        else:
            if (error := await self._check(identity)) is None:
                return self.async_create_entry(data={CONF_IDENTITY_ID: identity})
            # The pool issued an identity but it can't read the fridge: linking needed.
            self._auto_error = "automatic_unavailable" if error == "identity_rejected" else error
        return await self.async_step_manual()

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if self._auto_error:
            errors["base"] = self._auto_error
            self._auto_error = None
        if user_input is not None:
            identity = (user_input.get(CONF_IDENTITY_ID) or "").strip()
            if not identity:
                return self.async_create_entry(data={})
            if not _IDENTITY_RE.match(identity):
                errors[CONF_IDENTITY_ID] = "invalid_identity"
            elif (error := await self._check(identity)) is not None:
                errors["base"] = error
            else:
                return self.async_create_entry(data={CONF_IDENTITY_ID: identity})
        current = self.config_entry.options.get(CONF_IDENTITY_ID, "")
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Optional(CONF_IDENTITY_ID): str}), {CONF_IDENTITY_ID: current}
            ),
            errors=errors,
        )

    async def _check(self, identity: str) -> str | None:
        """Read one fridge's state to prove the identity can see it."""
        fridges = self.config_entry.runtime_data.data
        iot_id = next(iter(fridges.values())).info["IOTID"]
        try:
            await asyncio.wait_for(
                RoccoIot(async_get_clientsession(self.hass), identity, iot_id, lambda _: None).fetch_once(), 20
            )
        except RoccoIotError:
            return "identity_rejected"
        except (TimeoutError, aiohttp.ClientError, OSError):
            return "cannot_connect"
        return None

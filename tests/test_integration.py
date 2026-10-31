import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

from custom_components.rocco.api import RoccoApiError, RoccoAuthError
from custom_components.rocco.const import (
    CONF_CLIENT_TOKEN, CONF_CODE, CONF_DEVICE_ID, CONF_EMAIL, CONF_IDENTITY_ID, DOMAIN,
)
from custom_components.rocco.iot import RoccoIotError

from unittest.mock import patch

from .conftest import IOT_ID


IDENTITY = "us-east-1:00000000-0000-0000-0000-000000000000"


async def _setup(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="user-1", options=options or {},
                            data={CONF_EMAIL: "me@example.com", CONF_CLIENT_TOKEN: "tok", CONF_DEVICE_ID: "dev"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow(hass: HomeAssistant, api) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_EMAIL: " me@example.com "})
    assert result["step_id"] == "code"
    api.start_sign_in.assert_awaited_with("me@example.com")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: "123456"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    api.finish_sign_in.assert_awaited_with("sia_1", "123456")
    assert result["data"][CONF_CLIENT_TOKEN] == "client-token"
    assert result["data"][CONF_EMAIL] == "me@example.com"
    assert result["result"].unique_id == "user-1"


async def test_config_flow_unknown_email(hass: HomeAssistant, api) -> None:
    api.start_sign_in.side_effect = RoccoApiError("nope", "form_identifier_not_found")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_EMAIL: "x@example.com"})
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "unknown_email"}


async def test_config_flow_bad_code_then_retry(hass: HomeAssistant, api) -> None:
    api.finish_sign_in.side_effect = [RoccoApiError("bad", "form_code_incorrect"), "client-token"]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_EMAIL: "me@example.com"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: "000000"})
    assert result["errors"] == {"base": "invalid_code"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: "123456"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reauth(hass: HomeAssistant, api) -> None:
    entry = await _setup(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_EMAIL: "me@example.com"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: "123456"})
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_CLIENT_TOKEN] == "client-token"
    assert entry.data[CONF_DEVICE_ID] == "dev"


async def test_entities(hass: HomeAssistant, api) -> None:
    await _setup(hass)
    ids = sorted(e.entity_id for e in er.async_entries_for_config_entry(
        er.async_get(hass), hass.config_entries.async_entries(DOMAIN)[0].entry_id))
    print("\n".join(ids))
    assert hass.states.get("sensor.rocco_items").state == "3"
    assert hass.states.get("sensor.rocco_items").attributes["contents"] == {"Example Brewing Lager": 2, "Example Cola": 1}
    assert hass.states.get("sensor.rocco_rack_1_items").state == "2"
    assert hass.states.get("sensor.rocco_alert").state == "None"
    assert hass.states.get("binary_sensor.rocco_firmware_update").state == "off"
    # No shadow yet: controls exist but state is unknown.
    assert hass.states.get("light.rocco_interior_light").state == "unknown"


async def test_commands_update_state(hass: HomeAssistant, api) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    await _setup(hass)
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.rocco_interior_light"}, blocking=True)
    api.set_lamp.assert_awaited_with(IOT_ID, True)
    assert hass.states.get("light.rocco_interior_light").state == "on"
    assert hass.states.get("binary_sensor.rocco_door").state == "off"
    assert hass.states.get("binary_sensor.rocco_door_lock").state == "off"

    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.rocco_cooling_mode", "option": "party"}, blocking=True)
    api.set_mode.assert_awaited_with(IOT_ID, "CompressorControlModeParty")
    assert hass.states.get("select.rocco_cooling_mode").state == "party"

    await hass.services.async_call("number", "set_value",
                                   {"entity_id": "number.rocco_lower_zone_temperature", "value": 45}, blocking=True)
    api.set_temp.assert_awaited_with(IOT_ID, "lower", 45.0, "UnitTemperatureFahrenheit")
    assert float(hass.states.get("number.rocco_lower_zone_temperature").state) == 45

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("number", "set_value",
                                       {"entity_id": "number.rocco_upper_zone_temperature", "value": 50}, blocking=True)


async def test_auth_failure_starts_reauth(hass: HomeAssistant, api) -> None:
    api.get_account.side_effect = RoccoAuthError
    entry = MockConfigEntry(domain=DOMAIN, unique_id="user-1", data={CONF_CLIENT_TOKEN: "t", CONF_DEVICE_ID: "d"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())


async def test_metric_display(hass: HomeAssistant, api) -> None:
    """A metric HA shows the fridge's Fahrenheit setpoint converted to Celsius."""
    hass.config.units = METRIC_SYSTEM
    await _setup(hass)
    await hass.services.async_call("light", "turn_off", {"entity_id": "light.rocco_interior_light"}, blocking=True)
    assert round(float(hass.states.get("number.rocco_upper_zone_temperature").state)) == 3


async def test_live_updates(hass: HomeAssistant, api, fake_iot) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    await _setup(hass, {CONF_IDENTITY_ID: IDENTITY})
    assert fake_iot == [(IDENTITY, IOT_ID)]
    # Live state fills everything in without any command.
    assert hass.states.get("sensor.rocco_upper_zone_current_temperature").state == "39"
    assert hass.states.get("sensor.rocco_lower_zone_current_temperature").state == "45"
    assert hass.states.get("sensor.rocco_room_temperature").state == "64"
    assert hass.states.get("sensor.rocco_wi_fi_signal").state == "-60"
    assert hass.states.get("binary_sensor.rocco_compressor").state == "on"
    assert hass.states.get("binary_sensor.rocco_door").state == "on"
    assert hass.states.get("light.rocco_interior_light").state == "off"
    assert hass.states.get("select.rocco_cooling_mode").state == "smart"
    assert float(hass.states.get("number.rocco_upper_zone_temperature").state) == 38

    # A GraphQL command response (fewer fields) must not wipe live-only values.
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.rocco_interior_light"}, blocking=True)
    assert hass.states.get("light.rocco_interior_light").state == "on"
    assert hass.states.get("sensor.rocco_upper_zone_current_temperature").state == "39"


async def test_no_live_entities_without_identity(hass: HomeAssistant, api, fake_iot) -> None:
    await _setup(hass)
    assert fake_iot == []
    assert hass.states.get("sensor.rocco_upper_zone_current_temperature") is None
    assert hass.states.get("binary_sensor.rocco_compressor") is None


async def _menu(hass, entry, choice):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": choice})


async def test_options_manual_paste(hass: HomeAssistant, api, fake_iot) -> None:
    entry = await _setup(hass)
    result = await _menu(hass, entry, "manual")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_IDENTITY_ID: "nope"})
    assert result["errors"] == {CONF_IDENTITY_ID: "invalid_identity"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_IDENTITY_ID: f" {IDENTITY} "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {CONF_IDENTITY_ID: IDENTITY}
    assert round(float(hass.states.get("sensor.rocco_room_temperature").state)) == 18
    assert fake_iot == [(IDENTITY, IOT_ID)] * 2  # validate, then the live connection


async def test_options_automatic_success(hass: HomeAssistant, api, fake_iot) -> None:
    entry = await _setup(hass)
    result = await _menu(hass, entry, "automatic")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    # The self-minted identity was saved; no pasting needed.
    assert entry.options[CONF_IDENTITY_ID] == "us-east-1:11111111-1111-1111-1111-111111111111"
    assert hass.states.get("binary_sensor.rocco_compressor").state == "on"


async def test_options_automatic_falls_back_to_manual(hass: HomeAssistant, api, fake_iot) -> None:
    entry = await _setup(hass)
    with patch("custom_components.rocco.config_flow.RoccoIot.fetch_once", side_effect=RoccoIotError("no")):
        result = await _menu(hass, entry, "automatic")
    # Minting worked but the identity couldn't read the fridge: show the paste step with a notice.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "automatic_unavailable"}


async def test_options_manual_rejected_identity(hass: HomeAssistant, api, fake_iot) -> None:
    entry = await _setup(hass)
    result = await _menu(hass, entry, "manual")
    with patch("custom_components.rocco.config_flow.RoccoIot.fetch_once", side_effect=RoccoIotError("no")):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_IDENTITY_ID: IDENTITY})
    assert result["errors"] == {"base": "identity_rejected"}

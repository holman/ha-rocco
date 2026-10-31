"""Fixtures. Data shapes are taken from a real capture of the Rocco app."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]

FRIDGE_ID = "fridge-1"
IOT_ID = "iot-1"

ACCOUNT = {
    "ID": "user-1",
    "Fridges": [{"ID": FRIDGE_ID, "Color": "#FFFFFF", "IOTID": IOT_ID, "Name": "Rocco",
                 "CompletedLearningMode": False, "SightState": "enabled"}],
}


def _item(title, sub):
    return {"ID": title + sub, "DataProvider": "rocco",
            "product": {"ID": "p", "title": title, "subTitle": sub, "image": None,
                        "ContainerKind": "can", "category": "x"}}


CONTENTS = {
    "Name": "Rocco",
    "Inventory": {"racks": [
        {"num": 0, "title": "Rack One", "items": [_item("Example Brewing", "Lager")] * 2},
        {"num": 1, "title": "Rack Two", "items": [_item("Example Cola", "")]},
    ]},
    "Alert": None,
}

STATE = {"UnitTemperature": "UnitTemperatureFahrenheit", "UpperZoneSetTempC": 3, "LowerZoneSetTempC": 5,
         "UpperZoneSetTempF": 37, "LowerZoneSetTempF": 41, "DoorStatus": "DoorStatusClosed",
         "DoorLockEngaged": True, "Lamp2On": False, "Lamp2Brightness": "LampBrightnessMedium",
         "CompressorControlMode": "CompressorControlModeIntelligent"}


def shadow(**desired):
    d = deepcopy(STATE)
    d.update(UnitTemperature="", DoorStatus="", DoorLockEngaged=False, **desired)
    return {"State": {"Desired": d, "Reported": deepcopy(STATE)}}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def api():
    with patch("custom_components.rocco.RoccoApi", autospec=True) as cls, \
         patch("custom_components.rocco.config_flow.RoccoApi", new=cls):
        inst = cls.return_value
        inst.get_account = AsyncMock(return_value=deepcopy(ACCOUNT))
        inst.start_sign_in = AsyncMock(return_value="sia_1")
        inst.finish_sign_in = AsyncMock(return_value="client-token")
        inst.get_contents = AsyncMock(return_value=deepcopy(CONTENTS))
        inst.requires_update = AsyncMock(return_value=False)
        inst.set_lamp = AsyncMock(side_effect=lambda iot, on: shadow(Lamp2On=on))
        inst.set_mode = AsyncMock(side_effect=lambda iot, m: shadow(CompressorControlMode=m))
        inst.set_temp = AsyncMock(side_effect=lambda iot, z, t, u: shadow(**{f"{z.capitalize()}ZoneSetTempF": t}))
        yield inst


# Shape of a real get/accepted shadow state (values made up).
IOT_STATE = {
    "desired": {"lowerZoneSetTempF": 44, "upperZoneSetTempF": 38, "lamp2On": False,
                "compressorControlMode": "CompressorControlModeIntelligent"},
    "reported": {"unitTemperature": "UnitTemperatureFahrenheit", "lowerZoneCurrentTempF": 45,
                 "upperZoneCurrentTempF": 39, "lowerZoneSetTempF": 44, "upperZoneSetTempF": 38,
                 "ambientTemperatureF": 64, "evapZoneTemperatureF": 32, "compressorRunning": True,
                 "doorStatus": "DoorStatusOpen", "doorLockEngaged": True, "lamp2On": False,
                 "lamp2Brightness": "LampBrightnessMedium",
                 "compressorControlMode": "CompressorControlModeIntelligent", "wifiSignalStrength": -60},
}


@pytest.fixture
def fake_iot():
    """Replace the live connection with one that pushes IOT_STATE once."""
    import asyncio

    started = []

    class FakeIot:
        def __init__(self, session, identity_id, iot_id, on_state):
            self.on_state = on_state
            started.append((identity_id, iot_id))

        async def run_forever(self):
            self.on_state(deepcopy(IOT_STATE))
            await asyncio.Event().wait()

        async def fetch_once(self):
            return deepcopy(IOT_STATE)

    with patch("custom_components.rocco.RoccoIot", FakeIot), \
         patch("custom_components.rocco.config_flow.RoccoIot", FakeIot), \
         patch("custom_components.rocco.config_flow.create_identity",
               AsyncMock(return_value="us-east-1:11111111-1111-1111-1111-111111111111")):
        yield started

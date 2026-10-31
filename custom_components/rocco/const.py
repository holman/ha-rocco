"""Constants for the Rocco Fridge integration."""

from datetime import timedelta

DOMAIN = "rocco"

CLERK_BASE = "https://clerk.roccofridge.com"
CLERK_API_VERSION = "2025-11-10"
# The shipping iOS app (v1.0.35) talks to this host, despite the name.
API_URL = "https://customer-api-dev.roccofridge.com/query"
# Live state: the app reads the fridge's AWS IoT device shadow over MQTT on a
# WebSocket, using temporary credentials for an (unauthenticated) Cognito identity.
AWS_REGION = "us-east-1"
# Hardcoded in the Rocco app; identity pool ids are not secret.
COGNITO_POOL_ID = "us-east-1:b0a04cb8-615f-4d8b-bd11-5375ba2bf717"
IOT_ENDPOINT = "a2d2svg9kt6cpx-ats.iot.us-east-1.amazonaws.com"

CONF_CLIENT_TOKEN = "client_token"
CONF_DEVICE_ID = "device_id"
CONF_EMAIL = "email"
CONF_CODE = "code"
CONF_IDENTITY_ID = "identity_id"

SCAN_INTERVAL = timedelta(minutes=5)

# GraphQL enum value -> select option key
MODES = {
    "CompressorControlModeIntelligent": "smart",
    "CompressorControlModeParty": "party",
    "CompressorControlModeHoliday": "holiday",
}

UNIT_F = "UnitTemperatureFahrenheit"
UNIT_C = "UnitTemperatureCelsius"

# Zone limits from the Rocco manual. The lower zone must stay warmer than the upper.
ZONES = {
    "upper": {"enum": "FridgeTempZoneUpper", "f": (37, 55), "c": (3, 13)},
    "lower": {"enum": "FridgeTempZoneLower", "f": (41, 61), "c": (5, 16)},
}

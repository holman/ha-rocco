# Rocco Fridge for Home Assistant

Unofficial custom integration for the Rocco Super Smart Fridge. It talks to the
same cloud API as the Rocco iOS app. Rocco doesn't publish this API, so it can
break whenever they change their app.

## What you get

| Entity | Type | Notes |
|---|---|---|
| Upper / lower zone temperature | number | Setpoints. Ranges from the manual. Lower zone can't be set colder than upper. |
| Cooling mode | select | Smart, Party, Holiday |
| Interior light | light | On/off. Brightness shows as an attribute. |
| Door | binary_sensor | Open/closed |
| Door lock | binary_sensor | |
| Firmware update | binary_sensor | Diagnostic |
| Items, Rack N items | sensor | Inventory counts, with a `contents` attribute listing drinks |
| Alert | sensor | The fridge's current alert, if any |

With live updates turned on (see below), you also get:

| Entity | Type | Notes |
|---|---|---|
| Upper / lower zone current temperature | sensor | Measured, not the setpoint |
| Room temperature | sensor | Air temperature around the fridge |
| Compressor | binary_sensor | Running or not |
| Wi-Fi signal | sensor | Diagnostic |
| Evaporator temperature | sensor | Diagnostic, disabled by default |

## Live updates

Without live updates, door, light, mode and setpoints show "unknown" until you
send a command, and they only change when you do. Turning them on also adds
current-temperature, room-temperature, compressor and Wi-Fi sensors, and makes
door and temperature changes arrive instantly.

Open the Rocco integration, choose **Configure**, and pick one:

**Set up automatically.** The integration creates its own AWS identity (the way
the app does on first launch) and checks whether it can read your fridge. If your
Rocco account allows this, you're done, with nothing to paste. Whether it works
depends on how Rocco authorizes devices, so it may not be available for every
account. If it isn't, use the manual option.

**Paste an ID from the Rocco app.** This always works. Capture the Rocco app with
Proxyman using its **Network Extension** mode, because the app's AWS traffic
bypasses the normal proxy. The app asks AWS for credentials about once an hour;
when it does, open the request to `cognito-identity.us-east-1.amazonaws.com` and
copy the `IdentityId` from its body. It looks like `us-east-1:12345678-abcd-…`.
Treat it like a password.

AWS allows one live connection per identity. If you paste the *app's* identity,
Home Assistant and the app take turns: when the app is open, Home Assistant backs
off for a few minutes and then reconnects. Automatic setup avoids this, because it
uses its own identity.

## Install

Copy `custom_components/rocco` into your Home Assistant `config/custom_components/`
folder and restart Home Assistant. Or add this repo to HACS as a custom repository.

Then go to **Settings > Devices & services > Add integration > Rocco Fridge**.

## Signing in

The integration signs in the same way the app does. Enter the email address you
use with the Rocco app, and Rocco emails you a code to type in. Home Assistant
then keeps a long-lived token, so you won't be asked again unless the session is
revoked, for example by "log out of all devices". If that happens, Home Assistant
prompts you to sign in again.

Signing in from Home Assistant does not log the Rocco app out.

## Development

```
python -m pip install pytest-homeassistant-custom-component aiohttp
python -m pytest                                  # unit tests, no network
python scripts/check.py ~/Desktop/capture.har     # read-only live check using a captured token
```

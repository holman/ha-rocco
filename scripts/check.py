"""Read-only smoke test of the Rocco API client, outside Home Assistant.

usage: python3 scripts/check.py            (reads ROCCO_CLIENT_TOKEN from env)
       python3 scripts/check.py capture.har (pulls the token from a HAR export)
"""

import asyncio
import importlib.util
import json
import os
import sys
import types
import uuid
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "rocco"


def load_api():
    # Import api.py + const.py without running the HA-dependent __init__.py.
    pkg = types.ModuleType("rocco")
    pkg.__path__ = [str(PKG)]
    sys.modules["rocco"] = pkg
    for name in ("const", "api"):
        spec = importlib.util.spec_from_file_location(f"rocco.{name}", PKG / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"rocco.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["rocco.api"]


def token_from_har(path: str) -> str:
    # Use the newest client token: Clerk rotates it on sign-in/out.
    token = None
    for entry in json.load(open(path))["log"]["entries"]:
        if "clerk.roccofridge.com" in entry["request"]["url"]:
            for side in (entry["request"], entry["response"]):
                for h in side["headers"]:
                    if h["name"].lower() == "authorization":
                        token = h["value"]
    return token or sys.exit("No Clerk Authorization header found in the HAR")


async def main() -> None:
    import aiohttp

    api_mod = load_api()
    token = token_from_har(sys.argv[1]) if len(sys.argv) > 1 else os.environ["ROCCO_CLIENT_TOKEN"]
    async with aiohttp.ClientSession() as http:
        api = api_mod.RoccoApi(http, token, str(uuid.uuid4()).upper())
        account = await api.get_account()
        print(f"account ok, {len(account['Fridges'])} fridge(s)")
        for f in account["Fridges"]:
            contents = await api.get_contents(f["ID"])
            racks = contents["Inventory"]["racks"]
            print(f"- {f['Name']}: {sum(len(r['items']) for r in racks)} items on {len(racks)} racks, "
                  f"alert={contents['Alert']}, needs_update={await api.requires_update(f['ID'])}")


asyncio.run(main())

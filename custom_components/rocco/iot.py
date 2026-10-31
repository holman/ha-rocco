"""Live fridge state from AWS IoT, the way the Rocco app gets it.

The app holds a Cognito identity (no login attached). It exchanges that for
temporary AWS credentials, opens a SigV4-presigned MQTT-over-WebSocket
connection to Rocco's IoT endpoint, and reads the fridge's device shadow:
  publish  $aws/things/{iot_id}/shadow/get            -> .../get/accepted
  subscribe $aws/things/{iot_id}/shadow/update/documents (pushed on every change)

AWS only accepts the identity id as the MQTT client id, so this connection and
the Rocco app knock each other off. When that happens we back off and let the
app have it.

Pure aiohttp + stdlib, no Home Assistant imports.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

import aiohttp

from .const import AWS_REGION, IOT_ENDPOINT

_LOGGER = logging.getLogger(__name__)

KEEPALIVE = 300
PING_EVERY = 240
RETRY_DELAY = 120          # after an unexpected disconnect
YIELD_DELAY = 600          # after being kicked again soon after connecting
CRED_REFRESH_MARGIN = 300  # reconnect this long before credentials expire

# MQTT packet types
CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = (
    1, 2, 3, 4, 8, 9, 12, 13, 14)


class RoccoIotError(Exception):
    """IoT connection failed."""


async def create_identity(session: aiohttp.ClientSession, pool_id: str) -> str:
    """Mint a fresh unauthenticated Cognito identity, like the app on first launch."""
    async with session.post(
        f"https://cognito-identity.{AWS_REGION}.amazonaws.com/",
        data=json.dumps({"IdentityPoolId": pool_id}),
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityService.GetId",
        },
    ) as resp:
        data = await resp.json(content_type=None)
        if resp.status != 200 or "IdentityId" not in data:
            raise RoccoIotError(f"Cognito GetId failed: {data.get('__type') or resp.status}")
        return data["IdentityId"]


async def get_credentials(session: aiohttp.ClientSession, identity_id: str) -> dict[str, Any]:
    """Exchange a Cognito identity id for temporary AWS credentials."""
    async with session.post(
        f"https://cognito-identity.{AWS_REGION}.amazonaws.com/",
        data=json.dumps({"IdentityId": identity_id}),
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityService.GetCredentialsForIdentity",
        },
    ) as resp:
        data = await resp.json(content_type=None)
        if resp.status != 200 or "Credentials" not in data:
            raise RoccoIotError(f"Cognito rejected the identity: {data.get('__type') or resp.status}")
        return data["Credentials"]


def presign(creds: dict[str, Any], now: dt.datetime | None = None) -> str:
    """SigV4-presign the MQTT WebSocket URL."""
    now = now or dt.datetime.now(dt.UTC)
    amzdate, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    scope = f"{date}/{AWS_REGION}/iotdata/aws4_request"
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{creds['AccessKeyId']}/{scope}",
        "X-Amz-Date": amzdate,
        "X-Amz-SignedHeaders": "host",
    }
    q = "&".join(f"{_quote(k)}={_quote(v)}" for k, v in sorted(query.items()))
    canonical = "\n".join(["GET", "/mqtt", q, f"host:{IOT_ENDPOINT}\n", "host", hashlib.sha256(b"").hexdigest()])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ("AWS4" + creds["SecretKey"]).encode()
    for part in (date, AWS_REGION, "iotdata", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    # IoT expects the session token appended after signing.
    return f"wss://{IOT_ENDPOINT}/mqtt?{q}&X-Amz-Signature={sig}&X-Amz-Security-Token={_quote(creds['SessionToken'])}"


def _quote(s: str) -> str:
    return urllib.parse.quote(s, safe="")


# --- minimal MQTT 3.1.1 ----------------------------------------------------

def _str(s: str) -> bytes:
    b = s.encode()
    return len(b).to_bytes(2, "big") + b


def packet(ptype: int, flags: int, body: bytes) -> bytes:
    n, length = len(body), b""
    while True:
        digit, n = n % 128, n // 128
        length += bytes([digit | (0x80 if n else 0)])
        if not n:
            return bytes([(ptype << 4) | flags]) + length + body


def parse_packets(buf: bytearray) -> list[tuple[int, int, bytes]]:
    """Pop all complete packets from buf as (type, flags, body)."""
    out = []
    while len(buf) >= 2:
        mult, length, i = 1, 0, 1
        while True:
            if i >= len(buf):
                return out
            byte = buf[i]
            i += 1
            length += (byte & 0x7F) * mult
            mult *= 128
            if not byte & 0x80:
                break
        if len(buf) < i + length:
            return out
        out.append((buf[0] >> 4, buf[0] & 0x0F, bytes(buf[i:i + length])))
        del buf[: i + length]
    return out


def parse_publish(flags: int, body: bytes) -> tuple[str, int | None, bytes]:
    tlen = int.from_bytes(body[:2], "big")
    topic = body[2:2 + tlen].decode()
    i = 2 + tlen
    pid = None
    if (flags >> 1) & 3:
        pid = int.from_bytes(body[i:i + 2], "big")
        i += 2
    return topic, pid, body[i:]


def shadow_state(topic: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract {'desired', 'reported'} from a get/accepted or update/documents message."""
    if topic.endswith("/update/documents"):
        payload = payload.get("current") or {}
    return payload.get("state")


# --- connection --------------------------------------------------------------

class RoccoIot:
    """Keeps a live shadow subscription for one fridge."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        identity_id: str,
        iot_id: str,
        on_state: Callable[[dict[str, Any]], None],
    ) -> None:
        self._http = session
        self._identity = identity_id
        self._iot_id = iot_id
        self._on_state = on_state
        self._topic = f"$aws/things/{iot_id}/shadow"
        self.connected = False

    async def run_forever(self) -> None:
        """Connect, stream updates, and reconnect politely. Cancel to stop."""
        while True:
            started = time.monotonic()
            try:
                await self._session_once()
                delay = 1  # clean exit: credential refresh, reconnect right away
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep the loop alive
                lived = time.monotonic() - started
                delay = YIELD_DELAY if lived < 60 else RETRY_DELAY
                _LOGGER.debug("Rocco IoT disconnected after %.0fs (%s); retrying in %ss", lived, err, delay)
            finally:
                self.connected = False
            await asyncio.sleep(delay)

    async def fetch_once(self) -> dict[str, Any]:
        """Connect, read the shadow once, disconnect."""
        creds = await get_credentials(self._http, self._identity)
        async with self._http.ws_connect(presign(creds), protocols=("mqtt",), heartbeat=None) as ws:
            buf = bytearray()
            await self._handshake(ws, buf)
            while True:
                for topic, payload in await self._read(ws, buf):
                    if topic.endswith("/get/accepted"):
                        await ws.send_bytes(packet(DISCONNECT, 0, b""))
                        return shadow_state(topic, payload) or {}

    async def _session_once(self) -> None:
        creds = await get_credentials(self._http, self._identity)
        refresh_at = float(creds["Expiration"]) - CRED_REFRESH_MARGIN
        async with self._http.ws_connect(presign(creds), protocols=("mqtt",), heartbeat=None) as ws:
            buf = bytearray()
            await self._handshake(ws, buf)
            self.connected = True
            last_ping = time.monotonic()
            while time.time() < refresh_at:
                if time.monotonic() - last_ping > PING_EVERY:
                    await ws.send_bytes(packet(PINGREQ, 0, b""))
                    last_ping = time.monotonic()
                try:
                    messages = await self._read(ws, buf, timeout=30)
                except TimeoutError:
                    continue
                for topic, payload in messages:
                    if (state := shadow_state(topic, payload)) is not None:
                        self._on_state(state)
            await ws.send_bytes(packet(DISCONNECT, 0, b""))

    async def _handshake(self, ws: aiohttp.ClientWebSocketResponse, buf: bytearray) -> None:
        connect = _str("MQTT") + bytes([4, 0x02]) + KEEPALIVE.to_bytes(2, "big") + _str(self._identity)
        await ws.send_bytes(packet(CONNECT, 0, connect))
        ack = await self._expect(ws, buf, CONNACK)
        if len(ack) < 2 or ack[1] != 0:
            raise RoccoIotError(f"IoT refused the connection ({ack.hex()})")
        subs = b"".join(
            _str(f"{self._topic}/{t}") + b"\x01" for t in ("update/documents", "get/accepted")
        )
        await ws.send_bytes(packet(SUBSCRIBE, 2, (1).to_bytes(2, "big") + subs))
        await self._expect(ws, buf, SUBACK)
        await ws.send_bytes(packet(PUBLISH, 0, _str(f"{self._topic}/get")))

    async def _expect(self, ws: aiohttp.ClientWebSocketResponse, buf: bytearray, want: int) -> bytes:
        while True:
            for ptype, _flags, body in await self._read_raw(ws, buf):
                if ptype == want:
                    return body
            # anything else before CONNACK/SUBACK is ignored

    async def _read_raw(
        self, ws: aiohttp.ClientWebSocketResponse, buf: bytearray, timeout: float | None = None
    ) -> list[tuple[int, int, bytes]]:
        msg = await ws.receive(timeout=timeout)
        if msg.type != aiohttp.WSMsgType.BINARY:
            raise RoccoIotError(f"IoT connection closed ({msg.type.name})")
        buf.extend(msg.data)
        return parse_packets(buf)

    async def _read(
        self, ws: aiohttp.ClientWebSocketResponse, buf: bytearray, timeout: float | None = None
    ) -> list[tuple[str, dict[str, Any]]]:
        out = []
        for ptype, flags, body in await self._read_raw(ws, buf, timeout):
            if ptype != PUBLISH:
                continue
            topic, pid, raw = parse_publish(flags, body)
            if pid is not None:
                await ws.send_bytes(packet(PUBACK, 0, pid.to_bytes(2, "big")))
            with contextlib.suppress(ValueError):
                out.append((topic, json.loads(raw)))
        return out

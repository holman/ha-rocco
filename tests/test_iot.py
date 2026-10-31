"""Protocol helpers for the AWS IoT live connection."""

import datetime as dt
import json
import urllib.parse

from custom_components.rocco import iot


def test_packet_roundtrip_and_split_frames() -> None:
    body = iot._str("$aws/things/x/shadow/get/accepted") + (7).to_bytes(2, "big") + b'{"state":{}}'
    raw = iot.packet(iot.PUBLISH, 0x02, body) + iot.packet(iot.PINGRESP, 0, b"")
    buf = bytearray(raw[:5])
    assert iot.parse_packets(buf) == []  # incomplete, nothing popped
    buf.extend(raw[5:])
    (ptype, flags, got), (ptype2, _, _) = iot.parse_packets(buf)
    assert (ptype, ptype2) == (iot.PUBLISH, iot.PINGRESP)
    assert buf == bytearray()
    topic, pid, payload = iot.parse_publish(flags, got)
    assert (topic, pid, json.loads(payload)) == ("$aws/things/x/shadow/get/accepted", 7, {"state": {}})


def test_long_remaining_length() -> None:
    body = b"x" * 3000  # needs a two-byte length
    buf = bytearray(iot.packet(iot.PUBLISH, 0, iot._str("t") + body))
    [(_, _, got)] = iot.parse_packets(buf)
    assert got.endswith(body)


def test_shadow_state_from_both_topics() -> None:
    state = {"desired": {"lamp2On": True}, "reported": {"lamp2On": False}}
    assert iot.shadow_state("$aws/things/x/shadow/get/accepted", {"state": state}) == state
    doc = {"previous": {"state": {}}, "current": {"state": state}}
    assert iot.shadow_state("$aws/things/x/shadow/update/documents", doc) == state


def test_presign_shape() -> None:
    creds = {"AccessKeyId": "ASIAEXAMPLE", "SecretKey": "secret", "SessionToken": "tok/en+=="}
    url = iot.presign(creds, dt.datetime(2026, 10, 31, 20, 9, 1, tzinfo=dt.UTC))
    parsed = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(parsed.query)
    assert parsed.scheme == "wss" and parsed.netloc == iot.IOT_ENDPOINT and parsed.path == "/mqtt"
    assert q["X-Amz-Credential"] == ["ASIAEXAMPLE/20261031/us-east-1/iotdata/aws4_request"]
    assert q["X-Amz-Date"] == ["20261031T200901Z"]
    assert q["X-Amz-Security-Token"] == ["tok/en+=="]
    assert len(q["X-Amz-Signature"][0]) == 64
    # The token is appended after the signature, unsigned.
    assert parsed.query.index("X-Amz-Signature") < parsed.query.index("X-Amz-Security-Token")

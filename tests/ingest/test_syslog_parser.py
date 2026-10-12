import json

import pytest

from flocks.ingest.syslog.parser import parse_syslog


def test_parse_se_event_with_pri() -> None:
    raw = (
        '<14>2021-07-08 10:18:00|!secevent|!10.60.61.233|!{"ip":"1.1.1.1","threat_level":3,"tag":["Petya","勒索病毒"]}'
    )

    parsed = parse_syslog(raw, "se")

    assert parsed == {
        "raw": raw,
        "facility": 1,
        "severity": 6,
        "timestamp": "2021-07-08T10:18:00",
        "hostname": "10.60.61.233",
        "app_name": "secevent",
        "message": '{"ip":"1.1.1.1","threat_level":3,"tag":["Petya","勒索病毒"]}',
        "format": "se",
        "log_type": "secevent",
        "client_ip": "10.60.61.233",
        "parse_status": "ok",
        "parse_error": "",
        "data": {
            "ip": "1.1.1.1",
            "threat_level": 3,
            "tag": ["Petya", "勒索病毒"],
        },
    }


def test_parse_se_alarm_without_pri() -> None:
    raw = (
        "2022-01-26T10:11:18.468356+08:00 2022-01-26 10:12:15"
        '|!alarm|!10.222.124.250|!{"alert_id":2141000061,"reliability":3}'
    )

    parsed = parse_syslog(raw, "se")

    assert parsed["facility"] == 1
    assert parsed["severity"] == 6
    assert parsed["timestamp"] == "2022-01-26T10:11:18.468356+08:00"
    assert parsed["hostname"] == "10.222.124.250"
    assert parsed["app_name"] == "alarm"
    assert parsed["data"] == {"alert_id": 2141000061, "reliability": 3}


def test_auto_detects_se_format_without_pri() -> None:
    parsed = parse_syslog('2021-07-08 10:18:00|!secevent|!10.60.61.233|!{"eventKey":"117830036"}')

    assert parsed["format"] == "se"
    assert parsed["data"] == {"eventKey": "117830036"}


def test_parse_se_preserves_invalid_json_message() -> None:
    parsed = parse_syslog(
        "2022-01-26 10:12:15|!alarm|!10.222.124.250|!{invalid-json}",
        "se",
    )

    assert parsed["format"] == "se"
    assert parsed["message"] == "{invalid-json}"
    assert parsed["data"] is None
    assert parsed["parse_status"] == "error"
    assert parsed["parse_error"]


def test_existing_rfc3164_parsing_is_unchanged() -> None:
    parsed = parse_syslog("<34>Oct 11 22:14:15 host-a sshd: login accepted")

    assert parsed["format"] == "rfc3164"
    assert parsed["facility"] == 4
    assert parsed["severity"] == 2
    assert parsed["hostname"] == "host-a"
    assert parsed["app_name"] == "sshd"
    assert parsed["message"] == "login accepted"


@pytest.mark.parametrize("hint", ["auto", "se"])
@pytest.mark.parametrize("suffix", ["\x00", "\x00\r\n", " \x00\t\x00 "])
def test_se_decodes_boundary_terminators_without_changing_raw(hint: str, suffix: str) -> None:
    body = json.dumps({"eventKey": "123", "event_content": "literal |! inside evidence"})
    raw = "  <14>2026-10-12 10:00:00|!secevent|!192.0.2.10|!" + body + suffix

    parsed = parse_syslog(raw, hint)

    assert parsed["data"] == json.loads(body)
    assert parsed["message"] == body
    assert parsed["raw"] == raw
    assert parsed["parse_status"] == "ok"


@pytest.mark.parametrize("body", ['{"brief":"bad\x00evidence"}', '{"id":1}garbage', "[]", "{invalid}"])
def test_se_reports_corrupt_or_non_object_body(body: str) -> None:
    parsed = parse_syslog("2026-10-12 10:00:00|!alarm|!192.0.2.10|!" + body)

    assert parsed["format"] == "se"
    assert parsed["data"] is None
    assert parsed["message"] == body
    assert parsed["parse_status"] == "error"
    assert parsed["parse_error"]


def test_se_retains_escaped_nul_inside_evidence() -> None:
    data = {"brief": "valid\x00evidence"}
    parsed = parse_syslog("2026-10-12 10:00:00|!alarm|!192.0.2.10|!" + json.dumps(data))
    assert parsed["data"] == data
    assert parsed["parse_status"] == "ok"


def test_se_malformed_envelope_has_explicit_error() -> None:
    parsed = parse_syslog("<14>missing-envelope", "se")
    assert parsed["data"] is None
    assert parsed["parse_status"] == "error"


def test_existing_rfc5424_json_remains_unchanged() -> None:
    parsed = parse_syslog('<134>1 2026-10-12T10:00:00Z host sensor - - - {"id":1}')
    assert parsed["format"] == "rfc5424"
    assert parsed["message"] == '{"id":1}'
    assert parsed["app_name"] == "sensor"

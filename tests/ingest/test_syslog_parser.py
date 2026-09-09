from flocks.ingest.syslog.parser import parse_syslog


def test_parse_se_event_with_pri() -> None:
    raw = (
        '<14>2021-07-08 10:18:00|!secevent|!10.60.61.233|!'
        '{"ip":"1.1.1.1","threat_level":3,"tag":["Petya","勒索病毒"]}'
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
    parsed = parse_syslog(
        '2021-07-08 10:18:00|!secevent|!10.60.61.233|!{"eventKey":"117830036"}'
    )

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


def test_existing_rfc3164_parsing_is_unchanged() -> None:
    parsed = parse_syslog("<34>Oct 11 22:14:15 host-a sshd: login accepted")

    assert parsed["format"] == "rfc3164"
    assert parsed["facility"] == 4
    assert parsed["severity"] == 2
    assert parsed["hostname"] == "host-a"
    assert parsed["app_name"] == "sshd"
    assert parsed["message"] == "login accepted"


def test_forced_rfc3164_recovers_vendor_wrapped_json_with_suffix() -> None:
    payload = '{"net":{"real_src_ip":"192.0.2.1","dest_ip":"198.51.100.2"}}'
    raw = f"<134>2026-09-09 14:00:00 appliance audit stream {payload}|!vendor-tail"

    parsed = parse_syslog(raw, "rfc3164")

    assert parsed["format"] == "rfc3164"
    assert parsed["message"] == payload
    assert "data" not in parsed


def test_rfc3164_minified_json_is_not_mistaken_for_tag() -> None:
    payload = '{"id":"alert-1","uri":"/login"}'

    parsed = parse_syslog(f"<34>Oct 11 22:14:15 host-a {payload}")

    assert parsed["app_name"] == ""
    assert parsed["message"] == payload
    assert "data" not in parsed

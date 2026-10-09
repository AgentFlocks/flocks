from flocks.hooks.contracts import REDACTED_VALUE, redact_sensitive, restore_redacted_echo


def test_restore_redacted_echo_round_trips_redacted_view():
    original = {
        "host": "10.0.0.1",
        "password": "s3cret",
        "headers": {"Authorization": "Bearer abc", "Accept": "*/*"},
        "targets": [{"token": "t1", "name": "a"}, "plain"],
    }
    echoed = redact_sensitive(original)
    assert echoed["password"] == REDACTED_VALUE
    assert echoed["headers"]["Authorization"] == REDACTED_VALUE
    assert echoed["targets"][0]["token"] == REDACTED_VALUE

    assert restore_redacted_echo(original, echoed) == original


def test_restore_redacted_echo_keeps_real_changes_and_new_keys():
    original = {"host": "[[V_IP_1]]", "password": "s3cret"}
    patched = {"host": "10.0.0.1", "password": REDACTED_VALUE, "extra": "new"}

    assert restore_redacted_echo(original, patched) == {
        "host": "10.0.0.1",
        "password": "s3cret",
        "extra": "new",
    }


def test_restore_redacted_echo_does_not_invent_values():
    # A marker for a key the original never had is left alone, and a
    # non-sensitive key holding the literal marker is a real value.
    assert restore_redacted_echo({}, {"password": REDACTED_VALUE}) == {"password": REDACTED_VALUE}
    assert restore_redacted_echo({"note": "x"}, {"note": REDACTED_VALUE}) == {"note": REDACTED_VALUE}
    assert restore_redacted_echo({"a": 1}, "scalar") == "scalar"

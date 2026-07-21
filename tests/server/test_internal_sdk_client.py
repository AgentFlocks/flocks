from __future__ import annotations


def test_internal_sdk_client_module_imports():
    import flocks.server.client as client

    assert client.FlocksClient
    assert client.SessionClient

"""Slack app manifest helpers.

The shape mirrors the Hermes Slack gateway setup flow: users create a Slack
app from a manifest, install it, then paste the Bot User OAuth token and
App-level token into the channel config.
"""

from __future__ import annotations


def build_slack_app_manifest(
    *,
    app_name: str = "Flocks",
    description: str = "Connect Flocks agents to Slack via Socket Mode.",
) -> dict:
    """Return a Slack app manifest suitable for the Flocks Slack channel."""
    bot_scopes = [
        "app_mentions:read",
        "channels:history",
        "channels:read",
        "chat:write",
        "groups:history",
        "groups:read",
        "im:history",
        "im:read",
        "im:write",
        "mpim:history",
        "mpim:read",
        "users:read",
    ]
    bot_events = [
        "app_mention",
        "message.channels",
        "message.groups",
        "message.im",
        "message.mpim",
    ]

    return {
        "_metadata": {
            "major_version": 1,
            "minor_version": 1,
        },
        "display_information": {
            "name": app_name[:35],
            "description": description[:140],
            "background_color": "#0f172a",
        },
        "features": {
            "app_home": {
                "home_tab_enabled": False,
                "messages_tab_enabled": True,
                "messages_tab_read_only_enabled": False,
            },
            "bot_user": {
                "display_name": app_name[:80],
                "always_online": True,
            },
        },
        "oauth_config": {
            "scopes": {
                "bot": bot_scopes,
            },
        },
        "settings": {
            "event_subscriptions": {
                "bot_events": bot_events,
            },
            "interactivity": {
                "is_enabled": False,
            },
            "org_deploy_enabled": False,
            "socket_mode_enabled": True,
            "token_rotation_enabled": False,
        },
    }

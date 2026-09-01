"""Interactive setup for the HiveMind player provider."""

from __future__ import annotations

from typing import TYPE_CHECKING

from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import ConfigEntryType

if TYPE_CHECKING:
    from music_assistant.models.setup_flow import SetupSession


async def run_setup(session: SetupSession) -> None:
    values = await session.form(
        [
            ConfigEntry(
                key="host",
                type=ConfigEntryType.STRING,
                label="HiveMind host",
                required=True,
                description="Hostname or IP of the HiveMind node (e.g. myrpi.local).",
            ),
            ConfigEntry(
                key="port",
                type=ConfigEntryType.INTEGER,
                label="HiveMind port",
                required=True,
                default_value=5678,
            ),
            ConfigEntry(
                key="access_key",
                type=ConfigEntryType.SECURE_STRING,
                label="Access key",
                required=True,
                description="The access key issued by HiveMind (hivemind-core add-client).",
            ),
            ConfigEntry(
                key="password",
                type=ConfigEntryType.SECURE_STRING,
                label="Password",
                required=False,
                description="Password for the HiveMind client, if one was set during add-client.",
            ),
            ConfigEntry(
                key="ssl",
                type=ConfigEntryType.BOOLEAN,
                label="Use SSL (wss://)",
                required=False,
                default_value=True,
            ),
            ConfigEntry(
                key="player_name",
                type=ConfigEntryType.STRING,
                label="Player name",
                required=False,
                default_value="MA HiveMind Player",
                description="Display name shown in the MA UI.",
            ),
        ],
        last_step=True,
    )
    await session.finish(values)

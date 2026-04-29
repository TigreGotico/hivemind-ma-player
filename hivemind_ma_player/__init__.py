"""HiveMind player provider for Music Assistant.

Connects to a remote HiveMind node and drives OVOS / OCP playback via the
same OCP bus messages as the local OVOS provider.  The only difference from
the OVOS provider is the transport: HiveMind wraps each Mycroft/OVOS message
in an encrypted HiveMessage envelope and connects over an authenticated
websocket (wss://<host>:<port>?authorization=<b64-key>).

MA controls the remote OVOS instance; OCP state events tunnelled back through
HiveMind are used to keep the MA player state in sync.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from music_assistant_models.config_entries import ConfigEntry, ConfigValueType
from music_assistant_models.enums import (
    ConfigEntryType,
    PlaybackState,
    PlayerFeature,
    ProviderFeature,
)
from music_assistant_models.player import PlayerMedia

from music_assistant.models.player import Player
from music_assistant.models.player_provider import PlayerProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest
    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType

SUPPORTED_FEATURES: set[ProviderFeature] = set()

CONF_HOST = "host"
CONF_PORT = "port"
CONF_KEY = "access_key"
CONF_SSL = "ssl"
CONF_NAME = "player_name"

DEFAULT_PORT = 5678
DEFAULT_SSL = True


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    return HiveMindPlayerProvider(mass, manifest, config, SUPPORTED_FEATURES)


async def get_config_entries(
    mass: MusicAssistant,
    instance_id: str | None = None,
    action: str | None = None,
    values: dict[str, ConfigValueType] | None = None,
) -> tuple[ConfigEntry, ...]:
    return (
        ConfigEntry(
            key=CONF_HOST,
            type=ConfigEntryType.STRING,
            label="HiveMind host",
            required=True,
            description="Hostname or IP of the HiveMind node (e.g. myrpi.local).",
        ),
        ConfigEntry(
            key=CONF_PORT,
            type=ConfigEntryType.INTEGER,
            label="HiveMind port",
            required=False,
            default_value=DEFAULT_PORT,
        ),
        ConfigEntry(
            key=CONF_KEY,
            type=ConfigEntryType.SECURE_STRING,
            label="Access key",
            required=True,
            description="The access key issued by HiveMind (hivemind-core add-client).",
        ),
        ConfigEntry(
            key=CONF_SSL,
            type=ConfigEntryType.BOOLEAN,
            label="Use SSL (wss://)",
            required=False,
            default_value=DEFAULT_SSL,
        ),
        ConfigEntry(
            key=CONF_NAME,
            type=ConfigEntryType.STRING,
            label="Player name",
            required=False,
            default_value="MA HiveMind Player",
            description="Display name shown in the MA UI.",
        ),
    )


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

class HiveMindPlayer(Player):
    """MA Player backed by a remote OVOS instance via HiveMind."""

    def __init__(self, provider: HiveMindPlayerProvider, player_id: str, name: str) -> None:
        super().__init__(provider, player_id)
        self._attr_name = name
        self._attr_supported_features = {
            PlayerFeature.PLAY_MEDIA,
            PlayerFeature.POWER,
            PlayerFeature.PAUSE,
            PlayerFeature.VOLUME_SET,
            PlayerFeature.VOLUME_MUTE,
            PlayerFeature.SEEK,
            PlayerFeature.PLAY_ANNOUNCEMENT,
        }
        self._attr_powered = True
        self._attr_volume_level = 50
        self._attr_volume_muted = False
        self._attr_playback_state = PlaybackState.IDLE

    @property
    def needs_poll(self) -> bool:
        return True

    @property
    def poll_interval(self) -> int:
        return 5 if self._attr_playback_state == PlaybackState.PLAYING else 30

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit(self, msg_type: str, data: dict | None = None) -> None:
        """Send a Mycroft/OVOS message through the HiveMind tunnel."""
        msg = self.provider.Message(msg_type, data or {})
        # HiveMessageBusClient.emit_mycroft() wraps the Message in a
        # HiveMessage and sends it through the encrypted websocket.
        self.provider.bus.emit_mycroft(msg)

    # ------------------------------------------------------------------
    # Playback commands — identical message types to the OVOS provider
    # ------------------------------------------------------------------

    async def play(self) -> None:
        await asyncio.to_thread(self._emit, "ovos.common_play.resume")
        self._attr_playback_state = PlaybackState.PLAYING
        self.update_state()

    async def pause(self) -> None:
        await asyncio.to_thread(self._emit, "ovos.common_play.pause")
        self._attr_playback_state = PlaybackState.PAUSED
        self.update_state()

    async def stop(self) -> None:
        await asyncio.to_thread(self._emit, "ovos.common_play.stop")
        self._attr_playback_state = PlaybackState.IDLE
        self._attr_current_media = None
        self.update_state()

    async def seek(self, position: int) -> None:
        await asyncio.to_thread(
            self._emit, "ovos.common_play.set_track_position", {"position": float(position)}
        )

    async def volume_set(self, volume_level: int) -> None:
        await asyncio.to_thread(
            self._emit, "mycroft.volume.set", {"percent": volume_level / 100}
        )
        self._attr_volume_level = volume_level
        self.update_state()

    async def volume_mute(self, muted: bool) -> None:
        await asyncio.to_thread(
            self._emit, "mycroft.volume.mute" if muted else "mycroft.volume.unmute"
        )
        self._attr_volume_muted = muted
        self.update_state()

    async def power(self, powered: bool) -> None:
        if not powered:
            await self.stop()
        self._attr_powered = powered
        self.update_state()

    async def play_media(self, media: PlayerMedia) -> None:
        url = await self.provider.mass.streams.resolve_stream_url(self.player_id, media)
        await asyncio.to_thread(self._emit, "ovos.common_play.play", {
            "tracks": [{"uri": url,
                        "title": getattr(media, "title", None) or url,
                        "artist": getattr(media, "artist_name", None) or "",
                        "image": getattr(media, "image_url", None) or ""}],
        })
        self._attr_current_media = media
        self._attr_playback_state = PlaybackState.PLAYING
        self.update_state()

    async def play_announcement(
        self, announcement: PlayerMedia, volume_level: int | None = None
    ) -> None:
        url = await self.provider.mass.streams.resolve_stream_url(self.player_id, announcement)
        await asyncio.to_thread(self._emit, "ovos.common_play.play", {
            "tracks": [{"uri": url,
                        "title": getattr(announcement, "title", None) or "Announcement",
                        "artist": "",
                        "image": ""}],
        })

    async def poll(self) -> None:
        """Ask OCP for current playback state via HiveMind."""
        def _ask():
            return self.provider.bus.wait_for_mycroft(
                "ovos.common_play.status.response", timeout=2.0
            )

        self._emit("ovos.common_play.status")
        resp = await asyncio.to_thread(_ask)
        if resp:
            state = resp.data.get("state")
            if state == "playing":
                self._attr_playback_state = PlaybackState.PLAYING
            elif state == "paused":
                self._attr_playback_state = PlaybackState.PAUSED
            else:
                self._attr_playback_state = PlaybackState.IDLE
            pos = resp.data.get("position")
            if pos is not None:
                self._attr_elapsed_time = int(pos)
            volume = resp.data.get("volume")
            if volume is not None:
                self._attr_volume_level = int(volume * 100)
        self.update_state()

    async def on_unload(self) -> None:
        await self.stop()


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class HiveMindPlayerProvider(PlayerProvider):
    """Player provider that drives a remote OVOS / OCP instance via HiveMind."""

    Message = None

    async def handle_async_init(self) -> None:
        try:
            from ovos_bus_client import Message  # noqa: PLC0415
            from hivemind_bus_client import HiveMessageBusClient  # noqa: PLC0415
            self.Message = Message
            self._HiveMessageBusClient = HiveMessageBusClient
        except ImportError as err:
            from music_assistant_models.errors import ProviderUnavailableError
            raise ProviderUnavailableError(
                "hivemind-bus-client or ovos-bus-client not installed") from err

        host = self.config.get_value(CONF_HOST)
        port = int(self.config.get_value(CONF_PORT) or DEFAULT_PORT)
        key = self.config.get_value(CONF_KEY)
        ssl = bool(self.config.get_value(CONF_SSL) if self.config.get_value(CONF_SSL) is not None
                   else DEFAULT_SSL)

        url = self._HiveMessageBusClient.build_url(
            key=key, host=host, port=port, ssl=ssl,
            useragent="MusicAssistantHiveMindPlayer",
        )

        self.bus = self._HiveMessageBusClient(host=url, key=key)

        # connect() is synchronous — run it in a thread
        connect_done = threading.Event()
        connect_error: list[Exception] = []

        def _connect():
            try:
                self.bus.connect()
                connect_done.set()
            except Exception as exc:
                connect_error.append(exc)
                connect_done.set()

        t = threading.Thread(target=_connect, daemon=True)
        t.start()
        await asyncio.to_thread(connect_done.wait, 15)

        if connect_error:
            from music_assistant_models.errors import ProviderUnavailableError
            raise ProviderUnavailableError(
                f"HiveMind connection failed: {connect_error[0]}") from connect_error[0]
        if not connect_done.is_set():
            from music_assistant_models.errors import ProviderUnavailableError
            raise ProviderUnavailableError(
                f"Timed out connecting to HiveMind at {host}:{port}")

        self.logger.info("Connected to HiveMind at %s:%s", host, port)

        # Subscribe to OCP state events tunnelled back from the remote OVOS instance
        self.bus.on("ovos.common_play.track.state", self._on_track_state)
        self.bus.on("ovos.common_play.media.state", self._on_media_state)

    def _on_track_state(self, message) -> None:
        state = message.data.get("state")
        for player in self.players:
            if state == "playing":
                player._attr_playback_state = PlaybackState.PLAYING
            elif state == "paused":
                player._attr_playback_state = PlaybackState.PAUSED
            elif state in ("stopped", "end"):
                player._attr_playback_state = PlaybackState.IDLE
                player._attr_current_media = None
            player.update_state()

    def _on_media_state(self, message) -> None:
        state = message.data.get("state")
        if state in ("end", "error"):
            for player in self.players:
                player._attr_playback_state = PlaybackState.IDLE
                player._attr_current_media = None
                player.update_state()

    async def discover_players(self) -> None:
        player_id = f"{self.instance_id}:hivemind"
        name = self.config.get_value(CONF_NAME) or "MA HiveMind Player"
        player = HiveMindPlayer(self, player_id, name=name)
        await self.mass.players.register(player)

    async def unload(self) -> None:
        if hasattr(self, "bus"):
            self.bus.close()

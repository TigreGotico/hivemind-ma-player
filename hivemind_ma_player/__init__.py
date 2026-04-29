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
CONF_PASSWORD = "password"
CONF_SSL = "ssl"
CONF_NAME = "player_name"

DEFAULT_PORT = 5678
DEFAULT_SSL = True

# OCP PlayerState IntEnum values (from ovos_utils.ocp)
_OCP_STOPPED = 0
_OCP_PLAYING = 1
_OCP_PAUSED = 2


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
            key=CONF_PASSWORD,
            type=ConfigEntryType.SECURE_STRING,
            label="Password",
            required=False,
            description="Optional password for the HiveMind client (if set during add-client).",
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
        self.provider.bus.emit_mycroft(msg)

    def _make_media_entry(self, url: str, media: PlayerMedia):
        """Build an OCP MediaEntry for the given URL + MA PlayerMedia."""
        from ovos_utils.ocp import MediaEntry, MediaType, PlaybackType, TrackState  # noqa: PLC0415
        return MediaEntry(
            uri=url,
            title=getattr(media, "title", None) or url,
            artist=getattr(media, "artist_name", None) or "",
            length=int(getattr(media, "duration", None) or 0),
            match_confidence=100,
            skill_id="music_assistant",
            status=TrackState.QUEUED_AUDIO,
            media_type=MediaType.MUSIC,
            playback=PlaybackType.AUDIO,
            image=getattr(media, "image_url", None) or "",
        )

    # ------------------------------------------------------------------
    # Playback commands
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
        entry = await asyncio.to_thread(self._make_media_entry, url, media)
        await asyncio.to_thread(self._emit, "ovos.common_play.play", {"media": entry.as_dict})
        self._attr_current_media = media
        self._attr_playback_state = PlaybackState.PLAYING
        self.update_state()

    async def play_announcement(
        self, announcement: PlayerMedia, volume_level: int | None = None
    ) -> None:
        url = await self.provider.mass.streams.resolve_stream_url(self.player_id, announcement)
        entry = await asyncio.to_thread(self._make_media_entry, url, announcement)
        await asyncio.to_thread(self._emit, "ovos.common_play.play", {"media": entry.as_dict})

    async def poll(self) -> None:
        """Ask OCP for current playback state via HiveMind."""
        def _ask():
            # wait_for_response sends the message and registers the listener
            # atomically, avoiding the race where a response arrives before
            # wait_for_mycroft starts listening.
            return self.provider.bus.wait_for_response(
                self.provider.Message("ovos.common_play.status"),
                reply_type="ovos.common_play.status.response",
                timeout=3.0,
            )

        resp = await asyncio.to_thread(_ask)
        if resp:
            # wait_for_response returns a HiveMessage; .payload is the inner Message
            data = resp.payload.data if hasattr(resp, "payload") else resp.data
            # state is PlayerState IntEnum: STOPPED=0, PLAYING=1, PAUSED=2
            raw_state = data.get("state")
            if raw_state == _OCP_PLAYING:
                self._attr_playback_state = PlaybackState.PLAYING
            elif raw_state == _OCP_PAUSED:
                self._attr_playback_state = PlaybackState.PAUSED
            elif raw_state == _OCP_STOPPED:
                self._attr_playback_state = PlaybackState.IDLE
            media = data.get("media")
            if media and isinstance(media, dict):
                pos = media.get("position")
                if pos is not None:
                    self._attr_elapsed_time = int(pos)
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
            from ovos_bus_client.util import FakeBus  # noqa: PLC0415
            self.Message = Message
            self._HiveMessageBusClient = HiveMessageBusClient
            self._FakeBus = FakeBus
        except ImportError as err:
            from music_assistant_models.errors import ProviderUnavailableError
            raise ProviderUnavailableError(
                "hivemind-bus-client or ovos-bus-client not installed") from err

        host = self.config.get_value(CONF_HOST)
        port = int(self.config.get_value(CONF_PORT) or DEFAULT_PORT)
        key = self.config.get_value(CONF_KEY)
        password = self.config.get_value(CONF_PASSWORD) or ""
        ssl = bool(self.config.get_value(CONF_SSL) if self.config.get_value(CONF_SSL) is not None
                   else DEFAULT_SSL)

        # Positional: key first, then keyword args for host/port/password
        self.bus = self._HiveMessageBusClient(key, host=host, port=port,
                                              password=password, ssl=ssl)

        connect_done = threading.Event()
        connect_error: list[Exception] = []

        def _connect():
            try:
                # connect() requires a FakeBus for the local side of the tunnel
                self.bus.connect(self._FakeBus())
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

        # ovos.common_play.player.state carries PlayerState (STOPPED/PLAYING/PAUSED)
        self.bus.on("ovos.common_play.player.state", self._on_player_state)
        self.bus.on("ovos.common_play.media.state", self._on_media_state)

    def _on_player_state(self, message) -> None:
        """OCP high-level player state changed."""
        raw_state = message.data.get("state")
        for player in self.players:
            if raw_state == _OCP_PLAYING:
                player._attr_playback_state = PlaybackState.PLAYING
            elif raw_state == _OCP_PAUSED:
                player._attr_playback_state = PlaybackState.PAUSED
            elif raw_state == _OCP_STOPPED:
                player._attr_playback_state = PlaybackState.IDLE
                player._attr_current_media = None
            player.update_state()

    def _on_media_state(self, message) -> None:
        """OCP media pipeline finished or errored."""
        from ovos_utils.ocp import MediaState  # noqa: PLC0415
        state = message.data.get("state")
        if state in (MediaState.END, MediaState.ERROR, 6, 7):
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

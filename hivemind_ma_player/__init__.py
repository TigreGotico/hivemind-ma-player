"""HiveMind player provider for Music Assistant.

Connects to a remote HiveMind node and drives OVOS / OCP playback via the
same OCP bus messages as the local OVOS provider.  The only difference from
the OVOS provider is the transport: HiveMind wraps each Mycroft/OVOS message
in an encrypted HiveMessage envelope and connects over an authenticated
websocket (wss://<host>:<port>?authorization=<b64-key>).

Incoming messages are validated with ovos-pydantic-models before their
fields are accessed, so schema changes in OVOS surface as logged warnings
rather than silent AttributeErrors.
"""

from __future__ import annotations

import asyncio
import logging
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

_LOGGER = logging.getLogger(__name__)

SUPPORTED_FEATURES: set[ProviderFeature] = set()

CONF_HOST = "host"
CONF_PORT = "port"
CONF_KEY = "access_key"
CONF_PASSWORD = "password"
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
# Helpers (shared with ovos-ma-player, duplicated intentionally — no coupling)
# ---------------------------------------------------------------------------

def _make_ocp_media_entry(url: str, media: PlayerMedia) -> dict:
    """Serialize a MA PlayerMedia to an OCP MediaEntry dict."""
    from ovos_pydantic_models.skills.ocp import (  # noqa: PLC0415
        MediaEntry, MediaType, PlaybackType,
    )
    duration_s = getattr(media, "duration", None) or 0
    entry = MediaEntry(
        uri=url,
        title=getattr(media, "title", None) or url,
        artist=getattr(media, "artist_name", None) or "",
        length=int(duration_s * 1000),  # MA gives seconds; OCP expects ms
        match_confidence=1.0,
        skill_id="music_assistant",
        media_type=MediaType.MUSIC,
        playback=PlaybackType.AUDIO,
        image=getattr(media, "image_url", None) or "",
    )
    return entry.model_dump()


def _make_play_payload(entry_dict: dict) -> dict:
    """Build a valid ovos.common_play.play payload dict."""
    from ovos_pydantic_models.skills.ocp import OvosCommonPlayPlayData  # noqa: PLC0415
    return OvosCommonPlayPlayData(
        media=entry_dict,
        disambiguation=[],
        playlist=[entry_dict],
    ).model_dump()


def _parse_player_state(raw: dict) -> PlaybackState | None:
    """Validate ovos.common_play.player.state data; return MA PlaybackState or None."""
    from ovos_pydantic_models.skills.ocp import (  # noqa: PLC0415
        OvosCommonPlayPlayerStateData, PlayerState,
    )
    try:
        data = OvosCommonPlayPlayerStateData(**raw)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Unexpected ovos.common_play.player.state payload: %s", exc)
        return None
    if data.state == PlayerState.PLAYING:
        return PlaybackState.PLAYING
    if data.state == PlayerState.PAUSED:
        return PlaybackState.PAUSED
    return PlaybackState.IDLE


def _parse_media_state_end(raw: dict) -> bool:
    """Return True if ovos.common_play.media.state signals end or error."""
    from ovos_pydantic_models.audio.ocp import (  # noqa: PLC0415
        OvosCommonPlayMediaStateData, OcpMediaState,
    )
    try:
        data = OvosCommonPlayMediaStateData(**raw)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Unexpected ovos.common_play.media.state payload: %s", exc)
        return False
    return data.state in (OcpMediaState.END_OF_MEDIA, OcpMediaState.INVALID_MEDIA)


def _parse_status_response(raw: dict) -> tuple[PlaybackState | None, int | None]:
    """Validate ovos.common_play.status.response; return (playback_state, elapsed_ms)."""
    from ovos_pydantic_models.skills.ocp import (  # noqa: PLC0415
        OvosCommonPlayStatusResponseData, PlayerState,
    )
    try:
        data = OvosCommonPlayStatusResponseData(**raw)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Unexpected ovos.common_play.status.response payload: %s", exc)
        return None, None

    pb_state: PlaybackState | None = None
    if data.state == PlayerState.PLAYING:
        pb_state = PlaybackState.PLAYING
    elif data.state == PlayerState.PAUSED:
        pb_state = PlaybackState.PAUSED
    elif data.state is not None:
        pb_state = PlaybackState.IDLE

    elapsed_ms: int | None = None
    if isinstance(data.media, dict):
        pos = data.media.get("position")
        if pos is not None:
            elapsed_ms = int(pos)
    elif data.media is not None:
        pos = getattr(data.media, "position", None)
        if pos is not None:
            elapsed_ms = int(pos)

    return pb_state, elapsed_ms


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

    def _emit(self, msg_type: str, data: dict | None = None) -> None:
        """Send a Mycroft/OVOS message through the HiveMind tunnel."""
        msg = self.provider.Message(msg_type, data or {})
        self.provider.bus.emit_mycroft(msg)

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
        # OCP set_track_position expects milliseconds
        await asyncio.to_thread(
            self._emit, "ovos.common_play.set_track_position",
            {"position": int(position * 1000)},
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
        entry_dict = await asyncio.to_thread(_make_ocp_media_entry, url, media)
        payload = await asyncio.to_thread(_make_play_payload, entry_dict)
        await asyncio.to_thread(self._emit, "ovos.common_play.play", payload)
        self._attr_current_media = media
        self._attr_playback_state = PlaybackState.PLAYING
        self.update_state()

    async def play_announcement(
        self, announcement: PlayerMedia, volume_level: int | None = None
    ) -> None:
        url = await self.provider.mass.streams.resolve_stream_url(self.player_id, announcement)
        entry_dict = await asyncio.to_thread(_make_ocp_media_entry, url, announcement)
        payload = await asyncio.to_thread(_make_play_payload, entry_dict)
        await asyncio.to_thread(self._emit, "ovos.common_play.play", payload)

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
            # wait_for_response with a MycroftMessage returns a HiveMessage;
            # .payload on a HiveMessageType.BUS message reconstructs the inner Message.
            inner = resp.payload if hasattr(resp, "payload") else resp
            raw = inner.data if hasattr(inner, "data") else {}
            pb_state, elapsed_ms = _parse_status_response(raw)
            if pb_state is not None:
                self._attr_playback_state = pb_state
            if elapsed_ms is not None:
                self._attr_elapsed_time = elapsed_ms // 1000  # ms → s
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

        # key is positional; host/port/password are keyword args
        self.bus = self._HiveMessageBusClient(key, host=host, port=port,
                                              password=password, ssl=ssl)

        connect_done = threading.Event()
        connect_error: list[Exception] = []

        def _connect():
            try:
                self.bus.connect(self._FakeBus())
                connect_done.set()
            except Exception as exc:  # noqa: BLE001
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

        # bus.on() with a non-HiveMessageType name routes via on_mycroft(),
        # so handlers receive the inner Message (not HiveMessage wrapper).
        self.bus.on("ovos.common_play.player.state", self._on_player_state)
        self.bus.on("ovos.common_play.media.state", self._on_media_state)

    def _on_player_state(self, message) -> None:
        pb_state = _parse_player_state(message.data)
        if pb_state is None:
            return
        for player in self.players:
            player._attr_playback_state = pb_state
            if pb_state == PlaybackState.IDLE:
                player._attr_current_media = None
            player.update_state()

    def _on_media_state(self, message) -> None:
        if not _parse_media_state_end(message.data):
            return
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

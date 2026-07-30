# Architecture — hivemind-ma-player

## Full Stack Architecture

```
┌─────────────────────┐         wss (encrypted)        ┌───────────────────────────────┐
│   Music Assistant   │ ──────────────────────────────► │       Remote Device           │
│                     │                                  │                               │
│  hivemind-ma-player │         OCP bus messages         │  hivemind-core                │
│  (MA PlayerProvider)│ ◄──────────────────────────────  │    └─ hivemind-player-agent   │
│                     │      state events tunnelled       │         └─ ovos-audio (OCP)   │
└─────────────────────┘                                  │              └─ mpv/vlc        │
                                                          └───────────────────────────────┘
```

### Component roles

**MA + hivemind-ma-player (this package)**

`HiveMindPlayerProvider` holds a `HiveMessageBusClient` connected to the remote device.
`HiveMindPlayer._emit` wraps each OVOS `Message` in a `HiveMessage` envelope and calls
`bus.emit_mycroft()` to send it through the encrypted tunnel. State events that arrive from
the remote device reach an internal `FakeBus` and are dispatched to `_on_player_state` /
`_on_media_state`, which update MA's player state. `hivemind_ma_player/__init__.py:241`

**hivemind-core (on the remote device)**

Listens on the configured port (default 5678), authenticates connecting clients using the
access key and optional password, maintains the TLS WebSocket connection, and routes
`HiveMessage` envelopes between clients and the agent plugin.

**hivemind-player-agent (`HiveMindPlayerProtocol`)**

An `AgentProtocol` implementation loaded by `hivemind-core` through the `server.json`
`agent_protocol` config key. On startup it creates an internal `FakeBus` and instantiates
`ovos-audio`'s `PlaybackService` connected to that bus. `hivemind_player_protocol/__init__.py:15`

When `hivemind-core` receives an inbound `HiveMessage` from MA, it calls into the agent, which
unwraps the inner OVOS `Message` and emits it on the `FakeBus`. `PlaybackService` (OCP)
receives the message on its bus and acts on it — starting playback, pausing, seeking, and so
on.

`handle_internal_mycroft` forwards responses and state events that `PlaybackService` emits on
the `FakeBus` back to the connected client (MA). It wraps them in a
`HiveMessage(HiveMessageType.BUS)` and calls `client.send()`. `hivemind_player_protocol/__init__.py:83`

**ovos-audio + OCP**

`PlaybackService` is the audio subsystem from `ovos-audio`. It manages track queuing, playback
state, and delegates actual audio output to whichever backend plugin is active (mpv, VLC, and
so on). It emits `ovos.common_play.player.state` and `ovos.common_play.media.state` on the bus
as playback progresses. These events are tunnelled back to MA.

---

## Class diagram

```
music_assistant.models.player_provider.PlayerProvider
    └── HiveMindPlayerProvider              (hivemind_ma_player/__init__.py:339)
            │  owns bus: HiveMessageBusClient
            │  owns players: list[Player]
            │  attr: Message (class, from ovos_bus_client)
            └── registers
                    HiveMindPlayer          (hivemind_ma_player/__init__.py:213)
                        extends music_assistant.models.player.Player
                        back-ref provider -> HiveMindPlayerProvider

Module-level helpers (identical to ovos-ma-player):
    _make_ocp_media_entry(url, media) -> dict   :117
    _make_play_payload(entry_dict) -> dict       :137
    _parse_player_state(raw) -> PlaybackState   :147
    _parse_media_state_end(raw) -> bool          :164
    _parse_status_response(raw) -> tuple         :177
```

`HiveMindPlayerProvider` holds the single `HiveMessageBusClient` instance and exposes it (and
the `Message` class from `ovos_bus_client`) as instance attributes. `HiveMindPlayer` never
imports from either bus package directly. It always goes through `self.provider.bus` and
`self.provider.Message`.

---

## What is a Music Assistant PlayerProvider?

MA has three categories of provider: music providers (library content), player providers
(playback devices), and metadata providers. This package is a player provider.

For a full explanation of the MA provider model and OCP, see
[ovos-ma-player/docs/architecture.md](../../ovos-ma-player/docs/architecture.md). Everything
there applies here. The sections below focus on what differs in the HiveMind transport.

---

## HiveMind vs plain OVOS bus: what changes

| Aspect | ovos-ma-player | hivemind-ma-player |
|---|---|---|
| Transport | `ws://host:8181/core` (plain WebSocket, no auth) | `wss://host:5678` (TLS + key auth) |
| Client class | `MessageBusClient` | `HiveMessageBusClient` |
| Connection call | `bus.run_forever()` in daemon thread | `bus.connect(FakeBus())` in daemon thread |
| Send a message | `bus.emit(Message(...))` | `bus.emit_mycroft(Message(...))` |
| Request/reply | `bus.wait_for_response(...)` returns inner `Message` | `bus.wait_for_response(...)` returns `HiveMessage`; inner `Message` is at `resp.payload` |
| Event handlers | `bus.on(event, handler)` | `bus.on(event, handler)` — same API, but registered on internal `FakeBus` |
| Authentication | none | access key (positional arg) + optional password |
| Multiple instances | no (`multi_instance: false`) | yes (`multi_instance: true`) |
| Connection timeout | 10 seconds | 15 seconds (network round-trips) |

---

## The HiveMessage envelope

When code calls `HiveMindPlayer._emit` (`hivemind_ma_player/__init__.py:241`):

```python
def _emit(self, msg_type: str, data: dict | None = None) -> None:
    msg = self.provider.Message(msg_type, data or {})
    self.provider.bus.emit_mycroft(msg)
```

`emit_mycroft(msg)` wraps the OVOS `Message` object in a `HiveMessage` of type
`HiveMessageType.BUS` (also called `MYCROFT` in older versions). The envelope looks like this
on the wire:

```json
{
  "type": "bus",
  "payload": {
    "type": "ovos.common_play.play",
    "data": {"media": {...}},
    "context": {}
  }
}
```

HiveMind sends this over the TLS WebSocket. The remote HiveMind core receives it, unwraps the
`payload`, and emits it on the remote OVOS messagebus:

```
MA -> HiveMessage(type=BUS, payload=Message("ovos.common_play.play", {...}))
   -> [TLS WebSocket] ->
Remote HiveMind core -> Message("ovos.common_play.play", {...}) on local OVOS bus
                     -> OCP handles it, plays audio
```

### Receiving events

Events emitted by OCP on the remote OVOS bus are tunnelled in reverse:

```
Remote OCP emits Message("ovos.common_play.player.state", {"state": 1})
   -> HiveMind core wraps it in HiveMessage and sends back over TLS
   -> HiveMessageBusClient receives it, unwraps it, emits inner Message on FakeBus
   -> handler registered with bus.on("ovos.common_play.player.state", ...) is called
   -> handler receives a Message (not a HiveMessage), same as on a local bus
```

This is why `_on_player_state` and `_on_media_state` handlers look identical to those in
`ovos-ma-player`. They receive a plain `Message` object.

### wait_for_response and the payload access pattern

`poll()` (`hivemind_ma_player/__init__.py:306`) uses `bus.wait_for_response(...)`. Unlike
event-handler registration, `wait_for_response` returns the `HiveMessage` itself, not the
inner `Message`. Code accesses the inner `Message` through `resp.payload`:

```python
# hivemind_ma_player/__init__.py:322-323
inner = resp.payload if hasattr(resp, "payload") else resp
raw = inner.data if hasattr(inner, "data") else {}
```

The `hasattr` fallback handles the case where a future version of `hivemind-bus-client`
returns the inner message directly, or where the response is already unwrapped. This
defensive pattern avoids a hard `AttributeError` across client versions.

### Why wait_for_response instead of emit + listen

For polling, the plugin must send a request and then wait for a reply. The naive approach —
emit the request, then register a listener for the reply — has a race condition: if OCP is
fast, the reply arrives before the listener is registered and is silently dropped.
`wait_for_response` registers the listener before it sends the request, which removes this
race. `ovos-bus-client` and `hivemind-bus-client` both implement this guarantee.

---

## Threading model

The threading model is identical to `ovos-ma-player`, with one structural difference: the
connection call is `bus.connect(FakeBus())` rather than `bus.run_forever()`.

### FakeBus

`FakeBus` (from `ovos_bus_client.util`) is an in-process publish/subscribe bus with no network
connection. HiveMind uses it as the "local" side of the tunnel:

- When HiveMind receives a tunnelled message from the remote, it emits it on the `FakeBus`.
- Handlers registered with `bus.on()` are registered on this `FakeBus`.
- When the plugin calls `bus.emit_mycroft(msg)`, HiveMind wraps `msg` and sends it to the
  remote. It does not emit on the `FakeBus`.

The `FakeBus` is purely an internal routing mechanism. It is not visible to the plugin code
beyond being passed to `bus.connect()`.

### Connection sequence

```
handle_async_init (asyncio loop)
    |
    ├── create HiveMessageBusClient(key, host, port, password, ssl)
    ├── connect_done = threading.Event()
    ├── connect_error = []
    ├── start daemon thread -> _connect()
    |       ├── bus.connect(FakeBus())
    |       |       ├── TCP connect to host:port
    |       |       ├── TLS handshake
    |       |       ├── HiveMind authentication (key + password)
    |       |       └── enters receive loop
    |       └── sets connect_done on success, or appends to connect_error on failure
    └── await asyncio.to_thread(connect_done.wait, 15)
```

`hivemind_ma_player/__init__.py:368-390`

The code wraps `connect_done.wait(15)` in `asyncio.to_thread` to avoid blocking the event loop
during the 15-second window. If `connect_error` is non-empty, or `connect_done` was not set
within 15 seconds, it raises `ProviderUnavailableError`.

### Event callbacks

Same rules as the local provider: `_on_player_state` and `_on_media_state` run in the HiveMind
receive thread. They only mutate `_attr_*` attributes and call `player.update_state()`, which
is thread-safe (it schedules on the event loop with `call_soon_threadsafe`). Never `await`
inside these handlers.

---

## Optimistic state updates

Identical to `ovos-ma-player`. See
[ovos-ma-player/docs/architecture.md — Optimistic state updates](../../ovos-ma-player/docs/architecture.md#optimistic-state-updates).

---

## OCP / HiveMind message reference

### Messages sent by MA (MA to remote OVOS via HiveMind)

All messages are standard OVOS `Message` objects sent through `bus.emit_mycroft()`:

| Message type | Payload | Triggered by | Source |
|---|---|---|---|
| `ovos.common_play.resume` | `{}` | `HiveMindPlayer.play` | `:247` |
| `ovos.common_play.pause` | `{}` | `HiveMindPlayer.pause` | `:252` |
| `ovos.common_play.stop` | `{}` | `HiveMindPlayer.stop`, `HiveMindPlayer.power(False)` | `:257` |
| `ovos.common_play.set_track_position` | `{"position": int}` (milliseconds) | `HiveMindPlayer.seek` | `:264` |
| `mycroft.volume.set` | `{"percent": float}` (0.0-1.0) | `HiveMindPlayer.volume_set` | `:271` |
| `mycroft.volume.mute` | `{}` | `HiveMindPlayer.volume_mute(True)` | `:278` |
| `mycroft.volume.unmute` | `{}` | `HiveMindPlayer.volume_mute(False)` | `:278` |
| `ovos.common_play.play` | see payload schema | `HiveMindPlayer.play_media`, `HiveMindPlayer.play_announcement` | `:293`, `:303` |
| `ovos.common_play.status` | `{}` | `HiveMindPlayer.poll` | `:312` |

### Messages received by MA (remote OVOS to MA via HiveMind)

| Message type | Payload | Handler | Source |
|---|---|---|---|
| `ovos.common_play.player.state` | `{"state": <PlayerState>}` | `HiveMindPlayerProvider._on_player_state` | `:399` |
| `ovos.common_play.media.state` | `{"state": <OcpMediaState>}` | `HiveMindPlayerProvider._on_media_state` | `:409` |
| `ovos.common_play.status.response` | `{"state": <PlayerState>, "media": {...}}` | `HiveMindPlayer.poll` via `wait_for_response` | `:312` |

All line numbers refer to `hivemind_ma_player/__init__.py`.

For full payload schemas and enum values, see [ocp-protocol.md](ocp-protocol.md).

---

## State machine

### PlayerState

State parsing is identical to `ovos-ma-player`. The module-level `_parse_player_state`
function (`hivemind_ma_player/__init__.py:147`) validates the payload with
`OvosCommonPlayPlayerStateData` from `ovos_pydantic_models.skills.ocp` and maps
`PlayerState.PLAYING` to `PlaybackState.PLAYING`, `PlayerState.PAUSED` to
`PlaybackState.PAUSED`, and everything else to `PlaybackState.IDLE`.

### MediaState and end-of-track detection

`_on_media_state` (`hivemind_ma_player/__init__.py:409`) calls `_parse_media_state_end`
(`hivemind_ma_player/__init__.py:164`), which validates with `OvosCommonPlayMediaStateData` and
returns `True` only for `OcpMediaState.END_OF_MEDIA` and `OcpMediaState.INVALID_MEDIA`. When
this returns `True`, the player resets to IDLE and clears `current_media`.

---

## MediaEntry fields

`_make_ocp_media_entry(url, media)` — `hivemind_ma_player/__init__.py:117`

Identical to `ovos-ma-player`. See
[ovos-ma-player/docs/architecture.md — MediaEntry fields](../../ovos-ma-player/docs/architecture.md#mediaentry-fields)
for the full table.

**Key point specific to HiveMind:** the `uri` field is the MA stream URL, resolved by
`mass.streams.resolve_stream_url`. This URL must be reachable from the **remote OVOS device**,
not just from the MA server. If OVOS is on a different subnet or behind NAT, configure MA's
external/public URL in its network settings so the resolved stream URL is accessible from the
remote device.

**Note on payload structure:** `play_media` and `play_announcement` wrap the `MediaEntry` dict
in an `OvosCommonPlayPlayData` payload through `_make_play_payload`
(`hivemind_ma_player/__init__.py:137`):

```json
{
  "media": { ... },
  "disambiguation": [],
  "playlist": [{ ... }]
}
```

---

## Security model

HiveMind authentication operates at the WebSocket handshake level:

- The access key is the first positional argument to `HiveMessageBusClient`
  (`hivemind_ma_player/__init__.py:365`). It is transmitted in the initial handshake message
  sent right after the WebSocket connection is established.
- The optional password adds a second factor. HiveMind uses it to derive an encryption key for
  the message payload, in addition to TLS.
- TLS (`ssl=True`, the default) encrypts the transport layer. The MA host's OS certificate
  store should trust the TLS certificate on the HiveMind host.

MA stores `access_key` and `password` as `ConfigEntryType.SECURE_STRING`
(`hivemind_ma_player/__init__.py:88`, `:95`). MA encrypts these values at rest with its own key
store. They are never written in plaintext to disk.

---

## ProviderFeature vs PlayerFeature

`SUPPORTED_FEATURES` at module level (`hivemind_ma_player/__init__.py:37`) is empty for the
same reasons as `ovos-ma-player`. See
[ovos-ma-player/docs/architecture.md — ProviderFeature vs PlayerFeature](../../ovos-ma-player/docs/architecture.md#providerfeature-vs-playerfeature).

`HiveMindPlayer._attr_supported_features` declares the same set of `PlayerFeature` flags as
`OVOSPlayer` (`hivemind_ma_player/__init__.py:219-227`):

`PLAY_MEDIA`, `POWER`, `PAUSE`, `VOLUME_SET`, `VOLUME_MUTE`, `SEEK`, `PLAY_ANNOUNCEMENT`

---
[Home](../README.md) · [Plugin Authors Guide →](plugin-authors.md)

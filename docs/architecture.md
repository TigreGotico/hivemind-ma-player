# Architecture — hivemind-ma-player

## Class diagram

```
music_assistant.models.player_provider.PlayerProvider
    └── HiveMindPlayerProvider              (hivemind_ma_player/__init__.py:270)
            │  owns bus: HiveMessageBusClient
            │  owns players: list[Player]
            └── registers
                    HiveMindPlayer          (hivemind_ma_player/__init__.py:118)
                        extends music_assistant.models.player.Player
                        back-ref provider → HiveMindPlayerProvider
```

`HiveMindPlayerProvider` holds the single `HiveMessageBusClient` instance and exposes it (and
`Message`) as attributes that `HiveMindPlayer` uses via `self.provider.bus`.

---

## HiveMind vs plain OVOS bus: what changes

| Aspect | ovos-ma-player | hivemind-ma-player |
|---|---|---|
| Transport | `ws://host:8181/core` (plain WebSocket, no auth) | `wss://host:5678` (TLS + key auth) |
| Client class | `MessageBusClient` | `HiveMessageBusClient` |
| Connection call | `bus.run_forever()` in daemon thread | `bus.connect(FakeBus())` in daemon thread |
| Send a message | `bus.emit(Message(...))` | `bus.emit_mycroft(Message(...))` |
| Request/reply | `bus.wait_for_response(msg, reply_type=..., timeout=N)` — returns inner `Message` | `bus.wait_for_response(msg, reply_type=..., timeout=N)` — returns `HiveMessage`; inner `Message` is at `resp.payload` |
| Event handlers | `bus.on(event, handler)` | `bus.on(event, handler)` (same API, but registers on the internal FakeBus) |
| Authentication | none | access key (positional arg to `HiveMessageBusClient`) + optional password |
| Multiple instances | no (`multi_instance: false`) | yes (`multi_instance: true`) |

---

## Threading model

The threading model is identical to `ovos-ma-player` with one difference: the connection
call is `bus.connect(FakeBus())` rather than `bus.run_forever()`.

`FakeBus` (from `ovos_bus_client.util`) is an in-process pub/sub bus that HiveMind uses as the
"local" side of the tunnel. HiveMind connects to the remote node, and when it receives a
tunnelled OVOS message it emits it on the `FakeBus`. Handlers registered with `bus.on()` are
actually registered on this `FakeBus`.

**Connection sequence:**

```
handle_async_init (asyncio loop)
    │
    ├── create HiveMessageBusClient(key, host, port, password, ssl)
    ├── connect_done = threading.Event()
    ├── start daemon thread → bus.connect(FakeBus())
    │       ├── performs TLS handshake
    │       ├── sends authentication message (key + password)
    │       └── sets connect_done on success
    └── await asyncio.to_thread(connect_done.wait, 15)   ← blocks event loop thread only
```

Timeout is 15 seconds (vs 10 s for the local provider) to allow for network round-trips.

**Why `asyncio.to_thread` on `connect_done.wait`**

`connect_done.wait(15)` is a blocking call. Wrapping it in `asyncio.to_thread` offloads the
block to the thread pool, keeping the asyncio event loop free to handle other tasks during the
15-second window.

**Event callbacks**

Same rules as the local provider: `_on_player_state` and `_on_media_state` run in the
HiveMind receive thread. They only mutate `_attr_*` and call `player.update_state()`, which
is thread-safe.

---

## OCP / HiveMind message reference

### Messages sent by MA (MA → remote OVOS via HiveMind)

All messages are sent as standard OVOS `Message` objects, wrapped by `bus.emit_mycroft()`:

| Message type | Payload | Triggered by |
|---|---|---|
| `ovos.common_play.resume` | — | `HiveMindPlayer.play` |
| `ovos.common_play.pause` | — | `HiveMindPlayer.pause` |
| `ovos.common_play.stop` | — | `HiveMindPlayer.stop`, `HiveMindPlayer.power(False)` |
| `ovos.common_play.set_track_position` | `{"position": float}` (seconds) | `HiveMindPlayer.seek` |
| `mycroft.volume.set` | `{"percent": float}` (0.0–1.0) | `HiveMindPlayer.volume_set` |
| `mycroft.volume.mute` | — | `HiveMindPlayer.volume_mute(True)` |
| `mycroft.volume.unmute` | — | `HiveMindPlayer.volume_mute(False)` |
| `ovos.common_play.play` | `{"media": MediaEntry dict}` | `HiveMindPlayer.play_media`, `HiveMindPlayer.play_announcement` |
| `ovos.common_play.status` | — | `HiveMindPlayer.poll` |

### Messages received by MA (remote OVOS → MA via HiveMind)

| Message type | Payload | Handler |
|---|---|---|
| `ovos.common_play.player.state` | `{"state": int}` | `HiveMindPlayerProvider._on_player_state` |
| `ovos.common_play.media.state` | `{"state": int}` | `HiveMindPlayerProvider._on_media_state` |
| `ovos.common_play.status.response` | `{"state": int, "media": {"position": float, ...}}` | `HiveMindPlayer.poll` — accessed via `resp.payload.data` |

### HiveMessage envelope

`bus.emit_mycroft(Message(...))` wraps the inner `Message` in a `HiveMessage` of type
`MYCROFT`. HiveMind transmits this over the TLS WebSocket. The remote node receives the
`HiveMessage`, unwraps it, and emits the inner `Message` on the remote OVOS bus.

For `wait_for_response`, the return value is a `HiveMessage`. The inner OVOS `Message` is
accessed via `resp.payload`:

```python
# hivemind_ma_player/__init__.py:246
data = resp.payload.data if hasattr(resp, "payload") else resp.data
```

The fallback `resp.data` handles the case where the bus client returns the inner message
directly (implementation detail that may vary across versions).

---

## State machine

### MA PlaybackState ↔ OCP PlayerState

| OCP value | OCP name | MA PlaybackState |
|---|---|---|
| `0` | STOPPED | `PlaybackState.IDLE` |
| `1` | PLAYING | `PlaybackState.PLAYING` |
| `2` | PAUSED | `PlaybackState.PAUSED` |

### OCP MediaState → MA PlaybackState

`ovos.common_play.media.state` with state `6` (END) or `7` (ERROR) sets
`PlaybackState.IDLE` and clears `current_media`.

`_on_media_state` — `hivemind_ma_player/__init__.py:343`

### Optimistic state updates

Same pattern as the local provider: command methods update `_attr_*` and call `update_state()`
immediately after emitting, without waiting for the remote to confirm. Push events and polling
correct any discrepancy within 5 seconds.

---

## MediaEntry fields

`HiveMindPlayer._make_media_entry` — `hivemind_ma_player/__init__.py:155`

Identical to the local provider. See [ovos-ma-player architecture](../../ovos-ma-player/docs/architecture.md)
for field descriptions. The key point specific to HiveMind: the `uri` field is the MA stream
URL, which must be reachable from the **remote OVOS device**, not just from the MA server.

---

## Security model

HiveMind authentication happens at the WebSocket level:
- The access key is passed as the first positional argument to `HiveMessageBusClient` and is
  transmitted in the initial handshake message.
- The optional password adds a second factor; it is used to derive an encryption key for the
  payload.
- TLS (`ssl=True`, default) encrypts the transport layer.

MA stores the access key and password in its encrypted config store (`ConfigEntryType.SECURE_STRING`).

# Plugin Authors Guide — hivemind-ma-player

This document is for developers who want to fork, extend, or use this plugin as a template for
a Music Assistant PlayerProvider that talks to a remote OVOS device via HiveMind.

---

## Relationship to ovos-ma-player

`hivemind-ma-player` is structurally identical to `ovos-ma-player`. The differences are:

1. Transport: `HiveMessageBusClient` instead of `MessageBusClient`.
2. Send: `bus.emit_mycroft(msg)` instead of `bus.emit(msg)`.
3. Receive: push events arrive via the internal `FakeBus`; `wait_for_response` returns a
   `HiveMessage` with the inner `Message` at `resp.payload`.
4. Connection: `bus.connect(FakeBus())` instead of `bus.run_forever()`.
5. Config: five fields instead of two (`host`, `port`, `access_key`, `password`, `ssl`,
   `player_name`).
6. Manifest: `multi_instance: true`.

If you understand `ovos-ma-player`, you understand this plugin. Read
[ovos-ma-player/docs/plugin-authors.md](../../ovos-ma-player/docs/plugin-authors.md) first —
everything there applies here.

---

## How the MA plugin entrypoint system works

Same as `ovos-ma-player`. The entry point key is `hivemind_player`:

```toml
# pyproject.toml
[project.entry-points."music_assistant.provider"]
hivemind_player = "hivemind_ma_player"
```

MA reads `manifest.json` alongside the Python package. The critical field that differs from
`ovos-ma-player` is:

```json
"multi_instance": true
```

This allows MA to add multiple instances of this provider — one per remote OVOS device.

---

## How to add new PlayerFeature flags

Identical to `ovos-ma-player`. Add the flag to `HiveMindPlayer._attr_supported_features` and
implement the async method. Use `self._emit(msg_type, data)` to send the OCP message:

```python
# hivemind_ma_player/__init__.py:150
def _emit(self, msg_type: str, data: dict | None = None) -> None:
    msg = self.provider.Message(msg_type, data or {})
    self.provider.bus.emit_mycroft(msg)
```

Example — adding next/previous track:

```python
PlayerFeature.NEXT_PREVIOUS_TRACK,  # in _attr_supported_features

async def next_track(self) -> None:
    await asyncio.to_thread(self._emit, "ovos.common_play.next")

async def previous_track(self) -> None:
    await asyncio.to_thread(self._emit, "ovos.common_play.prev")
```

---

## How to add new bus message handlers

Subscribe in `handle_async_init` after `connect_done` is set:

```python
# hivemind_ma_player/__init__.py:326
self.bus.on("ovos.common_play.player.state", self._on_player_state)
self.bus.on("ovos.common_play.media.state", self._on_media_state)
```

`bus.on()` registers on the internal `FakeBus`. Events that the remote OVOS node emits are
tunnelled through HiveMind and re-emitted on the `FakeBus`, so handlers receive them exactly
as if they were on a local bus.

Handler rules are the same as `ovos-ma-player`: no `await` in handlers; use
`asyncio.run_coroutine_threadsafe(coro, self.mass.loop)` if you need to schedule async work.

---

## Testing tips

### Mock the HiveMind bus

`hivemind-bus-client` does not ship a `FakeBus` of its own, but `HiveMessageBusClient` wraps
a `FakeBus` internally. For unit tests you can mock the entire `HiveMessageBusClient`:

```python
from unittest.mock import MagicMock, patch

mock_bus = MagicMock()
mock_bus.connect.return_value = None

with patch("hivemind_ma_player.HiveMessageBusClient", return_value=mock_bus):
    # instantiate and call handle_async_init
    ...

# Assert the right message was sent
mock_bus.emit_mycroft.assert_called_once()
call_args = mock_bus.emit_mycroft.call_args[0][0]
assert call_args.msg_type == "ovos.common_play.pause"
```

### Fake poll responses

`wait_for_response` on `HiveMessageBusClient` returns a `HiveMessage`. Fake it:

```python
from unittest.mock import MagicMock

fake_inner = MagicMock()
fake_inner.data = {"state": 1, "media": {"position": 30.0}}

fake_hive_msg = MagicMock()
fake_hive_msg.payload = fake_inner

provider.bus.wait_for_response = MagicMock(return_value=fake_hive_msg)

await player.poll()
assert player._attr_playback_state == PlaybackState.PLAYING
assert player._attr_elapsed_time == 30
```

### Integration test against a real HiveMind node

1. Run OVOS + HiveMind on a test device (or VM).
2. Generate a key: `hivemind-core add-client --name test`.
3. Install the plugin in a MA dev instance and add a provider instance with the key.
4. Use `hivemind-client terminal` to observe traffic through the tunnel.
5. Use `ovos-bus-client monitor` on the remote to confirm OCP messages arrive and are handled.

### Testing multi-instance behaviour

Add two provider instances in MA pointing at two different HiveMind nodes (or two different
keys on the same node). Verify each player appears independently in the MA player list and
that commands to one do not affect the other.

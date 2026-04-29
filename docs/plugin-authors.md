# Plugin Authors Guide — hivemind-ma-player

This document is for developers who want to fork, extend, or use this plugin as a template for
a Music Assistant PlayerProvider that talks to a remote OVOS device via HiveMind.

**Read [ovos-ma-player/docs/plugin-authors.md](../../ovos-ma-player/docs/plugin-authors.md)
first.** Everything there applies here. This document only covers the differences introduced by
the HiveMind transport.

---

## Relationship to ovos-ma-player

`hivemind-ma-player` is structurally identical to `ovos-ma-player`. The differences are:

| Aspect | ovos-ma-player | hivemind-ma-player |
|---|---|---|
| Transport | `MessageBusClient`, plain WebSocket | `HiveMessageBusClient`, TLS WebSocket |
| Send method | `bus.emit(msg)` | `bus.emit_mycroft(msg)` |
| Receive (poll) | `wait_for_response` returns `Message` | `wait_for_response` returns `HiveMessage`; `.payload` is the inner `Message` |
| Connection | `bus.run_forever()` | `bus.connect(FakeBus())` |
| Config entries | 2 fields (`host`, `port`) | 6 fields (`host`, `port`, `access_key`, `password`, `ssl`, `player_name`) |
| Manifest | `multi_instance: false` | `multi_instance: true` |
| Entry-point key | `ovos_player` | `hivemind_player` |

If you understand `ovos-ma-player`, the only new concept is the `_emit` helper and the
`resp.payload.data` access pattern in `poll()`.

---

## How the MA plugin entrypoint system works

Same as `ovos-ma-player`. The entry-point key is `hivemind_player`:

```toml
# pyproject.toml
[project.entry-points."music_assistant.provider"]
hivemind_player = "hivemind_ma_player"
```

The critical manifest field that differs:

```json
"multi_instance": true
```

This allows MA to add multiple instances of this provider — one per remote OVOS device. Each
instance gets a unique `instance_id` from MA. Use it to namespace player IDs:

```python
# hivemind_ma_player/__init__.py:418
player_id = f"{self.instance_id}:hivemind"
```

Without the `instance_id` prefix, player IDs would collide across instances.

---

## The _emit helper

`HiveMindPlayer._emit` (`hivemind_ma_player/__init__.py:241`) is a convenience wrapper around
`bus.emit_mycroft()`:

```python
def _emit(self, msg_type: str, data: dict | None = None) -> None:
    msg = self.provider.Message(msg_type, data or {})
    self.provider.bus.emit_mycroft(msg)
```

All playback command methods call `_emit` via `asyncio.to_thread`:

```python
async def pause(self) -> None:
    await asyncio.to_thread(self._emit, "ovos.common_play.pause")
    self._attr_playback_state = PlaybackState.PAUSED
    self.update_state()
```

In `ovos-ma-player`, the equivalent line calls `bus.emit(Message(...))` directly. The pattern
is otherwise identical. When forking this plugin, replace `_emit` calls with whatever your
transport requires.

---

## How to add new PlayerFeature flags

Add to `HiveMindPlayer._attr_supported_features` and implement the async method using
`self._emit`:

```python
# In HiveMindPlayer.__init__:
PlayerFeature.NEXT_PREVIOUS_TRACK,

# New methods:
async def next_track(self) -> None:
    await asyncio.to_thread(self._emit, "ovos.common_play.next")

async def previous_track(self) -> None:
    await asyncio.to_thread(self._emit, "ovos.common_play.prev")
```

Verify that the remote OCP version handles `ovos.common_play.next` and
`ovos.common_play.prev`. Use `hivemind-client terminal` to observe whether the messages arrive
on the remote bus and trigger the expected OCP behaviour.

---

## How to add new bus message handlers

Subscribe in `handle_async_init` after `connect_done` is set:

```python
# After await asyncio.to_thread(connect_done.wait, 15):
self.bus.on("ovos.some.new.event", self._on_some_event)
```

`bus.on()` registers on the internal `FakeBus`. Events tunnelled from the remote OVOS node
are re-emitted on the `FakeBus`, so handlers receive a plain `Message` (not a `HiveMessage`),
identical to how they arrive in `ovos-ma-player`.

Handler rules are the same: no `await` in handlers; use
`asyncio.run_coroutine_threadsafe(coro, self.mass.loop)` if you need async work from a handler.

---

## Testing tips

### Mocking the HiveMind bus

`hivemind-bus-client` does not ship a standalone fake bus. Mock the entire client:

```python
from unittest.mock import MagicMock, AsyncMock, patch, call
import pytest


def make_provider_and_player():
    from hivemind_ma_player import HiveMindPlayerProvider, HiveMindPlayer
    from ovos_bus_client import Message

    provider = MagicMock(spec=HiveMindPlayerProvider)
    provider.bus = MagicMock()
    provider.Message = Message
    provider.mass = MagicMock()
    provider.mass.streams.resolve_stream_url = AsyncMock(return_value="http://stream/test.mp3")

    player = HiveMindPlayer(provider, "test:hivemind", name="Test Player")
    return player, provider


@pytest.mark.asyncio
async def test_pause_calls_emit_mycroft():
    player, provider = make_provider_and_player()
    await player.pause()
    provider.bus.emit_mycroft.assert_called_once()
    msg = provider.bus.emit_mycroft.call_args[0][0]
    assert msg.msg_type == "ovos.common_play.pause"
```

### Asserting _emit is used (not bus.emit)

The key difference from `ovos-ma-player` tests is that you assert `emit_mycroft` was called,
not `emit`:

```python
# CORRECT for hivemind-ma-player:
provider.bus.emit_mycroft.assert_called_once()

# WRONG — this would be the ovos-ma-player assertion:
provider.bus.emit.assert_called_once()
```

### Faking poll responses

`wait_for_response` on `HiveMessageBusClient` returns a `HiveMessage`. The plugin accesses
`resp.payload.data`. Fake it:

```python
@pytest.mark.asyncio
async def test_poll_updates_state_via_hivemessage():
    from music_assistant_models.enums import PlaybackState
    player, provider = make_provider_and_player()

    fake_inner = MagicMock()
    # state and media match what OCP returns; position is in milliseconds
    fake_inner.data = {"state": "playing", "media": {"position": 30000}}

    fake_hive_msg = MagicMock()
    fake_hive_msg.payload = fake_inner

    provider.bus.wait_for_response = MagicMock(return_value=fake_hive_msg)

    await player.poll()

    assert player._attr_playback_state == PlaybackState.PLAYING
    assert player._attr_elapsed_time == 30   # plugin converts ms -> s (30000 // 1000)
```

### Testing the payload fallback

The plugin handles both `HiveMessage` (`.payload`) and plain `Message` (`.data`) in `poll()`
(`hivemind_ma_player/__init__.py:322`). Test both paths:

```python
@pytest.mark.asyncio
async def test_poll_fallback_to_resp_data():
    from ovos_bus_client import Message
    player, provider = make_provider_and_player()

    # Simulate a version that returns inner Message directly
    fake_response = Message(
        "ovos.common_play.status.response",
        {"state": "paused", "media": {"position": 10000}}   # position in ms
    )
    provider.bus.wait_for_response = MagicMock(return_value=fake_response)

    await player.poll()

    from music_assistant_models.enums import PlaybackState
    assert player._attr_playback_state == PlaybackState.PAUSED
    assert player._attr_elapsed_time == 10   # 10000 ms // 1000 = 10 s
```

### Integration test against a real HiveMind node

1. Run OVOS + HiveMind on a test device or VM.
2. Generate a key: `hivemind-core add-client --name test`.
3. Install the plugin in a MA dev instance and add a provider instance with the key.
4. Use `hivemind-client terminal` to observe traffic through the tunnel.
5. Use `ovos-bus-client monitor` on the remote to confirm OCP messages arrive and are handled.
6. Check that state events (`ovos.common_play.player.state`) flow back through the tunnel and
   update the MA player state.

### Testing multi-instance behaviour

Add two provider instances in MA pointing at two different HiveMind nodes (or two different
keys on the same node). Each generates a distinct `instance_id`. Verify:

- Each player appears independently in the MA player list with its configured `player_name`.
- Commands sent to one player (e.g. pause) do not affect the other.
- Both players receive state updates independently when their respective OCP instances emit
  events.

To test this without two physical devices, run two separate HiveMind core instances on different
ports on the same machine, each with a different OVOS bus connection.

---

## Packaging and publishing

Identical to `ovos-ma-player`. See
[ovos-ma-player/docs/plugin-authors.md — Packaging and publishing](../../ovos-ma-player/docs/plugin-authors.md#packaging-and-publishing-to-pypi).

Key differences for this package:

```toml
[project]
name = "my-hivemind-ma-player"
dependencies = ["music-assistant-plugin-manager", "ovos-bus-client", "hivemind-bus-client"]

[project.entry-points."music_assistant.provider"]
my_hivemind_player = "my_hivemind_ma_player"
```

```json
{
  "multi_instance": true,
  "requirements": ["ovos-bus-client", "hivemind-bus-client"]
}
```

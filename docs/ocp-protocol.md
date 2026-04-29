# OCP Protocol Reference

The OCP protocol used by this plugin is identical to the one used by `ovos-ma-player`. The
messages, payload schemas, enum values, and gotchas are the same. The only difference is the
transport: every message is wrapped in a `HiveMessage` envelope before being sent.

**Read the full OCP protocol reference in ovos-ma-player:**
[ovos-ma-player/docs/ocp-protocol.md](../../ovos-ma-player/docs/ocp-protocol.md)

---

## HiveMind transport differences

### Sending messages

In `ovos-ma-player`:
```python
bus.emit(Message("ovos.common_play.play", {"media": entry.as_dict}))
```

In `hivemind-ma-player` (via `HiveMindPlayer._emit`):
```python
bus.emit_mycroft(Message("ovos.common_play.play", {"media": entry.as_dict}))
```

`emit_mycroft` wraps the `Message` in a `HiveMessage(type=BUS, payload=msg)` and sends it over
the TLS WebSocket. The remote HiveMind core unwraps it and emits the inner `Message` on the
remote OVOS bus. OCP on the remote host receives it and acts on it identically to the local
case.

### Receiving messages

Event handlers (`_on_player_state`, `_on_media_state`) receive a plain `Message` object,
not a `HiveMessage`. HiveMind unwraps incoming events before routing them to handlers registered
via `bus.on()`. From the handler's perspective, there is no difference from the local bus.

### poll() response unwrapping

`bus.wait_for_response()` in `hivemind-bus-client` returns a `HiveMessage`. The inner `Message`
is at `resp.payload`. The plugin accesses it defensively:

```python
# hivemind_ma_player/__init__.py:246
data = resp.payload.data if hasattr(resp, "payload") else resp.data
```

This is the only place in the codebase where the HiveMind transport requires special handling
compared to `ovos-ma-player`. All other protocol behaviour is identical.

### Poll timeout

The poll timeout is `3.0` seconds in this plugin (`hivemind_ma_player/__init__.py:240`) vs
`2.0` seconds in `ovos-ma-player`. The extra second accounts for network round-trip time to
the remote device.

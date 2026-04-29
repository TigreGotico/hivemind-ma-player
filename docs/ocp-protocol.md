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
# via OVOSPlayer._emit (ovos_ma_player/__init__.py:206)
self.provider.bus.emit(self.provider.Message(msg_type, data or {}))
```

In `hivemind-ma-player` (via `HiveMindPlayer._emit`, `hivemind_ma_player/__init__.py:241`):

```python
msg = self.provider.Message(msg_type, data or {})
self.provider.bus.emit_mycroft(msg)
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

`bus.wait_for_response()` in `hivemind-bus-client` returns a `HiveMessage`. The plugin
accesses the inner data defensively (`hivemind_ma_player/__init__.py:322-323`):

```python
inner = resp.payload if hasattr(resp, "payload") else resp
raw = inner.data if hasattr(inner, "data") else {}
```

This handles both `HiveMessage` (`.payload` is the inner `Message`) and the case where the
client returns the inner message directly (implementation detail that may vary across client
versions). The extracted `raw` dict is then passed to `_parse_status_response` (`:324`),
identical to the `ovos-ma-player` path.

### Poll timeout

The poll timeout is `3.0` seconds in this plugin (`hivemind_ma_player/__init__.py:315`) vs
`2.0` seconds in `ovos-ma-player`. The extra second accounts for network round-trip time to
the remote device.

### Message permissions on HiveMind

HiveMind requires explicit permission grants for each message type a client may send or
receive. The `hivemind-core allow-msg` command grants a message type to a specific client
(identified by Node ID). If a message type is not allowed, HiveMind silently drops it.

See [docs/deployment.md](deployment.md) for the full list of `allow-msg` commands needed for
this plugin to function.

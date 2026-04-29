# hivemind-ma-player

Music Assistant PlayerProvider that drives a remote OVOS / OCP instance via a HiveMind
encrypted websocket connection.

## Overview

HiveMind is a secure overlay on the OVOS messagebus that wraps each Mycroft/OVOS bus message
in an authenticated envelope and transmits it over a `wss://` connection. This package bridges
Music Assistant (the media server) with one or more remote OVOS devices running HiveMind.

Each provider instance in MA maps to exactly one remote OVOS device. Because `manifest.json`
sets `multi_instance: true`, you can add as many instances as you have remote devices.

## Relationship to ovos-ma-player

Both providers implement the same OCP protocol — the same set of bus messages, the same state
machine, the same `MediaEntry` serialization. The difference is entirely in the transport layer:

| | ovos-ma-player | hivemind-ma-player |
|---|---|---|
| Transport | Plain WebSocket `ws://host:8181` | Encrypted WebSocket `wss://host:5678` |
| Authentication | None | Access key + optional password |
| Multiple OVOS devices | Not supported (single instance) | Supported (`multi_instance: true`) |
| Use case | OVOS on the same machine as MA | OVOS on a remote device |
| Dependencies | `ovos-bus-client` | `ovos-bus-client` + `hivemind-bus-client` |
| Connection call | `bus.run_forever()` in daemon thread | `bus.connect(FakeBus())` in daemon thread |
| Sending a message | `bus.emit(Message(...))` | `bus.emit_mycroft(Message(...))` |
| Poll response type | OVOS `Message` | `HiveMessage` with inner `Message` at `.payload` |
| Connection timeout | 10 seconds | 15 seconds |

If you are reading the source of one, you understand the other. The only non-trivial difference
is how `poll()` unpacks the response:

```python
# hivemind_ma_player/__init__.py:246
data = resp.payload.data if hasattr(resp, "payload") else resp.data
```

This handles both `HiveMessage` (`.payload` is the inner `Message`) and the case where the
client returns the inner message directly.

## Key Classes

| Class | Purpose | Source |
|---|---|---|
| `HiveMindPlayerProvider` | MA `PlayerProvider` — manages the HiveMind bus connection and player registration | `hivemind_ma_player/__init__.py:270` |
| `HiveMindPlayer` | MA `Player` — translates MA commands to OCP messages via HiveMind | `hivemind_ma_player/__init__.py:118` |

## Contents

- [Installation, Quick Start, Configuration & Troubleshooting](../README.md)
- [Architecture, Threading Model & Message Reference](architecture.md)
- [Plugin Authors Guide — extending and testing](plugin-authors.md)
- [OCP Protocol Reference](ocp-protocol.md)
- [Deployment Guide](deployment.md)

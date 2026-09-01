# hivemind-ma-player

Music Assistant PlayerProvider that drives a **remote** OpenVoiceOS (OVOS) instance through a
[HiveMind](https://github.com/JarbasHiveMind/HiveMind-core) encrypted websocket connection.

HiveMind wraps each OVOS/Mycroft message in an authenticated envelope and sends it over a
`wss://` connection. This plugin uses the same OCP bus messages as
[ovos-ma-player](https://github.com/TigreGotico/ovos-ma-player) — the only difference is the
transport layer. Because HiveMind is multi-client, one Music Assistant server can control
multiple remote OVOS devices at the same time, each as its own provider instance.

**Typical use case:** Music Assistant runs on a home server. OVOS devices are placed around the
house (Raspberry Pis, desktops, and so on), each running HiveMind. Add one `hivemind-ma-player`
instance per device in the MA UI.

---

## Background: What is Music Assistant?

> If you already know what Music Assistant is, skip this section.

[Music Assistant](https://music-assistant.io) (MA) is a self-hosted media server. It gathers
music from many sources — Spotify, YouTube Music, local files, and more — and streams it to
players around your home. It runs as a server process (standalone or inside a Home Assistant
add-on) and exposes a web UI plus a WebSocket API. "Player providers" are plugins that teach MA
how to send audio to a specific type of playback device. This package is one such plugin.

## Background: What is OVOS and OCP?

> If you already know OVOS and OCP, skip this section.

[OpenVoiceOS](https://openvoiceos.org) (OVOS) is an open-source voice assistant platform, a
community fork and evolution of the original Mycroft AI assistant. It runs on Linux (commonly on
a Raspberry Pi) and listens for a wake word, then processes spoken commands through a pipeline of
skills. All internal communication happens over a local WebSocket called the **messagebus**
(`ws://localhost:8181/core`), where every event is a JSON message with a `type` and a `data` dict.

**OCP** (OpenVoiceOS Common Play) is the audio subsystem inside OVOS. It is implemented as a
skill/plugin and manages everything audio-related: queuing tracks, controlling the audio backend
(VLC, mpd, and so on), and reporting playback state on the bus. This plugin speaks the OCP
protocol, so from OVOS's perspective Music Assistant looks like another OCP skill.

## Background: What is HiveMind?

> If you already know HiveMind, skip this section.

[HiveMind](https://github.com/JarbasHiveMind/HiveMind-core) is an encrypted overlay network for
the OVOS messagebus. The problem it solves: the OVOS messagebus (`ws://host:8181/core`) has no
authentication. Any process that can reach port 8181 can send any command to OVOS. This is fine
for a single-machine setup but risky across a network.

HiveMind adds a **satellite/listener model** on top of the raw messagebus:

- The **HiveMind core** (server) runs on the same machine as OVOS. It connects to the local
  messagebus and exposes a new WebSocket port (default 5678) to the outside world. Every
  connection to this port must present a valid **access key** in the initial handshake.
- A **HiveMind client** (satellite) connects to the core using `wss://host:5678` with its
  access key. Once authenticated, it can send OVOS bus messages and receive events, all
  tunnelled through the encrypted connection.

The payload inside the tunnel is a `HiveMessage` envelope of type `MYCROFT`, which wraps a
standard OVOS `Message` object. The remote OVOS instance never sees the raw network connection.
It only sees messages arriving on its local bus, forwarded by the HiveMind core.

**Why use HiveMind instead of just opening port 8181?**
- Authentication: unauthenticated clients cannot connect.
- Encryption: TLS protects the message payload in transit.
- Multi-device: each device gets its own access key, and keys can be revoked individually.
- Firewall-friendly: you expose one port (5678) instead of the raw messagebus port.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | Required by both MA and this plugin |
| Music Assistant | The server that this plugin extends |
| OVOS with OCP | Must run on each remote device |
| HiveMind core | Must be installed and running on each remote OVOS device |
| Access key | Generated on the remote device with `hivemind-core add-client` |

---

## Install

### From PyPI (standard)

```bash
pip install hivemind-ma-player
```

Music Assistant finds the plugin automatically through the `music_assistant.provider`
entry-point group (key `hivemind_player`, declared in `pyproject.toml`). After installation,
restart MA. The provider appears in the UI under **Settings > Players > Add Provider >
HiveMind (remote OVOS)**. Because `manifest.json` sets `multi_instance: true`, you can add one
instance per remote OVOS device.

### Inside a Python virtual environment

```bash
source /path/to/ma-venv/bin/activate
pip install hivemind-ma-player
# then restart MA
```

### Home Assistant add-on

Add this line to the **Extra pip packages** field in the MA add-on configuration:

```
hivemind-ma-player
```

Save and restart the add-on. The remote OVOS devices must be reachable from the HA host on the
HiveMind port (default 5678).

### Docker

Extend the MA Docker image:

```dockerfile
FROM ghcr.io/music-assistant/server:latest
RUN pip install hivemind-ma-player
```

Rebuild and restart. Make sure the HiveMind port on each remote device is reachable from the
Docker container (use the host LAN IP or a Docker network that has access to the LAN).

---

## Setting up HiveMind on the remote device

Before you configure the MA plugin, HiveMind must run on each OVOS device you want to control.

### 1. Install HiveMind core

On the remote OVOS device, run:

```bash
pip install hivemind-core
```

### 2. Start HiveMind

```bash
hivemind-core listen --port 5678
```

For a production setup, run this as a systemd service. See
[docs/deployment.md](docs/deployment.md) for a complete service unit.

### 3. Generate an access key for MA

```bash
hivemind-core add-client --name "music-assistant"
```

This prints something like:

```
Access Key: abc123...
Password:   (none set)
```

Copy the access key. If you want an added password layer, run:

```bash
hivemind-core add-client --name "music-assistant" --password "mysecret"
```

### 4. Allow this provider's messages

HiveMind denies every message type by default, so the new client cannot do anything until you
allow the exact messages this provider sends. Use the Node ID printed by `add-client`:

```bash
hivemind-core allow-msg "ovos.common_play.play" <Node ID>
hivemind-core allow-msg "ovos.common_play.pause" <Node ID>
hivemind-core allow-msg "ovos.common_play.resume" <Node ID>
hivemind-core allow-msg "ovos.common_play.stop" <Node ID>
hivemind-core allow-msg "ovos.common_play.status" <Node ID>
hivemind-core allow-msg "ovos.common_play.set_track_position" <Node ID>
hivemind-core allow-msg "ovos.common_play.get_track_position" <Node ID>
hivemind-core allow-msg "mycroft.volume.set" <Node ID>
hivemind-core allow-msg "mycroft.volume.mute" <Node ID>
hivemind-core allow-msg "mycroft.volume.unmute" <Node ID>
```

Skip the last three if you do not need volume control from MA. Without this step the client
authenticates but every command is silently dropped by HiveMind's ACL.

### 5. Verify the key

```bash
hivemind-core list-clients
```

### 6. Revoke access if needed

```bash
hivemind-core delete-client --name "music-assistant"
```

---

## Quick Start

Follow these steps, starting with MA already running.

1. Set up HiveMind on the remote device (see the section above).

2. Check connectivity from the MA host.

   ```bash
   # Basic TCP check (replace REMOTE_HOST and PORT)
   nc -zv REMOTE_HOST 5678
   ```

   You should see `Connection to REMOTE_HOST 5678 port [tcp/*] succeeded!`. If you see
   "connection refused", HiveMind is not running or the port is firewalled.

3. Install the plugin.

   ```bash
   pip install hivemind-ma-player   # inside MA's Python environment
   ```

4. Restart Music Assistant.

5. Add the provider in the MA UI.
   - Go to **Settings > Players**.
   - Click **Add Provider**.
   - Select **HiveMind (remote OVOS)**.
   - Fill in:
     - **Host**: IP or hostname of the remote device (for example `192.168.1.42` or
       `myrpi.local`)
     - **Port**: `5678` (or the port you used when you started HiveMind)
     - **Access key**: the key from `hivemind-core add-client`
     - **Password**: leave blank unless you set one
     - **SSL**: leave enabled (default `true`)
     - **Player name**: a friendly name shown in the MA UI (for example `Living Room`)
   - Click **Save**.

6. Play something.
   - Browse to any track in MA and press play.
   - Select your new HiveMind player as the target.
   - Audio should start on the remote OVOS device within a few seconds.

---

## Server-side: hivemind-media-player

This plugin (hivemind-ma-player) is the **client**. It lives inside Music Assistant and sends
OCP commands over the HiveMind tunnel. The **remote device** must run a separate server-side
stack before MA can connect to it.

Source: [https://github.com/JarbasHiveMind/hivemind-media-player](https://github.com/JarbasHiveMind/hivemind-media-player)

### What runs on the remote device

Three components must be present and running on the device that plays audio:

| Component | Role |
|---|---|
| `hivemind-core` | Authenticates clients, encrypts the tunnel, routes HiveMessages |
| `hivemind-player-agent-plugin` | `AgentProtocol` implementation that receives OCP bus messages and passes them to ovos-audio through an internal `FakeBus` |
| `ovos-audio` with OCP enabled | Playback engine that drives mpv, VLC, or other backends |

MA connects to `hivemind-core` over `wss://`. HiveMind unwraps each incoming `HiveMessage` and
emits the inner OVOS `Message` on the internal `FakeBus` shared with `ovos-audio`. OCP handles
the message and starts, stops, or seeks audio on the hardware.

> **Note:** The remote device does not need a full OVOS installation. `hivemind-media-player`
> is designed for dedicated audio devices (such as a Raspberry Pi used as a speaker) that do
> not already run OVOS.

### Installation on the remote device

```bash
pip install hivemind-core hivemind-player-agent-plugin ovos-audio ovos-plugin-manager
```

Generate credentials for MA:

```bash
hivemind-core add-client --name "music-assistant"
```

Output:

```
Node ID: 3
Friendly Name: HiveMind-Node-2
Access Key: 57e488946808014168d9237c11e68959
Password: a60319726ca815102f7c5ff88527ec37
```

Note the Access Key and Password. Enter them in the MA provider config.

### Agent plugin configuration

Edit `~/.config/hivemind-core/server.json` on the remote device:

```json
{
    "agent_protocol": {
        "module": "hivemind-player-agent-plugin",
        "hivemind-player-agent-plugin": {}
    }
}
```

This tells `hivemind-core` to load `HiveMindPlayerProtocol` as its agent. `HiveMindPlayerProtocol`
starts `ovos-audio`'s `PlaybackService` on an internal `FakeBus` and forwards incoming messages
to it. `hivemind_player_protocol/__init__.py:15`

### Required HiveMind permissions

HiveMind denies every message type by default. After you create the client, grant each message
type with `hivemind-core allow-msg "<message>" <Node ID>`.

**Core audio (`ovos-audio`)**

```bash
hivemind-core allow-msg "speak" 3
hivemind-core allow-msg "mycroft.audio.is_alive" 3
hivemind-core allow-msg "mycroft.audio.is_ready" 3
hivemind-core allow-msg "mycroft.audio.speak.status" 3
hivemind-core allow-msg "mycroft.stop" 3
```

**OCP (Open Voice OS Common Play)**

```bash
hivemind-core allow-msg "ovos.common_play.player.status" 3
hivemind-core allow-msg "ovos.common_play.track_info" 3
hivemind-core allow-msg "ovos.common_play.get_track_length" 3
hivemind-core allow-msg "ovos.common_play.get_track_position" 3
hivemind-core allow-msg "ovos.common_play.playlist.queue" 3
hivemind-core allow-msg "ovos.common_play.play" 3
hivemind-core allow-msg "ovos.common_play.resume" 3
hivemind-core allow-msg "ovos.common_play.pause" 3
hivemind-core allow-msg "ovos.common_play.stop" 3
hivemind-core allow-msg "ovos.common_play.previous" 3
hivemind-core allow-msg "ovos.common_play.next" 3
hivemind-core allow-msg "ovos.common_play.set_track_position" 3
hivemind-core allow-msg "ovos.common_play.playlist.clear" 3
hivemind-core allow-msg "ovos.common_play.shuffle.set" 3
hivemind-core allow-msg "ovos.common_play.shuffle.unset" 3
hivemind-core allow-msg "ovos.common_play.repeat.set" 3
hivemind-core allow-msg "ovos.common_play.repeat.unset" 3
hivemind-core allow-msg "ovos.common_play.repeat.one" 3
```

**PHAL** (optional — only if `ovos-PHAL` is installed)

```bash
hivemind-core allow-msg "mycroft.phal.is_alive" 3
hivemind-core allow-msg "mycroft.phal.is_ready" 3
```

**Volume control** (optional — requires `ovos-phal-plugin-alsa`)

```bash
hivemind-core allow-msg "mycroft.volume.get" 3
hivemind-core allow-msg "mycroft.volume.set" 3
hivemind-core allow-msg "mycroft.volume.increase" 3
hivemind-core allow-msg "mycroft.volume.decrease" 3
hivemind-core allow-msg "mycroft.volume.mute" 3
hivemind-core allow-msg "mycroft.volume.unmute" 3
```

Replace `3` with the actual Node ID shown when you ran `hivemind-core add-client`.

### Start `hivemind-core`

```bash
hivemind-core listen --port 5678
```

### Docker alternative

`hivemind-media-player` ships a `Dockerfile` (based on `debian:trixie-slim`) and a
`docker-compose.yml` for a single-command deployment. The image installs `hivemind-core`, the
agent plugin, `ovos-audio`, and audio system dependencies (vlc, mpv, PipeWire, ALSA). See the
upstream repository for usage.

Mount `$HOME/.config/hivemind` as a volume so the container keeps its identity across restarts:

```yaml
volumes:
  - ${HOME}/.config/hivemind:/root/.config/hivemind
```

`hivemind-core` generates its node identity (and its keypair) the first time it starts. MA's
provider config pins the access key from that identity. Without the volume, a recreated
container generates a new identity on every restart, the pinned key on the MA side stops
matching, and the provider flaps between connected and unavailable.

### Then configure MA

Install this plugin in MA's Python environment, restart MA, and add the provider under
**Settings > Players > Add Provider > HiveMind (remote OVOS)**. Enter the host, port, access
key, and password from the `add-client` output above.

See [docs/deployment.md](docs/deployment.md) for a complete remote device setup walkthrough,
including `ovos-audio` configuration and the `mycroft.conf` example.

---

## Configuration

| Key | Type | Default | Required | Description |
|---|---|---|---|---|
| `host` | string | — | **yes** | Hostname or IP of the HiveMind node (for example `myrpi.local`, `192.168.1.42`). |
| `port` | integer | `5678` | no | HiveMind WebSocket port. Change only if HiveMind was started with a non-default port. |
| `access_key` | secure string | — | **yes** | The key generated by `hivemind-core add-client`. Stored encrypted by MA. |
| `password` | secure string | — | no | Optional password, only if you passed `--password` during `add-client`. |
| `ssl` | boolean | `true` | no | Whether to use `wss://` (encrypted). Disable only on a trusted local network for debugging. |
| `player_name` | string | `MA HiveMind Player` | no | Display name shown in the MA UI. Set a unique name when you have multiple instances. |

### What MA stores internally

MA stores provider configuration as JSON in its database. The section for this provider looks
like this:

```json
{
  "type": "player",
  "domain": "hivemind_player",
  "values": {
    "host": "192.168.1.42",
    "port": 5678,
    "access_key": "<encrypted>",
    "password": "<encrypted>",
    "ssl": true,
    "player_name": "Living Room"
  }
}
```

MA stores `access_key` and `password` as `ConfigEntryType.SECURE_STRING` and encrypts them at
rest. They are never written in plaintext.

### What happens if configuration is wrong

- **Wrong host or port / HiveMind not running:** The connection times out after 15 seconds, MA
  raises `ProviderUnavailableError`, and the provider shows as "unavailable" in MA.
- **Wrong access key:** HiveMind rejects the handshake. You see an authentication error in MA
  logs, and the provider shows as "unavailable".
- **Wrong password:** Same as a wrong access key — authentication fails at the handshake.
- **SSL mismatch:** If HiveMind uses a self-signed certificate and your OS does not trust it,
  the TLS handshake fails. Workaround: set `ssl: false` for local testing (do not use this in
  production).
- **OCP not installed on remote OVOS:** The provider connects but play commands are silently
  ignored. No audio plays.

---

## Architecture overview

```
+------------------------+
|   Music Assistant      |
|   (MA server)          |
|                        |
|  HiveMindPlayerProvider|
|  HiveMindPlayer        |
+------------------------+
        |
        | wss://host:5678  (TLS + key auth)
        | HiveMessage envelope (type=MYCROFT)
        |   wraps: ovos.common_play.* Message
        v
+------------------------+
|   HiveMind core        |  (on remote OVOS device)
|   (authenticates,      |
|    decrypts, forwards) |
+------------------------+
        |
        | ws://localhost:8181/core  (local bus only)
        v
+------------------------+
|   OVOS / OCP           |
|   (on remote device)   |
+------------------------+
        |
        v
+------------------------+
|   Audio backend        |
|   (VLC, mpd, etc.)     |
+------------------------+

State events travel the same path in reverse:
OVOS emits ovos.common_play.player.state
  -> HiveMind tunnels it back to MA
  -> HiveMindPlayerProvider._on_player_state updates MA player state
```

See [docs/architecture.md](docs/architecture.md) for a full class diagram, threading model,
HiveMessage envelope details, and the complete message reference.

---

## How it works

### Connection

`HiveMindPlayerProvider.handle_async_init` (`hivemind_ma_player/__init__.py:275`) creates a
`HiveMessageBusClient(key, host=host, port=port, password=password, ssl=ssl)` and calls
`bus.connect(FakeBus())` in a daemon thread. `FakeBus` is an in-process pub/sub bus that
HiveMind uses as the local side of the tunnel. MA waits up to 15 seconds for the connection.
If it fails, MA raises `ProviderUnavailableError`.

### Player registration

`discover_players` (`hivemind_ma_player/__init__.py:353`) registers a single `HiveMindPlayer`
with ID `<instance_id>:hivemind`. The display name comes from the `player_name` config entry.

### Command flow (MA to remote OVOS)

Playback commands call `HiveMindPlayer._emit(msg_type, data)`, which wraps the message in a
`HiveMessage` envelope and calls `bus.emit_mycroft(msg)`. HiveMind encrypts and sends it to the
remote node, which unwraps the envelope and emits the inner OVOS `Message` on the remote bus.

| MA action | OCP message emitted | Payload |
|---|---|---|
| Play (resume) | `ovos.common_play.resume` | — |
| Pause | `ovos.common_play.pause` | — |
| Stop | `ovos.common_play.stop` | — |
| Seek | `ovos.common_play.set_track_position` | `{"position": <float seconds>}` |
| Volume | `mycroft.volume.set` | `{"percent": <0.0-1.0>}` |
| Mute | `mycroft.volume.mute` | — |
| Unmute | `mycroft.volume.unmute` | — |
| Play media | `ovos.common_play.play` | `{"media": <MediaEntry dict>}` |
| Announcement | `ovos.common_play.play` | `{"media": <MediaEntry dict>}` |

**Important:** the stream URL in the `media` payload is resolved by MA and must be reachable
from the **remote OVOS device**, not just from the MA server. If OVOS is on a different subnet,
configure MA's external URL in its stream settings.

### State sync (remote OVOS to MA)

**Push:** The plugin subscribes to `ovos.common_play.player.state` and
`ovos.common_play.media.state` on the HiveMind bus. These events are tunnelled back from the
remote OVOS automatically.

**Pull (polling fallback):** `poll()` sends `ovos.common_play.status` through
`bus.wait_for_response(..., reply_type="ovos.common_play.status.response", timeout=3.0)`.
`wait_for_response` registers the reply listener before it sends the request, which avoids the
race condition where the response arrives before the listener is registered. Poll runs every 5
seconds while playing, and every 30 seconds otherwise.

---

## Verifying it works

### MA UI

After you add the provider and press play:
- The player tile shows the name you gave it (for example "Living Room") with a green status
  indicator.
- The progress bar advances as the track plays.

If the tile shows grey or "unavailable", the connection failed. See Troubleshooting.

### Watching the tunnel

On the remote device, run:

```bash
# Watch raw HiveMind traffic
hivemind-client terminal

# Watch the OVOS bus on the remote side
ovos-bus-client monitor
```

When MA triggers playback you should see `ovos.common_play.play` arrive on the remote OVOS bus,
followed by `ovos.common_play.player.state` with `{"state": 1}` tunnelled back.

---

## Troubleshooting

**Provider stays "unavailable" after saving config**

Symptom: The player tile is grey right after you save.

Cause: MA could not complete the HiveMind handshake within 15 seconds.

Fix:
- Verify HiveMind is running: `systemctl status hivemind` on the remote device.
- Test TCP connectivity: `nc -zv REMOTE_HOST 5678` from the MA host.
- Confirm the access key: `hivemind-core list-clients` on the remote.
- If you set a password, confirm you entered the same value in MA.

---

**"HiveMind connection failed: authentication error"**

Symptom: MA logs show an authentication error. The provider is unavailable.

Cause: HiveMind rejected the access key (or password).

Fix: Re-run `hivemind-core add-client --name "music-assistant"` on the remote and paste the new
key into the MA config. Delete the old client first if the name conflicts:
`hivemind-core delete-client --name "music-assistant"`.

---

**Playback starts on MA but no audio on the remote device**

Symptom: MA shows the track as playing but the OVOS device is silent.

Cause: OCP is not installed on the remote OVOS, or the stream URL is not reachable from the
remote device.

Fix:
- On the remote: `pip show ovos-skill-ocp` — install it if missing.
- On the remote: `curl -I <stream-url>` where `<stream-url>` is the URL MA resolved. If it
  fails, configure MA to use an externally reachable URL.
- Check OVOS logs on the remote: `journalctl -u ovos-core -f`.

---

**State in MA is always IDLE**

Symptom: The progress bar never moves. State always shows stopped.

Cause 1: HiveMind is not tunnelling `ovos.common_play.*` events back to clients.

Fix: Check HiveMind's `allowed_messages` configuration on the remote. By default HiveMind
forwards all OVOS bus messages to connected clients. Run `hivemind-client terminal` and watch
whether state events arrive after you trigger playback on the remote.

Cause 2: Network latency is too high, and poll responses time out (3-second timeout).

Fix: Reduce network latency, or increase the timeout in a fork. See
[docs/plugin-authors.md](docs/plugin-authors.md).

---

**Multiple devices have the same player name**

Symptom: Two player tiles with the same name appear in MA.

Fix: Set a unique `player_name` for each provider instance in the MA configuration UI.

---

**TLS certificate error**

Symptom: The connection fails with a certificate verification error in MA logs.

Cause: HiveMind uses a self-signed certificate that the OS does not trust.

Fix for testing: set `ssl: false` in the provider config (this disables TLS entirely — do not
use it in production). For production: install a valid certificate on the HiveMind host (for
example through Let's Encrypt), or add the self-signed certificate to the MA host's trust
store.

---

**"hivemind-bus-client or ovos-bus-client not installed"**

Symptom: The provider is immediately unavailable. MA logs show an import error.

Fix:
```bash
pip install ovos-bus-client hivemind-bus-client
```
inside MA's Python environment, then restart MA.

---

**Seek has no effect**

Symptom: Dragging the seek bar in MA does nothing.

Cause: The OCP backend on the remote OVOS device may not support seeking.

Fix: Check the audio backend used by OCP on the remote. Not all backends implement
`ovos.common_play.set_track_position`.

---

**Connection drops intermittently**

Symptom: The player goes "unavailable" periodically and comes back.

Cause: The network between MA and the remote device is unstable, or the remote device is
sleeping.

Fix:
- Check network reliability between the MA host and the remote.
- Make sure the remote device does not enter sleep or suspend while HiveMind should be active.
- This provider does not currently reconnect automatically. Reload the provider in MA after a
  drop, or add reconnect logic in a fork.

---

**Announcements interrupt music and it does not resume**

Symptom: Playing a TTS announcement through MA stops the current track permanently.

Cause: `play_announcement` sends `ovos.common_play.play`, the same message as `play_media`. OCP
treats it as a new track and does not automatically resume the previous queue.

Fix: This is a known limitation shared with `ovos-ma-player`. Fixing it needs OCP's native
interrupt/duck/resume announcement handling, which uses a different message path.

---

**Playback commands are accepted but `ovos-media` never plays anything**

Symptom: The connection is healthy, HiveMind forwards the messages, but the remote device
(running `ovos-media` instead of the classic OCP audio service) never starts audio.

Cause: HiveMind isolates each tunnelled, non-admin client into its own session, separate from
the device's local default session. `ovos-media` only accepts a media source when the requesting
session either has `media.validate_source` set to `false` or is an admin connection — this is by
design, not a bug, since it stops an arbitrary tunnelled peer from hijacking device-local
playback.

Fix: Set `media.validate_source: false` for this player in the remote device's `ovos-media`
configuration, or generate the access key with an admin-level client
(`hivemind-core add-client --admin`).

---

## Developer notes

- `HiveMindPlayerProvider` and `HiveMindPlayer` mirror `OVOSPlayerProvider`/`OVOSPlayer` from
  `ovos-ma-player`. The only structural difference is the bus type (`HiveMessageBusClient` vs.
  `MessageBusClient`) and the `_emit` helper on `HiveMindPlayer`, which calls
  `bus.emit_mycroft()` instead of `bus.emit()`.
- All `ovos_bus_client`, `hivemind_bus_client`, and `ovos_utils` imports are deferred to
  `handle_async_init` or method bodies, to avoid hard failures when the packages are absent.
- The `multi_instance: true` manifest flag allows multiple provider instances in MA.

See [docs/architecture.md](docs/architecture.md) for the full class diagram, OCP/HiveMind
message reference, and threading model, and [docs/plugin-authors.md](docs/plugin-authors.md)
for guidance on forking and extending the plugin.

---

## Related

- [ovos-ma-player](https://github.com/TigreGotico/ovos-ma-player) — same OCP protocol over a
  plain local WebSocket. Use this when OVOS runs on the same machine as MA.

---

## License

Apache 2.0

# Deployment Guide — hivemind-ma-player

This guide covers running `hivemind-ma-player` and the HiveMind core in production
environments. It assumes Music Assistant is already installed and running.

For MA installation and deployment (bare metal, Docker, HA add-on), see
[ovos-ma-player/docs/deployment.md](../../ovos-ma-player/docs/deployment.md) — the MA-side
steps are identical. This document focuses on the HiveMind-specific setup.

---

## Architecture recap

```
MA host                        Remote OVOS device
+----------------+             +------------------+
| Music Assistant|             | OVOS + OCP       |
| + this plugin  |--wss:5678-->| HiveMind core    |
+----------------+             +------------------+
```

HiveMind core must run on each remote OVOS device. MA (with this plugin) connects out to
HiveMind, not the other way around.

---

## Remote device setup

This section covers setting up a remote device from scratch so MA can drive it as a player.
The remote device does not need a full OVOS installation. `hivemind-media-player` provides a
standalone audio stack. See
[https://github.com/JarbasHiveMind/hivemind-media-player](https://github.com/JarbasHiveMind/hivemind-media-player)
for the upstream project.

### Step 1 — Install packages

```bash
pip install hivemind-core hivemind-player-agent-plugin ovos-audio ovos-plugin-manager
```

To include PHAL (platform/hardware abstraction) and ALSA volume control:

```bash
pip install ovos-PHAL ovos-phal-plugin-alsa
```

### Step 2 — Configure the agent plugin

Edit `~/.config/hivemind-core/server.json` (create it if it does not exist):

```json
{
    "agent_protocol": {
        "module": "hivemind-player-agent-plugin",
        "hivemind-player-agent-plugin": {}
    }
}
```

This tells `hivemind-core` to load `HiveMindPlayerProtocol`, which starts `ovos-audio`'s
`PlaybackService` on an internal `FakeBus` and forwards all incoming OCP messages to it.
`hivemind_player_protocol/__init__.py:15`

### Step 3 — Configure ovos-audio

Edit `~/.config/mycroft/mycroft.conf`:

```json
{
  "play_wav_cmdline": "paplay %1",
  "play_mp3_cmdline": "mpg123 %1",
  "play_ogg_cmdline": "ogg123 -q %1",

  "tts": {
    "module": "ovos-tts-plugin-server"
  },

  "Audio": {
    "backends": {
      "OCP": {
        "type": "ovos_common_play",
        "preferred_audio_services": ["mpv", "vlc"],
        "disable_mpris": true,
        "dbus_type": "session",
        "manage_external_players": false,
        "active": true
      },
      "vlc": {
        "type": "vlc",
        "active": true,
        "initial_volume": 100,
        "low_volume": 50
      },
      "mpv": {
        "type": "mpv",
        "active": true,
        "initial_volume": 100,
        "low_volume": 50
      }
    }
  }
}
```

OCP is the primary backend. mpv or VLC handles the actual audio output. Both must be installed
on the system (`apt install mpv vlc` or the equivalent for your distribution).

### Step 4 — Create a client and grant permissions

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

Note the Node ID. Grant each required message type (replace `3` with your Node ID):

**Core audio**

```bash
hivemind-core allow-msg "speak" 3
hivemind-core allow-msg "mycroft.audio.is_alive" 3
hivemind-core allow-msg "mycroft.audio.is_ready" 3
hivemind-core allow-msg "mycroft.audio.speak.status" 3
hivemind-core allow-msg "mycroft.stop" 3
```

**OCP**

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

**PHAL** (optional)

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

### Step 5 — Start hivemind-core

```bash
hivemind-core listen --port 5678
```

### Step 6 — Configure MA

In MA, go to **Settings > Players > Add Provider > HiveMind (remote OVOS)** and enter:

- **Host**: IP or hostname of the remote device
- **Port**: `5678`
- **Access Key**: the value from `add-client` output
- **Password**: the value from `add-client` output (leave blank if you did not generate one)
- **SSL**: enabled by default; disable only for local testing
- **Player name**: a unique friendly name (for example "Living Room")

### Docker alternative

`hivemind-media-player` ships a `Dockerfile` (based on `debian:trixie-slim`) that installs all
system-level audio dependencies (VLC, mpv, PipeWire, ALSA, mpg123, sox) alongside
`hivemind-core` and the agent plugin in a Python venv. It also provides a `docker-compose.yml`
for a single-command deployment. See the upstream repository for compose file usage and volume
mount paths for the config files.

---

## Setting up HiveMind core on a remote device (existing OVOS installation)

> Use this section only if the remote device already runs OVOS + OCP and you want to add
> HiveMind on top. For a fresh dedicated player device, use the "Remote device setup" section
> above instead.

### Install

```bash
pip install hivemind-core
```

Or install it into the same venv as OVOS if you manage OVOS in a venv.

### Generate an access key for MA

```bash
hivemind-core add-client --name "music-assistant"
```

Output:

```
Client added:
  Name: music-assistant
  Access Key: <long hex string>
  Password: (none)
```

If you want a password:

```bash
hivemind-core add-client --name "music-assistant" --password "choose-a-strong-password"
```

Copy and store the access key securely. You enter it in the MA provider config.

### Start HiveMind

```bash
hivemind-core listen --port 5678
```

HiveMind connects to the local OVOS messagebus (`ws://localhost:8181/core`) by default. Make
sure OVOS is running before you start HiveMind.

---

## systemd unit for HiveMind

Create `/etc/systemd/system/hivemind.service` on the remote OVOS device:

```ini
[Unit]
Description=HiveMind core
After=network.target ovos-core.service
Requires=ovos-core.service

[Service]
Type=simple
User=ovos
ExecStart=/home/ovos/.local/bin/hivemind-core listen --port 5678
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Adjust `User` and `ExecStart` path to match your installation. Then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable hivemind
sudo systemctl start hivemind
```

Verify HiveMind started:

```bash
sudo systemctl status hivemind
journalctl -u hivemind -f
```

You should see a line like this:

```
INFO  hivemind_core Listening on 0.0.0.0:5678
```

---

## TLS / SSL configuration

HiveMind supports TLS. The `ssl=True` default in this plugin means MA attempts a `wss://`
connection.

### Self-signed certificate (development)

HiveMind can generate a self-signed certificate automatically. Check the HiveMind-core
documentation for the `--ssl-cert` and `--ssl-key` flags, or run with `--no-ssl` for testing
(then set `ssl: false` in the MA provider config).

### Production certificate (Let's Encrypt)

If the remote device has a domain name and is reachable from the internet:

```bash
# Install certbot
sudo apt install certbot

# Obtain a certificate
sudo certbot certonly --standalone -d myrpi.example.com

# Point HiveMind at the certificate
hivemind-core listen --port 5678 \
  --ssl-cert /etc/letsencrypt/live/myrpi.example.com/fullchain.pem \
  --ssl-key  /etc/letsencrypt/live/myrpi.example.com/privkey.pem
```

All standard OS certificate stores trust a Let's Encrypt certificate, so MA accepts it without
any added configuration.

### Self-signed certificate (production workaround)

If you use a self-signed certificate, you can add it to the MA host's trust store:

```bash
# On the MA host (Ubuntu/Debian example)
sudo cp hivemind-ca.crt /usr/local/share/ca-certificates/hivemind.crt
sudo update-ca-certificates
```

Alternatively, set `ssl: false` in the MA provider config to disable TLS entirely. Do this only
on a fully isolated local network.

---

## Managing multiple remote devices

Each remote OVOS device needs:
1. OVOS + OCP running.
2. HiveMind core running (can share the same port or use different ports).
3. At least one access key generated for MA.

In MA, add one `hivemind-ma-player` provider instance per remote device:
- Set a unique `player_name` for each (for example, "Living Room", "Bedroom", "Office").
- Each instance gets a unique `instance_id` from MA, so player IDs will not collide.

There is no limit to the number of instances beyond MA's general provider limits.

### Key management

List all clients on a remote device:
```bash
hivemind-core list-clients
```

Revoke a key:
```bash
hivemind-core delete-client --name "music-assistant"
```

After you revoke a key, the MA provider instance fails to connect. Update the MA config with a
new key, or remove the provider instance.

### Key rotation

Periodic key rotation is good security practice:

1. `hivemind-core add-client --name "music-assistant-new"` — add a new client.
2. Update the MA provider config with the new key.
3. Verify the new connection works in MA.
4. `hivemind-core delete-client --name "music-assistant"` — revoke the old key.

---

## Network requirements

| Connection | Protocol | Port | Direction | Auth |
|---|---|---|---|---|
| MA -> HiveMind | WebSocket (`wss://` or `ws://`) | 5678 (default) | MA starts | Access key + optional password |
| HiveMind -> OVOS | WebSocket (`ws://`) | 8181 | HiveMind starts (local) | None |
| OVOS -> MA streams | HTTP | MA stream port | OVOS starts it when playing | None |

The remote OVOS device must be able to reach the MA stream port. Configure MA's network
settings to use an address reachable from the remote LAN or internet.

Firewall rule on the remote device (using `ufw`):

```bash
# Allow MA host to reach HiveMind
sudo ufw allow from MA_HOST_IP to any port 5678
```

HiveMind does not need to start connections back to MA. MA always starts the connection
from MA's side.

---

## Security considerations

### Access key strength

`hivemind-core add-client` generates a cryptographically random access key. Do not replace it
with a short or guessable value. If you need a human-readable name, use the `--name` parameter.
The key itself should stay auto-generated.

### Password layer

The optional `--password` adds a second factor. HiveMind uses the password to derive an
encryption key for the message payload. Without a password, TLS alone protects the payload.
With a password, the payload gets an added layer of encryption even if TLS is broken or
misconfigured.

Recommendation: use a password if the HiveMind port is exposed to untrusted networks.

### Firewall

Expose the HiveMind port (5678) only to the MA host's IP, not to the entire internet:

```bash
# UFW: allow only MA's IP
sudo ufw allow from 192.168.1.10 to any port 5678

# UFW: deny everything else on this port
sudo ufw deny 5678
```

### Key storage in MA

MA stores `access_key` and `password` as `ConfigEntryType.SECURE_STRING`
(`hivemind_ma_player/__init__.py:88`, `:95`). MA encrypts these values at rest with its own key
store. MA never writes them in plaintext to disk.

Do not share MA's config backup with untrusted parties. It contains the encrypted keys.

---

## Monitoring

### HiveMind logs

```bash
# systemd
journalctl -u hivemind -f

# Direct process
hivemind-core listen --port 5678 --verbose
```

A successful MA connection looks like this:

```
INFO  hivemind_core New client connected: music-assistant (key: abc123...)
```

An authentication failure:

```
WARNING hivemind_core Authentication failed for client (bad key)
```

### Traffic observation

On the remote device, watch messages flowing through the tunnel:

```bash
hivemind-client terminal
```

This shows both incoming (MA -> OVOS) and outgoing (OVOS -> MA) messages in real time.

### OVOS bus

On the remote OVOS device:

```bash
ovos-bus-client monitor
```

When MA triggers playback you should see `ovos.common_play.play` arriving and
`ovos.common_play.player.state` being emitted.

### MA logs

```bash
# systemd
journalctl -u music-assistant -f | grep hivemind_ma_player

# Docker
docker logs -f music-assistant | grep hivemind_ma_player
```

A successful connection:

```
INFO  hivemind_ma_player Connected to HiveMind at 192.168.1.42:5678
```

A failed connection:

```
ERROR hivemind_ma_player HiveMind connection failed: <error details>
```

---

## Version requirements

| Component | Notes |
|---|---|
| Python 3.11+ | Required by MA |
| Music Assistant 2.x | Plugin uses the 2.x provider API |
| ovos-bus-client | Any recent version |
| hivemind-bus-client | Any version compatible with your HiveMind core version |
| hivemind-core | Any version compatible with the above client library |
| OVOS with OCP | Must have `ovos-skill-ocp` installed on the remote device |

HiveMind core and `hivemind-bus-client` must be version-compatible. If you upgrade one, upgrade
the other. Check the HiveMind-core release notes for compatibility information.

---
[← OCP Protocol](ocp-protocol.md) · [Home](../README.md)

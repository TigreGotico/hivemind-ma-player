# hivemind-ma-player

Music Assistant PlayerProvider that drives a remote OVOS / OCP instance via a HiveMind
encrypted websocket connection.

## Overview

HiveMind is a secure overlay on the OVOS messagebus that wraps each Mycroft/OVOS bus message
in an authenticated envelope and transmits it over a `wss://` connection. This package bridges
Music Assistant (the media server) with one or more remote OVOS devices running HiveMind.

Each provider instance in MA maps to exactly one remote OVOS device. Because the manifest sets
`multi_instance: true`, you can add as many instances as you have remote devices.

## Key Classes

| Class | Purpose | Source |
|---|---|---|
| `HiveMindPlayerProvider` | MA `PlayerProvider` — manages the HiveMind bus connection and player registration | `hivemind_ma_player/__init__.py:270` |
| `HiveMindPlayer` | MA `Player` — translates MA commands to OCP messages via HiveMind | `hivemind_ma_player/__init__.py:118` |

## Contents

- [Installation, configuration & troubleshooting](../README.md)
- [Architecture & message reference](architecture.md)
- [Plugin authors guide](plugin-authors.md)

# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this integration, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, use GitHub's [private vulnerability reporting](https://github.com/bvis/fermax-blue-hass/security/advisories/new) so the report stays confidential while it's investigated.

Include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact

You will receive an initial response within 48 hours. We will work with you to understand and address the issue before any public disclosure.

## Scope

This integration communicates with the Fermax Blue (DuoxMe) cloud servers using the same protocol as the official mobile app. Security concerns may include:

- Credential handling and storage
- Network communication security (HTTPS API, WSS signaling, RTP media)
- FCM push notification handling
- Local file access (doorbell photos and recordings)

## Supported Versions

Only the latest release is supported with security updates.

## Threat model notes

These are known, accepted design constraints rather than open issues. They are
documented here so users can reason about the integration's security posture.

### Cloud credentials are user-supplied app identifiers

The OAuth `client_id`/`client_secret` the integration needs are app-level client
identifiers that each user takes from their own copy of the official app;
they are deliberately not distributed with this repository. The user's Fermax
account e-mail and password are stored by Home Assistant in its standard
config-entry storage (`.storage/`), and the push (FCM) registration in a file
next to it, protected by filesystem permissions like any other cloud
integration's credentials. The OAuth access token issued at login is held in
memory only and is requested again after a restart.

### Signaling endpoints from push payloads are validated

Camera streams are negotiated over a Socket.IO signaling channel whose URL
arrives inside an FCM push payload. Before connecting, the integration accepts
only hosts under `fermax.io` and upgrades insecure `http://`/`ws://` schemes to
`https://`/`wss://`, so a crafted push cannot redirect signaling to an
arbitrary host or downgrade the transport to plaintext. The payload also
carries a short-lived device JWT used to authenticate the media session; it is
held in memory for that session only.

### The live video endpoint is protected by a token, not by a login

Home Assistant's go2rtc reads the intercom's live video from
`/api/fermax_blue/webrtc/<token>`. go2rtc cannot log in to Home Assistant, so
this endpoint does not ask for one: the token is the only protection. It is
random (128 bits), exists in memory only and changes every time the
integration loads; an unknown token gets a 404.

Anyone who has the token and can reach Home Assistant can wake the intercom,
watch and listen, and answer a call with a microphone. They cannot open a
door. The token is part of the camera's stream source, so it is visible
wherever that source is: if you run your own go2rtc (the phone answer flow in
the README asks for one), its API on port 1984 lists stream sources to
everyone on your network unless you protect it.

### FCM push depends on an unofficial client

Push notifications use `firebase-messaging`, an unofficial community-maintained
FCM client, not a Google-supported library. Push carries doorbell rings and the
stream-setup payload, so if the listener is down those features stop working
while polling-based entities continue. Treat push delivery as best-effort.
Malformed or undecryptable push messages are isolated and skipped per message
rather than allowed to take the listener down.

### Local media is stored unencrypted

Doorbell photos and stream recordings are written to Home Assistant's media
folder (with configurable retention), and the last preview frame is persisted
across restarts in `.storage/`. Anyone with filesystem access to the Home
Assistant instance can read them — filesystem access control is the boundary.
Email addresses in the door-opening history are partially redacted before they
reach entity attributes and the recorder database.

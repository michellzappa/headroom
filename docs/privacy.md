# Privacy Policy — Headroom

**Last updated:** 31 July 2026

Headroom is a local-first tool for viewing AI coding quotas and related
development status. This policy covers the **Headroom iOS / iPadOS app**
(`com.centaur-labs.headroom`) and how it relates to the optional Mac host.

## Summary

- Headroom does **not** operate a cloud account for itself.
- The iOS app does **not** sell, rent, or share personal data with advertisers.
- Provider credentials (Claude, Codex, Cursor, GitHub, Supabase, Plausible, PostHog, …)
  remain on your Mac. The phone never stores those secrets.
- The phone stores only what it needs to talk to *your* Mac: endpoint URL and a
  **mobile pairing token**, plus a local cache of the last usage snapshot for
  offline display.

## What the iOS app does

1. Discovers Headroom Mac hosts on your local network (Bonjour) or connects to a
   URL you enter (LAN / Tailscale).
2. Requests the usage document from that host over HTTP(S) using your mobile
   token and the permissions you configured on the Mac (`read`, `refresh`,
   `sources`, `servers`).
3. Optionally sends local notifications when the host reports attention-level
   warnings.
4. Optionally uses Face ID / device authentication before asking the Mac to
   stop a local development server.

## Mac share profile

**Your usage → Your profile** on the Mac makes a link to a web page that shows
the AI coding subscriptions you have turned on, their plans and list prices,
how much of each weekly plan is left this week, your colours, and your stats
card. The profile is inside the link, after the `#`. Browsers do not send that
part to the server, so the page host (GitHub Pages) receives only a request
for the page itself. Nothing is sent until you copy the link and give it to
someone. Anyone who has the link can read the profile.

## Mac anonymous diagnostics

The **Mac app** can send one aggregate diagnostics batch per week when
**Share anonymous product diagnostics** is left on (Settings → Telemetry).
That batch is described in [`docs/telemetry.md`](telemetry.md): no prompts,
paths, tokens, or stable install ids. A week-scoped HMAC dedupe key prevents
the same Mac from inflating a week’s count; the secret that produces it never
leaves the Mac. Country is derived at the edge from Cloudflare geo (ISO code
only); the IP is not stored. The iOS app does not send this. Turning the
setting off deletes any pending local batch.

## Data we do not collect

Headroom (the product) does not run analytics SDKs, advertising SDKs, or
third-party crash reporters in the iOS app. We do not build advertising
profiles. We do not sell data.

## Data on your devices

| Data | Where it lives |
|---|---|
| Mobile pairing token | iOS Keychain |
| Host endpoint | iOS app preferences |
| Last `/usage` snapshot | On-device archive / App Group (widgets) |
| Provider API credentials | Mac Keychain / local CLIs only |
| Host auth token (ESP32 / LAN) | Mac `~/.headroom/token` |
| Multi-Mac settings + machine summary | Off by default. When you turn on `icloud_sync`, your own iCloud Drive |

Multi-Mac sync writes one small file per Mac into a folder in *your* iCloud
Drive so a second Mac can pick up your settings and show what the first one is
doing. It is off until you turn it on in Settings → Other Macs, it never
carries credentials or file paths, and there is still no Headroom account or
Headroom server involved. See [multi-mac.md](multi-mac.md).

Deleting the iOS app removes its Keychain items and local cache for that
install. Uninstalling the Mac host / purging `~/.headroom` removes host-side
state.

## Network

The iOS app communicates with the Mac host you choose. That host may call
third-party APIs (Anthropic, OpenAI/Codex, Cursor, GitHub, Vercel, Supabase,
Plausible, PostHog, …) using credentials you already configured on the Mac. Those
requests are between your Mac and those providers under *their* policies —
not uploaded to a Headroom server.

## Children’s privacy

Headroom is not directed at children under 13.

## Contact

Questions about this policy: open an issue at
https://github.com/michellzappa/headroom/issues

## Changes

We may update this page when the product changes. The “Last updated” date at
the top will change when we do.

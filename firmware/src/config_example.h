// Copy this file to config.h and fill in your values. config.h is gitignored
// so your Wi-Fi passwords never land in the repo.
#pragma once

// ---- Wi-Fi networks (tries whichever is in range) ----
// Add every network the tracker might travel to: home, your phone hotspot,
// office, etc. On the road: either tether both this board AND your Mac to a
// phone hotspot, or plug the board into the Mac over USB (host speaks HR over
// CDC). Hotel Wi-Fi usually blocks the Wi-Fi path (captive portals a headless
// board can't click through, plus client isolation).
static const struct { const char *ssid; const char *pass; } WIFI_NETWORKS[] = {
    {"home-ssid", "home-password"},
    {"phone-hotspot", "hotspot-password"},
    // add more {"ssid", "password"}, lines as needed
};

// ---- Host server ----
// Resolved by name via Bonjour/mDNS, so the Mac's changing IP doesn't matter —
// as long as both are on the same network. HOST_NAME has no ".local" suffix.
#define HOST_NAME "your-mac-hostname"   // `scutil --get LocalHostName` on the Mac
#define HOST_PORT 8737

// Fallback used only if mDNS can't resolve HOST_NAME (e.g. a network that
// blocks mDNS). Set to the Mac's IP on that network, or leave as-is.
#define HOST_FALLBACK_IP "192.168.1.50"

// Host token for LAN access (ESP32 / generic clients). /usage carries repo
// names, commit subjects, local server paths and spend, so the host requires
// this from anything that isn't loopback. Stored at ~/.headroom/token after
// first host start — not the iPhone mobile token (~/.headroom/mobile-token).
// Leave empty only if you set "require_auth": false in ~/.headroom/config.json.
// Not needed for the USB path: the cable already implies physical access.
#define HOST_TOKEN ""

// Seconds between polls. The server refreshes its own data every 15s.
#define POLL_INTERVAL_S 60

// Over-the-air updates. With this on, `pio run -t upload --upload-port
// headroom.local` reflashes without the cable. Set a password you don't mind
// living in this file; anyone on the network who has it can flash the board.
#define OTA_HOSTNAME "headroom"
#define OTA_PASSWORD "change-me"

// ---- Panel brightness (AMOLED) ----
// One level, all day and all night. Panel units 0-255.
#define PANEL_BRIGHTNESS 200

# Headroom for the ESP32 desk display

Optional always-on glance. Same `/usage` feed as the menu bar and phone — **not
part of the core install**. The Mac host + menu bar (and optional iPhone /
Watch) are the product; this board is the desk curiosity that paints the same
numbers.

```
Mac host ──Wi-Fi HTTP──▶ board   (preferred)
Mac host ──USB CDC────▶ board   (hotel / no LAN fallback)
```

## Supported boards

Firmware targets three Waveshare SKUs (pick the PlatformIO env that matches):

| | 1.8″ (default `esp32-s3-18`) | 2.16″ (`esp32-s3-216`) | 1.75″ round (`esp32-s3-175-round`) |
|---|---|---|---|
| **Product** | [ESP32-S3-Touch-AMOLED-1.8](https://www.waveshare.com/esp32-s3-touch-amoled-1.8.htm) | [ESP32-S3-Touch-AMOLED-2.16](https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm) | ESP32-S3-Touch-AMOLED-1.75 |
| **SoC** | ESP32-S3R8 (Wi‑Fi + BLE, 8MB PSRAM, 16MB flash) | same | same |
| **Panel** | 1.8″ AMOLED, **368×448**, SH8601 over QSPI | 2.16″ AMOLED, **480×480**, CO5300 over QSPI | round AMOLED, **466×466**, CO5300 over QSPI |
| **Touch** | FT3168 / FT3x68 (some V2 demos use CST816T at `0x15`) | CST9220 at `0x5A` (SensorLib ≥0.4.1, IRQ-gated) | CST9217 at `0x5A` (native command/read/ACK protocol) |
| **PMU** | AXP2101 | AXP2101 | AXP2101 |
| **Expander** | TCA9554 (LCD / touch reset + DSI power) — usually `0x20`, some units `0x21` | none — `LCD_RST` GPIO39, `TP_RST` GPIO40 | none — `LCD_RST` GPIO39, `TP_RST` GPIO40 |
| **Extras** | QMI8658 IMU, PCF85063 RTC, ES8311 audio, BOOT + PWR buttons | same family + dual mics / ES7210 | same family + ES8311 audio; Headroom uses BOOT + PWR + touch |

Pins and bring-up live in `firmware/src/pin_config.h` and `firmware/src/main.cpp`. Other sibling sizes are **not** drop-in.

The 1.75″ target shares its proven panel offset, pins, and PMU restraint with
the working `amoled-175c` target in the sibling `esp32-lofiair` project. Touch
uses the direct CST9217 protocol validated by the sibling
`esp32-thinking-orbs` project. Its Headroom layout is not a cropped square: every
horizontal band is sized from the circle chord it actually occupies. The title
occupies the top wedge, rings use the wide middle, the burndown expands across
its local chord while its verdict rows taper near the bottom, and the
power/link marks mirror each other below them.

![Headroom on the 466px round AMOLED](screenshots/esp32-round-glance.png)

Optional 3.7V LiPo on the MX1.25 header; USB-C alone is enough for desk use.
Bottom-left power glyph reads the AXP2101 (plug on VBUS, cell + % when a
battery is fitted).

**1.8 black screen?** Try `TCA9554_ADDR = 0x21` in `pin_config.h` (Waveshare issue
#3). `pio device monitor` and the host’s USB bridge cannot share the port —
use `./scripts/flash-esp32.sh`, which refuses to race. Select a non-default
target with `-e`:

```bash
./scripts/flash-esp32.sh -e esp32-s3-216
./scripts/flash-esp32.sh -e esp32-s3-175-round
```

## Flash

Needs [PlatformIO](https://platformio.org/).

1. `cp firmware/src/config_example.h firmware/src/config.h` — Mac hostname
   (`scutil --get LocalHostName`) or fallback IP, plus the host token and any
   optional build-time Wi‑Fi networks.
2. Paste the **host token** into `HOST_TOKEN` (`~/.headroom/token` after first
   host start — **not** the mobile token).
3. Prefer `./scripts/flash-esp32.sh` (checks the serial port is free). Or:
   `cd firmware && pio run -t upload && pio device monitor`.

Wi-Fi is the default transport and the normal LaunchAgent does not claim
`/dev/cu.usbmodem*`. For the offline USB fallback, start the host with
`HEADROOM_ENABLE_USB=1`; stop that host before flashing or monitoring:

```bash
launchctl bootout gui/$(id -u)/com.centaur-labs.headroom
./scripts/flash-esp32.sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.centaur-labs.headroom.plist
```

### Wi‑Fi provisioning

If the board has no saved Wi‑Fi credentials, or cannot reach its configured
networks during startup, it creates a temporary setup access point named
`Headroom-XXXX` with password `headroom`. Connect from a phone; the captive
portal should open automatically. If it does not, visit `http://192.168.4.1`,
choose the home network, and save. The credentials are stored in the board's
NVS and it restarts into normal station mode. USB CDC remains available while
the setup portal is running, so a Mac connection is not lost during
provisioning.

The portal configures Wi‑Fi only. `HOST_NAME`, `HOST_FALLBACK_IP`, `HOST_TOKEN`,
OTA settings, and display settings still come from `firmware/src/config.h`.

Update the Mac host before flashing: the board reads its three providers from
`/usage?view=device` → `providers[]`, which a host older than 1.0.9 does not
send. A board that gets no providers says so on the glance rather than guessing.

## Using it

Wi‑Fi first; USB CDC when LAN fails. **Tap** a glance slot for detail; tap the
header (or short-press secondary) to cycle the lower pane; **hold** a glance
slot to switch the upper half between Rings and Pace; **long-press** empty
chrome or secondary on glance → `POST /sync/refresh`.

| Key | 1.8″ | 2.16″ | 1.75″ round | Action |
|---|---|---|---|---|
| **BOOT** | GPIO0 | GPIO0 (right) | GPIO0 | Cycle pages |
| **PWR / secondary** | PWR via TCA EXIO4 | IO18 (left) | AXP2101 PEKEY | Glance short: cycle lower pane; long: force-sync where supported. Detail: home |
| **PWR / style** | — | PWR / SYS_OUT GPIO16 (middle) | — | Short: Rings↔Pace. Hold ≥4s still powers off |

On the round board, PWR is delivered as a release-time PMU event: a short press
cycles the lower pane (or returns home from detail), while a four-second hold
still powers the device off. Long-press sync remains available through touch.

| Corner | Meaning |
|---|---|
| Top-right | Host `updated` clock |
| Bottom-right | Link glyph — Wi‑Fi arcs or USB cable |
| Bottom-left | Power — plug on VBUS; battery fill + % when a cell is present; bolt while charging |

### Rings or Pace

The upper half has the two readings the macOS menu-bar icon has, and the board
keeps its own choice in NVS — the Mac's Settings → General picker does not
travel here, and neither does Settings → Desk display (see Settings below).

| Style | What each slot shows |
|---|---|
| **Rings** (default) | Concentric bands, arc = used, white dot = even spend ([docs/rings.md](rings.md)) |
| **Pace** | One pill per provider with a line at even spend, and an accent mark riding `tanh((used − pace) / 8)` above the line when over, below when under |

Pace drops the arc, so it answers only whether the burn is ahead or behind —
the same trade the menu bar makes, and the same curve, so a gap of 8 points
lands near halfway to the end of the pill either way. It reads the provider's
longer window, which is the pool the menu bar takes too. A provider with no
pace draws a dimmer pill, no line and no mark, rather than a mark at zero.

The even-spend line stops at each pill. The menu bar carries one rail across
all three slots, which it can afford at 18pt; at 448px the same rail read as a
shared scale, and the three slots do not share one — each pill is its own
provider against its own window.

Preview either style without a reflash. Add `--panel round-466` for the round
target:

```bash
.venv-shots/bin/python scripts/render_esp32_preview.py --input docs/demo_usage.json --glance-style pace --raw --out /tmp/pace.png

.venv-shots/bin/python scripts/render_esp32_preview.py --input docs/demo_usage.json --panel round-466 --out /tmp/headroom-round.png
```

## Reset celebration

When a provider's quota window rolls, the board takes over the screen for about
2.4 seconds with an accent-tinted confetti burst. The provider's configured
accent supplies all of the particle shades. To test it remotely from the Mac
or another private-network machine, send the host token and optionally name a
provider slot:

```bash
curl -sS -X POST \
  -H "X-Headroom-Token: $(cat ~/.headroom/token)" \
  -H 'Content-Type: application/json' \
  -d '{"effect":"reset","provider":"codex"}' \
  http://headroom.local:8737/device/effect
```

The command is picked up on the board's next normal poll. Omit `provider` to
use the first selected model's accent. The endpoint is token-authenticated and
private-network-only. With **Celebrate quota resets** off in Settings → Desk
display the board consumes the command and draws nothing.

## Settings

The board has no settings screen. Its buttons differ per SKU and every touch
gesture is spoken for, so the settings live where the rest of Headroom's do:
**Mac Settings → Desk display**. The host stores the answers in
`~/.headroom/config.json`, ships them in `/usage?view=device` as `display`
([contract.md](contract.md)), and the board applies them on its next poll and
mirrors them to NVS — a cold boot without the host comes up the same way.

| Setting | What it does | Default |
|---|---|---|
| **Brightness** | 25 / 50 / 75 / 100% of the panel's range | 75% |
| **Dim at night** | Holds 10% from 22:00 to 07:00 in the host's time zone (Settings → General → Day boundaries). The window and level are fixed, not settings | off |
| **Celebrate quota resets** | The confetti burst below, and the remote test command | on |
| **Boot animation** | The four-second title sequence on power-up. Off, the board goes straight to the amber checklist | on |
| **Pages** | Which of Vercel, Git and Local servers the BOOT button cycles through. A page also needs its source on under Integrations | all on |

The pane also shows what the board last reported: firmware stamp
(`build.commit`, see `firmware/version.py`), transport and how long ago. A
board that has never polled this host leaves that section empty, and a host
that predates the pane makes it read-only.

Rings/Pace and the lower pane are **not** in the pane. They stay board-side
gestures (hold a slot, tap the header) so no setting has two owners.

`PANEL_BRIGHTNESS` in `firmware/src/config.h` is now only the first-boot level
for a board that has never been told anything (see `config_example.h`; panel
units 0-255, default 200).

## Token

The board uses the **host token** (`~/.headroom/token`), same as any generic
LAN client. Do not paste the iPhone **mobile token**. See
[setup.md](setup.md#tokens-host-vs-mobile).

## Troubleshooting

| Symptom | Fix |
|---|---|
| **NO HOST** on the panel | The board names the failing half — SSID, address, token, why. `pio device monitor` prints the same plus `curl` checks for the Mac |
| Flash fights the cable | Host LaunchAgent holds the port — bootout / flash / bootstrap as above, or use `./scripts/flash-esp32.sh` which refuses when busy |
| Green fringe / black panel | Expander address or bring-up order — see Supported board |

More: [troubleshooting.md](troubleshooting.md).

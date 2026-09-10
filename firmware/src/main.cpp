// headroom — Claude / Codex / Cursor / Vercel / Git desk gadget.
// Waveshare ESP32-S3 Touch AMOLED: 1.8 (368×448 SH8601), 2.16 (480×480
// CO5300), or 1.75 round (466×466 CO5300). Polls the host server's /usage
// endpoint over Wi-Fi and renders dashboards on the AMOLED. Headroom is home:
// tap a provider to open detail; tap (or BOOT / long-press) to return home.
//
// Bring-up order matters: own the I2C bus (one Wire.begin), bring up the
// AXP2101 rails, release panel/touch reset, THEN start the panel driver.
// On the 1.8 the resets go through a TCA9554; on the 2.16 they are GPIOs.

#include <Arduino.h>
#include <math.h>
#include <string.h>
#include <time.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiMulti.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <esp_heap_caps.h>
#include <esp_task_wdt.h>
#include <esp_system.h>
#include <Preferences.h>
#include <Arduino_GFX_Library.h>
#define XPOWERS_CHIP_AXP2101
#include <XPowersLib.h>
#if defined(BOARD_WS_AMOLED_216)
// Waveshare Touch tutorial + 05_LVGL_Widgets: SensorLib ≥0.4.1, TouchDrv.hpp.
#include <TouchDrv.hpp>
#endif

#include "pin_config.h"
#include "boot_max.h"  // generated — see scripts/render_esp32_boot.py
#include "provider_marks.h"
#include "config.h"   // copy config_example.h -> config.h
#include "wifi_provisioning.h"

// Older config.h files predate these — keep them building.
#ifndef HOST_TOKEN
#define HOST_TOKEN ""
#endif
#ifndef OTA_HOSTNAME
#define OTA_HOSTNAME "headroom"
#endif
#ifndef OTA_PASSWORD
#define OTA_PASSWORD ""
#endif
// Panel brightness. One level, all day and all night. Override in config.h.
#ifndef PANEL_BRIGHTNESS
#define PANEL_BRIGHTNESS 200
#endif

// Reboot if a single loop pass wedges this long (stuck I2C, wedged HTTP stack).
// Generous: a cold Wi-Fi associate plus a USB sync is legitimately slow.
static const uint32_t WDT_TIMEOUT_S = 30;

// ---------------- Display ----------------
#if defined(BOARD_DIRECT_CO5300)
// CO5300 square canvases draw 1:1 into PSRAM. The 2.16 panel needs a MADCTL
// correction after begin(); the 1.75 round glass is already in native scan.
// Full-frame blit only — per-row QSPI writes leave this panel black.
static Arduino_DataBus *bus = new Arduino_ESP32QSPI(
    LCD_CS, LCD_SCLK, LCD_SDIO0, LCD_SDIO1, LCD_SDIO2, LCD_SDIO3);
#if defined(BOARD_WS_AMOLED_175_ROUND)
static Arduino_CO5300 *panel = new Arduino_CO5300(
    bus, LCD_RST, 0 /* rotation */, LCD_WIDTH, LCD_HEIGHT,
    LCD_COL_OFFSET, 0, 0, 0);
#else
static Arduino_CO5300 *panel = new Arduino_CO5300(
    bus, LCD_RST, 0 /* rotation */, LCD_WIDTH, LCD_HEIGHT);
#endif
#else
// SH8601 has no hardware rotation. Panel stays native portrait (368×448).
// We draw into a logical landscape PSRAM canvas (448×368) with no per-pixel
// rotate, then rotate once on flush → native. Use 1 for CW if you flip.
static Arduino_DataBus *bus = new Arduino_ESP32QSPI(
    LCD_CS, LCD_SCLK, LCD_SDIO0, LCD_SDIO1, LCD_SDIO2, LCD_SDIO3);
static Arduino_SH8601 *panel = new Arduino_SH8601(
    bus, -1 /* RST via expander, not a GPIO */, 0 /* rotation */,
    LCD_WIDTH, LCD_HEIGHT);
#endif

// Shared palette (RGB565) — early so canvas flush can seal with COL_BG.
static const uint16_t COL_BG     = RGB565(0, 0, 0);        // true OLED black
static const uint16_t COL_CLAUDE = RGB565(217, 119, 87);   // terracotta
static const uint16_t COL_OPENAI = RGB565(16, 163, 127);   // OpenAI / ChatGPT green
static const uint16_t COL_CURSOR = RGB565(120, 155, 200);  // faded blue (Cursor)
static const uint16_t COL_VERCEL = RGB565(240, 238, 234);  // Vercel-ish white
static const uint16_t COL_GIT    = RGB565(155, 85, 200);   // brighter GitHub purple
static const uint16_t COL_LOCAL  = RGB565(70, 175, 165);   // teal (local servers)
static const uint16_t COL_WHITE  = RGB565(240, 238, 234);
static const uint16_t COL_BLACK  = RGB565(0, 0, 0);
static const uint16_t COL_DIM    = RGB565(120, 116, 110);
static const uint16_t COL_BAR    = RGB565(42, 40, 38);     // unfilled quota track
static const uint16_t COL_GREEN  = RGB565(95, 155, 115);   // soft sage
static const uint16_t COL_AMBER  = RGB565(195, 155, 85);    // soft amber — in flight
static const uint16_t COL_ORANGE = RGB565(217, 138, 60);    // warn — actionable
static const uint16_t COL_RED    = RGB565(175, 105, 100);   // soft dusty red
// Boot / diagnostic chrome. Carries the splash palette rather than the old
// amber phosphor, so the ROM checklist reads as the same machine that just
// played the intro instead of a different one booting after it.
static const uint16_t COL_CRT    = RGB565(0, 214, 236);    // cyan phosphor
static const uint16_t COL_CRT_DIM= RGB565(150, 40, 120);   // magenta, receding
static const uint16_t COL_CRT_YELLOW = RGB565(236, 214, 0); // process yellow
static const uint16_t COL_CRT_BG = RGB565(6, 4, 14);       // = the splash bg
static const uint16_t COL_CRT_HDR= RGB565(22, 14, 44);     // boot header bar
static const uint16_t COL_CRT_SCAN= RGB565(12, 8, 26);     // scanlines

#if defined(BOARD_DIRECT_CO5300)
// Square canvas: logical == native. No software rotate, no edge seal. The
// physical glass can still be circular; BOARD_ROUND_UI owns that safe area.
static const int16_t LOG_W = LCD_WIDTH;
static const int16_t LOG_H = LCD_HEIGHT;

static void sealNativeEdges(uint16_t color) { (void)color; }

class LandscapeCanvas : public Arduino_Canvas {
  uint16_t _bg = COL_BG;

public:
  LandscapeCanvas(Arduino_G *out)
      : Arduino_Canvas(LOG_W, LOG_H, out, 0, 0, 0) {}

  bool begin(int32_t speed = GFX_NOT_DEFINED) override {
    if ((speed != GFX_SKIP_OUTPUT_BEGIN) && _output) {
      if (!_output->begin(speed)) return false;
    }
    const size_t logBytes = (size_t)LOG_W * LOG_H * sizeof(uint16_t);
    if (!_framebuffer) {
      _framebuffer = (uint16_t *)ps_malloc(logBytes);
      if (!_framebuffer) return false;
      memset(_framebuffer, 0, logBytes);
    }
    return true;
  }

  void clear(uint16_t color) {
    if (!_framebuffer) return;
    _bg = color;
    uint32_t c32 = ((uint32_t)color << 16) | color;
    uint32_t *p = (uint32_t *)_framebuffer;
    size_t n = ((size_t)LOG_W * LOG_H) / 2;
    while (n--) *p++ = c32;
  }

  void flush(bool force_flush = false) override {
    (void)force_flush;
    if (!_framebuffer || !_output) return;
    // One-shot full-frame blit — CO5300 goes black on per-row QSPI writes.
    _output->draw16bitRGBBitmap(0, 0, _framebuffer, LCD_WIDTH, LCD_HEIGHT);
  }

  void tearRows(int16_t y, int16_t h, int16_t dx, uint16_t fill) {
    if (!_framebuffer || dx == 0) return;
    if (y < 0) { h = (int16_t)(h + y); y = 0; }
    if (y + h > LOG_H) h = (int16_t)(LOG_H - y);
    if (h <= 0) return;
    const int16_t n = (int16_t)(dx < 0 ? -dx : dx);
    if (n >= LOG_W) return;
    const size_t keep = (size_t)(LOG_W - n) * sizeof(uint16_t);
    for (int16_t row = y; row < y + h; row++) {
      uint16_t *p = _framebuffer + (int32_t)row * LOG_W;
      if (dx > 0) {
        memmove(p + n, p, keep);
        for (int16_t i = 0; i < n; i++) p[i] = fill;
      } else {
        memmove(p, p + n, keep);
        for (int16_t i = (int16_t)(LOG_W - n); i < LOG_W; i++) p[i] = fill;
      }
    }
  }

  void flushLogicalRect(int16_t lx, int16_t ly, int16_t lw, int16_t lh) {
    // CO5300: never stream partial rows — fall back to a full flush.
    (void)lx; (void)ly; (void)lw; (void)lh;
    flush();
  }
};
static LandscapeCanvas *gfx = new LandscapeCanvas(panel);
#else
// Green fringe sits on the native right edge (logical bottom at rotation 3).
// Paint over it in-panel after every blit. Also blank a few GRAM columns past
// LCD_WIDTH in case the panel scans slightly beyond our framebuffer.
//
// The seal costs the bottom PANEL_SEAL_ROWS logical rows of every frame: they
// carry background instead of picture, so they sit flat while the rest of the
// frame animates, and they push the whole layout up against the top inset.
//
// Now zero. The library's own preset for this board declares 368x448 with all
// four offsets at 0, so anything the panel scans past our framebuffer lives
// beyond column LCD_WIDTH — which the GRAM wipe below covers without spending
// a row of picture. If a green fringe comes back along the bottom edge, that
// is evidence the visible window really does extend past our last column, and
// the fix is col_offset1 on the Arduino_SH8601 constructor, not painting over
// our own pixels.
static const int16_t PANEL_SEAL_ROWS = 0;

static void sealNativeEdges(uint16_t color) {
  const int16_t fringe = PANEL_SEAL_ROWS;
  if (fringe > 0) {
    panel->fillRect(LCD_WIDTH - fringe, 0, fringe, LCD_HEIGHT, color);
  }

  const int16_t extra = 16;
  bus->beginWrite();
  bus->writeC8D16D16(0x2A, LCD_WIDTH, LCD_WIDTH + extra - 1);
  bus->writeC8D16D16(0x2B, 0, LCD_HEIGHT - 1);
  bus->writeCommand(0x2C);
  bus->writeRepeat(color, (uint32_t)extra * LCD_HEIGHT);

  // Put the window back where the driver thinks it is. Arduino_SH8601 caches
  // the last address window and skips re-sending CASET/PASET when the next
  // call matches, so a raw poke like the one above leaves the panel addressed
  // to a 16-column strip while the driver still believes it is full-frame —
  // and the next blit pours a whole frame into that strip. This used to be
  // covered by accident: the fillRect above ran through the driver and
  // invalidated the cache, so dropping it blanked the screen.
  bus->writeC8D16D16(0x2A, 0, LCD_WIDTH - 1);
  bus->writeC8D16D16(0x2B, 0, LCD_HEIGHT - 1);
  bus->endWrite();
}

// Logical landscape (448×368). Drawing is axis-aligned into PSRAM; we only pay
// for 90° CCW rotation when pushing pixels to the native 368×448 panel.
static const int16_t LOG_W = LCD_HEIGHT;  // 448
static const int16_t LOG_H = LCD_WIDTH;   // 368

// Rotate logical → native (matches former Arduino_Canvas rotation 3):
//   native(nx, ny) = logical(lx = LOG_W-1-ny, ly = nx)
//
// Done in tiles rather than whole rows. The source walk strides by LOG_W, so a
// full-row pass touches 368 separate cache lines and evicts each before the
// next row reuses it — every one of the 164k pixels becomes a PSRAM round
// trip. A 32×32 tile keeps its ~2KB of source lines resident while 32 rows
// drain out of them.
static const int16_t ROT_TILE = 32;

static void rotateLogicalToNative(const uint16_t *src, uint16_t *dst) {
  for (int16_t ny0 = 0; ny0 < LCD_HEIGHT; ny0 += ROT_TILE) {
    const int16_t nyEnd =
        (int16_t)((ny0 + ROT_TILE < LCD_HEIGHT) ? ny0 + ROT_TILE : LCD_HEIGHT);
    for (int16_t nx0 = 0; nx0 < LCD_WIDTH; nx0 += ROT_TILE) {
      const int16_t nxEnd =
          (int16_t)((nx0 + ROT_TILE < LCD_WIDTH) ? nx0 + ROT_TILE : LCD_WIDTH);
      for (int16_t ny = ny0; ny < nyEnd; ny++) {
        const uint16_t *s =
            src + ((LOG_W - 1) - ny) + (int32_t)nx0 * LOG_W;
        uint16_t *d = dst + (int32_t)ny * LCD_WIDTH + nx0;
        for (int16_t nx = nx0; nx < nxEnd; nx++) {
          *d++ = *s;
          s += LOG_W;
        }
      }
    }
  }
}

// Canvas = logical framebuffer. _native is rotated for panel flush.
class LandscapeCanvas : public Arduino_Canvas {
  uint16_t *_native = nullptr;
  uint16_t _bg = COL_BG;   // last clear() colour — what the edge seal matches

public:
  LandscapeCanvas(Arduino_G *out)
      : Arduino_Canvas(LOG_W, LOG_H, out, 0, 0, 0 /* no per-pixel rotate */) {}

  bool begin(int32_t speed = GFX_NOT_DEFINED) override {
    if ((speed != GFX_SKIP_OUTPUT_BEGIN) && _output) {
      if (!_output->begin(speed)) return false;
    }
    const size_t logBytes = (size_t)LOG_W * LOG_H * sizeof(uint16_t);
    const size_t natBytes = (size_t)LCD_WIDTH * LCD_HEIGHT * sizeof(uint16_t);
    if (!_framebuffer) {
      _framebuffer = (uint16_t *)ps_malloc(logBytes);
      if (!_framebuffer) return false;
      memset(_framebuffer, 0, logBytes);
    }
    if (!_native) {
      _native = (uint16_t *)ps_malloc(natBytes);
      if (!_native) return false;
      memset(_native, 0, natBytes);
    }
    return true;
  }

  void clear(uint16_t color) {
    if (!_framebuffer) return;
    // Remembered so the edge seal can match the frame it is sealing. Sealing
    // in a fixed colour put a warm strip along the bottom of every cold-blue
    // boot screen, repainted each splash frame while the picture above it
    // rolled — it read as the bottom of the panel misbehaving.
    _bg = color;
    uint32_t c32 = ((uint32_t)color << 16) | color;
    uint32_t *p = (uint32_t *)_framebuffer;
    size_t n = ((size_t)LOG_W * LOG_H) / 2;
    while (n--) *p++ = c32;
  }

  void flush(bool force_flush = false) override {
    (void)force_flush;
    if (!_framebuffer || !_native || !_output) return;
    rotateLogicalToNative(_framebuffer, _native);
    _output->draw16bitRGBBitmap(0, 0, _native, LCD_WIDTH, LCD_HEIGHT);
    sealNativeEdges(_bg);
  }

  // Slide a band of logical rows sideways in place — the boot-splash stutter.
  // Cheaper than redrawing the band offset, and it tears the backdrop along
  // with the sprite, which is what makes it read as a broken signal.
  void tearRows(int16_t y, int16_t h, int16_t dx, uint16_t fill) {
    if (!_framebuffer || dx == 0) return;
    if (y < 0) { h = (int16_t)(h + y); y = 0; }
    if (y + h > LOG_H) h = (int16_t)(LOG_H - y);
    if (h <= 0) return;
    const int16_t n = (int16_t)(dx < 0 ? -dx : dx);
    if (n >= LOG_W) return;
    const size_t keep = (size_t)(LOG_W - n) * sizeof(uint16_t);
    for (int16_t row = y; row < y + h; row++) {
      uint16_t *p = _framebuffer + (int32_t)row * LOG_W;
      if (dx > 0) {
        memmove(p + n, p, keep);
        for (int16_t i = 0; i < n; i++) p[i] = fill;
      } else {
        memmove(p, p + n, keep);
        for (int16_t i = (int16_t)(LOG_W - n); i < LOG_W; i++) p[i] = fill;
      }
    }
  }

  // Push a logical axis-aligned dirty rect without a full frame.
  void flushLogicalRect(int16_t lx, int16_t ly, int16_t lw, int16_t lh) {
    if (!_framebuffer || !_output || lw <= 0 || lh <= 0) return;
    if (lx < 0) { lw += lx; lx = 0; }
    if (ly < 0) { lh += ly; ly = 0; }
    if (lx + lw > LOG_W) lw = (int16_t)(LOG_W - lx);
    if (ly + lh > LOG_H) lh = (int16_t)(LOG_H - ly);
    if (lw <= 0 || lh <= 0) return;

    const int16_t nx0 = ly;
    const int16_t nw = lh;
    const int16_t ny0 = (int16_t)((LOG_W - 1) - (lx + lw - 1));
    const int16_t nh = lw;

    uint16_t line[LCD_WIDTH];
    for (int16_t row = 0; row < nh; row++) {
      int16_t ny = (int16_t)(ny0 + row);
      int16_t lxCol = (int16_t)((LOG_W - 1) - ny);
      for (int16_t col = 0; col < nw; col++) {
        int16_t nx = (int16_t)(nx0 + col);
        line[col] = _framebuffer[(int32_t)nx * LOG_W + lxCol];
      }
      _output->draw16bitRGBBitmap(nx0, ny, line, nw, 1);
    }
  }
};
static LandscapeCanvas *gfx = new LandscapeCanvas(panel);
#endif

static void present() {
  gfx->flush();
}

static XPowersPMU PMU;

// Vendor "manufacturer page" unlock + display-on sequence for this SH8601
// panel batch. Arduino_GFX's generic SH8601 init table doesn't include this
// unlock, so panel->begin() "succeeds" (the QSPI writes go out fine) but the
// panel never actually lights up. Sequence + register values confirmed
// against a working init for this exact board; addresses re-sized for our
// 368x448 panel (367 = width-1, 447 = height-1).
static void sh8601VendorInit() {
  bus->beginWrite();
  bus->writeCommand(0x11);              // SLPOUT
  bus->endWrite();
  delay(120);

  bus->beginWrite();
  bus->writeC8D8(0xFE, 0x20);           // page select: MFR
  bus->writeC8D8(0x19, 0x10);
  bus->writeC8D8(0x1C, 0xA0);
  bus->writeC8D8(0xFE, 0x00);           // page select: USER
  bus->writeC8D8(0xC4, 0x80);
  bus->writeC8D8(0x3A, 0x55);           // pixel format: RGB565
  bus->writeC8D8(0x35, 0x00);           // tearing effect line
  // MADCTL: 0x00 = native portrait, no row/col exchange. The reference repo's
  // 0x30 sets the MV (row/col swap) bit, tuned for its square 480x480 panel —
  // on our rectangular 368x448 panel that swap misaligns the col/row address
  // windows below, causing wraparound (the green edge bar + bottom overflow).
  // If the image comes up mirrored or upside-down, try 0x40 (mirror X),
  // 0x80 (mirror Y), or 0xC0 (180 deg) instead.
  bus->writeC8D8(0x36, 0x00);
  bus->writeC8D8(0x53, 0x20);           // CABC control
  bus->writeC8D8(0x51, 0xFF);           // brightness: max (panel level set later)
  bus->writeC8D8(0x63, 0xFF);

  uint8_t col[4] = {0x00, 0x00, (uint8_t)((LCD_WIDTH - 1) >> 8),
                     (uint8_t)((LCD_WIDTH - 1) & 0xFF)};
  bus->writeCommand(0x2A);
  bus->writeBytes(col, 4);

  uint8_t row[4] = {0x00, 0x00, (uint8_t)((LCD_HEIGHT - 1) >> 8),
                     (uint8_t)((LCD_HEIGHT - 1) & 0xFF)};
  bus->writeCommand(0x2B);
  bus->writeBytes(row, 4);

  bus->writeCommand(0x29);              // DISPON
  bus->endWrite();
  delay(50);
}

// ---------------- Data ----------------
// One meter: whatever the host called it, how full it is, where an even burn
// would have it, and when it resets. The board no longer knows that Claude
// has a session and Cursor has an API pool — it draws the rows it is sent,
// in the order it is sent them.
static const uint8_t MAX_POOLS = 3;   // mirrors device_view.MAX_POOLS
struct PoolRow {
  String title;
  float pct = -1;
  float pace = -1;
  String resets;
};

struct ProviderQuota {
  bool ok = false;
  String plan;
  uint8_t n = 0;
  PoolRow pools[MAX_POOLS];
  // One optional extra line under the meters — Codex reset credits today,
  // composed host-side so the board owns no provider's vocabulary.
  String note;
  String note2;    // right-aligned on the same line (credit expiries)
};

// Burndown for one provider: the actual remaining-% curve plus where the
// current pace lands. The ideal line is a straight run from (t0,100) to
// (t1,0), so the host never sends it — we derive it here.
// Cursor may carry a second series (API) overlaid on the same axis.
static const uint8_t MAX_BURN_PTS = 24;   // mirrors device_view.MAX_BURNDOWN_POINTS
static const uint8_t MAX_HIST_PTS = 16;   // mirrors device_view.MAX_HISTORY_POINTS
static const uint8_t MAX_GRANTS = 4;      // mirrors device_view.MAX_GRANT_MARKS
// How far a provider page's plot may reach back past its own window to show
// the run a grant cut short, as a percentage of the window. A stub on purpose:
// the window is what that chart is about, so history gets only enough room to
// show the drop before the grant. Mirrors BurndownChartAxis.historyFraction
// (0.15) in Shared/BurndownChartMath.swift — change both together.
static const uint32_t HISTORY_REACH_PCT = 15;
struct Burndown {
  bool ok = false;
  uint32_t t0 = 0, t1 = 0;
  uint8_t n = 0;
  uint32_t t[MAX_BURN_PTS];
  float remaining[MAX_BURN_PTS];
  uint8_t projN = 0;
  uint32_t projT[2];
  float projR[2];
  // Windows already spent, drawn faint behind the live curve. `pts` stops at
  // t0, so without this a provider whose window just rolled draws a single
  // point — which is nothing — and the desk looks like it lost the week.
  // Split at `grantT` before stroking: this series climbs at every reset.
  uint8_t histN = 0;
  uint32_t histT[MAX_HIST_PTS];
  float histR[MAX_HIST_PTS];
  // When a grant handed a window back, and by how much. Oldest first.
  uint8_t grantN = 0;
  uint32_t grantT[MAX_GRANTS];
  float grantPct[MAX_GRANTS];
  bool warn = false;       // pace runs out before the window resets
  bool exhausted = false;
  bool estimated = false;  // projection from token history, not from samples
  // "Runs out tomorrow 10:26" — the same phrase the menu bar shows, so the
  // desk and the Mac answer "do I make it" with the same words. Host-supplied
  // rather than assembled here: one vocabulary, one place to change it.
  String verdict;
  // Optional overlay (Cursor API). n2==0 means absent.
  uint8_t n2 = 0;
  uint32_t t2[MAX_BURN_PTS];
  float remaining2[MAX_BURN_PTS];
  uint8_t projN2 = 0;
  uint32_t projT2[2];
  float projR2[2];
  bool warn2 = false;
  bool exhausted2 = false;
  bool estimated2 = false;
};

// Slots 0-2 keep the page numbers the fixed Claude / Codex / Cursor pages
// had, so a saved page and every touch target survive the change to
// host-driven providers.
enum Page : uint8_t {
  PAGE_GLANCE = 0,
  PAGE_SLOT0  = 1,
  PAGE_SLOT1  = 2,
  PAGE_SLOT2  = 3,
  PAGE_VERCEL = 4,
  PAGE_GIT    = 5,
  PAGE_LOCAL  = 6,
  PAGE_COUNT  = 7
};

static const uint8_t MAX_SLOTS = 3;   // mirrors device_view.MAX_PROVIDERS
static const uint8_t MAX_ACTIVITY_DAYS = 162;  // mirrors device_view.py
static const uint8_t MAX_DAILY_BURN_DAYS = 7;  // mirrors device_view.py

static inline bool isSlotPage(Page p) {
  return p >= PAGE_SLOT0 && p <= PAGE_SLOT2;
}

static inline uint8_t slotOf(Page p) { return (uint8_t)(p - PAGE_SLOT0); }

static inline Page slotPage(uint8_t i) { return (Page)(PAGE_SLOT0 + i); }

static const uint8_t MAX_DEPLOYS = 6;
static const uint8_t MAX_COMMITS = 6;
static const uint8_t MAX_SERVERS = 6;

struct DeployRow {
  String project;
  String status;   // ready / building / error
  String target;   // production / preview / …
  String ago;
  String branch;
};

struct CommitRow {
  String repo;
  String subject;
  String ago;
  String branch;
};

struct ServerRow {
  String name;
  int port = 0;
  String cmd;
};

// Build identity, stamped by firmware/version.py. The counter moves on every
// build, not every commit, so a rebuild of uncommitted work is still
// distinguishable — which is the case where "did that actually flash?" is
// hardest to answer. Sent to the host on every poll so the question has a
// reading rather than an inference.
#ifndef FW_BUILD
#define FW_BUILD 0
#endif
#ifndef FW_VERSION
#define FW_VERSION "unversioned"
#endif

static const String &fwVersion() {
  static const String v = FW_VERSION;
  return v;
}

static String updatedZ = "";
static bool haveData = false;
static bool hostOk = false;   // last /usage fetch succeeded
// The three providers on the glance, as the host picked them: pinned order,
// enabled only, in whatever color Mac Settings says. `slotN` can be 0-3 —
// turn every coding provider off and the rings go with them.
struct ProviderSlot {
  String id;
  String title;
  uint16_t accent = COL_DIM;
  ProviderQuota q;
  Burndown burn;
};
static ProviderSlot slots[MAX_SLOTS];
static uint8_t slotN = 0;
// Compact mixed-source activity history. The host sends a recent half-year of
// daily levels for the board; native details remain on the Mac/iPhone payload.
struct ActivityHistory {
  bool ok = false;
  String start;
  uint8_t startWeekday = 0;  // Monday = 0
  uint8_t n = 0;
  uint8_t levels[MAX_ACTIVITY_DAYS];
  uint16_t activeDays = 0;
  uint16_t currentStreak = 0;
};
static ActivityHistory activityHistory;

// Seven-day mixed-source burn strip. Values are percentage points, so they
// stay source-attributed in the stack without pretending providers share a
// dollar or token unit.
struct DailyBurnDay {
  String label;
  float burns[MAX_SLOTS] = {};
  float total = 0;
};
static DailyBurnDay dailyBurnDays[MAX_DAILY_BURN_DAYS];
static uint8_t dailyBurnN = 0;

// Spend keeps the app's existing semantics: estimated local token value, not
// a fabricated sum of provider billing controls and quota percentages.
struct SpendSummary {
  bool ok = false;
  bool estimated = true;
  float today = 0;
  float total = 0;
  float avg = 0;
  uint16_t days = 0;
};
static SpendSummary spendSummary;
// False when the payload carried no `providers` key at all — a host too old
// to send them. Distinct from "you turned every provider off", because the
// fix is different and the board is the only place either shows.
static bool providersSeen = false;
static bool vercelOk = false;
static String vercelTeam = "";
static uint8_t vercelN = 0;
static DeployRow vercelRows[MAX_DEPLOYS];
static bool gitOk = false;
static uint8_t gitN = 0;
static CommitRow gitRows[MAX_COMMITS];
static bool localOk = false;
static String localHost = "";
static uint8_t localN = 0;
static ServerRow localRows[MAX_SERVERS];
static Page page = PAGE_GLANCE;

// Home's lower half follows the Mac dashboard: mixed daily burn, activity
// history, estimated spend, then burndown. Vercel / Git / Local remain detail
// pages. Tap the header band to switch; the choice survives reboots in NVS so
// the board comes back the way it was left.
enum HomeMode : uint8_t {
  HOME_DAILY_BURN = 0,
  HOME_HISTORY = 1,
  HOME_SPEND = 2,
  HOME_BURNDOWN = 3,
};
static HomeMode homeMode = HOME_DAILY_BURN;

// Home's upper half has the same two readings the macOS menu-bar icon has.
// Rings paint what each pool has used, with a dot at even spend. Pace drops
// the arc and keeps only the distance from that dot — one rail across the
// three slots, one mark per provider. Long-press the ring band to switch; the
// choice survives reboots in NVS.
enum GlanceStyle : uint8_t {
  GLANCE_RINGS = 0,
  GLANCE_PACE = 1,
};
static GlanceStyle glanceStyle = GLANCE_RINGS;

static Preferences prefs;
static const char *PREFS_NS = "headroom";
// Versioned so the old Burndown/Activity/History ordering cannot silently
// restore the wrong card after this dashboard reshuffle.
static const char *PREF_HOME_MODE = "home_pane_v2";
static const char *PREF_GLANCE_STYLE = "glance_style";
static const char *PREF_EFFECT_ID = "effect_id";
static uint32_t lastEffectId = 0;
static bool pendingCelebration = false;
static uint16_t pendingCelebrationAccent = COL_DIM;

// The primary burndown window start is stable between polls and changes when
// the provider grants or schedules a new window. Keep one baseline per slot so
// the visual only fires after the first payload, never at boot.
static String resetBaselineIds[MAX_SLOTS];
static uint32_t resetBaselineStarts[MAX_SLOTS] = {};
static bool resetBaselineReady[MAX_SLOTS] = {};

// Must match docs/glossary.md / Shared/HeadroomCopy.swift.
static const char *LABEL_BURNDOWN = "Burndown";
static const char *LABEL_DAILY_BURN = "Daily burn";
static const char *LABEL_HISTORY = "History";
static const char *LABEL_SPEND = "Spend";
static const char *LABEL_COLLECTING_HISTORY = "Collecting history";
static const char *LABEL_NO_DATA = "no data";

static const char *homeModeName(HomeMode m) {
  if (m == HOME_HISTORY) return LABEL_HISTORY;
  if (m == HOME_SPEND) return LABEL_SPEND;
  if (m == HOME_BURNDOWN) return LABEL_BURNDOWN;
  return LABEL_DAILY_BURN;
}

static void homeModeLoad() {
  // Read-only open on a namespace that doesn't exist yet just fails — default.
  if (!prefs.begin(PREFS_NS, true)) return;
  const uint8_t saved = prefs.getUChar(PREF_HOME_MODE, HOME_DAILY_BURN);
  homeMode = saved <= HOME_BURNDOWN ? (HomeMode)saved : HOME_DAILY_BURN;
  prefs.end();
}

static void homeModeSave() {
  if (!prefs.begin(PREFS_NS, false)) return;
  prefs.putUChar(PREF_HOME_MODE, (uint8_t)homeMode);
  prefs.end();
}

static void glanceStyleLoad() {
  if (!prefs.begin(PREFS_NS, true)) return;
  const uint8_t saved = prefs.getUChar(PREF_GLANCE_STYLE, GLANCE_RINGS);
  glanceStyle = saved <= GLANCE_PACE ? (GlanceStyle)saved : GLANCE_RINGS;
  prefs.end();
}

static void glanceStyleSave() {
  if (!prefs.begin(PREFS_NS, false)) return;
  prefs.putUChar(PREF_GLANCE_STYLE, (uint8_t)glanceStyle);
  prefs.end();
}

static void effectLoad() {
  if (!prefs.begin(PREFS_NS, true)) return;
  lastEffectId = prefs.getULong(PREF_EFFECT_ID, 0);
  prefs.end();
}

static void effectSave() {
  if (!prefs.begin(PREFS_NS, false)) return;
  prefs.putULong(PREF_EFFECT_ID, lastEffectId);
  prefs.end();
}

// Desk display settings. Host-owned: Mac Settings → Desk display writes them,
// the host ships effective values in `/usage?view=device` → `display`, and the
// board applies what arrives and mirrors it to NVS so a cold boot without the
// host comes up the same way. PANEL_BRIGHTNESS (config.h) is only what a board
// that has never been told anything uses. A missing block is an older host:
// keep what NVS has. Rings/Pace and the lower pane stay board-side gestures on
// purpose, so no setting has two owners.
static const char *PREF_DISP_BRIGHT = "disp_bright";
static const char *PREF_DISP_CELEB = "disp_celeb";
static const char *PREF_DISP_SPLASH = "disp_splash";
static const char *PREF_DISP_PAGES = "disp_pages";
static const uint8_t DISP_PAGE_VERCEL = 1 << 0;
static const uint8_t DISP_PAGE_GIT = 1 << 1;
static const uint8_t DISP_PAGE_LOCAL = 1 << 2;
static const uint8_t DISP_PAGES_ALL =
    DISP_PAGE_VERCEL | DISP_PAGE_GIT | DISP_PAGE_LOCAL;
static uint8_t displayBrightness = PANEL_BRIGHTNESS;
static bool displayCelebrate = true;
static bool displayBootSplash = true;
static uint8_t displayPages = DISP_PAGES_ALL;

static void displayLoad() {
  if (!prefs.begin(PREFS_NS, true)) return;
  displayBrightness = prefs.getUChar(PREF_DISP_BRIGHT, PANEL_BRIGHTNESS);
  displayCelebrate = prefs.getBool(PREF_DISP_CELEB, true);
  displayBootSplash = prefs.getBool(PREF_DISP_SPLASH, true);
  displayPages = prefs.getUChar(PREF_DISP_PAGES, DISP_PAGES_ALL) & DISP_PAGES_ALL;
  prefs.end();
  if (displayBrightness == 0) displayBrightness = PANEL_BRIGHTNESS;
}

static void displaySave() {
  if (!prefs.begin(PREFS_NS, false)) return;
  prefs.putUChar(PREF_DISP_BRIGHT, displayBrightness);
  prefs.putBool(PREF_DISP_CELEB, displayCelebrate);
  prefs.putBool(PREF_DISP_SPLASH, displayBootSplash);
  prefs.putUChar(PREF_DISP_PAGES, displayPages);
  prefs.end();
}

static inline bool displayPageShown(uint8_t bit) {
  return (displayPages & bit) != 0;
}

// Apply the host's `display` block. Only a change touches NVS or the panel:
// this runs every poll, and neither is free.
static void applyDisplayDoc(JsonObject disp) {
  if (disp.isNull()) return;
  bool changed = false;
  const int bright = disp["brightness"] | 0;
  if (bright > 0 && bright <= 255 && (uint8_t)bright != displayBrightness) {
    displayBrightness = (uint8_t)bright;
    panel->setBrightness(displayBrightness);
    changed = true;
  }
  const bool celebrate = disp["celebrate_resets"] | true;
  if (celebrate != displayCelebrate) {
    displayCelebrate = celebrate;
    changed = true;
  }
  const bool splash = disp["boot_splash"] | true;
  if (splash != displayBootSplash) {
    displayBootSplash = splash;
    changed = true;
  }
  JsonObject pages = disp["pages"].as<JsonObject>();
  if (!pages.isNull()) {
    uint8_t mask = 0;
    if (pages["vercel"] | true) mask |= DISP_PAGE_VERCEL;
    if (pages["git"] | true) mask |= DISP_PAGE_GIT;
    if (pages["local"] | true) mask |= DISP_PAGE_LOCAL;
    if (mask != displayPages) {
      displayPages = mask;
      changed = true;
    }
  }
  if (changed) {
    displaySave();
    Serial.printf("display: brightness %u celebrate %d splash %d pages 0x%02x\n",
                  displayBrightness, (int)displayCelebrate,
                  (int)displayBootSplash, displayPages);
  }
}

// Shared Sources panel state (same payload Mac Settings reads/writes).
static const uint8_t MAX_SOURCES = 8;
struct SourceRow {
  String id;
  String title;
  bool enabled = true;
  bool ok = false;
  bool stale = false;
  // The host's own login-is-dead flag. Absent on older hosts, which is why it
  // defaults false rather than being inferred from `ok` — a host that cannot
  // say must not be made to look like it said no.
  bool authRequired = false;
  // Age of *these numbers*, which is not the age of the last fetch. The board
  // can be talking to the Mac every ten seconds while the Mac has been unable
  // to refresh a source since last night.
  int32_t ageS = -1;
};
static uint8_t sourceN = 0;
static SourceRow sourceRows[MAX_SOURCES];
static bool sourceEnabled(const char *id) {
  for (uint8_t i = 0; i < sourceN; i++) {
    if (sourceRows[i].id.equals(id)) return sourceRows[i].enabled;
  }
  return true;  // older hosts without sources[] → keep showing pages
}

// Oldest frozen reading on screen, in seconds, or -1 when everything the
// board is drawing is current. Only enabled rows count: a source switched off
// in Settings is not on any page, so its age is not a lie anyone can read.
static int32_t sourcesWorstStaleS() {
  int32_t worst = -1;
  for (uint8_t i = 0; i < sourceN; i++) {
    const SourceRow &r = sourceRows[i];
    if (!r.enabled || !r.stale) continue;
    if (r.ageS > worst) worst = r.ageS;
  }
  return worst;
}

// Whether any enabled source is behind a credential the Mac cannot use. The
// board cannot fix this and does not pretend to — it marks the reading and
// sends you to the Mac, which names the source and the command.
static bool sourcesNeedSignIn() {
  for (uint8_t i = 0; i < sourceN; i++) {
    if (sourceRows[i].enabled && sourceRows[i].authRequired) return true;
  }
  return false;
}

// ---------------- I2C helpers ----------------
#if defined(BOARD_TCA9554)
static uint8_t tcaAddr = TCA9554_ADDR;   // may be re-detected at boot
#endif

static bool i2cPresent(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

static void i2cScan() {
  Serial.print("I2C scan:");
  int n = 0;
  for (uint8_t a = 1; a < 127; a++)
    if (i2cPresent(a)) { Serial.printf(" 0x%02X", a); n++; }
  Serial.println(n ? "" : "  (NONE — check SDA=15/SCL=14 wiring)");
}

#if defined(BOARD_TCA9554)
// ---------------- TCA9554 (raw I2C) ----------------
static void tcaWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(tcaAddr);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

// Release the panel. On this board (per the working reference): expander pin 0
// = LCD reset, 1 = touch reset, 2 = DSI power-enable. Drive all three low, then
// high, so the display rail comes up and reset releases together.
static void panelReset() {
  tcaWrite(0x03, 0xF8);   // config: P0..P2 outputs, rest inputs
  tcaWrite(0x01, 0x00);   // low: assert reset + power off
  delay(30);
  tcaWrite(0x01, 0x07);   // high: release reset + enable DSI power
  delay(120);
}
#endif

// ---------------- Power ----------------
// Tracks whether begin() found the AXP2101. Rails and the power glyph both
// need it; a missing PMU leaves the panel dark, but the glyph still has to
// know not to poke a chip that never answered.
static bool pmuOk = false;

static void powerInit() {
  Wire.begin(IIC_SDA, IIC_SCL, 400000);   // own the bus once
  delay(50);
  i2cScan();

#if defined(BOARD_DIRECT_CO5300)
  // Direct-reset CO5300 boards need no TCA9554. LCD reset is handled by
  // Arduino_CO5300::begin(); touch reset is handled later by TouchDrvCST92xx.
#else
  // The TCA9554 straps at 0x20 on most units, 0x21 on some (repo issue #3).
  if (!i2cPresent(tcaAddr) && i2cPresent(0x21)) tcaAddr = 0x21;
  Serial.printf("TCA9554 @ 0x%02X %s\n", tcaAddr,
                i2cPresent(tcaAddr) ? "(ack)" : "(NO ACK — panel stays dark!)");
#endif

  pmuOk = PMU.begin(Wire, AXP2101_ADDR, IIC_SDA, IIC_SCL);
#if defined(BOARD_WS_AMOLED_175_ROUND)
  Serial.printf("AXP2101 begin: %s\n",
                pmuOk ? "ok" : "FAIL (power glyph unavailable)");
#else
  Serial.printf("AXP2101 begin: %s\n",
                pmuOk ? "ok" : "FAIL (display rail off!)");
#endif
  if (pmuOk) {
#if defined(BOARD_WS_AMOLED_175_ROUND)
    // Match the working round-board references: leave the already-working
    // rail configuration alone. Only enable battery telemetry and turn the
    // AXP2101 PEKEY into Headroom's second runtime button. A four-second hold
    // remains the hardware power-off gesture.
    PMU.enableBattDetection();
    PMU.enableBattVoltageMeasure();
    PMU.setPowerKeyPressOffTime(XPOWERS_POWEROFF_4S);
    PMU.enableLongPressShutdown();
    PMU.disableIRQ(XPOWERS_AXP2101_ALL_IRQ);
    PMU.clearIrqStatus();
    PMU.enableIRQ(XPOWERS_AXP2101_PKEY_SHORT_IRQ |
                  XPOWERS_AXP2101_PKEY_LONG_IRQ);
#else
    PMU.setALDO1Voltage(3300); PMU.enableALDO1();
    PMU.setALDO2Voltage(3300); PMU.enableALDO2();
    PMU.setALDO3Voltage(3300); PMU.enableALDO3();   // ALDO3 = the display rail
    PMU.setALDO4Voltage(3300); PMU.enableALDO4();
    // Fuel gauge + detect: without these isBatteryConnect() stays false and
    // getBatteryPercent() returns -1 even with a cell on the MX1.25 header.
    PMU.enableBattDetection();
    PMU.enableBattVoltageMeasure();
    delay(50);
#endif
  }
#if defined(BOARD_TCA9554)
  panelReset();
#endif
}

// ---------------- Formatting ----------------
// Render helpers write into caller-owned buffers. These run on every drawn
// row; returning Arduino String would churn the heap each frame and fragment
// it over the weeks this thing stays powered.
static const char *fmtPct(char *buf, size_t n, float p) {
  if (p < 0) snprintf(buf, n, "--%%");
  else if (p >= 99.5f) snprintf(buf, n, "100%%");
  else if (p >= 10) snprintf(buf, n, "%.0f%%", p);
  else snprintf(buf, n, "%.1f%%", p);
  return buf;
}

// ---------------- Networking ----------------
static WiFiMulti wifiMulti;
static String resolvedHost = "";   // cached IP (or hostname) of the Mac
static bool mdnsUp = false;
static uint32_t wifiDisconnectedSinceMs = 0;

// Why the last fetch failed, short enough for the panel and worth reading on
// Serial. "server unreachable" on its own sends you hunting the wrong half of
// the link — Wi-Fi down, mDNS miss, wrong token and dead host all look alike.
static String netErr = "";
static bool hostViaMdns = false;     // resolvedHost came from mDNS, not fallback
static int lastHttpCode = 0;         // last /usage HTTP status (or HTTPClient err)
static uint32_t lastOkMs = 0;        // millis() of the last successful fetch
static bool everOk = false;

// Which pipe the last good payload came down. The board silently prefers Wi-Fi
// and falls back to the cable, so without this the two are indistinguishable —
// and "why is it stale when I unplug it" has no answer on the glass.
enum LinkVia : uint8_t { LINK_NONE = 0, LINK_WIFI, LINK_USB };
static LinkVia linkVia = LINK_NONE;

static void setNetErr(const String &why) {
  netErr = why;
  Serial.printf("net: %s\n", why.c_str());
}

static void connectWifi() {
  // Credential ownership lives in provisioning NVS. Keep the Arduino Wi-Fi
  // layer from writing another copy when WiFiMulti tries an AP.
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  wifiProvisioningAddStoredNetwork(wifiMulti);
  for (auto &n : WIFI_NETWORKS) wifiMulti.addAP(n.ssid, n.pass);
}

// Resolve the Mac by name via mDNS; fall back to the configured IP. Cached so
// we only pay the lookup on first use and after a failed fetch.
static const String &hostFor() {
  if (resolvedHost.length()) return resolvedHost;
  if (mdnsUp) {
    IPAddress ip = MDNS.queryHost(HOST_NAME, 2000);
    if ((uint32_t)ip != 0) {
      resolvedHost = ip.toString();
      hostViaMdns = true;
      Serial.printf("mdns: %s.local → %s\n", HOST_NAME, resolvedHost.c_str());
      return resolvedHost;
    }
    Serial.printf("mdns: %s.local not found — falling back to %s\n",
                  HOST_NAME, HOST_FALLBACK_IP);
  } else {
    Serial.printf("mdns: responder down — falling back to %s\n",
                  HOST_FALLBACK_IP);
  }
  resolvedHost = HOST_FALLBACK_IP;   // last resort
  hostViaMdns = false;
  return resolvedHost;
}

// The host only demands a token from non-loopback callers, which is every
// request the board makes over Wi-Fi. The USB path needs nothing: the cable
// already proves physical access.
static void addAuthHeader(HTTPClient &http) {
  if (sizeof(HOST_TOKEN) > 1) {
    http.addHeader("X-Headroom-Token", HOST_TOKEN);
  }
}

// Ask the host to force-refresh Sources (same endpoint Mac Settings uses).
static bool requestSyncRefreshHttp() {
  if (WiFi.status() != WL_CONNECTED) return false;
  String url = "http://" + hostFor() + ":" + String(HOST_PORT) + "/sync/refresh";
  HTTPClient http;
  http.setConnectTimeout(800);
  http.setTimeout(1200);
  if (!http.begin(url)) return false;
  http.addHeader("Content-Type", "application/json");
  addAuthHeader(http);
  int code = http.POST("{}");
  http.end();
  Serial.printf("sync refresh → HTTP %d\n", code);
  return code >= 200 && code < 300;
}

// USB CDC framed protocol (same /usage JSON as HTTP). Coexists with Serial
// debug logs: only lines starting with "HR " are protocol.
// Background polls use a short timeout so a missing host can't freeze touch;
// explicit long-press sync may wait longer.
// ~5KB device view at 115200 ≈ 0.5s of wire; leave headroom for a busy host
// so a full burndown frame isn't truncated mid-read.
static const uint32_t USB_TIMEOUT_POLL_MS = 1500;
static const uint32_t USB_TIMEOUT_SYNC_MS = 3500;
// While the chart has no pts yet (common for a few seconds after host restart),
// poll much faster than POLL_INTERVAL_S so "Collecting history" doesn't sit
// for a full minute on a payload that was merely early.
static const uint32_t BURN_WARMUP_POLL_MS = 5000;
// The host serves the board its ?view=device projection (~2KB), so this no
// longer has to hold a 30KB document. CDC RX buffers come out of DRAM, not
// PSRAM, so the 24KB given back here is 24KB the UI and Wi-Fi stack can use.
// Still 8x the expected frame.
static const size_t USB_RX_BUF = 16 * 1024;

// ArduinoJson DOM — keep it in PSRAM so we don't blow the tiny internal heap
// (canvas + WiFi already live there).
struct SpiRamAllocator : ArduinoJson::Allocator {
  void *allocate(size_t size) override {
    return heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  }
  void deallocate(void *ptr) override { heap_caps_free(ptr); }
  void *reallocate(void *ptr, size_t new_size) override {
    return heap_caps_realloc(ptr, new_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  }
};
static SpiRamAllocator spiRamAlloc;

static void usbDrainInput() {
  uint32_t t0 = millis();
  while (Serial.available() && (millis() - t0) < 50) {
    Serial.read();
  }
}

static bool usbReadLine(String &out, uint32_t deadlineMs) {
  out = "";
  while (millis() < deadlineMs) {
    while (Serial.available()) {
      char c = (char)Serial.read();
      if (c == '\r') continue;
      if (c == '\n') return true;
      out += c;
      if (out.length() > 256) return false;
    }
    delay(1);
    yield();
  }
  return false;
}

static bool usbReadExact(char *out, size_t n, uint32_t deadlineMs) {
  size_t got = 0;
  while (got < n && millis() < deadlineMs) {
    while (Serial.available() && got < n) {
      int avail = Serial.available();
      if (avail <= 0) break;
      size_t chunk = (size_t)avail;
      if (chunk > n - got) chunk = n - got;
      size_t rd = Serial.readBytes(out + got, chunk);
      got += rd;
      if (rd == 0) break;
    }
    if (got < n) {
      delay(1);
      yield();
    }
  }
  return got == n;
}

// Wait for "HR <status> <nbytes>" then optionally read nbytes of body.
static bool usbTransact(const char *requestLine, int wantStatus,
                        char **bodyOut, size_t *bodyLen,
                        uint32_t timeoutMs) {
  if (bodyOut) *bodyOut = nullptr;
  if (bodyLen) *bodyLen = 0;

  usbDrainInput();
  Serial.print(requestLine);
  Serial.print('\n');
  // Do not Serial.flush() — with the Mac holding CDC it can block forever.

  uint32_t deadline = millis() + timeoutMs;
  while (millis() < deadline) {
    String line;
    if (!usbReadLine(line, deadline)) return false;
    if (!line.startsWith("HR ")) continue;

    // HR <status> <nbytes>
    int sp1 = line.indexOf(' ', 3);
    if (sp1 < 0) continue;
    int status = line.substring(3, sp1).toInt();
    int nbytes = line.substring(sp1 + 1).toInt();
    if (nbytes < 0 || nbytes > (int)USB_RX_BUF) return false;

    char *body = nullptr;
    if (nbytes > 0) {
      body = (char *)heap_caps_malloc(
          (size_t)nbytes + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
      if (!body) {
        Serial.println("usb: OOM body");
        return false;
      }
      if (!usbReadExact(body, (size_t)nbytes, deadline)) {
        heap_caps_free(body);
        Serial.printf("usb: short body want=%d\n", nbytes);
        return false;
      }
      body[nbytes] = '\0';
      // trailing newline after body
      uint32_t trailDeadline = millis() + 50;
      while (millis() < trailDeadline) {
        if (Serial.available()) {
          (void)Serial.read();
          break;
        }
        delay(1);
      }
    }

    if (status != wantStatus) {
      Serial.printf("usb → HR %d (want %d)\n", status, wantStatus);
      if (body) heap_caps_free(body);
      return false;
    }
    if (bodyOut) {
      *bodyOut = body;
      if (bodyLen) *bodyLen = (size_t)nbytes;
    } else if (body) {
      heap_caps_free(body);
    }
    return true;
  }
  return false;
}

static bool requestSyncRefreshUsb() {
  bool ok = usbTransact("HR POST /sync/refresh", 202, nullptr, nullptr,
                        USB_TIMEOUT_SYNC_MS);
  Serial.printf("sync refresh → USB %s\n", ok ? "ok" : "fail");
  return ok;
}

static bool requestSyncRefresh() {
  if (WiFi.status() == WL_CONNECTED && requestSyncRefreshHttp()) return true;
  return requestSyncRefreshUsb();
}

// Forward decl — applyUsageDoc folds host middots before the helper's body.
static String boardAscii(const char *s);

// Only these keys survive deserialization. The host's ?view=device projection
// already drops the rest, but the filter also protects the board if it is
// pointed at an older host still serving the full ~30KB document.
//
// Built once, in PSRAM, and checked for overflow — all three matter. ArduinoJson
// grows a document 4KB at a time (ARDUINOJSON_POOL_CAPACITY) and a failed
// allocation makes `filter[key] = true` a silent no-op that only sets
// overflowed(). A filter that lost keys still parses clean: the deserializer
// skips members the filter doesn't name and returns Ok. Since keys go in in
// source order, a short filter keeps the prefix — the providers — and drops
// vercel/git/local/burndown, which is a board that boots showing quota alone
// and "fixes itself" on the next power cycle. Rebuilding this per
// fetch asked the tightest heap on the board (canvas + Wi-Fi + LWIP live in
// DRAM) for 4KB contiguous every poll; PSRAM has room and the doc already uses it.
static bool usageFilterReady(JsonDocument **out) {
  static JsonDocument filter(&spiRamAlloc);
  static bool built = false;
  *out = &filter;
  if (built) return true;

  filter.clear();   // also resets overflowed()
  filter["updated"] = true;
  // Whole subtree: device_view already trimmed it to three providers with
  // their ring pools, so there is nothing further to filter. This goes in
  // first because a filter that overflows keeps its prefix, and losing the
  // providers is losing the whole quota half of the board.
  filter["providers"] = true;
  filter["vercel"]["ok"] = true;
  filter["vercel"]["team"] = true;
  filter["vercel"]["deployments"][0]["project"] = true;
  filter["vercel"]["deployments"][0]["status"] = true;
  filter["vercel"]["deployments"][0]["target"] = true;
  filter["vercel"]["deployments"][0]["ago"] = true;
  filter["vercel"]["deployments"][0]["branch"] = true;
  filter["git"]["ok"] = true;
  filter["git"]["commits"][0]["repo"] = true;
  filter["git"]["commits"][0]["subject"] = true;
  filter["git"]["commits"][0]["ago"] = true;
  filter["git"]["commits"][0]["branch"] = true;
  filter["local"]["ok"] = true;
  filter["local"]["host"] = true;
  filter["local"]["servers"][0]["name"] = true;
  filter["local"]["servers"][0]["port"] = true;
  filter["local"]["servers"][0]["cmd"] = true;
  filter["sources"][0]["id"] = true;
  filter["sources"][0]["title"] = true;
  filter["sources"][0]["enabled"] = true;
  filter["sources"][0]["ok"] = true;
  filter["sources"][0]["stale"] = true;
  filter["sources"][0]["auth_required"] = true;
  filter["sources"][0]["age_s"] = true;
  // Whole subtree, like codex/cursor above: device_view has already trimmed it
  // (one pool, or Total+API for Cursor), so there is nothing further to filter.
  filter["burndown"] = true;
  // The board only needs the compact level strip and its start alignment. Day
  // detail stays on the Mac/iPhone, keeping this projection small over USB.
  filter["activity_history"]["source"] = true;
  filter["activity_history"]["start"] = true;
  filter["activity_history"]["start_weekday"] = true;
  filter["activity_history"]["levels"][0] = true;
  filter["activity_history"]["active_days"] = true;
  filter["activity_history"]["current_streak"] = true;
  // Seven mixed-source daily-burn bars and the app's estimated spend figures
  // power the compact home cards; source detail remains on the Mac/iPhone.
  filter["daily_burn"]["source"] = true;
  filter["daily_burn"]["days"][0]["label"] = true;
  filter["daily_burn"]["days"][0]["burns"] = true;
  filter["daily_burn"]["days"][0]["total"] = true;
  filter["spend"]["estimated"] = true;
  filter["spend"]["today"] = true;
  filter["spend"]["total"] = true;
  filter["spend"]["days"] = true;
  filter["spend"]["avg"] = true;
  filter["device_effect"] = true;
  // Host-owned panel settings; whole subtree, it is four scalars and a map.
  filter["display"] = true;

  // Leave `built` false on overflow so the next fetch retries the build once
  // PSRAM frees up, rather than latching a filter that silently eats providers.
  built = !filter.overflowed();
  if (!built) Serial.println("json filter: PSRAM short — payload skipped");
  return built;
}

// "#D97757" → RGB565. Anything that isn't six hex digits falls back to the
// caller's default rather than painting black, which on this panel is
// indistinguishable from a ring that failed to draw.
static uint16_t accentToRgb565(const char *hex, uint16_t fallback) {
  if (!hex || !*hex) return fallback;
  if (*hex == '#') hex++;
  uint32_t value = 0;
  for (uint8_t i = 0; i < 6; i++) {
    const char c = hex[i];
    uint8_t nibble;
    if (c >= '0' && c <= '9') nibble = (uint8_t)(c - '0');
    else if (c >= 'a' && c <= 'f') nibble = (uint8_t)(c - 'a' + 10);
    else if (c >= 'A' && c <= 'F') nibble = (uint8_t)(c - 'A' + 10);
    else return fallback;
    value = (value << 4) | nibble;
  }
  if (hex[6] != '\0') return fallback;
  return RGB565((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF);
}

// One provider's burndown series: the actual remaining-% curve, where the
// current pace lands, and an optional second series (Cursor API) on the same
// axis. `out` is assumed freshly default-constructed.
static void applyBurndownDoc(JsonObject b, Burndown &out) {
  out.t0 = (uint32_t)(b["t0"] | 0);
  out.t1 = (uint32_t)(b["t1"] | 0);
  if (out.t1 <= out.t0) return;
  out.warn = b["warn"] | false;
  out.estimated = b["est"] | false;
  out.verdict = boardAscii((const char *)(b["verdict"] | ""));
  out.exhausted = strcmp((const char *)(b["status"] | ""), "exhausted") == 0;

  JsonArray pts = b["pts"].as<JsonArray>();
  if (!pts.isNull()) {
    for (JsonVariant v : pts) {
      if (out.n >= MAX_BURN_PTS) break;
      JsonArray pair = v.as<JsonArray>();
      if (pair.isNull() || pair.size() < 2) continue;
      out.t[out.n] = (uint32_t)(pair[0] | 0);
      out.remaining[out.n] = (float)(pair[1] | 0.0);
      out.n++;
    }
  }
  JsonArray proj = b["proj"].as<JsonArray>();
  if (!proj.isNull()) {
    for (JsonVariant v : proj) {
      if (out.projN >= 2) break;
      JsonArray pair = v.as<JsonArray>();
      if (pair.isNull() || pair.size() < 2) continue;
      out.projT[out.projN] = (uint32_t)(pair[0] | 0);
      out.projR[out.projN] = (float)(pair[1] | 0.0);
      out.projN++;
    }
  }

  JsonArray hist = b["hist"].as<JsonArray>();
  if (!hist.isNull()) {
    for (JsonVariant v : hist) {
      if (out.histN >= MAX_HIST_PTS) break;
      JsonArray pair = v.as<JsonArray>();
      if (pair.isNull() || pair.size() < 2) continue;
      out.histT[out.histN] = (uint32_t)(pair[0] | 0);
      out.histR[out.histN] = (float)(pair[1] | 0.0);
      out.histN++;
    }
  }
  JsonArray rsts = b["rsts"].as<JsonArray>();
  if (!rsts.isNull()) {
    for (JsonVariant v : rsts) {
      if (out.grantN >= MAX_GRANTS) break;
      JsonArray pair = v.as<JsonArray>();
      if (pair.isNull() || pair.size() < 2) continue;
      out.grantT[out.grantN] = (uint32_t)(pair[0] | 0);
      out.grantPct[out.grantN] = (float)(pair[1] | 0.0);
      out.grantN++;
    }
  }

  // Optional Cursor API overlay.
  JsonArray pts2 = b["pts2"].as<JsonArray>();
  if (!pts2.isNull()) {
    out.warn2 = b["warn2"] | false;
    out.estimated2 = b["est2"] | false;
    out.exhausted2 =
        strcmp((const char *)(b["status2"] | ""), "exhausted") == 0;
    for (JsonVariant v : pts2) {
      if (out.n2 >= MAX_BURN_PTS) break;
      JsonArray pair = v.as<JsonArray>();
      if (pair.isNull() || pair.size() < 2) continue;
      out.t2[out.n2] = (uint32_t)(pair[0] | 0);
      out.remaining2[out.n2] = (float)(pair[1] | 0.0);
      out.n2++;
    }
    JsonArray proj2 = b["proj2"].as<JsonArray>();
    if (!proj2.isNull()) {
      for (JsonVariant v : proj2) {
        if (out.projN2 >= 2) break;
        JsonArray pair = v.as<JsonArray>();
        if (pair.isNull() || pair.size() < 2) continue;
        out.projT2[out.projN2] = (uint32_t)(pair[0] | 0);
        out.projR2[out.projN2] = (float)(pair[1] | 0.0);
        out.projN2++;
      }
    }
  }
  // History alone is enough to draw. A window that rolled minutes ago has one
  // live point and nothing to join it to, and that is exactly the moment the
  // spent curve behind it is the only thing on the panel worth showing.
  out.ok = out.n > 0 || out.histN > 0;
}

static bool applyUsageDoc(JsonDocument &doc) {
  updatedZ = String((const char *)(doc["updated"] | ""));

  // The glance slots, exactly as the host ordered them. Which providers,
  // what they are called and what color they are painted all arrive here —
  // the board picks none of it, so Settings on the Mac is the one place any
  // of it is decided.
  for (uint8_t i = 0; i < MAX_SLOTS; i++) slots[i] = ProviderSlot{};
  slotN = 0;
  JsonArray provs = doc["providers"].as<JsonArray>();
  providersSeen = !provs.isNull();
  if (providersSeen) {
    for (JsonVariant v : provs) {
      if (slotN >= MAX_SLOTS) break;
      JsonObject p = v.as<JsonObject>();
      if (p.isNull()) continue;
      ProviderSlot &slot = slots[slotN];
      slot.id = String((const char *)(p["id"] | ""));
      if (!slot.id.length()) continue;
      slot.title = boardAscii((const char *)(p["title"] | ""));
      if (!slot.title.length()) slot.title = slot.id;
      // `| ""` like every other string read here: with a nullptr default
      // ArduinoJson hands back null whether or not the key is there, which
      // painted every ring COL_DIM.
      slot.accent = accentToRgb565((const char *)(p["accent"] | ""), COL_DIM);
      slot.q.ok = p["ok"] | false;
      slot.q.plan = boardAscii((const char *)(p["plan"] | ""));
      slot.q.note = boardAscii((const char *)(p["note"] | ""));
      slot.q.note2 = boardAscii((const char *)(p["note2"] | ""));
      JsonArray pools = p["pools"].as<JsonArray>();
      if (!pools.isNull()) {
        for (JsonVariant pv : pools) {
          if (slot.q.n >= MAX_POOLS) break;
          JsonObject pool = pv.as<JsonObject>();
          if (pool.isNull()) continue;
          PoolRow &row = slot.q.pools[slot.q.n];
          row.title = boardAscii((const char *)(pool["t"] | ""));
          row.pct = pool["p"].isNull() ? -1.f : (float)(pool["p"] | -1.0);
          row.pace = pool["c"].isNull() ? -1.f : (float)(pool["c"] | -1.0);
          row.resets = boardAscii((const char *)(pool["r"] | ""));
          if (row.pct < 0) continue;   // nothing to draw; reuse the row
          slot.q.n++;
        }
      }
      slotN++;
    }
  }

  // Mixed-source history: the board gets only the compact level strip. A
  // missing section is normal on an older host, so it simply disables the
  // History home mode's data rather than disturbing the quota view.
  activityHistory = ActivityHistory{};
  JsonObject ah = doc["activity_history"].as<JsonObject>();
  if (!ah.isNull()) {
    activityHistory.start = String((const char *)(ah["start"] | ""));
    activityHistory.startWeekday = (uint8_t)(ah["start_weekday"] | 0);
    activityHistory.activeDays = (uint16_t)(ah["active_days"] | 0);
    activityHistory.currentStreak = (uint16_t)(ah["current_streak"] | 0);
    JsonArray levels = ah["levels"].as<JsonArray>();
    if (!levels.isNull()) {
      for (JsonVariant value : levels) {
        if (activityHistory.n >= MAX_ACTIVITY_DAYS) break;
        int level = value | 0;
        if (level < 0) level = 0;
        if (level > 4) level = 4;
        activityHistory.levels[activityHistory.n++] = (uint8_t)level;
      }
    }
    activityHistory.ok = activityHistory.n > 0;
  }

  // Seven-day mixed-source burn strip. Source ids are matched to the same
  // host-selected slots that own the rings, so the stack and the legend never
  // disagree about color or provider order.
  dailyBurnN = 0;
  for (uint8_t i = 0; i < MAX_DAILY_BURN_DAYS; i++) {
    dailyBurnDays[i] = DailyBurnDay{};
  }
  JsonObject db = doc["daily_burn"].as<JsonObject>();
  if (!db.isNull()) {
    JsonArray days = db["days"].as<JsonArray>();
    if (!days.isNull()) {
      for (JsonVariant value : days) {
        if (dailyBurnN >= MAX_DAILY_BURN_DAYS) break;
        JsonObject day = value.as<JsonObject>();
        if (day.isNull()) continue;
        DailyBurnDay &row = dailyBurnDays[dailyBurnN];
        row.label = boardAscii((const char *)(day["label"] | ""));
        row.total = (float)(day["total"] | 0.0);
        JsonObject burns = day["burns"].as<JsonObject>();
        if (!burns.isNull()) {
          for (uint8_t i = 0; i < slotN; i++) {
            row.burns[i] = (float)(burns[slots[i].id.c_str()] | 0.0);
            if (row.burns[i] < 0) row.burns[i] = 0;
          }
        }
        if (row.total <= 0) {
          for (uint8_t i = 0; i < slotN; i++) row.total += row.burns[i];
        }
        dailyBurnN++;
      }
    }
  }

  spendSummary = SpendSummary{};
  JsonObject spend = doc["spend"].as<JsonObject>();
  if (!spend.isNull()) {
    spendSummary.estimated = spend["estimated"].isNull()
                                ? true : (bool)(spend["estimated"] | true);
    spendSummary.today = (float)(spend["today"] | 0.0);
    spendSummary.total = (float)(spend["total"] | 0.0);
    spendSummary.avg = (float)(spend["avg"] | 0.0);
    spendSummary.days = (uint16_t)(spend["days"] | 0);
    spendSummary.ok = spendSummary.today > 0 || spendSummary.total > 0;
  }

  // Burndown series, looked up by the slot's provider id (Cursor may overlay
  // API as *2). Ids now include extra accounts — "claude:work" — so the
  // lookup is by string rather than by a fixed trio.
  JsonObject bd = doc["burndown"].as<JsonObject>();
  if (!bd.isNull()) {
    for (uint8_t i = 0; i < slotN; i++) {
      JsonObject b = bd[slots[i].id.c_str()].as<JsonObject>();
      if (!b.isNull()) applyBurndownDoc(b, slots[i].burn);
    }
  }

  // A changed burndown window start is the host's cross-provider reset
  // boundary. Baseline the first payload so powering on never celebrates an
  // already-existing window; subsequent scheduled or granted rolls do.
  for (uint8_t i = 0; i < MAX_SLOTS; i++) {
    if (i >= slotN || !slots[i].burn.t0) {
      resetBaselineReady[i] = false;
      resetBaselineIds[i] = "";
      resetBaselineStarts[i] = 0;
      continue;
    }
    const bool sameProvider = resetBaselineReady[i]
        && resetBaselineIds[i] == slots[i].id;
    if (sameProvider && resetBaselineStarts[i] != slots[i].burn.t0) {
      pendingCelebration = true;
      pendingCelebrationAccent = slots[i].accent;
      Serial.printf("reset celebration: %s\n", slots[i].id.c_str());
    }
    resetBaselineIds[i] = slots[i].id;
    resetBaselineStarts[i] = slots[i].burn.t0;
    resetBaselineReady[i] = true;
  }

  // A device effect is additive and host-commanded. The id is persisted when
  // consumed, so a board reboot does not replay an old remote test command.
  JsonObject effect = doc["device_effect"].as<JsonObject>();
  if (!effect.isNull() && strcmp((const char *)(effect["kind"] | ""),
                                 "reset") == 0) {
    const uint32_t effectId = (uint32_t)(effect["id"] | 0u);
    if (effectId > lastEffectId) {
      lastEffectId = effectId;
      effectSave();
      pendingCelebration = true;
      pendingCelebrationAccent = slotN > 0 ? slots[0].accent : COL_DIM;
      const char *provider = (const char *)(effect["provider"] | "");
      for (uint8_t i = 0; i < slotN; i++) {
        if (provider[0] && slots[i].id == provider) {
          pendingCelebrationAccent = slots[i].accent;
          break;
        }
      }
      if (!provider[0] && slotN > 0) pendingCelebrationAccent = slots[0].accent;
      Serial.printf("remote celebration: %s\n",
                    provider[0] ? provider : "default");
    }
  }

  applyDisplayDoc(doc["display"].as<JsonObject>());

  // Vercel deployments
  vercelOk = false;
  vercelTeam = "";
  vercelN = 0;
  JsonObject vx = doc["vercel"].as<JsonObject>();
  if (!vx.isNull()) {
    vercelOk = vx["ok"] | false;
    vercelTeam = String((const char *)(vx["team"] | ""));
    JsonArray deps = vx["deployments"].as<JsonArray>();
    if (!deps.isNull()) {
      for (JsonVariant v : deps) {
        if (vercelN >= MAX_DEPLOYS) break;
        JsonObject d = v.as<JsonObject>();
        if (d.isNull()) continue;
        DeployRow &r = vercelRows[vercelN++];
        r.project = String((const char *)(d["project"] | "?"));
        r.status  = String((const char *)(d["status"] | ""));
        r.target  = String((const char *)(d["target"] | ""));
        r.ago     = String((const char *)(d["ago"] | ""));
        r.branch  = String((const char *)(d["branch"] | ""));
      }
    }
  }

  // Git commits
  gitOk = false;
  gitN = 0;
  JsonObject gx = doc["git"].as<JsonObject>();
  if (!gx.isNull()) {
    gitOk = gx["ok"] | false;
    JsonArray commits = gx["commits"].as<JsonArray>();
    if (!commits.isNull()) {
      for (JsonVariant v : commits) {
        if (gitN >= MAX_COMMITS) break;
        JsonObject c = v.as<JsonObject>();
        if (c.isNull()) continue;
        CommitRow &r = gitRows[gitN++];
        r.repo    = String((const char *)(c["repo"] | "?"));
        r.subject = String((const char *)(c["subject"] | ""));
        r.ago     = String((const char *)(c["ago"] | ""));
        r.branch  = String((const char *)(c["branch"] | ""));
      }
    }
  }

  // Local listening servers
  localOk = false;
  localHost = "";
  localN = 0;
  JsonObject lx = doc["local"].as<JsonObject>();
  if (!lx.isNull()) {
    localOk = lx["ok"] | false;
    localHost = String((const char *)(lx["host"] | ""));
    JsonArray servers = lx["servers"].as<JsonArray>();
    if (!servers.isNull()) {
      for (JsonVariant v : servers) {
        if (localN >= MAX_SERVERS) break;
        JsonObject s = v.as<JsonObject>();
        if (s.isNull()) continue;
        ServerRow &r = localRows[localN++];
        r.name = String((const char *)(s["name"] | "?"));
        r.port = (int)(s["port"] | 0);
        r.cmd  = String((const char *)(s["cmd"] | ""));
      }
    }
  }

  // Sources panel (Mac Settings + ESP32 footer share this list).
  sourceN = 0;
  JsonArray sx = doc["sources"].as<JsonArray>();
  if (!sx.isNull()) {
    for (JsonVariant v : sx) {
      if (sourceN >= MAX_SOURCES) break;
      JsonObject s = v.as<JsonObject>();
      if (s.isNull()) continue;
      SourceRow &r = sourceRows[sourceN++];
      r.id = String((const char *)(s["id"] | ""));
      r.title = String((const char *)(s["title"] | r.id.c_str()));
      r.enabled = s["enabled"].isNull() ? true : (bool)(s["enabled"] | true);
      r.ok = s["ok"] | false;
      r.stale = s["stale"] | false;
      r.authRequired = s["auth_required"] | false;
      r.ageS = (int32_t)(s["age_s"] | -1);
    }
  }

  haveData = true;
  hostOk = true;
  return true;
}

static bool applyUsageJson(const char *payload, size_t len) {
  JsonDocument *filter = nullptr;
  if (!usageFilterReady(&filter)) {
    hostOk = false;
    return false;
  }
  JsonDocument doc(&spiRamAlloc);
  DeserializationError err = (len > 0)
      ? deserializeJson(doc, payload, len,
                        DeserializationOption::Filter(*filter))
      : deserializeJson(doc, payload, DeserializationOption::Filter(*filter));
  if (err) {
    Serial.printf("json parse fail: %s\n", err.c_str());
    hostOk = false;
    return false;
  }
  return applyUsageDoc(doc);
}

static bool applyUsageStream(Stream &stream) {
  JsonDocument *filter = nullptr;
  if (!usageFilterReady(&filter)) {
    hostOk = false;
    return false;
  }
  JsonDocument doc(&spiRamAlloc);
  DeserializationError err =
      deserializeJson(doc, stream, DeserializationOption::Filter(*filter));
  if (err) {
    Serial.printf("json parse fail: %s\n", err.c_str());
    hostOk = false;
    return false;
  }
  return applyUsageDoc(doc);
}

static bool fetchUsageHttp() {
  if (WiFi.status() != WL_CONNECTED) {
    setNetErr("no wi-fi");
    return false;
  }
  const String host = hostFor();
  String url = "http://" + host + ":" + String(HOST_PORT) +
               "/usage?view=device&fw=" + fwVersion();
  HTTPClient http;
  // Fail fast — a slow/wrong LAN must not starve BOOT/touch.
  http.setConnectTimeout(700);
  http.setTimeout(1000);
  if (!http.begin(url)) {
    setNetErr("bad url " + host);
    return false;
  }
  addAuthHeader(http);
  Serial.printf("GET %s\n", url.c_str());
  int code = http.GET();
  lastHttpCode = code;
  if (code != 200) {
    http.end();
    // Negative codes are HTTPClient transport errors (refused, timeout, no
    // route); positive ones came from a server that answered. Different bug,
    // different fix — so say which.
    if (code < 0) {
      setNetErr(host + ": " + HTTPClient::errorToString(code));
      Serial.printf("usage → %s (%d) — is headroom_server.py running on "
                    "%s:%d? check: curl http://%s:%d/health\n",
                    HTTPClient::errorToString(code).c_str(), code,
                    host.c_str(), HOST_PORT, host.c_str(), HOST_PORT);
    } else if (code == 401 || code == 403) {
      setNetErr("HTTP " + String(code) + " bad token");
      Serial.println("usage → HTTP 401/403: HOST_TOKEN missing or wrong. "
                     "On the Mac: cat ~/.headroom/token — paste into "
                     "firmware/src/config.h");
    } else {
      setNetErr("HTTP " + String(code));
      Serial.printf("usage → HTTP %d\n", code);
    }
    resolvedHost = "";   // force a fresh mDNS lookup next time
    return false;
  }

  // Parse straight off the socket. getString() would hold the whole body in
  // heap at the same time as the JSON document — two copies of the payload
  // for no reason.
  bool ok = applyUsageStream(http.getStream());
  http.end();
  if (!ok) setNetErr("bad json from " + host);
  else linkVia = LINK_WIFI;
  return ok;
}

static bool fetchUsageUsb(uint32_t timeoutMs) {
  char *body = nullptr;
  size_t len = 0;
  const String request = "HR GET /usage?fw=" + fwVersion();
  if (!usbTransact(request.c_str(), 200, &body, &len, timeoutMs)) return false;
  bool ok = applyUsageJson(body, len);
  if (body) heap_caps_free(body);
  if (ok) linkVia = LINK_USB;
  return ok;
}

static bool fetchUsage(uint32_t usbTimeoutMs = USB_TIMEOUT_POLL_MS) {
  // Wi-Fi HTTP first when associated. USB is travel/offline fallback.
  // Do not service touch/BOOT from inside USB waits — Serial TX can block
  // forever while the host holds the CDC write path (UI deadlock).
  if (WiFi.status() == WL_CONNECTED && fetchUsageHttp()) {
    Serial.println("usage via Wi-Fi");
    netErr = "";
    lastOkMs = millis();
    everOk = true;
    return true;
  }
  const String wifiErr = netErr;   // keep the HTTP reason if USB also fails
  if (fetchUsageUsb(usbTimeoutMs)) {
    Serial.println("usage via USB");
    netErr = "";
    lastOkMs = millis();
    everOk = true;
    return true;
  }
  if (WiFi.status() != WL_CONNECTED) setNetErr("no wi-fi, no usb host");
  else netErr = wifiErr.length() ? wifiErr : String("no route to host");
  hostOk = false;
  return false;
}

// Everything you'd otherwise squint at a serial log for, in one dump. Called on
// every failed poll so a board left on the desk still explains itself.
static void logNetDiag() {
  const bool up = WiFi.status() == WL_CONNECTED;
  Serial.println("---- headroom net diag ----");
  Serial.printf("  fw       : %s\n", fwVersion().c_str());
  Serial.printf("  wifi     : %s", up ? "connected" : "DOWN");
  if (up) Serial.printf("  ssid=%s  rssi=%d dBm  ip=%s",
                        WiFi.SSID().c_str(), WiFi.RSSI(),
                        WiFi.localIP().toString().c_str());
  Serial.println();
  if (up) Serial.printf("  gateway  : %s  mask=%s  dns=%s\n",
                        WiFi.gatewayIP().toString().c_str(),
                        WiFi.subnetMask().toString().c_str(),
                        WiFi.dnsIP().toString().c_str());
  Serial.printf("  mdns     : responder=%s  %s.local → %s\n",
                mdnsUp ? "up" : "DOWN", HOST_NAME,
                resolvedHost.length()
                    ? (hostViaMdns ? resolvedHost.c_str()
                                   : (resolvedHost + " (fallback)").c_str())
                    : "unresolved");
  Serial.printf("  target   : http://%s:%d/usage\n",
                resolvedHost.length() ? resolvedHost.c_str()
                                      : HOST_FALLBACK_IP, HOST_PORT);
  Serial.printf("  token    : %s\n",
                sizeof(HOST_TOKEN) > 1 ? "set" : "EMPTY (host will 401)");
  Serial.printf("  link     : %s\n",
                linkVia == LINK_WIFI ? "wi-fi"
                                     : (linkVia == LINK_USB ? "usb" : "none"));
  Serial.printf("  last code: %d\n", lastHttpCode);
  Serial.printf("  last ok  : %s\n",
                everOk ? (String((millis() - lastOkMs) / 1000) + "s ago").c_str()
                       : "never");
  Serial.printf("  error    : %s\n", netErr.length() ? netErr.c_str() : "none");
  Serial.println("  on the Mac, in order:");
  Serial.printf("    curl -s http://127.0.0.1:%d/health\n", HOST_PORT);
  Serial.printf("    curl -s -H \"X-Headroom-Token: $(cat ~/.headroom/token)\""
                " http://%s:%d/usage | head -c 200\n",
                resolvedHost.length() ? resolvedHost.c_str()
                                      : HOST_FALLBACK_IP, HOST_PORT);
  Serial.println("---------------------------");
}

// ---------------- Rendering ----------------
// Logical landscape size (448×368).
static inline int16_t scrW() { return gfx->width(); }
static inline int16_t scrH() { return gfx->height(); }

static int16_t textWidth(const char *s, uint8_t size) {
  gfx->setTextSize(size);
  int16_t x1, y1; uint16_t w, h;
  gfx->getTextBounds(s, 0, 0, &x1, &y1, &w, &h);
  return (int16_t)w;
}

static void drawCentered(const char *s, int y, uint8_t size, uint16_t col) {
  const int16_t w = textWidth(s, size);
  gfx->setTextColor(col);
  gfx->setCursor((scrW() - w) / 2, y);
  gfx->print(s);
}

static void drawTextAt(const char *s, int x, int y, uint8_t size, uint16_t col) {
  gfx->setTextSize(size);
  gfx->setTextColor(col);
  gfx->setCursor(x, y);
  gfx->print(s);
}

// Right-aligned to `rightX`. Every page ends up doing this — measure, subtract,
// place — so it lives here instead of being open-coded a dozen times.
static void drawRightAt(const char *s, int rightX, int y, uint8_t size,
                        uint16_t col) {
  const int16_t w = textWidth(s, size);
  gfx->setTextColor(col);
  gfx->setCursor(rightX - w, y);
  gfx->print(s);
}

static void drawCentered(const String &s, int y, uint8_t size, uint16_t col) {
  drawCentered(s.c_str(), y, size, col);
}

static void drawTextAt(const String &s, int x, int y, uint8_t size, uint16_t col) {
  drawTextAt(s.c_str(), x, y, size, col);
}

static void drawRightAt(const String &s, int rightX, int y, uint8_t size,
                        uint16_t col) {
  drawRightAt(s.c_str(), rightX, y, size, col);
}

static bool providerBaseIs(const String &id, const char *base) {
  const size_t n = strlen(base);
  return id.length() >= n && strncmp(id.c_str(), base, n) == 0
      && (id.length() == n || id[n] == ':');
}

static const uint8_t *providerMark(const String &id) {
  if (providerBaseIs(id, "claude")) return MARK_CLAUDE;
  if (providerBaseIs(id, "codex")) return MARK_CODEX;
  if (providerBaseIs(id, "cursor")) return MARK_CURSOR;
  if (providerBaseIs(id, "copilot")) return MARK_COPILOT;
  if (providerBaseIs(id, "gemini")) return MARK_GEMINI;
  if (providerBaseIs(id, "windsurf")) return MARK_WINDSURF;
  if (providerBaseIs(id, "jetbrains")) return MARK_JETBRAINS;
  if (providerBaseIs(id, "zed")) return MARK_ZED;
  return nullptr;
}

static bool drawProviderMark(const String &id, int16_t x, int16_t y,
                             uint16_t color) {
  const uint8_t *bits = providerMark(id);
  if (!bits) return false;
  gfx->drawBitmap(x, y, bits, PROVIDER_MARK_W, PROVIDER_MARK_H, color);
  return true;
}

// Shared page inset — keep boot chrome + dashboards clear of the corner radius.
static const int16_t UI_PAD = 28;

// Every dashboard starts its first ink lower than the side inset. The bezel
// curves over the top of the panel and the biggest type on the board sits
// right there, so the side inset alone reads as tight. Each page pays for the
// extra out of slack inside its own header — under the ring labels on home,
// between the header lines on the sub-pages — so nothing below the divider
// moves and no page loses a row.
static const int16_t UI_TOP = UI_PAD + 10;

#if defined(BOARD_ROUND_UI)
// The round panel still exposes a square framebuffer. Layout bands are sized
// from the narrowest circle chord they cross, so nothing important relies on
// the controller clipping the square's corners. This is the same principle as
// LoFi Air's radial face, applied to Headroom's rings-and-reading hierarchy.
static const int16_t ROUND_CX = LOG_W / 2;
static const int16_t ROUND_CY = LOG_H / 2;
static const int16_t ROUND_R = (LOG_W < LOG_H ? LOG_W : LOG_H) / 2 - 6;

static int16_t roundChordHalfAt(int16_t y) {
  const int32_t dy = (int32_t)y - ROUND_CY;
  const int32_t inside = (int32_t)ROUND_R * ROUND_R - dy * dy;
  return inside > 0 ? (int16_t)sqrtf((float)inside) : 0;
}

static void roundBand(int16_t y0, int16_t y1, int16_t inset,
                      int16_t *x, int16_t *w) {
  const int16_t h0 = roundChordHalfAt(y0);
  const int16_t h1 = roundChordHalfAt(y1);
  int16_t half = (h0 < h1 ? h0 : h1) - inset;
  if (half < 1) half = 1;
  *x = (int16_t)(ROUND_CX - half);
  *w = (int16_t)(half * 2);
}
#endif

static void drawStatus(const String &msg, uint16_t col) {
  gfx->clear(COL_CRT_BG);
#if defined(BOARD_ROUND_UI)
  gfx->drawCircle(ROUND_CX, ROUND_CY, ROUND_R, COL_CRT_DIM);
  drawCentered("HEADROOM", 40, 2, COL_CRT);
#else
  const int16_t pad = UI_PAD;
  gfx->drawRect(pad, pad, scrW() - pad * 2, scrH() - pad * 2, COL_CRT_DIM);
  drawCentered("HEADROOM", pad + 16, 2, COL_CRT);
#endif
  drawCentered(msg, scrH() / 2, 2, col);
  gfx->flush();
}

// The unreachable screen, with the state that decides which half of the link to
// go poke. Beats "server unreachable" plus a walk to the Mac to guess.
static void drawNetDiag() {
  gfx->clear(COL_CRT_BG);
#if defined(BOARD_ROUND_UI)
  gfx->drawCircle(ROUND_CX, ROUND_CY, ROUND_R, COL_CRT_DIM);
  drawCentered("NO HOST", 38, 2, COL_CRT_YELLOW);
  int16_t y = 80;
#else
  const int16_t pad = UI_PAD;
  gfx->drawRect(pad, pad, scrW() - pad * 2, scrH() - pad * 2, COL_CRT_DIM);
  drawCentered("NO HOST", pad + 10, 2, COL_CRT_YELLOW);
  int16_t y = pad + 42;
  const int16_t x = pad + 12;
  // Same size 2 and 20px pitch as the boot checklist — this screen is read
  // from across the desk, which is the whole reason it exists.
  const int16_t xv = (int16_t)(x + 6 * 2 * 6);   // widest label is 5 chars + gap
  const int16_t maxChars = (int16_t)((scrW() - pad - 4 - xv) / (6 * 2));
#endif

  const bool up = WiFi.status() == WL_CONNECTED;
  const int16_t step = 20;

  auto row = [&](const char *label, const String &value, uint16_t col) {
#if defined(BOARD_ROUND_UI)
    int16_t x = 0, rowW = 0;
    roundBand(y, (int16_t)(y + 15), 10, &x, &rowW);
    const int16_t xv = (int16_t)(x + 6 * 2 * 6);
    const int16_t maxChars = (int16_t)((x + rowW - xv) / (6 * 2));
#endif
    drawTextAt(label, x, y, 2, COL_CRT_DIM);
    // Values run long (an mDNS miss, a wedged-socket message). Clip rather
    // than let Arduino_GFX wrap it into the next row.
    drawTextAt(value.length() > (unsigned)maxChars
                   ? value.substring(0, maxChars)
                   : value,
               xv, y, 2, col);
    y = (int16_t)(y + step);
  };

  row("WIFI", up ? WiFi.SSID() : String("not connected"),
      up ? COL_CRT : COL_CRT_YELLOW);
  if (up) {
    row("IP", WiFi.localIP().toString() + "  " + String(WiFi.RSSI()) + "dBm",
        COL_CRT);
  }
  row("HOST", String(HOST_NAME) + ":" + String(HOST_PORT), COL_CRT);
  row("ADDR", resolvedHost.length()
                  ? resolvedHost + (hostViaMdns ? "  mdns" : "  fallback")
                  : String("unresolved"),
      resolvedHost.length() && hostViaMdns ? COL_CRT : COL_CRT_YELLOW);
  row("TOKEN", sizeof(HOST_TOKEN) > 1 ? "set" : "EMPTY",
      sizeof(HOST_TOKEN) > 1 ? COL_CRT : COL_CRT_YELLOW);
  row("LAST", everOk ? String((millis() - lastOkMs) / 1000) + "s ago"
                     : String("never"),
      everOk ? COL_CRT : COL_CRT_YELLOW);
  row("WHY", netErr.length() ? netErr : String("unknown"), COL_CRT_YELLOW);

  // Size 2 is the minimum display type everywhere else; keep the instruction
  // short enough to remain readable at that size instead of shrinking it.
  drawCentered("START HOST ON MAC, THEN WAIT",
#if defined(BOARD_ROUND_UI)
               390,
#else
               scrH() - pad - 24,
#endif
               2, COL_CRT_DIM);
  gfx->flush();
}

// ---- 8-bit CRT boot ----
static int16_t bootY = 0;
static uint8_t bootBlink = 0;

// The checklist gets longer than the panel when Wi-Fi is the first path. Keep
// the lower border band clear and scroll completed rows inside the ROM frame;
// redrawing the chrome is cheap and avoids making the panel's framebuffer a
// second scrolling primitive.
static const int16_t BOOT_ROW_STEP = 20;
static const uint8_t BOOT_STATUS_MAX = 32;
struct BootStatusLine {
  char label[12];
  char status[40];
  uint16_t color;
};
static BootStatusLine bootStatusLines[BOOT_STATUS_MAX];
static uint8_t bootStatusN = 0;

static int16_t bootLastY() {
#if defined(BOARD_ROUND_UI)
  return 350;
#else
  return (int16_t)(scrH() - UI_PAD - 48);
#endif
}

static uint8_t bootVisibleRows() {
#if defined(BOARD_ROUND_UI)
  const int16_t top = 80;
#else
  const int16_t top = UI_PAD + 42;
#endif
  if (bootLastY() < top) return 1;
  return (uint8_t)((bootLastY() - top) / BOOT_ROW_STEP + 1);
}

static void bootScanlines() {
#if defined(BOARD_ROUND_UI)
  for (int16_t y = 72; y < 382; y += 3) {
    int16_t x = 0, w = 0;
    roundBand(y, y, 5, &x, &w);
    gfx->drawFastHLine(x, y, w, COL_CRT_SCAN);
  }
#else
  const int16_t x0 = UI_PAD + 4;
  const int16_t x1 = scrW() - UI_PAD - 4;
  const int16_t y0 = UI_PAD + 36;
  const int16_t y1 = scrH() - UI_PAD - 4;
  for (int16_t y = y0; y < y1; y += 3) {
    gfx->drawFastHLine(x0, y, (int16_t)(x1 - x0), COL_CRT_SCAN);
  }
#endif
}

static void bootChrome() {
  gfx->clear(COL_CRT_BG);
#if defined(BOARD_ROUND_UI)
  gfx->drawCircle(ROUND_CX, ROUND_CY, ROUND_R, COL_CRT);
  gfx->drawCircle(ROUND_CX, ROUND_CY, (int16_t)(ROUND_R - 3), COL_CRT_DIM);
  int16_t headerX = 0, headerW = 0;
  roundBand(27, 69, 5, &headerX, &headerW);
  gfx->fillRect(headerX, 27, headerW, 42, COL_CRT_HDR);
  drawCentered("HEADROOM", 34, 2, COL_CRT);
  drawCentered("ROM", 54, 1, COL_CRT_DIM);
  gfx->drawFastHLine(headerX, 70, headerW, COL_CRT);
  bootScanlines();
  bootY = 80;
#else
  const int16_t pad = UI_PAD;
  const int16_t inner = pad + 3;
  gfx->drawRect(pad, pad, scrW() - pad * 2, scrH() - pad * 2, COL_CRT);
  gfx->drawRect(inner, inner, scrW() - inner * 2, scrH() - inner * 2, COL_CRT_DIM);
  gfx->fillRect(inner + 1, inner + 1, scrW() - inner * 2 - 2, 26,
                COL_CRT_HDR);
  drawTextAt("HEADROOM", pad + 10, pad + 8, 2, COL_CRT);
  drawTextAt("ROM", scrW() - pad - 42, pad + 8, 2, COL_CRT_DIM);
  gfx->drawFastHLine(inner + 1, pad + 30, scrW() - inner * 2 - 2, COL_CRT);
  bootScanlines();
  bootY = pad + 42;
#endif
}

static void bootFlush() {
  bootBlink ^= 1;
#if defined(BOARD_ROUND_UI)
  const int16_t cx = (int16_t)(ROUND_CX + 72);
  const int16_t cy = 52;
#else
  const int16_t cx = scrW() - UI_PAD - 18;
  const int16_t cy = UI_PAD + 8;
#endif
  gfx->fillRect(cx, cy, 8, 14, bootBlink ? COL_CRT : COL_CRT_HDR);
  gfx->flush();
}

// ---- Max Headroom cold-boot splash ----
// Deliberately off-palette: the amber ROM chrome below is the machine at work,
// this is the machine showing off. The vertical roll at the end is the hinge
// between the two. Mask, copper tables and previews all come out of
// scripts/render_esp32_boot.py — nothing here is hand-tuned art.
//
// The head is a mask, not a picture: everything inside the silhouette resolves
// to the copper bar field, so no face is ever drawn. Only the lenses and the
// vector outline are painted as themselves.
static const uint16_t COL_MX_BG   = RGB565(6, 4, 14);
static const uint16_t COL_MX_VOID = RGB565(0, 0, 0);        // lenses, mouth
static const uint16_t COL_MX_EDGE = RGB565(255, 255, 255);  // vector outline
static const uint16_t COL_MX_TEXT = RGB565(255, 255, 255);
static const uint16_t COL_MX_TMID = RGB565(150, 190, 235);  // chrome bevel
static const uint16_t COL_MX_TLOW = RGB565(40, 70, 150);
static const uint16_t COL_MX_GHC  = RGB565(0, 190, 210);    // chroma-split ghosts
static const uint16_t COL_MX_GHM  = RGB565(215, 30, 130);

// Backdrop wedges. Kept dim on purpose — the copper head has to stay the
// brightest thing in the frame or the whole picture turns to noise.
static const uint16_t MX_RAY[4] = {
    RGB565(64, 12, 46), RGB565(0, 52, 64),
    RGB565(26, 58, 30), RGB565(72, 56, 14),
};

static const int16_t MX_SCALE = 5;
static const uint8_t MX_RAY_COUNT = 26;
static const float MX_RAY_DUTY = 0.22f;  // lit fraction of each slot
// Backdrop rotation per frame — slow enough to read as a sweep, not a spin.
static const float MX_RAY_STEP = 0.035f;
// Copper scroll per frame, in scanlines.
static const int16_t MX_COP_STEP = 5;

// Everything about Max's placement is relative to the canvas so the same
// splash centres on 448×368 and 480×480 without a second set of constants.
static inline int16_t mxHeadW() { return (int16_t)(MAX_W * MX_SCALE); }
static inline int16_t mxHeadH() { return (int16_t)(MAX_H * MX_SCALE); }
static inline int16_t mxHeadX() { return (int16_t)((scrW() - mxHeadW()) / 2); }
static inline int16_t mxHeadY() { return (int16_t)((scrH() - mxHeadH()) / 2); }
static inline int16_t mxHeadCX() { return (int16_t)(mxHeadX() + mxHeadW() / 2); }
static inline int16_t mxHeadCY() { return (int16_t)(mxHeadY() + mxHeadH() / 2); }
// Wordmark sits just above the silhouette; scroller hugs the bottom inset.
static inline int16_t mxTitleY() {
  const int16_t y = (int16_t)(mxHeadY() - 40);
  return y < 8 ? (int16_t)8 : y;
}
static inline int16_t mxScrollY() { return (int16_t)(scrH() - 44); }
static inline float mxRayR() {
  const float hw = (float)scrW() * 0.5f;
  const float hh = (float)scrH() * 0.5f;
  return sqrtf(hw * hw + hh * hh) * 1.15f;
}

static inline uint8_t mxPixel(int16_t sx, int16_t sy) {
  const uint8_t b = pgm_read_byte(&MAX_PIX[(int32_t)sy * MAX_STRIDE + (sx >> 1)]);
  return (sx & 1) ? (uint8_t)(b & 0x0F) : (uint8_t)(b >> 4);
}

// Resolve one mask pixel against the copper field at this screen row.
static uint16_t mxInk(uint8_t ink, int16_t y, int16_t cop, bool dimRow) {
  if (ink == MAX_INK_VOID) return dimRow ? COL_MX_BG : COL_MX_VOID;
  if (ink == MAX_INK_EDGE) return COL_MX_EDGE;
  int32_t i = y + cop;
  // The quiff runs half a band out of phase with the face. One continuous
  // field turned the silhouette into a smooth egg — the phase break is what
  // puts the hairline back without ever drawing a hairline.
  if (ink == MAX_INK_HAIR) i += MAX_COPPER_BAND / 2;
  i %= MAX_COPPER_N;
  if (i < 0) i += MAX_COPPER_N;
  return dimRow ? (uint16_t)pgm_read_word(&MAX_COPPER_DIM[i])
                : (uint16_t)pgm_read_word(&MAX_COPPER[i]);
}

// A mask row never has more spans than this; guarded rather than sized to the
// worst case, since overflowing would silently clip the right of the face.
static const uint8_t MX_MAX_SPANS = 24;

// Drawn one screen scanline at a time, not one mask block at a time: the
// copper colour changes every screen row, so filling a 5px block in a single
// colour would quantise the bars to the mask grid and flatten the gradient.
//   shear — px of lean across the full height (the idle bob)
//   rows  — mask rows to draw, top down (the wipe-in)
//   tint  — non-zero forces everything flat (the chroma-split ghosts)
static void mxHead(int16_t x0, int16_t y0, int16_t sy, int16_t shear,
                   int16_t rows, uint16_t tint, int16_t cop) {
  if (rows > MAX_H) rows = MAX_H;
  struct Span { int16_t x, w; uint8_t ink; } span[MX_MAX_SPANS];
  for (int16_t r = 0; r < rows; r++) {
    const int16_t lean = (int16_t)((shear * (2 * r - MAX_H)) / (2 * MAX_H));
    uint8_t n = 0;
    int16_t c = 0;
    while (c < MAX_W) {
      const uint8_t ink = mxPixel(c, r);
      int16_t run = 1;
      while (c + run < MAX_W && mxPixel((int16_t)(c + run), r) == ink) run++;
      if (ink && n < MX_MAX_SPANS) {
        span[n].x = (int16_t)(c + lean);
        span[n].w = run;
        span[n].ink = ink;
        n++;
      }
      c = (int16_t)(c + run);
    }
    for (int16_t sub = 0; sub < sy; sub++) {
      const int16_t py = (int16_t)(y0 + r * sy + sub);
      const bool dimRow = (sub == sy - 1) && sy > 1;
      for (uint8_t i = 0; i < n; i++) {
        gfx->drawFastHLine((int16_t)(x0 + span[i].x * MX_SCALE), py,
                           (int16_t)(span[i].w * MX_SCALE),
                           tint ? tint : mxInk(span[i].ink, py, cop, dimRow));
      }
    }
  }
}

// Wedges radiating from a vanishing point behind his head — the show's
// standing backdrop. squeeze < 1 flattens them toward mid-screen for the roll.
static void mxRays(float phase, float squeeze) {
  gfx->clear(COL_MX_BG);
  const float vx = (float)mxHeadCX();
  const float mid = (float)(scrH() / 2);
  // Vanishing point sits on the head centre at full squeeze; collapses to
  // mid-screen as the picture rolls shut.
  const float vy = mid + ((float)mxHeadCY() - mid) * squeeze;
  const float rayR = mxRayR();
  const float step = 6.2831853f / MX_RAY_COUNT;
  for (uint8_t i = 0; i < MX_RAY_COUNT; i++) {
    const float a0 = phase + i * step;
    const float a1 = a0 + step * MX_RAY_DUTY;
    gfx->fillTriangle(
        (int16_t)vx, (int16_t)vy,
        (int16_t)(vx + rayR * cosf(a0)),
        (int16_t)(vy + rayR * sinf(a0) * squeeze),
        (int16_t)(vx + rayR * cosf(a1)),
        (int16_t)(vy + rayR * sinf(a1) * squeeze),
        MX_RAY[i & 3]);
  }
}

// gfx_font/Arduino_GFX both advance a fixed 6*size cell per glyph.
static inline int16_t mxTextW(const char *s, uint8_t size) {
  return (int16_t)(strlen(s) * 6 * size);
}

// Three passes at one-pixel offsets — the cheap bitmap-font chrome bevel. A
// real gradient fill needs a mask the panel can't afford; stacking dark, mid
// and bright copies gets the same read for the price of three blits.
static void mxChromeText(const char *s, int16_t x, int16_t y, uint8_t size) {
  drawTextAt(s, x, (int16_t)(y + 2), size, COL_MX_TLOW);
  drawTextAt(s, x, (int16_t)(y + 1), size, COL_MX_TMID);
  drawTextAt(s, x, y, size, COL_MX_TEXT);
}

static const char MX_SCROLL[] =
    "HEADROOM ... 20 MINUTES INTO THE FUTURE ... C-C-CATCH THE WAVE ... ";
static const int16_t MX_SCROLL_AMP = 8;
// Pixels of travel per frame. Must stay well clear of the 12px character cell:
// at 11 it aliased to -1px and the crawl appeared to run backwards.
static const int16_t MX_SCROLL_STEP = 5;

// Sine scroller: per-character vertical offset off a travelling wave.
static void mxScroller(int16_t offset, int16_t y) {
  const int16_t cell = 12;    // 6px cell at text size 2
  const int16_t n = (int16_t)(sizeof(MX_SCROLL) - 1);
  // Own band, so the wave never fights the silhouette behind it.
  gfx->fillRect(0, (int16_t)(y - MX_SCROLL_AMP - 4), scrW(),
                (int16_t)(MX_SCROLL_AMP * 2 + 20), COL_MX_BG);
  const int16_t first = (int16_t)(offset / cell);
  for (int16_t i = 0; i < scrW() / cell + 2; i++) {
    int16_t idx = (int16_t)((first + i) % n);
    if (idx < 0) idx = (int16_t)(idx + n);
    const char ch = MX_SCROLL[idx];
    if (ch == ' ') continue;
    const int16_t x = (int16_t)(i * cell - (offset % cell));
    const int16_t wave = (int16_t)(sinf((offset + x) * 0.021f) * MX_SCROLL_AMP);
    const char buf[2] = {ch, 0};
    drawTextAt(buf, x, (int16_t)(y + wave), 2, COL_MX_TEXT);
  }
}

// Wordmark over the head with the scroller beneath. No plate behind either —
// a solid panel reads as a UI card, and this is meant to read as an intro.
static void mxTitle(bool stutter, int16_t phase) {
  const char *word = stutter ? "H-H-HEADROOM" : "HEADROOM";
  mxChromeText(word, (int16_t)((scrW() - mxTextW(word, 4)) / 2),
               mxTitleY(), 4);
  mxScroller(phase, mxScrollY());
}

// Hold for a target frame time measured from before the draw, so a slow flush
// eats its own budget instead of stretching the whole sequence.
static void mxFrame(uint32_t startMs, uint32_t targetMs) {
  gfx->flush();
  const uint32_t spent = millis() - startMs;
  if (spent < targetMs) delay(targetMs - spent);
}

static void maxSplash() {
  const int16_t hx = mxHeadX();
  const int16_t hy = mxHeadY();
  uint32_t t;

  gfx->clear(COL_MX_BG);
  gfx->flush();
  delay(200);

  // CRT strikes: a sliver at mid-screen opens up.
  static const int16_t slivers[] = {1, 4, 10, 24};
  for (uint8_t i = 0; i < 4; i++) {
    t = millis();
    gfx->clear(COL_MX_BG);
    const int16_t mid = (int16_t)(scrH() / 2);
    gfx->fillRect(0, (int16_t)(mid - slivers[i]), scrW(),
                  (int16_t)(slivers[i] * 2 + 1), MX_RAY[0]);
    gfx->drawFastHLine(0, mid, scrW(), COL_WHITE);
    mxFrame(t, 40);
  }

  // He wipes in over the backdrop, top down.
  static const int16_t wipe[] = {11, 21, 31, 41, 51, MAX_H};
  for (uint8_t i = 0; i < 6; i++) {
    t = millis();
    mxRays(i * MX_RAY_STEP, 1.0f);
    mxHead(hx, hy, MX_SCALE, 0, wipe[i], 0, (int16_t)(i * MX_COP_STEP));
    mxFrame(t, 50);
  }

  // Idle bob.
  static const int16_t lean[] = {0, 2, 3, 2, 0, -2, -3};
  for (uint8_t i = 0; i < 7; i++) {
    t = millis();
    mxRays((6 + i) * MX_RAY_STEP, 1.0f);
    mxHead(hx, hy, MX_SCALE, lean[i], MAX_H, 0, (int16_t)((6 + i) * MX_COP_STEP));
    mxFrame(t, 70);
  }

  // The stutter. Ghosts first so the real head covers them except at the edges.
  for (uint8_t i = 0; i < 3; i++) {
    t = millis();
    mxRays((13 + i) * MX_RAY_STEP, 1.0f);
    mxHead((int16_t)(hx - 4), hy, MX_SCALE, -4, MAX_H, COL_MX_GHC, 0);
    mxHead((int16_t)(hx + 4), hy, MX_SCALE, -4, MAX_H, COL_MX_GHM, 0);
    mxHead(hx, hy, MX_SCALE, -4, MAX_H, 0, (int16_t)((13 + i) * MX_COP_STEP));
    mxTitle(true, (int16_t)(i * MX_SCROLL_STEP));
    // Tear bands ride the silhouette, not absolute panel rows.
    gfx->tearRows((int16_t)(hy + 36), 22, 14, COL_MX_BG);
    gfx->tearRows((int16_t)(hy + 108), 16, -22, COL_MX_BG);
    gfx->tearRows((int16_t)(hy + 190), 12, 9, COL_MX_BG);
    mxFrame(t, 70);
  }

  t = millis();
  mxRays(16 * MX_RAY_STEP, 1.0f);
  mxHead(hx, hy, MX_SCALE, -3, MAX_H, 0, (int16_t)(16 * MX_COP_STEP));
  mxFrame(t, 60);

  // Title card holds while Wi-Fi associates in the background.
  for (uint8_t i = 0; i < 14; i++) {
    t = millis();
    mxRays((17 + i) * MX_RAY_STEP, 1.0f);
    mxHead(hx, hy, MX_SCALE, (i % 4) < 2 ? 2 : 3, MAX_H, 0,
           (int16_t)((17 + i) * MX_COP_STEP));
    mxTitle(false, (int16_t)(40 + i * MX_SCROLL_STEP));
    mxFrame(t, 100);
  }

  // Vertical roll — the picture collapses to a line and the ROM page takes over.
  static const float squeeze[] = {0.70f, 0.40f, 0.16f, 0.05f};
  for (uint8_t i = 0; i < 4; i++) {
    t = millis();
    int16_t sy = (int16_t)(MX_SCALE * squeeze[i]);
    if (sy < 1) sy = 1;
    mxRays(0.2f, squeeze[i]);
    mxHead(hx, (int16_t)((scrH() - MAX_H * sy) / 2), sy, 0, MAX_H, 0,
           (int16_t)(31 * MX_COP_STEP));
    gfx->drawFastHLine(0, (int16_t)(scrH() / 2), scrW(), COL_WHITE);
    mxFrame(t, 45);
  }
}

// A warm reboot is almost always an OTA push or a watchdog bite — nobody is
// watching, and the dev loop shouldn't pay four seconds for the show. Holding
// BOOT at power-on skips it too.
static bool wantMaxSplash() {
  if (!displayBootSplash) return false;
  if (digitalRead(BTN_BOOT) == LOW) return false;
  const esp_reset_reason_t why = esp_reset_reason();
  return why == ESP_RST_POWERON || why == ESP_RST_BROWNOUT;
}

static void bootSplash() {
  bootStatusN = 0;
  if (wantMaxSplash()) {
    maxSplash();
  } else {
    bootChrome();
    drawCentered("20 MINUTES", scrH() / 2 - 28, 2, COL_CRT_DIM);
    drawCentered("INTO THE FUTURE", scrH() / 2 - 6, 2, COL_CRT);
    drawCentered("* SYSTEM ONLINE *", scrH() / 2 + 28, 2, COL_CRT_DIM);
    gfx->fillRect(scrW() / 2 - 60, scrH() / 2 + 50, 120, 4, COL_CRT);
    bootFlush();
    delay(550);
  }
  bootChrome();
  bootFlush();
}

static void bootDrawLineAt(const char *label, const char *status,
                           uint16_t statusCol, int16_t y) {
#if defined(BOARD_ROUND_UI)
  int16_t x0 = 0, rowW = 0;
  roundBand(y, (int16_t)(y + 15), 10, &x0, &rowW);
  const int16_t xMax = (int16_t)(x0 + rowW);
#else
  const int16_t x0 = UI_PAD + 10;
  const int16_t xMax = scrW() - UI_PAD - 10;
#endif
  gfx->setTextSize(2);
  int16_t x1, y1; uint16_t lw, lh, rw, rh, dw, dh;
  gfx->getTextBounds(label, 0, 0, &x1, &y1, &lw, &lh);
  gfx->getTextBounds(".", 0, 0, &x1, &y1, &dw, &dh);
  if (dw < 1) dw = 6;

  // Keep room for label + a few leader dots; show as many status chars as fit.
  const int16_t budget =
      (int16_t)(xMax - x0 - (int16_t)lw - 4 - (int16_t)(dw + 2) * 3);
  char right[40];
  size_t sn = strlen(status);
  if (sn >= sizeof(right)) sn = sizeof(right) - 1;
  memcpy(right, status, sn);
  right[sn] = '\0';
  while (sn > 0) {
    gfx->getTextBounds(right, 0, 0, &x1, &y1, &rw, &rh);
    if ((int16_t)rw <= budget) break;
    right[--sn] = '\0';
  }
  if (sn == 0) rw = 0;

  // Label (bright) … leaders (faded) … status
  drawTextAt(label, x0, y, 2, COL_CRT);
  const int16_t statusX = (int16_t)(xMax - (int16_t)rw);
  gfx->setTextColor(statusCol);
  gfx->setCursor(statusX, y);
  gfx->print(right);

  int16_t dotX = (int16_t)(x0 + (int16_t)lw + 4);
  const int16_t dotEnd = (int16_t)(statusX - 4);
  gfx->setTextColor(COL_CRT_DIM);
  while (dotX + (int16_t)dw <= dotEnd) {
    gfx->setCursor(dotX, y);
    gfx->print('.');
    dotX = (int16_t)(dotX + (int16_t)dw + 2);
  }
}

static void bootRedrawStatus() {
  bootChrome();
  const uint8_t visible = bootVisibleRows();
  const uint8_t first = bootStatusN > visible ? bootStatusN - visible : 0;
#if defined(BOARD_ROUND_UI)
  const int16_t rowTop = 80;
#else
  const int16_t rowTop = UI_PAD + 42;
#endif
  uint8_t shown = 0;
  for (uint8_t i = first; i < bootStatusN; i++) {
    bootDrawLineAt(bootStatusLines[i].label, bootStatusLines[i].status,
                   bootStatusLines[i].color,
                   (int16_t)(rowTop + shown * BOOT_ROW_STEP));
    shown++;
  }
  bootY = (int16_t)(rowTop + shown * BOOT_ROW_STEP);
}

static void bootLine(const char *label, const char *status, uint16_t statusCol) {
  if (bootStatusN < BOOT_STATUS_MAX) {
    strlcpy(bootStatusLines[bootStatusN].label, label,
            sizeof(bootStatusLines[bootStatusN].label));
    strlcpy(bootStatusLines[bootStatusN].status, status,
            sizeof(bootStatusLines[bootStatusN].status));
    bootStatusLines[bootStatusN].color = statusCol;
    bootStatusN++;
  } else {
    // Keep the newest status visible if a future boot path grows beyond the
    // fixed diagnostic history as well as beyond the panel height.
    for (uint8_t i = 1; i < BOOT_STATUS_MAX; i++)
      bootStatusLines[i - 1] = bootStatusLines[i];
    strlcpy(bootStatusLines[BOOT_STATUS_MAX - 1].label, label,
            sizeof(bootStatusLines[BOOT_STATUS_MAX - 1].label));
    strlcpy(bootStatusLines[BOOT_STATUS_MAX - 1].status, status,
            sizeof(bootStatusLines[BOOT_STATUS_MAX - 1].status));
    bootStatusLines[BOOT_STATUS_MAX - 1].color = statusCol;
  }

  bootRedrawStatus();

  bootFlush();
  delay(55);
}

// Growing dots after the label, marching toward the right edge (not a 4-dot loop).
static void bootProgress(const char *label, uint8_t step) {
  const int16_t y = bootY > bootLastY() ? bootLastY() : bootY;
#if defined(BOARD_ROUND_UI)
  int16_t x0 = 0, bw = 0;
  roundBand(y, (int16_t)(y + 17), 10, &x0, &bw);
  const int16_t xMax = (int16_t)(x0 + bw);
#else
  const int16_t x0 = UI_PAD + 10;
  const int16_t xMax = scrW() - UI_PAD - 10;
  const int16_t bw = (int16_t)(xMax - x0);
#endif
  gfx->fillRect(x0, y, bw, 18, COL_CRT_BG);
  for (int16_t sy = y; sy < y + 18; sy += 3)
    gfx->drawFastHLine(x0, sy, bw, COL_CRT_SCAN);

  drawTextAt(label, x0, y, 2, COL_CRT);

  int16_t x1, y1; uint16_t lw, lh, dw, dh;
  gfx->setTextSize(2);
  gfx->getTextBounds(label, 0, 0, &x1, &y1, &lw, &lh);
  gfx->getTextBounds(".", 0, 0, &x1, &y1, &dw, &dh);
  if (dw < 1) dw = 6;

  const int16_t start = (int16_t)(x0 + (int16_t)lw + 4);
  const int16_t gap = (int16_t)(dw + 2);
  uint8_t maxDots = 0;
  for (int16_t x = start; x + (int16_t)dw <= xMax; x = (int16_t)(x + gap))
    maxDots++;
  if (maxDots < 1) maxDots = 1;

  uint8_t dots = (uint8_t)(step + 1);
  if (dots > maxDots) dots = maxDots;

  gfx->setTextColor(COL_CRT);
  int16_t dotX = start;
  for (uint8_t i = 0; i < dots; i++) {
    gfx->setCursor(dotX, y);
    gfx->print('.');
    dotX = (int16_t)(dotX + gap);
  }
  bootFlush();
}

// Pill progress bar + optional pace dot (where a linear burn would be right
// now in the window). Same mark as drawPaceRing — white disc sized off the
// bar height — so bars and rings read as one system. Keep provider accent
// even at 100%.
static void drawBar(int16_t x, int16_t y, int16_t w, int16_t h,
                    float pct, float pacePct, uint16_t accent) {
  gfx->fillRoundRect(x, y, w, h, h / 2, COL_BAR);
  float p = pct < 0 ? 0 : (pct > 100 ? 100 : pct);
  int16_t fill = (int16_t)((w * p) / 100.0f + 0.5f);
  if (fill < h) fill = (p > 0) ? h : 0;
  if (fill > 0) {
    if (fill > w) fill = w;
    gfx->fillRoundRect(x, y, fill, h, h / 2, accent);
  }
  if (pacePct >= 0) {
    float pp = pacePct > 100 ? 100 : pacePct;
    // Radius matches drawPaceDot: thick * 5/14 → diameter thick * 5/7.
    const int16_t dot = (int16_t)lroundf(h * 5.0f / 14.0f);
    int16_t cx = x + (int16_t)((w * pp) / 100.0f + 0.5f);
    if (cx < x + dot) cx = x + dot;
    if (cx > x + w - 1 - dot) cx = x + w - 1 - dot;
    gfx->fillCircle(cx, (int16_t)(y + h / 2), dot, COL_WHITE);
  }
}

// Compact full-width meter for model pages: title, bar, pct + reset on one
// line. Sized so two of them fit in the top half and leave the bottom half
// for the burndown chart (side-by-side columns were too narrow for the labels).
static const int16_t QUOTA_ROW_H = 54;

static void drawQuotaRowCompact(const char *title, float pct, float pace,
                                const String &resets, int16_t y, int16_t pad,
                                uint16_t accent) {
  const int16_t x = pad;
  const int16_t w = (int16_t)(scrW() - pad * 2);
  char buf[48];
  char pctBuf[12];
  drawTextAt(title, x, y, 2, COL_WHITE);
  drawBar(x, y + 18, w, 10, pct, pace, accent);
  snprintf(buf, sizeof buf, "%s used", fmtPct(pctBuf, sizeof pctBuf, pct));
  drawTextAt(buf, x, y + 36, 2, COL_WHITE);
  if (resets.length()) {
    snprintf(buf, sizeof buf, "Resets in %s", resets.c_str());
    drawRightAt(buf, (int16_t)(x + w), y + 36, 2, COL_DIM);
  }
}

// glcdfont is ASCII-only. Host copy uses middot (U+00B7 = UTF-8 C2 B7) which
// otherwise paints as two garbage glyphs — "On pace XX 15%". device_view
// already substitutes, but older hosts and firmware-built strings still need
// this pass.
static String boardAscii(const char *s) {
  String out;
  if (!s || !s[0]) return out;
  out.reserve(strlen(s));
  while (*s) {
    const unsigned char c = (unsigned char)*s;
    if (c < 0x80) {
      out += (char)c;
      s++;
      continue;
    }
    // Middot · and the common dash codepoints → ASCII hyphen.
    if (c == 0xC2 && (unsigned char)s[1] == 0xB7) {  // U+00B7
      out += '-';
      s += 2;
      continue;
    }
    if (c == 0xE2 && (unsigned char)s[1] == 0x80 &&
        ((unsigned char)s[2] == 0x93 || (unsigned char)s[2] == 0x94)) {
      // U+2013 en-dash / U+2014 em-dash
      out += '-';
      s += 3;
      continue;
    }
    // Skip any other multi-byte sequence so we don't emit continuation bytes.
    if ((c & 0xE0) == 0xC0 && s[1]) { s += 2; out += '-'; continue; }
    if ((c & 0xF0) == 0xE0 && s[1] && s[2]) { s += 3; out += '-'; continue; }
    if ((c & 0xF8) == 0xF0 && s[1] && s[2] && s[3]) {
      s += 4;
      out += '-';
      continue;
    }
    s++;
  }
  return out;
}

static String truncFit(const String &s, int16_t maxW, uint8_t size) {
  // Ellipsize so a line fits in maxW pixels at the given text size.
  gfx->setTextSize(size);
  int16_t x1, y1; uint16_t tw, th;
  gfx->getTextBounds(s.c_str(), 0, 0, &x1, &y1, &tw, &th);
  if ((int16_t)tw <= maxW) return s;
  String out = s;
  while (out.length() > 1) {
    out.remove(out.length() - 1);
    String trial = out + "...";
    gfx->getTextBounds(trial.c_str(), 0, 0, &x1, &y1, &tw, &th);
    if ((int16_t)tw <= maxW) return trial;
  }
  return "...";
}

// Hard-clip to maxW — no ellipsis, so more of the real name stays visible.
static String clipFit(const String &s, int16_t maxW, uint8_t size) {
  gfx->setTextSize(size);
  int16_t x1, y1; uint16_t tw, th;
  gfx->getTextBounds(s.c_str(), 0, 0, &x1, &y1, &tw, &th);
  if ((int16_t)tw <= maxW) return s;
  String out = s;
  while (out.length() > 0) {
    out.remove(out.length() - 1);
    gfx->getTextBounds(out.c_str(), 0, 0, &x1, &y1, &tw, &th);
    if ((int16_t)tw <= maxW) return out;
  }
  return "";
}

// Name left + age right-aligned; clip name to one space before the age.
static void drawNameAgoRow(int16_t x, int16_t y, int16_t colW,
                           const String &name, const String &ago,
                           uint16_t nameCol, uint16_t agoCol) {
  gfx->setTextSize(2);
  int16_t x1, y1; uint16_t aw, ah, sw, sh;
  gfx->getTextBounds(ago.c_str(), 0, 0, &x1, &y1, &aw, &ah);
  gfx->getTextBounds(" ", 0, 0, &x1, &y1, &sw, &sh);  // one space gap
  String clipped = clipFit(name, (int16_t)(colW - (int16_t)aw - (int16_t)sw), 2);
  drawTextAt(clipped, x, y, 2, nameCol);
  gfx->setTextColor(agoCol);
  gfx->setCursor(x + colW - (int16_t)aw, y);
  gfx->print(ago);
}

// Activity dots stay neutral — status words carry good vs bad
// (same rule as the Mac GitHub list).
static uint16_t statusColor(const String &status) {
  (void)status;
  return COL_DIM;
}

// Compact "ago" for glance columns: hours first, then days (not always hours).
static String glanceAgo(const String &ago) {
  // ASCII only: gfx->print walks bytes into the 5x7 table, so a UTF-8 dash
  // would paint three CP437 glyphs.
  if (!ago.length()) return "-";
  int days = 0, hours = 0, minutes = 0;
  const char *p = ago.c_str();
  while (*p) {
    while (*p == ' ') p++;
    if (!*p) break;
    char *end = nullptr;
    long v = strtol(p, &end, 10);
    if (end == p) break;
    if (*end == 'd' || *end == 'D') { days = (int)v; p = end + 1; }
    else if (*end == 'h' || *end == 'H') { hours = (int)v; p = end + 1; }
    else if (*end == 'm' || *end == 'M') { minutes = (int)v; p = end + 1; }
    else if (*end == 's' || *end == 'S') { p = end + 1; }  // ignore seconds
    else break;
  }
  const int totalH = days * 24 + hours;
  char b[12];
  if (totalH >= 24) {
    snprintf(b, sizeof b, "%dd", totalH / 24);
  } else if (totalH >= 1) {
    snprintf(b, sizeof b, "%dh", totalH);
  } else if (minutes >= 1) {
    snprintf(b, sizeof b, "%dm", minutes);
  } else {
    snprintf(b, sizeof b, "0h");
  }
  return String(b);
}

// "…T14:32:00+0200" -> "14:32". Every page shows this, so it gets a buffer
// rather than five substring() allocations per frame.
static bool updatedHHMM(char *buf, size_t n) {
  if (updatedZ.length() < 16 || n < 6) {
    buf[0] = '\0';
    return false;
  }
  memcpy(buf, updatedZ.c_str() + 11, 5);
  buf[5] = '\0';
  return true;
}

static const char *pageName(Page p) {
  if (isSlotPage(p)) {
    const uint8_t i = slotOf(p);
    return i < slotN ? slots[i].title.c_str() : "?";
  }
  switch (p) {
    case PAGE_GLANCE: return "Headroom";
    case PAGE_VERCEL: return "Vercel";
    case PAGE_GIT:    return "Git";
    case PAGE_LOCAL:  return "Local";
    default:          return "?";
  }
}

// Blend a colour toward the background. RGB565 has to be unpacked to do it.
static uint16_t dimToward(uint16_t color, uint16_t bg, float factor);
static void playResetCelebration(uint16_t accent);

// Footprint the home page reserves for the link glyph, bottom-right.
static const int16_t LINK_GLYPH_W = 18;
static const int16_t LINK_GLYPH_H = 15;

// Compact enough for the corner: "42m", "11h", "3d". Minutes alone was fine
// for a dropped link, which is minutes old by the time anyone looks, and
// useless for a frozen reading — the case that sent us here would have drawn
// "696m".
static void formatAge(char *buf, size_t n, uint32_t seconds) {
  if (seconds < 3600UL) {
    snprintf(buf, n, "%lum", (unsigned long)(seconds / 60UL));
  } else if (seconds < 86400UL) {
    snprintf(buf, n, "%luh", (unsigned long)(seconds / 3600UL));
  } else {
    snprintf(buf, n, "%lud", (unsigned long)(seconds / 86400UL));
  }
}

static void formatLastLinkAge(char *buf, size_t n) {
  if (!everOk) {
    snprintf(buf, n, "--m");
    return;
  }
  formatAge(buf, n, (millis() - lastOkMs) / 1000UL);
}

// Which pipe fed the numbers above: Wi-Fi arcs, or a cable.connector for USB.
// (rightX, bottomY) is the bottom-right corner the glyph is tucked into.
static void drawLinkGlyph(int16_t rightX, int16_t bottomY) {
  // Two different ways the numbers above can be wrong, and the glyph has to
  // answer both. `hostOk` is the cable: it says whether the Mac answered.
  // Stale sources are the payload: the Mac answers every ten seconds and has
  // been unable to refresh a source since last night. Gating on `hostOk`
  // alone drew nothing for the second case, which is the one that lasts.
  const int32_t staleFor = sourcesWorstStaleS();
  const bool signIn = sourcesNeedSignIn();
  const bool suspect = !hostOk || staleFor >= 0 || signIn;
  const uint16_t col = suspect ? COL_CRT_YELLOW : COL_DIM;
  const int16_t gx = (int16_t)(rightX - LINK_GLYPH_W);
  const int16_t gy = (int16_t)(bottomY - LINK_GLYPH_H);

  if (suspect) {
    char age[8];
    char label[12];
    if (!hostOk) {
      formatLastLinkAge(age, sizeof age);
    } else if (staleFor >= 0) {
      formatAge(age, sizeof age, (uint32_t)staleFor);
    } else {
      // A dead login the host caught before it had anything to replay: no
      // frozen reading, so no age to report, but still worth marking.
      snprintf(age, sizeof age, "--");
    }
    // The Mac names the source and the fix. This only has to say the reading
    // is not what it looks like, and which kind of not.
    snprintf(label, sizeof label, "%s%s", signIn ? "!" : "", age);
    drawRightAt(label, (int16_t)(gx - 6), gy, 2, col);
  }

  if (linkVia == LINK_USB) {
    // SF Symbol-style cable.connector: tip, housing, cable. Tip is lighter so
    // the metal insert reads against the grip, same hierarchy as the symbol.
    const int16_t cx = (int16_t)(gx + LINK_GLYPH_W / 2);
    const uint16_t tip = dimToward(COL_WHITE, col, 0.55f);
    gfx->fillRoundRect((int16_t)(cx - 4), gy, 8, 3, 1, tip);
    gfx->fillRoundRect((int16_t)(cx - 5), (int16_t)(gy + 3), 10, 7, 2, col);
    gfx->fillRoundRect((int16_t)(cx - 1), (int16_t)(gy + 10), 2, 5, 1, col);
    return;
  }

  // Wi-Fi: three arcs fanning up from a dot. -90 is 12 o'clock here, so a
  // -135..-45 sweep is the upward 90 degree fan.
  const int16_t cx = (int16_t)(gx + LINK_GLYPH_W / 2);
  const int16_t cy = (int16_t)(bottomY - 2);
  gfx->fillArc(cx, cy, 13, 11, -135, -45, col);
  gfx->fillArc(cx, cy, 8, 6, -135, -45, col);
  gfx->fillCircle(cx, (int16_t)(cy - 1), 1, col);
}

// Footprint the home page reserves for the power glyph, bottom-left.
// Matches the link glyph's band so one lowBottom covers both corners.
static const int16_t POWER_GLYPH_W = 22;
static const int16_t POWER_GLYPH_H = 14;

// Tiny lightning bolt for "charging" — three stacked wedges, SF Symbol scale.
static void drawChargeBolt(int16_t cx, int16_t cy, uint16_t col) {
  gfx->fillTriangle((int16_t)(cx + 1), (int16_t)(cy - 5),
                    (int16_t)(cx - 3), (int16_t)(cy + 0),
                    (int16_t)(cx + 1), (int16_t)(cy + 0), col);
  gfx->fillTriangle((int16_t)(cx - 1), (int16_t)(cy + 0),
                    (int16_t)(cx + 3), (int16_t)(cy + 0),
                    (int16_t)(cx - 1), (int16_t)(cy + 5), col);
}

// Power source from the AXP2101, tucked into the bottom-left the way the link
// glyph owns the bottom-right. USB on VBUS draws a plug (with percent beside
// it when a cell is fitted); battery-only draws the cell with fill; charging
// keeps the cell and adds a bolt. Percent always sits to the right of the
// glyph so the corner still reads at desk distance.
// (leftX, bottomY) is the bottom-left corner of the reserved band.
static void drawPowerGlyph(int16_t leftX, int16_t bottomY) {
  if (!pmuOk) return;

  const bool vbus = PMU.isVbusIn();
  const bool batt = PMU.isBatteryConnect();
  const bool charging = batt && PMU.isCharging();
  const int pct = batt ? PMU.getBatteryPercent() : -1;
  // Same yellow the link glyph uses when the reading is suspect — low fuel is
  // the one power state that wants to shout from across the desk.
  const bool low = batt && pct >= 0 && pct <= 20;
  const uint16_t col = low ? COL_CRT_YELLOW : COL_DIM;
  const int16_t gx = leftX;
  const int16_t gy = (int16_t)(bottomY - POWER_GLYPH_H);

  // Source first: plug while VBUS is up and we aren't actively charging the
  // cell (charging wants the battery+bolt so you can see it's taking a charge).
  // Battery-only falls through to the cell.
  if (vbus && !charging) {
    const int16_t bx = (int16_t)(gx + 8);
    const int16_t by = (int16_t)(gy + 2);
    gfx->fillRect((int16_t)(gx + 1), (int16_t)(by + 1), 7, 2, col);
    gfx->fillRect((int16_t)(gx + 1), (int16_t)(by + 6), 7, 2, col);
    gfx->fillRoundRect(bx, by, 12, 10, 2, col);
    if (pct >= 0) {
      char label[8];
      snprintf(label, sizeof label, "%d%%", pct > 100 ? 100 : pct);
      drawTextAt(label, (int16_t)(gx + POWER_GLYPH_W + 4), gy, 2, col);
    }
    return;
  }

  if (!batt) {
    // No VBUS reading and no cell — still put something in the corner so the
    // layout matches the link glyph's presence. Plug is the least-wrong mark.
    const int16_t bx = (int16_t)(gx + 8);
    const int16_t by = (int16_t)(gy + 2);
    gfx->fillRect((int16_t)(gx + 1), (int16_t)(by + 1), 7, 2, col);
    gfx->fillRect((int16_t)(gx + 1), (int16_t)(by + 6), 7, 2, col);
    gfx->fillRoundRect(bx, by, 12, 10, 2, col);
    return;
  }

  // Battery body + positive terminal nub. Fill grows left→right inside a
  // 1px inset so the outline stays readable at empty and full.
  const int16_t bw = 16;
  const int16_t bh = 10;
  const int16_t bx = gx;
  const int16_t by = (int16_t)(gy + 2);
  gfx->drawRoundRect(bx, by, bw, bh, 2, col);
  gfx->fillRoundRect((int16_t)(bx + bw), (int16_t)(by + 3), 2, 4, 1, col);

  if (pct > 0) {
    const int16_t innerW = (int16_t)(bw - 4);
    int16_t fillW = (int16_t)((innerW * pct + 50) / 100);
    if (fillW < 1) fillW = 1;
    if (fillW > innerW) fillW = innerW;
    gfx->fillRoundRect((int16_t)(bx + 2), (int16_t)(by + 2),
                       fillW, (int16_t)(bh - 4), 1, col);
  }

  if (charging) {
    // Knock a clear window out of the fill, then drop the bolt on top so it
    // reads against both empty and full cells.
    gfx->fillRect((int16_t)(bx + 5), (int16_t)(by + 1), 6, (int16_t)(bh - 2),
                  COL_BG);
    drawChargeBolt((int16_t)(bx + bw / 2), (int16_t)(by + bh / 2), col);
  }

  if (pct >= 0) {
    char label[8];
    snprintf(label, sizeof label, "%d%%", pct > 100 ? 100 : pct);
    drawTextAt(label, (int16_t)(gx + POWER_GLYPH_W + 4), gy, 2, col);
  }
}

// Hottest pool % + matching pace for a provider (-1 if unavailable).
// One pace layer: a pool's usage and where an even spend would have it.
struct PaceLayer { float pct; float pace; };

// Up to `max` layers, longer window first so the weekly band ends up the
// outermost ring. A pool the API doesn't report is simply absent — Codex has
// no session window on some plans — so a provider can legitimately draw one
// ring instead of two.
// The host already dropped non-ring pools and ordered the rest (Weekly then
// Session, Total then API), so a layer is just the next row.
static uint8_t providerLayers(const ProviderQuota &q, PaceLayer *out,
                              uint8_t max) {
  uint8_t n = 0;
  if (!q.ok) return 0;
  for (uint8_t i = 0; i < q.n && n < max; i++) {
    out[n++] = {q.pools[i].pct, q.pools[i].pace};
  }
  return n;
}

// Blend a colour toward the background. RGB565 has to be unpacked to do it.
static uint16_t dimToward(uint16_t color, uint16_t bg, float factor) {
  const int cr = (color >> 11) & 0x1F;
  const int cg = (color >> 5) & 0x3F;
  const int cb = color & 0x1F;
  const int br = (bg >> 11) & 0x1F;
  const int bgc = (bg >> 5) & 0x3F;
  const int bb = bg & 0x1F;
  const int r = br + (int)((cr - br) * factor + 0.5f);
  const int g = bgc + (int)((cg - bgc) * factor + 0.5f);
  const int b = bb + (int)((cb - bb) * factor + 0.5f);
  return (uint16_t)((r << 11) | (g << 5) | b);
}

// Full-screen reset celebration. It is intentionally a small, deterministic
// particle field rather than a stored bitmap: every piece is a shade of the
// provider accent, the frame fits in the existing canvas, and the same show is
// available for both observed resets and the remote test command.
static const uint16_t CELEBRATION_SEEDS[32] = {
    0x13A7, 0x2C41, 0x38D2, 0x4F19, 0x52B8, 0x67E3, 0x71A0, 0x8C26,
    0x95D4, 0xA13B, 0xB84E, 0xC207, 0xD96A, 0xE31C, 0xF047, 0x0B92,
    0x1DD8, 0x2A6F, 0x3E15, 0x46C9, 0x58B2, 0x63F1, 0x76AC, 0x89D3,
    0x9A28, 0xA74D, 0xB36E, 0xC8F0, 0xD41B, 0xE6A5, 0xF92C, 0x05E7,
};
static const uint8_t CELEBRATION_FRAMES = 35;
static const uint32_t CELEBRATION_FRAME_MS = 70;

static uint16_t celebrationShade(uint16_t accent, uint8_t index) {
  switch (index % 5) {
    case 0: return accent;
    case 1: return dimToward(accent, COL_BG, 0.42f);
    case 2: return dimToward(accent, COL_BG, 0.70f);
    case 3: return dimToward(accent, COL_WHITE, 0.40f);
    default: return dimToward(accent, COL_WHITE, 0.82f);
  }
}

static void playResetCelebration(uint16_t accent) {
  const float totalS = (CELEBRATION_FRAMES - 1) *
                       (CELEBRATION_FRAME_MS / 1000.0f);
  const int16_t cx = scrW() / 2;
  const int16_t cy = scrH() / 2;
  for (uint8_t frame = 0; frame < CELEBRATION_FRAMES; frame++) {
    const uint32_t started = millis();
    const float seconds = frame * (CELEBRATION_FRAME_MS / 1000.0f);
    const float progress = seconds / totalS;
    gfx->clear(COL_BG);

    // A restrained radial pulse gives the first burst a sense of lift without
    // turning the selected accent into a new alarm colour.
    const int16_t radius = (int16_t)(18 + progress * 170.0f);
    gfx->drawCircle(cx, cy, radius,
                    dimToward(accent, COL_BG, 0.16f * (1.0f - progress)));
    if (progress < 0.35f) {
      gfx->fillCircle(cx, cy, (int16_t)(10 + (1.0f - progress) * 16),
                      dimToward(accent, COL_BG, 0.10f));
    }

    for (uint8_t i = 0; i < 32; i++) {
      const uint16_t seed = CELEBRATION_SEEDS[i];
      const float originX = (float)((int)(seed & 0x7F) - 64);
      const float originY = (float)((int)((seed >> 7) & 0x1F) - 16);
      const float vx = (float)((int)((seed >> 2) & 0x1F) - 16) * 7.0f;
      const float vy = -42.0f - (float)((seed >> 10) & 0x1F) * 2.3f;
      const float x = cx + originX + vx * seconds;
      const float y = cy + originY + vy * seconds + 57.0f * seconds * seconds;
      const int16_t px = (int16_t)lroundf(x);
      const int16_t py = (int16_t)lroundf(y);
      const int16_t size = 3 + (seed & 0x03);
      const uint16_t color = celebrationShade(accent, i);
      if (((frame + seed) & 0x03) == 0) {
        gfx->drawFastVLine(px, py, (int16_t)(size + 4), color);
      } else if (((frame + seed) & 0x01) == 0) {
        gfx->fillRect(px, py, (int16_t)(size + 2), size, color);
      } else {
        gfx->fillRect(px, py, size, (int16_t)(size + 2), color);
      }
    }
    gfx->flush();
    const uint32_t spent = millis() - started;
    if (spent < CELEBRATION_FRAME_MS) delay(CELEBRATION_FRAME_MS - spent);
  }
}

// Fixed-size "now" dot, riding inside the band. Sizing it off the band rather
// than the radius keeps inner and outer quota rings matched.
static void drawPaceDot(int16_t cx, int16_t cy, int16_t r, int16_t thick,
                        float angle, uint16_t color) {
  const float radians = angle * DEG_TO_RAD;
  const float mid = (float)r - thick / 2.0f;
  const int16_t dot = (int16_t)lroundf(thick * 5.0f / 14.0f);
  gfx->fillCircle((int16_t)lroundf(cx + cosf(radians) * mid),
                  (int16_t)lroundf(cy + sinf(radians) * mid), dot, color);
}

// Usage arc with half-round ends, mirroring the round line cap on the Swift
// surfaces. fillArc only cuts square ends, so the arc is pulled in by the cap
// radius and a disc is dropped on each end: the painted sweep still matches the
// real one, which is what keeps the distance to the pace tick honest.
static void fillRoundArc(int16_t cx, int16_t cy, int16_t r, int16_t thick,
                         float startDeg, float sweepDeg, uint16_t color) {
  const int16_t inner = (int16_t)(r - thick);
  const float mid = (float)r - thick / 2.0f;
  const int16_t cap = (int16_t)(thick / 2);
  const float capDeg = (cap / mid) * RAD_TO_DEG;
  float s = startDeg + capDeg;
  float e = startDeg + sweepDeg - capDeg;
  if (e > s) {
    gfx->fillArc(cx, cy, r, inner, s, e, color);
  } else {
    // Shorter than its own two caps: one disc is the whole arc.
    s = e = startDeg + sweepDeg / 2.0f;
  }
  for (uint8_t i = 0; i < 2; i++) {
    const float a = (i == 0 ? s : e) * DEG_TO_RAD;
    gfx->fillCircle((int16_t)lroundf(cx + cosf(a) * mid),
                    (int16_t)lroundf(cy + sinf(a) * mid), cap, color);
  }
}

// One ring band: track + filled arc from 12 o'clock + pace dot.
// The gap between where the arc stops and where the dot sits is the deficit.
static void drawPaceRing(int16_t cx, int16_t cy, int16_t r, int16_t thick,
                         float pct, float pacePct, uint16_t accent) {
  const int16_t inner = (int16_t)(r - thick);
  // A neutral track is indistinguishable from background at this size, so two
  // near-empty rings merge into one dark blob. Tinting keeps each ring legible
  // as a ring before any of it fills.
  // Shared Headroom ring contract: 20% tinted track, round-ended usage arc, and
  // a high-contrast pace dot. Swift surfaces mirror these semantics.
  gfx->fillArc(cx, cy, r, inner, 0, 360, dimToward(accent, COL_BG, 0.20f));
  if (pct >= 0) {
    float p = pct > 100 ? 100 : pct;
    float sweep = p * 3.6f;
    if (p > 0 && sweep < 2.0f) sweep = 2.0f;
    // Always round-cap, including at 100%: the two caps meet at 12 o'clock
    // and leave the same ")(" seam SwiftUI's StrokeStyle(.round) does. A solid
    // 360° fill would erase it.
    fillRoundArc(cx, cy, r, thick, -90.0f, sweep, accent);
  }
  if (pacePct >= 0) {
    float pp = pacePct > 100 ? 100 : pacePct;
    float a = -90.0f + pp * 3.6f;
    drawPaceDot(cx, cy, r, thick, a, COL_WHITE);
  }
}

// Concentric pace layers for one provider, plus the label underneath.
static void drawQuotaRing(int16_t cx, int16_t cy, int16_t r,
                          const ProviderQuota &q, uint16_t accent,
                          const char *label) {
  const int16_t thick = 6;
  const int16_t gap = 4;   // below ~4 the two bands read as one thick border
  PaceLayer layers[2];
  uint8_t n = providerLayers(q, layers, 2);
  if (n == 0) {
    drawPaceRing(cx, cy, r, thick, -1, -1, accent);
  } else {
    int16_t rr = r;
    for (uint8_t i = 0; i < n; i++) {
      // Equal thickness: a thinner inner band reads as subordinate, when the
      // two pools are just different time horizons of the same thing.
      drawPaceRing(cx, cy, rr, thick, layers[i].pct, layers[i].pace, accent);
      rr = (int16_t)(rr - thick - gap);
    }
  }
  gfx->setTextSize(2);
  int16_t x1, y1; uint16_t tw, th;
  gfx->getTextBounds(label, 0, 0, &x1, &y1, &tw, &th);
  gfx->setTextColor(accent);
  gfx->setCursor(cx - (int16_t)tw / 2, cy + r + 8);
  gfx->print(label);
}

// ---- Pace glyph: the same reading the macOS menu-bar icon's Pace style has ----
// Rings answer "how much is gone"; this answers "am I ahead or behind", and
// nothing else. One rail across the group is even spend, and each provider's
// mark rides above it when it is over pace, below when it is under.
//
// Softness of the curve. A gap of this many points lands near halfway to the
// edge (tanh(1) ≈ 0.76), so small gaps stay readable and large ones asymptote
// instead of clipping. Mirrors MenuBarIconStyle.paceScale in
// Shared/MenuBarIconStyle.swift — change one, change the other.
static const float PACE_SCALE = 8.0f;
// Proportions carried over from the 18pt menu-bar glyph: a 1:5 pill, a dot
// two-thirds of its width, and a rail thin enough to read as a line.
static const int16_t PACE_COL_W = 14;
static const int16_t PACE_COL_H = 68;
static const int16_t PACE_DOT_R = 5;
static const int16_t PACE_PAD = 7;
// The even-spend line, drawn across one pill and no further.
static const int16_t PACE_RAIL_H = 3;

// The pool this reads is layer 0 — the longer window, which is the same pool
// the menu bar takes. Absent when the provider is down or reports no pace.
static bool paceLayerFor(const ProviderQuota &q, PaceLayer *out) {
  PaceLayer layers[2];
  const uint8_t n = providerLayers(q, layers, 2);
  if (n == 0 || layers[0].pct < 0 || layers[0].pace < 0) return false;
  *out = layers[0];
  return true;
}

// Pill track, even-spend line, label. Same 20% accent tint the ring bands use
// for their track, dropped further when there is nothing to place on it.
// The line stops at the pill: it belongs to that provider, and carried across
// the gaps it read as one shared scale the three slots do not have.
static void drawPaceTrack(int16_t cx, int16_t cy, const ProviderQuota &q,
                          uint16_t accent, const char *label) {
  PaceLayer layer;
  const bool have = paceLayerFor(q, &layer);
  const int16_t x = (int16_t)(cx - PACE_COL_W / 2);
  const int16_t y = (int16_t)(cy - PACE_COL_H / 2);
  const int16_t radius = PACE_COL_W / 2;
  gfx->fillRoundRect(x, y, PACE_COL_W, PACE_COL_H, radius,
                     dimToward(accent, COL_BG, have ? 0.20f : 0.10f));
  gfx->drawRoundRect(x, y, PACE_COL_W, PACE_COL_H, radius,
                     dimToward(accent, COL_BG, have ? 0.45f : 0.22f));
  if (have) {
    gfx->fillRect(x, (int16_t)(cy - PACE_RAIL_H / 2), PACE_COL_W, PACE_RAIL_H,
                  COL_DIM);
  }
  gfx->setTextSize(2);
  int16_t x1, y1; uint16_t tw, th;
  gfx->getTextBounds(label, 0, 0, &x1, &y1, &tw, &th);
  gfx->setTextColor(accent);
  // Same baseline the ring labels sit on, so the lower pane never moves.
  gfx->setCursor(cx - (int16_t)tw / 2, cy + PACE_COL_H / 2 + 6);
  gfx->print(label);
}

// The mark. It carries the whole reading here, so it takes the accent — on
// the rings the accent is the arc and the dot stays white.
static void drawPaceMark(int16_t cx, int16_t cy, const ProviderQuota &q,
                         uint16_t accent) {
  PaceLayer layer;
  if (!paceLayerFor(q, &layer)) return;
  const float t = tanhf((layer.pct - layer.pace) / PACE_SCALE);
  const int16_t travel = (int16_t)(PACE_COL_H / 2 - PACE_PAD - PACE_DOT_R);
  // Screen y grows downward, and over pace reads high, same as the Mac.
  const int16_t dy = (int16_t)lroundf(-t * (float)travel);
  gfx->fillCircle(cx, (int16_t)(cy + dy), PACE_DOT_R, accent);
}

// Every track first, then every mark — the paint order in
// MeterIconRenderer.drawPaceGlyph, so no line can land on a mark.
static void drawPaceGlyph(int16_t padX, int16_t span, int16_t cy) {
  if (slotN == 0) return;
  const int16_t slot = (int16_t)(span / slotN);
  for (uint8_t i = 0; i < slotN; i++) {
    const int16_t cx = (int16_t)(padX + (int16_t)i * slot + slot / 2);
    drawPaceTrack(cx, cy, slots[i].q, slots[i].accent,
                  slots[i].title.c_str());
  }
  for (uint8_t i = 0; i < slotN; i++) {
    drawPaceMark((int16_t)(padX + (int16_t)i * slot + slot / 2), cy,
                 slots[i].q, slots[i].accent);
  }
}

// Host `updated` ends in ±HHMM — needed before drawBurndown for local day rules.
static int32_t updatedTzOffsetS();

static bool burnHistoryReady() {
  for (uint8_t i = 0; i < slotN; i++) {
    const Burndown &b = slots[i].burn;
    if (b.ok && (b.n > 0 || b.histN > 0)) return true;
  }
  return false;
}

// Eight-dot orbit while burndown pts are empty. Unit octagon — no trig/frame.
static const int8_t SPIN_DX[8] = {7, 5, 0, -5, -7, -5, 0, 5};
static const int8_t SPIN_DY[8] = {0, 5, 7, 5, 0, -5, -7, -5};
static bool collectSpinActive = false;
static int16_t collectSpinCx = 0, collectSpinCy = 0;
static uint8_t collectSpinFrame = 255;

static void paintHistorySpinner(int16_t cx, int16_t cy, uint8_t frame) {
  const int16_t box = 18;
  gfx->fillRect((int16_t)(cx - box / 2), (int16_t)(cy - box / 2), box, box,
                COL_BG);
  for (uint8_t i = 0; i < 8; i++) {
    const uint8_t dist = (uint8_t)((i + 8 - (frame & 7)) & 7);
    if (dist > 3) continue;
    const uint16_t col = dist == 0 ? COL_WHITE
                       : dist == 1 ? COL_DIM
                       : dimToward(COL_DIM, COL_BG, 0.45f);
    gfx->fillCircle((int16_t)(cx + SPIN_DX[i]), (int16_t)(cy + SPIN_DY[i]),
                    1, col);
  }
}

// "Collecting history" + spinner. Narrow charts get a short label (the full
// phrase is ~216px at size 2 and will not fit a home column).
static void drawCollectingHistory(int16_t x, int16_t y, int16_t w) {
  if (w >= 232) {
    drawTextAt(LABEL_COLLECTING_HISTORY, x, y, 2, COL_DIM);
    collectSpinCx = (int16_t)(x + textWidth(LABEL_COLLECTING_HISTORY, 2) + 16);
  } else {
    drawTextAt(LABEL_NO_DATA, x, y, 2, COL_DIM);
    collectSpinCx = (int16_t)(x + textWidth(LABEL_NO_DATA, 2) + 14);
  }
  collectSpinCy = (int16_t)(y + 6);
  collectSpinActive = true;
  collectSpinFrame = (uint8_t)((millis() / 100) & 7);
  paintHistorySpinner(collectSpinCx, collectSpinCy, collectSpinFrame);
}

static void tickCollectingSpinner() {
  if (!collectSpinActive || burnHistoryReady()) {
    collectSpinActive = false;
    return;
  }
  const uint8_t frame = (uint8_t)((millis() / 100) & 7);
  if (frame == collectSpinFrame) return;
  collectSpinFrame = frame;
  paintHistorySpinner(collectSpinCx, collectSpinCy, frame);
  const int16_t box = 18;
  gfx->flushLogicalRect((int16_t)(collectSpinCx - box / 2),
                        (int16_t)(collectSpinCy - box / 2), box, box);
}

// Projection dashes along path length — not X. Stepping by X makes steep
// forecasts (Codex running out soon) look sparse and shallow ones look solid.
static const int16_t PROJ_DASH_ON = 3;
static const int16_t PROJ_DASH_STRIDE = 12;  // 3 on, 9 off

static void strokeDashedProj(int16_t x0, int16_t y0, int16_t x1, int16_t y1,
                             uint16_t col, int16_t clipY, int16_t clipH) {
  const int32_t dx = (int32_t)x1 - x0;
  const int32_t dy = (int32_t)y1 - y0;
  const int32_t adx = dx < 0 ? -dx : dx;
  const int32_t ady = dy < 0 ? -dy : dy;
  // Octagon length approx — close enough to phase dashes; avoids float sqrt.
  const int32_t len = adx > ady ? adx + ady / 2 : ady + adx / 2;
  if (len < 1) return;
  for (int32_t i = 0; i < len; i += PROJ_DASH_STRIDE) {
    const int32_t i1 = (i + PROJ_DASH_ON > len) ? len : i + PROJ_DASH_ON;
    const int16_t ax = (int16_t)(x0 + dx * i / len);
    const int16_t ay = (int16_t)(y0 + dy * i / len);
    const int16_t bx = (int16_t)(x0 + dx * i1 / len);
    const int16_t by = (int16_t)(y0 + dy * i1 / len);
    for (int16_t d = -1; d <= 1; d++) {
      const int16_t a = (int16_t)(ay + d), c = (int16_t)(by + d);
      if (a < clipY || a > clipY + clipH - 1 ||
          c < clipY || c > clipY + clipH - 1) continue;
      gfx->drawLine(ax, a, bx, c, col);
    }
  }
}

// Burndown chart: dotted budget line falling from full at the window's start
// to zero at its reset, the actual remaining-% curve over it, and a lightly
// dashed accent tail for where the current pace lands. Below the budget line
// means burning faster than the window can afford.
//
// X-axis matches Mac/iOS BurndownChartAxis: at most seven weekday-named
// columns (never day-of-month numbers); monthly windows clip to seven days
// covering "now"; session windows get hour ticks instead of a blank axis.
// Defined below with the overall-chart helpers; the provider page needs it
// first, to clip its spent-window curve to the plot.
static bool clipBurnSeg(uint32_t ta, float ra, uint32_t tb, float rb,
                        uint32_t tLo, uint32_t tHi,
                        uint32_t *oa, float *ora, uint32_t *ob, float *orb);

static void drawBurndown(const Burndown &b, int16_t x, int16_t y,
                         int16_t w, int16_t h, uint16_t accent) {
  if (!b.ok || b.t1 <= b.t0) {
    const uint16_t track = dimToward(accent, COL_BG, 0.45f);
    gfx->drawRect(x, y, w, h, track);
    drawCollectingHistory(x + 8, (int16_t)(y + h / 2 - 8), w);
    return;
  }
  collectSpinActive = false;

  const uint32_t win0 = b.t0;
  const uint32_t win1 = b.t1;
  const uint32_t winSpan = win1 - win0;
  // "Now" ≈ last actual sample.
  uint32_t nowT = win0;
  if (b.n > 0) nowT = b.t[b.n - 1];

  // Plot domain: full window if ≤7d+slack, else 7 days covering now.
  const uint32_t weekS = 7u * 86400u;
  uint32_t plot0 = win0;
  uint32_t plot1 = win1;
  if (winSpan > weekS + 3600u) {
    plot0 = (nowT > 3u * 86400u) ? (nowT - 3u * 86400u) : 0;
    plot1 = plot0 + weekS;
    if (plot0 < win0) {
      plot0 = win0;
      plot1 = plot0 + weekS;
    }
    if (plot1 > win1) {
      plot1 = win1;
      plot0 = (plot1 > weekS) ? (plot1 - weekS) : win0;
      if (plot0 < win0) plot0 = win0;
    }
  }
  // Reach back past the window's own start toward the spent run, by at most
  // HISTORY_REACH_PCT of the window. Without this a window that just rolled
  // draws one point against a full-width budget diagonal, which is the detail
  // page's version of losing the week.
  //
  // Only where the plot already covers the whole window. The branch above is a
  // moving 7-day slice clamped inside a monthly window; pulling its left edge
  // outside would put the slice where the budget line does not run.
  if (winSpan <= weekS + 3600u && b.histN > 0) {
    const uint32_t reach =
        (uint32_t)((uint64_t)winSpan * HISTORY_REACH_PCT / 100u);
    const uint32_t limit = (win0 > reach) ? (win0 - reach) : 0;
    uint32_t oldest = b.histT[0] > limit ? b.histT[0] : limit;
    if (oldest < plot0) plot0 = oldest;
  }
  if (plot1 <= plot0) plot1 = plot0 + 3600u;
  const uint32_t plotSpan = plot1 - plot0;
  const bool showDays = plotSpan >= 2u * 86400u;
  const bool showHours = !showDays;
  const int16_t axisH = (showDays || showHours) && h >= 48 ? 12 : 0;
  const int16_t plotH = (int16_t)(h - axisH);

  const uint16_t track = dimToward(accent, COL_BG, 0.45f);
  gfx->drawRect(x, y, w, plotH, track);

  auto px = [&](uint32_t t) -> int16_t {
    if (t <= plot0) return x;
    if (t >= plot1) return (int16_t)(x + w - 1);
    return (int16_t)(x + (int32_t)((uint64_t)(t - plot0) * (w - 1) / plotSpan));
  };
  auto py = [&](float remaining) -> int16_t {
    float r = remaining < 0 ? 0 : (remaining > 100 ? 100 : remaining);
    return (int16_t)(y + plotH - 1 - (int16_t)(r * (plotH - 1) / 100.0f));
  };
  // Budget % from the full pool window (not the clipped plot).
  auto budgetR = [&](uint32_t t) -> float {
    if (t <= win0) return 100.0f;
    if (t >= win1) return 0.0f;
    return 100.0f * (1.0f - (float)(t - win0) / (float)winSpan);
  };

  auto stroke = [&](int16_t ax, int16_t ay, int16_t bx, int16_t by,
                    uint16_t col) {
    for (int16_t d = -1; d <= 1; d++) {
      const int16_t a = (int16_t)(ay + d), c = (int16_t)(by + d);
      if (a < y || a > y + plotH - 1 || c < y || c > y + plotH - 1) continue;
      gfx->drawLine(ax, a, bx, c, col);
    }
  };

  // Axis: ≤7 weekday names, or hour ticks for sessions.
  if (axisH > 0) {
    const int32_t tz = updatedTzOffsetS();
    const uint16_t grid = dimToward(COL_WHITE, COL_BG, 0.22f);
    const int16_t axisY = (int16_t)(y + plotH + 2);
    if (showDays) {
      static const char *const WD[] = {
          "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};
      const uint32_t localP0 = (uint32_t)((int64_t)plot0 + tz);
      const uint32_t localDay0 = localP0 - (localP0 % 86400u);
      uint8_t labeled = 0;
      for (uint16_t d = 0; d < 14 && labeled < 7; d++) {
        const uint32_t localMidnight = localDay0 + (uint32_t)d * 86400u;
        const uint32_t dayUtc = (uint32_t)((int64_t)localMidnight - tz);
        if (dayUtc >= plot1) break;
        if (dayUtc + 86400u <= plot0) continue;  // day fully before plot
        const int16_t dx = px(dayUtc > plot0 ? dayUtc : plot0);
        if (dayUtc > plot0) {
          gfx->drawFastVLine(dx, (int16_t)(y + 1), (int16_t)(plotH - 2), grid);
        }
        time_t tt = (time_t)localMidnight;
        struct tm parts;
        gmtime_r(&tt, &parts);
        drawTextAt(WD[parts.tm_wday], (int16_t)(dx + 2), axisY, 1, COL_DIM);
        labeled++;
      }
    } else {
      // Hour ticks — local clock via tz on the unix stamp.
      const uint32_t localP0 = (uint32_t)((int64_t)plot0 + tz);
      uint32_t localHour = localP0 - (localP0 % 3600u);
      if (localHour < localP0) localHour += 3600u;
      uint8_t labeled = 0;
      for (; labeled < 12; labeled++) {
        const uint32_t hourUtc = (uint32_t)((int64_t)localHour - tz);
        if (hourUtc >= plot1) break;
        const int16_t dx = px(hourUtc);
        if (hourUtc > plot0) {
          gfx->drawFastVLine(dx, (int16_t)(y + 1), (int16_t)(plotH - 2), grid);
        }
        char label[8];
        snprintf(label, sizeof label, "%02u:%02u",
                 (unsigned)((localHour / 3600u) % 24u), 0u);
        drawTextAt(label, (int16_t)(dx + 2), axisY, 1, COL_DIM);
        localHour += 3600u;
      }
    }
  }

  // Budget line across the visible plot (full-window %).
  {
    const uint16_t budget = dimToward(COL_WHITE, COL_BG, 0.55f);
    const uint32_t b0 = plot0 > win0 ? plot0 : win0;
    const uint32_t b1 = plot1 < win1 ? plot1 : win1;
    if (b1 > b0) {
      const int16_t x0 = px(b0), y0 = py(budgetR(b0));
      const int16_t x1 = px(b1), y1 = py(budgetR(b1));
      const int16_t steps = (int16_t)(x1 - x0);
      for (int16_t i = 0; i < steps; i += 6) {
        const int16_t i2 = (int16_t)((i + 3 < steps) ? i + 3 : steps);
        const int16_t ya =
            (int16_t)(y0 + (int32_t)(y1 - y0) * i / (steps ? steps : 1));
        const int16_t yb =
            (int16_t)(y0 + (int32_t)(y1 - y0) * i2 / (steps ? steps : 1));
        gfx->drawLine((int16_t)(x0 + i), ya, (int16_t)(x0 + i2), yb, budget);
        if (ya + 1 <= y + plotH - 1) {
          gfx->drawLine((int16_t)(x0 + i), (int16_t)(ya + 1),
                        (int16_t)(x0 + i2), (int16_t)(yb + 1), budget);
        }
      }
    }
  }

  // Cursor API under Total so the headline pool sits on top when they share
  // an accent. Dimmed so the two stay distinguishable. Exhaustion does not
  // gray the series out — same rule as the primary curve and the quota bars.
  if (b.n2 > 1) {
    const uint16_t line2 = dimToward(accent, COL_BG, 0.70f);
    for (uint8_t i = 1; i < b.n2; i++) {
      stroke(px(b.t2[i - 1]), py(b.remaining2[i - 1]),
             px(b.t2[i]), py(b.remaining2[i]), line2);
    }
    if (b.projN2 == 2) {
      const float dR2 = b.projR2[1] - b.projR2[0];
      if (dR2 < -0.5f || dR2 > 0.5f || b.warn2) {
        const int16_t x0 = px(b.projT2[0]), y0 = py(b.projR2[0]);
        const int16_t x1 = px(b.projT2[1]), y1 = py(b.projR2[1]);
        if (dR2 < -0.5f || dR2 > 0.5f) {
          strokeDashedProj(x0, y0, x1, y1, line2, y, plotH);
        }
        if (b.warn2) gfx->fillCircle(x1, y1, 3, line2);
      }
    }
    if (b.n2 > 0) {
      gfx->fillCircle(px(b.t2[b.n2 - 1]), py(b.remaining2[b.n2 - 1]), 2, line2);
    }
  }

  // Actual curve (primary / Total). Keep the provider accent even when the
  // pool is spent — exhaustion is already in the curve hitting zero, the warn
  // dot, and the caption. Graying the line out read as a dead series; painting
  // it red would spend the loudest colour on the thing least able to be precise
  // about it (settled for Mac in drained(), for the board as keep-accent).
  const uint16_t line = accent;

  // Spent windows in the stub of plot before this one, and a dotted rule where
  // each grant landed. Drawn under the live curve, one pixel rather than three:
  // this budget is gone, so it must never be mistaken for the reading. A
  // segment straddling a grant is skipped — joining it would climb across the
  // reset between two budgets that never coexisted.
  if (b.histN > 1) {
    const uint16_t ghost = dimToward(accent, COL_BG, 0.70f);
    for (uint8_t i = 1; i < b.histN; i++) {
      // A pair straddling a grant is the recharge. Square it off on the grant
      // instant — level up to it, vertical through it — rather than letting
      // the raw diagonal imply the pool refilled over the sample gap. The
      // riser lands on the same x as the dotted rule below, so they agree.
      uint32_t grantAt = 0;
      for (uint8_t g = 0; g < b.grantN; g++) {
        if (b.grantT[g] > b.histT[i - 1] && b.grantT[g] <= b.histT[i]) {
          grantAt = b.grantT[g];
        }
      }
      if (grantAt != 0) {
        uint32_t la, lb; float lra, lrb;
        if (clipBurnSeg(b.histT[i - 1], b.histR[i - 1], grantAt, b.histR[i - 1],
                        plot0, plot1, &la, &lra, &lb, &lrb)) {
          gfx->drawLine(px(la), py(lra), px(lb), py(lrb), ghost);
        }
        if (grantAt > plot0 && grantAt < plot1) {
          gfx->drawLine(px(grantAt), py(b.histR[i - 1]),
                        px(grantAt), py(b.histR[i]), ghost);
        }
        if (clipBurnSeg(grantAt, b.histR[i], b.histT[i], b.histR[i],
                        plot0, plot1, &la, &lra, &lb, &lrb)) {
          gfx->drawLine(px(la), py(lra), px(lb), py(lrb), ghost);
        }
        continue;
      }
      uint32_t ta, tb; float ra, rb;
      if (!clipBurnSeg(b.histT[i - 1], b.histR[i - 1], b.histT[i], b.histR[i],
                       plot0, plot1, &ta, &ra, &tb, &rb)) {
        continue;
      }
      gfx->drawLine(px(ta), py(ra), px(tb), py(rb), ghost);
    }
    for (uint8_t g = 0; g < b.grantN; g++) {
      const uint32_t at = b.grantT[g];
      if (at <= plot0 || at >= plot1) continue;
      const int16_t gx = px(at);
      for (int16_t yy = y; yy < (int16_t)(y + plotH); yy += 4) {
        gfx->drawFastVLine(gx, yy, 2, ghost);
      }
    }
  }

  for (uint8_t i = 1; i < b.n; i++) {
    stroke(px(b.t[i - 1]), py(b.remaining[i - 1]),
           px(b.t[i]), py(b.remaining[i]), line);
  }

  // Projection: path-length dashes (3 on, 9 off) so steep and shallow
  // forecasts match. Skip a level forecast — measured-zero pace would paint a
  // bar across the whole window and erase the budget diagonal.
  if (b.projN == 2) {
    const float dR = b.projR[1] - b.projR[0];
    if (dR < -0.5f || dR > 0.5f || b.warn) {
      const int16_t x0 = px(b.projT[0]), y0 = py(b.projR[0]);
      const int16_t x1 = px(b.projT[1]), y1 = py(b.projR[1]);
      if (dR < -0.5f || dR > 0.5f) {
        strokeDashedProj(x0, y0, x1, y1, line, y, plotH);
      }
      if (b.warn) gfx->fillCircle(x1, y1, 4, line);
    }
  }

  // Now marker (primary).
  if (b.n > 0) {
    const int16_t nx = px(b.t[b.n - 1]);
    const int16_t ny = py(b.remaining[b.n - 1]);
    gfx->fillCircle(nx, ny, 3, COL_WHITE);
    gfx->drawCircle(nx, ny, 4, COL_BG);   // keeps it off the curve it sits on
  }
}

// Headroom tap targets (logical coords) → detail pages, plus the header chip
// and lower pane that flip between the home modes.
enum HitKind : uint8_t { HIT_PAGE = 0, HIT_MODE = 1 };
struct GlanceHit {
  int16_t x, y, w, h;
  HitKind kind;
  Page target;
};
static const uint8_t MAX_GLANCE_HITS = 11;  // 2 mode + 3 rings + 3 chart + 3 legend
static GlanceHit glanceHits[MAX_GLANCE_HITS];
static uint8_t glanceHitN = 0;

static void glanceClearHits() { glanceHitN = 0; }

static void glanceAddHit(int16_t x, int16_t y, int16_t w, int16_t h, Page target) {
  if (glanceHitN >= MAX_GLANCE_HITS) return;
  glanceHits[glanceHitN++] = {x, y, w, h, HIT_PAGE, target};
}

static void glanceAddModeHit(int16_t x, int16_t y, int16_t w, int16_t h) {
  if (glanceHitN >= MAX_GLANCE_HITS) return;
  glanceHits[glanceHitN++] = {x, y, w, h, HIT_MODE, PAGE_GLANCE};
}

static void nativeToLogical(int16_t nx, int16_t ny, int16_t *lx, int16_t *ly) {
#if defined(BOARD_WS_AMOLED_175_ROUND)
  // CST9217 packets are native panel coordinates. The controller on this
  // exact no-C board is mounted with both axes reversed.
  const int16_t x = (int16_t)constrain(nx, 0, LOG_W - 1);
  const int16_t y = (int16_t)constrain(ny, 0, LOG_H - 1);
  *lx = (int16_t)(LOG_W - 1 - x);
  *ly = (int16_t)(LOG_H - 1 - y);
#elif defined(BOARD_TOUCH_CST92XX)
  // Direct-panel touch is remapped in touchInit() to canvas coordinates.
  *lx = nx;
  *ly = ny;
#else
  *lx = (int16_t)((LOG_W - 1) - ny);
  *ly = nx;
#endif
}

static bool glanceHitAt(int16_t nx, int16_t ny, GlanceHit *out) {
  if (page != PAGE_GLANCE || glanceHitN == 0) return false;
  int16_t lx, ly;
  nativeToLogical(nx, ny, &lx, &ly);
  for (uint8_t i = 0; i < glanceHitN; i++) {
    const GlanceHit &h = glanceHits[i];
    if (lx >= h.x && lx < h.x + h.w && ly >= h.y && ly < h.y + h.h) {
      *out = h;
      return true;
    }
  }
  return false;
}

static const Burndown &burnFor(Page p);

static uint16_t activityCellColor(uint8_t level) {
  switch (level > 4 ? 4 : level) {
    case 0: return COL_BLACK;
    case 1: return dimToward(COL_GREEN, COL_BLACK, 0.28f);
    case 2: return dimToward(COL_GREEN, COL_BLACK, 0.48f);
    case 3: return dimToward(COL_GREEN, COL_BLACK, 0.72f);
    default: return COL_GREEN;
  }
}

// Lower half, history reading: a mixed-source coding cadence heatmap. The
// host has already combined Claude session evidence and quota-source burns;
// the board only paints the resulting levels and keeps its own memory small.
static void drawGlanceHistory(int16_t padX, int16_t span, int16_t midY,
                              int16_t lowBottom) {
  drawTextAt("History", padX, (int16_t)(midY + 8), 2, COL_WHITE);
  if (!activityHistory.ok || activityHistory.n == 0) {
    drawTextAt("Collecting activity", padX, (int16_t)(midY + 38), 2, COL_DIM);
    return;
  }

  char summary[28];
  snprintf(summary, sizeof summary, "%u active · %ud streak",
           (unsigned)activityHistory.activeDays,
           (unsigned)activityHistory.currentStreak);
#if defined(BOARD_ROUND_UI)
  drawRightAt(summary, (int16_t)(padX + span), (int16_t)(midY + 11), 1,
              COL_DIM);
#else
  drawRightAt(summary, (int16_t)(padX + span), (int16_t)(midY + 8), 2, COL_DIM);
#endif

#if defined(BOARD_ROUND_UI)
  // A focused twelve-week window reads better on the circular face than the
  // landscape view's dense 162-day strip. At 20px these become real glanceable
  // activity marks and fill the lower chord without touching its edge.
  const int16_t cell = 20;
  const int16_t gap = 3;
  const uint8_t visibleDays = 84;
#else
  const int16_t cell = 14;
  const int16_t gap = 2;
  const uint8_t visibleDays = MAX_ACTIVITY_DAYS;
#endif
  // Treat the board strip as a fixed recent window, even when an older host
  // sends fewer days. This keeps today at the right edge instead of centering
  // a short history in the middle of the display; empty leading cells remain
  // true black and therefore disappear naturally.
  const uint8_t historyOffset = activityHistory.n > visibleDays
                                  ? (uint8_t)(activityHistory.n - visibleDays)
                                  : 0;
  const uint8_t shownDays =
      (uint8_t)(activityHistory.n - historyOffset);
  const uint8_t leadingDays = (uint8_t)(visibleDays - shownDays);
  const uint8_t startWeekday = (uint8_t)(
      (activityHistory.startWeekday + historyOffset) % 7);
  const uint8_t gridStartWeekday = (uint8_t)(
      (startWeekday + 7 - (leadingDays % 7)) % 7);
  const uint8_t cols = (uint8_t)((gridStartWeekday + visibleDays + 6) / 7);
  const int16_t gridW = (int16_t)(cols * cell + (cols - 1) * gap);
  const int16_t gridY = (int16_t)(midY + 30);
  int16_t gridX = (int16_t)(padX + span - gridW);
#if defined(BOARD_ROUND_UI)
  int16_t gridBandX = 0, gridBandW = 0;
  const int16_t gridBottom =
      (int16_t)(gridY + 7 * cell + 6 * gap - 1);
  roundBand(gridY, gridBottom, 8, &gridBandX, &gridBandW);
  gridX = (int16_t)(gridBandX + (gridBandW - gridW) / 2);
#endif
  for (uint8_t i = 0; i < shownDays; i++) {
    const uint16_t slot = (uint16_t)gridStartWeekday + leadingDays + i;
    const uint8_t col = (uint8_t)(slot / 7);
    const uint8_t row = (uint8_t)(slot % 7);
    const int16_t x = (int16_t)(gridX + col * (cell + gap));
    const int16_t y = (int16_t)(gridY + row * (cell + gap));
    gfx->fillRoundRect(x, y, cell, cell, 3,
                       activityCellColor(
                           activityHistory.levels[historyOffset + i]));
  }

  // A thin baseline keeps an entirely empty row legible without stealing a
  // second legend line from the glance pane.
#if !defined(BOARD_ROUND_UI)
  gfx->drawFastHLine(padX, lowBottom, span,
                     dimToward(COL_DIM, COL_BG, 0.35f));
#endif
}

static String formatUsd(float value) {
  if (value < 0) value = 0;
  const uint32_t dollars = (uint32_t)(value + 0.5f);
  char digits[16];
  snprintf(digits, sizeof digits, "%lu", (unsigned long)dollars);

  String out = "$";
  const int len = (int)strlen(digits);
  for (int i = 0; i < len; i++) {
    if (i > 0 && ((len - i) % 3) == 0) out += ',';
    out += digits[i];
  }
  return out;
}

// Lower half, Daily burn: the same seven-day mixed-source reading as the Mac
// card, with provider colors stacked into one bar per calendar day.
static void drawGlanceDailyBurn(int16_t padX, int16_t span, int16_t midY,
                                int16_t lowBottom) {
  drawTextAt(LABEL_DAILY_BURN, padX, (int16_t)(midY + 8), 2, COL_WHITE);
  if (dailyBurnN == 0) {
    drawTextAt("Update host for burn", padX, (int16_t)(midY + 42), 2, COL_DIM);
    return;
  }

  char today[24];
  snprintf(today, sizeof today, "Today %.0f%%",
           dailyBurnDays[dailyBurnN - 1].total);
  drawRightAt(today, (int16_t)(padX + span), (int16_t)(midY + 8), 2, COL_DIM);

  float maxTotal = 1;
  for (uint8_t i = 0; i < dailyBurnN; i++) {
    if (dailyBurnDays[i].total > maxTotal) maxTotal = dailyBurnDays[i].total;
  }

  const int16_t labelY = (int16_t)(lowBottom - 12);
  const int16_t chartTop = (int16_t)(midY + 34);
  const int16_t chartBottom = (int16_t)(labelY - 5);
  const int16_t chartH = (int16_t)(chartBottom - chartTop);
  const int16_t gap = 6;
  const int16_t barW = (int16_t)((span - (dailyBurnN - 1) * gap) / dailyBurnN);
  if (chartH <= 4 || barW <= 2) return;

  for (uint8_t i = 0; i < dailyBurnN; i++) {
    const DailyBurnDay &day = dailyBurnDays[i];
    const int16_t x = (int16_t)(padX + i * (barW + gap));
    gfx->fillRoundRect(x, chartTop, barW, chartH, 3, COL_BAR);
    int16_t filledH = day.total > 0
        ? (int16_t)((chartH * day.total / maxTotal) + 0.5f) : 0;
    if (filledH < 1 && day.total > 0) filledH = 1;
    int16_t y = chartBottom;
    for (int8_t slot = (int8_t)slotN - 1; slot >= 0; slot--) {
      const float amount = dailyBurnDays[i].burns[slot];
      if (amount <= 0 || filledH <= 0) continue;
      int16_t segment = (int16_t)((chartH * amount / maxTotal) + 0.5f);
      if (segment < 1) segment = 1;
      if (segment > y - chartTop) segment = (int16_t)(y - chartTop);
      if (segment <= 0) break;
      gfx->fillRect(x, (int16_t)(y - segment), barW, segment,
                    slots[slot].accent);
      y = (int16_t)(y - segment);
    }
    if (day.label.length()) {
      const int16_t labelW = textWidth(day.label.c_str(), 1);
      drawTextAt(day.label, (int16_t)(x + (barW - labelW) / 2), labelY,
                 1, COL_DIM);
    }
  }
}

// Lower half, Spend: the three estimated figures the Mac card leads with.
// This is intentionally not a provider-billing sum; quota percentages and
// provider spend controls do not share a trustworthy dollar unit.
static void drawGlanceSpend(int16_t padX, int16_t span, int16_t midY,
                            int16_t lowBottom) {
  drawTextAt(LABEL_SPEND, padX, (int16_t)(midY + 8), 2, COL_WHITE);
  drawRightAt("Estimated", (int16_t)(padX + span), (int16_t)(midY + 8), 2,
              COL_DIM);
  if (!spendSummary.ok) {
    drawTextAt("No spend yet", padX, (int16_t)(midY + 45), 2, COL_DIM);
    return;
  }

  const char *captions[3] = {"today", "per active day", "month"};
  const String values[3] = {
      formatUsd(spendSummary.today),
      formatUsd(spendSummary.avg),
      formatUsd(spendSummary.total),
  };
#if defined(BOARD_ROUND_UI)
  // Rows, not columns. Three columns on a circle are width-starved: the chord
  // at the figures' height affords about 130px a column, which caps the type
  // at the size the square panel already uses and leaves the bottom third of
  // the band empty. One figure a row gets most of the chord, so the numbers
  // fill the lower band the way the burn and history charts do.
  const int16_t bandTop = (int16_t)(midY + 30);
  const int16_t rowGap = 7;
  // Each row's budget is measured against its own caption, not the widest
  // one. "per active day" is the long label and the month total is the long
  // figure, and they are never on the same row — charging every row for both
  // costs two type sizes for a collision that cannot happen.
  uint8_t valueSize = 6;
  int16_t rowH = 0, totalH = 0, top = 0, numberX = 0, numberW = 0;
  while (valueSize > 2) {
    rowH = (int16_t)(valueSize * 8);
    totalH = (int16_t)(3 * rowH + 2 * rowGap);
    if (bandTop + totalH > lowBottom) {
      valueSize--;
      continue;
    }
    top = (int16_t)(bandTop + (lowBottom - bandTop - totalH) / 2);
    // The circle narrows row by row. Taking the narrowest chord the block
    // crosses and giving it to every row keeps the figures in one column
    // instead of a ragged right edge.
    roundBand(top, (int16_t)(top + totalH), 10, &numberX, &numberW);
    bool fits = true;
    for (uint8_t i = 0; i < 3; i++) {
      const int16_t budget =
          (int16_t)(numberW - textWidth(captions[i], 2) - 14);
      if (textWidth(values[i].c_str(), valueSize) > budget) fits = false;
    }
    if (fits) break;
    valueSize--;
  }
  rowH = (int16_t)(valueSize * 8);
  totalH = (int16_t)(3 * rowH + 2 * rowGap);
  top = (int16_t)(bandTop + (lowBottom - bandTop - totalH) / 2);
  roundBand(top, (int16_t)(top + totalH), 10, &numberX, &numberW);
  for (uint8_t i = 0; i < 3; i++) {
    const int16_t rowY = (int16_t)(top + i * (rowH + rowGap));
    drawTextAt(captions[i], numberX, (int16_t)(rowY + (rowH - 16) / 2), 2,
               COL_DIM);
    drawRightAt(values[i], (int16_t)(numberX + numberW), rowY, valueSize,
                COL_WHITE);
  }
#else
  const int16_t colW = (int16_t)(span / 3);
  const int16_t captionY = (int16_t)(midY + 24);
  const int16_t valueY = (int16_t)(midY + 41);
  for (uint8_t i = 0; i < 3; i++) {
    const int16_t colX = (int16_t)(padX + i * colW);
    drawTextAt(captions[i], colX, captionY, 1, COL_DIM);
    drawTextAt(values[i], colX, valueY, 3, COL_WHITE);
  }
  gfx->drawFastHLine(padX, lowBottom, span,
                     dimToward(COL_DIM, COL_BG, 0.35f));
#endif
}

// Lower half, activity reading: what shipped lately.
static void drawGlanceActivity(int16_t padX, int16_t span, int16_t midY,
                               int16_t lowBottom) {
  const Page lowPages[3] = {PAGE_VERCEL, PAGE_GIT, PAGE_LOCAL};

  // Local narrow (ports); Vercel/Git share the rest (~2–3 more chars).
  const int16_t lowTop = midY + 6;
  const int16_t localW = 78;
  const int16_t wideW = (span - localW) / 2;
  const int16_t lowW[3] = {wideW, (int16_t)(span - localW - wideW), localW};
  const int16_t lowX[3] = {
      padX,
      (int16_t)(padX + lowW[0]),
      (int16_t)(padX + lowW[0] + lowW[1])};
  const int16_t colPad = 4;
  const int16_t dotR = 5;
  const int16_t rowH = 20;
  const int16_t textX = 14;  // gap after status dot
  const uint8_t lowMax = 6;

  for (uint8_t i = 0; i < 3; i++) {
    glanceAddHit(lowX[i], midY, lowW[i], (int16_t)(lowBottom - midY), lowPages[i]);
    int16_t x = lowX[i] + colPad;
    int16_t colW = lowW[i] - colPad * 2;
    int16_t y = lowTop;

    if (i == 0) {
      // Vercel — status dot + project + compact age (like Git).
      drawTextAt("Vercel", x, y, 2, COL_WHITE);
      y += 22;
      if (vercelOk && vercelN > 0) {
        for (uint8_t d = 0; d < vercelN && d < lowMax; d++) {
          if (y > lowBottom - 18) break;
          const DeployRow &r = vercelRows[d];
          gfx->fillCircle(x + dotR, y + 8, dotR, statusColor(r.status));
          drawNameAgoRow(x + textX, y, (int16_t)(colW - textX),
                         r.project, glanceAgo(r.ago),
                         COL_WHITE, COL_DIM);
          y += rowH;
        }
      } else {
        drawTextAt(vercelOk ? "-" : "down", x, y, 2, COL_DIM);
      }
    } else if (i == 1) {
      // Git — repo leaf (no owner) + compact age (right-aligned).
      drawTextAt("Git", x, y, 2, COL_WHITE);
      y += 22;
      if (gitOk && gitN > 0) {
        for (uint8_t c = 0; c < gitN && c < lowMax; c++) {
          if (y > lowBottom - 18) break;
          String repo = gitRows[c].repo;
          int slash = repo.lastIndexOf('/');
          if (slash >= 0) repo = repo.substring(slash + 1);
          drawNameAgoRow(x, y, colW, repo, glanceAgo(gitRows[c].ago),
                         COL_WHITE, COL_DIM);
          y += rowH;
        }
      } else {
        drawTextAt(gitOk ? "-" : "down", x, y, 2, COL_DIM);
      }
    } else {
      // Local — one teal dot + port per listening server.
      drawTextAt("Local", x, y, 2, COL_WHITE);
      y += 22;
      if (localOk && localN > 0) {
        for (uint8_t s = 0; s < localN && s < lowMax; s++) {
          if (y > lowBottom - 18) break;
          gfx->fillCircle(x + dotR, y + 8, dotR, COL_LOCAL);
          String port = localRows[s].port > 0
                            ? (":" + String(localRows[s].port))
                            : "-";
          drawTextAt(port, x + textX, y, 2, COL_WHITE);
          y += rowH;
        }
      } else {
        drawTextAt(localOk ? "none" : "down", x, y, 2, COL_DIM);
      }
    }
  }
}

// Host `updated` ends in ±HHMM (e.g. +0200). Used to align the overall chart
// to the same local calendar week the Mac/iOS cards use. No NTP on the board.
static int32_t updatedTzOffsetS() {
  const int n = (int)updatedZ.length();
  if (n < 5) return 0;
  const char *s = updatedZ.c_str();
  const char sign = s[n - 5];
  if (sign != '+' && sign != '-') return 0;
  const int hh = (s[n - 4] - '0') * 10 + (s[n - 3] - '0');
  const int mm = (s[n - 2] - '0') * 10 + (s[n - 1] - '0');
  if (hh < 0 || hh > 14 || mm < 0 || mm > 59) return 0;
  const int32_t off = (int32_t)hh * 3600 + (int32_t)mm * 60;
  return sign == '-' ? -off : off;
}

// Clip a time/remaining segment to [tLo, tHi], interpolating at the edges so a
// weekly pool that runs past Friday still paints up to the chart's right edge
// instead of vanishing (Mac OverviewBurndownCard does the same).
static bool clipBurnSeg(uint32_t ta, float ra, uint32_t tb, float rb,
                        uint32_t tLo, uint32_t tHi,
                        uint32_t *oa, float *ora, uint32_t *ob, float *orb) {
  if (ta == tb) return false;
  if (ta > tb) {
    uint32_t ts = ta; ta = tb; tb = ts;
    float rs = ra; ra = rb; rb = rs;
  }
  if (tb < tLo || ta > tHi) return false;
  *oa = ta; *ora = ra; *ob = tb; *orb = rb;
  const float span = (float)(tb - ta);
  if (ta < tLo) {
    const float u = (float)(tLo - ta) / span;
    *oa = tLo;
    *ora = ra + u * (rb - ra);
  }
  if (tb > tHi) {
    const float u = (float)(tHi - ta) / span;
    *ob = tHi;
    *orb = ra + u * (rb - ra);
  }
  return *oa < *ob || (*oa == *ob && *ora != *orb);
}

// One provider's actual + dashed projection on a shared absolute-time axis.
// No budget diagonal — windows start and reset at different times.
static void drawOverallSeries(const Burndown &b, uint16_t accent,
                              int16_t x, int16_t y, int16_t w, int16_t h,
                              uint32_t tLo, uint32_t tHi) {
  if (!b.ok || (b.n < 1 && b.histN < 1) || tHi <= tLo) return;
  const uint32_t span = tHi - tLo;
  auto px = [&](uint32_t t) -> int16_t {
    if (t <= tLo) return x;
    if (t >= tHi) return (int16_t)(x + w - 1);
    return (int16_t)(x + (int32_t)((uint64_t)(t - tLo) * (w - 1) / span));
  };
  auto py = [&](float remaining) -> int16_t {
    float r = remaining < 0 ? 0 : (remaining > 100 ? 100 : remaining);
    return (int16_t)(y + h - 1 - (int16_t)(r * (h - 1) / 100.0f));
  };
  auto stroke = [&](int16_t ax, int16_t ay, int16_t bx, int16_t by,
                    uint16_t col) {
    for (int16_t d = -1; d <= 1; d++) {
      const int16_t a = (int16_t)(ay + d), c = (int16_t)(by + d);
      if (a < y || a > y + h - 1 || c < y || c > y + h - 1) continue;
      gfx->drawLine(ax, a, bx, c, col);
    }
  };

  const uint16_t line = accent;

  // Spent windows first, so the live curve covers them where they overlap.
  // A segment is skipped when a grant sits between its two samples: that pair
  // straddles a reset, and joining them would draw a diagonal climbing across
  // the chart between two budgets that never existed at the same time.
  if (b.histN > 1) {
    const uint16_t ghost = dimToward(accent, COL_BG, 0.70f);
    for (uint8_t i = 1; i < b.histN; i++) {
      // A pair straddling a grant is the recharge. Square it off on the grant
      // instant — level up to it, vertical through it — rather than letting
      // the raw diagonal imply the pool refilled over the sample gap. The
      // riser lands on the same x as the dotted rule below, so they agree.
      uint32_t grantAt = 0;
      for (uint8_t g = 0; g < b.grantN; g++) {
        if (b.grantT[g] > b.histT[i - 1] && b.grantT[g] <= b.histT[i]) {
          grantAt = b.grantT[g];
        }
      }
      if (grantAt != 0) {
        uint32_t la, lb; float lra, lrb;
        if (clipBurnSeg(b.histT[i - 1], b.histR[i - 1], grantAt, b.histR[i - 1],
                        tLo, tHi, &la, &lra, &lb, &lrb)) {
          gfx->drawLine(px(la), py(lra), px(lb), py(lrb), ghost);
        }
        if (grantAt > tLo && grantAt < tHi) {
          gfx->drawLine(px(grantAt), py(b.histR[i - 1]),
                        px(grantAt), py(b.histR[i]), ghost);
        }
        if (clipBurnSeg(grantAt, b.histR[i], b.histT[i], b.histR[i],
                        tLo, tHi, &la, &lra, &lb, &lrb)) {
          gfx->drawLine(px(la), py(lra), px(lb), py(lrb), ghost);
        }
        continue;
      }
      uint32_t ta, tb; float ra, rb;
      if (!clipBurnSeg(b.histT[i - 1], b.histR[i - 1], b.histT[i], b.histR[i],
                       tLo, tHi, &ta, &ra, &tb, &rb)) {
        continue;
      }
      // One pixel, not the three `stroke` lays down: this is context behind
      // the reading, and at three it competes with the live curve.
      gfx->drawLine(px(ta), py(ra), px(tb), py(rb), ghost);
    }
    // A dotted rule where each grant landed — the moment the curve above it
    // jumps back to full.
    for (uint8_t g = 0; g < b.grantN; g++) {
      const uint32_t at = b.grantT[g];
      if (at <= tLo || at >= tHi) continue;
      const int16_t gx = px(at);
      for (int16_t yy = y; yy < (int16_t)(y + h); yy += 4) {
        gfx->drawFastVLine(gx, yy, 2, ghost);
      }
    }
  }

  for (uint8_t i = 1; i < b.n; i++) {
    uint32_t ta, tb; float ra, rb;
    if (!clipBurnSeg(b.t[i - 1], b.remaining[i - 1], b.t[i], b.remaining[i],
                     tLo, tHi, &ta, &ra, &tb, &rb)) {
      continue;
    }
    stroke(px(ta), py(ra), px(tb), py(rb), line);
  }

  if (b.projN == 2) {
    uint32_t p0t = b.projT[0], p1t = b.projT[1];
    float p0r = b.projR[0], p1r = b.projR[1];
    // Crop at held reset — same as Mac/iOS OverallBurndownChartMath.
    if (b.t1 > 0 && p1t > b.t1 && p1t > p0t) {
      const float u = (float)(b.t1 - p0t) / (float)(p1t - p0t);
      p1r = p0r + u * (p1r - p0r);
      p1t = b.t1;
    }
    if (p0r < 0) p0r = 0;
    if (p1r < 0) p1r = 0;
    const float dR = p1r - p0r;
    if (dR < -0.5f || dR > 0.5f || b.warn) {
      uint32_t ta, tb; float ra, rb;
      if (clipBurnSeg(p0t, p0r, p1t, p1r, tLo, tHi, &ta, &ra, &tb, &rb)) {
        const int16_t x0 = px(ta), y0 = py(ra);
        const int16_t x1 = px(tb), y1 = py(rb);
        // Path-length 3 on / 9 off — same density for steep and shallow slopes.
        if (dR < -0.5f || dR > 0.5f) {
          strokeDashedProj(x0, y0, x1, y1, line, y, h);
        }
        if (tb == p1t) {
          const int16_t r = (b.warn && p1r <= 0.5f) ? 3 : 2;
          gfx->fillCircle(x1, y1, r, line);
        }
      }
    }
  }

  // Accent dotted reset at t1 when it falls inside the shared domain.
  if (b.t1 > tLo && b.t1 < tHi) {
    const int16_t rx = px(b.t1);
    for (int16_t yy = (int16_t)(y + 1); yy < (int16_t)(y + h - 1); yy += 4) {
      const int16_t seg = (yy + 2 < y + h - 1) ? 2 : 1;
      gfx->drawFastVLine(rx, yy, seg, accent);
    }
  }

  // Now marker — last in-range actual point.
  for (int8_t i = (int8_t)b.n - 1; i >= 0; i--) {
    if (b.t[i] < tLo || b.t[i] > tHi) continue;
    const int16_t nx = px(b.t[i]);
    const int16_t ny = py(b.remaining[i]);
    gfx->fillCircle(nx, ny, 3, line);
    gfx->drawCircle(nx, ny, 4, COL_BG);
    break;
  }
}

// Lower half, burndown reading: one combined chart (Mac/iOS Overall burndown)
// under the three rings — shared calendar week, no budget diagonal.
static void drawGlanceBurndown(int16_t padX, int16_t span, int16_t midY,
                               int16_t lowBottom) {
  uint32_t nowT = 0;
  uint8_t ready = 0;
  for (uint8_t i = 0; i < slotN; i++) {
    const Burndown &b = slots[i].burn;
    if (!b.ok) continue;
    // History alone counts: a provider whose window rolled this poll has one
    // live sample and a spent curve behind it, which is a chart, not a gap.
    if (b.n > 0 || b.histN > 0) ready++;
    // "Now" still comes from the live series only — the spent curve reaches
    // into the past, and letting it set the marker would drag it left.
    if (b.n > 0 && b.t[b.n - 1] > nowT) nowT = b.t[b.n - 1];
  }

  // Day labels between chart and verdicts so curves keep the plot area.
  // Verdicts are full-width rows (not 3 cramped columns) — size-2 "Runs out
  // tomorrow 00:49" simply does not fit a third of this panel.
  const int16_t axisH = 12;
  const int16_t rowH = 16;
  const int16_t legendH = (int16_t)(slotN * rowH + 2);
  const int16_t chartY = (int16_t)(midY + 6);
  const int16_t chartH =
      (int16_t)(lowBottom - legendH - axisH - chartY);
  int16_t chartX = padX;
  int16_t chartW = span;
#if defined(BOARD_ROUND_UI)
  // Size the plot for the chord it actually occupies, not for the much
  // narrower legend rows near the bottom of the circle. This recovers almost
  // a quarter of the panel width while keeping every plotted pixel inset.
  roundBand(chartY, (int16_t)(chartY + chartH - 1), 10, &chartX, &chartW);
#endif

  if (ready == 0 || chartH < 40) {
    drawCollectingHistory(chartX + 8,
                          chartY + (chartH > 0 ? chartH / 2 : 8), chartW);
    return;
  }
  collectSpinActive = false;

  // Fixed local calendar week: today−3 … today+4. Resets inside still paint;
  // farther ones stay off-canvas so history isn't compressed (matches
  // OverallBurndownChartMath on Mac/iOS).
  const int32_t tz = updatedTzOffsetS();
  const uint32_t localNow = (uint32_t)((int64_t)nowT + tz);
  const uint32_t localDay = localNow - (localNow % 86400u);
  const uint32_t todayUtc = (uint32_t)((int64_t)localDay - tz);
  const uint32_t tLo = todayUtc - 3u * 86400u;
  const uint32_t tHi = tLo + 7u * 86400u;

  const uint16_t track = dimToward(COL_WHITE, COL_BG, 0.35f);
  gfx->drawRect(chartX, chartY, chartW, chartH, track);

  // Horizontal 50% rule + vertical day rules (no Y labels — rings carry %).
  const uint16_t grid = dimToward(COL_WHITE, COL_BG, 0.22f);
  {
    const int16_t mid = (int16_t)(chartY + chartH / 2);
    gfx->drawFastHLine(chartX + 1, mid, chartW - 2, grid);
  }
  static const char *const WD[] = {
      "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};
  // Unix day 0 = Thu → (days + 4) % 7 = Sun..Sat.
  const int startWd = (int)(((localDay / 86400u) + 4u) % 7u);
  const int16_t axisY = (int16_t)(chartY + chartH + 2);
  const uint32_t daySpan = (tHi > tLo) ? (tHi - tLo) : 1u;
  const uint8_t dayCount =
      (uint8_t)((daySpan + 86400u - 1u) / 86400u);
  const uint8_t daysToLabel = dayCount > 14 ? 14 : dayCount;
  for (uint8_t d = 0; d < daysToLabel; d++) {
    const uint32_t dayT = tLo + (uint32_t)d * 86400u;
    if (dayT > tHi) break;
    const int16_t dx =
        (int16_t)(chartX +
                  (int32_t)((uint64_t)(dayT - tLo) * (chartW - 1) / (tHi - tLo)));
    if (d > 0) {
      gfx->drawFastVLine(dx, (int16_t)(chartY + 1), (int16_t)(chartH - 2), grid);
    }
    const int wd = (startWd - 3 + (int)d + 70) % 7;
    drawTextAt(WD[wd], (int16_t)(dx + 2), axisY, 1, COL_DIM);
  }

  // "Now" marker — darker than day rules so it isn't mistaken for one.
  if (nowT > tLo && nowT < tHi) {
    const uint16_t nowCol = dimToward(COL_WHITE, COL_BG, 0.45f);
    const int16_t nx =
        (int16_t)(chartX +
                  (int32_t)((uint64_t)(nowT - tLo) * (chartW - 1) / (tHi - tLo)));
    gfx->drawFastVLine(nx, (int16_t)(chartY + 1), (int16_t)(chartH - 2),
                       nowCol);
  }

  for (uint8_t i = 0; i < slotN; i++) {
    drawOverallSeries(slots[i].burn, slots[i].accent, chartX, chartY, chartW,
                      chartH, tLo, tHi);
  }

  // Chart split by slot → provider detail (same left→right order as rings).
  const int16_t chartHitH = (int16_t)(chartY + chartH + axisH - midY);
  const int16_t chartSlot =
      slotN > 0 ? (int16_t)(chartW / slotN) : chartW;
  for (uint8_t i = 0; i < slotN; i++) {
    glanceAddHit((int16_t)(chartX + (int16_t)i * chartSlot), midY,
                 chartSlot, chartHitH, slotPage(i));
  }

  // Legend: ring order, top → bottom. Full width so host verdicts stay intact.
  const int16_t legY = (int16_t)(lowBottom - legendH + 1);
  const int16_t dotR = 3;
  const int16_t textX = (int16_t)(padX + dotR * 2 + 8);
  const int16_t textW = (int16_t)(span - (textX - padX));
  for (uint8_t i = 0; i < slotN; i++) {
    const int16_t y = (int16_t)(legY + (int16_t)i * rowH);
    glanceAddHit(padX, y, span, rowH, slotPage(i));
    const Burndown &b = slots[i].burn;
    const uint16_t dot = b.ok ? slots[i].accent : COL_DIM;
    gfx->fillCircle((int16_t)(padX + dotR), (int16_t)(y + 6), dotR, dot);
    if (b.ok && b.verdict.length()) {
      drawTextAt(truncFit(b.verdict, textW, 2), textX, y, 2, COL_DIM);
    } else if (b.ok && b.n > 0) {
      char left[8];
      snprintf(left, sizeof left, "%d%%",
               (int)(b.remaining[b.n - 1] + 0.5f));
      drawTextAt(left, textX, y, 2, slots[i].accent);
    } else {
      drawTextAt("-", textX, y, 2, COL_DIM);
    }
  }
}

static void drawGlancePage() {
  gfx->clear(COL_BG);
  glanceClearHits();
  const int16_t W = scrW();
  const int16_t H = scrH();
#if defined(BOARD_ROUND_UI)
  int16_t padX = 0, span = 0;
  int16_t ringPadX = 0, ringSpan = 0;
  int16_t footerX = 0, footerW = 0;
  const int16_t titleY = 24;
  const int16_t modeY = 70;
  const int16_t ringCy = 143;
  const int16_t ringR = 30;
  const int16_t midY = 205;
  const int16_t lowBottom = 397;
  roundBand((int16_t)(midY + 1), lowBottom, 10, &padX, &span);
  roundBand((int16_t)(ringCy - ringR - 8),
            (int16_t)(ringCy + ringR + 24), 8, &ringPadX, &ringSpan);
  roundBand(418, 436, 8, &footerX, &footerW);

  drawCentered("Headroom", titleY, 3, COL_WHITE);
  char when[8];
  if (updatedHHMM(when, sizeof when)) {
    char updated[20];
    snprintf(updated, sizeof updated, "UPDATED %s", when);
    drawCentered(updated, 52, 1, COL_DIM);
  }
  const char *modeName = homeModeName(homeMode);
  const int16_t chipW = (int16_t)(textWidth(modeName, 2) + 16);
  const int16_t chipX = (int16_t)((W - chipW) / 2);
  gfx->drawRoundRect(chipX, modeY, chipW, 24, 6, COL_DIM);
  drawTextAt(modeName, (int16_t)(chipX + 8), (int16_t)(modeY + 6), 2,
             COL_DIM);
  glanceAddModeHit(chipX - 12, modeY - 8, chipW + 24, 40);
#else
  const int16_t padX = UI_PAD;
  // Paid for out of the slack under the ring labels — see UI_TOP.
  const int16_t top = UI_TOP;
  const int16_t bot = UI_PAD;

  drawTextAt("Headroom", padX, top, 3, COL_WHITE);
  // The chip names what the lower half is showing; the whole header band above
  // the rings switches it. A fingertip is ~40px on this panel, so the target is
  // the band, not the word — the outline is only there to say it's a control.
  const char *modeName = homeModeName(homeMode);
  const int16_t chipX = padX + 152;
  const int16_t chipW = (int16_t)(textWidth(modeName, 2) + 16);
  gfx->drawRoundRect(chipX - 8, top + 2, chipW, 24, 6, COL_DIM);
  drawTextAt(modeName, chipX, top + 6, 2, COL_DIM);
  glanceAddModeHit(0, 0, W, (int16_t)(top + 34));

  char when[8];
  if (updatedHHMM(when, sizeof when)) {
    drawRightAt(when, W - padX, top + 6, 2, COL_DIM);
  }

  // Equal top slots (quota rings) — as many as the host sent, up to three.
  const int16_t span = W - padX * 2;
  const int16_t ringR = 32;
  const int16_t ringCy = top + 74;
  const int16_t midY = ringCy + ringR + 38;  // clear labels under rings
  // No footer, but the bottom strip belongs to the corner glyphs (power left,
  // link right) — reserve it in both home modes so a long verdict or a sixth
  // port can't run underneath.
  const int16_t lowBottom = (int16_t)(H - bot - LINK_GLYPH_H);
  const int16_t ringPadX = padX;
  const int16_t ringSpan = span;
  const int16_t footerX = padX;
  const int16_t footerW = span;
#endif
  const int16_t slot = slotN > 0 ? (int16_t)(ringSpan / slotN) : ringSpan;

  // Hits first and in both styles: a tap still opens the detail page, and a
  // hold on the same band is what switches the style.
  for (uint8_t i = 0; i < slotN; i++) {
#if defined(BOARD_ROUND_UI)
    const int16_t hitTop = (int16_t)(ringCy - ringR - 12);
#else
    const int16_t hitTop = (int16_t)(top + 36);
#endif
    int16_t colX = ringPadX + (int16_t)i * slot;
    glanceAddHit(colX, hitTop, slot, (int16_t)(midY - hitTop),
                 slotPage(i));
  }
  if (glanceStyle == GLANCE_PACE) {
    drawPaceGlyph(ringPadX, ringSpan, ringCy);
  } else {
    for (uint8_t i = 0; i < slotN; i++) {
      drawQuotaRing((int16_t)(ringPadX + (int16_t)i * slot + slot / 2), ringCy,
                    ringR, slots[i].q, slots[i].accent,
                    slots[i].title.c_str());
    }
  }
  if (slotN == 0) {
#if defined(BOARD_ROUND_UI)
    drawCentered(providersSeen ? "No coding providers" : "Update the Mac host",
                 (int16_t)(ringCy - 12), 2, COL_DIM);
    if (!providersSeen) {
      drawCentered("host too old for providers", (int16_t)(ringCy + 10), 1,
                   COL_DIM);
    }
#else
    drawTextAt(providersSeen ? "No coding providers enabled"
                             : "Update the Mac host",
               padX, (int16_t)(ringCy - 12), 2, COL_DIM);
    if (!providersSeen) {
      drawTextAt("this host is too old to send providers", padX,
                 (int16_t)(ringCy + 10), 1, COL_DIM);
    }
#endif
  }

#if !defined(BOARD_ROUND_UI)
  gfx->drawFastHLine(padX, midY, span, COL_DIM);
#endif
  // The lower pane is a second, much larger mode button. Register it before
  // burndown's provider rows so the same tap gesture works everywhere on the
  // lower half, while the quota rings above remain detail targets.
#if defined(BOARD_ROUND_UI)
  // The physical circle already clips the square framebuffer. Let the touch
  // target cover the whole lower chord, including the wider round-aware plot.
  glanceAddModeHit(0, (int16_t)(midY + 1), W,
                   (int16_t)(lowBottom - midY - 1));
#else
  glanceAddModeHit(0, (int16_t)(midY + 1), W,
                   (int16_t)(lowBottom - midY - 1));
#endif

  if (homeMode != HOME_BURNDOWN || slotN > 0) {
    if (homeMode == HOME_DAILY_BURN) {
      drawGlanceDailyBurn(padX, span, midY, lowBottom);
    } else if (homeMode == HOME_HISTORY) {
      drawGlanceHistory(padX, span, midY, lowBottom);
    } else if (homeMode == HOME_SPEND) {
      drawGlanceSpend(padX, span, midY, lowBottom);
    } else {
      drawGlanceBurndown(padX, span, midY, lowBottom);
    }
  }

#if defined(BOARD_ROUND_UI)
  drawPowerGlyph(footerX, 436);
  drawLinkGlyph((int16_t)(footerX + footerW), 436);
#else
  drawPowerGlyph(padX, (int16_t)(H - bot));
  drawLinkGlyph((int16_t)(W - padX), (int16_t)(H - bot));
#endif
  present();
}

static const Burndown kNoBurndown{};

static const Burndown &burnFor(Page p) {
  if (!isSlotPage(p)) return kNoBurndown;
  const uint8_t i = slotOf(p);
  return i < slotN ? slots[i].burn : kNoBurndown;
}

static void drawQuotaPage() {
  gfx->clear(COL_BG);
  const int16_t W = scrW();
  const int16_t H = scrH();
#if defined(BOARD_ROUND_UI)
  int16_t padX = 0, contentW = 0;
  roundBand(116, 244, 12, &padX, &contentW);
  const int16_t top = 28;
  const int16_t bot = (int16_t)(H - 397);
#else
  const int16_t padX = UI_PAD;
  const int16_t top = UI_TOP;
  const int16_t bot = UI_PAD;
#endif

  static const ProviderSlot kEmptySlot{};
  const uint8_t index = slotOf(page);
  const ProviderSlot &s = index < slotN ? slots[index] : kEmptySlot;
  const ProviderQuota &q = s.q;
  const uint16_t accent = s.accent;
  const char *brand = s.title.length() ? s.title.c_str() : "-";

  // Header — provider name in its color, with a crisp white provider mark.
#if defined(BOARD_ROUND_UI)
  const bool hasMark = providerMark(s.id) != nullptr;
  const int16_t brandW = textWidth(brand, 3);
  const int16_t groupW = (int16_t)(brandW + (hasMark ? PROVIDER_MARK_W + 10 : 0));
  const int16_t groupX = (int16_t)((W - groupW) / 2);
  if (hasMark) drawProviderMark(s.id, groupX, top, COL_WHITE);
  drawTextAt(brand, (int16_t)(groupX + (hasMark ? PROVIDER_MARK_W + 10 : 0)),
             top, 3, accent);
  if (q.plan.length()) drawCentered(q.plan.c_str(), top + 31, 1, COL_DIM);
  char when[8], updatedLine[20];
  snprintf(updatedLine, sizeof updatedLine, "Updated %s",
           updatedHHMM(when, sizeof when) ? when : "--");
  int16_t metaX = 0, metaW = 0;
  roundBand(top + 55, top + 68, 12, &metaX, &metaW);
  drawTextAt(updatedLine, metaX, top + 55, 1, COL_DIM);
  if (dailyBurnN > 0 && index < slotN) {
    char todayBuf[24];
    snprintf(todayBuf, sizeof todayBuf, "Today %.0f%%",
             dailyBurnDays[dailyBurnN - 1].burns[index]);
    drawRightAt(todayBuf, (int16_t)(metaX + metaW), top + 55, 1, COL_DIM);
  }
  int16_t ruleX = 0, ruleW = 0;
  roundBand(top + 77, top + 77, 12, &ruleX, &ruleW);
  gfx->drawFastHLine(ruleX, top + 77, ruleW, COL_DIM);
#else
  // Rectangular header: mark owns the top-right corner. Plan copy ends before
  // it so neither collides.
  drawTextAt(brand, padX, top, 3, accent);
  const int16_t markX = (int16_t)(W - padX - PROVIDER_MARK_W);
  const bool hasMark = drawProviderMark(
      s.id, markX, top, COL_WHITE);
  if (q.plan.length()) {
    drawRightAt(q.plan.c_str(),
                hasMark ? (int16_t)(markX - 8) : (int16_t)(W - padX),
                top + 6, 2, COL_DIM);
  }
  char when[8], updatedLine[20];
  snprintf(updatedLine, sizeof updatedLine, "Updated %s",
           updatedHHMM(when, sizeof when) ? when : "--");
  drawTextAt(updatedLine, padX, top + 29, 2, COL_DIM);
  // Same "Today N%" reading as the Daily burn home pane, scoped to this
  // provider's headline meter — how many percentage points of the pool went
  // today, not the window's lifetime used figure on the bars below.
  if (dailyBurnN > 0 && index < slotN) {
    char todayBuf[24];
    snprintf(todayBuf, sizeof todayBuf, "Today %.0f%%",
             dailyBurnDays[dailyBurnN - 1].burns[index]);
    drawRightAt(todayBuf, W - padX, top + 29, 2, COL_DIM);
  }
  gfx->drawFastHLine(padX, top + 51, W - padX * 2, COL_DIM);
#endif

  // Content below the header rule splits evenly: meters on top, burndown below.
  // Glance rings already carry pct/pace, so the detail page's job is readable
  // full-width bars plus a chart large enough to read at desk distance.
  const int16_t contentTop =
#if defined(BOARD_ROUND_UI)
      116;
#else
      top + 62;
#endif
  // Down to the page inset. There is no footer on this page — the rule that
  // used to sit here was reserving 22px for page dots nothing draws, which put
  // 50px of nothing under the chart against 28 above the header.
  const int16_t contentBot = (int16_t)(H - bot);
  const int16_t midY = (int16_t)(contentTop + (contentBot - contentTop) / 2);

  if (q.ok && q.n > 0) {
    // Whatever meters the host sent, in its order. Only rows that fit above
    // the chart are drawn — the chart is the reason to open this page.
    int16_t rowY = contentTop;
    for (uint8_t i = 0; i < q.n; i++) {
      if (rowY + QUOTA_ROW_H > midY) break;
      const PoolRow &row = q.pools[i];
      drawQuotaRowCompact(row.title.c_str(), row.pct, row.pace, row.resets,
                          rowY, padX, accent);
      rowY += QUOTA_ROW_H;
    }

    // The provider's one extra line (Codex reset credits) in whatever top-half
    // space is left over, never the chart's.
    if (q.note.length() && rowY + 20 <= midY) {
      drawTextAt(q.note.c_str(), padX, rowY, 2, COL_DIM);
      if (q.note2.length()) {
        drawRightAt(q.note2.c_str(), W - padX, rowY, 2, COL_DIM);
      }
    }

    // Bottom half: burndown for the provider's longest window.
    const Burndown &burn = burnFor(page);
    int16_t burnY = midY;
#if defined(BOARD_ROUND_UI)
    int16_t chartPad = 0, chartW = 0;
    roundBand((int16_t)(burnY + 22), contentBot, 10, &chartPad, &chartW);
#else
    const int16_t chartPad = padX;
    const int16_t chartW = (int16_t)(W - padX * 2);
#endif
    drawTextAt(LABEL_BURNDOWN, chartPad, burnY, 2, COL_WHITE);
    // The host's verdict, in the host's words. Falls back to the locally
    // assembled tags only when talking to a server too old to send one.
    if (burn.verdict.length()) {
      // Neutral always — the words carry the warning; colour would shout.
      const uint16_t tint = burn.exhausted ? COL_DIM : COL_WHITE;
      // Host copy can grow, and running under the Burndown label is worse
      // than a smaller face, so measure before committing to size 2.
      const int16_t room =
          (int16_t)(chartW - textWidth(LABEL_BURNDOWN, 2) - 12);
      const bool full = textWidth(burn.verdict.c_str(), 2) <= room;
      drawRightAt(burn.verdict.c_str(), (int16_t)(chartPad + chartW),
                  full ? burnY : (int16_t)(burnY + 4), full ? 2 : 1, tint);
    } else if (burn.warn || burn.warn2) {
      drawRightAt("runs out early", (int16_t)(chartPad + chartW), burnY, 2,
                  COL_DIM);
    } else if (burn.estimated) {
      drawRightAt("estimated", (int16_t)(chartPad + chartW), burnY, 2,
                  COL_DIM);
    }
    burnY += 22;
    const int16_t chartH = (int16_t)(contentBot - burnY);
    if (chartH >= 36) {
      drawBurndown(burn, chartPad, burnY, chartW, chartH, accent);
    }
  } else {
    char missing[48];
    snprintf(missing, sizeof missing, "%s quota unavailable", brand);
    drawTextAt(missing, padX, top + 100, 2, COL_DIM);
  }

  present();
}

static void drawVercelPage() {
  gfx->clear(COL_BG);
  const int16_t W = scrW();
  const int16_t H = scrH();
#if defined(BOARD_ROUND_UI)
  int16_t padX = 0, pageW = 0;
  roundBand(116, 370, 10, &padX, &pageW);
  const int16_t top = 30;
  const int16_t bot = 58;
#else
  const int16_t padX = UI_PAD;
  const int16_t top = UI_TOP;
  const int16_t bot = UI_PAD;
#endif

#if defined(BOARD_ROUND_UI)
  drawCentered("Vercel", top, 3, COL_VERCEL);
  if (vercelTeam.length()) drawCentered(vercelTeam.c_str(), top + 31, 1, COL_DIM);
  char when[8], updatedLine[20];
  snprintf(updatedLine, sizeof updatedLine, "Updated %s",
           updatedHHMM(when, sizeof when) ? when : "--");
  drawCentered(updatedLine, top + 51, 1, COL_DIM);
  int16_t ruleX = 0, ruleW = 0;
  roundBand(top + 75, top + 75, 10, &ruleX, &ruleW);
  gfx->drawFastHLine(ruleX, top + 75, ruleW, COL_DIM);
#else
  drawTextAt("Vercel", padX, top, 3, COL_VERCEL);
  if (vercelTeam.length()) {
    drawRightAt(vercelTeam.c_str(), W - padX, top + 6, 2, COL_DIM);
  }
  char when[8], updatedLine[20];
  snprintf(updatedLine, sizeof updatedLine, "Updated %s",
           updatedHHMM(when, sizeof when) ? when : "--");
  drawTextAt(updatedLine, padX, top + 29, 2, COL_DIM);
  gfx->drawFastHLine(padX, top + 51, W - padX * 2, COL_DIM);
#endif

  int16_t rowY =
#if defined(BOARD_ROUND_UI)
      116;
#else
      top + 60;
#endif
  if (vercelOk && vercelN > 0) {
    for (uint8_t i = 0; i < vercelN; i++) {
      const DeployRow &r = vercelRows[i];
      uint16_t sc = statusColor(r.status);
      // Status pill (small filled circle)
      gfx->fillCircle(padX + 4, rowY + 8, 5, sc);

      String proj = truncFit(r.project, W - padX * 2 - 90, 2);
      drawTextAt(proj, padX + 18, rowY, 2, COL_WHITE);

      String right = r.ago.length() ? r.ago : "--";
      drawRightAt(right.c_str(), W - padX, rowY, 2, COL_DIM);

      String sub = r.status;
      if (r.target.length()) sub += " - " + r.target;
      else if (r.branch.length()) sub += " - " + r.branch;
      drawTextAt(truncFit(sub, W - padX * 2 - 20, 2),
                 padX + 18, rowY + 20, 2, COL_DIM);

      rowY += 46;
      if (rowY > H - bot - 40) break;
    }
  } else {
    drawTextAt(vercelOk ? "no deployments" : "vercel unavailable",
               padX, top + 100, 2, COL_DIM);
  }

  const int16_t footY = H - bot - 10;
#if defined(BOARD_ROUND_UI)
  drawCentered((String((int)vercelN) + " recent").c_str(), footY, 2, COL_DIM);
#else
  gfx->drawFastHLine(padX, footY - 12, W - padX * 2, COL_DIM);
  drawTextAt(String((int)vercelN) + " recent", padX, footY, 2, COL_DIM);
#endif
  present();
}

static void drawGitPage() {
  gfx->clear(COL_BG);
  const int16_t W = scrW();
  const int16_t H = scrH();
#if defined(BOARD_ROUND_UI)
  int16_t padX = 0, pageW = 0;
  roundBand(116, 370, 10, &padX, &pageW);
  const int16_t top = 30;
  const int16_t bot = 58;
#else
  const int16_t padX = UI_PAD;
  const int16_t top = UI_TOP;
  const int16_t bot = UI_PAD;
#endif

#if defined(BOARD_ROUND_UI)
  drawCentered("Git", top, 3, COL_GIT);
  char when[8], updatedLine[20];
  snprintf(updatedLine, sizeof updatedLine, "Updated %s",
           updatedHHMM(when, sizeof when) ? when : "--");
  drawCentered(updatedLine, top + 42, 1, COL_DIM);
  int16_t ruleX = 0, ruleW = 0;
  roundBand(top + 75, top + 75, 10, &ruleX, &ruleW);
  gfx->drawFastHLine(ruleX, top + 75, ruleW, COL_DIM);
#else
  drawTextAt("Git", padX, top, 3, COL_GIT);
  char when[8], updatedLine[20];
  snprintf(updatedLine, sizeof updatedLine, "Updated %s",
           updatedHHMM(when, sizeof when) ? when : "--");
  drawTextAt(updatedLine, padX, top + 29, 2, COL_DIM);
  gfx->drawFastHLine(padX, top + 51, W - padX * 2, COL_DIM);
#endif

  int16_t rowY =
#if defined(BOARD_ROUND_UI)
      116;
#else
      top + 60;
#endif
  if (gitOk && gitN > 0) {
    for (uint8_t i = 0; i < gitN; i++) {
      const CommitRow &r = gitRows[i];
      drawTextAt(truncFit(r.repo, W - padX * 2 - 80, 2), padX, rowY, 2, COL_DIM);

      String right = r.ago.length() ? r.ago : "--";
      drawRightAt(right.c_str(), W - padX, rowY, 2, COL_DIM);

      drawTextAt(truncFit(r.subject, W - padX * 2, 2),
                 padX, rowY + 21, 2, COL_WHITE);

      rowY += 47;
      if (rowY > H - bot - 40) break;
    }
  } else {
    drawTextAt(gitOk ? "no commits" : "git unavailable",
               padX, top + 100, 2, COL_DIM);
  }

  const int16_t footY = H - bot - 10;
#if defined(BOARD_ROUND_UI)
  drawCentered((String((int)gitN) + " recent").c_str(), footY, 2, COL_DIM);
#else
  gfx->drawFastHLine(padX, footY - 12, W - padX * 2, COL_DIM);
  drawTextAt(String((int)gitN) + " recent", padX, footY, 2, COL_DIM);
#endif
  present();
}

static void drawLocalPage() {
  gfx->clear(COL_BG);
  const int16_t W = scrW();
  const int16_t H = scrH();
#if defined(BOARD_ROUND_UI)
  int16_t padX = 0, pageW = 0;
  roundBand(116, 370, 10, &padX, &pageW);
  const int16_t top = 30;
  const int16_t bot = 58;
#else
  const int16_t padX = UI_PAD;
  const int16_t top = UI_TOP;
  const int16_t bot = UI_PAD;
#endif

#if defined(BOARD_ROUND_UI)
  drawCentered("Local", top, 3, COL_LOCAL);
  if (localHost.length()) drawCentered(localHost.c_str(), top + 31, 1, COL_DIM);
  char when[8], updatedLine[20];
  snprintf(updatedLine, sizeof updatedLine, "Updated %s",
           updatedHHMM(when, sizeof when) ? when : "--");
  drawCentered(updatedLine, top + 51, 1, COL_DIM);
  int16_t ruleX = 0, ruleW = 0;
  roundBand(top + 75, top + 75, 10, &ruleX, &ruleW);
  gfx->drawFastHLine(ruleX, top + 75, ruleW, COL_DIM);
#else
  drawTextAt("Local", padX, top, 3, COL_LOCAL);
  if (localHost.length()) {
    drawRightAt(localHost.c_str(), W - padX, top + 6, 2, COL_DIM);
  }
  char when[8], updatedLine[20];
  snprintf(updatedLine, sizeof updatedLine, "Updated %s",
           updatedHHMM(when, sizeof when) ? when : "--");
  drawTextAt(updatedLine, padX, top + 29, 2, COL_DIM);
  gfx->drawFastHLine(padX, top + 51, W - padX * 2, COL_DIM);
#endif

  int16_t rowY =
#if defined(BOARD_ROUND_UI)
      116;
#else
      top + 60;
#endif
  if (localOk && localN > 0) {
    for (uint8_t i = 0; i < localN; i++) {
      const ServerRow &r = localRows[i];
      gfx->fillCircle(padX + 4, rowY + 8, 5, COL_GREEN);

      String left = truncFit(r.name, W - padX * 2 - 90, 2);
      drawTextAt(left, padX + 18, rowY, 2, COL_WHITE);

      String right = r.port > 0 ? (":" + String(r.port)) : "--";
      drawRightAt(right.c_str(), W - padX, rowY, 2, COL_DIM);

      String sub = r.cmd.length() ? r.cmd : "listening";
      drawTextAt(truncFit(sub, W - padX * 2 - 20, 2),
                 padX + 18, rowY + 20, 2, COL_DIM);

      rowY += 46;
      if (rowY > H - bot - 40) break;
    }
  } else {
    drawTextAt(localOk ? "no servers" : "local unavailable",
               padX, top + 100, 2, COL_DIM);
  }

  const int16_t footY = H - bot - 10;
#if defined(BOARD_ROUND_UI)
  drawCentered((String((int)localN) + " up").c_str(), footY, 2, COL_DIM);
#else
  gfx->drawFastHLine(padX, footY - 12, W - padX * 2, COL_DIM);
  drawTextAt(String((int)localN) + " up", padX, footY, 2, COL_DIM);
#endif
  present();
}

static void drawDashboard() {
  // Full redraws own the panel; the spinner tick only resumes if a collecting
  // empty-state paints again below.
  collectSpinActive = false;
  if (page != PAGE_GLANCE) glanceClearHits();
  if (page == PAGE_GLANCE) drawGlancePage();
  else if (page == PAGE_VERCEL) drawVercelPage();
  else if (page == PAGE_GIT) drawGitPage();
  else if (page == PAGE_LOCAL) drawLocalPage();
  else drawQuotaPage();
}

// ---------------- Touch ----------------
// 1.8: FT3168 / CST816 via TCA9554 reset + raw I2C.
// 2.16: CST9220 via SensorLib. 1.75 round: CST9217's native command/read/ack
// protocol, copied from the working esp32-thinking-orbs target.
//
// Gestures: tap slot on Headroom → detail; tap on detail → home;
// long-press → home; BOOT → next page (cycles through all, then home).
#if defined(BOARD_TOUCH_CST92XX)
#if defined(BOARD_WS_AMOLED_216)
static TouchDrvCST92xx cstTouch;
#endif
static bool touchReady = false;
#if defined(BOARD_WS_AMOLED_216)
// The 2.16 CST9220 uses SensorLib's IRQ-gated path.
static bool cstFingerDown = false;
#endif
static uint32_t cstSampleAt = 0;
static uint8_t cstSampleN = 0;
static int16_t cstSampleX = 0, cstSampleY = 0;
#if defined(BOARD_WS_AMOLED_175_ROUND)
// The CST9217 and AXP2101 share the same I2C bus. The app loop is much faster
// than a display frame, so polling on every pass only creates bus pressure and
// makes a single empty/noisy packet look like a complete tap. Two readings at
// a modest cadence are still quick to a finger while rejecting those gaps.
static const uint32_t CST9217_POLL_MS = 12;
static const uint8_t CST9217_STABLE_SAMPLES = 2;
static uint8_t cstDownSamples = 0;
static uint8_t cstUpSamples = 0;
static bool cstStableDown = false;
static int16_t cstCandidateX = 0, cstCandidateY = 0;
static uint32_t cstStableDownAt = 0;
#endif
#else
static uint8_t touchAddr = 0;          // 0 = absent
#endif
static volatile bool touchIrq = false;
static bool touchDown = false;
static bool touchIgnore = false;
static bool touchCommitted = false;
static bool touchLongFired = false;
static bool touchHaveXY = false;   // true once this press has valid coords
static uint32_t touchDownMs = 0;
static uint32_t lastGestureMs = 0;
static int16_t touchStartX = 0, touchStartY = 0;
static int16_t touchLastX = 0, touchLastY = 0;
static const int16_t TAP_MAX_PX = 28;
static const uint32_t GESTURE_DEBOUNCE_MS = 40;
static const uint32_t LONG_PRESS_MS = 400;

// Named UiGesture — SensorLib 0.4+ also declares `enum class Gesture`.
enum UiGesture : uint8_t {
  GESTURE_NONE = 0,
  GESTURE_TAP,
  GESTURE_HOME,
  GESTURE_STYLE
};

// Set when GESTURE_TAP should open a glance slot (coords = touchStart*).
static bool gestureTapHit = false;

static void IRAM_ATTR onTouchIrq() { touchIrq = true; }

#if defined(BOARD_TOUCH_CST92XX)
#if defined(BOARD_WS_AMOLED_175_ROUND)
static bool cst9217WriteThenRead(const uint8_t *w, size_t wn, uint8_t *r,
                                 size_t rn) {
  Wire.beginTransmission(CST92XX_ADDR);
  Wire.write(w, wn);
  if (Wire.endTransmission(false) != 0) return false;
  if (rn == 0) return true;
  if (Wire.requestFrom((int)CST92XX_ADDR, (int)rn) != (int)rn) return false;
  for (size_t i = 0; i < rn; i++) r[i] = Wire.read();
  return true;
}

static bool cst9217Write(const uint8_t *w, size_t wn) {
  Wire.beginTransmission(CST92XX_ADDR);
  Wire.write(w, wn);
  return Wire.endTransmission() == 0;
}

static bool cst9217Attach() {
  pinMode(TP_RST, OUTPUT);
  digitalWrite(TP_RST, LOW);
  delay(10);
  digitalWrite(TP_RST, HIGH);
  delay(30);

  uint8_t buf[8];
  const uint8_t enterCmd[2] = {0xD1, 0x01};
  if (!cst9217Write(enterCmd, sizeof enterCmd)) return false;
  delay(10);

  const uint8_t readCheck[2] = {0xD1, 0xFC};
  if (!cst9217WriteThenRead(readCheck, sizeof readCheck, buf, 4)) return false;
  const uint32_t checkcode = ((uint32_t)buf[3] << 24) |
                             ((uint32_t)buf[2] << 16) |
                             ((uint32_t)buf[1] << 8) | buf[0];

  const uint8_t readRes[2] = {0xD1, 0xF8};
  if (!cst9217WriteThenRead(readRes, sizeof readRes, buf, 4)) return false;
  const uint16_t resX = (uint16_t)((buf[1] << 8) | buf[0]);
  const uint16_t resY = (uint16_t)((buf[3] << 8) | buf[2]);

  const uint8_t readType[2] = {0xD2, 0x04};
  if (!cst9217WriteThenRead(readType, sizeof readType, buf, 4)) return false;
  const uint16_t chipType = (uint16_t)((buf[3] << 8) | buf[2]);

  const uint8_t readVersion[2] = {0xD2, 0x08};
  if (!cst9217WriteThenRead(readVersion, sizeof readVersion, buf, 8))
    return false;
  const uint32_t fwVersion = ((uint32_t)buf[3] << 24) |
                             ((uint32_t)buf[2] << 16) |
                             ((uint32_t)buf[1] << 8) | buf[0];

  Serial.printf("CST9217: check=0x%08lX res=%ux%u type=0x%04X fw=0x%08lX\n",
                (unsigned long)checkcode, resX, resY, chipType,
                (unsigned long)fwVersion);
  return fwVersion != 0xA5A5A5A5 &&
         (checkcode & 0xFFFF0000UL) == 0xCACA0000UL &&
         (chipType == 0x9220 || chipType == 0x9217);
}

static bool cst9217Poll(int16_t *x, int16_t *y) {
  uint8_t buf[15];
  const uint8_t readCmd[2] = {0xD0, 0x00};
  if (!cst9217WriteThenRead(readCmd, sizeof readCmd, buf, sizeof buf))
    return false;
  const uint8_t ackCmd[3] = {0xD0, 0x00, 0xAB};
  if (!cst9217Write(ackCmd, sizeof ackCmd) || buf[6] != 0xAB) return false;

  const uint8_t numPoints = buf[5] & 0x7F;
  const uint8_t id = buf[0] >> 4;
  const uint8_t event = buf[0] & 0x0F;
  if (numPoints == 0 || numPoints > 2 || id >= 2 || event != 0x06)
    return false;
  *x = (int16_t)(((uint16_t)buf[1] << 4) | (buf[3] >> 4));
  *y = (int16_t)(((uint16_t)buf[2] << 4) | (buf[3] & 0x0F));
  return true;
}
#endif

static bool cstPollSample() {
  const uint32_t now = millis();

#if defined(BOARD_WS_AMOLED_175_ROUND)
  // CST9217 contact and release share one read/ack transaction. Poll
  // continuously as the working Thinking Orbs firmware does, but at a bounded
  // cadence: Headroom's loop is otherwise far faster than that app's frame
  // loop. Cache the stable state between polls so "not due yet" never becomes
  // a false release.
  if (cstSampleAt != 0 && now - cstSampleAt < CST9217_POLL_MS)
    return cstSampleN > 0;
  if (touchIrq) touchIrq = false;

  int16_t rawX = 0, rawY = 0;
  const bool rawDown = cst9217Poll(&rawX, &rawY);
  cstSampleAt = now;

  if (rawDown) {
    cstUpSamples = 0;
    if (cstStableDown) {
      cstSampleX = rawX;
      cstSampleY = rawY;
      cstSampleN = 1;
      return true;
    }

    if (cstDownSamples == 0 ||
        abs(rawX - cstCandidateX) > TAP_MAX_PX ||
        abs(rawY - cstCandidateY) > TAP_MAX_PX) {
      cstCandidateX = rawX;
      cstCandidateY = rawY;
      cstDownSamples = 1;
      cstSampleN = 0;
      return false;
    }

    if (cstDownSamples < CST9217_STABLE_SAMPLES) cstDownSamples++;
    if (cstDownSamples < CST9217_STABLE_SAMPLES) {
      cstSampleN = 0;
      return false;
    }

    cstStableDown = true;
    cstStableDownAt = now;
    cstDownSamples = 0;
    cstSampleX = rawX;
    cstSampleY = rawY;
    cstSampleN = 1;
    int16_t logicalX = 0, logicalY = 0;
    nativeToLogical(rawX, rawY, &logicalX, &logicalY);
    Serial.printf("touch DOWN raw=%d,%d logical=%d,%d\n", rawX, rawY,
                  logicalX, logicalY);
    return true;
  }

  cstDownSamples = 0;
  if (cstStableDown) {
    if (cstUpSamples < CST9217_STABLE_SAMPLES) cstUpSamples++;
    if (cstUpSamples < CST9217_STABLE_SAMPLES) {
      cstSampleN = 1;
      return true;
    }
    Serial.printf("touch UP held=%lums\n",
                  (unsigned long)(now - cstStableDownAt));
  }
  cstStableDown = false;
  cstUpSamples = 0;
  cstSampleN = 0;
  return false;
#else
  if (cstSampleAt == now) return cstSampleN > 0;
  const bool irq = touchIrq;
  if (irq) touchIrq = false;
  // No edge and no active finger → stay off the bus.
  if (!irq && !cstFingerDown) {
    cstSampleN = 0;
    return false;
  }

  int16_t xs[2] = {0}, ys[2] = {0};
  cstSampleN = cstTouch.getPoint(xs, ys, 2);
  cstSampleAt = now;
  if (cstSampleN > 0) {
    cstFingerDown = true;
    cstSampleX = xs[0];
    cstSampleY = ys[0];
    return true;
  }
  cstFingerDown = false;
  return false;
#endif
}
#endif

#if !defined(BOARD_TOUCH_CST92XX)
static bool touchWrite8(uint8_t addr, uint8_t reg, uint8_t val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

static bool touchRead8(uint8_t addr, uint8_t reg, uint8_t *out) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)addr, 1) != 1) return false;
  *out = Wire.read();
  return true;
}
#endif

static bool touchReadXY(int16_t *x, int16_t *y) {
#if defined(BOARD_TOUCH_CST92XX)
  if (!touchReady) return false;
  if (!cstPollSample()) return false;
  *x = cstSampleX;
  *y = cstSampleY;
  return true;
#else
  if (!touchAddr) return false;
  uint8_t n = 0;
  if (!touchRead8(touchAddr, 0x02, &n) || (n & 0x0F) == 0) return false;

  Wire.beginTransmission(touchAddr);
  Wire.write(0x03);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)touchAddr, 4) != 4) return false;
  uint8_t b0 = Wire.read();
  uint8_t b1 = Wire.read();
  uint8_t b2 = Wire.read();
  uint8_t b3 = Wire.read();
  *x = (int16_t)(((uint16_t)(b0 & 0x0F) << 8) | b1);
  *y = (int16_t)(((uint16_t)(b2 & 0x0F) << 8) | b3);
  return true;
#endif
}

static bool touchInit() {
#if defined(BOARD_TOUCH_CST92XX)
#if defined(BOARD_WS_AMOLED_175_ROUND)
  // The working Thinking Orbs target talks to this exact CST9217 directly.
  // SensorLib's generic CST92xx init completed but produced no live touches
  // on this unit, so use the controller's own attach and ACK protocol.
  Wire.setClock(400000);
  pinMode(TP_INT, INPUT_PULLUP);
  if (!i2cPresent(CST92XX_ADDR) || !cst9217Attach()) {
    Serial.println("touch: CST9217 @ 0x5A unavailable — gestures disabled");
    touchReady = false;
    return false;
  }
  attachInterrupt(digitalPinToInterrupt(TP_INT), onTouchIrq, FALLING);
  touchReady = true;
  Serial.printf("touch CST9217 @ 0x%02X irq=GPIO%d raw-ack\n",
                CST92XX_ADDR, TP_INT);
  return true;
#else
  // Waveshare CST92xx touch examples:
  //   setPins → begin → setMax/Swap/Mirror → INPUT_PULLUP + FALLING IRQ
  // begin()'s getAttribute() enters command mode and never leaves it.
  // SensorLib's CST9217 example resets after begin so REG_READ reports points.
  cstTouch.setPins(TP_RST, TP_INT);
  if (!cstTouch.begin(Wire, CST92XX_SLAVE_ADDRESS, IIC_SDA, IIC_SCL)) {
    Serial.println("touch: CST92xx @ 0x5A failed — gestures disabled");
    touchReady = false;
    Wire.setClock(400000);
    return false;
  }
  cstTouch.reset();
  delay(50);
  Wire.setClock(400000);   // begin() may have re-init'd Wire without our clock

  // The 2.16 panel is mounted 90° from scan and gets MADCTL 0xA0.
  cstTouch.setMaxCoordinates(LCD_WIDTH, LCD_HEIGHT);
  cstTouch.setSwapXY(true);
  cstTouch.setMirrorXY(true, false);
  pinMode(TP_INT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(TP_INT), onTouchIrq, FALLING);
  touchReady = true;
  Serial.printf("touch %s @ 0x%02X irq=GPIO%d res=%ux%u\n",
                cstTouch.getModelName(), CST92XX_SLAVE_ADDRESS, TP_INT,
                (unsigned)cstTouch.getResolutionX(),
                (unsigned)cstTouch.getResolutionY());
  return true;
#endif
#else
  pinMode(TP_INT, INPUT_PULLUP);

  // Prefer FT3168 @ 0x38 (product docs); fall back to CST816 @ 0x15 (V2 demos).
  if (i2cPresent(FT3168_ADDR)) {
    touchAddr = FT3168_ADDR;
    // Active mode (reg 0x00 = normal). Do NOT put the chip in monitor
    // (0xA5=0x01) — monitor reports no coordinates while held.
    touchWrite8(touchAddr, 0x00, 0x00);
    touchWrite8(touchAddr, 0xA5, 0x00);
    delay(20);
  } else if (i2cPresent(CST816_ADDR)) {
    touchAddr = CST816_ADDR;
    // Interrupt mode: periodic reports while touched (Waveshare demo).
    touchWrite8(touchAddr, 0xFA, 0x40);
    delay(20);
  } else {
    Serial.println("touch: no FT3168@0x38 or CST816@0x15 — gestures disabled");
    return false;
  }

  uint8_t id = 0xFF;
  // FT: ID @ 0xA0 (0x03 = FT3168). CST: ID @ 0xA7.
  touchRead8(touchAddr, touchAddr == FT3168_ADDR ? 0xA0 : 0xA7, &id);
  attachInterrupt(digitalPinToInterrupt(TP_INT), onTouchIrq, FALLING);
  Serial.printf("touch @ 0x%02X id=0x%02X irq=GPIO%d\n", touchAddr, id, TP_INT);
  return true;
#endif
}

static bool touchPressed() {
#if defined(BOARD_TOUCH_CST92XX)
  if (!touchReady) return false;
  return cstPollSample();
#else
  if (!touchAddr) return false;
  uint8_t n = 0;
  if (!touchRead8(touchAddr, 0x02, &n)) return false;
  return (n & 0x0F) > 0;
#endif
}

// Logical deltas. 1.8 canvas rotation 3: X ↔ inverted native Y.
static void touchLogicalDelta(int16_t *dHoriz, int16_t *dVert) {
  const int16_t dNatX = (int16_t)(touchLastX - touchStartX);
  const int16_t dNatY = (int16_t)(touchLastY - touchStartY);
#if defined(BOARD_TOUCH_CST92XX)
  *dHoriz = dNatX;
  *dVert = dNatY;
#else
  *dHoriz = (int16_t)(-dNatY);
  *dVert = dNatX;
#endif
}

// Fire navigation on press (like BOOT), not on release — release felt laggy.
// FT3168 often asserts "down" one poll before XY is readable. We must keep
// trying until coords arrive; a one-shot edge check drops most taps when
// serviceUi() samples aggressively during USB waits.
static UiGesture consumeGesture() {
#if defined(BOARD_TOUCH_CST92XX)
  if (!touchReady) return GESTURE_NONE;
#else
  if (!touchAddr) return GESTURE_NONE;
  // FT3168 path polls registers directly; IRQ is a wake hint only.
  if (touchIrq) touchIrq = false;
#endif

  const bool down = touchPressed();
  const uint32_t now = millis();
  int16_t x = 0, y = 0;
  const bool haveXY = down && touchReadXY(&x, &y);

  if (down) {
    if (!touchDown) {
      touchDown = true;
      touchCommitted = false;
      touchLongFired = false;
      touchHaveXY = false;
      gestureTapHit = false;
      touchDownMs = now;
      touchStartX = touchStartY = touchLastX = touchLastY = 0;
      if ((now - lastGestureMs) < GESTURE_DEBOUNCE_MS) {
        touchIgnore = true;
        return GESTURE_NONE;
      }
      touchIgnore = false;
    }
    if (touchIgnore || touchCommitted) return GESTURE_NONE;

    if (haveXY) {
      if (!touchHaveXY) {
        touchHaveXY = true;
        touchStartX = touchLastX = x;
        touchStartY = touchLastY = y;
      } else {
        touchLastX = x;
        touchLastY = y;
      }
    }

    // Detail page: any press with or without XY goes home immediately.
    if (page != PAGE_GLANCE) {
      touchCommitted = true;
      lastGestureMs = now;
      return GESTURE_HOME;
    }

    // Glance: wait for XY, then act on the slot under the finger.
    if (touchHaveXY) {
      GlanceHit hit;
      if (glanceHitAt(touchStartX, touchStartY, &hit)) {
        // The mode band answers on the way down — it has nothing to hold for.
        if (hit.kind == HIT_MODE) {
          touchCommitted = true;
          lastGestureMs = now;
          gestureTapHit = true;
          return GESTURE_TAP;
        }
        // A ring slot answers on release instead, so the same press can be
        // held to switch how the upper half reads. A tap lifts in well under
        // LONG_PRESS_MS, so the detail page still opens on the first touch.
        if (!touchLongFired && (now - touchDownMs) >= LONG_PRESS_MS) {
          touchLongFired = true;
          touchCommitted = true;
          lastGestureMs = now;
          return GESTURE_STYLE;
        }
        return GESTURE_NONE;
      }
      // Missed slots — allow long-press sync on empty chrome.
      int16_t dHoriz = 0, dVert = 0;
      touchLogicalDelta(&dHoriz, &dVert);
      if (!touchLongFired &&
          abs(dHoriz) <= TAP_MAX_PX && abs(dVert) <= TAP_MAX_PX &&
          (now - touchDownMs) >= LONG_PRESS_MS) {
        touchLongFired = true;
        touchCommitted = true;
        lastGestureMs = now;
        return GESTURE_HOME;
      }
    }
    return GESTURE_NONE;
  }

  if (!touchDown) return GESTURE_NONE;
  // A ring slot pressed and let go without being held is the tap that opens
  // its detail page. Anything already committed has had its answer.
  const bool slotTap = page == PAGE_GLANCE && touchHaveXY && !touchIgnore &&
                       !touchCommitted;
  touchDown = false;
  touchIgnore = false;
  touchCommitted = false;
  touchLongFired = false;
  touchHaveXY = false;
  if (slotTap) {
    GlanceHit hit;
    if (glanceHitAt(touchStartX, touchStartY, &hit) && hit.kind == HIT_PAGE) {
      lastGestureMs = now;
      gestureTapHit = true;
      return GESTURE_TAP;
    }
  }
  gestureTapHit = false;
  return GESTURE_NONE;
}

// ---------------- Arduino ----------------
static uint32_t lastPoll = 0;

// Consecutive failed fetches, for backoff. A host that is off (Mac asleep,
// laptop elsewhere) shouldn't be hammered every POLL_INTERVAL_S forever —
// each attempt costs a blocking connect timeout the UI has to sit through.
static uint8_t fetchFails = 0;
static const uint8_t FETCH_BACKOFF_MAX = 8;   // POLL_INTERVAL_S * 8 ceiling

static uint32_t pollIntervalMs() {
  // A boot can finish before either USB-CDC or Wi-Fi is ready. Do not apply
  // the normal 60-second backoff while the board has no payload at all: that
  // turns one missed startup fetch into a screen that appears dead until a
  // reboot. Keep trying at a short, bounded cadence until the first payload
  // lands; once we have data, normal failure backoff applies again.
  if (!haveData) return 5000u;
  // A successful-but-empty first payload after host restart used to park here
  // for a full POLL_INTERVAL_S with "Collecting history" frozen on screen.
  if (haveData && !burnHistoryReady()) return BURN_WARMUP_POLL_MS;
  uint32_t mult = fetchFails < FETCH_BACKOFF_MAX ? fetchFails : FETCH_BACKOFF_MAX;
  if (mult == 0) mult = 1;
  return (uint32_t)POLL_INTERVAL_S * 1000u * mult;
}

static void goToPage(Page target, const char *why) {
  if (target == page) return;
  page = target;
  Serial.printf("%s → page %s\n", why, pageName(page));
  if (haveData) drawDashboard();
  else drawStatus("fetching...", COL_DIM);
}

static void goHome() {
  if (page == PAGE_GLANCE) return;
  goToPage(PAGE_GLANCE, "home");
}

static void toggleHomeMode() {
  if (homeMode == HOME_DAILY_BURN) {
    homeMode = HOME_HISTORY;
  } else if (homeMode == HOME_HISTORY) {
    homeMode = HOME_SPEND;
  } else if (homeMode == HOME_SPEND) {
    homeMode = HOME_BURNDOWN;
  } else {
    homeMode = HOME_DAILY_BURN;
  }
  homeModeSave();
  Serial.printf("tap → home mode %s\n", homeModeName(homeMode));
  if (haveData) drawDashboard();
  else drawStatus("fetching...", COL_DIM);
}

static void toggleGlanceStyle() {
  glanceStyle = glanceStyle == GLANCE_RINGS ? GLANCE_PACE : GLANCE_RINGS;
  glanceStyleSave();
  Serial.printf("hold → glance style %s\n",
                glanceStyle == GLANCE_PACE ? "pace" : "rings");
  if (haveData) drawDashboard();
  else drawStatus("fetching...", COL_DIM);
}

static bool pageEnabled(Page p) {
  switch (p) {
    case PAGE_GLANCE: return true;
    // Slots are the host's `focus`: already enabled-only and in pinned
    // order, so an empty slot is simply a page that does not exist.
    case PAGE_SLOT0:  return slotN > 0;
    case PAGE_SLOT1:  return slotN > 1;
    case PAGE_SLOT2:  return slotN > 2;
    // A source page needs the source on (Integrations) and the page shown
    // (Desk display); either switch alone takes it out of the cycle.
    case PAGE_VERCEL:
      return sourceEnabled("vercel") && displayPageShown(DISP_PAGE_VERCEL);
    case PAGE_GIT:
      return sourceEnabled("git") && displayPageShown(DISP_PAGE_GIT);
    case PAGE_LOCAL:
      return sourceEnabled("local") && displayPageShown(DISP_PAGE_LOCAL);
    default: return true;
  }
}

// BOOT cycles Headroom → enabled sources only → Headroom.
static void goNextPage() {
  Page next = page;
  for (uint8_t i = 0; i < (uint8_t)PAGE_COUNT; i++) {
    next = (Page)(((int)next + 1) % (int)PAGE_COUNT);
    if (pageEnabled(next)) {
      goToPage(next, "boot");
      return;
    }
  }
}

static void forceSyncFromDesk() {
  drawStatus("syncing…", COL_AMBER);
  // An explicit long-press means the user wants a retry now — clear any
  // backoff the poller built up while the host was away.
  fetchFails = 0;
  bool ok = requestSyncRefresh();
  delay(150);
  yield();
  esp_task_wdt_reset();
  if (fetchUsage(USB_TIMEOUT_SYNC_MS)) {
    if (!pageEnabled(page)) page = PAGE_GLANCE;
    drawDashboard();
    if (pendingCelebration) {
      pendingCelebration = false;
      if (displayCelebrate) {
        playResetCelebration(pendingCelebrationAccent);
        drawDashboard();
      }
    }
  } else {
    drawStatus(ok ? "synced" : "sync failed", ok ? COL_GREEN : COL_RED);
  }
}

// ---------------- OTA ----------------
// The board lives on a shelf; without this every firmware tweak means finding
// the cable. Only starts once Wi-Fi is up.
static bool otaUp = false;

static void otaBegin() {
  if (otaUp || WiFi.status() != WL_CONNECTED) return;
  ArduinoOTA.setHostname(OTA_HOSTNAME);
  if (sizeof(OTA_PASSWORD) > 1) ArduinoOTA.setPassword(OTA_PASSWORD);
  ArduinoOTA.onStart([]() {
    // The flash write starves everything else; the watchdog would fire.
    esp_task_wdt_delete(NULL);
    drawStatus("OTA update…", COL_AMBER);
  });
  ArduinoOTA.onProgress([](unsigned int done, unsigned int total) {
    static uint8_t lastPct = 255;
    uint8_t pct = total ? (uint8_t)((done * 100) / total) : 0;
    if (pct == lastPct) return;
    lastPct = pct;
    char line[24];
    snprintf(line, sizeof line, "OTA %u%%", (unsigned)pct);
    drawStatus(line, COL_CRT);
  });
  ArduinoOTA.onEnd([]() { drawStatus("OTA done — rebooting", COL_GREEN); });
  ArduinoOTA.onError([](ota_error_t) {
    drawStatus("OTA failed", COL_RED);
    esp_task_wdt_add(NULL);
  });
  ArduinoOTA.begin();
  otaUp = true;
  Serial.printf("OTA ready at %s.local\n", OTA_HOSTNAME);
}

// Physical keys.
//   BOOT          — cycle pages (all boards)
//   secondary     — on glance: short = cycle lower pane (homeMode); long = force-sync
//                   on detail: home
//                   1.8: PWR via TCA EXIO4 (active HIGH)
//                   2.16: IO18 (left, active LOW)
//                   1.75 round: AXP2101 PEKEY short-press IRQ (release event;
//                   a four-second hold remains hardware power-off)
//   style (2.16)  — toggle rings↔pace (PWR / SYS_OUT, active HIGH; short only —
//                   ~4s hold still hardware-powers-off via AXP)
static bool gpioActive(int pin, bool activeLow) {
  const bool low = digitalRead(pin) == LOW;
  return activeLow ? low : !low;
}

#if defined(BOARD_TCA9554)
static bool tcaPwrPressed() {
  // 1.8 PWR = TCA9554 input bit EXIO4, active HIGH while pressed.
  Wire.beginTransmission(tcaAddr);
  Wire.write(0x00);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)tcaAddr, 1) != 1) return false;
  const uint8_t in = Wire.read();
  return (in & (1u << BTN_PWR_TCA_BIT)) != 0;
}
#endif

#if defined(BOARD_WS_AMOLED_175_ROUND)
static bool roundPwrShortPressed() {
  if (!pmuOk) return false;
  // PKEY IRQs persist until cleared, so checking at human-button cadence saves
  // most of the shared I2C traffic without losing a press.
  static uint32_t lastPollAt = 0;
  const uint32_t now = millis();
  if (lastPollAt != 0 && now - lastPollAt < 25) return false;
  lastPollAt = now;
  PMU.getIrqStatus();
  const bool pressed = PMU.isPekeyShortPressIrq();
  PMU.clearIrqStatus();
  return pressed;
}
#endif

static void pollButtons() {
  static bool bootWas = false, secWas = false, styleWas = false;
  static uint32_t bootEdge = 0, secDownAt = 0, styleDownAt = 0;
  static bool secLongFired = false;
  const uint32_t now = millis();

  const bool bootDown = gpioActive(BTN_BOOT, true);
  if (bootDown != bootWas && (now - bootEdge) >= 40) {
    bootEdge = now;
    bootWas = bootDown;
    if (bootDown) {
      Serial.println("btn BOOT → next");
      goNextPage();
    }
  }

#if defined(BOARD_WS_AMOLED_175_ROUND)
  if (roundPwrShortPressed()) {
    if (page == PAGE_GLANCE) {
      Serial.println("btn PWR → lower pane");
      toggleHomeMode();
    } else {
      Serial.println("btn PWR → home");
      goHome();
    }
  }
#endif

#if defined(BOARD_WS_AMOLED_216)
  const bool secDown = gpioActive(BTN_SECONDARY, BTN_SECONDARY_ACTIVE_LOW);
#elif defined(BOARD_TCA9554)
  const bool secDown = tcaPwrPressed();
#else
  const bool secDown = false;
#endif
  if (secDown && !secWas) {
    secDownAt = now;
    secLongFired = false;
  }
  // Long-press sync while still held on glance (same threshold as touch).
  if (secDown && page == PAGE_GLANCE && !secLongFired &&
      (now - secDownAt) >= LONG_PRESS_MS) {
    secLongFired = true;
    Serial.println("btn secondary → sync");
    forceSyncFromDesk();
  }
  if (secDown != secWas) {
    secWas = secDown;
    if (!secDown) {
      const uint32_t held = now - secDownAt;
      if (held >= 40 && !secLongFired) {
        if (page == PAGE_GLANCE) {
          Serial.println("btn secondary → lower pane");
          toggleHomeMode();
          drawDashboard();
        } else {
          Serial.println("btn secondary → home");
          goHome();
        }
      }
    }
  }

#if BTN_HAS_STYLE
  const bool styleDown = gpioActive(BTN_STYLE, BTN_STYLE_ACTIVE_LOW);
  if (styleDown && !styleWas) styleDownAt = now;
  if (styleDown != styleWas) {
    styleWas = styleDown;
    if (!styleDown) {
      const uint32_t held = now - styleDownAt;
      // Ignore holds past 800ms — longer presses are heading for AXP power-off.
      if (held >= 40 && held < 800) {
        Serial.println("btn PWR → style");
        toggleGlanceStyle();
      }
    }
  }
#else
  (void)styleWas;
  (void)styleDownAt;
#endif
}

static void handleInput() {
  pollButtons();
  UiGesture g = consumeGesture();
  if (g == GESTURE_TAP) {
    if (page == PAGE_GLANCE) {
      GlanceHit hit;
      if (gestureTapHit && glanceHitAt(touchStartX, touchStartY, &hit)) {
        if (hit.kind == HIT_MODE) {
          toggleHomeMode();
        } else if (!pageEnabled(hit.target)) {
          drawStatus("disabled in Sources", COL_AMBER);
          delay(200);
          drawDashboard();
        } else {
          goToPage(hit.target, "tap");
        }
      }
    } else {
      goHome();
    }
  } else if (g == GESTURE_HOME) {
    if (page == PAGE_GLANCE) forceSyncFromDesk();
    else goHome();
  } else if (g == GESTURE_STYLE) {
    toggleGlanceStyle();
  }
}

// A board reset briefly tears down USB-Serial/JTAG while the host bridge is
// reopening it. One request in that window is not evidence that the host is
// down, so give the first boot a few short, bounded retries before falling
// through to the Wi-Fi path.
static const uint8_t BOOT_FETCH_ATTEMPTS = 3;
static const uint32_t BOOT_FETCH_RETRY_MS = 650;

static bool bootFetchUsb() {
  for (uint8_t attempt = 0; attempt < BOOT_FETCH_ATTEMPTS; attempt++) {
    if (attempt > 0) {
      bootProgress("LINK", attempt);
      delay(BOOT_FETCH_RETRY_MS);
    }
    if (fetchUsageUsb(USB_TIMEOUT_SYNC_MS)) return true;
  }
  return false;
}

static bool bootFetch() {
  for (uint8_t attempt = 0; attempt < BOOT_FETCH_ATTEMPTS; attempt++) {
    if (attempt > 0) {
      bootProgress("LINK", attempt);
      delay(BOOT_FETCH_RETRY_MS);
    }
    if (fetchUsage(USB_TIMEOUT_SYNC_MS)) return true;
  }
  return false;
}

void setup() {
  // /usage over CDC is ~30KB; the default 256-byte RX buffer drops it.
  Serial.setRxBufferSize(USB_RX_BUF);
  Serial.begin(115200);
  // Never block the UI forever when the Mac isn't draining CDC TX.
  Serial.setTxTimeoutMs(0);
  delay(1500);   // let native USB-CDC re-enumerate so the boot log isn't lost
  Serial.printf("headroom firmware %s\n", fwVersion().c_str());
  Serial.println("\n=== headroom booting ===");
  wifiProvisioningBegin();

  powerInit();
  pinMode(BTN_BOOT, INPUT_PULLUP);
#if defined(BOARD_WS_AMOLED_216)
  pinMode(BTN_SECONDARY, INPUT_PULLUP);
  pinMode(BTN_STYLE, INPUT);   // active-HIGH via BSS138; no pull-down needed
#endif
#if defined(BOARD_TOUCH_CST92XX)
  bool touchOk = false;   // after panel bring-up — Waveshare order
#else
  bool touchOk = touchInit();
#endif
  homeModeLoad();   // before the first home paint
  glanceStyleLoad();
  effectLoad();
  displayLoad();

  bool pok = panel->begin();
  Serial.printf("panel->begin: %s\n", pok ? "ok" : "FAIL");
#if defined(BOARD_WS_AMOLED_216)
  // Panel is physically mounted 90° from native scan; Arduino_CO5300's
  // setRotation only does X/Y mirror, so write MADCTL (MV+MY) directly.
  bus->beginWrite();
  bus->writeC8D8(0x36, 0xA0);
  bus->endWrite();
  Serial.println("CO5300 MADCTL 0xA0 sent");
#elif !defined(BOARD_DIRECT_CO5300)
  sh8601VendorInit();
  Serial.println("sh8601VendorInit sent");
#endif
  // Wipe GRAM — including the columns past LCD_WIDTH — BEFORE raising
  // brightness. Lighting the panel first shows one frame of power-on garbage,
  // which is the green line that used to flash along the bottom edge.
  panel->fillScreen(COL_BLACK);
  sealNativeEdges(COL_BLACK);
  panel->setBrightness(displayBrightness);

  // Panel already started — skip nested begin inside the canvas.
  bool cok = gfx->begin(GFX_SKIP_OUTPUT_BEGIN);
  Serial.printf("canvas->begin (%dx%d): %s  psram=%d\n",
                scrW(), scrH(), cok ? "ok" : "FAIL", (int)psramFound());

#if defined(BOARD_TOUCH_CST92XX)
  touchOk = touchInit();
#endif

  // Kick STA + known APs before the splash, not after: associating takes about
  // as long as the animation runs, so the show costs ~nothing in time-to-data.
  // Association itself completes in loop().
  connectWifi();

  bootSplash();
  bootLine("CPU", "ESP32-S3", COL_CRT);
  {
    char mem[16];
    snprintf(mem, sizeof mem, "%uKB", (unsigned)(ESP.getFreeHeap() / 1024));
    bootLine("HEAP", mem, COL_CRT);
  }
  bootLine("PSRAM", psramFound() ? "OK" : "FAIL",
           psramFound() ? COL_CRT : COL_RED);
  {
    char disp[20];
    snprintf(disp, sizeof disp, "%dx%d", (int)scrW(), (int)scrH());
    bootLine("DISPLAY", disp, pok && cok ? COL_CRT : COL_RED);
  }
  bootLine("PANEL", pok && cok ? "OK" : "FAIL",
           pok && cok ? COL_CRT : COL_RED);
  bootLine("TOUCH", touchOk ? "OK" : "FAIL",
           touchOk ? COL_CRT : COL_RED);

  {
    const size_t nAp = sizeof(WIFI_NETWORKS) / sizeof(WIFI_NETWORKS[0]);
    char aps[16];
    snprintf(aps, sizeof aps, "%u AP", (unsigned)nAp);
    bootLine("RADIO", aps, COL_CRT);
  }

  {
    int16_t hostY = bootY;
    bootProgress("LINK", 0);
    bool ok = bootFetchUsb();
    for (uint8_t i = 0; ok && !burnHistoryReady() && i < 8; i++) {
      bootProgress("BURN", i);
      delay(400);
      ok = fetchUsageUsb(USB_TIMEOUT_SYNC_MS);
    }
    bootY = hostY;

    if (ok) {
      // Cable answered — skip the Wi-Fi wait; mDNS/OTA come up in loop().
      Serial.println("usage via USB (boot) — Wi-Fi continues in background");
      bootLine("WIFI", "PENDING", COL_CRT_DIM);
      bootLine("HOST", "USB", COL_CRT_DIM);
      bootLine("LINK", "USB", COL_CRT);
      bootLine("USAGE", burnHistoryReady() ? "OK" : "WARM", COL_CRT);
      bootLine("READY", "GO", COL_CRT);
      delay(320);
      drawDashboard();
    } else {
      // No host on CDC — wait for Wi-Fi, then HTTP (USB still a fallback).
      uint32_t t0 = millis();
      uint32_t lastDots = 0;
      uint8_t spin = 0;
      bootProgress("WIFI", spin++);
      while (wifiMulti.run() != WL_CONNECTED && millis() - t0 < 20000) {
        if (millis() - lastDots >= 1000) {
          bootProgress("WIFI", spin++);
          lastDots = millis();
        }
        delay(50);
      }

      if (WiFi.status() == WL_CONNECTED) {
        String ssid = WiFi.SSID();
        bootLine("WIFI", ssid.length() ? ssid.c_str() : "OK", COL_CRT);
        bootLine("IP", WiFi.localIP().toString().c_str(), COL_CRT);
        {
          char rssi[12];
          snprintf(rssi, sizeof rssi, "%d dBm", WiFi.RSSI());
          bootLine("RSSI", rssi, COL_CRT);
        }
        mdnsUp = MDNS.begin(OTA_HOSTNAME);   // needed for MDNS.queryHost()
        bootLine("MDNS", mdnsUp ? OTA_HOSTNAME : "FAIL",
                 mdnsUp ? COL_CRT : COL_RED);
        otaBegin();
        bootLine("OTA", otaUp ? OTA_HOSTNAME : "OFF",
                 otaUp ? COL_CRT : COL_CRT_DIM);
        Serial.printf("wifi ok  ip=%s  ssid=%s  mdns=%d\n",
                      WiFi.localIP().toString().c_str(),
                      WiFi.SSID().c_str(), mdnsUp);
        bootLine("HOST", HOST_NAME, COL_CRT_DIM);
      } else {
        bootLine("WIFI", "FAIL", COL_RED);
        Serial.println("wifi FAILED — trying USB");
        // Which is it: SSID not in range, or in range and the password is
        // wrong? A scan answers that; guessing from "FAIL" does not.
        Serial.println("visible APs:");
        int n = WiFi.scanNetworks();
        for (int i = 0; i < n; i++) {
          bool known = false;
          for (auto &k : WIFI_NETWORKS)
            if (WiFi.SSID(i) == k.ssid) known = true;
          Serial.printf("  %-32s %4d dBm  ch%-3d %s\n", WiFi.SSID(i).c_str(),
                        WiFi.RSSI(i), WiFi.channel(i),
                        known ? "← in config.h" : "");
        }
        if (n <= 0) Serial.println("  (none — antenna or radio problem)");
        WiFi.scanDelete();
        wifiProvisioningStartPortal();
        bootLine("SETUP AP", wifiProvisioningApName(), COL_CRT);
        bootLine("AP PASS", wifiProvisioningApPassword(), COL_CRT);
        bootLine("PORTAL", "192.168.4.1", COL_CRT);
        bootLine("HOST", "USB", COL_CRT_DIM);
      }

      hostY = bootY;
      bootProgress("LINK", 0);
      ok = bootFetch();
      // Host's first publish after restart often has meters but no burndown
      // pts yet. Pull a few more times before parking on "Collecting history".
      for (uint8_t i = 0; ok && !burnHistoryReady() && i < 8; i++) {
        bootProgress("BURN", i);
        delay(400);
        ok = fetchUsage(USB_TIMEOUT_SYNC_MS);
      }
      bootY = hostY;
      const char *linkLabel = ok
          ? (resolvedHost.length() ? resolvedHost.c_str() : "USB")
          : "FAIL";
      bootLine("LINK", linkLabel, ok ? COL_CRT : COL_RED);

      if (ok) {
        // Name the slots rather than a fixed trio — which three they are is
        // the host's call now, and the log is where you check it took.
        String provs;
        for (uint8_t i = 0; i < slotN; i++) {
          if (provs.length()) provs += ' ';
          provs += slots[i].id;
          provs += slots[i].q.ok ? "=1" : "=0";
        }
        if (!provs.length()) provs = "none";
        Serial.printf("host=%s  fetch ok  burn=%d providers=[%s] vercel=%d git=%d local=%d\n",
                      resolvedHost.length() ? resolvedHost.c_str() : "usb",
                      (int)burnHistoryReady(), provs.c_str(),
                      (int)vercelOk, (int)gitOk, (int)localOk);
        bootLine("USAGE", burnHistoryReady() ? "OK" : "WARM", COL_CRT);
        bootLine("READY", "GO", COL_CRT);
        delay(320);
        drawDashboard();
      } else {
        Serial.println("fetch FAILED (server/host unreachable)");
        logNetDiag();
        bootLine("USAGE", "FAIL", COL_RED);
        bootLine("WHY", netErr.length() ? netErr.c_str() : "unknown", COL_RED);
        bootLine("READY", "FAIL", COL_RED);
        delay(400);
        drawNetDiag();
      }
    }
  }
  lastPoll = millis();

  // Last: a wedged I2C bus or HTTP stack should reboot us, not leave a frozen
  // panel on the desk. Registered after setup so a slow first associate can't
  // trip it.
  esp_task_wdt_config_t wdt = {
      .timeout_ms = WDT_TIMEOUT_S * 1000,
      .idle_core_mask = 0,
      .trigger_panic = true,
  };
  esp_task_wdt_reconfigure(&wdt);
  esp_task_wdt_add(NULL);
}

void loop() {
  esp_task_wdt_reset();

  // Input first, every pass — never starve BOOT/touch behind Wi-Fi or USB.
  handleInput();

  // The portal is deliberately non-blocking. USB CDC remains the preferred
  // offline transport while a phone is connected to the setup AP.
  wifiProvisioningLoop();

  if (otaUp) ArduinoOTA.handle();

  // WiFiMulti.run() blocks for seconds while hunting APs. Call it rarely when
  // disconnected; when associated just poke it occasionally. lastWifi==0 means
  // try on the first loop pass (USB-fast boot left associate pending).
  static uint32_t lastWifi = 0;
  const bool wifiUp = WiFi.status() == WL_CONNECTED;
  if (wifiUp) {
    wifiDisconnectedSinceMs = 0;
  } else if (!wifiProvisioningActive()) {
    if (wifiDisconnectedSinceMs == 0) wifiDisconnectedSinceMs = millis();
    // A USB-fast boot does not wait for Wi-Fi. Give association a fair window,
    // then expose the same setup path without taking USB away from the host.
    if (millis() - wifiDisconnectedSinceMs >= 20000) {
      wifiProvisioningStartPortal();
    }
  }

  if (!wifiProvisioningActive()) {
    const uint32_t wifiEvery = wifiUp ? 10000u : 20000u;
    if (lastWifi == 0 || millis() - lastWifi >= wifiEvery) {
      lastWifi = millis();
      uint8_t st = wifiMulti.run();
      if (st == WL_CONNECTED) {
        if (!mdnsUp) mdnsUp = MDNS.begin(OTA_HOSTNAME);
        otaBegin();
      }
    }
  }

  // Animate the collecting-history spinner between polls (partial flush).
  if (!touchDown) tickCollectingSpinner();

  // Background poll — skip while finger is down.
  if (!touchDown && millis() - lastPoll >= pollIntervalMs()) {
    lastPoll = millis();
    if (fetchUsage()) {
      fetchFails = 0;
      // Settings can hide the page on screen; land on the glance rather than
      // keep drawing a page BOOT can no longer reach.
      if (!pageEnabled(page)) page = PAGE_GLANCE;
      drawDashboard();
      if (pendingCelebration) {
        pendingCelebration = false;
        if (displayCelebrate) {
          playResetCelebration(pendingCelebrationAccent);
          drawDashboard();
        }
      }
    } else {
      if (fetchFails < FETCH_BACKOFF_MAX) fetchFails++;
      logNetDiag();
      if (!haveData) drawNetDiag();
    }
  }
  delay(4);
}

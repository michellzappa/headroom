# Headroom rings

The rings are one indicator with the same meaning on macOS, iOS, widgets, the
menu bar, and the desk display.

- A provider uses at most two concentric bands.
- The longer quota window is the outer band (week outside, session inside).
- The provider accent is the usage arc; arcs begin at 12 o'clock and grow
  clockwise.
- The track is the same accent mixed 20% over the surface.
- The usage arc has round ends. The cap is half the band thickness, and the arc
  is pulled in by that much at each end, so the painted arc still covers exactly
  the percentage used. At 100% the two caps meet at 12 o'clock and leave a
  visible `)(` seam — do not collapse that into a solid ring.
- The dot is the expected usage at the current point in time. It rides inside
  the band and is sized off the band thickness, so it is the same dot on inner
  and outer rings. The distance between the arc and the dot shows whether usage
  is ahead of or behind pace. Nothing on the ring labels the dot, so the
  macOS overview ring says this in its tooltip and VoiceOver reads the pace
  after the usage.
- Any line of text that sits under more than one ring names the source and
  window it describes ("Codex weekly: 50% left · resets today 23:00"). A ring's
  own caption names its window and counts down ("Weekly · 3d 6h"); it never
  shows a clock time.
- Missing data draws a track without inventing zero usage.

## The combined dial (Apple Watch)

A watch face has room for one glyph, not three, so the watch spends its bands
on the other axis: **one band per source**, outside-in in pinned Sources order
(first provider outermost), each filled to the pool that binds it. Position
identifies the source the way left-to-right order does on Activity and on the
board. Everything above still holds — accent arc, pace dot, 20% track,
12 o'clock start.

Two things are watch-only, both forced by the `.accented` rendering mode a
complication draws in, where the system flattens the view to a single tint:

- The glyph draws in two passes (`HeadroomRingPass`). Bands go in the
  accentable group, pace dots in the other, so the gap between arc and dot —
  the whole reading — survives being tinted. Stacked in one pass the dot would
  land on an arc of its own colour and disappear.
- Band proportions come from `HeadroomRingProfile.watch`, not the shipped
  ratios. A third band at a third of the diameter needs thicker strokes and
  tighter gaps or it falls under the minimum and is dropped.

## What a band says out loud

Every band carries a `name` beside its number, and the glyph speaks them in the
order the glossary fixes: name, value, state. What the name holds depends on
what the bands are:

- **Pools of one provider** (Mac, iPhone, widgets) — the pool's title, because
  each of those surfaces already names the provider beside the glyph.
- **One band per provider** (the watch's combined dial) — the provider's full
  `title`, `Claude · Work`. Speech has no mark to lean on, so the `label` the
  face draws would leave three accounts all saying "Work".

The name is separate from the layer's `id` for exactly that reason: an id is
`claude:work`, which VoiceOver reads as "claude colon work". The widget cache
carries both, and a cache written before it had a name falls back to the id —
where an older app happened to leave the title anyway.

The watch app itself renders full colour and keeps the brand hues; only the
complications go monochrome.

`Shared/HeadroomRings.swift` is the Swift implementation. The constants beside
`drawPaceRing` in `firmware/src/main.cpp` mirror it for the embedded display.

The desk display can also drop the rings for the menu bar's Pace glyph — one
pill per provider, a line at even spend, a mark, no arc
([docs/esp32.md](esp32.md)).
That is a different indicator, not a ring variant: it spends the whole glyph on
the arc-to-dot distance and says nothing about how much is gone. Nothing above
applies to it, and `Shared/MenuBarIconStyle.swift` owns its curve.
The app icon is the same glyph — three bands in process CMY (yellow / cyan /
magenta), each at 90 percent outside-in, carrying pace dots at 30 / 60 / 90,
rendered by `scripts/render_icon.py` into both asset catalogs and the App Store
PNG. Live rings keep each provider's brand accent; the icon uses CMY so the arcs
stay distinct at small sizes.

The Mac catalog gets that glyph on Apple's icon grid: an 824-of-1024 rounded
square with transparent margins, no baked shadow. macOS masks nothing for you,
so a full-bleed square stays a square in a Dock full of rounded tiles. iPhone,
Watch and the App Store PNG stay square and opaque — those are masked by the
system, and App Store Connect rejects alpha.

`AboutAppIcon.imageset` on Mac and iPhone is a fifth copy of that same
full-bleed artwork, because an appiconset is not loadable as a named image and a
menu-bar `LSUIElement` app has no usable `applicationIconImage`. The About
window and the macOS welcome screen draw it and round the corner themselves.
`render_icon.py` writes it too — it was hand-copied once and sat a redesign
behind, which nothing catches, since it only shows in a window nobody opens
while iterating on the icon.

User-facing names for charts and sections live in [`docs/glossary.md`](glossary.md).

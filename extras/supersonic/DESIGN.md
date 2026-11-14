# Supersonic native design contract

## Research log
- Reference: installed `omarchy.media/BarWidget.qml`, `qs.Ui.BarWidget`, `PopupCard`, `Button`, `TextField`, `SearchableDropdown`, and `qs.Commons.Style/Color/Border` (read only).
- Keep stock media click/scroll mapping and popup rhythm. SearchableDropdown's local-only filter cannot query a library; use the same native TextField + bounded ListView pattern in the existing PopupCard instead, with explicit async search.
- No new palette/font lookup: this is a bounded native extension, not a visual redesign. Installed theme is authoritative, not a generated dashboard palette.

## Atmosphere & identity
Native, compact, understated. Audience: Omarchy users controlling their already-running Supersonic library. Signature: album/year metadata and song/album search in the media popup, not a second application window.

## Color
Use `bar.barForeground` on the bar; `Color.popups.text/background/border` in the popup; `Color.accent` and `Style.controlFill`/native Button focus states for interaction. No fixed hex colors, gradients, or bespoke semantic palette. Error/absent states are explicit text, not color alone. Theme contrast is inherited; contrast measurement remains live acceptance work.

## Typography
Use `bar.fontFamily` / `Style.font.family` and `Style.font.body`, `bodySmall`, `caption`, `subtitle`. PlainText only for all arbitrary metadata. The track/artist/album/year and elapsed/total bar summary uses its full natural width up to 1000 theme units by default (`maxLabelWidth` setting, bounded 120–1600), capped to 60% of the actual bar width on narrow displays. Popup rows elide at available width. CJK uses installed Qt fallback; full metadata available via bar tooltip. No marquee or custom tracking.

## Spacing & layout
Use Style.space(4/6/8/10/14) and native control spacing. Popup desired width 400 theme units, height capped by PopupCard screen fitting; header owns metadata/actions/search, ListView owns remaining scroll. Vertical bars show glyph only with the same tooltip/dropdown. Result cap 40. No artwork fetches or server URLs.

## Components & states
- BarWidget: completely hidden with zero layout footprint while Supersonic is absent, loading, timed out or otherwise unreachable. No unavailable/error text or neutral placeholder glyph on the bar. Background status polling continues so a successful ready/idle observation restores it automatically; an open popup closes when availability is lost. Running-but-idle remains usable for library search. Observed MPRIS play/pause glyph is neutral only when playback state is unknown for an available app. Exact player address and track ID must match before timing/state is shown. Left/middle/scroll controls are disabled while a command is in flight.
- PopupCard: native placement, dismissal, border, padding and opacity animation. Header contains track, artist, album and explicit year unknown. Buttons: previous, play/pause, next and optional All/Songs/Albums result filters (All initially selected), Retry search.
- Native TextField: one **Search music…** query covering native song/album/artist matching, 300ms debounce, at least two non-whitespace characters; Enter searches immediately. All combines both native endpoints with fair interleaving, typed IDs and a shared 40-row cap. Loading/empty/partial/error text is distinct; no unsupported regex promise or filtering a partial cache as a complete library. Query/filter changes immediately invalidate old results; obsolete completions never replace current results.
- Result rows: title and artist/album/year; native hover/focus fill, Tab focus, Enter/Space activates actual ID. Selection sends IPC play-track or play-album with first track zero and shuffle false. Popup stays open with honest command receipt (not playback proof).

## Motion & interaction
Stock click map: left play/pause, middle next, right popup; positive vertical wheel previous, negative next. Popup stock transition only; no custom motion, scrolling animation or decorative easing. Keyboard focus on search on open; Escape closes; Tab/Enter/Space operate controls. Reduced-motion behavior is inherited from native primitives, no additional animation.

## Depth & surface
PopupCard's BorderSurface only. No shadows, extra card nesting, blur changes or broad visual redesign.

## Accessibility & accepted debt
Use plain-text metadata, visible status, elision and explicit keyboard row activation. Live keyboard/focus, small-screen/high-DPI fitting, CJK clipping and theme contrast are not proven by headless QML. IPC does not expose playing/paused; never infer it from current_track. Upstream play endpoints acknowledge before confirming playback and discard backend play errors: label the receipt as requested, never verified playing. Year is looked up by exact track ID in IPC search; missing/truncated/unavailable matches show unknown, never a guessed album year.

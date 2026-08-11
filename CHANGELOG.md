# Changelog

All notable changes to Lightsaber are logged here. This project is **pre-1.0
and under active development** — expect breaking changes to scene JSON shape
and APIs between minor versions until a 1.0 release.

## [Unreleased]

### Fixed — mesh seams on any shape below full opacity
- A distorted shape draws as a ~72-triangle mesh, each triangle inflated by
  1.5px so its neighbours can't leave gaps. At full opacity that overlap is
  invisible (opaque pixels just redraw the same colour), but at **any**
  opacity below 1 the band was alpha-composited two to four times and showed
  as a bright grid across the shape. Measured on flat grey: at 50% opacity
  the body read 64 while the seams reached **125**, nearly double; at 25% the
  seams were over 3x too bright.
- Same bug and same fix as the vector renderer already had (ARCHITECTURE.md's
  "per-primitive opacity blended BEFORE compositing double-counts anti-seam
  overlap"): the mesh is now drawn at full opacity into an off-screen buffer
  and that finished layer is blended in **once**. Affects media, webcam and
  text shapes, in the editor and the output windows.
- The parallelogram fast path is untouched — it's a single `drawImage` with
  nothing to overlap, so it keeps applying opacity directly.
- Verified: the shape body is now flat to within 1-2 levels of rounding at
  25%, 50%, 80% and 100%, at exactly the expected brightness, with clipping
  and the editor's zoom/pan transform still correct.
- **Cost**: roughly +0.15ms per translucent distorted layer at 1080p — one
  extra full-canvas clear and blit. Against the ~8.8ms a distorted 1080p
  video layer already costs, that's a couple of percent, and it's paid only
  by shapes actually below full opacity.
- Restricting the clear/blit to the shape's own bounding box was tried and
  **rejected**: an interleaved A/B measured it slower in 3 of 4 cases (a
  GPU-accelerated whole-canvas clear and blit beats a sub-rect `drawImage`
  plus the device-space maths), so the simpler version stayed.

### Changed — "Knockout" is now "Cutout", and it's an Input source (42)
- It was reachable only as a *fill type* on an existing shape, which is
  backwards: it isn't a fill, it's a kind of shape you add. It has its own
  entry on the Input rail now, alongside video, webcam, text and scenes.
- Renamed to **Cutout**. Not "mask": this app already calls a shape's own
  silhouette a mask (the Mask/clipping pane), and two different things under
  one word is worse than an unfamiliar one. "Cutout" says what it does —
  a hole in the output, for killing light on a window or a doorway.
- **The stored `source_type` is still `"knockout"`**, so existing canvases
  load unchanged. Only the label moved.

### Added
- **"Draw custom polygon" is its own button** (43) rather than the last row
  of the clip-shape dropdown. Drawing a mask by hand is a different kind of
  action from picking a preset, and it was buried. The button toggles, so
  it's both the way in and the way out.
- **"Keep it true" on preset clip shapes** (44) — a preset is sized from the
  shape's bounding box, so on a 2:1 shape "circle" came out an ellipse. Ticked,
  the smaller half-axis is used for both and the shape is true. New per-shape
  `clip_uniform`; absent on older canvases, which read as off.
- **Hide shape borders** in the canvas toolbar (`square` icon, off by
  default) — hides shape outlines, corner pins and mask handles for a clean
  look at the picture. The **selected** shape keeps its own chrome, so you
  can still see what you're editing; deselect for a completely clean frame.
  Shapes stay draggable while hidden (hit-testing is independent of
  drawing), which the tooltip says so it isn't a surprise.
- **Z-index badge toggle** in the canvas toolbar (`front` icon, default on) —
  useful while stacking shapes, clutter once the stack is settled. Kept
  independent of the borders toggle, and of the frame guides, so each piece
  of canvas chrome can be dropped on its own.
- **Clicking outside the Input column shuts it** (46), so the canvas gets its
  width back without a trip to the rail. Clicks inside a modal don't count:
  a file or folder picker opened *from* that pane is logically still part of
  it, and collapsing underneath you mid-pick is disorienting.

### Changed — Input column is a rail + one pane
- The Input sources (video/image, webcam, text, 3D scenes) were a stack of
  accordions inside a collapsible column. They're now an always-visible icon
  rail plus a single sliding pane, the same shape as the Panes column, with
  the section's name as the pane heading and the active icon highlighted.
- Clicking the **active** icon shuts the pane, giving the width back to the
  canvas — that's this column's collapse, so the separate collapse/expand
  buttons are gone. 343px open, 103px shut. (Panes deliberately can't close;
  it has no equivalent screen-space job.)

### Added
- **Output frame guides toggle** in the canvas toolbar (`border-outer` icon),
  default on — turns off the dashed crop/overlap guides when they're in the
  way of judging the picture. A view preference, so it's per browser and
  never touches the project.
- **Vertical dividers in the canvas toolbar**, grouping it into document
  actions / view tools / display toggles rather than one undifferentiated
  strip of eight identical buttons.

### Changed
- **Shapes list rows no longer have a delete ✕.** A list you use for
  *selection* shouldn't be able to destroy a shape on a mis-click; Shape
  settings already has an unambiguous Delete button. The padlock moved to the
  row's right edge, where it reads as a status badge rather than a control.

### Added
- **Test pattern per output, on the Output Preview tab** (41) — a checkbox
  under each tile, so throwing the calibration grid on a projector is one
  click from the screen you're already watching while aligning. Same engine
  state as the Project tab's, so ticking either moves both.
  Deliberately kept out of the tile rebuild signature: rebuilding a tile
  recreates its `<iframe>`, which would reload that output's whole page every
  time the grid was toggled.

### Changed
- Shape settings takes the sliders icon; the old Settings pane is now **3D
  Scene Settings** with a 3D icon, since everything in it (motion, hue, scene
  camera) drives the generated scene and is ignored by video/image/webcam/text
  shapes — which the pane now says.
- "Disable Visuals" removed from that pane. `set_visuals_disabled` still
  exists in the engine; nothing is bound to it (Start/Stop covers it).
- Input list reordered — Video/image files first, **3D Scenes** (renamed from
  "Scene library") last, with the collapsed icon rail matching.
- The pane column no longer collapses: clicking the active icon is a no-op
  rather than closing it and leaving a bare strip of icons. Browsers whose
  stored state has every pane closed fall back to Shape settings.

### Fixed
- **"Reset to original polygon" gave every shape the same stretched
  rectangle.** It snapped to a hardcoded quad that renders at 3.16:1 on
  screen regardless of content — the same wrong default that was fixed for
  *new* shapes but never for reset. It now rebuilds from the content's own
  aspect (media, webcam, text 1:1, scene) and keeps the shape where it is,
  since reset means "undo my distortion", not "move it back to the middle".
- **Reset mask** and **Reset distortion** added to the Mask/clipping pane as
  separate buttons, so neither depends on which edit mode is active.

### Changed — Bootstrap Icons throughout (34)
- Every emoji in the UI is now a **Bootstrap Icon** (v1.13.1, MIT), vendored
  as an inline SVG sprite. No CDN, no font file, nothing fetched at runtime.
  Icons inherit `currentColor` and are sized in `em`, so they pick up each
  button's colour and font-size without per-icon rules.
- **A subset, not the whole set**, because the numbers are lopsided: the full
  sprite is 1.1MB and the icon font plus its CSS ~220KB, against ~17KB for
  the 45 icons actually used. An external file isn't an option either —
  cross-document `<use href="icons.svg#id">` doesn't work in Chrome or
  Safari, and `<img>` can't inherit `currentColor`, so icons would render
  black on a dark UI.
- Adding one later is copy-paste: grab the SVG from icons.getbootstrap.com
  and paste it into the sprite as a `<symbol>`. The sprite header says so.
- Three transport controls compared `textContent` against an emoji to decide
  play vs pause; they now carry a `data-playing` flag, which is what made
  replacing the glyph safe.

- Project scaffolding for VSCode / GitHub (this changelog, `.vscode/`, `LICENSE`, `pyproject.toml`)

## [0.36.0]

### Added — projector edge blending
- **Outputs can now overlap and feather into each other** instead of only
  butting edge to edge. `Project > Output monitors > edge overlap` sets the
  width of the shared band as a percentage of one output's width, and each
  output says explicitly which of its own edges (left/right/top/bottom) sit
  in a physical overlap.
- Ticking an edge **widens that output's viewport** by half the band, so the
  two projectors' cones genuinely cover a shared strip that each fades
  across. Feathering inside the authored crop instead would simply dim the
  last few percent of each output and leave a dark stripe down the join.
- The falloff is **smoothstep, chosen because S(t) + S(1-t) = 1** — two
  neighbours' ramps are exact complements, so the shared band comes back to
  full brightness. Verified by measurement, not assumption: summed light
  across the band is flat to within 0.4% (12-stop gradient quantisation and
  8-bit rounding). It's a `multiply` pass on the finished frame, because this
  models light adding between two projectors, not transparency.
- Which edges blend is **explicit, never inferred** from whether two
  viewports happen to abut — projectors don't always sit edge to edge, and
  guessing wrong fades content to black against nothing.
- The editor and Output Preview show the overlap as a **shaded guide band**
  over the existing crop guides rather than the real ramp; the editor shows
  the whole uncropped canvas, so drawing the actual falloff there would dim
  a stripe across the middle of what you're composing. The output windows
  themselves render the real blend.
- Defaults to 0 (no blending, hard cuts), so every existing project is
  unchanged. The field only appears once display viewports are on, since
  duplicate feeds have no seam to blend.
- The data model mirrors `renderhost`'s `OutputSpec.left_overlap` /
  `right_overlap`, so a project configured here carries over to the render
  host unchanged (ARCHITECTURE.md §5).

### Fixed
- **Output Preview tiles are shaped like the output they preview.** Every
  tile was built at the full canvas aspect regardless of crops, so on a
  2048×768 canvas split between two projectors each tile was drawn 2.67:1
  while the slice inside it is 1024×768 (1.33:1) — the content came out
  letterboxed inside a box the wrong shape, with roughly half of each tile
  black. Tiles now take their own output's aspect, follow `duplicate_of`,
  and widen with the edge-overlap expansion, so a tile matches what that
  output actually renders. Measured: content now fills each tile to within
  1px. Tiles may differ from each other, since outputs can have different
  crops.
- Each tile is labelled with the source pixels it's fed (`Output 1 ·
  1024×768`) — the number to check against the projector's native mode.

## [0.35.0]

### Fixed — shapes spanning two projectors (40)
- **`applyKeystone` clamped coordinates to [-1,1].** Clamping a *coordinate*
  crops nothing; it collapses off-screen geometry onto the edge, corrupting
  the shape it belongs to. Worst on the parallelogram fast path in
  `drawMediaWarped`, where the destination affine comes from just three
  corners — clamp one and the whole texture is squeezed into the visible
  area. A text shape crossing the seam therefore drew the **entire word on
  both outputs at different scales** instead of one half on each. Measured
  before the fix: output 1 placed the quad's top-right at x=1024 (the window
  edge) where the true value was ~1506.
- This affected **every** shape crossing a seam, not only text — media was
  less obvious because the 7×7 mesh path spreads the error across many
  points rather than three. It also became much easier to hit once corners
  were allowed past the canvas edge (19).

### Canvas tab (37/38/39)
- **Output crop guides.** A dim dashed outline plus a number marks each
  projector's slice of the canvas, so a shape sitting across a physical join
  is visible while composing rather than discovered on the wall. Only drawn
  when viewports are enabled and there's more than one output.
- **The Shapes list stopped overflowing its column.** The shared `.lib` grid
  uses `minmax(170px, 1fr)`, wider than the 140px the column has been since
  it was narrowed (23), so rows ran 30px past the gutter and over the rail's
  divider — the "overlapping line". The list is one column now.
- The rail has its own heading, so its icons sit on the same baseline as the
  Shapes rows and the Shape settings fields instead of ~30px above them, and
  the divider no longer runs up alongside the other columns' titles.
- **The "Shape settings" heading no longer disappears** when nothing is
  selected. It lived inside the fields group, which is hidden in that state,
  so the column lost its title while its neighbour kept one; it now sits
  outside the group with an empty-state hint, matching "Shapes".

### Output frame rate (36)
- **Painting is decoupled from delivery.** Output windows painted straight
  out of `onmessage`, so a burst of queued messages — exactly what a busy
  window gets when its socket backlog flushes — became a burst of
  full-canvas repaints of near-identical frames. That worsened the stall,
  which grew the next backlog: falling behind was self-reinforcing. Each
  window now keeps only the newest state, paints on `requestAnimationFrame`,
  and never faster than the project's frame rate; stale frames are dropped
  rather than drawn.
- **The frame-rate overlay was lying.** It was an EMA of the *instantaneous*
  rate (`1000/dt`), which is convex and so biased upward by jitter: two
  messages delivered 1ms apart inject 100 into the average in a single step.
  Modelled, 10% coalesced frames made a true 33.6fps read as **141**. It read
  highest exactly when the window was struggling, because struggling is what
  makes delivery bursty. Now a straight count of paints in the last second.
- **One slow client no longer holds up the others.** The broadcaster awaited
  each client in turn, so with two output windows the busier one decided
  when the other got its frame — and the other then received several at
  once. Sends now go out concurrently. Measured at 60fps with two windows:
  61.5 and 50.7 paints/s, up from 34.9 and 44.7.
- `--fps` is clamped to 1..120 like the other two paths that set it; it was
  the one way in that skipped the clamp, so `--fps 240` really did drive the
  render loop and broadcaster at 240Hz.
- Fixed a 404 on every output window: it asked for `lightsaber.svg`, which
  doesn't exist (the file is `lightsaber.png`).
- **Frame rate is now a dropdown, not a free number.** Since output windows
  paint on `requestAnimationFrame`, the only rates a browser can actually
  hit are refresh/n — on a 60Hz display 60, 30, 20, 15, 10. Asking for 50
  silently gave 30, which is precisely the "fps isn't doing what I set"
  confusion this removes. The list is built from the display's *measured*
  refresh rather than assuming 60, so a 50Hz projector offers 50/25/10 and a
  120Hz panel offers more; a refresh that divides badly (144, 165) falls
  back to the usual rates with a note that they'll be approximate. A project
  saved at a rate this display can't hit keeps its value as a flagged extra
  option instead of being snapped to a neighbour on next save.

### Media tab fixes
- **Images preview again.** A still was handed to the `<video>` element,
  which renders black — so a JPEG looked like a broken file. Stills now get
  their own `<img>`, and the trim controls hide rather than offering an
  in/out and a "duration unknown" that will never resolve. (30)
- **"Upload a copy…" actually uploads.** It used to switch to the Canvas
  tab and `focus()` a file input inside two closed accordions, which opens
  no picker and looks like nothing happened. It now uploads from the Media
  tab and refreshes both lists. (31)
- **The trim bars scrub.** Pressing anywhere that isn't an in/out handle now
  drags the playhead continuously, like any other transport bar; before, it
  seeked once on mousedown and ignored the rest of the gesture. Cursor
  reflects which gesture you'll get. (29)

### Media folders (was "media roots")
- Renamed and rewritten in plain language: folders on this computer that
  Lightsaber may read from, without copying. (32)
- **Added a folder picker.** The OS dialog can't be used for this — browsers
  deliberately withhold absolute paths from web pages, so
  `showDirectoryPicker`/`webkitdirectory` yield a bare folder name and no
  way to locate it on disk. The picker browses the *server's* folders
  instead, via a new `/fs/dirs` route (directories only). This grants no
  access that `POST /media/roots` didn't already allow.
- Documented why folders are per computer rather than per project: projects
  store the short name, not the path, which is exactly what lets a project
  move to the render host and still find its media.

### Canvas
- **Shift-drag a corner scales the whole shape** about its centre, keeping
  any corner-pin distortion intact. (28)
- **A new webcam shape takes the camera's native aspect** rather than a 16:9
  guess. (26)
- More separation between the Shapes list and the icon rail, which were
  touching. (27)

### Added
- **Escape closes any open modal**, and clicking the dimmed backdrop does
  too. Works off `.modal-backdrop.open`, so modals added later are covered
  without maintaining a list. (33)

## [0.34.0]

### Canvas tab layout
- **The drawer rail is icon-only and has moved** to sit immediately after the
  Shapes list rather than at the far right of the row, so a handle now sits
  directly against the pane it opens instead of being stranded on the other
  side of a wide content panel. The words survive as tooltips and as each
  pane's own heading. (24)
- **Playback is its own pane** — in/out points, loop mode, speed, offset and
  the trim scrubber, split out of Shape settings, which had grown into one
  long scroll mixing "what is this shape" with "how does its clip play". The
  pane says why it's empty for anything that isn't a video. (21)
- **Mask / clipping is its own pane**, taking the clip shape, clip size and
  the geometry-edit toggle with it — that toggle picks which geometry the
  canvas handles edit, so it belongs beside the mask rather than beside the
  fill type. **"Warp" is now "Distort"** throughout. (22)
- Shapes list narrowed by 30% (200px → 140px); the width was coming straight
  out of the canvas's own share of the row. (23)

### Fixed
- **New shapes are no longer stretched.** The default quad's half-height was
  computed as `0.5 / aspect`, which multiplies the project's own aspect
  distortion instead of cancelling it — a new shape on a 1920x1080 project
  came out **3.16:1 rather than 16:9**. Text and media looked worst, having
  a definite shape of their own to disagree with. (18)
- **A shape now takes its content's aspect** once that content reports one:
  a clip's true ratio, or 1:1 for text. Applied only while the quad is still
  the untouched default — anything placed by hand is left alone. (18)
- **Corners can be dragged past the canvas edge** (up to one canvas-width).
  Clamping them to exactly the boundary made it impossible to push a
  *distorted* shape flush against an edge, because the corner you need to
  move is the one already sitting on the limit. Content is now clipped to
  the artboard in the editor, matching what the output actually shows, while
  outlines and handles stay visible outside it. (19)
- The editor no longer sits on an empty inspector: opening a canvas, or
  deleting whatever was selected, lands on the first shape with Shape
  settings open. Clicking bare canvas to deselect still works. (20)

### Added
- **About modal** — a `?` in the header, rendering `about.md` from the repo
  root plus the running version, so the blurb has one home rather than being
  duplicated into markup that drifts. Served by a new `/about` route. (25)

## [0.33.1]

### Fixed — video playback performance
- **Output windows no longer decode and warp video they can't see.** With
  viewports enabled, the crop is applied as a coordinate remap, so a shape
  belonging to another projector still ran the whole decode-and-mesh-warp
  path and was discarded only by the canvas edge. Each window now tests a
  shape's corner quad against its own bounds first, and skips the media
  element entirely — so the existing eviction sweep releases the decoder and
  the file genuinely stops decoding in windows that don't show it.
  Measured on a 4-output rig with 10 videos across the canvas: worst-case
  frame **113.3ms → 23.8ms**, layers drawn per output 10 → 3.5.
- **No more corrective seek on every loop of a full-length clip.** A clip
  whose trim spans the whole file wraps by itself via `<video loop>`, so by
  the time the server's playhead wraps the element is already in position;
  seeking there stalled decode for 0.5–1.2s once per loop to correct
  nothing. A target jump now only triggers a seek if the element didn't
  already follow it, which leaves scrubs, trim changes and genuinely
  trimmed loops working as before. The compositor publishes `media_span`
  and `media_native_loop` for this.
- Per-tick disk reads removed from the render path: `media_meta` is cached
  on the file's mtime+size (49.4µs → 4.11µs) and `media_roots` caches the
  parsed roots table (30.0µs → 0.14µs).

### Docs
- `ARCHITECTURE.md` is tracked in git again, and records the measured cost
  model behind the render-host design: one decode per clip rather than one
  per output is the core argument for the shared-FBO ordering; video bitrate
  turns out to be irrelevant to throughput and decode is not the bottleneck;
  and seeking is the expensive playhead correction, not the cheap one —
  which bears on the planned libmpv integration.

## [0.33.0]

### Linked media — reference files instead of copying them
- **Media roots** (`media_roots.py`) — folders you register as places linked
  media may come from. A shape can now point at a file *where it already
  lives* (`PolygonSpec.media_link`) instead of uploading a copy into the
  app's `media/`, so a multi-gigabyte video costs no extra disk. Register
  with `--media-root NAME=PATH` (persists) or from Settings.
- **This is an allowlist, not a convenience.** The server binds
  `--host 0.0.0.0` by default, so a route serving arbitrary absolute paths
  would be a remote file read for anyone on the same network. Every path
  goes through one `resolve()` boundary: containment is checked after
  `realpath()` (so neither `../` nor a symlink planted inside a root
  escapes), and only media file extensions are served. Verified over HTTP —
  traversal, encoded traversal, symlink escape, unregistered root and
  non-media files all 404; browse escapes 400.
- **Links store `root-name::relative/path`, not an absolute path.** The UI
  shows the full resolved path, but a project moved to another machine (the
  render host, whose media is on its own disk) resolves as long as a root of
  the same name exists there — nothing to relink by hand.
- A link whose file is missing is **kept**, not dropped: an unplugged drive
  mid-set must not have any unrelated edit silently destroy the link. Those
  surface as an amber "file not found — Replace… to relink" instead.
- Linking is now the primary action in Input > video/image files; the
  upload-a-copy flow is still there, demoted.

### Video playback, per instance
- **Playhead is derived from the canvas clock, not the `<video>` element.**
  `composite.py` computes each media shape's position and publishes it per
  layer; every client converges on that value rather than free-running.
  Generated scenes were already pure functions of `t` — video now is too.
  Two independent output windows measured **1–5 ms apart**, so a clip
  spanning two projectors through a viewport crop holds together. Freeze and
  Stop halt video with everything else, for free.
- **Per-instance trim and mode** — `media_in`/`media_out`/`media_mode`/
  `media_rate`/`media_offset` on PolygonSpec. The same file can appear
  several times on a canvas, and across canvases, each with its own trim and
  playhead. in/out default to `null` = inherit the asset's own default trim
  (`media_meta.py`), so re-trimming a file moves every shape that hasn't
  overridden it. `offset` phases one instance against another.
- Modes are loop / once / once-and-hold. **No ping-pong**: browsers have no
  reverse playback, and faking it with a backward seek per frame stutters
  badly on exactly the long files this is for.
- The `<video>` cache is keyed by asset **plus playback config**, so shapes
  trimmed identically share one decoder and stay frame-identical, while
  differing trims get their own playhead — bounding decoder count to
  distinct configurations rather than shape count. Orphaned elements (every
  trim edit mints a new key) are evicted after ~3s unused; without that,
  nudging an in-point a few times left several videos decoding forever.
- Trim UI is built for long files: typed `HH:MM:SS.mmm` fields are the real
  control (a 30-minute clip is ~6s per pixel in a 300px bar), with
  set-from-playhead, ±1s nudges, and a region/playhead bar for orientation.

### Media tab
- A new top-level tab between Canvas and Sequencer: every file the project
  can draw from, linked and uploaded together, with duration, size, usage
  count and a missing flag. The inventory is the union of the upload folder,
  every `media_link` across the project's canvases, and anything with stored
  metadata — so a link whose last shape was just deleted still appears
  rather than having its trim silently orphaned.
- **Two-tier trim timeline** — a whole-file overview (where am I in 30
  minutes) plus a zoomed band around the playhead (2s–10min) for placing an
  edge precisely. Drag a region edge to move it, click elsewhere to seek.
  The preview player is deliberately not tied to the engine clock: scrubbing
  here must not move what the projectors are showing.
- **Used by** lists every shape across the project's saved canvases, click
  to open that canvas. **Relink** repoints every shape using an asset at a
  different file — rewriting saved canvases on disk, since a relink that
  fixed 1 of 12 shapes would be worse than none — and carries the trim over.
  It refuses a target outside any media root.

### Editing and UI
- **Per-project frame rate** (Project > Performance) — paces the render loop
  *and* the state broadcaster together, applied live. `--fps` is now just
  the startup default a loaded project overrides.
- **Shape lock** (`PolygonSpec.locked`) — a locked shape can't be selected
  or dragged on the canvas, so finished mapping can't be nudged out of
  alignment mid-set. Clicks pass through to shapes underneath; it stays
  reachable from the Shapes list (which is how you unlock it), and effectors
  still animate it. Dashed outline, padlock in the list.
- **Sticky unsaved-changes bar**, bottom-right — names what's unsaved and
  offers the relevant Save, visible from every tab. The inline indicator was
  invisible precisely when you'd most likely lose work.
- Project tab split into collapsible sections (Output size / Performance /
  Output monitors / Notes); collapsing the Input column now collapses its
  accordions too; whole shape-list rows are clickable; "+ Add shape" moved
  below the list it adds to; tool buttons are icon-only.

## [0.32.0]

### Custom polygon masks, with Warp/Mask editing
- **Custom polygon mask** — a new `custom` option on a shape's clip shape,
  backed by `PolygonSpec.clip_points`: an editable N-point silhouette that a
  media/scene/webcam/text shape's content is hard-clipped to, *in addition
  to* its four corner pins. Points are stored in the quad's own UV space and
  mapped through the same homography as the content, so distorting the
  corner pins warps the mask and the content together as one object — the
  same model as an After Effects mask (layer space) under a Corner Pin
  effect. The preset clip shapes stay bounding-box based and unchanged: a
  circle clip is still a circle however hard the quad is keystoned.
- **Warp / Mask edit modes** — the two geometries both want click-and-drag
  on the same pixels, so they're modal rather than simultaneously live, with
  the inactive one's handles drawn demoted rather than hidden (the
  object-mode/edit-mode split Blender, Illustrator and MadMapper all use).
  Mask mode reuses the knockout point editor's gestures: drag a point,
  double-click an edge to insert, double-click a point to remove, drag
  inside to move the whole mask; `Esc` returns to Warp. Editor tool state
  only — never persisted to the project.
- Picking `custom` seeds the mask with the quad's own outline (a deliberate
  visual no-op) and switches straight to Mask mode, so there is always
  something visible to drag. "Reset to original polygon" follows the active
  mode and relabels itself accordingly.
- Internal: the identical three-line clip preamble repeated at all eight
  content-draw call sites across `index.html`/`output.html` is now one
  `polyClipPath`/`polyClipActive` pair, which also guards the degenerate
  case that would otherwise clip a shape away to nothing.

### Fixed
- `--fps` had no observable effect: `server.py`'s `broadcaster()` pushed
  state to every websocket client at a hardcoded ~20Hz regardless of the
  setting, silently capping the frame rate every browser window actually
  saw, even though the compositor's own render loop already respected
  `--fps` correctly. Now paced from `engine.composite.fps` (~55 of a 60fps
  target, measured).
- Viewport-cropped outputs were stretched/distorted: `paintComposite`
  letterboxed against the *full* canvas aspect rather than the cropped
  slice's own, so e.g. a 2048×768 canvas split into two half-width outputs
  fitted the whole wide aspect into each window before remapping within it.

## [0.31.0]

### Projects, pixel-accurate canvas resolution, shape masking
- **Projects** (`projects.py`) — the new base-level container. Each project
  owns its own canvas library and sequencer document (`projects/<name>/`);
  scenes and uploaded media stay global/shared across every project. New UI
  panel (open/save/save-as/delete) at the top of column 2, above Canvas.
- **Canvas resolution is now a literal pixel width/height** (two number
  inputs) instead of a picked-from-a-list projector preset — groundwork
  toward `ARCHITECTURE.md`'s eventual multi-projector output layer, which
  treats canvas dimensions as authored/derived data, not a fixed mode.
- **Shape masking** — circle/hexagon/triangle/square, with a 0-50px
  feathered (soft) edge, in the new Shape settings block. Implemented
  client-side (both the canvas editor and the monitor output windows) via a
  CSS-blur-filtered mask shape composited with `source-in`, since Canvas 2D's
  own `clip()` is hard-edged only.
- **Media upload widget** (Input > Local video/image files) — plain HTTP
  upload/list/delete under `media/`. Storage/labelling only for now; shapes
  can be assigned a media source but rendering it is a later step.

### Laser/DAC hardware path removed
The physical Helios DAC output, and the single-scene render loop that used
to feed both it and (until this same pass) a since-removed single-scene
browser preview, are gone: `lightsaber/output/` (the whole package),
`--laser`/`--pps`/`--max-step`/`--invert-x`/`--keystone-h`/`--keystone-v`,
laser keystone + test-pattern calibration, crossfade, and the global (not
per-shape) hue-override/glow/trail/mirror/disable-plane controls and the
Modulation accordion — all of it existed only to serve the single-scene
loop, which had no remaining consumer. `Engine` is now a thin coordinator
(scene/project libraries + the Claude director) wired into
`CompositeRenderer`, which is the only render loop left in the app and now
also owns the master Start/Stop gate, the "Disable Visuals" fade
(`master_gain`, applied client-side), and the perf-diagnostics instrumentation
that used to live on the old loop. Clicking a scene-library row now assigns
it to the selected shape (or adds a new shape with it) instead of loading a
now-nonexistent active scene. `SceneManager` lost its "current scene"
concept entirely and is a pure on-disk library, same as `CanvasManager`.

## [0.30.0]

### Performance (the headline of this release)
- **Vectorized the 3D camera projection path** (`scene3d.py`) — was a
  per-point Python loop (visibility test, clip, project) for every stroke,
  every frame; profiled as the single largest render-loop cost for
  stroke-rich scenes. Rewrote the whole pipeline (visibility mask, run-
  finding, projection, and a new exact off-screen-stroke skip — scenes with
  more geometry than fits the camera's view at any moment were paying full
  clip cost for strokes that render nothing) as batched numpy plus plain
  Python for the genuinely small per-run work, where plain `min()`/`max()`
  benchmarked ~40% faster than numpy for arrays this size. Verified against
  the *unvectorized* version across 540 randomized trials (all camera
  modes × depth modes, varied geometry/near-far crossings/LOD) — 0
  mismatches — then measured: one representative scene went from **47ms to
  17ms per frame**; a stroke-dense scene from 26ms to ~17-24ms depending on
  its own `max_strokes` headroom.
- **`World` generator was rebuilding static geometry from scratch every
  frame** (`generators/world.py`) — every primitive node (planet, ring,
  ball, torus, crystal, starfield) was re-run through raw numpy/trig/RNG on
  every tick even though none of them (except the genuinely time-animated
  jellyfish) depend on time at all. Now cached per (primitive, params), like
  the def-based shapes already were. Forest trees had the same problem worse
  — full RNG-driven regeneration (trunk + branches) every frame just to
  apply a small sway offset; now the static layout is built once and only
  the sway translation runs per frame.
- **Audio DSP** (`audio/dsp.py`) pad/osc voice rendering was a Python
  triple-nested loop calling a tiny numpy op once per (chord note × detune
  layer × partial) — up to ~64 separate calls per voice per callback.
  Batched into one vectorized call per voice; verified numerically
  identical to the old loop across 200 randomized trials (max diff ~6e-7,
  pure float32 rounding).
- **Soundscape crossfades no longer double the audio DSP cost.** The old
  crossfade rendered the outgoing *and* incoming soundscape simultaneously
  for the whole fade — correct-sounding, but literally 2x the per-callback
  work, measured pushing a several-voice scene's callback over 300% of
  budget. Replaced with a sequential fade-out → swap → fade-in: only one
  soundscape is ever rendered at a time, and the swap lands exactly at the
  silent point between the two halves so there's no audible click. Verified
  the sequenced fade has no clipping and a clean crossover.
- Output with no laser attached was still doing the full DAC point-planning
  pass (arc-length resample + coordinate transform) every tick purely to
  feed the UI's point counter (~8ms on a mid-size scene). Now recomputed
  every 6th tick instead of every tick (~7Hz refresh on a text counter is
  plenty) — measured output cost drop from ~9ms to ~1.5ms average.
- The scene library listing (`SceneManager.names()`) did a fresh
  `os.listdir()` + sort on every ~20Hz state broadcast regardless of
  whether anything changed. Now cached, invalidated only on save/delete.
- **Fixed a real bug that could freeze the browser video preview whenever
  audio was struggling**, independent of the engine's own render loop:
  `synth.vu()` (the VU meter) was called from the websocket broadcaster
  thread and *blocked* waiting for the same lock the audio callback holds
  for its entire render — so a slow audio callback (which we'd just found
  several real causes of) froze every connected browser's preview for
  however long that render took. Made `vu()` non-blocking: it tries the
  lock and falls back to the last known reading on contention, since a
  meter reading one tick stale is imperceptible.
- Diagnostics themselves had overhead: `statistics.mean()` in the perf/audio
  summaries is ~100x slower than a plain `sum()/len()` for lists this size
  (benchmarked ~500us vs ~5us per call) for no precision benefit worth
  having — fixed, and it was on the same broadcaster thread already
  contending with the render/audio threads for the GIL.

### Diagnostics
- Added a render-loop performance monitor (`promptwaver/perf.py`) mirroring
  the existing audio `CallbackStats`: per-tick render/output timing, dropped-
  tick tracking, and — specifically — whether a drop happened *during a
  scene crossfade*, so "does it lag right when scenes fade" is something you
  can read off real numbers instead of guessing. New **Performance**
  accordion (sidebar) surfaces it; a lightweight always-on FPS counter (under
  the visualiser) works independent of the fuller instrumentation.
- Diagnostics (both the render-loop monitor above and the audio callback
  stats) are now **off by default** — a small but real cost (instrumentation
  timing calls, ~2.5% of a frame measured) most sessions don't need paying
  for. Toggle live in **Settings > Diagnostics**, or launch with `--diag`; no
  relaunch needed either way. When off, the Audio diagnostics/Performance
  blocks hide entirely rather than sitting open empty; the FPS counter keeps
  working regardless, since it's tracked separately and is effectively free.
- Audio diagnostics now also tags whether a slow/underrun callback happened
  during a soundscape crossfade, for the same "is it the fade" question on
  the audio side.

### New: monitor filters, disable scene plane, keystone, dual outputs
- Added **glow**, **trails**, and **mirror** (x/y, reflects one half over the
  centre line) as monitor-only canvas effects — screen/display only, never
  touch the vector data sent to the laser. These, plus **Disable scene
  plane**, are now **per-scene settings**: saved into the scene's own JSON
  via the existing "Save Camera settings" / "Save all scene settings"
  buttons, loaded back with whatever scene set them, off/0 by default for
  every scene that hasn't (including every scene that predates this).
- Added **Disable scene plane** (Camera controls): hides floor/ground/grid/
  plane-named geometry from a 3D scene, applied in `World.render3d` before
  the frame is even built — affects the laser and every display identically,
  not just a preview overlay. Matched by a small regex against the node's
  authored name (not its content), with a deliberate guard so "plane" doesn't
  also match "planet" (a very common primitive). Claude's scene-authoring
  prompt now asks it to name floor/backdrop shapes accordingly so future
  generations reliably work with this.
- Added a **second output window** (header: Output 1 / Output 2) — same live
  feed, independently flippable (whole-image reverse, distinct from the
  "mirror" effect above) and independently keystone-corrected, for driving
  two screens/projectors from one session.
- Added **live keystone correction** (Settings > Keystone): the laser's
  keystone (previously `--keystone-h/-v`, launch-only) is now adjustable
  while watching the beam, with the main visualiser mirroring it live so it
  can be tuned without the laser on. Output 1 and Output 2 each get their
  own independent keystone too (display-only, physical-screen concern, nothing
  sent anywhere). A **test pattern** toggle (border, diagonals, crosshair,
  inner box) overrides the live scene everywhere at once — laser, visualiser,
  both output windows — for calibrating against a known shape instead of
  whatever a scene happens to show. Verified the browser-side keystone
  formula is bit-for-bit identical to the DAC-side one for the same inputs.

### Safety / control
- Replaced the **LASER BLANK** button with an independent **Start/Stop
  Laser** toggle, off by default regardless of `--laser`: visuals, audio,
  and the browser preview all run normally while the physical beam stays
  dark until explicitly armed — useful while composing/previewing a scene
  before it's safe to send to the rig. NullOutput (no hardware) is
  unaffected either way, so the preview/point-counter stay accurate with
  nothing to protect.
- **Start** now fades audio in over 1 second instead of snapping straight to
  full level (a real pop/click before). **Stop** stays instant — the
  safety-critical direction must not lag behind the click.
- Fixed the audio blocksize/latency dropdown snapping back to the old value
  right after clicking Apply (or even before, on some browsers) — the
  broadcaster's next state update would overwrite the user's pick before the
  request had actually landed, since the activeElement-based guard used
  elsewhere doesn't reliably hold focus on a `<select>` after picking an
  option. Fixed with an explicit dirty/pending flag instead. Also fixed a
  related false negative: the server used to guess a fixed 300ms wait for a
  reconfigure to land, reporting "failed to (re)start" if a slower device
  reopen took longer than that even though it went on to succeed.

### UI
- The scene-transition indicator is now always visible (previously popped
  in/out of the layout on every scene switch, shoving the crossfade/audio-
  fade fields around) and sits directly above the crossfade field.
- The under-visualiser readout is now just the FPS counter.
- The vertical master fader is 35% shorter.

## [0.22.0]
- Added **per-voice "swell"** (`promptwaver/audio/dsp.py`) — a slow, continuous
  level modulation layered on top of each voice's own level, independent of
  the ADSR envelope (which only fires once on trigger/mute). Each voice gets
  its own random phase and period so voices swell in and out *independently*
  rather than in lockstep — the difference between "breathing" and
  "arranged". Off by default (`swell_amount=0`, byte-identical to before);
  new `swell`/`swell period` knobs in the Soundscape mixer for live control.
  Verified live on a real Claude-generated scene, real audio hardware: output
  peak measurably varied 3x (0.06-0.18) over a 30s window with swell active.
- Added **character sliders** (Generate modal): cold↔warm, calm↔energetic,
  static↔evolving, styled like the preference sliders on AI character
  generators. Warmth/energy are soft prompt hints appended to the Claude
  system prompt (same mechanism as effort/scene-size) — biasing voice-type
  mix, tone, attack time, and tempo; centered values (0.5) add no hint at
  all, so the default behaviour is unchanged. **Evolving is a guarantee, not
  a hint** — it sets `swell_amount` deterministically after the response
  comes back (`evolution * 0.6`), so orchestration happens regardless of
  whether Claude's own output would have included it. Verified with two real
  Claude generations at opposite extremes: warm/calm/evolving produced
  mostly pad/sub voices (one pluck accent), tone 0.3, 7-8s attacks, tempo 48,
  swell_amount exactly 0.54; cold/energetic/static produced two arpeggiated
  voices, tone 0.7, tempo 120, swell_amount exactly 0.0.
- Raised the ADSR attack ceiling from 10s to 15s so pad voices can genuinely
  take up to 15 seconds to build, matching the slower end of what the warmth
  slider now asks Claude for.
- Threaded warmth/energy/evolution through the "regenerate audio only" flow
  too, and included all three (plus scene size) in the generation cache key
  so different slider settings for the same keyword produce genuinely
  different, independently-cached results.

## [0.21.2]
- **Removed the "Envelope" section (Swell/Release buttons) — it never did
  anything.** Investigated rather than guessed: the matrix's `"env"` source
  was registered and triggered correctly, but no fallback scene, no shipped
  scene JSON, and no example in the Claude system prompt ever set
  `"source":"env"` on a modulation route, and there was no UI to author one —
  so its sampled value was computed and discarded every frame with zero
  observable effect, for every scene path. Removed the dead backend
  (`Engine.trigger()`/`release()`, the `env` matrix source, the `trigger`/
  `release` websocket messages) along with the UI. Confirmed **Modulation**
  (audio↔visual coupling, per-route depth) and **Scene**'s LFO rate *do* both
  have real, live effect (`audio_level` and `lfo_slow` are used throughout).
  Consolidated Scene's one remaining control (LFO rate — it drives the same
  `lfo_slow` source Modulation's routes act on) into the Modulation accordion
  rather than leaving it as its own near-empty section.
- Fixed Global-section sliders overflowing their column (crossfade/audio
  fade/hue-override values were getting pushed outside the sidecar): range
  and text inputs inside `.field` rows had no `min-width:0`, so flexbox
  respected their native intrinsic width instead of letting them shrink to
  fit a narrow container. Applied generally (not just to Global), so this
  can't recur in any other narrow column.

## [0.21.1]
- Global section: master fader and VU meter are now 25% shorter, with the
  Disable Audio/Visuals buttons moved to their right instead of below — more
  compact.
- Added a global **hue override** (Global section): a checkbox + hue slider
  that recolours every output stroke to one hue, keeping each point's own
  saturation/value so depth cueing and relative brightness still read.
  Pure-stdlib `colorsys` HSV round-trip on the final frame, independent of
  whatever the scene/generator itself chose. Verified visually on a real
  scene: turning it on repaints the whole wireframe a single consistent
  colour (tested red and blue), turning it off restores each object's
  original per-object colour exactly.
- Added a **scene transition indicator** (Global section, above the master
  fader): shows the outgoing/incoming scene names with a glowing gradient bar
  that fills proportionally to crossfade progress — so the crossfade slider's
  duration is now visible, not just felt. `SceneManager.transition_state()`
  exposes the in-flight crossfade's names + 0..1 progress; the indicator
  hides entirely when no transition is running. Verified live: the bar fills
  from 0 to 100% in step with a 6s crossfade and the indicator disappears the
  moment it completes.

## [0.21.0]
- Added a **Global** section (top of the sidecar column) with a vertical
  master fader and vertical VU meter, and independent **Disable Audio** /
  **Disable Visuals** buttons — each gracefully fades over 2s rather than
  cutting instantly, separate from Start/Stop (which stays instant, since
  that's the safety-critical gate). Required real DSP work, not just a UI
  toggle: `Soundscape.set_muted()` now ramps a gain multiplier over the fade
  window instead of snapping a boolean, and "Disable Visuals" dims every
  point's colour toward black each tick (the same per-point technique
  `SceneManager` already uses for a scene crossfade) while audio keeps
  playing normally. Verified live: the VU meter genuinely falls to 0 over the
  audio fade, and the visualiser genuinely dims to black and back over the
  visual fade, independently of each other. The Scene Transitions controls
  (crossfade, audio fade, scene PPS) moved here too, hint text dropped.
- Added a **Scene settings** section under Global: **Save Soundscape
  settings** / **Save Camera settings** / **Save all scene settings** (same
  underlying save, now with independent camera/soundscape flags so each
  button only touches its half of the saved file) and a **Save as…** button
  opening a small modal to save the current live config under a new name.
  Fixed a real, pre-existing bug found while building this: saving used
  `spec.name` (the scene's free-text internal name) as the library file key
  — for the 3 shipped scenes with a name/filename mismatch (see 0.19.0),
  this silently created a *new*, differently-named duplicate file instead of
  updating the one that's actually open, every time "Update scene from
  config" was clicked. Now saves under the tracked `library_name` instead,
  which also self-heals the mismatch (the file's internal name is corrected
  to match its filename on next save). Verified directly: loading
  `painters_studio` and saving no longer creates a `painters studio.json`
  duplicate.
- The bottom-row Scene column is now just the **Generate scene…** button —
  the inline save/update controls moved to the new Scene settings section.
- Header indicators reworked to "Connections — [●] Engine [●] Claude API":
  a new Engine dot tracks the websocket connection itself (previously only
  shown as text), alongside the existing Claude API dot.

## [0.20.0]
- Added a standalone **output window** (`promptwaver/web/static/output.html`,
  served at `/output`) — a chrome-less fullscreen canvas meant to be dragged
  onto a projector or second screen, opened/closed via a new header button.
  It's just another websocket client watching the same broadcast the control
  UI does, so it works identically with or without `--laser`: this is a
  software-only display path, useful for non-laser installations too (an
  ambient visual/data piece projected or shown on a second monitor, no
  hardware DAC required). Connects with `?hq=1`, which the server now honours
  per-connection — the output window gets a much less thinned preview
  (`max_points=6000, stroke_thin=300` vs. the control UI's `400/60`) since
  it's the actual thing being watched, not a status glance; built lazily so
  it costs nothing when no hq client is connected. Auto-reconnects if the
  websocket drops, and letterboxes the scene to whatever window size it's
  dragged/resized to rather than stretching. The laser DAC output path is
  completely unaffected — this is a purely additive second output, not a
  replacement.

## [0.19.0]
- Added a **scene size** option (small/medium/large, Generate modal) — tells
  Claude to spread objects and size the camera for a physically bigger world,
  independent of **effort** (which controls object *count/detail*, not
  distance). Defaults to small, matching prior scene sizing exactly (no hint
  is sent at all for "small" — it reproduces today's behaviour byte-for-byte).
- Generate scene modal now closes itself automatically once a generation
  completes.
- Fixed the ADSR envelope knobs (attack/decay/sustain/release) rendering
  cramped at a fixed 64px instead of full column width like every other
  slider in the mixer.
- Moved Camera/Motion out of the sidecar accordion and directly under the
  visualiser, and moved the crossfade, audio fade, and per-scene PPS controls
  there too (inline, not in an accordion) — grouping everything about *this
  scene's* transition/camera behaviour in one place next to what it affects.
- Trimmed the visualiser readout row to just scene name and point count —
  pps/output/audio/level/director were either redundant with the header's
  new connection dot or with Audio diagnostics.
- Highlighted the currently-loaded scene in the Library grid. Building this
  surfaced a real pre-existing content inconsistency: 3 of the 4 shipped
  example scenes have an internal `name` that doesn't match their filename
  (e.g. `forest_flythrough.json` is internally named "forest flythrough") —
  matching the grid against that free-text name would have silently failed
  to highlight them. Fixed properly rather than patching the content: the
  engine now tracks which library file was actually loaded as its own
  `library_name` state field, independent of the mutable scene name, and the
  UI matches against that instead.
- Bottom row is now 7fr/1fr (was 75%/25%) — Library gets most of the width,
  the Scene actions column is narrower.

## [0.18.0]
- **Fixed real overs during a soundscape crossfade** ("jerky/glitchy", peaking
  up to ~150% on a complex scene): the crossfade mixed two already
  master-scaled, tanh-limited full mixes with an equal-power (sin/cos) curve,
  whose weights sum to ~1.41 at the midpoint — fine for a single continuous
  signal, but for two independently-normalized full mixes with correlated
  peaks it can genuinely sum past 100%. Switched to a linear crossfade
  (weights always sum to exactly 1, so the blend of two bounded signals is
  provably bounded too) plus a safety-clamp on the combined signal, matching
  `Soundscape.render`'s own limiter. Verified on a deliberately complex
  6-voice scene: max peak during the fade dropped from the reported ~150%
  to 51% offline, and 32% across four live scene switches on the running
  server. Also checked whether rendering both scapes at once (double DSP
  cost) could itself cause glitching via a blown render budget — even at the
  smallest blocksize it stays under ~13% of budget, so that wasn't a factor.
  The VU meter also now reflects the true post-blend output during a fade
  (previously it only showed the incoming scape's own already-limited peak,
  which would never have shown the actual overs).
- **Layout**: pages now scroll naturally instead of clipping inside
  fixed-height per-panel scrollboxes — a long Library or several open
  sidecol accordions push the page down (confirmed the "Audio diagnostics"
  device/blocksize controls, previously unreachable, now scroll into view).
  The Soundscape globals row is now a fixed 5-column grid with full-width
  sliders instead of an unpredictable flex-wrap count. "Generate scene" moved
  out of the cramped bottom column into its own modal, opened via a
  **Generate scene…** button. Added a **Save Soundscape** button directly on
  the Soundscape panel (writes the live mix back to the loaded scene, same
  action as Update). The Claude connection indicator moved to the header as a
  red/green dot labelled "API connection"; a new **⚙ Settings** modal (button
  next to the title) now holds the API key/Connection controls and the
  Output (max/scene PPS) fields, out of the always-visible sidecar.

## [0.17.0]
- **Fixed a second, more common cause of "the soundscape doesn't change on a
  new scene"**: clicking a scene in the Library never reached `_install_spec`
  — it called a bespoke `scenes.set_scene(...)` directly, which only
  crossfades the *visuals*. The synth's soundscape, the modulation routes, and
  the PPS ceiling all silently stayed on whatever the previously-loaded scene
  had. `load_scene()` now routes through `_install_spec` like a fresh
  generation does, so all three actually update. Verified directly over the
  websocket: loading three different library scenes in a row now changes the
  reported soundscape every time (previously it wouldn't budge after the
  first scene of the session).
- Added a soundscape crossfade: switching scenes now equal-power fades the
  outgoing soundscape into the incoming one instead of hard-cutting, over a
  new **audio fade** slider (0-16s, Scene panel) independent of the visual
  crossfade duration. Implemented as `SoundscapeMixer` in `dsp.py` — renders
  both the outgoing and incoming `Soundscape` for the fade's duration and
  blends them, pure numpy, no per-sample loop. `fade=0` keeps the previous
  instant-swap behaviour.
- **Reworked the whole layout** to a compact, realtime-oriented arrangement
  tuned for a full-HD fullscreen browser: a fixed 720px top row (20% visualiser
  / 65% Soundscape / 15% Camera+Connection with Output, Audio diagnostics,
  Envelope, Modulation, and Scene collapsed into accordions), and a bottom row
  (75% Library, now a multi-column grid instead of one vertical list / 25%
  Generate scene, which now also carries the "save as" and "Update scene from
  config" controls).

## [0.16.0]
- **Fixed the real cause of "every scene gets the same soundscape"**: the
  `anthropic` package was never installed in the running environment, so the
  director silently fell back to the local keyword-based scene builder on
  *every* generation — and none of those fallback builders ever set a
  soundscape, so the identical hardcoded default got stamped onto every scene
  regardless of keyword. Confirmed directly: `scenes/generated/` (which only
  ever holds genuine Claude output) was completely empty, and the shipped
  library scenes carried byte-identical soundscapes. Also fixed a related
  diagnostic bug that made this hard to notice: the UI reported "no API key"
  for this failure even when a key was saved and the package was the actual
  problem — `SceneDirector` now tracks and surfaces the real reason.
- Added a 3-band EQ (low/mid/high, ±24dB) to the soundscape output stage — a
  per-block frequency-domain gain curve (rFFT → scale bins → irFFT), pure
  numpy like the rest of the DSP core, no new dependency. Live knobs in the
  Soundscape panel; saved into the scene JSON under `soundscape.eq`.
- Added a VU meter with a clipping indicator to the Soundscape panel,
  measuring the actual post-master, post-limiter output — moving the master
  fader (or muting) is directly visible on the meter.
- `requirements.txt` now documents actually-tested versions for the optional
  `sounddevice`/`anthropic` packages and clarifies `pyo` is no longer used
  (dropped for the pure-numpy synth after repeated GCC compile failures).
- Reorganized the main layout: Soundscape now sits beside the visualiser
  instead of below it; Library / Generate scene / Output form a row
  underneath; Output and Audio diagnostics are now collapsed accordions at
  the bottom of that row. Also fixed a layout bug hit while building this —
  the bottom row could get squeezed to near-zero visible height when the
  soundscape panel above it was tall (many voice cards), hiding the Library
  list entirely.
- Added a live Claude "connected" status dot to the Connection panel,
  reflecting the director's actual online state rather than only updating
  after clicking Test.

## [0.15.1]
- Fixed: an explicit blocksize request (e.g. clicking Apply) could silently
  fall back to a smaller size on failure *and persist it*, so the next
  session started from an already-degraded value — a one-way ratchet down
  with no way back up. Startup autodetect still falls back gracefully;
  explicit requests now try only the requested size and honestly report
  failure, restoring the last known-good config instead of substituting.
- Fixed: waveform/arpeggiator-mode `<select>` elements rendered absurdly
  tall — a global `flex:1` meant for horizontal control rows was also
  stretching selects inside column-flex containers.

## [0.15.0]
- **Found the real cause of audio glitching on complex scenes**: `PathPlanner`
  resampled every stroke with a pure per-point Python loop, holding the GIL
  and starving the realtime audio callback thread — confirmed by a user
  report that a *visual* setting (`max_strokes`) measurably affected *audio*
  glitching. Measured 431ms/frame (1941% over a 45fps budget) on a real
  scene. Rewrote fully vectorised (arc-length resampling via `np.interp`,
  one batched coordinate transform per frame instead of per stroke,
  zero-copy ctypes buffer). Result: ~20ms/frame, ~21x.

## [0.14.0]
- ADSR envelopes (attack/decay/sustain/release) for `pad`/`osc` voices,
  replacing a fixed 3s fade-in with no release — muting now fades out
  gracefully instead of cutting instantly. Reuses the existing `Envelope`
  state machine from the visual modulation matrix.
- Fixed a mixer UI bug (from 0.13.0): arpeggiator rate/decay knobs used a
  CSS selector that assumed a wrapper element one level higher than exists,
  so dragging them silently threw a JS error and never sent the change.

## [0.13.0]
- `osc` voice type: unison multi-oscillator with detune spread and an
  optional sub-octave layer, distinct from `pad`'s harmonic-partial warmth.
- Arpeggiator: any `pad`/`osc` voice can step through its chord (up / down /
  up-down / random) instead of sustaining it. Shares the same note-scheduling
  safety caps as `pluck` (see 0.12.0), so it can't reintroduce the same bug.
- Blocksize fallback ladder (first pass — refined in 0.15.1) and honest
  device/error reporting in the Audio Diagnostics panel.

## [0.12.0]
- **Fixed the original DSP-side glitch cause**: the `pluck` voice scheduler
  had no floor on onset spacing and no cap on active notes. A soundscape
  with a high tempo/rate could schedule notes faster than they decay,
  growing unboundedly (reproduced: 1248 active notes, 100ms+ renders against
  a ~93ms budget, within 30 seconds). Fixed with an onset-spacing floor, a
  hard cap on active notes, and centralised range-clamping of every
  soundscape parameter.

## [0.11.x]
- Master Start/Stop toggle and a Blank (immediate beam-off) button — nothing
  animates/plays/draws until Start is clicked; the scene clock freezes
  (not resets) while stopped.
- Fixed: the page was browser-cacheable, so an open tab could silently keep
  running a stale build across updates — `Cache-Control: no-store` added.
- Fixed a serious bug: `sd.default.device` could return a non-JSON-serialisable
  object, and the broadcaster had no per-tick error handling, so the first
  bad tick after a browser connected silently killed the state broadcast for
  the rest of the session (the UI would show "linked" but never update again).

## [0.10.x]
- Audio Diagnostics panel: realtime callback timing, hardware-reported xrun
  (underrun) counts, device enumeration, live device/blocksize/latency
  reconfiguration.
- Fixed a naked `while: pass` spin-wait in the Helios DAC output path
  (GIL-thrashing under load); 8192 made the default blocksize.

## [0.9.0]
- Vectorised the audio delay effect (was a per-sample Python loop in the
  realtime callback — a real glitch source) and tuned default blocksize/latency.
- Audio↔visual modulation mapping: a global "level effect" slider (scales
  all audio-driven visual coupling at once) plus per-route depth sliders.
- Regenerate just the soundscape for an existing scene without touching its visuals.

## [0.8.0]
- AI-generated ambient **soundscape** per scene (pure numpy + sounddevice —
  pyo would not compile on the target machine across two attempts, so the
  synth was built dependency-light by design) with a live mixer UI: master/
  tempo/distortion/delay, per-voice level/waveform/tone/pan/mute.

## [0.5.0]–[0.7.0]
- Model (Haiku/Sonnet/Opus) and effort (low/med/high) controls for scene
  generation; auto-save every generation to the library; "Update scene from
  config" to save live tweaks back into a scene; PPS (points/sec) as both a
  global hardware-ceiling setting (sent to Claude so scenes are authored
  within budget) and a per-scene override; fixed camera drift-mode jerkiness
  (was multiplying absolute time by live-modulated speed — now integrates a
  phase accumulator, matching how orbit mode already worked).

## [0.4.x]
- The core architectural shift: Claude **authors scene geometry directly** as
  a small shape grammar (line/polyline/circle/rect/box/arc/grid/lathe)
  rather than composing from a fixed bucket of primitives — genuinely
  unbounded scene vocabulary. Fixed the director silently falling back to
  local/cached content on truncated or malformed responses.

## [0.2.0]–[0.3.x]
- 3D scenes: a drifting/orbiting camera, near-plane + frame clipping,
  depth cueing (hue and/or hard cull, with a TTL-quantize option for
  on/off-only lasers), and a ready-made low-poly primitive kit (planet,
  ring, starfield, jellyfish, torus, crystal) for prompt-composed worlds.
- In-app Anthropic API key entry with a connection test.

## [0.1.0]
- Initial MVP: procedural flat-pattern generators (flow field, attractor,
  ripples), the modulation matrix (LFOs/envelopes/audio-reactive routing),
  Helios DAC + null output, the browser control surface, and a local
  keyword→scene fallback for offline use.

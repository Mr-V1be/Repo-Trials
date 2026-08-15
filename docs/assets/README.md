# Visual assets

Every file here is a hand-written, self-contained SVG: no external fonts, no embedded
raster images, no scripts, no network requests. They render identically on GitHub, in a
browser, and in offline documentation builds.

| File | Size | Animated | Used for |
| --- | --- | --- | --- |
| `hero.svg` | 1200 x 420 | no | README banner |
| `social-preview.svg` | 1280 x 640 | no | GitHub social preview / `og:image` |
| `terminal-demo.svg` | 1040 x 446 | yes (SMIL, 23.8 s loop) | README "see it run" block |
| `pipeline.svg` | 1200 x 520 | no | README and `docs/architecture.md` mechanism diagram |

## Design language

All four files share one palette and one set of primitives, defined inline in each file's
`<defs>`. Copy them verbatim when adding a new asset.

| Token | Value | Use |
| --- | --- | --- |
| background gradient | `#07101f` -> `#101a35` -> `#17143a` (diagonal) | page/canvas fill |
| accent gradient | `#58e1c1` -> `#8d83ff` | rules, connectors, logo mark |
| teal | `#58e1c1` | evaluator side, pass, `OK` |
| violet | `#8d83ff` (light `#a69eff`) | agent side, `INFO`, evidence |
| red | `#ff8a9b` | fail counts (`0/1`, `0.0%`) |
| card fill / stroke | `#141f38` / `#334466` | all cards |
| terminal screen | `#0b1428` | terminal body |
| text | `#f5f7ff` bright, `#b8c4df` body, `#91a1c1` muted, `#7f8dab` dim | |
| grid | 32 px `<pattern>`, `#91a4ca` at 0.08 opacity | canvas texture |

Headings use `Inter,Segoe UI,Helvetica Neue,Arial,sans-serif`; everything code-like uses
`ui-monospace,SFMono-Regular,Menlo,Consolas,DejaVu Sans Mono,monospace`. Both are system
font stacks — nothing is downloaded, and nothing is converted to outlines, so the text
stays selectable and searchable.

### Two things that will bite you

1. **Never stroke a purely horizontal path with an `objectBoundingBox` gradient.** Such a
   path has a zero-height bounding box, so the gradient degenerates and the line does not
   paint at all. `pipeline.svg` defines a second gradient, `pl-flow`, with
   `gradientUnits="userSpaceOnUse"` for exactly this reason. (`hero.svg` predates that fix
   and its short connector lines are invisible for this reason; only the chevrons show.)
2. **Set `xml:space="preserve"` on any `<text>` whose alignment depends on runs of
   spaces.** SVG collapses whitespace by default, which silently destroys ASCII tables.
   `terminal-demo.svg` sets it on every line.

## `social-preview.svg`

1280 x 640 is GitHub's social preview size and also a clean 2:1 for link unfurls.
Upload it at **Settings -> General -> Social preview**. GitHub and several link
unfurlers crop the edges, so all content sits inside an 80 px safe margin
(x 80..1200, y 80..560) — keep it that way.

Legibility floor: the card is routinely rendered at 320 px wide (a 1:4 downscale). The
wordmark (100 px), the hook (50 px) and the score line (34 px) all survive that; the
`$ python scripts/demo.py` line and the footer are deliberately tertiary. Do not drop the
subtitle below 28 px.

Content comes from the positioning copy and must stay factually true: the score line
(`noop-agent 0/1 -> fix-agent 1/1, delta +100 pp`) and the `about 3.5 s` timing are the
measured output of `python scripts/demo.py`. If the demo's numbers change, change this
file. The `pre-release · v0.1` pill exists so the card cannot oversell the project; keep
it until there is a stable release.

## `terminal-demo.svg`

A looping terminal cast of the documented quickstart, built from pure SMIL. No
JavaScript and no CSS keyframes: GitHub strips `<script>`, and CSS animation inside an
SVG served as an image is unreliable, but `<animate>` on `opacity` is rendered
consistently.

**Structure.** Nine sibling `<g>` frames sit at the same coordinates inside a clip path.
Each carries one `<animate attributeName="opacity" ... repeatCount="indefinite">` with
the same `dur` (the total loop length), differing only in `keyTimes`. Frames accumulate
within a "page" of related commands, then the screen clears for the next page:

| Frame | Shows | Duration |
| --- | --- | --- |
| 1 | `init` | 2.0 s |
| 2 | `init` + `doctor` | 2.8 s |
| 3 | `mine` | 1.8 s |
| 4 | `mine` + `candidates` | 2.6 s |
| 5 | `validate` | 1.8 s |
| 6 | `validate` + `run` (noop) | 1.8 s |
| 7 | `validate` + both runs | 2.2 s |
| 8 | `compare` | 2.8 s |
| 9 | `compare` + `report` | **6.0 s** (the payoff, held longest) |

Total loop: **23.8 s**.

**The keyTimes arithmetic.** With `T` = total, `s` = frame start, `e` = frame end and a
0.18 s cross-fade `f`, a middle frame is:

```
values   = "0;0;1;1;0;0"
keyTimes = "0; s/T; (s+f)/T; (e-f)/T; e/T; 1"
```

The first frame starts visible so the loop has no blank seam at the wrap
(`values="1;1;0;0"`, `keyTimes="0; (e-f)/T; e/T; 1"`), and the last frame's fade-out ends
exactly at `T` (`values="0;0;1;1;0"`, `keyTimes="0; s/T; (s+f)/T; (e-f)/T; 1"`). Every
frame group also carries a static `opacity` attribute matching its value at t=0, so a
renderer that ignores SMIL still shows frame 1 rather than a blank box.

A blinking caret rect inside each frame runs its own 1.1 s `<animate>`; nested opacity
multiplies, so the caret is only visible while its frame is.

**Editing.** The file is plain, readable XML — edit it directly. Constraints to respect:

- Maximum 11 content rows plus the caret line (first baseline 100, line height 26, screen
  clipped at y=392).
- Maximum 92 characters per line. The terminal screen is 984 px wide with 26 px padding,
  and a 15 px monospace glyph advances about 9 px.
- Keep `xml:space="preserve"` and write each `<text>` on a single source line, or the
  `candidates` and `compare` tables will lose their column alignment.
- If you add or retime a frame, recompute **every** frame's `keyTimes` with the formula
  above — they all share one `dur`, so a change to any duration shifts all of them.
- The transcript must stay faithful to real CLI output. The one liberty taken is the
  `candidates` table's `title` column, truncated to 25 characters with an ellipsis so the
  row fits the terminal width.

## `pipeline.svg`

The whole product in one glance: the nine-stage flow plus the trust boundary.

Row 1 is task construction (`01 Git history` -> `05 Validate`); row 2 is sealed
evaluation (`06 Oracle vault` -> `09 Report`). The dashed violet rectangle around
`07 Agent workspace` is the load-bearing element — it marks *everything the agent can
see*. Exactly two arrows cross it, and both are labelled: a base tree export going in, a
working-tree patch coming back out. Everything else — hidden tests, the gold patch, later
history, original commit IDs, remotes — stays outside it, which is what the two caption
lines at the bottom spell out.

Layout is on a fixed grid: content spans x 50..1150; row 1 is five 192 px cards with
35 px gaps; row 2 is 196/196/196/188 px cards with 132 px gaps around the workspace (the
boundary-crossing labels live there, and the dashed rectangle eats 14 px of each side)
and a 60 px gap before the report. If you widen a card, shrink a gap by the same amount
and re-check that no label runs into the dashed rectangle.

Teal cards and the teal left accent bar mean evaluator side; violet means agent side.
That mapping is stated in the legend at the top right — keep the two in sync.

## Verifying a change

There is no build step. After editing, check the XML parses and then look at the result:

```bash
python -c "import xml.dom.minidom,sys;[xml.dom.minidom.parse(p) for p in sys.argv[1:]];print('XML OK')" \
  docs/assets/*.svg

google-chrome --headless --disable-gpu --screenshot=/tmp/pipeline.png \
  --window-size=1200,520 --hide-scrollbars "file://$PWD/docs/assets/pipeline.svg"
```

A headless screenshot of an animated SVG always captures t=0, and
`--virtual-time-budget` does not advance SMIL for a top-level SVG document. To inspect a
later frame of `terminal-demo.svg`, paste it inline into a scratch HTML file and seek:

```html
<svg id="s" ...>...</svg>
<script>
  var s = document.getElementById("s");
  s.pauseAnimations();
  s.setCurrentTime(21);   // seconds into the loop
</script>
```

then screenshot that HTML file instead.

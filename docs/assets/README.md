# Visual assets

Every SVG here is hand-written and self-contained: no external fonts, no embedded raster
images, no scripts, no network requests. They render identically on GitHub, in a browser,
and in offline documentation builds. The two PNGs are the exceptions and are both
generated, never hand-edited: `report-preview.png` is a screenshot of the demo's HTML
report, and `social-preview.png` is a 1:1 raster of `social-preview.svg` (GitHub's social
preview upload does not accept SVG).

| File | Dimensions | Bytes | Animated | Used for |
| --- | --- | --- | --- | --- |
| `hero.svg` | 1200 x 420 | 4,062 | no | README banner and `docs/index.md` |
| `pipeline.svg` | 1200 x 520 | 10,588 | no | README mechanism diagram |
| `terminal-demo.svg` | 1040 x 446 | 16,518 | yes (SMIL, 23.8 s loop) | README "see it run" block |
| `social-preview.svg` | 1280 x 640 | 5,186 | no | Editable source of the social card; not embedded in any page |
| `social-preview.png` | 1280 x 640 | 270,891 | no | The file uploaded at Settings -> General -> Social preview |
| `report-preview.png` | 1400 x 589 | 79,527 | no | README report screenshot |

Byte sizes are recorded so an accidental re-export — a raster editor rewriting an SVG, or a
lossless PNG turning into a 3 MB one — is visible in review. Update the row when you update
the file.

## Design language

The SVGs share one palette and one set of primitives, defined inline in each file's
`<defs>`. Copy them verbatim when adding a new asset. `social-preview.svg` drifts a little
— a 36 px grid at 0.075 opacity, card fill `#121f38` on stroke `#314769`, and a background
gradient ending at `#1a123d` — because it is sized for a standalone card rather than an
in-page figure. Match the table below for anything new.

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
   `gradientUnits="userSpaceOnUse"` for exactly this reason. Two files predate that fix and
   still lose a line to it: `hero.svg`'s short connector lines are invisible, so only the
   chevrons show, and `social-preview.svg`'s full-width divider (`M95 566H1185`, stroked
   with the `objectBoundingBox` `accent` gradient) does not paint at all — the row is pure
   background in `social-preview.png`. The 420 px accent bar just above it survives because
   a `<rect>` has height. Fix either by adding a `userSpaceOnUse` gradient.
2. **Set `xml:space="preserve"` on any `<text>` whose alignment depends on runs of
   spaces.** SVG collapses whitespace by default, which silently destroys ASCII tables.
   `terminal-demo.svg` sets it on every line.

## `social-preview.svg` and `social-preview.png`

1280 x 640 is GitHub's social preview size and also a clean 2:1 for link unfurls. The SVG
is the editable source; the PNG is what you actually upload at **Settings -> General ->
Social preview**, because that form takes PNG, JPG, or GIF and not SVG. Edit the SVG,
re-render the PNG, and commit both — a PNG that has drifted from its source is worse than
no PNG.

**What it shows.** A wordmark and glyph on the top left, an `OPEN SOURCE · LOCAL-FIRST`
pill on the top right, the hook `SWE-bench for your own codebase.` with the subtitle
`Turn real fixes from Git history into sealed coding-agent evaluations.`, then the
four-stage flow as monospace cards — `01 · GIT HISTORY` -> `02 · VALIDATE`
(`BASE ✓ RED ✕ GOLD ✓`) -> `03 · RUN AGENTS` -> `04 · EVIDENCE` (`pass@k + reports`) —
with the repository slug and `Python 3.11+ · Apache-2.0` in the footer. Teal reads
evaluator side and violet reads agent side, the same mapping `pipeline.svg` uses: cards 01
and 02 are teal-titled, 03 and 04 are violet.

**Real content bounds**, measured off the rendered PNG rather than assumed: bright content
occupies x 88..1225 and y 64..603, so the margins are 88 left, 54 right, 64 top, 36 bottom.
The right margin is set by card 04 and the bottom margin by the footer line. GitHub and
several link unfurlers crop the edges, so nothing new should push past those bounds, and
the footer is the first thing a crop will eat — keep anything load-bearing out of it.

**Known defect, unfixed.** In the committed PNG the `OPEN SOURCE · LOCAL-FIRST` label
overruns its pill on both sides: the text spans roughly x 950..1189 while the rounded
rect spans 958..1182. It is a font-metric overflow, not a layout error, so the exact
overhang depends on which font resolves for `Inter, Segoe UI, sans-serif`. The one-line
fix is to widen the pill and ease the tracking — in `social-preview.svg`, change the
group to `translate(794 8)`, the rect to `width="300"`, and the label to `x="150"`,
`font-size="14"`, `letter-spacing="1.2"`. It was left alone here because the PNG has to
be re-rendered in the same environment that produced the current one, or the wordmark's
weight changes; do both edits together or neither.

**Legibility floor.** The card is routinely rendered at 320 px wide (a 1:4 downscale). The
hook (59 px), the wordmark (58 px), and the card headlines (20 px monospace) survive that;
the 14 px card captions and pill label and the 16 px footer do not, and are tertiary by
design. Do not drop the subtitle below its current 25 px.

**Factual claims on the card.** There are no numbers on it — no score line, no timing, no
star or download count — which is deliberate, because a card cannot be re-verified once it
is cached by an unfurler. `BASE ✓ RED ✕ GOLD ✓` is the validation contract and
`Python 3.11+ · Apache-2.0` matches `pyproject.toml`. Keep it that way: if you add a claim,
it has to stay true for as long as the image is live.

Re-render the PNG after any SVG edit:

```bash
google-chrome --headless --disable-gpu --screenshot=docs/assets/social-preview.png \
  --window-size=1280,640 --hide-scrollbars "file://$PWD/docs/assets/social-preview.svg"
```

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

## `report-preview.png`

A screenshot of the HTML report `repotrials demo` produces, embedded in the project
README. Unlike `social-preview.png` it has no vector source in this directory — the report
template is. When the template changes, regenerate the screenshot rather than editing it.

`repotrials demo` is unreleased and resolves from `main`; on a v0.1.0 checkout the
equivalent is `python scripts/demo.py`.

```bash
repotrials demo --output /tmp/rt-demo
google-chrome --headless --disable-gpu --screenshot=docs/assets/report-preview.png \
  --window-size=1400,589 --hide-scrollbars \
  "file:///tmp/rt-demo/demo-repository/.repotrials/reports/demo/report.html"
```

The task id in the screenshot (`rt_...`) is different on every run; that is expected and
is not a reason to hold off on regenerating.

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

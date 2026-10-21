# Engineering portfolio

Source for **https://cougarstiles.github.io/**

Bradley Alexander Cougar Stiles. Mechatronics, controls and embedded
engineering, with the project management to deliver it.

## How it is built

One file. `index.html` holds the markup and the whole stylesheet, and
`media/` holds the photographs and video it references. There is no
framework, no build step, no bundler and no dependencies. Opening the
file is the same as deploying it.

A few things that are less obvious than they look:

- **Every figure is framed from CSS**, not by editing images. One block
  near the end of the sheet gives each photo its own aspect ratio, crop,
  centre point, rotation and zoom, so reframing is a number change
  rather than a trip through an image editor.
- **One accent hue.** Every teal on the page derives from a single
  `--teal-h`. Light and dark differ only in lightness and a small
  saturation adjustment, so the themes are one design inverted rather
  than two designs.
- **Paper texture is generated, not an image.** An inline SVG
  `feTurbulence` filter, tiled, blended into the background.

## Running it locally

```
python serve.py          # or double-click preview.cmd
```

Serves the folder at `http://localhost:8234/`. Plain Python 3, nothing
to install. Edit `index.html`, save, and the browser reloads itself.

`serve.py` adds three things at serve time that are never written to
disk: a live-reload poller, an Alt+Click jump-to-source handler, and
`data-srcline` attributes for it to target. It also supports HTTP Range,
which `file://` does not and which video scrubbing needs.

`crop.html` is a drag-to-crop helper for picking figure framing values.

## Publishing

```
python check-publish.py     # must exit 0
git add -A && git commit -m "what changed" && git push
```

GitHub Pages serves `main` from the repository root. Deploys land within
about a minute.

`check-publish.py` is the gate. Because `serve.py` injects dev tooling at
serve time, saving the page *from the browser* instead of editing it on
disk would bake that tooling into production. The check looks for those
markers and exits 1 if it finds any.

Replaced images and video need a hard refresh to show up, since their
filenames do not change.

## Browser support

Tested on desktop Chrome, iOS Safari and Android. Notes on what that
took, in case any of it is useful:

- Text sheen uses `background-clip: text` over a gradient.
  `background-attachment: fixed` must not be combined with it: the
  background is anchored to the viewport but the element's box never
  changes, so Android reuses a stale paint and the text draws in the
  wrong place or not at all. The gradient is oversized per element
  instead.
- `-webkit-text-size-adjust: 100%` is set, because Safari otherwise
  scales text per block against viewport width and captions in
  different grid columns stop matching each other.
- Tiled SVG turbulence needs an explicit `filterUnits="userSpaceOnUse"`
  region. At the default region WebKit rasterizes larger and clips back,
  and tile edges that miss a whole device pixel leave unpainted seams.
- Video is H.264 with a poster frame. iOS cannot decode VP9 in MP4, and
  it downgrades `preload` to `none` on cellular, so a clip without a
  poster shows an empty box.

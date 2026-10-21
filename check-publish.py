"""Pre-publish check: make sure no local dev tooling is in the file.

serve.py injects a live-reload poller, an Alt+Click "jump to source"
handler, and data-srcline attributes on every element. None of that is
ever written to disk - but saving the page FROM THE BROWSER would bake
it in. Run this before pushing to GitHub Pages.

    python check-publish.py                 # checks index.html
    python check-publish.py other.html      # checks another file

Exit code 0 = safe to publish. 1 = dev tooling found.
"""
import pathlib, re, sys

TARGET = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "index.html")

# Markers that only ever appear in serve.py's injected output.
MARKERS = {
    "data-srcline":      "per-element source line attributes",
    "__reload":          "live-reload polling endpoint",
    "__goto":            "editor jump endpoint",
    "jump to source":    "Alt+Click hint badge",
    "localhost:8234":    "hardcoded dev server address",
    "127.0.0.1":         "hardcoded loopback address",
}

if not TARGET.exists():
    print("NOT FOUND: %s" % TARGET)
    sys.exit(1)

text = TARGET.read_text("utf-8")
found = [(m, why, text.count(m)) for m, why in MARKERS.items() if m in text]

print("checking %s  (%.1f KB)" % (TARGET.name, TARGET.stat().st_size / 1024))
print()

if found:
    print("DEV TOOLING PRESENT - do not publish this file:")
    for marker, why, n in found:
        print("   %-18s x%-3d %s" % (marker, n, why))
    print()
    print("This usually means the file was saved from the browser instead")
    print("of copied from disk. Re-copy it from the source file.")
    sys.exit(1)

print("clean - no dev tooling found")

# A couple of extra things worth knowing before going public.
warnings = []
if "PLACEHOLDER" in text or "PROVISIONAL" in text:
    warnings.append("placeholder markers still in the source")
if 'href="Stiles_Resume.pdf"' in text and not (TARGET.parent / "Stiles_Resume.pdf").exists():
    warnings.append("resume link points at Stiles_Resume.pdf, which is not beside the page")

if warnings:
    print()
    print("notes (not blockers):")
    for w in warnings:
        print("   - " + w)

sys.exit(0)

"""
serve.py - local preview server with live reload for index.html

Run:   python serve.py
Then:  http://localhost:8234/index.html

Edit index.html in any editor, hit save, and the browser tab reloads
itself. No VS Code extension, no npm, nothing to install.

Why not just double-click the .html:
  - file:// blocks some things and gives no auto-reload
  - video seeking needs HTTP Range support, which this provides

Stop with Ctrl+C, or close the window.
"""

import bisect
import http.server
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import urllib.parse
import webbrowser

PORT = 8234
ROOT = os.path.dirname(os.path.abspath(__file__))
PAGE = "index.html"

# Matches an HTML tag, respecting quoted attribute values so a ">" inside
# an attribute doesn't end the match early.
TAG_RE = re.compile(r"""<(/?)([a-zA-Z][a-zA-Z0-9:-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>""")


def inject_src_lines(text):
    """Tag every element with the source line it came from.

    Skips the contents of <style> and <script> so CSS/JS is never touched.
    Only affects what the browser receives - the file on disk is unchanged.
    """
    newlines = [i for i, ch in enumerate(text) if ch == "\n"]
    inserts = []
    skip_until = None

    for m in TAG_RE.finditer(text):
        closing, name = m.group(1), m.group(2).lower()

        if skip_until:
            if closing and name == skip_until:
                skip_until = None
            continue
        if closing:
            continue
        if name in ("style", "script"):
            skip_until = name
            continue

        line = bisect.bisect_right(newlines, m.start()) + 1
        inserts.append((m.start() + 1 + len(name), ' data-srcline="%d"' % line))

    for at, attr in reversed(inserts):
        text = text[:at] + attr + text[at:]
    return text

# Injected into every HTML response. Polls for a change token and
# reloads when it moves. Appended at the end because index.html
# has no </body> tag to inject before.
RELOAD_JS = """
<script>
(function () {
  var current = null;
  function poll() {
    fetch('/__reload', { cache: 'no-store' })
      .then(function (r) { return r.text(); })
      .then(function (token) {
        if (current === null) { current = token; }
        else if (token !== current) { location.reload(); }
      })
      .catch(function () { /* server restarting; keep trying */ });
  }
  setInterval(poll, 600);
  poll();
})();
</script>
"""


# Alt+Click any element -> VS Code opens index.html at that line.
# The HTML equivalent of SyncTeX reverse sync.
SYNC_JS = """
<script>
(function () {
  var hint = document.createElement('div');
  hint.textContent = 'Alt+Click \\u2192 jump to source';
  hint.setAttribute('style', [
    'position:fixed', 'left:12px', 'bottom:12px', 'z-index:99999',
    'font:500 11px/1.4 ui-monospace,Menlo,Consolas,monospace',
    'letter-spacing:.06em', 'text-transform:uppercase',
    'padding:5px 9px', 'border-radius:2px', 'pointer-events:none',
    'color:#DCE9E1', 'background:rgba(14,127,120,.92)',
    'box-shadow:0 1px 6px rgba(0,0,0,.28)',
    'transition:opacity .25s', 'opacity:.62'
  ].join(';'));
  document.body.appendChild(hint);

  function lineFor(el) {
    while (el && el.nodeType === 1) {
      if (el.hasAttribute('data-srcline')) return el.getAttribute('data-srcline');
      el = el.parentElement;
    }
    return null;
  }

  document.addEventListener('click', function (e) {
    if (!e.altKey) return;
    var line = lineFor(e.target);
    if (!line) return;
    e.preventDefault();
    e.stopPropagation();

    var el = e.target;
    var prev = el.style.outline;
    el.style.outline = '2px solid #12938B';
    setTimeout(function () { el.style.outline = prev; }, 700);

    function say(msg, ms) {
      hint.textContent = msg;
      hint.style.opacity = '1';
      setTimeout(function () {
        hint.textContent = 'Alt+Click \\u2192 jump to source';
        hint.style.opacity = '.62';
      }, ms || 1600);
    }

    // Ask the server to drive VS Code. Avoids the browser's vscode://
    // permission prompt entirely, and works inside VS Code's Simple Browser.
    fetch('/__goto?line=' + encodeURIComponent(line), { cache: 'no-store' })
      .then(function (r) {
        if (r.status === 204) { say('line ' + line); }
        else { say('jump failed (' + r.status + ')', 2600); }
      })
      .catch(function () { say('jump failed - server down', 2600); });
  }, true);
})();
</script>
"""


def vscode_exe():
    """Locate the VS Code CLI so the server can drive the editor."""
    found = shutil.which("code") or shutil.which("code.cmd")
    if found:
        return found
    for cand in (
        r"D:\Microsoft VS Code\bin\code.cmd",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd"),
        r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
    ):
        if os.path.isfile(cand):
            return cand
    return None


VSCODE = vscode_exe()


def goto_line(line):
    """Scroll the ALREADY-OPEN index.html in VS Code to the given line.

    --reuse-window is what keeps this from spawning a second window (and
    a second copy of the file) on every Alt+Click. Without it, `code`
    opens a new window each time instead of moving the cursor in the one
    that is already showing the file.
    """
    if not VSCODE:
        return False
    target = os.path.join(ROOT, PAGE)

    # VS Code stores open documents under a URI with a LOWERCASE drive
    # letter and compares those URIs case-sensitively. os.path.abspath
    # hands back "D:\...", so passing it while the editor holds "d:\..."
    # reads as a different document and opens a second tab instead of
    # moving the cursor in the one already on screen.
    if os.name == "nt" and len(target) > 1 and target[1] == ":":
        target = target[0].lower() + target[1:]

    spec = "%s:%s:1" % (target, line)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # no console flash
    try:
        subprocess.Popen(
            '"%s" --reuse-window --goto "%s"' % (VSCODE, spec),
            shell=True,
            creationflags=flags,
        )
        return True
    except OSError:
        return False


def change_token():
    """Max mtime + file count across the page and media. Cheap and good enough."""
    newest = 0.0
    count = 0
    for base, _dirs, files in os.walk(ROOT):
        if os.path.basename(base) == "__pycache__":
            continue
        for name in files:
            if name.endswith((".py", ".pyc")):
                continue
            try:
                newest = max(newest, os.path.getmtime(os.path.join(base, name)))
                count += 1
            except OSError:
                pass
    return "{:.3f}-{}".format(newest, count)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        """Forbid caching on EVERYTHING, not just the HTML.

        Media files get replaced in place under the same filename. The
        default handler serves them with Last-Modified and no cache
        directive, so the browser keeps showing the old picture and the
        swap looks like it silently failed.
        """
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def send_header(self, keyword, value):
        # Drop the handler's own Last-Modified so the browser cannot make
        # a conditional request and get a 304 for a file that changed.
        if keyword.lower() == "last-modified":
            return
        super().send_header(keyword, value)

    def log_message(self, fmt, *args):
        # Quiet: one line per page load is enough, skip every asset.
        if "__reload" in (args[0] if args else ""):
            return
        sys.stderr.write("  %s\n" % (fmt % args))

    def do_GET(self):
        if self.path.startswith("/__goto"):
            # Only this machine may drive the editor - the server is also
            # reachable from the LAN so phones can view the page.
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self.send_response(403)
                self.end_headers()
                return
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            line = (q.get("line") or ["1"])[0]
            ok = goto_line(line) if line.isdigit() else False
            self.send_response(204 if ok else 500)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        if self.path.startswith("/__reload"):
            body = change_token().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        path = self.translate_path(self.path)

        if path.lower().endswith((".html", ".htm")) and os.path.isfile(path):
            self._serve_html(path)
            return

        if os.path.isfile(path) and self.headers.get("Range"):
            self._serve_range(path)
            return

        super().do_GET()

    def _serve_html(self, path):
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        text = inject_src_lines(text)
        text += RELOAD_JS
        text += SYNC_JS.replace("__FILE__", json.dumps(path.replace("\\", "/")))
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_range(self, path):
        """Partial content, so video scrubbing works."""
        size = os.path.getsize(path)
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if not m:
            super().do_GET()
            return

        start_s, end_s = m.group(1), m.group(2)
        if start_s == "":                      # suffix range: last N bytes
            length = int(end_s or 0)
            start = max(0, size - length)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        end = min(end, size - 1)

        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", "bytes */%d" % size)
            self.end_headers()
            return

        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        remaining = end - start + 1
        with open(path, "rb") as fh:
            fh.seek(start)
            while remaining > 0:
                chunk = fh.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return        # browser seeked away; normal
                remaining -= len(chunk)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def lan_ip():
    """This machine's address on the local network, for testing on a phone."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))     # no packets sent; just picks the route
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def main():
    url = "http://localhost:%d/%s" % (PORT, PAGE)
    try:
        # 0.0.0.0 so phones and tablets on the same Wi-Fi can reach it.
        httpd = Server(("0.0.0.0", PORT), Handler)
    except OSError as e:
        print("Could not bind port %d: %s" % (PORT, e))
        print("Something else is using it, or a previous serve.py is still running.")
        return 1

    ip = lan_ip()

    print("")
    print("  Portfolio preview running")
    print("")
    print("  This computer : " + url)
    if ip:
        print("  Phone / tablet: http://%s:%d/%s" % (ip, PORT, PAGE))
        print("                  (same Wi-Fi network)")
    else:
        print("  Phone / tablet: could not determine this machine's LAN address")
    print("")
    print("  Serving: " + ROOT)
    print("  Save index.html and the browser reloads itself.")
    print("  Ctrl+C to stop.")
    print("")

    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

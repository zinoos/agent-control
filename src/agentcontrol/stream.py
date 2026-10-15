"""Live view of an agent desktop: frames straight from the agent's compositor (wlr-screencopy into shared
memory) onto a GTK texture. Captures only while the widget is on screen."""

from __future__ import annotations

import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

try:  # optional: live streams need hypr-computer's screencopy client (bin/* add it to PYTHONPATH if found)
    from hypr_computer.wlvirt import Connection, ScreenCopy, WaylandError  # noqa: E402
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

FORMATS = {
    0: Gdk.MemoryFormat.B8G8R8A8,  # ARGB8888
    1: getattr(Gdk.MemoryFormat, "B8G8R8X8", Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED),  # XRGB8888
}


class StreamView(Gtk.Overlay):
    def __init__(self):
        super().__init__()
        self.add_css_class("stream")
        self.pic = Gtk.Picture(can_shrink=True, hexpand=True, vexpand=True)
        self.pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.set_child(self.pic)

        top = Gtk.Box(spacing=6, halign=Gtk.Align.START, valign=Gtk.Align.START, margin_top=8, margin_start=8)
        self.live = Gtk.Label(label="● LIVE")
        self.live.add_css_class("live-badge")
        self.where = Gtk.Label()
        self.where.add_css_class("stream-chip")
        top.append(self.live)
        top.append(self.where)
        self.add_overlay(top)

        self.ticker = Gtk.Label(xalign=0, ellipsize=3, halign=Gtk.Align.FILL, valign=Gtk.Align.END)
        self.ticker.add_css_class("stream-ticker")
        self.add_overlay(self.ticker)

        self.socket: str | None = None
        self.fps = 30
        self._gen = 0
        self._want = False
        self.connect("map", lambda *_: self._set_running(True))
        self.connect("unmap", lambda *_: self._set_running(False))

    def set_source(self, wl_socket: str | None, where: str = ""):
        self.where.set_label(where)
        self.where.set_visible(bool(where))
        if wl_socket == self.socket:
            return
        self.socket = wl_socket
        self._restart()

    def set_activity(self, live: bool, text: str):
        self.live.set_visible(live)
        self.ticker.set_label(f"▶ {text}" if text else "")
        self.ticker.set_visible(bool(text))

    def _set_running(self, on: bool):
        self._want = on
        self._restart()

    def _restart(self):
        self._gen += 1
        if AVAILABLE and self._want and self.socket:
            threading.Thread(target=self._loop, args=(self._gen, self.socket), daemon=True).start()

    def _loop(self, gen: int, socket: str):
        try:
            conn = Connection(socket)
            sc = ScreenCopy(conn)
        except Exception:
            return
        first = True
        try:
            while gen == self._gen:
                t0 = time.time()
                try:
                    frame = sc.capture(with_damage=not first, timeout=2)
                except WaylandError as ex:
                    if "timed out" in str(ex):
                        continue  # nothing changed on the agent's screen
                    break
                if frame is None:
                    time.sleep(0.2)
                    continue
                first = False
                GLib.idle_add(self._show, gen, frame)
                wait = 1 / max(1, self.fps) - (time.time() - t0)
                if wait > 0:
                    time.sleep(wait)
        finally:
            conn.close()

    def _show(self, gen, frame):
        if gen != self._gen:
            return False
        data, w, h, stride, fmt, _inverted = frame
        tex = Gdk.MemoryTexture.new(w, h, FORMATS.get(fmt, FORMATS[1]), GLib.Bytes.new(data), stride)
        self.pic.set_paintable(tex)
        return False

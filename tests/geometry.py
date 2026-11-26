#!/usr/bin/env python3
"""Fullscreen CRT geometry pattern (optional diagnostic; needs python3-tk).

Shows a border, safe-area rectangles, a centre crosshair, a 40-pixel grid and
a circle whose width is corrected for the CRT's non-square pixels and the
profile's margins, then exits after --seconds (or on any key/click).

Run it as the desktop user on the CRT, not as root, for example over SSH:
    DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" tests/geometry.py --seconds 60

Use --print to show the computed circle size without opening a window.

The circle is an approximation: it assumes the full 720-pixel active line
fills the 4:3 picture (as mpv --monitoraspect=4:3 does). Strict BT.601
geometry puts 4:3 in ~704 of the 720 pixels; pass --par to try other pixel
aspect ratios. A photograph of the screen is not a precision measurement.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))

from rpicrt import profile as profile_mod  # noqa: E402


def circle_size(p, diameter_lines=240, par=None, use_margins=True):
    """Return (width_px, height_px, framebuffer_pixel_aspect)."""
    w, h = p.h[0], p.v[0]
    aw, ah = p.physical_aspect
    raster_par = par if par is not None else (aw / float(ah)) / (w / float(h))
    if use_margins:
        sx = (w - p.margin_left - p.margin_right) / float(w)
        sy = (h - p.margin_top - p.margin_bottom) / float(h)
    else:
        sx = sy = 1.0
    fb_par = raster_par * sx / sy
    return int(round(diameter_lines / fb_par)), diameter_lines, fb_par


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default="sony-trinitron-lontium")
    ap.add_argument("--seconds", type=int, default=60, help="auto-exit after this many seconds (default 60)")
    ap.add_argument("--diameter", type=int, default=240, help="circle height in framebuffer lines (default 240)")
    ap.add_argument("--par", type=float, help="override the raster pixel aspect ratio (default from PHYSICAL_ASPECT)")
    ap.add_argument("--no-margin-correction", action="store_true",
                    help="ignore margins (reproduces the historical 270x240 circle)")
    ap.add_argument("--print", action="store_true", help="print the computed geometry and exit")
    args = ap.parse_args(argv)

    p = profile_mod.load(os.path.dirname(HERE), args.profile)
    cw, ch, fb_par = circle_size(p, args.diameter, args.par, not args.no_margin_correction)
    info = "%s  L%d R%d T%d B%d  circle %dx%d (pixel aspect %.4f)" % (
        p.mode_name, p.margin_left, p.margin_right, p.margin_top, p.margin_bottom, cw, ch, fb_par)
    if args.print:
        print(info)
        return 0
    if os.geteuid() == 0:
        print("geometry.py: run as the desktop user, not root", file=sys.stderr)
        return 2
    try:
        import tkinter as tk
    except ImportError:
        print("geometry.py: python3-tk is not installed (sudo apt install python3-tk)", file=sys.stderr)
        return 2

    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.configure(background="black", cursor="none")
    W, H = root.winfo_screenwidth(), root.winfo_screenheight()
    c = tk.Canvas(root, width=W, height=H, background="black", highlightthickness=0)
    c.pack()
    for x in range(0, W, 40):
        c.create_line(x, 0, x, H, fill="#555555")
    for y in range(0, H, 40):
        c.create_line(0, y, W, y, fill="#555555")
    c.create_rectangle(0, 0, W - 1, H - 1, outline="white", width=2)
    for frac, colour in ((0.05, "#00c000"), (0.10, "#c0c000")):  # action-safe / title-safe
        c.create_rectangle(W * frac, H * frac, W * (1 - frac), H * (1 - frac), outline=colour)
    c.create_line(W / 2, 0, W / 2, H, fill="red")
    c.create_line(0, H / 2, W, H / 2, fill="red")
    c.create_oval(W / 2 - cw / 2.0, H / 2 - ch / 2.0, W / 2 + cw / 2.0, H / 2 + ch / 2.0, outline="cyan", width=2)
    c.create_text(W / 2, H * 0.15, text=info, fill="white")
    c.create_text(W / 2, H * 0.85, text="%dx%d framebuffer - any key exits - auto exit %ds" % (W, H, args.seconds),
                  fill="white")
    root.bind("<Key>", lambda e: root.destroy())
    root.bind("<Button>", lambda e: root.destroy())
    root.after(args.seconds * 1000, root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

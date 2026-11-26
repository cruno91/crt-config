"""Parsers for runtime diagnostics (xrandr --verbose, DRM debugfs state)."""

import re

_OUTPUT_HDR = re.compile(r"^(\S+) (connected|disconnected|unknown connection)\b(.*)$")
_MODE_HDR = re.compile(r"^\s{2}(\S+) \((0x[0-9a-f]+)\)\s+([0-9.]+)MHz(.*)$")
_H_LINE = re.compile(r"^\s+h: width\s+(\d+) start\s+(\d+) end\s+(\d+) total\s+(\d+) skew\s+(\d+) clock\s+([0-9.]+)KHz")
_V_LINE = re.compile(r"^\s+v: height\s+(\d+) start\s+(\d+) end\s+(\d+) total\s+(\d+)\s+clock\s+([0-9.]+)Hz")
_PROP = re.compile(r"^\t([^\t:][^:]*):\s?(.*?)\s*$")


def parse_xrandr_verbose(text):
    """Return {output: {...}} from ``xrandr --verbose``."""
    outputs = {}
    cur = None
    mode = None
    for line in text.splitlines():
        m = _OUTPUT_HDR.match(line)
        if m:
            rest = m.group(3)
            cur_id = re.search(r"\((0x[0-9a-f]+)\)", rest)
            geom = re.search(r"(\d+x\d+\+\d+\+\d+)", rest)
            cur = {
                "name": m.group(1),
                "connected": m.group(2) == "connected",
                "current_mode_id": cur_id.group(1) if cur_id else None,
                "geometry": geom.group(1) if geom else None,
                "props": {},
                "modes": [],
            }
            outputs[cur["name"]] = cur
            mode = None
            continue
        if line.startswith("Screen "):
            cur, mode = None, None
            continue
        if cur is None:
            continue
        m = _MODE_HDR.match(line)
        if m:
            tail = m.group(4)
            mode = {
                "name": m.group(1),
                "id": m.group(2),
                "clock_mhz": float(m.group(3)),
                "flags": [t for t in tail.split() if not t.startswith("*") and not t.startswith("+")],
                "current": "*current" in tail,
                "preferred": "+preferred" in tail,
            }
            cur["modes"].append(mode)
            continue
        m = _H_LINE.match(line)
        if m and mode is not None:
            mode["h"] = tuple(int(m.group(i)) for i in range(1, 5))
            mode["hskew"] = int(m.group(5))
            continue
        m = _V_LINE.match(line)
        if m and mode is not None:
            mode["v"] = tuple(int(m.group(i)) for i in range(1, 5))
            mode["refresh_hz"] = float(m.group(5))
            continue
        m = _PROP.match(line)
        if m:
            cur["props"][m.group(1).strip()] = m.group(2).strip()
            mode = None
    return outputs


_CRTC_HDR = re.compile(r"^crtc\[(\d+)\]: (\S+)")
_CONN_HDR = re.compile(r"^connector\[(\d+)\]: (\S+)")
_OTHER_HDR = re.compile(r"^(plane|encoder|bridge)\[")
_MODE_LINE = re.compile(
    r'^\s*mode:\s*"([^"]*)":\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)')


def parse_drm_state(text):
    """Parse /sys/kernel/debug/dri/N/state into crtcs and connectors."""
    crtcs, connectors = {}, {}
    cur = None
    for line in text.splitlines():
        m = _CRTC_HDR.match(line)
        if m:
            cur = crtcs.setdefault(m.group(2), {"id": int(m.group(1)), "name": m.group(2)})
            continue
        m = _CONN_HDR.match(line)
        if m:
            cur = connectors.setdefault(m.group(2), {"id": int(m.group(1)), "name": m.group(2)})
            continue
        if _OTHER_HDR.match(line):
            cur = None
            continue
        if cur is None:
            continue
        m = _MODE_LINE.match(line)
        if m:
            cur["mode"] = {
                "name": m.group(1),
                "vrefresh": int(m.group(2)),
                "clock": int(m.group(3)),
                "h": tuple(int(m.group(i)) for i in range(4, 8)),
                "v": tuple(int(m.group(i)) for i in range(8, 12)),
                "type": int(m.group(12), 16),
                "flags": int(m.group(13), 16),
            }
            continue
        m = re.match(r"^\s+([a-z_]+)=(.*)$", line)
        if m:
            cur[m.group(1)] = m.group(2).strip()
    return {"crtcs": crtcs, "connectors": connectors}


def active_crtc_for(state, connector):
    """Return (crtc dict or None, reason)."""
    conn = state["connectors"].get(connector)
    if conn is None:
        return None, "connector %s not present in DRM state" % connector
    crtc_name = conn.get("crtc")
    if not crtc_name or crtc_name in ("(null)", "0"):
        return None, "connector %s is not attached to a CRTC" % connector
    crtc = state["crtcs"].get(crtc_name)
    if crtc is None:
        return None, "connector %s points at unknown CRTC %s" % (connector, crtc_name)
    if crtc.get("enable") != "1" or crtc.get("active") != "1":
        return None, "CRTC %s for %s is not enabled and active (enable=%s active=%s)" % (
            crtc_name, connector, crtc.get("enable"), crtc.get("active"))
    return crtc, "ok"

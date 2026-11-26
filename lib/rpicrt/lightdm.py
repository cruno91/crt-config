"""LightDM configuration: effective-value resolution and a minimal edit.

LightDM merges configuration from drop-in directories first and the main
``/etc/lightdm/lightdm.conf`` last, so a value in the main file's
``[Seat:*]`` section overrides drop-ins. Seat-specific sections such as ``[Seat:seat0]``
override ``[Seat:*]``. ``lightdm --show-config`` is authoritative when
available; the file model here is used for planning and offline tests.
"""

import fnmatch
import os
import re


class LightDMError(Exception):
    pass


MAIN_CONF = "/etc/lightdm/lightdm.conf"
DROPIN_DIRS = (
    "/usr/share/lightdm/lightdm.conf.d",
    "/usr/local/share/lightdm/lightdm.conf.d",
    "/etc/xdg/lightdm/lightdm.conf.d",
    "/etc/lightdm/lightdm.conf.d",
)
KEY = "display-setup-script"
SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
KV_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=\s*(.*?)\s*$")


def config_files(root):
    """Ordered list of (system_path, text) in LightDM load order."""
    out = []
    for d in DROPIN_DIRS:
        real = root.p(d)
        if os.path.isdir(real):
            for name in sorted(os.listdir(real)):
                if name.endswith(".conf"):
                    path = d + "/" + name
                    out.append((path, root.read_text(path)))
    if root.exists(MAIN_CONF):
        out.append((MAIN_CONF, root.read_text(MAIN_CONF)))
    return out


def parse(text):
    """Yield (section, key, value, line_index)."""
    section = None
    for i, line in enumerate(text.splitlines()):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(";"):
            continue
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            continue
        m = KV_RE.match(line)
        if m and section is not None:
            yield section, m.group(1), m.group(2), i


def _seat_rank(section, seat):
    """0 for generic seat sections, 1 for matching specific ones, None otherwise."""
    if section in ("Seat:*", "SeatDefaults"):
        return 0
    if section.startswith("Seat:"):
        pattern = section[len("Seat:"):]
        if fnmatch.fnmatchcase(seat, pattern):
            return 1
    return None


def effective_seat(files, seat="seat0"):
    """Resolve effective seat keys: {key: (value, source, section)}."""
    generic, specific = {}, {}
    for path, text in files:
        for section, key, value, _ in parse(text):
            rank = _seat_rank(section, seat)
            if rank == 0:
                generic[key] = (value, path, section)
            elif rank == 1:
                specific[key] = (value, path, section)
    result = dict(generic)
    result.update(specific)
    return result


def parse_show_config(output):
    """Parse ``lightdm --show-config`` into {section: {key: value}}."""
    sections = {}
    current = None
    for line in output.splitlines():
        if line.strip() == "Sources:":
            break
        m = SECTION_RE.match(line)
        if m:
            current = sections.setdefault(m.group(1).strip(), {})
            continue
        if current is None:
            continue
        m = re.match(r"^\s*(?:[A-Z]{1,3}\s+)?([A-Za-z0-9_-]+)=(.*?)\s*$", line)
        if m:
            current[m.group(1)] = m.group(2)
    return sections


def effective_from_show_config(sections, seat="seat0"):
    result = {}
    for name in ("SeatDefaults", "Seat:*"):
        result.update(sections.get(name, {}))
    for name, values in sections.items():
        if _seat_rank(name, seat) == 1:
            result.update(values)
    return result


def _section_bounds(lines, name):
    """Return list of (start, end) line index ranges for sections called ``name``."""
    bounds = []
    start = None
    for i, line in enumerate(lines):
        m = SECTION_RE.match(line)
        if m:
            if start is not None:
                bounds.append((start, i))
                start = None
            if m.group(1).strip() == name:
                start = i
    if start is not None:
        bounds.append((start, len(lines)))
    return bounds


def plan_install(text, script_path, previous=None):
    """Point the main file's [Seat:*] display-setup-script at ``script_path``.

    Returns (new_text, record). ``record['prior_line']`` is the original line
    (or None) so that uninstall can restore it exactly.
    """
    want = "%s=%s" % (KEY, script_path)
    lines = text.splitlines(True)
    bounds = _section_bounds(lines, "Seat:*")
    if len(bounds) > 1:
        raise LightDMError("%s contains %d [Seat:*] sections; merge them manually first" % (MAIN_CONF, len(bounds)))
    if not bounds and _section_bounds(lines, "SeatDefaults"):
        raise LightDMError("%s uses the legacy [SeatDefaults] section; convert it to [Seat:*] first" % MAIN_CONF)

    if not bounds:
        prefix = "" if (not text or text.endswith("\n\n")) else ("\n" if text.endswith("\n") else "\n\n")
        block = prefix + "[Seat:*]\n" + want + "\n"
        record = {"installed_line": want, "prior_line": None, "added_section": True}
        if previous:
            record["prior_line"] = previous.get("prior_line")
            record["added_section"] = previous.get("added_section", True)
        return text + block, record

    start, end = bounds[0]
    active = []
    commented = []
    for i in range(start + 1, end):
        s = lines[i].strip()
        if re.match(r"^%s\s*=" % re.escape(KEY), s):
            active.append(i)
        elif re.match(r"^[#;]\s*%s\s*=" % re.escape(KEY), s):
            commented.append(i)
    if len(active) > 1:
        raise LightDMError("[Seat:*] sets %s %d times; fix it manually first" % (KEY, len(active)))

    record = {"installed_line": want, "prior_line": None, "added_section": False}
    if active:
        i = active[0]
        current = lines[i].rstrip("\r\n").strip()
        record["prior_line"] = current
        if current != want:
            nl = "\n" if lines[i].endswith("\n") else ""
            lines[i] = want + nl
    else:
        insert_at = commented[-1] + 1 if commented else start + 1
        if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
            lines[insert_at - 1] += "\n"
        lines.insert(insert_at, want + "\n")
    if previous:
        record["prior_line"] = previous.get("prior_line")
        record["added_section"] = previous.get("added_section", False)
    return "".join(lines), record


def plan_uninstall(text, record):
    """Restore the prior [Seat:*] line. Returns (new_text, conflicts)."""
    lines = text.splitlines(True)
    want = record["installed_line"]
    matches = []
    for start, end in _section_bounds(lines, "Seat:*"):
        for i in range(start + 1, end):
            if lines[i].rstrip("\r\n").strip() == want:
                matches.append((i, start, end))
    if not matches:
        prior = record.get("prior_line")
        if prior is not None and any(l.rstrip("\r\n").strip() == prior for l in lines):
            return text, []
        if prior is None and not any(l.strip() == want for l in lines):
            # Our line is gone; check nobody else points at a different hook.
            return text, []
        return text, ["%s no longer contains %r in [Seat:*]; left unchanged" % (MAIN_CONF, want)]
    if len(matches) > 1:
        return text, ["%s contains %r more than once; left unchanged" % (MAIN_CONF, want)]
    i, start, end = matches[0]
    prior = record.get("prior_line")
    if prior is not None:
        nl = "\n" if lines[i].endswith("\n") else ""
        lines[i] = prior + nl
        return "".join(lines), []
    del lines[i]
    if record.get("added_section"):
        # Remove the header we appended if the section is now empty.
        body = [l for l in lines[start + 1: end - 1] if l.strip()]
        if not body:
            del lines[start]
            # Drop the blank separator we added before the header.
            if start > 0 and start - 1 < len(lines) and not lines[start - 1].strip():
                del lines[start - 1]
    return "".join(lines), []


def session_kind(root, name):
    """Return 'x11', 'wayland', 'both' or None for a session desktop name."""
    x = root.exists("/usr/share/xsessions/%s.desktop" % name)
    w = root.exists("/usr/share/wayland-sessions/%s.desktop" % name)
    if x and w:
        return "both"
    if x:
        return "x11"
    if w:
        return "wayland"
    return None

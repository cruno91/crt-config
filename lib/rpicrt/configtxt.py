"""Inspection and minimal editing of /boot/firmware/config.txt.

The only edit this project makes is adding the ``noaudio`` parameter to the
single active ``dtoverlay=vc4-kms-v3d...`` line when the HDMI audio policy is
``disabled``. Everything else is inspected and reported, never rewritten.
"""

import re


class ConfigTxtError(Exception):
    pass


VC4_OVERLAY_RE = re.compile(r"^\s*dtoverlay\s*=\s*(vc4-kms-v3d(?:-pi[0-9]+)?)\s*(?:,(.*))?$")
SECTION_RE = re.compile(r"^\s*\[([^\]]*)\]\s*$")

# Model filters look like [pi5], [pi500], [cm5], [pi0w], [pi3+]. Which ones
# apply is derived from the device-tree model string, not from a fixed table.
MODEL_FILTER_RE = re.compile(r"^(pi|cm)[0-9][0-9a-z+]*$")


def model_filters(model):
    """Return the config.txt model filters that apply to a device-tree model."""
    filters = set()
    m = re.match(r"^Raspberry Pi Compute Module (\d+)(S?)\b", model)
    if m:
        return {"pi" + m.group(1), "cm" + m.group(1) + m.group(2).lower()}
    m = re.match(r"^Raspberry Pi Zero( 2)?( W)?\b", model)
    if m:
        filters.add("pi0")
        if m.group(1):
            filters.add("pi02")
        elif m.group(2):
            filters.add("pi0w")
        return filters
    m = re.match(r"^Raspberry Pi (\d)(00)?\b", model)
    if m:
        filters.add("pi" + m.group(1))
        if m.group(2):
            filters.add("pi%s00" % m.group(1))
        if " Plus" in model:
            filters.add("pi%s+" % m.group(1))
        return filters
    if model.startswith("Raspberry Pi Model"):
        filters.add("pi1")
    return filters

LEGACY_VIDEO_KEYS = (
    "hdmi_group", "hdmi_mode", "hdmi_cvt", "hdmi_timings", "hdmi_drive",
    "hdmi_pixel_encoding", "sdtv_mode", "sdtv_aspect", "enable_tvout",
    "hdmi_force_hotplug", "hdmi_ignore_edid", "hdmi_edid_file",
)


def model_filter_applies(name, model):
    if not MODEL_FILTER_RE.match(name):
        return None  # not a model filter
    return name in model_filters(model)


def _scan(lines, model):
    """Yield (index, line, applicable) with applicable True/False/None(unknown)."""
    model_filter = None  # None means no model filter in force
    other_filters = []
    for i, line in enumerate(lines):
        m = SECTION_RE.match(line)
        if m:
            name = m.group(1).strip()
            lname = name.lower()
            if lname == "all":
                model_filter, other_filters = None, []
            elif lname == "none":
                model_filter, other_filters = "none", []
            elif MODEL_FILTER_RE.match(lname):
                model_filter = lname
            else:
                other_filters.append(name)
            yield i, line, None, True
            continue
        if model_filter == "none":
            applicable = False
        elif model_filter is not None and not model_filter_applies(model_filter, model):
            applicable = False
        elif other_filters:
            applicable = None
        else:
            applicable = True
        yield i, line, applicable, False


def _active(line):
    s = line.strip()
    return bool(s) and not s.startswith("#")


def find_vc4_overlay(text, model):
    """Locate the one applicable vc4-kms-v3d overlay line.

    Returns dict(index, line, overlay, params, noaudio) where ``noaudio`` also
    honours a following ``dtparam=noaudio`` (dtparams after a dtoverlay line
    apply to that overlay).
    """
    lines = text.splitlines()
    candidates = []
    unknown = []
    for i, line, applicable, is_section in _scan(lines, model):
        if is_section or not _active(line):
            continue
        m = VC4_OVERLAY_RE.match(line)
        if not m:
            continue
        if applicable is None:
            unknown.append((i, line))
        elif applicable:
            candidates.append((i, line, m))
    if unknown:
        raise ConfigTxtError(
            "vc4-kms-v3d overlay appears under a conditional filter this tool cannot evaluate "
            "(line %d: %r); edit config.txt manually" % (unknown[0][0] + 1, unknown[0][1].strip())
        )
    if not candidates:
        raise ConfigTxtError(
            "no applicable dtoverlay=vc4-kms-v3d line found; the KMS driver is required for this profile"
        )
    if len(candidates) > 1:
        raise ConfigTxtError(
            "found %d applicable vc4-kms-v3d overlay lines (%s); refusing to guess which one is effective"
            % (len(candidates), ", ".join(str(c[0] + 1) for c in candidates))
        )
    idx, line, m = candidates[0]
    params = [p.strip() for p in (m.group(2) or "").split(",") if p.strip()]
    for p in params:
        if p.startswith("noaudio") and p != "noaudio":
            raise ConfigTxtError("unsupported noaudio parameter form %r on line %d" % (p, idx + 1))
    noaudio = "noaudio" in params
    dtparam_noaudio = False
    for j in range(idx + 1, len(lines)):
        s = lines[j].strip()
        if SECTION_RE.match(lines[j]) or re.match(r"^dtoverlay\s*=", s):
            break
        if re.match(r"^dtparam\s*=\s*noaudio(\s*=\s*(on|true|yes|1))?\s*$", s):
            dtparam_noaudio = True
        elif re.match(r"^dtparam\s*=\s*noaudio", s):
            raise ConfigTxtError("unsupported dtparam noaudio form on line %d: %r" % (j + 1, s))
    return {
        "index": idx,
        "line": line,
        "overlay": m.group(1),
        "params": params,
        "noaudio": noaudio or dtparam_noaudio,
        "noaudio_on_line": noaudio,
    }


def plan_audio(text, model, policy, previous=None):
    """Return (new_text, record). ``record`` is None when nothing is owned."""
    info = find_vc4_overlay(text, model)
    if policy == "preserve":
        return text, None
    if policy != "disabled":
        raise ConfigTxtError("unknown HDMI audio policy %r" % policy)
    if previous and info["noaudio"]:
        # Reinstall: still disabled; keep the original baseline record so that
        # uninstall can restore (or report) the owner's original line.
        return text, dict(previous)
    if info["noaudio"]:
        return text, None  # already disabled by the owner; nothing owned
    lines = text.splitlines(True)
    old = lines[info["index"]]
    newline = "\n" if old.endswith("\n") else ""
    body = old.rstrip("\r\n").rstrip()
    new_line = body + ",noaudio"
    lines[info["index"]] = new_line + newline
    record = {"installed_line": new_line, "prior_line": body}
    if previous:
        record["prior_line"] = previous["prior_line"]
    return "".join(lines), record


def plan_uninstall(text, record):
    """Return (new_text, conflicts)."""
    if not record:
        return text, []
    lines = text.splitlines(True)
    matches = [i for i, l in enumerate(lines) if l.rstrip("\r\n").rstrip() == record["installed_line"]]
    if len(matches) == 1:
        i = matches[0]
        nl = "\n" if lines[i].endswith("\n") else ""
        lines[i] = record["prior_line"] + nl
        return "".join(lines), []
    if not matches:
        if any(l.rstrip("\r\n").rstrip() == record["prior_line"] for l in lines):
            return text, []  # already restored
        return text, [
            "config.txt no longer contains %r; HDMI audio overlay setting left unchanged"
            % record["installed_line"]
        ]
    return text, ["config.txt contains %r more than once; left unchanged" % record["installed_line"]]


def overlay_documents_noaudio(readme_text):
    """True if the installed overlay README documents vc4-kms-v3d's noaudio."""
    if not readme_text:
        return False
    for m in re.finditer(r"^Name:\s+vc4-kms-v3d\s*$", readme_text, re.M):
        end = readme_text.find("\nName:", m.end())
        block = readme_text[m.end(): end if end != -1 else len(readme_text)]
        if re.search(r"^(?:Params:)?\s*noaudio\b", block, re.M):
            return True
    return False


def find_key(text, key, model):
    """Return values of an applicable top-level ``key=value`` setting."""
    values = []
    for _, line, applicable, is_section in _scan(text.splitlines(), model):
        if is_section or not _active(line) or applicable is False:
            continue
        m = re.match(r"^\s*%s\s*=\s*(.*?)\s*$" % re.escape(key), line)
        if m:
            values.append(m.group(1))
    return values


def legacy_video_settings(text, model):
    found = []
    for key in LEGACY_VIDEO_KEYS:
        for value in find_key(text, key, model):
            found.append("%s=%s" % (key, value))
    return found


def explicit_initramfs(text, model):
    """Return the file named by an applicable ``initramfs <file>`` line, if any."""
    found = None
    for _, line, applicable, is_section in _scan(text.splitlines(), model):
        if is_section or not _active(line) or applicable is False:
            continue
        m = re.match(r"^\s*initramfs\s+(\S+)", line)
        if m:
            found = m.group(1)
    return found

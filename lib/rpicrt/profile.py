"""Safe parser and validator for profile files.

A profile is a shell-compatible ``KEY=VALUE`` file, but it is never sourced
or executed. Only whitelisted keys are accepted, every value is validated
against a strict type, and values that could carry shell syntax are rejected.
Validated values are later substituted into generated root-owned scripts, so
the character sets allowed here are deliberately narrow.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Tuple


class ProfileError(Exception):
    pass


# DRM_MODE_FLAG_* and RR_* share the same bit values.
MODE_FLAG_BITS = {
    "phsync": 0x1,
    "nhsync": 0x2,
    "pvsync": 0x4,
    "nvsync": 0x8,
    "interlace": 0x10,
    "dblscan": 0x20,
    "csync": 0x40,
    "pcsync": 0x80,
    "ncsync": 0x100,
    "dblclk": 0x1000,
}

PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_BARE_RE = re.compile(r"^[A-Za-z0-9._,:+@/%-]*$")
# Characters allowed inside a double-quoted value. No quotes, no $, no
# backticks, no backslashes, no control characters.
_QUOTED_RE = re.compile(r"^[A-Za-z0-9 ._,:;+@/%()&=#'!?<>*~^\[\]{}|-]*$")

_CONNECTOR_RE = re.compile(r"^[A-Za-z]+(-[A-Za-z])?-[0-9]{1,2}$")  # HDMI-A-1
_OUTPUT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,31}$")  # HDMI-1
_MODE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")
_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.bin$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_KVIDEO_RE = re.compile(r"^[0-9]{2,4}x[0-9]{2,4}(@[0-9]{2,3})?[imMReD]*$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


def _text(v):
    if not v or len(v) > 200:
        raise ValueError("must be 1-200 characters")
    return v


def _token(v):
    if not _TOKEN_RE.match(v):
        raise ValueError("must be a simple token")
    return v


def _semi_list(v):
    items = tuple(x.strip() for x in v.split(";") if x.strip())
    if not items:
        raise ValueError("must list at least one item (separate with ';')")
    for item in items:
        _text(item)
    return items


def _regex(rx, what):
    def check(v):
        if not rx.match(v):
            raise ValueError("is not a valid %s" % what)
        return v

    return check


def _int_range(lo, hi):
    def check(v):
        if not re.match(r"^[0-9]{1,7}$", v):
            raise ValueError("must be a non-negative integer")
        n = int(v)
        if not lo <= n <= hi:
            raise ValueError("must be between %d and %d" % (lo, hi))
        return n

    return check


def _timings(v):
    parts = v.split(",")
    if len(parts) != 4 or not all(re.match(r"^[0-9]{1,5}$", p) for p in parts):
        raise ValueError("must be four comma-separated integers")
    a, b, c, d = (int(p) for p in parts)
    if not (0 < a <= b < c <= d):
        raise ValueError("must satisfy active <= sync start < sync end <= total")
    return (a, b, c, d)


def _flags(v):
    names = tuple(x for x in v.split(",") if x)
    if not names:
        raise ValueError("must list at least one flag")
    for n in names:
        if n not in MODE_FLAG_BITS:
            raise ValueError("unknown flag %r (known: %s)" % (n, ", ".join(sorted(MODE_FLAG_BITS))))
    if len(set(names)) != len(names):
        raise ValueError("duplicate flag")
    return names


def _hex(v):
    if not re.match(r"^0x[0-9a-fA-F]{1,8}$", v):
        raise ValueError("must be a hexadecimal value such as 0x101a")
    return int(v, 16)


def _aspect(v):
    m = re.match(r"^([1-9][0-9]?):([1-9][0-9]?)$", v)
    if not m:
        raise ValueError("must look like 4:3")
    return (int(m.group(1)), int(m.group(2)))


def _audio(v):
    if v not in ("disabled", "preserve"):
        raise ValueError("must be 'disabled' or 'preserve'")
    return v


# key -> (attribute, validator, required)
SCHEMA = {
    "PROFILE_ID": ("id", _regex(PROFILE_NAME_RE, "profile id"), True),
    "PROFILE_DESCRIPTION": ("description", _text, True),
    "PROFILE_STATUS": ("status", _text, True),
    "HARDWARE_TESTED_MODELS": ("tested_models", _semi_list, True),
    "CONVERTER": ("converter", _text, True),
    "CONVERTER_EDID_NAME": ("converter_edid_name", _token, True),
    "TV_MODEL": ("tv_model", _text, True),
    "TV_INPUT": ("tv_input", _text, True),
    "OS_TESTED_ID": ("os_id", _token, True),
    "OS_TESTED_CODENAMES": ("os_codenames", _semi_list, True),
    "OS_TESTED_ARCH": ("os_arch", _semi_list, True),
    "KERNEL_TESTED": ("kernel_tested", _token, True),
    "DRM_CONNECTOR": ("drm_connector", _regex(_CONNECTOR_RE, "DRM connector name"), True),
    "XRANDR_OUTPUT": ("xrandr_output", _regex(_OUTPUT_RE, "XRandR output name"), True),
    "EDID_FILE": ("edid_file", _regex(_FILE_RE, "EDID file name"), True),
    "EDID_SHA256": ("edid_sha256", _regex(_SHA_RE, "SHA-256"), True),
    "EDID_SOURCE_FILE": ("edid_source_file", _regex(_FILE_RE, "EDID file name"), True),
    "EDID_SOURCE_SHA256": ("edid_source_sha256", _regex(_SHA_RE, "SHA-256"), True),
    "KERNEL_VIDEO_MODE": ("kernel_video_mode", _regex(_KVIDEO_RE, "kernel video= mode"), True),
    "MODE_NAME": ("mode_name", _regex(_MODE_NAME_RE, "mode name"), True),
    "MODE_CLOCK_KHZ": ("clock_khz", _int_range(1000, 600000), True),
    "MODE_H": ("h", _timings, True),
    "MODE_V": ("v", _timings, True),
    "MODE_FLAGS": ("flags", _flags, True),
    "EXPECTED_DRM_FLAGS": ("expected_drm_flags", _hex, True),
    "MARGIN_LEFT": ("margin_left", _int_range(0, 100), True),
    "MARGIN_RIGHT": ("margin_right", _int_range(0, 100), True),
    "MARGIN_TOP": ("margin_top", _int_range(0, 100), True),
    "MARGIN_BOTTOM": ("margin_bottom", _int_range(0, 100), True),
    "PHYSICAL_ASPECT": ("physical_aspect", _aspect, True),
    "HDMI_AUDIO_DEFAULT": ("hdmi_audio_default", _audio, True),
}


@dataclass(frozen=True)
class Profile:
    id: str
    description: str
    status: str
    tested_models: Tuple[str, ...]
    converter: str
    converter_edid_name: str
    tv_model: str
    tv_input: str
    os_id: str
    os_codenames: Tuple[str, ...]
    os_arch: Tuple[str, ...]
    kernel_tested: str
    drm_connector: str
    xrandr_output: str
    edid_file: str
    edid_sha256: str
    edid_source_file: str
    edid_source_sha256: str
    kernel_video_mode: str
    mode_name: str
    clock_khz: int
    h: Tuple[int, int, int, int]
    v: Tuple[int, int, int, int]
    flags: Tuple[str, ...]
    expected_drm_flags: int
    margin_left: int
    margin_right: int
    margin_top: int
    margin_bottom: int
    physical_aspect: Tuple[int, int]
    hdmi_audio_default: str
    raw: Dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def mode_flag_bits(self):
        bits = 0
        for name in self.flags:
            bits |= MODE_FLAG_BITS[name]
        return bits

    @property
    def edid_firmware_path(self):
        """Path relative to /lib/firmware, as drm.edid_firmware= expects."""
        return "edid/" + self.edid_file

    @property
    def margins(self):
        return {
            "left": self.margin_left,
            "right": self.margin_right,
            "top": self.margin_top,
            "bottom": self.margin_bottom,
        }

    def helper_args(self):
        """Arguments for crt-xrandr that describe this profile's mode."""
        return [
            "--output", self.xrandr_output,
            "--name", self.mode_name,
            "--clock", str(self.clock_khz),
            "--h", ",".join(str(x) for x in self.h),
            "--v", ",".join(str(x) for x in self.v),
            "--flags", ",".join(self.flags),
        ]

    def kernel_mode_line(self):
        """The timing fields of a DRM debugfs ``mode:`` line."""
        return (self.clock_khz,) + self.h + self.v

    # The mode is described in logical (non-repeated) pixels: 13.5 MHz over
    # 858 pixels is the real line rate. With DBLCLK the HDMI TMDS clock is
    # doubled (27 MHz) together with the pixel count, so rates are unchanged.
    def field_rate_hz(self):
        """Vertical rate as XRandR reports it (fields/s for interlaced modes)."""
        rate = self.clock_khz * 1000.0 / (self.h[3] * self.v[3])
        if "interlace" in self.flags:
            rate *= 2
        return rate

    def line_rate_khz(self):
        return self.clock_khz / float(self.h[3])


def parse_text(text, source="<profile>"):
    values = {}
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            raise ProfileError("%s:%d: expected KEY=VALUE" % (source, lineno))
        key, value = m.group(1), m.group(2)
        if key not in SCHEMA:
            raise ProfileError("%s:%d: unknown key %s" % (source, lineno, key))
        if key in values:
            raise ProfileError("%s:%d: duplicate key %s" % (source, lineno, key))
        if value.startswith('"'):
            if len(value) < 2 or not value.endswith('"'):
                raise ProfileError("%s:%d: unterminated quoted value" % (source, lineno))
            inner = value[1:-1]
            if not _QUOTED_RE.match(inner):
                raise ProfileError(
                    "%s:%d: %s contains characters that are not allowed in a profile "
                    "(quotes, $, backticks, backslashes and control characters are rejected)"
                    % (source, lineno, key)
                )
            value = inner
        elif not _BARE_RE.match(value):
            raise ProfileError(
                "%s:%d: unquoted value for %s contains unsupported characters" % (source, lineno, key)
            )
        values[key] = value

    missing = [k for k, (_, _, req) in SCHEMA.items() if req and k not in values]
    if missing:
        raise ProfileError("%s: missing required keys: %s" % (source, ", ".join(missing)))

    kwargs = {}
    for key, (attr, validator, _) in SCHEMA.items():
        try:
            kwargs[attr] = validator(values[key])
        except ValueError as exc:
            raise ProfileError("%s: %s %s" % (source, key, exc))
    profile = Profile(raw=dict(values), **kwargs)
    _cross_check(profile, source)
    return profile


def _cross_check(p, source):
    if p.mode_flag_bits != p.expected_drm_flags:
        raise ProfileError(
            "%s: MODE_FLAGS give 0x%x but EXPECTED_DRM_FLAGS is 0x%x"
            % (source, p.mode_flag_bits, p.expected_drm_flags)
        )
    if "phsync" in p.flags and "nhsync" in p.flags:
        raise ProfileError("%s: MODE_FLAGS cannot contain both phsync and nhsync" % source)
    if "pvsync" in p.flags and "nvsync" in p.flags:
        raise ProfileError("%s: MODE_FLAGS cannot contain both pvsync and nvsync" % source)
    if p.margin_left + p.margin_right >= p.h[0] or p.margin_top + p.margin_bottom >= p.v[0]:
        raise ProfileError("%s: margins exceed the active area" % source)
    if p.edid_file == p.edid_source_file:
        raise ProfileError("%s: EDID_FILE and EDID_SOURCE_FILE must differ" % source)


def load(repo_dir, name):
    if not PROFILE_NAME_RE.match(name or ""):
        raise ProfileError("invalid profile name %r" % (name,))
    path = os.path.join(repo_dir, "profiles", name + ".conf")
    if not os.path.isfile(path):
        available = list_profiles(repo_dir)
        raise ProfileError(
            "profile %r not found (available: %s)" % (name, ", ".join(available) or "none")
        )
    with open(path, "r", encoding="utf-8") as fh:
        profile = parse_text(fh.read(), source=path)
    if profile.id != name:
        raise ProfileError("%s: PROFILE_ID %r does not match file name" % (path, profile.id))
    return profile


def list_profiles(repo_dir):
    d = os.path.join(repo_dir, "profiles")
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".conf") and PROFILE_NAME_RE.match(f[:-5]))


def from_raw(raw, source="<manifest>"):
    """Rebuild a profile from the normalized values stored in the manifest."""
    lines = []
    for key, value in raw.items():
        lines.append('%s="%s"' % (key, value))
    return parse_text("\n".join(lines), source=source)

"""Editing of the single-line Raspberry Pi kernel command line.

Only two arguments are owned, and only for the profile's DRM connector:

* the connector's entry inside ``drm.edid_firmware=`` (other connectors'
  entries in the same argument are preserved), and
* ``video=<connector>:<mode>``.

Everything else (``root=``, consoles, unrelated parameters, their order) is
left untouched. Ambiguous or conflicting existing arguments are reported
rather than "fixed".
"""


class CmdlineError(Exception):
    pass


EDID_KEY = "drm.edid_firmware"
CONFLICTING_KEYS = ("drm_kms_helper.edid_firmware",)


def split(text):
    """Return (tokens, trailing_newline). Rejects multi-line files."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) == 0:
        raise CmdlineError("kernel command line file is empty")
    if len(lines) != 1:
        raise CmdlineError(
            "kernel command line must be a single line, found %d non-empty lines" % len(lines)
        )
    return lines[0].split(), text.endswith("\n")


def join(tokens, trailing_newline=True):
    return " ".join(tokens) + ("\n" if trailing_newline else "")


def _parse_edid_value(value):
    """Parse ``CONN:path[,CONN:path...]`` or a bare global ``path``."""
    entries = []
    for part in value.split(","):
        if not part:
            raise CmdlineError("empty entry in %s=%s" % (EDID_KEY, value))
        if ":" in part:
            conn, path = part.split(":", 1)
            if not conn or not path:
                raise CmdlineError("malformed entry %r in %s" % (part, EDID_KEY))
            entries.append((conn, path))
        else:
            entries.append((None, part))
    return entries


def _format_edid_value(entries):
    return ",".join(path if conn is None else "%s:%s" % (conn, path) for conn, path in entries)


def _find(tokens, connector):
    edid_idx = [i for i, t in enumerate(tokens) if t.startswith(EDID_KEY + "=")]
    video_idx = []
    for i, t in enumerate(tokens):
        if t.startswith("video="):
            spec = t[len("video="):]
            if ":" not in spec:
                raise CmdlineError(
                    "found global %r which applies to every connector; refusing to guess how it "
                    "interacts with the profile" % t
                )
            if spec.split(":", 1)[0] == connector:
                video_idx.append(i)
    for key in CONFLICTING_KEYS:
        if any(t.startswith(key + "=") for t in tokens):
            raise CmdlineError("found legacy %s= argument; remove it manually first" % key)
    if len(edid_idx) > 1:
        raise CmdlineError("found %d %s= arguments; expected at most one" % (len(edid_idx), EDID_KEY))
    if len(video_idx) > 1:
        raise CmdlineError("found %d video=%s:... arguments; expected at most one" % (len(video_idx), connector))
    return (edid_idx[0] if edid_idx else None), (video_idx[0] if video_idx else None)


def _edid_entry(tokens, idx, connector):
    """Return (entries, connector entry path or None)."""
    if idx is None:
        return [], None
    entries = _parse_edid_value(tokens[idx].split("=", 1)[1])
    if any(conn is None for conn, _ in entries):
        raise CmdlineError(
            "%s has a global (connector-less) entry: %s; refusing to modify it" % (EDID_KEY, tokens[idx])
        )
    mine = [path for conn, path in entries if conn == connector]
    if len(mine) > 1:
        raise CmdlineError("%s lists connector %s more than once" % (EDID_KEY, connector))
    return entries, (mine[0] if mine else None)


def current_state(tokens, connector):
    """Return the owned values currently present (edid path, video token)."""
    edid_idx, video_idx = _find(tokens, connector)
    _, edid_path = _edid_entry(tokens, edid_idx, connector)
    return {"edid": edid_path, "video": tokens[video_idx] if video_idx is not None else None}


def _set_edid(tokens, connector, path):
    """Set (path) or remove (None) the connector entry. Returns new tokens."""
    tokens = list(tokens)
    idx, _ = _find(tokens, connector)
    entries, _ = _edid_entry(tokens, idx, connector)
    others = [(c, p) for c, p in entries if c != connector]
    if path is None:
        new_entries = others
    elif any(c == connector for c, _ in entries):
        new_entries = [(c, path if c == connector else p) for c, p in entries]
    else:
        new_entries = entries + [(connector, path)]
    if idx is None:
        if new_entries:
            tokens.append(EDID_KEY + "=" + _format_edid_value(new_entries))
    elif new_entries:
        tokens[idx] = EDID_KEY + "=" + _format_edid_value(new_entries)
    else:
        del tokens[idx]
    return tokens


def _set_video(tokens, connector, token):
    tokens = list(tokens)
    _, idx = _find(tokens, connector)
    if token is None:
        if idx is not None:
            del tokens[idx]
    elif idx is None:
        tokens.append(token)
    else:
        tokens[idx] = token
    return tokens


def plan_install(text, connector, edid_path, video_mode, previous=None):
    """Compute the new command line.

    ``previous`` is the record from an earlier (possibly interrupted)
    installation; its ``prior`` values are kept so that a reinstall never
    replaces the original baseline with our own values.

    Returns ``(new_text, record)``.
    """
    tokens, nl = split(text)
    state = current_state(tokens, connector)
    want_video = "video=%s:%s" % (connector, video_mode)
    record = {
        "connector": connector,
        "edid": {"installed": edid_path, "prior": state["edid"]},
        "video": {"installed": want_video, "prior": state["video"]},
    }
    if previous:
        if previous.get("connector") != connector:
            raise CmdlineError(
                "an earlier installation used connector %s; uninstall it before switching to %s"
                % (previous.get("connector"), connector)
            )
        record["edid"]["prior"] = previous["edid"]["prior"]
        record["video"]["prior"] = previous["video"]["prior"]
    new_tokens = _set_edid(tokens, connector, edid_path)
    new_tokens = _set_video(new_tokens, connector, want_video)
    if new_tokens == tokens:
        return text, record
    return join(new_tokens, nl), record


def plan_uninstall(text, record):
    """Restore prior values for owned arguments that still hold our values.

    Returns ``(new_text, conflicts)``. Arguments changed since installation
    are left alone and reported as conflicts.
    """
    tokens, nl = split(text)
    connector = record["connector"]
    conflicts = []
    state = current_state(tokens, connector)
    new_tokens = list(tokens)

    edid = record["edid"]
    if state["edid"] == edid["installed"]:
        new_tokens = _set_edid(new_tokens, connector, edid["prior"])
    elif state["edid"] != edid["prior"]:
        conflicts.append(
            "%s entry for %s is %r; expected installed value %r (or original %r). Left unchanged."
            % (EDID_KEY, connector, state["edid"], edid["installed"], edid["prior"])
        )

    video = record["video"]
    if state["video"] == video["installed"]:
        new_tokens = _set_video(new_tokens, connector, video["prior"])
    elif state["video"] != video["prior"]:
        conflicts.append(
            "video argument for %s is %r; expected installed value %r (or original %r). Left unchanged."
            % (connector, state["video"], video["installed"], video["prior"])
        )

    # Any conflict leaves the whole line untouched: the owner sees exactly the
    # state they created, and partial reverts never produce an untested mix.
    if conflicts or new_tokens == tokens:
        return text, conflicts
    return join(new_tokens, nl), conflicts


def duplicates(tokens, connector):
    """Count owned arguments (for verification of idempotence)."""
    edid_count = sum(1 for t in tokens if t.startswith(EDID_KEY + "="))
    video_count = sum(
        1 for t in tokens if t.startswith("video=%s:" % connector)
    )
    return {"edid_args": edid_count, "video_args": video_count}

"""Minimal EDID 1.3 + CTA-861 inspection (validation only, no generation).

Generation lives in ``edid/generate-edid.py``.
"""

HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"


class EdidError(Exception):
    pass


def block_checksums_ok(data):
    """Return list of (block_index, ok)."""
    if len(data) % 128 != 0 or not data:
        raise EdidError("EDID length %d is not a positive multiple of 128" % len(data))
    return [(i, sum(data[i * 128:(i + 1) * 128]) % 256 == 0) for i in range(len(data) // 128)]


def _descriptor_name(desc):
    if desc[0:3] == b"\x00\x00\x00" and desc[3] == 0xFC:
        return desc[5:18].split(b"\x0a")[0].decode("ascii", "replace").rstrip()
    return None


def inspect(data):
    """Summarize the parts of an EDID relevant to this project."""
    if len(data) < 128 or data[:8] != HEADER:
        raise EdidError("missing EDID header")
    info = {
        "size": len(data),
        "checksums": block_checksums_ok(data),
        "extensions": data[126],
        "established_timings": data[35:38] != b"\x00\x00\x00",
        "standard_timings": [data[i:i + 2].hex() for i in range(38, 54, 2) if data[i:i + 2] != b"\x01\x01"],
        "base_dtds": 0,
        "monitor_name": None,
        "preferred_timing_flag": bool(data[24] & 0x02),
        "cta": None,
    }
    for off in range(54, 126, 18):
        desc = data[off:off + 18]
        if desc[0] or desc[1]:
            info["base_dtds"] += 1
        name = _descriptor_name(desc)
        if name:
            info["monitor_name"] = name
    if len(data) >= 256 and data[128] == 0x02:
        info["cta"] = _inspect_cta(data[128:256])
    return info


def _inspect_cta(block):
    dtd_offset = block[2]
    cta = {
        "revision": block[1],
        "flags": block[3],
        "native_dtds": block[3] & 0x0F,
        "vics": [],
        "native_vics": [],
        "audio_blocks": 0,
        "speaker_blocks": 0,
        "vendor_blocks": [],
        "extended_tags": [],
        "dtds": 0,
    }
    off = 4
    end = dtd_offset if dtd_offset >= 4 else 4
    while off < end:
        tag = block[off] >> 5
        length = block[off] & 0x1F
        payload = block[off + 1: off + 1 + length]
        if tag == 2:
            for b in payload:
                vic = b & 0x7F if (b & 0x7F) <= 64 else b
                if b & 0x80 and (b & 0x7F) <= 64:
                    cta["native_vics"].append(b & 0x7F)
                cta["vics"].append(vic)
        elif tag == 1:
            cta["audio_blocks"] += 1
        elif tag == 4:
            cta["speaker_blocks"] += 1
        elif tag == 3 and len(payload) >= 3:
            cta["vendor_blocks"].append("%02x%02x%02x" % (payload[2], payload[1], payload[0]))
        elif tag == 7 and payload:
            cta["extended_tags"].append(payload[0])
        off += 1 + length
    if dtd_offset >= 4:
        o = dtd_offset
        while o + 18 <= 127:
            if block[o] == 0 and block[o + 1] == 0:
                break
            cta["dtds"] += 1
            o += 18
    return cta


def restriction_problems(data, allowed_vics=(6,)):
    """Return a list of reasons the EDID is *not* a restricted 480i-only EDID."""
    info = inspect(data)
    problems = []
    for idx, ok in info["checksums"]:
        if not ok:
            problems.append("block %d checksum is invalid" % idx)
    if info["established_timings"]:
        problems.append("base block advertises established timings")
    if info["standard_timings"]:
        problems.append("base block advertises standard timings %s" % ", ".join(info["standard_timings"]))
    if info["base_dtds"]:
        problems.append("base block contains %d detailed timing descriptor(s)" % info["base_dtds"])
    cta = info["cta"]
    if cta is None:
        problems.append("no CTA-861 extension block")
    else:
        if sorted(set(cta["vics"])) != sorted(allowed_vics):
            problems.append("CTA video data block lists VICs %s (expected only %s)" % (cta["vics"], list(allowed_vics)))
        if cta["dtds"]:
            problems.append("CTA block contains %d detailed timing descriptor(s)" % cta["dtds"])
    return problems

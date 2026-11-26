from pathlib import Path

source = Path.home() / "converter.edid"
target = Path.home() / "converter-crt.edid"

edid = bytearray(source.read_bytes())

assert len(edid) == 256
assert edid[128] == 0x02

# Remove established timings.
edid[35:38] = bytes(3)

# Remove standard timings.
edid[38:54] = b"\x01\x01" * 8

# Remove the base block's preferred timing flag.
edid[24] &= ~0x02

# Preserve the monitor name descriptor.
name = None

for offset in range(54, 126, 18):
    descriptor = edid[offset:offset + 18]

    if descriptor[:5] == b"\x00\x00\x00\xfc\x00":
        name = bytes(descriptor)

assert name is not None

# Replace base block descriptors.
# Avoid a base-block DTD because CTA VIC 6 carries
# the required HDMI pixel repetition semantics.
dummy = b"\x00\x00\x00\x10\x00" + bytes(13)

edid[54:126] = name + dummy * 3

# Rebuild CTA data blocks.
blocks = bytearray()
offset = 132
end = 128 + edid[130]
video_found = False

while offset < end:
    tag = edid[offset] >> 5
    length = edid[offset] & 0x1f

    if tag == 2:
        # Advertise only VIC 6 (480i 4:3).
        # Set the native mode flag.
        if not video_found:
            blocks.extend([0x41, 0x86])
            video_found = True
    else:
        # Preserve audio, speaker allocation,
        # HDMI vendor-specific and other blocks.
        blocks.extend(edid[offset:offset + length + 1])

    offset += length + 1

assert video_found
assert len(blocks) <= 123

# Remove all existing CTA detailed timings.
edid[132:255] = bytes(123)

# Install rebuilt CTA data blocks.
edid[132:132 + len(blocks)] = blocks
edid[130] = 4 + len(blocks)

# Clear native detailed timing count.
edid[131] &= 0xf0

# Physical display dimensions are unknown.
edid[21] = 0
edid[22] = 0

# Correct reserved digital input bits for EDID 1.3.
edid[20] = 0x80

# Signal sRGB chromaticities.
edid[24] |= 0x04

# Recalculate checksums.
for offset in (0, 128):
    edid[offset + 127] = (
        -sum(edid[offset:offset + 127])
    ) & 0xff

target.write_bytes(edid)

print(f"Created {target}")

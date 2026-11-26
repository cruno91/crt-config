# EDID

- `edid/lontium-original.bin`: the EDID read from the converter (monitor name `LONTIUM`).
- `edid/converter-crt.bin`: the restricted EDID installed to `/lib/firmware/edid/`.

The converter's preferred timing is 720p, and it also offers 1080p and 1080i. It does not
scale, so any HD mode reaches the CRT as an unusable picture, and forcing 480i on the
kernel command line alone produced a wrong progressive mode. The restricted EDID offers
only CTA **VIC 6**: 720(1440)×480i at 59.94 Hz, 4:3, with 2× pixel repetition. The
kernel then exposes only standard 480i modes with the `DBLCLK` flag that HDMI needs for
13.5 MHz content. The audio and HDMI data blocks are kept, so the sink still claims audio
support; HDMI audio is disabled separately ([known-issues.md](known-issues.md)).

```sh
edid/generate-edid.py --check                       # regenerate and compare with the committed bytes
edid/generate-edid.py --input X.bin --output Y.bin  # for another converter
```

The transformation steps in `generate-edid.py` must stay byte-compatible with the
committed file; `--check` and the tests enforce this.

## `edid-decode --check` failures are intentional

```text
Block 0: EDID 1.3 requires that the first detailed timing is the preferred timing.
Block 0: Missing Display Range Limits Descriptor.
Block 1: Video Capability Data Block: Set Selectable RGB Quantization to avoid interop issues.
Block 1: Required 640x480p60 timings are missing in the established timings and the SVD list (VIC 1).
EDID:    CTA-861: A native interlaced timing is present, but not a native progressive timing.
```

Don't "fix" these. A base-block preferred DTD, range limits or VIC 1 would put back the
progressive modes the restriction removes. The quantization note was already in the
original EDID. `verify.sh` checks the restriction itself with its own parser: only VIC 6,
no detailed timings, no established or standard timings, valid checksums.

## Another converter

Don't substitute a generic EDID. Read the converter's own EDID from
`/sys/class/drm/card*-HDMI-A-1/edid` while no override is active, run the generator on
it, check the result with `edid-decode`, and put both hashes in a new profile.

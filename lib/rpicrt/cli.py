"""Command-line entry point used by install.sh, verify.sh and uninstall.sh."""

import argparse
import os
import sys

sys.dont_write_bytecode = True  # never leave root-owned __pycache__ in the checkout

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if os.path.dirname(HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(HERE))

from rpicrt import TOOL_NAME, VERSION  # noqa: E402
from rpicrt import profile as profile_mod  # noqa: E402
from rpicrt.fsutil import Root  # noqa: E402
from rpicrt.installer import InstallError, Installer  # noqa: E402
from rpicrt.runner import RunnerError  # noqa: E402
from rpicrt.state import StateError  # noqa: E402
from rpicrt.uninstaller import UninstallError, Uninstaller  # noqa: E402
from rpicrt.verifier import Verifier, render_json, render_text  # noqa: E402

INSTALL_EPILOG = """\
examples:
  sudo ./install.sh --profile sony-trinitron-lontium --dry-run
  sudo ./install.sh --profile sony-trinitron-lontium
  sudo ./install.sh --profile sony-trinitron-lontium --migrate-legacy   # take over a hand-made setup

Nothing is rebooted or restarted. After installing: sudo reboot, then
sudo ./verify.sh --profile sony-trinitron-lontium. Recovery: docs/recovery.md
"""


def _add_root(ap):
    # Staged filesystem root for offline tests. Requires RPI_CRT_MOCK_BIN.
    ap.add_argument("--root", default="/", help=argparse.SUPPRESS)


def cmd_install(argv):
    ap = argparse.ArgumentParser(
        prog="install.sh",
        description="Install a CRT display profile (idempotent; safe to rerun).",
        epilog=INSTALL_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True, help="profile name (available: %s)"
                    % ", ".join(profile_mod.list_profiles(REPO)))
    ap.add_argument("--hdmi-audio", choices=("disabled", "preserve"),
                    help="HDMI audio policy (default: the profile's HDMI_AUDIO_DEFAULT)")
    ap.add_argument("--dry-run", action="store_true", help="show the plan and diffs, change nothing")
    ap.add_argument("--migrate-legacy", action="store_true",
                    help="take over a hand-made setup (crt-edid initramfs hook, crt-display-setup.sh LightDM hook)")
    ap.add_argument("--replace-display-hook", action="store_true",
                    help="replace an unrelated existing LightDM display-setup-script instead of chaining it")
    ap.add_argument("--allow-untested-hardware", action="store_true",
                    help="continue on a Raspberry Pi model the profile was not tested on")
    ap.add_argument("--allow-untested-os", action="store_true",
                    help="continue on an OS release/architecture the profile was not tested on")
    ap.add_argument("--no-install-deps", action="store_true", help="fail instead of installing missing packages")
    ap.add_argument("--with-diagnostics", action="store_true",
                    help="also install diagnostic packages (edid-decode, libdrm-tests, mpv, ffmpeg, python3-tk)")
    _add_root(ap)
    args = ap.parse_args(argv)

    prof = profile_mod.load(REPO, args.profile)
    root = Root(args.root)
    options = {
        "hdmi_audio": args.hdmi_audio or prof.hdmi_audio_default,
        "dry_run": args.dry_run,
        "migrate_legacy": args.migrate_legacy,
        "replace_display_hook": args.replace_display_hook,
        "allow_untested_hardware": args.allow_untested_hardware,
        "allow_untested_os": args.allow_untested_os,
        "install_deps": not args.no_install_deps,
        "with_diagnostics": args.with_diagnostics,
    }
    inst = Installer(REPO, root, prof, options)
    findings = inst.preflight()
    for n in findings.notes:
        print("note: " + n)
    for w in findings.warnings:
        print("WARNING: " + w)
    if findings.errors:
        for e in findings.errors:
            print("ERROR: " + e, file=sys.stderr)
        print("\nPreflight failed; nothing was changed.", file=sys.stderr)
        return 1
    inst.build_plan()
    print()
    inst.print_plan()
    if args.dry_run:
        print("\nDry run: nothing was changed.")
        return 0
    print("\nApplying ...")
    inst.apply()
    print("""
Installed profile %s.

Nothing has been restarted. Next steps:
  1. sudo reboot                        (the EDID and kernel mode apply at boot)
  2. sudo ./verify.sh --profile %s
  3. Check the picture on the CRT; see tests/geometry.py and tests/playback.sh.

If the picture is unusable, SSH in and see docs/recovery.md. Quick revert:
  sudo ./uninstall.sh && sudo reboot
Original files: %s/baseline/ and %s/cmdline.txt.rpi-crt-baseline (readable from
another computer on the SD card's boot partition).""" % (prof.id, prof.id, "/var/lib/rpi-crt", "/boot/firmware"))
    return 0


def cmd_uninstall(argv):
    ap = argparse.ArgumentParser(prog="uninstall.sh", description="Restore the original settings and remove installed files.")
    ap.add_argument("--dry-run", action="store_true", help="show what would be restored, change nothing")
    _add_root(ap)
    args = ap.parse_args(argv)
    un = Uninstaller(Root(args.root), dry_run=args.dry_run)
    conflicts = un.run()
    if conflicts:
        print("\nUninstall left %d item(s) for manual attention:" % len(conflicts), file=sys.stderr)
        for c in conflicts:
            print("  - " + c, file=sys.stderr)
        if not args.dry_run:
            print("\nResolve them (docs/recovery.md) and rerun uninstall.sh. Nothing else was overwritten.", file=sys.stderr)
        return 1
    if args.dry_run:
        print("\nDry run: nothing was changed.")
    else:
        print("\nUninstalled. Shared packages were not removed. Reboot to return to the original display settings.")
    return 0


def cmd_verify(argv):
    ap = argparse.ArgumentParser(prog="verify.sh", description="Read-only verification of the installed profile and runtime state.")
    ap.add_argument("--profile", help="profile name (default: the installed profile)")
    ap.add_argument("--runtime-only", action="store_true",
                    help="check only the running system against the profile (e.g. a hand-made setup)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--display", help="X display (default :0)")
    ap.add_argument("--xauthority", help="X authority file (default: $XAUTHORITY or LightDM's root cookie)")
    _add_root(ap)
    args = ap.parse_args(argv)
    v = Verifier(REPO, Root(args.root), profile_name=args.profile, runtime_only=args.runtime_only,
                 display=args.display, xauthority=args.xauthority)
    checks = v.run()
    pid = v.profile.id if v.profile else (args.profile or "?")
    text, code = (render_json if args.json else render_text)(checks, pid)
    print(text)
    return code


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("install", "uninstall", "verify"):
        print("usage: cli.py {install,uninstall,verify} ...", file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    try:
        return {"install": cmd_install, "uninstall": cmd_uninstall, "verify": cmd_verify}[cmd](rest)
    except (profile_mod.ProfileError, InstallError, UninstallError, StateError, RunnerError) as exc:
        print("%s: ERROR: %s" % (cmd, exc), file=sys.stderr)
        if cmd == "install":
            print("Completed steps are recorded in /var/lib/rpi-crt/manifest.json; rerun install.sh to resume "
                  "or uninstall.sh to roll back.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted; rerun to resume (install) or roll back (uninstall)", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

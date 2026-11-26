import os
import unittest

import support
from rpicrt import lightdm as ld
from rpicrt.fsutil import Root

OURS = "/usr/local/libexec/rpi-crt/display-setup"
STOCK = support.fixture("lightdm.conf.stock")
LEGACY = support.fixture("lightdm.conf.legacy")


class EditTests(unittest.TestCase):
    def test_replaces_active_line_in_seat_section_and_round_trips(self):
        new, rec = ld.plan_install(STOCK, OURS)
        self.assertEqual(rec["prior_line"], "display-setup-script=/usr/share/dispsetup.sh")
        self.assertEqual(new.count("display-setup-script=%s" % OURS), 1)
        self.assertEqual(len(new.splitlines()), len(STOCK.splitlines()))
        restored, conflicts = ld.plan_uninstall(new, rec)
        self.assertEqual((restored, conflicts), (STOCK, []))

    def test_legacy_line_is_replaced_and_restored(self):
        new, rec = ld.plan_install(LEGACY, OURS)
        self.assertEqual(rec["prior_line"], "display-setup-script=/usr/local/bin/crt-display-setup.sh")
        self.assertIn("##display-setup-script=/usr/share/dispsetup.sh", new)  # comment untouched
        self.assertEqual(ld.plan_uninstall(new, rec), (LEGACY, []))

    def test_inserts_after_commented_placeholder(self):
        text = "[LightDM]\n[Seat:*]\n#display-setup-script=\nuser-session=rpd-x\n"
        new, rec = ld.plan_install(text, OURS)
        self.assertEqual(new, "[LightDM]\n[Seat:*]\n#display-setup-script=\ndisplay-setup-script=%s\nuser-session=rpd-x\n" % OURS)
        self.assertEqual(ld.plan_uninstall(new, rec), (text, []))

    def test_appends_section_when_missing(self):
        text = "[LightDM]\nlogind-check-graphical=true\n"
        new, rec = ld.plan_install(text, OURS)
        self.assertTrue(rec["added_section"])
        self.assertTrue(new.endswith("\n[Seat:*]\ndisplay-setup-script=%s\n" % OURS))
        self.assertEqual(ld.plan_uninstall(new, rec), (text, []))

    def test_idempotent(self):
        new, rec = ld.plan_install(STOCK, OURS)
        again, rec2 = ld.plan_install(new, OURS, previous=rec)
        self.assertEqual(again, new)
        self.assertEqual(rec2["prior_line"], rec["prior_line"])

    def test_refuses_ambiguous_files(self):
        for text in ("[Seat:*]\na=1\n[Seat:*]\nb=2\n",
                     "[Seat:*]\ndisplay-setup-script=/a\ndisplay-setup-script=/b\n",
                     "[SeatDefaults]\nuser-session=x\n"):
            with self.subTest(text=text), self.assertRaises(ld.LightDMError):
                ld.plan_install(text, OURS)

    def test_uninstall_conflict_when_changed(self):
        new, rec = ld.plan_install(STOCK, OURS)
        edited = new.replace(OURS, "/usr/local/bin/other.sh")
        restored, conflicts = ld.plan_uninstall(edited, rec)
        self.assertEqual(restored, edited)
        self.assertEqual(len(conflicts), 1)


class PrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.root = support.make_root("stock")

    def tearDown(self):
        support.cleanup(self.root)

    def test_main_file_wins_over_dropins(self):
        support.write(self.root, "/etc/lightdm/lightdm.conf.d/50-x.conf", "[Seat:*]\ndisplay-setup-script=/from/dropin\n")
        eff = ld.effective_seat(ld.config_files(Root(self.root)))
        self.assertEqual(eff["display-setup-script"][0], "/usr/share/dispsetup.sh")
        self.assertEqual(eff["display-setup-script"][1], ld.MAIN_CONF)

    def test_dropin_value_used_when_main_has_none(self):
        text = support.read(self.root, ld.MAIN_CONF).replace("display-setup-script=/usr/share/dispsetup.sh\n", "")
        support.write(self.root, ld.MAIN_CONF, text)
        support.write(self.root, "/usr/share/lightdm/lightdm.conf.d/01-a.conf", "[Seat:*]\ndisplay-setup-script=/a\n")
        support.write(self.root, "/etc/lightdm/lightdm.conf.d/50-b.conf", "[Seat:*]\ndisplay-setup-script=/b\n")
        eff = ld.effective_seat(ld.config_files(Root(self.root)))
        self.assertEqual(eff["display-setup-script"][0], "/b")

    def test_seat_specific_section_overrides(self):
        support.write(self.root, "/etc/lightdm/lightdm.conf.d/50-seat0.conf", "[Seat:seat0]\ndisplay-setup-script=/specific\n")
        eff = ld.effective_seat(ld.config_files(Root(self.root)))
        self.assertEqual(eff["display-setup-script"][:3:2], ("/specific", "Seat:seat0"))

    def test_show_config_parser(self):
        out = ("[LightDM]\nA  run-directory=/run/lightdm\n\n[Seat:*]\nB  display-setup-script=/x\n"
               "B  user-session=rpd-x\n\n[Seat:seat0]\nC  autologin-session=rpd-x\n\nSources:\n"
               "A  /usr/share/lightdm/lightdm.conf.d/01.conf\nB  /etc/lightdm/lightdm.conf\n")
        sections = ld.parse_show_config(out)
        self.assertEqual(sections["Seat:*"]["display-setup-script"], "/x")
        eff = ld.effective_from_show_config(sections)
        self.assertEqual((eff["display-setup-script"], eff["autologin-session"]), ("/x", "rpd-x"))

    def test_session_kind(self):
        r = Root(self.root)
        self.assertEqual(ld.session_kind(r, "rpd-x"), "x11")
        self.assertEqual(ld.session_kind(r, "rpd-labwc"), "wayland")
        self.assertIsNone(ld.session_kind(r, "nope"))


if __name__ == "__main__":
    unittest.main()

"""Tests del actualizador. Correr con: python -m unittest tests.test_updater"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from m365_bridge import updater  # noqa: E402


class VersionTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(updater.parse_version("v1.0.4"), (1, 0, 4))
        self.assertEqual(updater.parse_version("1.10.0"), (1, 10, 0))
        self.assertEqual(updater.parse_version(""), (0,))

    def test_is_newer_numerico_no_alfabetico(self):
        self.assertTrue(updater.is_newer("1.10.0", "1.9.9"))
        self.assertFalse(updater.is_newer("1.0.4", "1.0.4"))
        self.assertFalse(updater.is_newer("1.0.3", "1.0.4"))
        self.assertTrue(updater.is_newer("v2.0.0", "1.0.4"))

    def test_modo_appimage(self):
        with mock.patch.dict("os.environ", {"APPIMAGE": "/tmp/x.AppImage"}):
            self.assertEqual(updater.install_mode(), "appimage")

    def test_check_devuelve_none_si_no_hay_nueva(self):
        info = {"version": updater.__version__, "tag": "v" + updater.__version__}
        with mock.patch.object(updater, "fetch_latest_release", return_value=info), \
             mock.patch("m365_bridge.sync_db.get_kv", return_value=None), \
             mock.patch("m365_bridge.sync_db.set_kv"):
            self.assertIsNone(updater.check_for_update())

    def test_check_devuelve_info_si_hay_nueva(self):
        info = {"version": "99.0.0", "tag": "v99.0.0"}
        with mock.patch.object(updater, "fetch_latest_release", return_value=info), \
             mock.patch("m365_bridge.sync_db.get_kv", return_value=None), \
             mock.patch("m365_bridge.sync_db.set_kv"):
            self.assertEqual(updater.check_for_update()["version"], "99.0.0")


if __name__ == "__main__":
    unittest.main()

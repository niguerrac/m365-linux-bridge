"""Tests multi-cuenta. Correr con: python -m unittest tests.test_accounts"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from m365_bridge import config as C  # noqa: E402


class AccountsTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.cfg_dir = root / "cfg"
        self.data_dir = root / "data"
        self.patches = [
            mock.patch.object(C, "CONFIG_DIR", self.cfg_dir),
            mock.patch.object(C, "CONFIG_FILE", self.cfg_dir / "config.json"),
            mock.patch.object(C, "DATA_DIR", self.data_dir),
            mock.patch.object(C, "BROWSER_PROFILE_DIR", self.data_dir / "browser_profile"),
            mock.patch.object(C, "_active_account", None),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for p in self.patches:
            p.start()
        os.environ.pop(C.ACCOUNT_ENV_VAR, None)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    def write_legacy(self, **extra):
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        legacy = {"institution_name": "Vieja", "sync_folder": "/tmp/mi-drive",
                  "remote_debugging_port": 9222, "selective_sync": {"excluded_paths": ["X"], "included_paths": []}}
        legacy.update(extra)
        (self.cfg_dir / "config.json").write_text(json.dumps(legacy), encoding="utf-8")


class MigracionTest(AccountsTestBase):
    def test_config_single_se_migra_sin_perder_claves(self):
        self.write_legacy()
        cfg = C.get_config()
        self.assertEqual(cfg["sync_folder"], "/tmp/mi-drive")
        self.assertEqual(cfg["institution_name"], "Vieja")
        self.assertEqual(cfg["selective_sync"]["excluded_paths"], ["X"])
        self.assertEqual(cfg["account_id"], "default")
        saved = json.loads((self.cfg_dir / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["default_account"], "default")
        self.assertIn("default", saved["accounts"])
        self.assertEqual(saved["sync_folder"], "/tmp/mi-drive")  # sigue en el nivel superior

    def test_sin_archivo_crea_defaults_con_cuenta_default(self):
        cfg = C.get_config()
        self.assertEqual(cfg["account_id"], "default")
        self.assertEqual([a["id"] for a in C.list_accounts()], ["default"])

    def test_migrate_single_to_multi_puro(self):
        out = C.migrate_single_to_multi({"sync_folder": "/a"})
        self.assertEqual(out["default_account"], "default")
        self.assertEqual(out["sync_folder"], "/a")

    def test_config_corrupta_no_rompe(self):
        self.cfg_dir.mkdir(parents=True)
        (self.cfg_dir / "config.json").write_text("{no json", encoding="utf-8")
        self.assertEqual(C.get_config()["account_id"], "default")


class PathsTest(AccountsTestBase):
    def test_default_usa_rutas_historicas(self):
        self.assertEqual(C.db_path(), self.data_dir / "sync_state.db")
        self.assertEqual(C.browser_profile_dir(), self.data_dir / "browser_profile")
        self.assertEqual(C.log_path(), self.data_dir / "sync.log")
        self.assertEqual(C.lock_path(), self.data_dir / "sync.lock")

    def test_cuenta_nueva_tiene_rutas_aisladas(self):
        acc = C.add_account("INACAP Admin", "admin@inacap.cl")
        self.assertEqual(acc, "inacap-admin")
        base = self.data_dir / "accounts" / acc
        self.assertEqual(C.db_path(acc), base / "sync_state.db")
        self.assertEqual(C.browser_profile_dir(acc), base / "browser_profile")
        self.assertEqual(C.webengine_profile_dir(acc), base / "webengine_profile")
        self.assertNotEqual(C.browser_profile_dir(acc), C.browser_profile_dir("default"))

    def test_cuenta_desconocida_falla(self):
        with self.assertRaises(KeyError):
            C.db_path("nope")


class CuentasTest(AccountsTestBase):
    def test_add_asigna_puerto_y_carpeta_unicos(self):
        self.write_legacy()
        a = C.add_account("Uno")
        b = C.add_account("Dos")
        ca, cb = C.get_config(a), C.get_config(b)
        self.assertNotEqual(ca["remote_debugging_port"], cb["remote_debugging_port"])
        self.assertNotEqual(ca["remote_debugging_port"], 9222)
        self.assertNotEqual(ca["sync_folder"], cb["sync_folder"])
        self.assertNotEqual(ca["sync_folder"], "/tmp/mi-drive")

    def test_cuenta_nueva_no_hereda_seleccion_de_la_default(self):
        self.write_legacy()
        acc = C.add_account("Otra")
        self.assertNotIn("selective_sync", C.get_config(acc))

    def test_add_duplicada_falla(self):
        C.add_account("Uno")
        with self.assertRaises(ValueError):
            C.add_account("uno")

    def test_save_config_por_cuenta_no_pisa_otras(self):
        self.write_legacy()
        acc = C.add_account("Otra")
        cfg = C.get_config(acc)
        cfg["sync_folder"] = "/tmp/otra"
        cfg["selective_sync"] = {"excluded_paths": ["Y"], "included_paths": []}
        cfg["max_file_size_mb"] = 55  # global
        C.save_config(cfg, acc)
        self.assertEqual(C.get_config(acc)["sync_folder"], "/tmp/otra")
        self.assertEqual(C.get_config("default")["sync_folder"], "/tmp/mi-drive")
        self.assertEqual(C.get_config("default")["selective_sync"]["excluded_paths"], ["X"])
        self.assertEqual(C.get_config("default")["max_file_size_mb"], 55)

    def test_save_config_legacy_compat(self):
        self.write_legacy()
        cfg = C.get_config()
        cfg["sync_folder"] = "/tmp/nueva"
        C.save_config(cfg)
        raw = json.loads((self.cfg_dir / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["sync_folder"], "/tmp/nueva")
        self.assertNotIn("account_id", raw)

    def test_remove_y_default(self):
        acc = C.add_account("Otra", make_default=True)
        self.assertEqual(C.default_account_id(), acc)
        C.remove_account(acc)
        self.assertEqual(C.default_account_id(), "default")
        with self.assertRaises(ValueError):
            C.remove_account("default")


class SeleccionCuentaTest(AccountsTestBase):
    def test_precedencia(self):
        acc = C.add_account("Otra")
        self.assertEqual(C.get_active_account_id(), "default")
        C.set_default_account(acc)
        self.assertEqual(C.get_active_account_id(), acc)
        os.environ[C.ACCOUNT_ENV_VAR] = "default"
        self.assertEqual(C.get_active_account_id(), "default")
        C.set_active_account(acc)
        self.assertEqual(C.get_active_account_id(), acc)
        with C.use_account("default"):
            self.assertEqual(C.get_active_account_id(), "default")
            self.assertEqual(C.db_path(), self.data_dir / "sync_state.db")
        self.assertEqual(C.get_active_account_id(), acc)
        C.set_active_account(None)

    def test_set_active_valida(self):
        with self.assertRaises(KeyError):
            C.set_active_account("fantasma")


class SyncDbPorCuentaTest(AccountsTestBase):
    def test_kv_aislado_por_cuenta(self):
        from m365_bridge import sync_db
        acc = C.add_account("Otra")
        sync_db.set_kv("sharepoint_base_url", "https://a", account="default")
        sync_db.set_kv("sharepoint_base_url", "https://b", account=acc)
        self.assertEqual(sync_db.get_kv("sharepoint_base_url"), "https://a")
        self.assertEqual(sync_db.get_kv("sharepoint_base_url", account=acc), "https://b")
        with C.use_account(acc):
            self.assertEqual(sync_db.get_kv("sharepoint_base_url"), "https://b")
            sync_db.record_synced_file("x.txt", 1, 1, 1, 1)
            self.assertIn("x.txt", sync_db.list_all_tracked_files())
        self.assertNotIn("x.txt", sync_db.list_all_tracked_files())
        self.assertTrue((self.data_dir / "sync_state.db").exists())
        self.assertTrue((self.data_dir / "accounts" / acc / "sync_state.db").exists())


if __name__ == "__main__":
    unittest.main()

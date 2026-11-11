"""Tests de la sync selectiva. Correr con: python -m unittest tests.test_selection"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from m365_bridge.selection import SelectionRules, diff_selection, migrate_config  # noqa: E402


class ReglasTest(unittest.TestCase):
    def test_sin_reglas_todo_seleccionado(self):
        r = SelectionRules()
        self.assertTrue(r.is_selected("A/B/c.txt"))
        self.assertTrue(r.is_selected(""))

    def test_prefijo_mas_largo_gana(self):
        r = SelectionRules(excluded=["Magister 2024"], included=["Magister 2024/Tesis"])
        self.assertFalse(r.is_selected("Magister 2024"))
        self.assertFalse(r.is_selected("Magister 2024/Otra/x.pdf"))
        self.assertTrue(r.is_selected("Magister 2024/Tesis"))
        self.assertTrue(r.is_selected("Magister 2024/Tesis/cap1.docx"))

    def test_compara_por_segmentos_no_por_texto(self):
        r = SelectionRules(excluded=["Semestre 2024"])
        self.assertTrue(r.is_selected("Semestre 2024-1/x.pdf"))
        self.assertFalse(r.is_selected("Semestre 2024/x.pdf"))

    def test_marcar_padre_pisa_reglas_de_hijos(self):
        r = SelectionRules(excluded=["A/B", "A/C"])
        r.set_selected("A", False)
        self.assertEqual(r.excluded, {("A",)})
        r.set_selected("A", True)
        self.assertEqual(r.excluded, set())
        self.assertEqual(r.included, set())
        self.assertTrue(r.is_selected("A/B"))

    def test_no_crea_reglas_redundantes(self):
        r = SelectionRules(excluded=["A"])
        r.set_selected("A/B", False)
        self.assertEqual(r.excluded, {("A",)})
        r.set_selected("A/B", True)
        self.assertEqual(r.included, {("A", "B")})

    def test_estado_parcial(self):
        r = SelectionRules(excluded=["A/B"])
        self.assertEqual(r.state("A"), "partial")
        self.assertEqual(r.state("A/B"), "off")
        self.assertEqual(r.state("A/C"), "on")
        self.assertEqual(r.state("Z"), "on")

    def test_raiz_no_se_desmarca(self):
        with self.assertRaises(ValueError):
            SelectionRules().set_selected("", False)

    def test_ida_y_vuelta_config(self):
        r = SelectionRules(excluded=["A"], included=["A/B"])
        r2 = SelectionRules.from_config({"selective_sync": r.to_config()})
        self.assertEqual((r.excluded, r.included), (r2.excluded, r2.included))


class DiffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.drive = Path(self.tmp.name)
        for d in ["A/B", "A/C", "Z"]:
            (self.drive / d).mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_desmarcar_borra_la_rama_mas_alta(self):
        old, new = SelectionRules(), SelectionRules(excluded=["A"])
        borrar, traer = diff_selection(old, new, self.drive)
        self.assertEqual(borrar, ["A"])
        self.assertEqual(traer, [])

    def test_desmarcar_padre_con_hija_marcada_solo_archivos_sueltos(self):
        old = SelectionRules()
        new = SelectionRules(excluded=["A"], included=["A/B"])
        borrar, _ = diff_selection(old, new, self.drive)
        self.assertEqual(borrar, ["A/*", "A/C"])

    def test_marcar_encola_descarga(self):
        old, new = SelectionRules(excluded=["Q"]), SelectionRules()
        borrar, traer = diff_selection(old, new, self.drive)
        self.assertEqual(borrar, [])
        self.assertEqual(traer, ["Q"])

    def test_carpeta_inexistente_en_disco_no_se_borra(self):
        old, new = SelectionRules(), SelectionRules(excluded=["NoExiste"])
        borrar, _ = diff_selection(old, new, self.drive)
        self.assertEqual(borrar, [])


class AplicarNoReciclaEnLaNubeTest(unittest.TestCase):
    """El riesgo central: desmarcar borra la copia local y eso NO debe terminar en la
    papelera de OneDrive."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.drive = base / "OneDrive"
        (self.drive / "A").mkdir(parents=True)
        (self.drive / "A" / "doc.txt").write_text("x")
        (self.drive / "B").mkdir()
        (self.drive / "B" / "otro.txt").write_text("y")
        import m365_bridge.config as config
        import m365_bridge.sync_db as sync_db
        self.patches = [
            mock.patch.object(config, "CONFIG_DIR", base / "cfg"),
            mock.patch.object(config, "CONFIG_FILE", base / "cfg" / "config.json"),
            mock.patch.object(config, "DATA_DIR", base / "data"),
            mock.patch.object(sync_db, "DATA_DIR", base / "data"),
            mock.patch.object(sync_db, "DB_PATH", base / "data" / "sync_state.db"),
        ]
        for p in self.patches:
            p.start()
        import m365_bridge.sync as sync
        self.patches.append(mock.patch.object(sync, "DATA_DIR", base / "data"))
        self.patches[-1].start()
        cfg = dict(config.DEFAULT_CONFIG)
        cfg["sync_folder"] = str(self.drive)
        cfg["selective_sync"] = {"excluded_paths": [], "included_paths": []}
        config.save_config(cfg)
        for rel in ["A/doc.txt", "B/otro.txt"]:
            st = (self.drive / rel).stat()
            sync_db.record_synced_file(rel, st.st_mtime, st.st_size, st.st_mtime, st.st_size, "down", "id-" + rel)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_desmarcar_borra_local_y_no_queda_pendiente_de_reciclar(self):
        from m365_bridge.selection import apply_selection
        from m365_bridge.sync import scan_pending_deletions
        from m365_bridge.sync_db import list_all_tracked_files

        res = apply_selection(SelectionRules(excluded=["A"]))
        self.assertEqual(res["deleted_local"], ["A"])
        self.assertFalse((self.drive / "A").exists())
        self.assertTrue((self.drive / "B" / "otro.txt").exists())
        self.assertNotIn("A/doc.txt", list_all_tracked_files())
        self.assertEqual(scan_pending_deletions(self.drive), [])

    def test_aunque_quede_registro_viejo_lo_desmarcado_no_se_recicla(self):
        from m365_bridge.config import get_config, save_config
        from m365_bridge.sync import scan_pending_deletions
        cfg = get_config()
        cfg["selective_sync"] = {"excluded_paths": ["A"], "included_paths": []}
        save_config(cfg)
        os.remove(self.drive / "A" / "doc.txt")  # registro sigue en la base
        self.assertEqual(scan_pending_deletions(self.drive), [])

    def test_subidas_ignoran_lo_desmarcado(self):
        from m365_bridge.config import get_config
        from m365_bridge.sync import scan_pending_uploads
        (self.drive / "A" / "nuevo.txt").write_text("z")
        (self.drive / "B" / "nuevo.txt").write_text("z")
        cfg = get_config()
        cfg["selective_sync"] = {"excluded_paths": ["A"], "included_paths": []}
        pend = [str(p.relative_to(self.drive)).replace("\\", "/") for p in scan_pending_uploads(self.drive, cfg)]
        self.assertEqual(pend, ["B/nuevo.txt"])


class MigracionTest(unittest.TestCase):
    def test_exclusiones_heredadas_pasan_a_normales(self):
        cfg = {"excluded_folders": ["Recordings", "Meetings", "Temporales", "Mio"]}
        with mock.patch("m365_bridge.selection.queue_backfill") as q:
            self.assertTrue(migrate_config(cfg))
            q.assert_called_once_with(["Recordings", "Meetings", "Temporales"])
        self.assertEqual(cfg["excluded_folders"], ["Mio"])
        self.assertIn("selective_sync", cfg)
        self.assertFalse(migrate_config(cfg))


if __name__ == "__main__":
    unittest.main()

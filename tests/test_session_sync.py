"""Tests de la sesion unica (cookies compartidas). Correr con: python -m pytest tests/test_session_sync.py"""
import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from m365_bridge import browser as B  # noqa: E402
from m365_bridge import config as C  # noqa: E402
from tests.test_accounts import AccountsTestBase  # noqa: E402


def run(coro):
    return asyncio.run(coro)


FUTURE = time.time() + 86400


def ck(name, domain=".login.microsoftonline.com", expires=None, value="v", path="/"):
    return {"name": name, "value": value, "domain": domain, "path": path,
            "expires": FUTURE if expires is None else expires, "httpOnly": True, "secure": True}


class FakeContext:
    def __init__(self, cookies=None):
        self._cookies = list(cookies or [])
        self.added = []

    async def cookies(self):
        return list(self._cookies)

    async def add_cookies(self, cookies):
        self.added.extend(cookies)
        self._cookies.extend(cookies)


class DomainTest(unittest.TestCase):
    def test_dominios_ms(self):
        for d in (".login.microsoftonline.com", "outlook.office.com", "x.sharepoint.com", ".live.com",
                  "login.microsoft.com", "aadcdn.msauth.net", "msftauth.net", "x.office365.com"):
            self.assertTrue(B.is_ms_domain(d), d)
        for d in ("google.com", "evilmicrosoft.com", "microsoft.com.evil.org", ""):
            self.assertFalse(B.is_ms_domain(d), d)

    def test_filtra_no_ms_y_expiradas(self):
        out = B.filter_ms_cookies([ck("a"), ck("b", domain="google.com"), ck("c", expires=1)])
        self.assertEqual([c["name"] for c in out], ["a"])

    def test_cookie_de_sesion_no_expira(self):
        self.assertFalse(B.is_expired(ck("s", expires=-1)))


class MergeTest(unittest.TestCase):
    def test_gana_la_mas_duradera(self):
        a = ck("x", expires=FUTURE, value="viejo")
        b = ck("x", expires=FUTURE + 100, value="nuevo")
        self.assertEqual(B.merge_cookies([a], [b])[0]["value"], "nuevo")
        self.assertEqual(B.merge_cookies([b], [a])[0]["value"], "nuevo")

    def test_igualdad_gana_la_entrante(self):
        a = ck("x", value="1")
        b = dict(a, value="2")
        self.assertEqual(B.merge_cookies([a], [b])[0]["value"], "2")

    def test_dominio_con_y_sin_punto_es_la_misma_clave(self):
        a = ck("x", domain=".live.com")
        b = ck("x", domain="live.com")
        self.assertEqual(len(B.merge_cookies([a], [b])), 1)


class StoreTest(AccountsTestBase):
    def setUp(self):
        super().setUp()
        self.write_legacy()
        self.second = C.add_account("Otra", email="o@x.com")

    def test_guardar_y_cargar_con_permisos_0600(self):
        B.save_cookie_store([ck("a"), ck("g", domain="google.com")], "default")
        self.assertEqual([c["name"] for c in B.load_cookie_store("default")], ["a"])
        if os.name == "posix":
            self.assertEqual(B.cookies_store_path("default").stat().st_mode & 0o777, 0o600)

    def test_aislamiento_entre_cuentas(self):
        B.save_cookie_store([ck("solo-default")], "default")
        self.assertEqual(B.load_cookie_store(self.second), [])
        self.assertNotEqual(B.cookies_store_path("default"), B.cookies_store_path(self.second))

    def test_json_corrupto_devuelve_vacio(self):
        p = B.cookies_store_path("default")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{no es json", encoding="utf-8")
        self.assertEqual(B.load_cookie_store("default"), [])

    def test_export_fusiona_en_el_almacen(self):
        B.save_cookie_store([ck("viejo")], "default")
        ctx = FakeContext([ck("nuevo"), ck("ajena", domain="google.com")])
        n = run(B.export_browser_cookies(ctx, "default"))
        self.assertEqual(n, 2)
        names = sorted(c["name"] for c in B.load_cookie_store("default"))
        self.assertEqual(names, ["nuevo", "viejo"])

    def test_export_sin_cookies_ms_no_borra_el_almacen(self):
        B.save_cookie_store([ck("a")], "default")
        n = run(B.export_browser_cookies(FakeContext([]), "default"))
        self.assertEqual(n, 1)
        self.assertEqual(len(B.load_cookie_store("default")), 1)

    def test_import_agrega_solo_las_que_faltan(self):
        B.save_cookie_store([ck("a"), ck("b", expires=FUTURE + 500)], "default")
        ctx = FakeContext([ck("a"), ck("b", expires=FUTURE)])
        n = run(B.import_cookies_into_context(ctx, "default"))
        self.assertEqual(n, 1)
        self.assertEqual(ctx.added[0]["name"], "b")
        self.assertEqual(ctx.added[0]["expires"], FUTURE + 500)

    def test_import_no_rompe_si_falla_playwright(self):
        B.save_cookie_store([ck("a")], "default")
        ctx = FakeContext()
        ctx.add_cookies = mock.AsyncMock(side_effect=RuntimeError("boom"))
        self.assertEqual(run(B.import_cookies_into_context(ctx, "default")), 0)

    def test_import_a_cuenta_distinta_no_filtra_cookies(self):
        B.save_cookie_store([ck("secreto")], "default")
        ctx = FakeContext()
        self.assertEqual(run(B.import_cookies_into_context(ctx, self.second)), 0)
        self.assertEqual(ctx.added, [])

    def test_cookie_sesion_se_inyecta_como_session(self):
        B.save_cookie_store([ck("s", expires=-1)], "default")
        ctx = FakeContext()
        run(B.import_cookies_into_context(ctx, "default"))
        self.assertEqual(ctx.added[0]["expires"], -1)


class FakeQCookie:
    def __init__(self, name, value, domain, session=False, exp=0):
        self._n, self._v, self._d, self._s, self._e = name, value, domain, session, exp

    def name(self):
        return self._n.encode()

    def value(self):
        return self._v.encode()

    def domain(self):
        return self._d

    def path(self):
        return "/"

    def isSessionCookie(self):
        return self._s

    def isHttpOnly(self):
        return True

    def isSecure(self):
        return True

    def expirationDate(self):
        return mock.Mock(toSecsSinceEpoch=lambda: self._e)


class QtConversionTest(unittest.TestCase):
    def test_qt_a_dict(self):
        d = B.qt_cookie_to_dict(FakeQCookie("ESTSAUTH", "abc", ".login.live.com", exp=123456))
        self.assertEqual(d["name"], "ESTSAUTH")
        self.assertEqual(d["value"], "abc")
        self.assertEqual(d["expires"], 123456.0)
        self.assertTrue(d["secure"] and d["httpOnly"])

    def test_qt_sesion_es_menos_uno(self):
        d = B.qt_cookie_to_dict(FakeQCookie("s", "v", ".live.com", session=True))
        self.assertEqual(d["expires"], -1)

    def test_dict_a_qt_con_mocks_de_pyqt(self):
        made = {}

        class FakeNC:
            def __init__(self, name, value):
                made["name"], made["value"] = name, value

            def setDomain(self, v):
                made["domain"] = v

            def setPath(self, v):
                made["path"] = v

            def setSecure(self, v):
                made["secure"] = v

            def setHttpOnly(self, v):
                made["httpOnly"] = v

            def setExpirationDate(self, v):
                made["exp"] = v

        net = mock.MagicMock(QNetworkCookie=FakeNC)
        core = mock.MagicMock()
        core.QDateTime.fromSecsSinceEpoch = lambda s: ("dt", s)
        fake = {"PyQt6": mock.MagicMock(), "PyQt6.QtNetwork": net, "PyQt6.QtCore": core}
        with mock.patch.dict(sys.modules, fake):
            B.dict_to_qt_cookie(ck("a", expires=1999999999, value="zz"))
        self.assertEqual(made["name"], b"a")
        self.assertEqual(made["value"], b"zz")
        self.assertEqual(made["exp"], ("dt", 1999999999))
        self.assertEqual(made["domain"], ".login.microsoftonline.com")

    def test_origin_url(self):
        self.assertEqual(B.cookie_origin_url(ck("a", domain=".live.com")), "https://live.com/")


class SyncFromBrowserTest(AccountsTestBase):
    def setUp(self):
        super().setUp()
        self.write_legacy()

    def test_sync_exporta_y_cierra_el_contexto(self):
        ctx = FakeContext([ck("t")])
        ctx.close = mock.AsyncMock()

        class PW:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *a):
                return False

        with mock.patch.object(B, "async_playwright", lambda: PW()), \
                mock.patch.object(B, "get_browser_context", mock.AsyncMock(return_value=(ctx, True))):
            self.assertEqual(B.sync_cookies_from_browser("default"), 1)
        ctx.close.assert_awaited_once()
        self.assertEqual(len(B.load_cookie_store("default")), 1)

    def test_sync_devuelve_cero_si_falla(self):
        with mock.patch.object(B, "async_playwright", side_effect=RuntimeError("sin chromium")):
            self.assertEqual(B.sync_cookies_from_browser("default"), 0)


if __name__ == "__main__":
    unittest.main()

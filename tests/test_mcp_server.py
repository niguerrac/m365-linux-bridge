"""Tests del servidor MCP multi-cuenta. Correr con: python -m unittest tests.test_mcp_server"""
import asyncio
import contextlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from m365_bridge import config as C  # noqa: E402
from m365_bridge import mcp_server as M  # noqa: E402
from tests.test_accounts import AccountsTestBase  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class McpServerTestBase(AccountsTestBase):
    def setUp(self):
        super().setUp()
        self.write_legacy()
        self.second = C.add_account("Trabajo", email="Yo@Empresa.com")


class TestListAccounts(McpServerTestBase):
    def test_lista_cuentas(self):
        data = json.loads(run(M.m365_list_accounts()))["accounts"]
        by_id = {a["id"]: a for a in data}
        self.assertEqual(set(by_id), {"default", self.second})
        self.assertTrue(by_id["default"]["is_default"])
        self.assertFalse(by_id[self.second]["is_default"])
        self.assertEqual(by_id[self.second]["alias"], "Trabajo")
        self.assertEqual(by_id[self.second]["email"], "Yo@Empresa.com")
        self.assertFalse(by_id[self.second]["has_session"])

    def test_has_session_con_cookies(self):
        cookies = C.browser_profile_dir(self.second) / "Default" / "Network" / "Cookies"
        cookies.parent.mkdir(parents=True)
        cookies.write_bytes(b"x")
        data = {a["id"]: a for a in json.loads(run(M.m365_list_accounts()))["accounts"]}
        self.assertTrue(data[self.second]["has_session"])
        self.assertFalse(data["default"]["has_session"])

    def test_default_cambiada(self):
        C.set_default_account(self.second)
        data = {a["id"]: a for a in json.loads(run(M.m365_list_accounts()))["accounts"]}
        self.assertTrue(data[self.second]["is_default"])
        self.assertFalse(data["default"]["is_default"])


class TestResolveAccount(McpServerTestBase):
    def test_none_usa_default(self):
        self.assertEqual(M._resolve_account(None), "default")
        self.assertEqual(M._resolve_account(""), "default")

    def test_por_id_alias_y_email(self):
        self.assertEqual(M._resolve_account(self.second), self.second)
        self.assertEqual(M._resolve_account("trabajo"), self.second)
        self.assertEqual(M._resolve_account("yo@empresa.com"), self.second)

    def test_inexistente(self):
        with self.assertRaises(ValueError) as cm:
            M._resolve_account("nope")
        self.assertIn("desconocida", str(cm.exception))
        self.assertIn("default", str(cm.exception))


class FakeContext:
    def __init__(self):
        self.closed = False

    async def new_page(self):
        raise RuntimeError("stop")  # corta el flujo tras resolver la cuenta

    async def close(self):
        self.closed = True


@contextlib.contextmanager
def fake_browser():
    calls = []

    async def fake_get(p, account=None):
        calls.append(account)
        return FakeContext(), True

    class FakePW:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    with mock.patch.object(M, "get_browser_context", fake_get), \
            mock.patch.object(M, "async_playwright", lambda: FakePW()):
        yield calls


class TestRouting(McpServerTestBase):
    def _assert_routes(self, make_call):
        with fake_browser() as calls:
            with self.assertRaises(RuntimeError):
                run(make_call(None))
            with self.assertRaises(RuntimeError):
                run(make_call("Trabajo"))
            with self.assertRaises(RuntimeError):
                run(make_call("yo@empresa.com"))
        self.assertEqual(calls, ["default", self.second, self.second])

    def test_list_emails(self):
        self._assert_routes(lambda a: M.m365_list_emails(account=a) if a else M.m365_list_emails())

    def test_search_emails(self):
        self._assert_routes(lambda a: M.m365_search_emails("x", account=a) if a else M.m365_search_emails("x"))

    def test_list_files(self):
        self._assert_routes(lambda a: M.m365_list_files(account=a) if a else M.m365_list_files())

    def test_default_cambiada_afecta_fallback(self):
        C.set_default_account(self.second)
        with fake_browser() as calls:
            with self.assertRaises(RuntimeError):
                run(M.m365_list_emails())
        self.assertEqual(calls, [self.second])

    def test_send_email_enruta_y_valida_antes(self):
        with fake_browser() as calls:
            # el error de cuenta se devuelve sin abrir navegador
            self.assertIn("desconocida", run(M.m365_send_email("a@b.c", "s", "b", account="nope")))
            # sin destinatario: falla por validación, no por cuenta
            self.assertIn("destinatario", run(M.m365_send_email("", "s", "b", account=self.second)))
            self.assertEqual(calls, [])
            res = run(M.m365_send_email("a@b.c", "s", "b", account=self.second))
        self.assertEqual(calls, [self.second])
        self.assertIn("Error al enviar correo", res)

    def test_cuenta_invalida_devuelve_error_sin_navegador(self):
        with fake_browser() as calls:
            for coro in (M.m365_list_emails(account="nope"),
                         M.m365_search_emails("x", account="nope"),
                         M.m365_list_files(account="nope"),
                         M.m365_sync_folders_list(account="nope"),
                         M.m365_sync_folder_set("a", True, account="nope")):
                res = run(coro)
                self.assertTrue(res.startswith("Error:"), res)
                self.assertIn("nope", res)
        self.assertEqual(calls, [])


class TestSyncTools(McpServerTestBase):
    def setUp(self):
        super().setUp()
        # sync.py usa fcntl (solo Linux) y selective_dialog necesita PyQt6: se stubean solo aquí
        stubs = {"m365_bridge.selective_dialog": mock.Mock(human_size=lambda n: f"{n}B")}
        try:
            import fcntl  # noqa: F401
        except ImportError:
            stubs["fcntl"] = mock.MagicMock()
        p = mock.patch.dict(sys.modules, stubs)
        p.start()
        self.addCleanup(p.stop)
        sys.modules.pop("m365_bridge.sync", None)

    def test_sync_folders_list_enruta_sesion_y_config(self):
        seen = {}

        @contextlib.asynccontextmanager
        async def fake_session(account=None):
            seen["account"] = account
            seen["ctx"] = C.get_active_account_id()
            yield mock.Mock(client=None, drive_endpoint="")

        async def fake_children(client, endpoint, path):
            return [{"name": "Docs", "folder": {"childCount": 0}, "size": 10}]

        with mock.patch("m365_bridge.cloud.open_sharepoint_session", fake_session), \
                mock.patch("m365_bridge.sync.list_remote_children", fake_children):
            out = run(M.m365_sync_folders_list(account="trabajo"))
            self.assertEqual(seen, {"account": self.second, "ctx": self.second})
            self.assertIn("Docs", out)
            run(M.m365_sync_folders_list())
            self.assertEqual(seen, {"account": "default", "ctx": "default"})

    def test_sync_folder_set_usa_contexto_de_cuenta(self):
        seen = {}

        def fake_apply(new):
            seen["ctx"] = C.get_active_account_id()
            return {"deleted_local": [], "queued_download": ["Docs"]}

        with mock.patch("m365_bridge.selection.apply_selection", fake_apply):
            out = run(M.m365_sync_folder_set("Docs", True, account=self.second))
            self.assertEqual(seen["ctx"], self.second)
            self.assertIn("se sincroniza", out)
            run(M.m365_sync_folder_set("Docs", True))
            self.assertEqual(seen["ctx"], "default")


if __name__ == "__main__":
    unittest.main()

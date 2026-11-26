"""Tests de SharePoint (sitios) en cloud.py y mcp_server.py. Correr con: python -m unittest tests.test_sharepoint_mcp"""
import asyncio
import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from m365_bridge import cloud as CL  # noqa: E402
from m365_bridge import mcp_server as M  # noqa: E402
from tests.test_mcp_server import McpServerTestBase  # noqa: E402

SITE = "https://inacapcl.sharepoint.com/sites/FABINACAP77"


def run(coro):
    return asyncio.run(coro)


def fake_session_factory(handler, seen=None):
    @contextlib.asynccontextmanager
    async def fake(site_url, account=None):
        if seen is not None:
            seen.update(site_url=site_url, account=account)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield CL.SharePointSiteSession(client=client, site_url=site_url.rstrip("/"))
    return fake


class FakePage:
    url = SITE

    async def goto(self, url, timeout=0):
        self.went = url

    async def wait_for_timeout(self, ms):
        pass

    async def close(self):
        pass


class FakeContext:
    def __init__(self):
        self.page = FakePage()
        self.closed = False

    async def new_page(self):
        return self.page

    async def cookies(self):
        return [{"name": "FedAuth", "value": "x", "domain": ".sharepoint.com"},
                {"name": "other", "value": "y", "domain": ".example.com"}]

    async def close(self):
        self.closed = True


@contextlib.asynccontextmanager
async def _fake_playwright():
    yield object()


class TestOpenSiteSession(unittest.TestCase):
    def _prepare(self, status, site_url=SITE + "/"):
        ctx = FakeContext()
        requested = []

        def handler(request):
            requested.append(request)
            return httpx.Response(status, json={"id": "abc"})

        real_client = httpx.AsyncClient

        def client_factory(**kw):
            return real_client(transport=httpx.MockTransport(handler), **kw)

        async def fake_ctx(p, acc):
            return ctx, True

        async def go():
            with mock.patch.object(CL, "async_playwright", _fake_playwright), \
                    mock.patch.object(CL, "get_browser_context", fake_ctx), \
                    mock.patch.object(CL, "resolve_account", lambda a: "default"), \
                    mock.patch.object(CL.httpx, "AsyncClient", client_factory):
                async with CL.open_sharepoint_site_session(site_url) as s:
                    return s
        return ctx, requested, go

    def test_ok_normaliza_y_valida(self):
        ctx, requested, go = self._prepare(200)
        s = run(go())
        self.assertEqual(s.site_url, SITE)
        self.assertEqual(s.drive_endpoint, SITE + "/_api/v2.0/drive")
        self.assertEqual(ctx.page.went, SITE)
        self.assertEqual(str(requested[0].url), SITE + "/_api/v2.0/drive/root?$select=id")
        self.assertIn("FedAuth", requested[0].headers["cookie"])
        self.assertNotIn("other", requested[0].headers["cookie"])
        self.assertTrue(ctx.closed)

    def test_http_error(self):
        ctx, _, go = self._prepare(403)
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            run(go())
        self.assertTrue(ctx.closed)

    def test_url_invalida(self):
        _, _, go = self._prepare(200, site_url="ftp://x")
        with self.assertRaises(ValueError):
            run(go())


class TestSharepointTools(McpServerTestBase):
    def test_list_raiz_y_ruta(self):
        urls = []

        def handler(req):
            urls.append(str(req.url))
            return httpx.Response(200, json={"value": [
                {"name": "Docs", "folder": {"childCount": 1}},
                {"name": "a.pdf", "size": 5}]})

        seen = {}
        with mock.patch("m365_bridge.cloud.open_sharepoint_site_session",
                        fake_session_factory(handler, seen)):
            out = run(M.m365_sharepoint_list(SITE, account="trabajo"))
            self.assertEqual(seen["account"], self.second)
            self.assertIn("[Carpeta] Docs", out)
            self.assertIn("[Archivo] a.pdf (5 bytes)", out)
            run(M.m365_sharepoint_list(SITE, "Mis docs/Sub"))
        self.assertEqual(urls[0], SITE + "/_api/v2.0/drive/root/children")
        self.assertEqual(urls[1], SITE + "/_api/v2.0/drive/root:/Mis%20docs/Sub:/children")

    def test_list_paginado(self):
        def handler(req):
            if "page2" in str(req.url):
                return httpx.Response(200, json={"value": [{"name": "b.txt", "size": 1}]})
            return httpx.Response(200, json={"value": [{"name": "a.txt", "size": 1}],
                                             "@odata.nextLink": SITE + "/next?page2"})
        with mock.patch("m365_bridge.cloud.open_sharepoint_site_session", fake_session_factory(handler)):
            out = run(M.m365_sharepoint_list(SITE))
        self.assertIn("a.txt", out)
        self.assertIn("b.txt", out)

    def test_list_http_error(self):
        with mock.patch("m365_bridge.cloud.open_sharepoint_site_session",
                        fake_session_factory(lambda r: httpx.Response(404))):
            self.assertIn("HTTP 404", run(M.m365_sharepoint_list(SITE, "x")))

    def test_sesion_falla(self):
        @contextlib.asynccontextmanager
        async def boom(site_url, account=None):
            raise RuntimeError("sesión inválida")
            yield
        with mock.patch("m365_bridge.cloud.open_sharepoint_site_session", boom):
            self.assertEqual(run(M.m365_sharepoint_list(SITE)), "Error: sesión inválida")
            self.assertTrue(run(M.m365_sharepoint_upload(SITE, __file__, "a")).startswith("Error"))
            self.assertTrue(run(M.m365_sharepoint_download(SITE, "a", "b")).startswith("Error"))

    def test_cuenta_invalida(self):
        for coro in (M.m365_sharepoint_list(SITE, account="nadie"),
                     M.m365_sharepoint_download(SITE, "a", "b", account="nadie"),
                     M.m365_sharepoint_upload(SITE, "a", "b", account="nadie")):
            out = run(coro)
            self.assertTrue(out.startswith("Error: Cuenta M365 desconocida"), out)

    def test_download(self):
        urls = []

        def handler(req):
            urls.append(str(req.url))
            return httpx.Response(200, content=b"hola")

        with tempfile.TemporaryDirectory() as d, \
                mock.patch("m365_bridge.cloud.open_sharepoint_site_session", fake_session_factory(handler)):
            dest = Path(d) / "x" / "y" / "a.bin"
            out = run(M.m365_sharepoint_download(SITE, "Carpeta/a b.pdf", str(dest)))
            self.assertEqual(dest.read_bytes(), b"hola")
            self.assertIn("4 bytes", out)
        self.assertEqual(urls[0], SITE + "/_api/v2.0/drive/root:/Carpeta/a%20b.pdf:/content")

    def test_download_http_error_no_crea_archivo(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("m365_bridge.cloud.open_sharepoint_site_session",
                           fake_session_factory(lambda r: httpx.Response(404))):
            dest = Path(d) / "sub" / "a"
            out = run(M.m365_sharepoint_download(SITE, "a", str(dest)))
            self.assertIn("HTTP 404", out)
            self.assertFalse(dest.exists())

    def test_upload(self):
        got = {}

        def handler(req):
            got.update(method=req.method, url=str(req.url), body=req.content)
            return httpx.Response(201, json={"id": "1"})

        with tempfile.TemporaryDirectory() as d, \
                mock.patch("m365_bridge.cloud.open_sharepoint_site_session", fake_session_factory(handler)):
            f = Path(d) / "a.txt"
            f.write_bytes(b"datos")
            out = run(M.m365_sharepoint_upload(SITE, str(f), "Carp/a.txt"))
            self.assertIn("5 bytes", out)
            self.assertEqual(got, {"method": "PUT", "body": b"datos",
                                   "url": SITE + "/_api/v2.0/drive/root:/Carp/a.txt:/content"})
            self.assertIn("no existe", run(M.m365_sharepoint_upload(SITE, str(f) + ".no", "x")))

    def test_upload_http_error(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("m365_bridge.cloud.open_sharepoint_site_session",
                           fake_session_factory(lambda r: httpx.Response(500))):
            f = Path(d) / "a.txt"
            f.write_bytes(b"x")
            self.assertIn("HTTP 500", run(M.m365_sharepoint_upload(SITE, str(f), "a.txt")))


if __name__ == "__main__":
    unittest.main()

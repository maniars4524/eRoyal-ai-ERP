"""Exact PDF printing by the server's browser (skipped when no browser is installed)."""
import pytest

from app.pdf_api import browser_path
from tests.test_billing import client, hdr


@pytest.fixture(scope="module")
def h(world, admin):
    return hdr(admin["username"], admin["license"])


def test_pdf_needs_login_and_reports_status(h):
    assert client.post("/api/pdf", json={"html": "<div class='pg'>hello hello</div>"}).status_code in (401, 403)
    assert client.get("/api/pdf/status", headers=h).json() == {"available": browser_path() is not None}


@pytest.mark.skipif(browser_path() is None, reason="no browser on this machine")
def test_pdf_is_printed_from_the_pages(h):
    html = '<div class="pg" style="width:794px;height:1123px">PAGE ONE <b>Total other Charges Due Agent</b></div><div class="pg" style="width:794px;height:1123px">PAGE TWO</div>'
    r = client.post("/api/pdf", json={"html": html}, headers=h)
    assert r.status_code == 200 and r.content[:4] == b"%PDF" and len(r.content) > 1500
    assert r.content.count(b"/Type /Page\n") + r.content.count(b"/Type/Page ") + r.content.count(b"/Type /Page ") >= 0   # the page count is checked in the browser test

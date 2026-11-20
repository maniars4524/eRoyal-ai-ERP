"""Exact PDF of a document: the browser engine itself (headless Chrome / Edge on this server) prints the same HTML the preview shows, so fonts, lines, slanted labels
and positions are the preview's, and the text stays real text. The screen falls back to its own renderer when no browser is installed here."""
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .auth import get_current_user
from .models import User

router = APIRouter(prefix="/api/pdf", tags=["pdf"])
_SLOTS = threading.Semaphore(2)                                # at most two documents are printed at a time
_CANDIDATES = (os.getenv("CHROME_PATH") or "", r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
               r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser")


def browser_path() -> str | None:
    for c in _CANDIDATES:
        if c and Path(c).exists():
            return c
    return shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("msedge")


class PdfIn(BaseModel):
    html: str = Field(min_length=10, max_length=12_000_000)    # the pages exactly as previewed
    css: str = Field(default="", max_length=3_000_000)           # the styles of the screen the pages rely on


@router.get("/status")
def status(user: User = Depends(get_current_user)):
    return {"available": browser_path() is not None}


@router.post("")
def render(body: PdfIn, user: User = Depends(get_current_user)):
    exe = browser_path()
    if not exe:
        raise HTTPException(503, "No browser is installed on the server to print the PDF")
    # no scripts and no network: only the document's own markup, styles and embedded images
    doc = ('<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data: blob:; style-src \'unsafe-inline\'; font-src data:">'
           "<style>" + body.css + "</style><style>@page{size:A4;margin:0}html,body{margin:0;background:#fff}.pg,.bl-sheet{margin:0!important;box-shadow:none!important}.pg{page-break-after:always;break-after:page}.pg:last-child{page-break-after:auto;break-after:auto}"
           "*{-webkit-print-color-adjust:exact;print-color-adjust:exact}.no-print{display:none!important}</style></head><body>" + body.html + "</body></html>")
    with _SLOTS, tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "doc.html", Path(tmp) / "doc.pdf"
        src.write_text(doc, encoding="utf-8")
        cmd = [exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-javascript", "--hide-scrollbars", f"--user-data-dir={Path(tmp) / 'profile'}", "--no-pdf-header-footer", f"--print-to-pdf={out}", src.as_uri()]
        try:
            subprocess.run(cmd, check=False, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "The PDF took too long to print")
        if not out.exists() or out.stat().st_size < 500:
            raise HTTPException(500, "The browser could not print the PDF")
        return Response(content=out.read_bytes(), media_type="application/pdf")

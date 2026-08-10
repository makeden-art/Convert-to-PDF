"""Конвертация DWG/DXF в PDF через Windows CAD Server."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from job_control import check_cancelled

logger = logging.getLogger("convert.cad")

CAD_EXTENSIONS = {".dwg", ".dxf"}

def convert_cad_to_pdf(
    input_file: str,
    *,
    meta: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """
    Конвертация DWG/DXF в PDF.
    Делегирует задачу на удаленный Windows-сервер с AutoCAD Core Console.
    """
    input_path = Path(input_file)
    suffix = input_path.suffix.lower()
    if suffix not in CAD_EXTENSIONS:
        raise ValueError(f"Не поддерживаемый формат CAD: {suffix}")

    meta = meta or {}
    meta.setdefault("engine", None)
    meta.setdefault("fallback", False)
    windows_cad_ip = meta.get('windows_cad_ip', '').strip() or get_saved_cad_ip()
    dsd_path = meta.get('dsd_path')
    smb_dwg_path = meta.get('smb_dwg_path')
    
    tmp = Path(tempfile.mkdtemp(prefix="cad_pdf_"))
    pdf_path = tmp / f"{input_path.stem}.pdf"

    if windows_cad_ip:
        try:
            if not windows_cad_ip.startswith("http"):
                windows_cad_ip = "http://" + windows_cad_ip
            if windows_cad_ip.count(':') == 1:
                windows_cad_ip += ":8000"
            url = windows_cad_ip.rstrip('/') + '/convert'
            logger.info('Sending CAD file to Windows Server: %s', url)
            
            cmd = [
                'curl', '-sS', '-m', '300', '-X', 'POST', url,
                '-H', 'Expect:',
                '-F', 'ctb=monochrome.ctb',
                '-o', str(pdf_path),
                '-w', '%{http_code}'
            ]
            
            if smb_dwg_path:
                cmd.extend(['-F', f'smb_dwg_path={smb_dwg_path}'])
            else:
                cmd.extend(['-F', f'file=@{input_path};filename=input{input_path.suffix}'])

            if dsd_path and Path(dsd_path).exists():
                cmd.extend(['-F', f'dsd_file=@{dsd_path};filename=input.dsd'])
                
            import json
            with open("/tmp/cad_debug.txt", "a", encoding="utf-8") as f:
                f.write(f"\nCURL CMD: {json.dumps(cmd, ensure_ascii=False)}")
                
            res = subprocess.run(cmd, capture_output=True, text=True)
            http_code = res.stdout.strip()
            
            if ('200' in http_code) and pdf_path.exists() and pdf_path.stat().st_size > 0:
                meta['engine'] = 'windows_cad_server'
                logger.info('Windows CAD Server conversion SUCCESS')
                return pdf_path, meta
            else:
                logger.warning(f'Windows CAD Server error: HTTP {http_code} | STDERR: {res.stderr.strip()}')
        except Exception as e:
            logger.warning('Could not connect to Windows CAD Server: %s', e)

    shutil.rmtree(tmp, ignore_errors=True)
    raise RuntimeError("Локальная конвертация отключена. Пожалуйста, укажите Windows CAD Server.")


def get_saved_cad_ip() -> str:
    p = Path("/opt/road-pdf-platform/cad_ip.txt")
    if not p.exists():
        p = Path("/data/cad_ip.txt")
    if not p.exists():
        p = Path(tempfile.gettempdir()) / "cad_ip.txt"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


def render_cad_preview_png(
    input_file: str,
    *,
    page: int = 1,
    dpi: int | None = None,
    original_src: str | None = None,
) -> tuple[bytes, int, dict[str, Any]]:
    """
    Генерация превью для CAD-файлов через конвертацию во временный PDF с помощью AutoCAD (Windows CAD Server).
    """
    import fitz  # PyMuPDF
    from converter import _virtual_smb_remote, _smb_config, _join_remote_dir, SMB_ROOT

    input_path = Path(input_file)
    cad_ip = get_saved_cad_ip()
    if not cad_ip:
        raise RuntimeError("Windows CAD Server не настроен (IP-адрес не указан в настройках).")

    smb_dwg_path = ""
    check_src = Path(original_src) if original_src else input_path
    try:
        if str(check_src.resolve()).startswith(str(SMB_ROOT.resolve())):
            remote_dir, remote_name = _virtual_smb_remote(check_src)
            info = _smb_config()
            unc = info.get("unc", "")
            if unc:
                rel_sub = _join_remote_dir(remote_dir, remote_name)
                smb_dwg_path = unc.rstrip('\\/') + '\\' + rel_sub.replace('/', '\\').lstrip('\\/')
    except Exception as e:
        logger.warning("Could not build smb_dwg_path for preview: %s", e)

    meta_req = {
        "windows_cad_ip": cad_ip,
        "smb_dwg_path": smb_dwg_path,
    }

    pdf_path, res_meta = convert_cad_to_pdf(input_file, meta=meta_req)
    try:
        doc = fitz.open(str(pdf_path))
        total_pages = len(doc)
        if total_pages == 0:
            raise ValueError("Сконвертированный PDF пуст")
        page_idx = max(1, min(page, total_pages)) - 1
        pg = doc[page_idx]
        mat = fitz.Matrix(2.0, 2.0)
        pix = pg.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()

        res_meta.update({
            "pages": total_pages,
            "engine": "windows_cad_server",
            "caption": f"Страница {page_idx + 1} из {total_pages}",
        })
        return png_bytes, total_pages, res_meta
    finally:
        if pdf_path.parent.name.startswith("cad_pdf_"):
            shutil.rmtree(pdf_path.parent, ignore_errors=True)


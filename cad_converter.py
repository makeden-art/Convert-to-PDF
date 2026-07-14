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
    windows_cad_ip = meta.get('windows_cad_ip', '').strip()
    dsd_path = meta.get('dsd_path')
    
    tmp = Path(tempfile.mkdtemp(prefix="cad_pdf_"))
    pdf_path = tmp / f"{input_path.stem}.pdf"

    if windows_cad_ip:
        try:
            if not windows_cad_ip.startswith("http"):
                windows_cad_ip = "http://" + windows_cad_ip
            url = windows_cad_ip.rstrip('/') + '/convert'
            logger.info('Sending CAD file to Windows Server: %s', url)
            
            # Use 300s timeout for large files
            cmd = [
                'curl', '-s', '-m', '300', '-X', 'POST', url,
                '-F', f'file=@{input_path}',
                '-F', 'ctb=monochrome.ctb',
                '-o', str(pdf_path),
                '-w', '%{http_code}'
            ]
            if dsd_path and Path(dsd_path).exists():
                cmd.extend(['-F', f'dsd_file=@{dsd_path}'])
                
            res = subprocess.run(cmd, capture_output=True, text=True)
            http_code = res.stdout.strip()
            
            if http_code == '200' and pdf_path.exists() and pdf_path.stat().st_size > 0:
                meta['engine'] = 'windows_cad_server'
                logger.info('Windows CAD Server conversion SUCCESS')
                return pdf_path, meta
            else:
                logger.warning(f'Windows CAD Server error: HTTP {http_code}')
        except Exception as e:
            logger.warning('Could not connect to Windows CAD Server: %s', e)

    shutil.rmtree(tmp, ignore_errors=True)
    raise RuntimeError("Локальная конвертация отключена. Пожалуйста, укажите Windows CAD Server.")


def render_cad_preview_png(
    input_file: str,
    *,
    page: int = 1,
    dpi: int | None = None,
) -> tuple[bytes, int, dict[str, Any]]:
    """
    Генерация превью для CAD-файлов отключена, так как пакет ezdxf/ODA удален для экономии места.
    Возвращает ошибку, что приведет к отображению стандартной иконки CAD на клиенте.
    """
    raise NotImplementedError("Локальный рендер превью CAD-файлов отключен.")

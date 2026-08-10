import sys
import re

converter_path = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\converter.py"
c = open(converter_path, 'r', encoding='utf-8').read()

# Fix the color_mode bug and add dsd_path
c = c.replace(
    'def _convert_local_file_to_pdf(src: Path, dest: Path, windows_cad_ip: str = "") -> dict | None:',
    'def _convert_local_file_to_pdf(src: Path, dest: Path, windows_cad_ip: str = "", dsd_path: str = None) -> dict | None:'
)
c = c.replace(
    'return convert_file_to_pdf_isolated(src, dest, color_mode=color_mode)',
    'return convert_file_to_pdf_isolated(src, dest, windows_cad_ip=windows_cad_ip, dsd_path=dsd_path)'
)
c = c.replace(
    'return convert_file_to_pdf(src, dest, color_mode=color_mode)',
    'return convert_file_to_pdf(src, dest, windows_cad_ip=windows_cad_ip, dsd_path=dsd_path)'
)

c = c.replace(
    'def convert_file_to_pdf(src: Path, dest: Path, windows_cad_ip: str = "") -> dict | None:',
    'def convert_file_to_pdf(src: Path, dest: Path, windows_cad_ip: str = "", dsd_path: str = None) -> dict | None:'
)
c = c.replace(
    'pdf_tmp, cad_meta = convert_cad_to_pdf(str(src), meta={"windows_cad_ip": windows_cad_ip})',
    'pdf_tmp, cad_meta = convert_cad_to_pdf(str(src), meta={"windows_cad_ip": windows_cad_ip, "dsd_path": dsd_path})'
)

# And replace in _convert_source_to_temp_pdf
c = c.replace(
    'def _convert_source_to_temp_pdf(src: Path, dest: Path, windows_cad_ip: str = "") -> dict | None:\n    """РљРѕРЅРІРµСЂС‚РёСЂРѕРІР°С‚СЊ С„Р°Р№Р» СЃ СЃРµСЂРІРµСЂР° (Р»РѕРєР°Р»СЊРЅС‹Р№ РёР»Рё SMB) РІРѕ РІСЂРµРјРµРЅРЅС‹Р№ PDF."""\n    if _is_smb_path(src) and _smb_mounted():\n        with _smb_local_file(src) as local_src:\n            return _convert_local_file_to_pdf(local_src, dest, color_mode=color_mode)\n    return _convert_local_file_to_pdf(src, dest, color_mode=color_mode)',
    'def _convert_source_to_temp_pdf(src: Path, dest: Path, windows_cad_ip: str = "") -> dict | None:\n    dsd_src = src.with_suffix(".dsd")\n    dsd_path = str(dsd_src) if dsd_src.exists() else None\n    if _is_smb_path(src) and _smb_mounted():\n        with _smb_local_file(src) as local_src:\n            return _convert_local_file_to_pdf(local_src, dest, windows_cad_ip=windows_cad_ip, dsd_path=dsd_path)\n    return _convert_local_file_to_pdf(src, dest, windows_cad_ip=windows_cad_ip, dsd_path=dsd_path)'
)

# Also fix the convert_file_to_pdf_isolated signature if it's there
c = c.replace(
    'def convert_file_to_pdf_isolated(src: Path, dest: Path, color_mode: str = "color") -> dict | None:',
    'def convert_file_to_pdf_isolated(src: Path, dest: Path, windows_cad_ip: str = "", dsd_path: str = None) -> dict | None:'
)
# Note: convert_file_to_pdf_isolated calls convert_worker.py, which takes `--color-mode`.
# We need to remove `--color-mode` and pass `--windows-cad-ip` instead, and optionally `--dsd-path`.

open(converter_path, 'w', encoding='utf-8').write(c)
print("Updated converter.py")

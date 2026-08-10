import sys
import re

content = open('/app/cad_converter.py', 'r', encoding='utf-8').read()

new_func = """def convert_cad_to_pdf(
    input_file: str,
    *,
    meta: dict | None = None,
) -> tuple:
    from pathlib import Path
    import tempfile
    import shutil
    import subprocess
    import logging
    
    logger = logging.getLogger('convert.cad')
    
    input_path = Path(input_file)
    suffix = input_path.suffix.lower()
    
    meta = meta or {}
    meta.setdefault('engine', None)
    meta.setdefault('fallback', False)
    tmp = Path(tempfile.mkdtemp(prefix='cad_pdf_'))
    pdf_path = tmp / f"{input_path.stem}.pdf"
    
    try:
        url = 'http://192.168.88.14:8000/convert'
        logger.info('Sending CAD file to Windows Server: %s', url)
        cmd = [
            'curl', '-s', '-X', 'POST', url,
            '-F', f'file=@{input_path}',
            '-F', 'ctb=monochrome.ctb',
            '-o', str(pdf_path),
            '-w', '%{http_code}'
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        http_code = res.stdout.strip()
        if http_code == '200' and pdf_path.exists() and pdf_path.stat().st_size > 0:
            meta['engine'] = 'windows_cad_server'
            logger.info('Windows CAD Server conversion SUCCESS')
            return pdf_path, meta
        else:
            logger.warning(f'Windows CAD Server error: {http_code}')
    except Exception as e:
        logger.warning('Could not connect to Windows CAD Server: %s', e)

    if suffix == '.dwg':
        meta['fallback'] = True
        meta['engine'] = 'ezdxf'

    try:
        if suffix == '.dwg':
            dxf_path = convert_dwg_to_dxf(str(input_path))
            work_dxf = tmp / dxf_path.name
            shutil.copy2(dxf_path, work_dxf)
            shutil.rmtree(dxf_path.parent, ignore_errors=True)
        else:
            work_dxf = tmp / input_path.name
            shutil.copy2(input_path, work_dxf)
            meta.setdefault('engine', 'ezdxf')

        convert_dxf_to_pdf(work_dxf, pdf_path, meta=meta)
        return pdf_path, meta
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
"""

patched = re.sub(r'def convert_cad_to_pdf\(.*', new_func, content, flags=re.DOTALL)
open('/app/cad_converter.py', 'w', encoding='utf-8').write(patched)
print('Patched successfully!')

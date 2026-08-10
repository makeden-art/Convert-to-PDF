import re

content = open(r'C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_converter.py', 'r', encoding='utf-8').read()

new_func = """    meta = meta or {}
    meta.setdefault("engine", None)
    meta.setdefault("fallback", False)
    windows_cad_ip = meta.get('windows_cad_ip', '').strip()
    dsd_path = meta.get('dsd_path')
    
    tmp = Path(tempfile.mkdtemp(prefix="cad_pdf_"))
    pdf_path = tmp / f"{input_path.stem}.pdf"
    size_mb = input_path.stat().st_size / (1024 * 1024) if input_path.exists() else 0

    if windows_cad_ip:
        try:
            import subprocess
            if not windows_cad_ip.startswith("http"):
                windows_cad_ip = "http://" + windows_cad_ip
            url = windows_cad_ip.rstrip('/') + '/convert'
            logger.info('Sending CAD file to Windows Server: %s', url)
            cmd = [
                'curl', '-s', '-X', 'POST', url,
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
                logger.warning(f'Windows CAD Server error: {http_code}')
        except Exception as e:
            logger.warning('Could not connect to Windows CAD Server: %s', e)
"""

old_target = """    meta = meta or {}
    meta.setdefault("engine", None)
    meta.setdefault("fallback", False)
    tmp = Path(tempfile.mkdtemp(prefix="cad_pdf_"))
    pdf_path = tmp / f"{input_path.stem}.pdf"
    size_mb = input_path.stat().st_size / (1024 * 1024) if input_path.exists() else 0"""

if old_target in content:
    content = content.replace(old_target, new_func)
    open(r'C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_converter.py', 'w', encoding='utf-8').write(content)
    print("Successfully patched cad_converter.py")
else:
    print("Could not find old_target in cad_converter.py")

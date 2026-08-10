import pathlib

path = pathlib.Path(r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_converter_copy.py")
content = path.read_text(encoding="utf-8")

old_text = """    tmp = Path(tempfile.mkdtemp(prefix="cad_pdf_"))
    pdf_path = tmp / f"{input_path.stem}.pdf"
    size_mb = input_path.stat().st_size / (1024 * 1024) if input_path.exists() else 0

    if suffix == ".dwg":
        # Bypassed direct ODA PDF conversion to strictly use the frame-detection engine on the _Р РЃРЎвЂљР В°Р С˜Р С—_РЎР‚Р В°Р С˜Р С”Р В° layer.
        meta["fallback"] = True
        meta["engine"] = "ezdxf\""""

new_text = """    tmp = Path(tempfile.mkdtemp(prefix="cad_pdf_"))
    pdf_path = tmp / f"{input_path.stem}.pdf"
    size_mb = input_path.stat().st_size / (1024 * 1024) if input_path.exists() else 0

    try:
        import subprocess
        url = "http://192.168.88.14:8000/convert"
        logger.info("Отправка файла на Windows CAD Server: %s", url)
        cmd = [
            "curl", "-s", "-X", "POST", url,
            "-F", f"file=@{input_path}",
            "-F", "ctb=monochrome.ctb",
            "-o", str(pdf_path),
            "-w", "%{http_code}"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        http_code = res.stdout.strip()
        if http_code == "200" and pdf_path.exists() and pdf_path.stat().st_size > 0:
            meta["engine"] = "windows_cad_server"
            logger.info("Успешно конвертировано через Windows CAD Server")
            return pdf_path, meta
        else:
            logger.warning(f"Windows CAD Server вернул ошибку: {http_code}")
    except Exception as e:
        logger.warning("Не удалось подключиться к Windows CAD Server: %s", e)

    if suffix == ".dwg":
        # Bypassed direct ODA PDF conversion to strictly use the frame-detection engine on the _Р РЃРЎвЂљР В°Р С˜Р С—_РЎР‚Р В°Р С˜Р С”Р В° layer.
        meta["fallback"] = True
        meta["engine"] = "ezdxf\""""

if old_text in content:
    content = content.replace(old_text, new_text)
    path.write_text(content, encoding="utf-8")
    print("PATCHED SUCCESS!")
else:
    print("NOT FOUND!")
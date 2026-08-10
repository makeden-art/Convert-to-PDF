import sys

converter_path = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\converter.py"
worker_path = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\convert_worker.py"

c = open(converter_path, 'r', encoding='utf-8').read()

# Update convert_file_to_pdf_isolated logic
old_isolated = """    cmd = [
        sys.executable,
        str(_WORKER_SCRIPT),
        str(src),
        str(dest),
        "--color-mode",
        color_mode,
    ]"""
new_isolated = """    cmd = [
        sys.executable,
        str(_WORKER_SCRIPT),
        str(src),
        str(dest),
        "--windows-cad-ip", windows_cad_ip,
    ]
    if dsd_path:
        cmd.extend(["--dsd-path", dsd_path])"""
c = c.replace(old_isolated, new_isolated)
open(converter_path, 'w', encoding='utf-8').write(c)

w = open(worker_path, 'r', encoding='utf-8').read()
w = w.replace('parser.add_argument("--color-mode", default="color")', 'parser.add_argument("--windows-cad-ip", default="")\n    parser.add_argument("--dsd-path", default="")')
w = w.replace('color_mode=args.color_mode', 'windows_cad_ip=args.windows_cad_ip, dsd_path=args.dsd_path')
open(worker_path, 'w', encoding='utf-8').write(w)

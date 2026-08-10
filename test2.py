import subprocess
import os

dwg = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_server_workdir\Несколько_рамок_в_принудительном_порядке_в_листе.dwg"
scr = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_server_workdir\test2.scr"

code = """(vl-load-com)
(setvar "CTAB" (car (layoutlist)))
(setq acadObj (vlax-get-acad-object))
(setq doc (vla-get-ActiveDocument acadObj))
(setq layout (vla-get-ActiveLayout doc))
(vla-put-StyleSheet layout "monochrome.ctb")
(vla-put-ConfigName layout "DWG To PDF.pc3")
(command "_.-EXPORT" "_PDF" "_All" "C:/Users/makeden/Documents/test2.pdf")
(command "_.QUIT" "_Y")
"""
with open(scr, "w", encoding="cp1251") as f:
    f.write(code)

cmd = f'"E:\\Autodesk\\acad\\AutoCAD 2022\\accoreconsole.exe" /i "{dwg}" /l ru-RU /s "{scr}"'
res = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="ignore")
print("PDF exists:", os.path.exists(r"C:\Users\makeden\Documents\test2.pdf"))
print(res.stdout)

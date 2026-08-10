import subprocess
import os

dwg = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_server_workdir\Несколько_рамок_в_принудительном_порядке_в_листе.dwg"
scr = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_server_workdir\test3.scr"

lisp_switch = '(setq layouts (dictsearch (namedobjdict) "ACAD_LAYOUT"))(setq firstLayout nil)(foreach item layouts (if (and (= (car item) 3) (/= (cdr item) "Model") (not firstLayout)) (setq firstLayout (cdr item))))(if firstLayout (setvar "CTAB" firstLayout))'
code = f'{lisp_switch} (command "_.-PLOT" "_Yes" "" "DWG To PDF.pc3" "" "_Millimeters" "_Landscape" "_No" "_Layout" "" "" "_Yes" "monochrome.ctb" "_Yes" "_No" "_No" "_No" "C:/Users/makeden/Documents/test3.pdf" "_No" "_Yes") (command "_.QUIT" "_Y")\n'

with open(scr, "w", encoding="cp1251") as f:
    f.write(code)

cmd = f'"E:\\Autodesk\\acad\\AutoCAD 2022\\accoreconsole.exe" /i "{dwg}" /l ru-RU /s "{scr}"'
res = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="ignore")
print("PDF exists:", os.path.exists(r"C:\Users\makeden\Documents\test3.pdf"))
print(res.stdout)

import subprocess
import os
import shutil
import tempfile

dwg = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_server_workdir\11-2025-1-ТКР9-1_Кальмиус_варианты.dwg"
temp_dwg = os.path.join(tempfile.gettempdir(), "test_export.dwg")
shutil.copy2(dwg, temp_dwg)

scr = os.path.join(tempfile.gettempdir(), "test_entmod.scr")

lisp_code = """
(setq dict (dictsearch (namedobjdict) "ACAD_LAYOUT"))
(while (setq item (assoc 350 dict))
  (setq ent (cdr item))
  (setq edata (entget ent))
  (if (assoc 7 edata)
    (setq edata (subst (cons 7 "monochrome.ctb") (assoc 7 edata) edata))
    (setq edata (append edata (list (cons 7 "monochrome.ctb"))))
  )
  (setq flags (cdr (assoc 70 edata)))
  (if flags
    (setq edata (subst (cons 70 (logior flags 32)) (assoc 70 edata) edata))
  )
  (entmod edata)
  (setq dict (cdr (member item dict)))
)
(command "_.-EXPORT" "_PDF" "_All" "C:/Users/makeden/Documents/test_export.pdf")
(command "_.QUIT" "_Y")
"""

with open(scr, "w", encoding="cp1251") as f:
    f.write(lisp_code)

cmd = f'"E:\\Autodesk\\acad\\AutoCAD 2022\\accoreconsole.exe" /i "{temp_dwg}" /l ru-RU /s "{scr}"'
res = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="ignore")
print("PDF exists:", os.path.exists(r"C:\Users\makeden\Documents\test_export.pdf"))
print(res.stdout)

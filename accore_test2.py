import os
import subprocess
import time

ACAD_PATH = r"E:\Autodesk\acad\AutoCAD 2022\accoreconsole.exe"
INPUT_DWG = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\Несколько_рамок_в_модели_в_одном_слое_штамп_рамка.dwg"
OUTPUT_PDF = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\accore_test_output.pdf"

def test_accore():
    if os.path.exists(OUTPUT_PDF):
        os.remove(OUTPUT_PDF)
        
    scr_path = os.path.abspath("print2.scr")
    # Используем _Extents
    scr_content = f"""_.-EXPORT
_PDF
_Extents
{OUTPUT_PDF}
_QUIT
_Y
"""
    with open(scr_path, "w", encoding="utf-8") as f:
        f.write(scr_content)
        
    cmd = f'"{ACAD_PATH}" /i "{INPUT_DWG}" /s "{scr_path}" /l ru-RU'
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    with open("out.log", "w", encoding="utf-8") as f:
        f.write(r.stdout)
        f.write("\nERR:\n")
        f.write(r.stderr)

if __name__ == "__main__":
    test_accore()

import os
import subprocess
import time

ACAD_PATH = r"E:\Autodesk\acad\AutoCAD 2022\accoreconsole.exe"
INPUT_DWG = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\Несколько_рамок_в_модели_в_одном_слое_штамп_рамка.dwg"
OUTPUT_PDF = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\accore_test_output.pdf"

def test_accore():
    # Удаляем старый файл, если есть
    if os.path.exists(OUTPUT_PDF):
        os.remove(OUTPUT_PDF)
        
    scr_path = os.path.abspath("print.scr")
    # Создаем скрипт для Автокада (печатаем Лист, монохромно, в PDF)
    # _.-EXPORT _PDF _All C:\path\to\output.pdf работает очень просто и экспортирует все листы!
    scr_content = f"""_.-EXPORT
_PDF
_All
{OUTPUT_PDF}
_QUIT
_Y
"""
    with open(scr_path, "w", encoding="utf-8") as f:
        f.write(scr_content)
        
    print(f"Запускаем AutoCAD Core Console для файла: {os.path.basename(INPUT_DWG)}")
    
    cmd = f'"{ACAD_PATH}" /i "{INPUT_DWG}" /s "{scr_path}" /l ru-RU'
    
    print(f"Команда: {cmd}")
    
    start = time.time()
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    print(f"Время выполнения: {time.time() - start:.1f} сек")
    
    if os.path.exists(OUTPUT_PDF):
        print(f"УРА! PDF успешно создан: {OUTPUT_PDF}")
    else:
        print("Ошибка! PDF не найден.")
        print("Вывод AutoCAD:")
        print(result.stdout)
        print("Ошибки AutoCAD:")
        print(result.stderr)

if __name__ == "__main__":
    test_accore()

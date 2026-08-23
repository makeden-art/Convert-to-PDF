import os
import sys
from pathlib import Path

# УСТАРЕЛО: Старый алгоритм через SaveAs ломал файлы. 
# Теперь используем excel_export.py.

try:
    import excel_export
except ImportError:
    print("Не найден модуль excel_export.py!")
    sys.exit(1)

in_path = r"E:\share_test\2_9_27_ТКР1-ВОР17_Перепады.xlsx"
out_path = r"E:\share_test\2_9_27_ТКР1-ВОР17_Перепады_new.pdf"

print(f"Конвертируем через новый алгоритм: {os.path.basename(in_path)}")
excel_export.excel_to_pdf(Path(in_path), Path(out_path))
print(f"OK! Сохранено: {out_path}")

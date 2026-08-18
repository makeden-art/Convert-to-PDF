import win32com.client
import os
import shutil
import tempfile

in_path = r"E:\share_test\2_9_27_ТКР1-ВОР17_Перепады.xlsx"
out_path = r"E:\share_test\2_9_27_ТКР1-ВОР17_Перепады_new.pdf"

print(f"Converting: {os.path.basename(in_path)}")
excel = win32com.client.DispatchEx("Excel.Application")
excel.Visible = False
excel.DisplayAlerts = False

# Сохраняем во временный файл (чтобы не конфликтовать с открытым PDF)
tmp_pdf = os.path.join(tempfile.gettempdir(), "excel_tmp_export.pdf")
if os.path.exists(tmp_pdf):
    os.remove(tmp_pdf)

try:
    wb = excel.Workbooks.Open(in_path)
    cm = excel.CentimetersToPoints(0.5)

    for ws in wb.Worksheets:
        try:
            ws.PageSetup.PaperSize = 9       # A4
            ws.PageSetup.Orientation = 1     # Книжная
            ws.PageSetup.Zoom = False
            ws.PageSetup.FitToPagesWide = 1
            ws.PageSetup.FitToPagesTall = False
            ws.PageSetup.LeftMargin = cm
            ws.PageSetup.RightMargin = cm
            ws.PageSetup.TopMargin = cm
            ws.PageSetup.BottomMargin = cm
            ws.PageSetup.HeaderMargin = 0
            ws.PageSetup.FooterMargin = 0
            ws.PageSetup.CenterHorizontally = False
            ws.PageSetup.CenterVertically = False
        except Exception as e:
            print(f"  PageSetup error on '{ws.Name}': {e}")

    # Сохраняем во временную папку
    wb.SaveAs(tmp_pdf, FileFormat=57)
    wb.Close(SaveChanges=False)
    print(f"  Exported to temp: {tmp_pdf}")

    # Копируем в целевой путь (перезаписываем)
    shutil.copy2(tmp_pdf, out_path)
    os.remove(tmp_pdf)
    print(f"OK! Saved: {out_path}")

except Exception as e:
    print(f"Error: {e}")
finally:
    try:
        excel.Quit()
    except Exception:
        pass

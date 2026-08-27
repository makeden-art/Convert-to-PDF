import os
import subprocess
import time
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
import uvicorn
import shutil
import glob
import sys
import tempfile
import uuid
import threading

try:
    import win32com.client
except ImportError:
    print("Устанавливаем pywin32 для работы MS Office...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pywin32"])
    import win32com.client

app = FastAPI(title="AutoCAD Print Server")

def find_accoreconsole():
    search_paths = [
        r"C:\Program Files\Autodesk\AutoCAD *\accoreconsole.exe",
        r"D:\Program Files\Autodesk\AutoCAD *\accoreconsole.exe",
        r"E:\Program Files\Autodesk\AutoCAD *\accoreconsole.exe",
        r"C:\Autodesk\AutoCAD *\accoreconsole.exe",
        r"D:\Autodesk\AutoCAD *\accoreconsole.exe",
        r"E:\Autodesk\acad\AutoCAD *\accoreconsole.exe"
    ]
    for pattern in search_paths:
        matches = glob.glob(pattern)
        if matches:
            return sorted(matches, reverse=True)[0]
    default_p = r"C:\Program Files\Autodesk\AutoCAD 2022\accoreconsole.exe"
    return default_p if os.path.exists(default_p) else None

def check_word_installed() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application"):
            return True
    except Exception:
        return False

def check_excel_installed() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Excel.Application"):
            return True
    except Exception:
        return False

@app.get("/")
@app.get("/status")
def server_status():
    acad_p = find_accoreconsole()
    acad_ok = bool(acad_p and os.path.exists(acad_p))
    word_ok = check_word_installed()
    excel_ok = check_excel_installed()
    return {
        "status": "ok",
        "autocad": {
            "available": acad_ok,
            "path": acad_p if acad_ok else None
        },
        "word": {
            "available": word_ok
        },
        "excel": {
            "available": excel_ok
        }
    }

ACAD_PATH = find_accoreconsole()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(SCRIPT_DIR, "cad_server_workdir")
os.makedirs(WORK_DIR, exist_ok=True)

SHARE_LOCAL_PATH = os.environ.get("SHARE_LOCAL_PATH", r"E:\share_test")
SHARE_UNC_PATH = os.environ.get("SHARE_UNC_PATH", r"\\192.168.88.14\share_test")
_office_lock = threading.Lock()
ACAD_TIMEOUT_SEC = 3600

def unc_to_local(path: str) -> str:
    normalized = path.replace("/", "\\")
    unc_norm = SHARE_UNC_PATH.replace("/", "\\").rstrip("\\")
    local_norm = SHARE_LOCAL_PATH.rstrip("\\")
    if normalized.lower().startswith(unc_norm.lower()):
        return local_norm + normalized[len(unc_norm):]
    return path

def _generate_lisp_script(safe_pdf_path: str, force_smart: bool, ctb: str = None) -> str:
    """Генерирует LISP-код для печати DWG -> PDF."""
    ctb_lisp = ""
    if ctb and ctb.lower() != "none":
        ctb_lisp = f"""
(vl-catch-all-apply 'setvar (list "PLOTSTYLEMODE" 0))
(vl-catch-all-apply 'setvar (list "CPROFILE" "<<VANILLA>>"))
(vl-catch-all-apply
  (function
    (lambda ()
      (vlax-for layout (vla-get-Layouts (vla-get-ActiveDocument (vlax-get-acad-object)))
        (vla-put-StyleSheet layout "{ctb}")
      )
    )
  )
)
"""

    attsync_lisp = """
(vl-catch-all-apply
  (function
    (lambda ()
      (setq blks (vla-get-Blocks (vla-get-ActiveDocument (vlax-get-acad-object))))
      (vlax-for blk blks
        (if (= (vla-get-HasAttributes blk) :vlax-true)
          (command "_.ATTSYNC" "_N" (vla-get-Name blk))
        )
      )
    )
  )
)
"""

    # Динамический поиск C:\Common
    common_path_lisp = """
(setq paths (getenv "ACAD"))
(if (and (findfile "C:\\\\Common") (not (vl-string-search "C:\\\\Common" paths)))
  (setenv "ACAD" (strcat paths ";C:\\\\Common;C:\\\\Common\\\\Support;C:\\\\Common\\\\Fonts;C:\\\\Common\\\\LISP"))
)
"""

    force_smart_val = "T" if force_smart else "nil"
    pdf_prefix = safe_pdf_path.replace("\\", "/").replace(".pdf", "")
    
    lisp_code = f"""(vl-load-com)
(defun GetFrames ( / ss i ent edata p1 p2 dx dy lines xmin ymin xmax ymax w h ratio frames vx vymin vymax pts)
  (setq frames (quote ()))
  (setq lines (quote ()))
  
  (setq ss (ssget "X" (quote ((0 . "LINE") (8 . "*ШТАМП*,*РАМКА*,*Штамп*,*рамка*") (410 . "Model")))))
  (if ss
    (progn (setq i 0)
      (while (< i (sslength ss))
        (setq ent (ssname ss i) edata (entget ent))
        (setq p1 (cdr (assoc 10 edata)) p2 (cdr (assoc 11 edata)))
        (setq dx (abs (- (car p1) (car p2))) dy (abs (- (cadr p1) (cadr p2))))
        (if (and (> dx 150) (< dy 1.0)) (setq lines (cons (list (quote H) (min (car p1) (car p2)) (max (car p1) (car p2)) (cadr p1)) lines)))
        (if (and (> dy 150) (< dx 1.0)) (setq lines (cons (list (quote V) (car p1) (min (cadr p1) (cadr p2)) (max (cadr p1) (cadr p2))) lines)))
        (setq i (1+ i))
      )
    )
  )
  
  (foreach h1 lines
    (if (eq (car h1) (quote H))
      (progn
        (setq xmin (cadr h1) xmax (caddr h1) y (cadddr h1) w (- xmax xmin))
        (foreach v1 lines
          (if (eq (car v1) (quote V))
            (progn
              (setq vx (cadr v1) vymin (caddr v1) vymax (cadddr v1))
              (if (and (< (abs (- vx xmin)) 1.0) (< (abs (- vymin y)) 1.0))
                (progn
                  (setq h (- vymax vymin))
                  (if (and (> h 0.01) (> w 0.01))
                    (progn
                      (if (> w h) (setq ratio (/ (float w) (float h))) (setq ratio (/ (float h) (float w))))
                      (if (and (> w 150) (> h 150) (< w 5000) (< h 5000) (> ratio 1.35) (< ratio 1.48))
                        (setq frames (cons (list xmin y xmax vymax w h) frames))
                      )
                    )
                  )
                )
              )
            )
          )
        )
      )
    )
  )
  
  (setq ss (ssget "X" (quote ((0 . "LWPOLYLINE") (8 . "*ШТАМП*,*РАМКА*,*Штамп*,*рамка*") (410 . "Model")))))
  (if ss
    (progn (setq i 0)
      (while (< i (sslength ss))
        (setq ent (ssname ss i) edata (entget ent) pts (quote ()))
        (foreach item edata (if (= (car item) 10) (setq pts (cons (cdr item) pts))))
        (if (or (= (length pts) 4) (= (length pts) 5))
          (progn 
            (setq xmin (caar pts) xmax (caar pts) ymin (cadar pts) ymax (cadar pts))
            (foreach p (cdr pts) 
              (if (< (car p) xmin) (setq xmin (car p))) 
              (if (> (car p) xmax) (setq xmax (car p))) 
              (if (< (cadr p) ymin) (setq ymin (cadr p))) 
              (if (> (cadr p) ymax) (setq ymax (cadr p)))
            )
            (setq w (- xmax xmin) h (- ymax ymin))
            (if (and (> h 0.01) (> w 0.01))
              (progn
                (if (> w h) (setq ratio (/ (float w) (float h))) (setq ratio (/ (float h) (float w))))
                (if (and (> w 150) (> h 150) (< w 5000) (< h 5000) (> ratio 1.35) (< ratio 1.48))
                  (setq frames (cons (list xmin ymin xmax ymax w h) frames))
                )
              )
            )
          )
        )
        (setq i (1+ i))
      )
    )
  )
        
  (setq frames (vl-sort frames (function (lambda (a b) (if (> (abs (- (cadr a) (cadr b))) 10.0) (> (cadr a) (cadr b)) (< (car a) (car b)))))))
  (setq uniqFrames (quote ()))
  (setq lastFrm nil)
  (foreach frm frames
    (if (not lastFrm)
      (progn (setq uniqFrames (cons frm uniqFrames)) (setq lastFrm frm))
      (if (or (> (abs (- (car frm) (car lastFrm))) 50.0) (> (abs (- (cadr frm) (cadr lastFrm))) 50.0))
        (progn (setq uniqFrames (cons frm uniqFrames)) (setq lastFrm frm))
      )
    )
  )
  uniqFrames
)
(defun ExportModelFrames ()
  (setvar "TILEMODE" 1)
  (setq frames (GetFrames))
  (setq rowTol 150.0)
  (setq frames (vl-sort frames (function (lambda (a b) (if (> (abs (- (cadr a) (cadr b))) rowTol) (> (cadr a) (cadr b)) (< (car a) (car b)))))))
  (setq idx 1)
  (foreach frm frames
    (setq w (nth 4 frm) h (nth 5 frm))
    (if (> w h)
      (cond ((and (< w 450) (< h 320)) (setq paper "ISO_full_bleed_A3_(420.00_x_297.00_MM)"))
            ((and (< w 620) (< h 450)) (setq paper "ISO_full_bleed_A2_(594.00_x_420.00_MM)"))
            ((and (< w 870) (< h 620)) (setq paper "ISO_full_bleed_A1_(841.00_x_594.00_MM)"))
            (t (setq paper "ISO_full_bleed_A0_(1189.00_x_841.00_MM)")))
      (cond ((and (< w 320) (< h 450)) (setq paper "ISO_full_bleed_A3_(297.00_x_420.00_MM)"))
            ((and (< w 450) (< h 620)) (setq paper "ISO_full_bleed_A2_(420.00_x_594.00_MM)"))
            ((and (< w 620) (< h 870)) (setq paper "ISO_full_bleed_A1_(594.00_x_841.00_MM)"))
            (t (setq paper "ISO_full_bleed_A0_(841.00_x_1189.00_MM)"))))
    (setq outpath (strcat "{pdf_prefix}_" (itoa idx) ".pdf"))
    (command "_.-PLOT" "_Y" "Model" "DWG To PDF.pc3" paper "_M" "_L" "_N" "_W" (list (car frm) (cadr frm)) (list (caddr frm) (cadddr frm)) "_F" "_C" "_Y" "monochrome.ctb" "_Y" "_W" outpath "_N" "_Y")
    (setq idx (1+ idx))
  )
)

(defun ExportPaperSpace ()
  (setvar "TILEMODE" 0)
  (vl-catch-all-apply
    (function (lambda ()
      (command "_.-EXPORT" "_PDF" "_All" "{safe_pdf_path.replace(chr(92), chr(47))}")
    ))
  )
)

(setq force-smart {force_smart_val})
(if force-smart
  (ExportModelFrames)
  (ExportPaperSpace)
)
(command "_.QUIT" "_Y")"""
    return lisp_code.replace("\n", " ") + "\n"

def _merge_pdf_parts(pdf_prefix_base: str, output_pdf_path: str):
    """Склеивает промежуточные PDF файлы (от поиска рамок) в один."""
    from pypdf import PdfWriter
    pdf_files = sorted(
        glob.glob(f"{pdf_prefix_base}_[0-9]*.pdf"),
        key=lambda f: int(f.rsplit("_", 1)[-1].replace(".pdf", ""))
    )
    if pdf_files:
        try:
            merger = PdfWriter()
            for pdf in pdf_files:
                merger.append(pdf)
            merger.write(output_pdf_path)
            merger.close()
        except Exception as e:
            print(f"Ошибка при объединении PDF (smart): {e}")
        
        # Удаляем промежуточные куски
        for pdf in pdf_files:
            try: os.remove(pdf)
            except: pass
    else:
        raise ValueError("В Модели не найдено ни одной прямоугольной рамки поперечника.")

def _remove_empty_pages(pdf_path: str):
    """Рендерит страницы в мини-картинки и удаляет пустые (шаблонные) листы."""
    if not os.path.exists(pdf_path):
        return
        
    try:
        import fitz
        doc = fitz.open(pdf_path)
        if len(doc) <= 1:
            doc.close()
            return
            
        pages_to_keep = []
        mat = fitz.Matrix(0.08, 0.08)  # 8% масштаб
        
        for pno in range(len(doc)):
            page = doc[pno]
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
            total = len(pix.samples)
            if total == 0:
                continue
            white = sum(1 for b in pix.samples if b > 245)
            if white / total < 0.98:  # есть реальный контент
                pages_to_keep.append(pno)

        print(f"Страниц: {len(doc)}, непустых: {len(pages_to_keep)}")
        if 0 < len(pages_to_keep) < len(doc):
            new_doc = fitz.open()
            for pno in pages_to_keep:
                new_doc.insert_pdf(doc, from_page=pno, to_page=pno)
            doc.close()
            
            tmp_clean = pdf_path + ".clean.pdf"
            new_doc.save(tmp_clean)
            new_doc.close()
            os.replace(tmp_clean, pdf_path)
        else:
            doc.close()
    except Exception as e:
        print(f"Не удалось удалить пустые страницы: {e}")

# ISO A0..A3 стороны (мм). Допуск шире — внешняя рамка листа часто чуть больше поля чертежа.
ISO_SIDES_MM = (297.0, 420.0, 594.0, 841.0, 1189.0)
ISO_SIDE_TOL = 25.0
DUMP_TIMEOUT_SEC = 300
ORTHO_TOL = 1.5
CORNER_TOL = 3.0

def _kill_accore():
    subprocess.run("taskkill /F /IM accoreconsole.exe", shell=True, capture_output=True)

def _run_accore(cmd, timeout_sec: int):
    try:
        return subprocess.run(cmd, shell=False, capture_output=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        _kill_accore()
        raise

def _decode_acad_out(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        return raw.decode("utf-16", errors="replace")
    except Exception:
        return raw.decode("cp1251", errors="replace")

def _is_iso_side(length: float, tol: float = ISO_SIDE_TOL) -> bool:
    return any(abs(length - side) <= tol for side in ISO_SIDES_MM)

def _paper_for_frame(w: float, h: float) -> str:
    """
    Бумага должна быть чуть больше рамки. Для «длинных» листов ~840×297
    стандартного формата нет — берём A1 и потом обрезаем поля в PDF.
    """
    if w >= h:
        if w <= 430 and h <= 310:
            return "ISO_full_bleed_A3_(420.00_x_297.00_MM)"
        if w <= 610 and h <= 430:
            return "ISO_full_bleed_A2_(594.00_x_420.00_MM)"
        if w <= 860 and h <= 610:
            return "ISO_full_bleed_A1_(841.00_x_594.00_MM)"
        return "ISO_full_bleed_A0_(1189.00_x_841.00_MM)"
    if w <= 310 and h <= 430:
        return "ISO_full_bleed_A3_(297.00_x_420.00_MM)"
    if w <= 430 and h <= 610:
        return "ISO_full_bleed_A2_(420.00_x_594.00_MM)"
    if w <= 610 and h <= 860:
        return "ISO_full_bleed_A1_(594.00_x_841.00_MM)"
    return "ISO_full_bleed_A0_(841.00_x_1189.00_MM)"


def _trim_pdf_to_content(pdf_path: str, pad_pt: float = 4.0):
    """
    Убирает пустые поля после печати рамки Model на более крупный ISO-лист
    (например 840×297 → A1 841×594 с Fit). Обрезка по реальному содержимому.
    """
    if not os.path.exists(pdf_path):
        return
    try:
        import fitz
        doc = fitz.open(pdf_path)
        changed = False
        for page in doc:
            # Низкое разрешение достаточно, чтобы найти непустую область
            mat = fitz.Matrix(0.25, 0.25)
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
            w, h = pix.width, pix.height
            samples = pix.samples
            thr = 250
            min_x, min_y, max_x, max_y = w, h, -1, -1
            for y in range(h):
                row = y * w
                for x in range(w):
                    if samples[row + x] < thr:
                        if x < min_x:
                            min_x = x
                        if y < min_y:
                            min_y = y
                        if x > max_x:
                            max_x = x
                        if y > max_y:
                            max_y = y
            if max_x < 0:
                continue
            # координаты pixmap → точки PDF
            sx = page.rect.width / w
            sy = page.rect.height / h
            rect = fitz.Rect(
                min_x * sx - pad_pt,
                min_y * sy - pad_pt,
                (max_x + 1) * sx + pad_pt,
                (max_y + 1) * sy + pad_pt,
            )
            rect = rect & page.rect
            # Обрезаем только если поля заметные (>3% с любой стороны)
            pr = page.rect
            if (
                rect.width < pr.width * 0.97
                or rect.height < pr.height * 0.97
            ):
                page.set_cropbox(rect)
                page.set_mediabox(rect)
                changed = True
        if changed:
            tmp = pdf_path + ".trim.pdf"
            doc.save(tmp, deflate=True)
            doc.close()
            try:
                os.replace(tmp, pdf_path)
            except PermissionError:
                alt = os.path.splitext(pdf_path)[0] + "_trimmed.pdf"
                try:
                    os.remove(alt)
                except OSError:
                    pass
                os.replace(tmp, alt)
                print(f"Исходный PDF занят, сохранено: {alt}")
                return
            print(f"Trimmed PDF margins: {pdf_path}")
        else:
            doc.close()
    except Exception as e:
        print(f"Не удалось обрезать поля PDF: {e}")

def _build_dump_lisp(lines_file: str) -> str:
    """
    Дамп LINE/LWPOLYLINE из Model.
    LINE: все ортогональные >200 (со слоем) — для рамок на слое штампа.
    LWPOLYLINE: крупные прямоугольники (со слоем) — запасной поиск.
    """
    path = lines_file.replace("\\", "/")
    return f"""(vl-load-com)
(setvar "FILEDIA" 0)
(setvar "CMDDIA" 0)
(setvar "EXPERT" 5)
(setvar "PROXYNOTICE" 0)
(defun DumpFrames ( / f ss i ent edata p1 p2 dx dy pts xmin ymin xmax ymax w h g67 lay)
  (setq f (open "{path}" "w"))
  (if (not f)
    (progn (princ "ERR_OPEN") (command "_.QUIT" "_Y") (princ))
  )
  (setq ss (ssget "X" (quote ((0 . "LINE")))))
  (if ss
    (progn
      (setq i 0)
      (while (< i (sslength ss))
        (setq ent (ssname ss i)
              edata (entget ent)
              g67 (cdr (assoc 67 edata))
              lay (cdr (assoc 8 edata)))
        (if (or (null g67) (= g67 0))
          (progn
            (setq p1 (cdr (assoc 10 edata))
                  p2 (cdr (assoc 11 edata)))
            (setq dx (abs (- (car p1) (car p2)))
                  dy (abs (- (cadr p1) (cadr p2))))
            (if (and (> dx 200.0) (< dy {ORTHO_TOL}))
              (write-line
                (strcat "H;"
                        (rtos (min (car p1) (car p2)) 2 3) ";"
                        (rtos (max (car p1) (car p2)) 2 3) ";"
                        (rtos (cadr p1) 2 3) ";"
                        lay)
                f)
            )
            (if (and (> dy 200.0) (< dx {ORTHO_TOL}))
              (write-line
                (strcat "V;"
                        (rtos (car p1) 2 3) ";"
                        (rtos (min (cadr p1) (cadr p2)) 2 3) ";"
                        (rtos (max (cadr p1) (cadr p2)) 2 3) ";"
                        lay)
                f)
            )
          )
        )
        (setq i (1+ i))
      )
    )
  )
  (setq ss (ssget "X" (quote ((0 . "LWPOLYLINE")))))
  (if ss
    (progn
      (setq i 0)
      (while (< i (sslength ss))
        (setq ent (ssname ss i)
              edata (entget ent)
              g67 (cdr (assoc 67 edata))
              lay (cdr (assoc 8 edata))
              pts (quote ()))
        (if (or (null g67) (= g67 0))
          (progn
            (foreach item edata
              (if (= (car item) 10)
                (setq pts (cons (cdr item) pts))
              )
            )
            (if (and (>= (length pts) 4) (<= (length pts) 6))
              (progn
                (setq xmin (caar pts) xmax (caar pts)
                      ymin (cadar pts) ymax (cadar pts))
                (foreach p (cdr pts)
                  (if (< (car p) xmin) (setq xmin (car p)))
                  (if (> (car p) xmax) (setq xmax (car p)))
                  (if (< (cadr p) ymin) (setq ymin (cadr p)))
                  (if (> (cadr p) ymax) (setq ymax (cadr p)))
                )
                (setq w (- xmax xmin) h (- ymax ymin))
                (if (and (> w 250.0) (> h 250.0) (< w 2000.0) (< h 2000.0))
                  (write-line
                    (strcat "P;"
                            (rtos xmin 2 3) ";"
                            (rtos ymin 2 3) ";"
                            (rtos xmax 2 3) ";"
                            (rtos ymax 2 3) ";"
                            lay)
                    f)
                )
              )
            )
          )
        )
        (setq i (1+ i))
      )
    )
  )
  (close f)
  (princ "DUMP_OK")
)
(DumpFrames)
(command "_.QUIT" "_Y")
"""


def _write_lsp_and_scr(lsp_path: str, scr_path: str, lisp_code: str):
    """Пишет .lsp и короткий .scr с (load ...), чтобы не ломать парсер SCR."""
    with open(lsp_path, "w", encoding="ascii", errors="replace") as f:
        f.write(lisp_code)
    load_path = lsp_path.replace("\\", "/")
    with open(scr_path, "w", encoding="ascii", errors="replace") as f:
        f.write(f'(load "{load_path}")\n')

def _is_sheet_size(w: float, h: float) -> bool:
    """
    Рамка листа в Model: ISO или нестандарт (Robur ~631×297, 840×297 и т.п.).
    Короткая сторона ~высота листа, длинная — ширина формата.
    """
    a, b = (w, h) if w >= h else (h, w)
    if b < 240 or a < 280 or a > 2000 or b > 1300:
        return False
    ratio = a / b
    if ratio > 4.0:
        return False
    # классический ISO ~1.414
    if 1.25 <= ratio <= 1.55 and _is_iso_side(a) and _is_iso_side(b):
        return True
    # удлинённый / нестандарт: высота листа ~A4..A3, ширина произвольная
    if 240 <= b <= 340 and a >= b * 1.15:
        return True
    if _is_iso_side(b) and a >= b * 1.2:
        return True
    if _is_iso_side(a) and b >= 240:
        return True
    return False


def _is_inner_drawing_frame(w: float, h: float) -> bool:
    """Внутреннее поле чертежа внутри рамки (не лист)."""
    pairs = (
        (375, 252), (795, 252),
        (395, 287), (606, 287),
        (375, 212),
    )
    for pw, ph in pairs:
        if abs(w - pw) <= 12 and abs(h - ph) <= 12:
            return True
        if abs(w - ph) <= 12 and abs(h - pw) <= 12:
            return True
    return False


def _is_stamp_layer(name: str) -> bool:
    n = (name or "").casefold()
    keys = ("штамп", "рамка", "stamp", "frame", "format", "формат")
    return any(k in n for k in keys)


def _rects_from_hv(hl, vl, require_v=True):
    """Собрать прямоугольники из H/V отрезков с проверкой вертикалей."""
    frames = []
    n = len(hl)
    for i in range(n):
        x1a, x2a, ya = hl[i]
        wa = x2a - x1a
        for j in range(i + 1, n):
            x1b, x2b, yb = hl[j]
            if abs((x2b - x1b) - wa) > CORNER_TOL:
                continue
            if abs(x1a - x1b) > CORNER_TOL or abs(x2a - x2b) > CORNER_TOL:
                continue
            height = abs(yb - ya)
            if height < 200:
                continue
            xmin, xmax = min(x1a, x1b), max(x2a, x2b)
            ymin, ymax = min(ya, yb), max(ya, yb)
            if require_v and vl:
                def has_v(x):
                    for vx, vy0, vy1 in vl:
                        if abs(vx - x) > CORNER_TOL:
                            continue
                        if abs(vy0 - ymin) <= CORNER_TOL and abs(vy1 - ymax) <= CORNER_TOL:
                            return True
                        if abs(vy0 - ymax) <= CORNER_TOL and abs(vy1 - ymin) <= CORNER_TOL:
                            return True
                    return False
                if not (has_v(xmin) and has_v(xmax)):
                    continue
            frames.append((xmin, ymin, xmax, ymax, xmax - xmin, ymax - ymin))
    return frames


def _keep_outermost(frames):
    def contains(outer, inner, pad=5.0):
        return (
            outer[0] <= inner[0] + pad
            and outer[1] <= inner[1] + pad
            and outer[2] >= inner[2] - pad
            and outer[3] >= inner[3] - pad
            and (outer[4] * outer[5]) > (inner[4] * inner[5]) + 50.0
        )

    outer = []
    for i, fr in enumerate(frames):
        if any(contains(frames[j], fr) for j in range(len(frames)) if j != i):
            continue
        outer.append(fr)
    outer.sort(key=lambda fr: (-fr[1], fr[0]))
    unique = []
    for fr in outer:
        if not any(abs(fr[0] - u[0]) < 30 and abs(fr[1] - u[1]) < 30 for u in unique):
            unique.append(fr[:6])
    return unique


def _frame_contains(outer, inner, pad=10.0) -> bool:
    return (
        outer[0] <= inner[0] + pad
        and outer[1] <= inner[1] + pad
        and outer[2] >= inner[2] - pad
        and outer[3] >= inner[3] - pad
    )


def _frames_overlap(a, b, pad=10.0) -> bool:
    return not (
        a[2] < b[0] - pad
        or b[2] < a[0] - pad
        or a[3] < b[1] - pad
        or b[3] < a[1] - pad
    )


def _pick_widest_per_row(frames, y_tol: float = 20.0):
    rows = []
    for fr in sorted(frames, key=lambda t: -t[1]):
        placed = False
        for row in rows:
            if abs(row["y"] - fr[1]) <= y_tol:
                row["items"].append(fr)
                placed = True
                break
        if not placed:
            rows.append({"y": fr[1], "items": [fr]})
    picked = [max(r["items"], key=lambda t: t[4] * t[5]) for r in rows]
    picked.sort(key=lambda fr: (-fr[1], fr[0]))
    return picked


def _find_frames_from_dump(lines_file: str):
    """
    Листы в Model (разные форматы, в т.ч. нестандарт):
    1) Рамки слоя штампа: LINE + LWPOLYLINE (внешние).
    2) Если штамп дал только «левую половину» ~420, а Bounds имеет 840 —
       берём полную 840 (чтобы не обрезать штамп справа).
    3) Если штампа нет — Bounds / ISO, с предпочтением внешних.
    """
    from collections import defaultdict

    hl_by = defaultdict(list)
    vl_by = defaultdict(list)
    polys = []

    raw = open(lines_file, "rb").read().splitlines()
    for b in raw:
        for enc in ("cp1251", "utf-8"):
            try:
                line = b.decode(enc)
                break
            except Exception:
                line = b.decode("utf-8", errors="replace")
        parts = line.strip().split(";")
        if len(parts) < 4:
            continue
        kind = parts[0]
        if kind == "H" and len(parts) >= 5:
            hl_by[parts[4]].append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif kind == "V" and len(parts) >= 5:
            vl_by[parts[4]].append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif kind == "H":
            hl_by[""].append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif kind == "V":
            vl_by[""].append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif kind == "P" and len(parts) >= 5:
            xmin, ymin, xmax, ymax = map(float, parts[1:5])
            lay = parts[5] if len(parts) >= 6 else ""
            w, h = xmax - xmin, ymax - ymin
            polys.append((xmin, ymin, xmax, ymax, w, h, lay))

    bounds_frames = []
    stamp_poly_frames = []
    for xmin, ymin, xmax, ymax, w, h, lay in polys:
        if _is_inner_drawing_frame(w, h) or not _is_sheet_size(w, h):
            continue
        fr = (xmin, ymin, xmax, ymax, w, h)
        if lay == "Bounds" or lay.casefold() == "bounds":
            bounds_frames.append(fr)
        elif _is_stamp_layer(lay):
            stamp_poly_frames.append(fr)

    stamp_hl, stamp_vl = [], []
    for lay, lines in hl_by.items():
        if _is_stamp_layer(lay):
            stamp_hl.extend(lines)
    for lay, lines in vl_by.items():
        if _is_stamp_layer(lay):
            stamp_vl.extend(lines)

    stamp_line_frames = []
    if stamp_hl and stamp_vl:
        for fr in _rects_from_hv(stamp_hl, stamp_vl, require_v=True):
            if _is_inner_drawing_frame(fr[4], fr[5]):
                continue
            if _is_sheet_size(fr[4], fr[5]) or (fr[4] > 250 and fr[5] > 250):
                stamp_line_frames.append(fr[:6])

    stamp_frames = _keep_outermost(stamp_line_frames + stamp_poly_frames)
    stamp_frames = _pick_widest_per_row(stamp_frames)

    if stamp_frames:
        # Расширять 420→840 только если справа на Bounds реально есть штамп
        # (иначе пустая половина + лишние линии Bounds).
        line_outer = _keep_outermost(stamp_line_frames) if stamp_line_frames else []
        stamp_h_all = list(stamp_hl)
        stamp_poly_all = list(stamp_poly_frames)

        def _right_half_has_stamp(bounds_fr) -> bool:
            mid = (bounds_fr[0] + bounds_fr[2]) / 2.0
            for p in stamp_poly_all:
                if p[4] < 80:
                    continue
                if p[0] >= mid - 10 and _frame_contains(bounds_fr, p, pad=20.0):
                    return True
            for x1, x2, y in stamp_h_all:
                if y < bounds_fr[1] - 5 or y > bounds_fr[3] + 5:
                    continue
                if x1 >= mid - 30 and x2 <= bounds_fr[2] + 30:
                    return True
                if x1 < mid < x2 and (x2 - mid) > 80:
                    return True
            return False

        expanded = []
        for sf in stamp_frames:
            chosen = sf[:6]
            was_expanded = False
            if 400 <= sf[4] <= 450:
                hosts = [
                    b for b in bounds_frames
                    if b[4] >= 700 and _frame_contains(b, sf, pad=15.0)
                ]
                if hosts:
                    host = max(hosts, key=lambda b: b[4] * b[5])[:6]
                    if _right_half_has_stamp(host):
                        chosen = host
                        was_expanded = True
            if line_outer and any(
                _frames_overlap(chosen, L, pad=5.0) or _frame_contains(chosen, L, pad=20.0)
                for L in line_outer
            ):
                continue
            if was_expanded and line_outer and len(line_outer) <= 10:
                adj = False
                for L in line_outer:
                    if abs(chosen[0] - L[0]) > 50:
                        continue
                    if 0 <= (L[1] - chosen[3]) <= 15 or 0 <= (chosen[1] - L[3]) <= 15:
                        adj = True
                        break
                if adj:
                    continue
            expanded.append(chosen)
        expanded.extend(L[:6] for L in line_outer)
        result = _keep_outermost(expanded)
        result = _pick_widest_per_row(result)
        from collections import Counter
        sizes = dict(Counter((round(f[4]), round(f[5])) for f in result))
        print(f"Stamp sheets: {len(result)} sizes={sizes}")
        return result

    # --- Запасной путь: только Bounds / линии без штампа ---
    frames = list(bounds_frames)
    all_hl = [x for lines in hl_by.values() for x in lines]
    all_vl = [x for lines in vl_by.values() for x in lines]
    for fr in _rects_from_hv(all_hl, all_vl, require_v=bool(all_vl)):
        if _is_inner_drawing_frame(fr[4], fr[5]):
            continue
        if _is_sheet_size(fr[4], fr[5]):
            frames.append(fr[:6])

    frames = _keep_outermost(frames)
    result = _pick_widest_per_row(frames)
    if result:
        max_w = max(f[4] for f in result)
        if max_w >= 700:
            wide = [f for f in result if f[4] >= max_w * 0.85]
            # не отбрасываем узкие, если их много — разные форматы в одной модели
            if wide and len(wide) >= len(result) * 0.5:
                # только если «широкие» доминируют; иначе оставляем смесь форматов
                pass
    from collections import Counter
    print(f"Bounds sheets: {len(result)} sizes={dict(Counter((round(f[4]), round(f[5])) for f in result))}")
    return result

def _build_plot_lisp(frames, pdf_prefix: str) -> str:
    """
    Чистый SCR: ответы на -PLOT.
    Thaw/Unlock слоёв (без _On *: иначе включаются выключенные Bounds/Таблица объёмов).
    """
    lines = [
        "FILEDIA", "0",
        "BACKGROUNDPLOT", "0",
        "CMDDIA", "0",
        "PROXYNOTICE", "0",
        "EXPERT", "5",
        "TILEMODE", "1",
        "FILLMODE", "1",
        "TEXTFILL", "1",
        "FRAME", "0",
        "WIPEOUTFRAME", "0",
        # Только Thaw/Unlock: _On * включает выключенные слои Robur
        # (Таблица объемов, Bounds) → лишний текст и линии рамки.
        "_.-LAYER",
        "_Thaw", "*",
        "_Unlock", "*",
        "",  # выход из -LAYER
    ]
    margin = 0.5
    for idx, frm in enumerate(frames, 1):
        paper = _paper_for_frame(frm[4], frm[5])
        outpath = f"{pdf_prefix}_{idx}.pdf".replace("\\", "/")
        x1, y1 = frm[0] - margin, frm[1] - margin
        x2, y2 = frm[2] + margin, frm[3] + margin
        orient = "_L" if (x2 - x1) >= (y2 - y1) else "_P"
        lines.extend([
            "_.-PLOT",
            "_Y",
            "Model",
            "DWG To PDF.pc3",
            paper,
            "_M",
            orient,
            "_N",
            "_W",
            f"{x1},{y1}",
            f"{x2},{y2}",
            "_F",
            "_C",
            "_Y",
            "monochrome.ctb",
            "_Y",
            "_A",
            outpath,
            "_N",
            "_Y",
        ])
    lines.extend(["_.QUIT", "_Y"])
    return "\n".join(lines) + "\n"

@app.post("/convert")
def convert(
    file: UploadFile = File(None),
    ctb: str = Form(None),
    profile: str = Form(None),
    smb_dwg_path: str = Form(None),
    smart_search: str = Form(None)
):
    if not ACAD_PATH or not os.path.exists(ACAD_PATH):
        return JSONResponse(status_code=400, content={"error": "AutoCAD (accoreconsole.exe) не найден на сервере."})

    start_time = time.time()
    safe_uid = uuid.uuid4().hex
    temp_dir = tempfile.gettempdir()
    
    dwg_path = None
    safe_dwg_path = None
    safe_pdf_path = None
    scr_path = None
    stdout_text = ""
    is_smart = False
    
    try:
        # 1. Определение путей и копирование файла
        if smb_dwg_path:
            local_path = unc_to_local(smb_dwg_path)
            if os.path.exists(local_path):
                safe_dwg_path = local_path
                safe_filename = os.path.basename(local_path)
            else:
                return JSONResponse(status_code=400, content={"error": f"Файл не найден по локальному пути: {local_path}"})
        elif file and file.filename:
            safe_filename = file.filename.replace(" ", "_")
            dwg_path = os.path.join(temp_dir, f"{safe_uid}_{safe_filename}")
            with open(dwg_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            safe_dwg_path = dwg_path
        else:
            return JSONResponse(status_code=400, content={"error": "Не передан файл."})

        # Итоговые пути
        safe_pdf_path = os.path.join(temp_dir, f"{safe_uid}_{safe_filename}.pdf")
        scr_path = os.path.join(WORK_DIR, f"print_{safe_uid}.scr")

        # 2. Флаги логики
        name_lower = safe_filename.lower()
        if "_model" in name_lower or "модель" in name_lower or "поперечник" in name_lower:
            smart_search = "true"
        is_smart = (smart_search and smart_search.lower() == "true")

        # 3. Генерация и запись LISP скрипта
        if not is_smart:
            lisp_code = _generate_lisp_script(safe_pdf_path, False, ctb)
            with open(scr_path, "w", encoding="cp1251") as f:
                f.write(lisp_code)

            cmd = [ACAD_PATH, "/i", safe_dwg_path, "/l", "ru-RU", "/s", scr_path]
            if profile:
                cmd.extend(["/p", profile])
            
            try:
                result = _run_accore(cmd, ACAD_TIMEOUT_SEC)
                stdout_text = _decode_acad_out(result.stdout)
            except subprocess.TimeoutExpired:
                return JSONResponse(status_code=504, content={"error": f"AutoCAD timeout {ACAD_TIMEOUT_SEC}s. Process killed.", "log": ""})
        else:
            lines_file = os.path.join(WORK_DIR, f"cad_lines_{safe_uid}.txt")
            dump_scr = os.path.join(WORK_DIR, f"dump_{safe_uid}.scr")
            with open(dump_scr, "w", encoding="ascii", errors="replace") as f:
                f.write(_build_dump_lisp(lines_file))

            cmd1 = [ACAD_PATH, "/i", safe_dwg_path, "/l", "ru-RU", "/s", dump_scr]
            if profile:
                cmd1.extend(["/p", profile])
            try:
                result1 = _run_accore(cmd1, DUMP_TIMEOUT_SEC)
                stdout_text = _decode_acad_out(result1.stdout)
                print(f"Dump done, out_len={len(stdout_text)}, lines_exists={os.path.exists(lines_file)}")
            except subprocess.TimeoutExpired:
                return JSONResponse(status_code=504, content={"error": "AutoCAD timeout on step 1 (dump frames)."})

            try:
                os.remove(dump_scr)
            except Exception:
                pass

            if not os.path.exists(lines_file):
                return JSONResponse(status_code=400, content={"error": "Failed to extract frames dump.", "log": stdout_text[-4000:]})

            unique_frames = _find_frames_from_dump(lines_file)
            try:
                os.remove(lines_file)
            except Exception:
                pass

            print(f"Found frames: {len(unique_frames)}")
            if not unique_frames:
                return JSONResponse(status_code=400, content={"error": "В Модели не найдено ни одной рамки.", "log": stdout_text[-4000:]})

            pdf_prefix = safe_pdf_path.replace(".pdf", "")
            with open(scr_path, "w", encoding="ascii", errors="replace") as f:
                f.write(_build_plot_lisp(unique_frames, pdf_prefix))

            cmd2 = [ACAD_PATH, "/i", safe_dwg_path, "/l", "ru-RU", "/s", scr_path]
            if profile:
                cmd2.extend(["/p", profile])
            try:
                result = _run_accore(cmd2, ACAD_TIMEOUT_SEC)
                stdout_text = _decode_acad_out(result.stdout)
            except subprocess.TimeoutExpired:
                return JSONResponse(status_code=504, content={"error": f"AutoCAD timeout {ACAD_TIMEOUT_SEC}s on step 2."})

        if "ERROR_NO_LAYOUTS" in stdout_text:
            print(f"ОШИБКА: В чертеже {safe_filename} нет настроенных листов!")
            return JSONResponse(status_code=400, content={"error": "В чертеже нет ни одного листа (Layout).", "log": stdout_text})

        # 5. Сборка PDF и удаление пустых листов
        if is_smart:
            try:
                pdf_prefix_base = safe_pdf_path.replace(".pdf", "")
                _merge_pdf_parts(pdf_prefix_base, safe_pdf_path)
            except ValueError as e:
                return JSONResponse(status_code=400, content={"error": str(e), "log": stdout_text[-4000:]})
        else:
            for f in glob.glob(safe_pdf_path.replace(".pdf", "") + "_ps_*.pdf"):
                try:
                    os.remove(f)
                except Exception:
                    pass

        _remove_empty_pages(safe_pdf_path)
        _trim_pdf_to_content(safe_pdf_path)

        print(f"Время выполнения: {time.time() - start_time:.1f} сек")
        if os.path.exists(safe_pdf_path):
            return FileResponse(path=safe_pdf_path, filename=safe_filename.replace(".dwg", ".pdf"), media_type='application/pdf')
        print("ОШИБКА ПЕЧАТИ:")
        print(stdout_text)
        return JSONResponse(status_code=500, content={"error": "Не удалось создать PDF. Проверьте консоль сервера.", "log": stdout_text[-4000:]})

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print(f"CONVERT ERROR:\n{err}")
        _kill_accore()
        return JSONResponse(status_code=500, content={"error": str(e), "log": err[-4000:]})
    finally:
        try:
            if dwg_path and os.path.exists(dwg_path):
                os.remove(dwg_path)
            if scr_path and os.path.exists(scr_path):
                os.remove(scr_path)
        except Exception as e:
            print(f"Ошибка при очистке временных файлов: {e}")

@app.post("/convert-office")
def convert_office(file: UploadFile = File(...)):
    if not _office_lock.acquire(timeout=600):
        return JSONResponse(status_code=503, content={"error": "Office конвертер занят, попробуйте позже."})
    try:
        return _convert_office_impl(file)
    finally:
        _office_lock.release()

def _convert_office_impl(file: UploadFile):
    safe_filename = file.filename.replace(" ", "_")
    ext = os.path.splitext(safe_filename)[1].lower()
    in_path = os.path.join(WORK_DIR, safe_filename)
    pdf_path = in_path.replace(ext, ".pdf")
    
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
        
    with open(in_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    start_time = time.time()
    import pythoncom
    try:
        if ext in [".doc", ".docx", ".rtf"]:
            print(f"Конвертация Word: {safe_filename}")
            pythoncom.CoInitialize()
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = False
            doc = word.Documents.Open(in_path, ReadOnly=True)
            doc.ExportAsFixedFormat(
                OutputFileName=pdf_path,
                ExportFormat=17,
                OpenAfterExport=False,
                OptimizeFor=0,
                CreateBookmarks=1,
                DocStructureTags=True,
                BitmapMissingFonts=True,
                UseISO19005_1=False
            )
            doc.Close(SaveChanges=False)
            word.Quit()
            
        elif ext in [".xls", ".xlsx"]:
            print(f"Конвертация Excel: {safe_filename}")
            import excel_export
            from pathlib import Path
            excel_export.excel_to_pdf(Path(in_path), Path(pdf_path))
            
            if excel_export._pdf_is_empty(Path(pdf_path)):
                return JSONResponse(status_code=500, content={"error": "Получен пустой PDF после экспорта из Excel", "log": ""})

        else:
            return JSONResponse(status_code=400, content={"error": f"Формат {ext} не поддерживается"})
            
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print(f"ОШИБКА OFFICE:\n{err}")
        subprocess.run('taskkill /F /IM WINWORD.EXE', shell=True)
        subprocess.run('taskkill /F /IM EXCEL.EXE', shell=True)
        return JSONResponse(status_code=500, content={"error": "Ошибка MS Office", "log": err})
    finally:
        try:
            os.remove(in_path)
        except Exception:
            pass
        if ext in [".doc", ".docx", ".rtf"]:
            pythoncom.CoUninitialize()
            
    print(f"Время выполнения: {time.time() - start_time:.1f} сек")
    
    if os.path.exists(pdf_path):
        return FileResponse(path=pdf_path, filename=safe_filename.replace(ext, ".pdf"), media_type='application/pdf')
    else:
        return JSONResponse(status_code=500, content={"error": "PDF не создан.", "log": ""})

if __name__ == "__main__":
    print("--------------------------------------------------")
    print(" Сервер AutoCAD Core Console запущен! (Стабильная версия)")
    print(" Ожидание чертежей на порту 8000...")
    print("--------------------------------------------------")
    uvicorn.run(app, host="0.0.0.0", port=8000)

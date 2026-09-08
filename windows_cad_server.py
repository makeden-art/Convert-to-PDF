import os
import subprocess
import time
import json
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Body
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

@app.post("/shutdown")
def shutdown_server():
    def _kill():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_kill, daemon=True).start()
    return {"status": "shutting down"}

ACAD_PATH = find_accoreconsole()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(SCRIPT_DIR, "cad_server_workdir")
os.makedirs(WORK_DIR, exist_ok=True)

SHARE_LOCAL_PATH = os.environ.get("SHARE_LOCAL_PATH", r"E:\share_test")
SHARE_UNC_PATH = os.environ.get("SHARE_UNC_PATH", r"\\192.168.88.14\share_test")
_office_lock = threading.Lock()
ACAD_TIMEOUT_SEC = 3600

SMB_CACHE_FILE = os.path.join(SCRIPT_DIR, "smb_credentials.json")

def _load_smb_credentials() -> dict:
    defaults = {
        "192.168.88.22": ["caduser", "smbpasswd"],
        "192.168.88.13": ["smbuser", "smbuser"],
        "192.168.88.14": ["makeden", "Acfbfba2dt"],
    }
    if os.path.exists(SMB_CACHE_FILE):
        try:
            with open(SMB_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                defaults.update(data)
        except Exception as e:
            print(f"[SMB] Ошибка чтения {SMB_CACHE_FILE}: {e}")
    return defaults

def _save_smb_credentials(creds: dict):
    try:
        with open(SMB_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(creds, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[SMB] Ошибка записи {SMB_CACHE_FILE}: {e}")

SMB_CREDENTIALS = _load_smb_credentials()

def ensure_smb_connection(unc_path: str, username: str = None, password: str = None):
    """Автоматически подключает сетевую шару, если она недоступна или переданы новые креды"""
    if not unc_path or not unc_path.startswith(r"\\"):
        return
    parts = unc_path.lstrip("\\").split("\\")
    if not parts:
        return
    server_ip = parts[0]
    share_name = parts[1] if len(parts) > 1 else ""
    share_root = rf"\\{server_ip}\{share_name}" if share_name else rf"\\{server_ip}"

    # Если переданы явные креды — сохраняем/обновляем
    if username and password:
        SMB_CREDENTIALS[server_ip] = [username, password]
        _save_smb_credentials(SMB_CREDENTIALS)

    if os.path.exists(share_root):
        return

    creds = SMB_CREDENTIALS.get(server_ip)
    if creds:
        user, pwd = creds
        try:
            print(f"[SMB] Подключаем сетевую шару {share_root} под пользователем {user}...")
            subprocess.run(["cmdkey", f"/add:{server_ip}", f"/user:{user}", f"/pass:{pwd}"], capture_output=True, text=True)
            subprocess.run(["net", "use", share_root, f"/user:{user}", pwd, "/y"], capture_output=True, text=True)
        except Exception as e:
            print(f"[SMB] Ошибка подключения {share_root}: {e}")

@app.post("/api/smb/auth")
def api_smb_auth(data: dict = Body(...)):
    """Регистрация сетевой шары и её реквизитов от портала"""
    server = data.get("server", "").strip()
    share = data.get("share", "").strip()
    user = data.get("username", "").strip()
    pwd = data.get("password", "").strip()
    if not server or not user:
        return JSONResponse(status_code=400, content={"error": "Не указаны server или username"})
    
    unc_path = rf"\\{server}\{share}" if share else rf"\\{server}"
    ensure_smb_connection(unc_path, username=user, password=pwd)
    accessible = os.path.exists(unc_path)
    return {
        "ok": True,
        "unc": unc_path,
        "accessible": accessible,
        "server": server,
        "username": user
    }

def unc_to_local(path: str) -> str:
    if not path:
        return path
    import re
    p = path.replace("/", "\\")
    p_clean = "\\\\" + re.sub(r"\\+", r"\\", p).lstrip("\\")
    unc_clean = "\\\\" + re.sub(r"\\+", r"\\", SHARE_UNC_PATH.replace("/", "\\")).lstrip("\\").rstrip("\\")
    local_norm = SHARE_LOCAL_PATH.rstrip("\\")
    if p_clean.lower().startswith(unc_clean.lower()):
        return local_norm + p_clean[len(unc_clean):]
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

    if not force_smart:
        out_dir = safe_pdf_path.replace("\\", "/").replace(".pdf", "")
        return f"""(command "_.UNDO" "_Control" "_None")
(setvar "TILEMODE" 0)
(setvar "EXPERT" 5)
(setvar "CMDDIA" 0)
(setvar "FILEDIA" 0)
(setvar "XREFNOTIFY" 0)
(setvar "PROXYSHOW" 1)
(setvar "PROXYNOTICE" 0)
(setvar "XLOADCTL" 2)
(setvar "DEMANDLOAD" 3)
(setvar "INDEXCTL" 3)
(setvar "REGENMODE" 0)
(setvar "DRAWORDERCTL" 0)
(setq dwgDir (getvar "DWGPREFIX"))
(setq paths (getenv "ACAD"))
(if (and dwgDir (not (vl-string-search dwgDir paths)))
  (setenv "ACAD" (strcat paths ";" dwgDir ";" dwgDir "Внешние ссылки;" dwgDir "Xref;" dwgDir "Xrefs"))
)
{common_path_lisp}
(defun ExportPaperSpace ()
  (setq lays (quote ()))
  (setq dict (dictsearch (namedobjdict) "ACAD_LAYOUT"))
  (foreach item dict
    (if (= (car item) 3)
      (if (/= (cdr item) "Model")
        (setq lays (cons (cdr item) lays))
      )
    )
  )
  (setq lays (reverse lays))
  (setq i 1)
  (setq out-dir "{out_dir}")
  (foreach lay lays
    (setvar "CTAB" lay)
    (setq pdfPath (strcat out-dir "_" (itoa i) ".pdf"))
    (if (findfile pdfPath) (vl-file-delete pdfPath))
    (vl-catch-all-apply
      (function (lambda ()
        (command "_.-EXPORT" "_PDF" "_Current" "_N" pdfPath)
      ))
    )
    (setq i (1+ i))
  )
)
(ExportPaperSpace)
(command "_.QUIT" "_Y")
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
  
  (setq ss (ssget "X" (quote ((0 . "*POLYLINE") (8 . "*ШТАМП*,*РАМКА*,*Штамп*,*рамка*") (410 . "Model")))))
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
  (setvar "EXPERT" 5)
  (setvar "CMDDIA" 0)
  (setvar "FILEDIA" 0)
  (setq lays (quote ()))
  (setq dict (dictsearch (namedobjdict) "ACAD_LAYOUT"))
  (foreach item dict
    (if (= (car item) 3)
      (if (/= (cdr item) "Model")
        (setq lays (cons (cdr item) lays))
      )
    )
  )
  (setq lays (reverse lays))
  (setq i 1)
  (setq out-dir "{safe_pdf_path.replace(chr(92), chr(47)).replace('.pdf', '')}")
  (foreach lay lays
    (setvar "CTAB" lay)
    (setq pdfPath (strcat out-dir "_" (itoa i) ".pdf"))
    (if (findfile pdfPath) (vl-file-delete pdfPath))
    (vl-catch-all-apply
      (function (lambda ()
        (print (strcat "STARTING_EXPORT_FOR_" lay))
        (command "_.-EXPORT" "_PDF" "_Current" "_N" pdfPath)
        (print (strcat "FINISHED_EXPORT_FOR_" lay))
      ))
    )
    (setq i (1+ i))
  )
)

(setq force-smart {force_smart_val})
(if force-smart
  (ExportModelFrames)
  (ExportPaperSpace)
)
(command "_.QUIT" "_Y")"""
    return lisp_code + "\n"

def _merge_pdf_parts(pdf_prefix_base: str, output_pdf_path: str, stamp_result: list = None):
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
            # Страница с реальным чертежом должна иметь чертежи и текст
            drawings = page.get_drawings()
            text = page.get_text().strip()
            # Если почти нет векторных элементов (< 10) и нет текста - это пустая заготовка/артефакт
            if len(drawings) < 10 and len(text) < 5:
                continue
            
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
            total = len(pix.samples)
            if total == 0:
                continue
            white = sum(1 for b in pix.samples if b > 245)
            if white / total < 0.999:  # есть реальный контент
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

# Стандартные ширины рулонов и листов (мм) по ГОСТ 2.301-68 / СПДС
STANDARD_WIDTHS_MM = (297.0, 420.0, 594.0, 610.0, 841.0, 914.0)
WIDTH_TOL = 20.0
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

def _is_standard_width(side: float, tol: float = WIDTH_TOL) -> bool:
    return any(abs(side - std) <= tol for std in STANDARD_WIDTHS_MM)

def _paper_for_frame(w: float, h: float) -> str:
    """
    Выбор формата бумаги плоттера.
    Ширина рулона/листа фиксирована (297, 420, 594, 610, 841, 914),
    а длина может быть произвольной (в т.ч. удлиненной/нестандартной).
    AutoCAD центрирует и масштабирует Fit, а затем _trim_pdf_to_content
    обрезает поля строго до точного размера рамки чертежа.
    """
    long_side = max(w, h)
    short_side = min(w, h)
    is_landscape = (w >= h)

    if is_landscape:
        if long_side <= 430 and short_side <= 310:
            return "ISO_full_bleed_A3_(420.00_x_297.00_MM)"
        if long_side <= 610 and short_side <= 430:
            return "ISO_full_bleed_A2_(594.00_x_420.00_MM)"
        if long_side <= 860 and short_side <= 610:
            return "ISO_full_bleed_A1_(841.00_x_594.00_MM)"
        if long_side <= 1200 and short_side <= 860:
            return "ISO_full_bleed_A0_(1189.00_x_841.00_MM)"
        # Длинные рулоны (> 1189 мм) - выбираем максимальный A0 (подрезка восстановит точный bbox)
        return "ISO_full_bleed_A0_(1189.00_x_841.00_MM)"
    else:
        if short_side <= 310 and long_side <= 430:
            return "ISO_full_bleed_A3_(297.00_x_420.00_MM)"
        if short_side <= 430 and long_side <= 610:
            return "ISO_full_bleed_A2_(420.00_x_594.00_MM)"
        if short_side <= 610 and long_side <= 860:
            return "ISO_full_bleed_A1_(594.00_x_841.00_MM)"
        return "ISO_full_bleed_A0_(841.00_x_1189.00_MM)"


def _trim_pdf_to_content(pdf_path: str, pad_pt: float = 3.0):
    """
    Убирает пустые поля после печати рамки Model на более крупный ISO-лист
    (например рамка А3х2 840×297 напечатана на А1 841×594).
    Точная векторная обрезка по границам чертежа.
    """
    if not os.path.exists(pdf_path):
        return
    try:
        import fitz
        doc = fitz.open(pdf_path)
        changed = False
        for page in doc:
            rects = [d['rect'] for d in page.get_drawings()]
            if not rects:
                continue
            bbox = rects[0]
            for r in rects[1:]:
                bbox |= r
            
            # Если страница заметно больше содержимого (>30 пунктов / >10 мм с любой стороны)
            diff_w = page.rect.width - bbox.width
            diff_h = page.rect.height - bbox.height
            # Защита: обрезка применяется только если сам чертеж адекватных размеров (> 150 мм по каждой стороне)
            if (diff_w > 30 or diff_h > 30) and bbox.width > 400 and bbox.height > 400:
                new_crop = fitz.Rect(
                    max(0, bbox.x0 - pad_pt),
                    max(0, bbox.y0 - pad_pt),
                    min(page.rect.width, bbox.x1 + pad_pt),
                    min(page.rect.height, bbox.y1 + pad_pt)
                )
                page.set_cropbox(new_crop)
                page.set_mediabox(new_crop)
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
            print(f"Trimmed oversized page margins in: {pdf_path}")
        else:
            doc.close()
    except Exception as e:
        print(f"Не удалось скорректировать поля PDF: {e}")

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
  (setq ss (ssget "X" (quote ((0 . "*POLYLINE")))))
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
                (if (and (> w 200.0) (> h 200.0) (< w 10000.0) (< h 10000.0))
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
    with open(lsp_path, "w", encoding="cp1251", errors="replace") as f:
        f.write(lisp_code)
    load_path = lsp_path.replace("\\", "/")
    with open(scr_path, "w", encoding="cp1251", errors="replace") as f:
        f.write(f'(load "{load_path}")\n')

def _is_sheet_size(w: float, h: float) -> bool:
    """
    Рамка листа в Model: ширина рулона стандартна (297, 420, 594, 610, 841, 914 мм),
    а длина может быть произвольной (в т.ч. удлиненной/нестандартной по ГОСТ 2.301-68).
    """
    a, b = (w, h) if w >= h else (h, w)  # a - длина, b - ширина/высота
    # Минимальный формат А4 (210×297) или рулон от 200 мм; максимальная длина до 10 метров
    if b < 200.0 or a < 280.0 or b > 1500.0 or a > 10000.0:
        return False
    # Короткая сторона (ширина рулона) должна быть стандартной
    if _is_standard_width(b):
        return True
    # Либо длинная сторона стандартная (вертикальный лист)
    if _is_standard_width(a) and b >= 200.0:
        return True
    # Классический лист А4 (210х297)
    if abs(b - 210.0) <= WIDTH_TOL and abs(a - 297.0) <= WIDTH_TOL:
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
    frames = []
    n = len(hl)
    for i in range(n):
        x1a, x2a, ya = hl[i]
        for j in range(i + 1, n):
            x1b, x2b, yb = hl[j]
            overlap_min = max(x1a, x1b)
            overlap_max = min(x2a, x2b)
            if overlap_max - overlap_min < 150:
                continue
            height = abs(yb - ya)
            if height < 200:
                continue
            ymin, ymax = min(ya, yb), max(ya, yb)
            
            valid_v = []
            if vl:
                for vx, vy0, vy1 in vl:
                    if vx >= overlap_min - 10.0 and vx <= overlap_max + 10.0:
                        if min(vy0, vy1) <= ymin + 10.0 and max(vy0, vy1) >= ymax - 10.0:
                            valid_v.append(vx)
            valid_v.sort()
            
            if require_v:
                for vidx1 in range(len(valid_v)):
                    for vidx2 in range(vidx1 + 1, len(valid_v)):
                        v_left = valid_v[vidx1]
                        v_right = valid_v[vidx2]
                        w = v_right - v_left
                        if w < 150:
                            continue
                        if w / height > 3.0 or height / w > 3.0:
                            continue
                        frames.append((v_left, ymin, v_right, ymax, w, height))
            else:
                frames.append((overlap_min, ymin, overlap_max, ymax, overlap_max - overlap_min, height))
    return frames


def _keep_outermost(frames):
    def contains(outer, inner, pad=-2.0):
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
            if _is_sheet_size(fr[4], fr[5]) or (fr[4] > 200 and fr[5] > 200):
                stamp_line_frames.append(fr[:6])

    stamp_frames = _keep_outermost(stamp_poly_frames) if stamp_poly_frames else []
    if not stamp_frames and stamp_line_frames:
        stamp_frames = _keep_outermost(stamp_line_frames)
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
                _frames_overlap(chosen, L, pad=-2.0) or _frame_contains(chosen, L, pad=20.0)
                for L in line_outer
            ):
                continue

            expanded.append(chosen)
        expanded.extend(L[:6] for L in line_outer)
        result = _keep_outermost(expanded)
        result = _pick_widest_per_row(result)
        from collections import Counter
        sizes = dict(Counter((round(f[4]), round(f[5])) for f in result))
        print(f"Stamp sheets: {len(result)} sizes={sizes}")
        return result

    # Если найдены готовые полилинии рамок (Bounds / Штамп) — используем строго их!
    candidate_polys = stamp_poly_frames if stamp_poly_frames else bounds_frames
    if candidate_polys:
        result = _keep_outermost(candidate_polys)
        result.sort(key=lambda fr: (-fr[1], fr[0]))
        from collections import Counter
        print(f"Poly sheet frames: {len(result)} sizes={dict(Counter((round(f[4]), round(f[5])) for f in result))}")
        return result

    # --- Запасной путь: только если полилиний рамок вообще нет в чертеже ---
    frames = []
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
    margin = 0.0
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
    smb_user: str = Form(None),
    smb_pass: str = Form(None),
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
            ensure_smb_connection(smb_dwg_path, username=smb_user, password=smb_pass)
            local_path = unc_to_local(smb_dwg_path)
            if os.path.exists(local_path):
                safe_dwg_path = local_path
                safe_filename = os.path.basename(local_path)
                dwg_path = None
            elif os.path.exists(smb_dwg_path):
                # Если файл на удалённой SMB-шаре (например, \\192.168.88.22\...):
                # Копируем папку с файлом и ссылками на локальный SSD для ускорения работы AutoCAD в 10 раз
                safe_filename = os.path.basename(smb_dwg_path)
                remote_dir = os.path.dirname(smb_dwg_path)
                local_job_dir = os.path.join(WORK_DIR, f"proj_{safe_uid}")
                try:
                    print(f"[ACCEL] Копируем проект из {remote_dir} на локальный SSD {local_job_dir}...")
                    shutil.copytree(remote_dir, local_job_dir, dirs_exist_ok=True)
                    safe_dwg_path = os.path.join(local_job_dir, safe_filename)
                    dwg_path = local_job_dir # чтобы удалилось в finally
                except Exception as e:
                    print(f"[ACCEL WARN] Не удалось скопировать проект локально ({e}), читаем напрямую с сети...")
                    safe_dwg_path = smb_dwg_path
                    dwg_path = None
            else:
                return JSONResponse(status_code=400, content={"error": f"Файл не найден по локальному или сетевому пути: {local_path} / {smb_dwg_path}"})
        elif file and file.filename:
            safe_filename = file.filename.replace(" ", "_")
            dwg_path = os.path.join(WORK_DIR, f"input_{safe_uid}.dwg")
            with open(dwg_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            safe_dwg_path = dwg_path
        else:
            return JSONResponse(status_code=400, content={"error": "Не передан файл."})

        # Итоговые пути
        safe_pdf_path = os.path.join(WORK_DIR, f"input_{safe_uid}.pdf")
        scr_path = os.path.join(WORK_DIR, f"print_{safe_uid}.scr")
        lsp_path = os.path.join(WORK_DIR, f"print_{safe_uid}.lsp")

        # 2. Флаги логики
        name_lower = safe_filename.lower()
        if "_model" in name_lower or "модель" in name_lower or "поперечник" in name_lower:
            smart_search = "true"
        is_smart = (smart_search and smart_search.lower() == "true")

        # 3. Генерация и запись LISP скрипта
        if not is_smart:
            lisp_code = _generate_lisp_script(safe_pdf_path, False, ctb)
            with open(scr_path, "w", encoding="cp1251", errors="replace") as f:
                f.write(lisp_code)
            with open("C:/CadServer/cad_server_workdir/LAST_RUN.scr", "w", encoding="cp1251", errors="replace") as f:
                f.write(lisp_code)

            cmd = [ACAD_PATH, "/i", safe_dwg_path, "/l", "ru-RU", "/s", scr_path]
            if profile:
                cmd.extend(["/p", profile])
            
            try:
                result = _run_accore(cmd, ACAD_TIMEOUT_SEC)
                stdout_text = _decode_acad_out(result.stdout)
            except subprocess.TimeoutExpired:
                _kill_accore()
                # Проверяем, успели ли сгенерироваться листы до таймаута
                pdf_prefix_base = safe_pdf_path.replace(".pdf", "")
                parts = glob.glob(f"{pdf_prefix_base}_[0-9]*.pdf")
                if parts:
                    print(f"[TIMEOUT WARN] Таймаут {ACAD_TIMEOUT_SEC}s, но найдены {len(parts)} сгенерированных листов! Продолжаем сборку...")
                    stdout_text = f"AutoCAD timeout {ACAD_TIMEOUT_SEC}s, salvaged {len(parts)} parts."
                else:
                    return JSONResponse(status_code=504, content={"error": f"AutoCAD timeout {ACAD_TIMEOUT_SEC}s. Process killed.", "log": ""})
        else:
            lines_file = os.path.join(WORK_DIR, f"cad_lines_{safe_uid}.txt")
            dump_scr = os.path.join(WORK_DIR, f"dump_{safe_uid}.scr")
            with open(dump_scr, "w", encoding="cp1251", errors="replace") as f:
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
            with open(scr_path, "w", encoding="cp1251", errors="replace") as f:
                f.write(_build_plot_lisp(unique_frames, pdf_prefix))

            cmd2 = [ACAD_PATH, "/i", safe_dwg_path, "/l", "ru-RU", "/s", scr_path]
            if profile:
                cmd2.extend(["/p", profile])
            try:
                result = _run_accore(cmd2, ACAD_TIMEOUT_SEC)
                stdout_text = _decode_acad_out(result.stdout)
            except subprocess.TimeoutExpired:
                _kill_accore()
                pdf_prefix_base = safe_pdf_path.replace(".pdf", "")
                parts = glob.glob(f"{pdf_prefix_base}_[0-9]*.pdf")
                if parts:
                    print(f"[TIMEOUT WARN] Таймаут {ACAD_TIMEOUT_SEC}s на шаге 2, но найдены {len(parts)} листов! Продолжаем сборку...")
                    stdout_text = f"AutoCAD timeout {ACAD_TIMEOUT_SEC}s on step 2, salvaged {len(parts)} parts."
                else:
                    return JSONResponse(status_code=504, content={"error": f"AutoCAD timeout {ACAD_TIMEOUT_SEC}s on step 2."})

        if "ERROR_NO_LAYOUTS" in stdout_text:
            print(f"ОШИБКА: В чертеже {safe_filename} нет настроенных листов!")
            return JSONResponse(status_code=400, content={"error": "В чертеже нет ни одного листа (Layout).", "log": stdout_text})

        # 5. Сборка PDF и удаление пустых листов
        pdf_prefix_base = safe_pdf_path.replace(".pdf", "")
        parts = sorted(
            glob.glob(f"{pdf_prefix_base}_[0-9]*.pdf"),
            key=lambda f: int(f.rsplit("_", 1)[-1].replace(".pdf", "")) if f.rsplit("_", 1)[-1].replace(".pdf", "").isdigit() else 0
        )
        if parts:
            try:
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
                if os.path.isdir(dwg_path):
                    shutil.rmtree(dwg_path, ignore_errors=True)
                else:
                    os.remove(dwg_path)
            if scr_path and os.path.exists(scr_path):
                try: os.remove(scr_path)
                except: pass
            if lsp_path and os.path.exists(lsp_path):
                try: os.remove(lsp_path)
                except: pass
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

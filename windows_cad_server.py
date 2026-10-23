import os
import subprocess
import time
import json
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Body, BackgroundTasks
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

CAD_TASKS = {}
_tasks_lock = threading.Lock()

def update_cad_task(task_id: str, **kwargs):
    if not task_id:
        return
    with _tasks_lock:
        if task_id not in CAD_TASKS:
            CAD_TASKS[task_id] = {
                "task_id": task_id,
                "status": "running",
                "stage": "init",
                "stage_text": "Инициализация...",
                "current_sheet": 0,
                "total_sheets": 0,
                "percent": 0,
                "start_time": time.time(),
                "updated_at": time.time(),
                "eta_sec": None,
            }
        task = CAD_TASKS[task_id]
        task.update(kwargs)
        task["updated_at"] = time.time()
        
        total = task.get("total_sheets", 0)
        curr = task.get("current_sheet", 0)
        start = task.get("start_time", time.time())
        elapsed = time.time() - start
        
        if total > 0:
            if task.get("stage") == "plotting":
                plot_pct = int(20 + (curr / total) * 70)
                task["percent"] = min(90, max(20, plot_pct))
                if curr > 0:
                    sec_per_sheet = elapsed / max(1, curr)
                    rem_sheets = max(0, total - curr)
                    task["eta_sec"] = int(rem_sheets * sec_per_sheet) + 5
            elif task.get("stage") == "merging":
                task["percent"] = 92
                task["eta_sec"] = 5
            elif task.get("stage") == "done":
                task["percent"] = 100
                task["eta_sec"] = 0

@app.get("/job-progress/{job_id}")
def get_job_progress(job_id: str):
    with _tasks_lock:
        task = CAD_TASKS.get(job_id)
        if not task:
            return {"task_id": job_id, "status": "unknown", "percent": 0, "stage_text": "Ожидание..."}
        return dict(task)

@app.post("/job-cancel/{job_id}")
@app.post("/cancel/{job_id}")
def cancel_cad_job(job_id: str):
    with _tasks_lock:
        task = CAD_TASKS.get(job_id)
        if task:
            task["status"] = "cancelled"
            task["stage"] = "cancelled"
            task["stage_text"] = "Отменено пользователем"
            task["updated_at"] = time.time()
    print(f"[CANCEL] Получен запрос отмены для {job_id}. Завершаем accoreconsole...")
    _kill_accore()
    return {"ok": True, "task_id": job_id, "status": "cancelled"}

ACAD_PATH = find_accoreconsole()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(SCRIPT_DIR, "cad_server_workdir")
os.makedirs(WORK_DIR, exist_ok=True)

def _safe_remove(path: str):
    """Безопасное удаление файла после передачи клиенту"""
    try:
        if path and os.path.exists(path):
            os.remove(path)
            print(f"[CLEANUP] Удален временный файл: {os.path.basename(path)}")
    except Exception as e:
        print(f"[CLEANUP] Не удалось удалить {path}: {e}")

def cleanup_old_work_files(max_age_sec: int = 1800, clean_all_on_start: bool = False):
    """Очищает старые временные файлы input_*, print_*, cad_lines_*, dump_* из рабочей папки"""
    try:
        now = time.time()
        count = 0
        patterns = ["input_*", "print_*", "cad_lines_*", "dump_*", "~$*"]
        for pat in patterns:
            for f in glob.glob(os.path.join(WORK_DIR, pat)):
                try:
                    if clean_all_on_start or (now - os.path.getmtime(f) > max_age_sec):
                        if os.path.isdir(f):
                            shutil.rmtree(f, ignore_errors=True)
                        else:
                            os.remove(f)
                        count += 1
                except Exception:
                    pass
        if count > 0:
            print(f"[CLEANUP] Удалено временных файлов: {count}")
    except Exception as e:
        print(f"[CLEANUP] Ошибка очистки рабочей папки: {e}")

# Очистка при старте сервера
cleanup_old_work_files(clean_all_on_start=True)

def _periodic_cleanup_worker():
    while True:
        time.sleep(900)  # каждые 15 минут
        cleanup_old_work_files(max_age_sec=1800)

_cleaner_thread = threading.Thread(target=_periodic_cleanup_worker, daemon=True)
_cleaner_thread.start()

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
        return f"""(vl-load-com)
(command "_.UNDO" "_Control" "_None")
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
(setvar "DRAWORDERCTL" 3)
(setvar "SORTENTS" 127)
(setq dwgDir (getvar "DWGPREFIX"))
(setq paths (getenv "ACAD"))
(if (and dwgDir (not (vl-string-search dwgDir paths)))
  (setenv "ACAD" (strcat paths ";" dwgDir ";" dwgDir "Внешние ссылки;" dwgDir "Xref;" dwgDir "Xrefs"))
)
{common_path_lisp}
(defun ExportPaperSpace ( / dict layName layEnt tabOrder sortedLays i out-dir pdfPath item lay )
  (setvar "TILEMODE" 0)
  (setvar "EXPERT" 5)
  (setvar "CMDDIA" 0)
  (setvar "FILEDIA" 0)
  (setvar "DRAWORDERCTL" 3)
  (setvar "SORTENTS" 127)
  (setq dict (dictsearch (namedobjdict) "ACAD_LAYOUT"))
  (setq sortedLays (quote ()))
  (while dict
    (if (= (caar dict) 3)
      (progn
        (setq layName (cdar dict))
        (setq dict (cdr dict))
        (if (and dict (= (caar dict) 350))
          (progn
            (setq layEnt (entget (cdar dict)))
            (setq tabOrder (cdr (assoc 71 layEnt)))
            (if (/= layName "Model")
              (setq sortedLays (cons (cons tabOrder layName) sortedLays))
            )
          )
        )
      )
    )
    (setq dict (cdr dict))
  )
  (setq sortedLays (vl-sort sortedLays (function (lambda (a b) (< (car a) (car b))))))
  (setq i 1)
  (setq out-dir "{out_dir}")
  (foreach item sortedLays
    (setq lay (cdr item))
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
                      (if (and (> w 150) (> h 150) (< w 15000) (< h 15000))
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
  
  (setq ss (ssget "X" (quote ((0 . "*POLYLINE") (8 . "*ШТАМП*,*РАМКА*,*Штамп*,*рамка*,_Штамп_рамка,*border*,*frame*") (410 . "Model")))))
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
                (if (and (> w 150) (> h 150) (< w 15000) (< h 15000))
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
    (command "_.-PLOT" "_Y" "Model" "DWG To PDF.pc3" paper "_M" "_L" "_N" "_W" (list (car frm) (cadr frm)) (list (caddr frm) (cadddr frm)) "1:1" "_C" "_Y" "monochrome.ctb" "_Y" "_W" outpath "_N" "_Y")
    (setq idx (1+ idx))
  )
)

(defun ExportPaperSpace ( / dict layName layEnt tabOrder sortedLays i out-dir pdfPath item lay )
  (setvar "TILEMODE" 0)
  (setvar "EXPERT" 5)
  (setvar "CMDDIA" 0)
  (setvar "FILEDIA" 0)
  (setvar "DRAWORDERCTL" 3)
  (setvar "SORTENTS" 127)
  (setq dict (dictsearch (namedobjdict) "ACAD_LAYOUT"))
  (setq sortedLays (quote ()))
  (while dict
    (if (= (caar dict) 3)
      (progn
        (setq layName (cdar dict))
        (setq dict (cdr dict))
        (if (and dict (= (caar dict) 350))
          (progn
            (setq layEnt (entget (cdar dict)))
            (setq tabOrder (cdr (assoc 71 layEnt)))
            (if (/= layName "Model")
              (setq sortedLays (cons (cons tabOrder layName) sortedLays))
            )
          )
        )
      )
    )
    (setq dict (cdr dict))
  )
  (setq sortedLays (vl-sort sortedLays (function (lambda (a b) (< (car a) (car b))))))
  (setq i 1)
  (setq out-dir "{safe_pdf_path.replace(chr(92), chr(47)).replace('.pdf', '')}")
  (foreach item sortedLays
    (setq lay (cdr item))
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
    Поддерживает стандартные форматы от А4 до А0, а также удлиненные/нестандартные форматы.
    AutoCAD центрирует и масштабирует Fit, а затем _trim_pdf_to_content
    обрезает поля строго до точного размера рамки чертежа.
    """
    long_side = max(w, h)
    short_side = min(w, h)
    is_landscape = (w >= h)

    if is_landscape:
        if long_side <= 310 and short_side <= 220:
            return "ISO_full_bleed_A4_(297.00_x_210.00_MM)"
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
        if short_side <= 220 and long_side <= 310:
            return "ISO_full_bleed_A4_(210.00_x_297.00_MM)"
        if short_side <= 310 and long_side <= 430:
            return "ISO_full_bleed_A3_(297.00_x_420.00_MM)"
        if short_side <= 430 and long_side <= 610:
            return "ISO_full_bleed_A2_(420.00_x_594.00_MM)"
        if short_side <= 610 and long_side <= 860:
            return "ISO_full_bleed_A1_(594.00_x_841.00_MM)"
        return "ISO_full_bleed_A0_(841.00_x_1189.00_MM)"


def _trim_pdf_to_content(pdf_path: str, frames: list = None):
    """
    Убирает пустые поля после печати рамки Model на более крупный ISO-лист
    (например рамка 626×297 или 840×297 напечатана на А1 841×594).
    Если переданы frames (точные размеры рамок из Модели), центрирует и обрезает
    каждый лист строго по размеру соответствующей рамки.
    """
    if not os.path.exists(pdf_path):
        return
    try:
        import fitz
        doc = fitz.open(pdf_path)
        changed = False
        num_pages = len(doc)
        
        for idx in range(num_pages):
            page = doc[idx]
            mb = page.mediabox
            page_w_pt = mb.width
            page_h_pt = mb.height

            # 1. Если известны точные размеры исходной рамки
            if frames and idx < len(frames):
                frm = frames[idx]
                fw_mm = float(frm[4])
                fh_mm = float(frm[5])
                # Определяем ориентацию (w >= h => landscape)
                if page_w_pt >= page_h_pt:
                    target_w_mm = max(fw_mm, fh_mm)
                    target_h_mm = min(fw_mm, fh_mm)
                else:
                    target_w_mm = min(fw_mm, fh_mm)
                    target_h_mm = max(fw_mm, fh_mm)
                
                target_w_pt = target_w_mm / 25.4 * 72.0
                target_h_pt = target_h_mm / 25.4 * 72.0
                
                # Обрезаем только если бумага заметно больше рамки (> 5 мм = ~14 pt)
                if (page_w_pt - target_w_pt > 14.0) or (page_h_pt - target_h_pt > 14.0):
                    cx = (mb.x0 + mb.x1) / 2.0
                    cy = (mb.y0 + mb.y1) / 2.0
                    new_x0 = cx - (target_w_pt / 2.0)
                    new_y0 = cy - (target_h_pt / 2.0)
                    new_x1 = cx + (target_w_pt / 2.0)
                    new_y1 = cy + (target_h_pt / 2.0)
                    
                    new_box = fitz.Rect(new_x0, new_y0, new_x1, new_y1)
                    page.set_cropbox(new_box)
                    page.set_mediabox(new_box)
                    changed = True
                    print(f"Page {idx+1}: trimmed to exact frame {target_w_mm:.1f}x{target_h_mm:.1f} mm")
                continue

            # 2. Запасной вариант (векторный анализ bbox чертежа)
            rects = [d['rect'] for d in page.get_drawings()]
            if not rects:
                continue
            bbox = rects[0]
            for r in rects[1:]:
                bbox |= r
            
            diff_w = page.rect.width - bbox.width
            diff_h = page.rect.height - bbox.height
            if (diff_w > 30 or diff_h > 30) and bbox.width > 400 and bbox.height > 400:
                crop_x0 = max(mb.x0, mb.x0 + bbox.x0)
                crop_y0 = max(mb.y0, mb.y0 + bbox.y0)
                crop_x1 = min(mb.x1, mb.x0 + bbox.x1)
                crop_y1 = min(mb.y1, mb.y0 + bbox.y1)

                w_mm = (crop_x1 - crop_x0) / 72.0 * 25.4
                h_mm = (crop_y1 - crop_y0) / 72.0 * 25.4

                for std_w in STANDARD_WIDTHS_MM:
                    if abs(h_mm - std_w) <= 6.0:
                        std_pt = std_w / 25.4 * 72.0
                        delta = std_pt - (crop_y1 - crop_y0)
                        crop_y0 = max(mb.y0, crop_y0 - delta / 2.0)
                        crop_y1 = min(mb.y1, crop_y0 + std_pt)
                        break
                    elif abs(w_mm - std_w) <= 6.0:
                        std_pt = std_w / 25.4 * 72.0
                        delta = std_pt - (crop_x1 - crop_x0)
                        crop_x0 = max(mb.x0, crop_x0 - delta / 2.0)
                        crop_x1 = min(mb.x1, crop_x0 + std_pt)
                        break

                new_box = fitz.Rect(crop_x0, crop_y0, crop_x1, crop_y1)
                page.set_cropbox(new_box)
                page.set_mediabox(new_box)
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
            (if (and (> dx 150.0) (< dy {ORTHO_TOL}))
              (write-line
                (strcat "H;"
                        (rtos (min (car p1) (car p2)) 2 3) ";"
                        (rtos (max (car p1) (car p2)) 2 3) ";"
                        (rtos (cadr p1) 2 3) ";"
                        lay)
                f)
            )
            (if (and (> dy 150.0) (< dx {ORTHO_TOL}))
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
                (if (and (> w 150.0) (> h 150.0) (< w 15000.0) (< h 15000.0))
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
    Рамка листа в Model: минимальный размер 150 мм (А5/А4),
    максимальный размер до 15000 мм (удлиненные рулоны/трассы по ГОСТ 2.301-68).
    """
    a, b = (w, h) if w >= h else (h, w)
    if b < 150.0 or a < 150.0 or b > 5000.0 or a > 15000.0:
        return False
    return True


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


def _rects_from_hv(hl, vl, tol: float = 3.0, require_v=True):
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
            if height < 150:
                continue
            ymin, ymax = min(ya, yb), max(ya, yb)
            
            valid_v = []
            if vl:
                for vx, vy0, vy1 in vl:
                    if vx >= overlap_min - tol and vx <= overlap_max + tol:
                        v_min, v_max = min(vy0, vy1), max(vy0, vy1)
                        if abs(v_min - ymin) <= tol and abs(v_max - ymax) <= tol:
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
    unique = []
    for fr in outer:
        if not any(abs(fr[0] - u[0]) < 30 and abs(fr[1] - u[1]) < 30 for u in unique):
            unique.append(fr[:6])
    return unique


def _sort_frames_top_bottom_left_right(frames, row_tol: float = 150.0):
    """
    Сортировка рамок чертежей:
    Сверху вниз (по строкам, Ymax убывает),
    Слева направо внутри каждой строки (Xmin возрастает).
    row_tol — допуск по вертикали для объединения рамок в одну строку.
    """
    if not frames:
        return []
    
    # Сортируем предварительно по убыванию верхней границы Ymax (индекс 3)
    sorted_by_y = sorted(frames, key=lambda f: -f[3])
    
    rows = []
    for f in sorted_by_y:
        f_ymax = f[3]
        placed = False
        for row in rows:
            if abs(row["ref_y"] - f_ymax) <= row_tol:
                row["frames"].append(f)
                placed = True
                break
        if not placed:
            rows.append({"ref_y": f_ymax, "frames": [f]})
            
    # Сортируем каждую строку слева направо (по Xmin, индекс 0)
    result = []
    for r in rows:
        r["frames"].sort(key=lambda f: f[0])
        result.extend(r["frames"])
        
    return result


def _find_frames_from_dump(lines_file: str):
    """
    Умный поиск рамок в пространстве Model (в т.ч. нестандартных листов и поперечников):
    1) Приоритет 1: замкнутые полилинии на целевом слое штампа/рамки (*ШТАМП*, *РАМКА*, *STAMP*, *FRAME*).
       Исключаем внутренние рамки штампа (_keep_outermost).
    2) Приоритет 2: прямоугольные рамки из линий на слое штампа.
    3) Приоритет 3: полилинии на слое Bounds.
    4) Приоритет 4: любые прямоугольные полилинии формата листа.
    5) Fallback: прямоугольники из всех линий чертежа.
    Сортировка строго сверху вниз (по строкам), слева направо!
    """
    from collections import defaultdict, Counter

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

    stamp_poly_frames = []
    bounds_frames = []
    other_poly_frames = []
    for xmin, ymin, xmax, ymax, w, h, lay in polys:
        if w < 150.0 or h < 150.0:
            continue
        fr = (xmin, ymin, xmax, ymax, w, h)
        if _is_stamp_layer(lay):
            stamp_poly_frames.append(fr)
        elif lay.casefold() == "bounds":
            bounds_frames.append(fr)
        elif _is_sheet_size(w, h):
            other_poly_frames.append(fr)

    # 1. Рамки со слоя штампа (объединяем и полилинии, и прямоугольники из линий)
    stamp_frames = list(stamp_poly_frames)
    stamp_hl = [L for lay, lines in hl_by.items() if _is_stamp_layer(lay) for L in lines]
    stamp_vl = [L for lay, lines in vl_by.items() if _is_stamp_layer(lay) for L in lines]
    if stamp_hl and stamp_vl:
        line_rects = _rects_from_hv(stamp_hl, stamp_vl, tol=3.0, require_v=True)
        if line_rects:
            stamp_frames.extend(line_rects)

    if stamp_frames:
        outer = _keep_outermost(stamp_frames)
        result = _sort_frames_top_bottom_left_right(outer)
        sizes = dict(Counter((round(f[4]), round(f[5])) for f in result))
        print(f"[SMART] Найдено рамок со слоя штампа: {len(result)} (размеры: {sizes})")
        return result

    # 3. Рамки со слоя Bounds
    if bounds_frames:
        outer = _keep_outermost(bounds_frames)
        result = _sort_frames_top_bottom_left_right(outer)
        sizes = dict(Counter((round(f[4]), round(f[5])) for f in result))
        print(f"[SMART] Найдено рамок слоя Bounds: {len(result)} (размеры: {sizes})")
        return result

    # 4. Другие полилинии формата листов
    if other_poly_frames:
        outer = _keep_outermost(other_poly_frames)
        result = _sort_frames_top_bottom_left_right(outer)
        sizes = dict(Counter((round(f[4]), round(f[5])) for f in result))
        print(f"[SMART] Найдено полилиний других слоев: {len(result)} (размеры: {sizes})")
        return result

    # 5. Запасной вариант: все линии чертежа
    all_hl = [x for lines in hl_by.values() for x in lines]
    all_vl = [x for lines in vl_by.values() for x in lines]
    if all_hl and all_vl:
        all_rects = _rects_from_hv(all_hl, all_vl, require_v=bool(all_vl))
        all_rects = [f for f in all_rects if _is_sheet_size(f[4], f[5])]
        if all_rects:
            outer = _keep_outermost(all_rects)
            result = _sort_frames_top_bottom_left_right(outer)
            sizes = dict(Counter((round(f[4]), round(f[5])) for f in result))
            print(f"[SMART] Найдено рамок из общих линий: {len(result)} (размеры: {sizes})")
            return result

    return []


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
        "DRAWORDERCTL", "3",
        "SORTENTS", "127",
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
            "1:1",
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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(None),
    ctb: str = Form(None),
    profile: str = Form(None),
    smb_dwg_path: str = Form(None),
    smb_user: str = Form(None),
    smb_pass: str = Form(None),
    smart_search: str = Form(None),
    job_id: str = Form(None)
):
    if not ACAD_PATH or not os.path.exists(ACAD_PATH):
        return JSONResponse(status_code=400, content={"error": "AutoCAD (accoreconsole.exe) не найден на сервере."})

    start_time = time.time()
    safe_uid = uuid.uuid4().hex
    cad_task_id = (job_id or "").strip() or safe_uid
    temp_dir = tempfile.gettempdir()
    
    dwg_path = None
    safe_dwg_path = None
    safe_pdf_path = None
    scr_path = None
    lsp_path = None
    stdout_text = ""
    is_smart = False
    unique_frames = []
    
    update_cad_task(cad_task_id, status="running", stage="init", stage_text="Подготовка файла чертежа...", percent=5)

    try:
        # 1. Определение путей и копирование файла
        if smb_dwg_path:
            ensure_smb_connection(smb_dwg_path, username=smb_user, password=smb_pass)
            if os.path.exists(smb_dwg_path):
                safe_dwg_path = smb_dwg_path
                safe_filename = os.path.basename(smb_dwg_path)
                dwg_path = None
            else:
                local_path = unc_to_local(smb_dwg_path)
                if os.path.exists(local_path):
                    safe_dwg_path = local_path
                    safe_filename = os.path.basename(local_path)
                    dwg_path = None
                else:
                    err_msg = f"Файл не найден по сетевому или локальному пути: {smb_dwg_path}"
                    update_cad_task(cad_task_id, status="error", stage="error", stage_text=err_msg)
                    return JSONResponse(status_code=400, content={"error": err_msg})
        elif file and file.filename:
            safe_filename = file.filename.replace(" ", "_")
            dwg_path = os.path.join(WORK_DIR, f"input_{safe_uid}.dwg")
            with open(dwg_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            safe_dwg_path = dwg_path
        else:
            err_msg = "Не передан файл."
            update_cad_task(cad_task_id, status="error", stage="error", stage_text=err_msg)
            return JSONResponse(status_code=400, content={"error": err_msg})

        # Итоговые пути
        safe_pdf_path = os.path.join(WORK_DIR, f"input_{safe_uid}.pdf")
        pdf_prefix_base = safe_pdf_path.replace(".pdf", "")
        scr_path = os.path.join(WORK_DIR, f"print_{safe_uid}.scr")
        lsp_path = os.path.join(WORK_DIR, f"print_{safe_uid}.lsp")

        # 2. Флаги логики
        name_lower = safe_filename.lower()
        if "_model" in name_lower or "модель" in name_lower or "поперечник" in name_lower:
            smart_search = "true"
        is_smart = (smart_search and smart_search.lower() == "true")

        update_cad_task(cad_task_id, filename=safe_filename, percent=10)

        # 3. Генерация и запись LISP скрипта
        if not is_smart:
            update_cad_task(cad_task_id, stage="plotting", stage_text="Печать листов пространства листа...", percent=20)
            lisp_code = _generate_lisp_script(safe_pdf_path, False, ctb)
            with open(scr_path, "w", encoding="cp1251", errors="replace") as f:
                f.write(lisp_code)
            with open("C:/CadServer/cad_server_workdir/LAST_RUN.scr", "w", encoding="cp1251", errors="replace") as f:
                f.write(lisp_code)

            cmd = [ACAD_PATH, "/i", safe_dwg_path, "/l", "ru-RU", "/s", scr_path]
            if profile:
                cmd.extend(["/p", profile])
            
            stop_watcher = threading.Event()
            def _watch_layouts():
                while not stop_watcher.is_set():
                    with _tasks_lock:
                        t = CAD_TASKS.get(cad_task_id)
                        if t and t.get("status") == "cancelled":
                            _kill_accore()
                            break
                    p = glob.glob(f"{pdf_prefix_base}_[0-9]*.pdf")
                    if p:
                        update_cad_task(cad_task_id, current_sheet=len(p), stage_text=f"Печать листов: создано {len(p)} стр.")
                    stop_watcher.wait(0.5)

            watcher_th = threading.Thread(target=_watch_layouts, daemon=True)
            watcher_th.start()

            try:
                result = _run_accore(cmd, ACAD_TIMEOUT_SEC)
                stdout_text = _decode_acad_out(result.stdout)
            except subprocess.TimeoutExpired:
                _kill_accore()
                parts = glob.glob(f"{pdf_prefix_base}_[0-9]*.pdf")
                if parts:
                    print(f"[TIMEOUT WARN] Таймаут {ACAD_TIMEOUT_SEC}s, salvaged {len(parts)} parts.")
                    stdout_text = f"AutoCAD timeout {ACAD_TIMEOUT_SEC}s, salvaged {len(parts)} parts."
                else:
                    update_cad_task(cad_task_id, status="error", stage="error", stage_text=f"Таймаут AutoCAD {ACAD_TIMEOUT_SEC}s.")
                    return JSONResponse(status_code=504, content={"error": f"AutoCAD timeout {ACAD_TIMEOUT_SEC}s. Process killed.", "log": ""})
            finally:
                stop_watcher.set()
                watcher_th.join(timeout=0.5)

            with _tasks_lock:
                if CAD_TASKS.get(cad_task_id, {}).get("status") == "cancelled":
                    return JSONResponse(status_code=499, content={"error": "Cancelled by user", "status": "cancelled"})
        else:
            update_cad_task(cad_task_id, stage="dump_frames", stage_text="Поиск рамок штампов в пространстве модели...", percent=12)
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
                update_cad_task(cad_task_id, status="error", stage="error", stage_text="Таймаут поиска рамок (шаг 1).")
                return JSONResponse(status_code=504, content={"error": "AutoCAD timeout on step 1 (dump frames)."})

            with _tasks_lock:
                if CAD_TASKS.get(cad_task_id, {}).get("status") == "cancelled":
                    return JSONResponse(status_code=499, content={"error": "Cancelled by user", "status": "cancelled"})

            try:
                os.remove(dump_scr)
            except Exception:
                pass

            if not os.path.exists(lines_file):
                update_cad_task(cad_task_id, status="error", stage="error", stage_text="Не удалось извлечь дамп рамок.")
                return JSONResponse(status_code=400, content={"error": "Failed to extract frames dump.", "log": stdout_text[-4000:]})

            with _tasks_lock:
                if CAD_TASKS.get(cad_task_id, {}).get("status") == "cancelled":
                    return JSONResponse(status_code=499, content={"error": "Cancelled by user", "status": "cancelled"})

            unique_frames = _find_frames_from_dump(lines_file)
            try:
                os.remove(lines_file)
            except Exception:
                pass

            with _tasks_lock:
                if CAD_TASKS.get(cad_task_id, {}).get("status") == "cancelled":
                    return JSONResponse(status_code=499, content={"error": "Cancelled by user", "status": "cancelled"})

            total_frames = len(unique_frames)
            print(f"Found frames: {total_frames}")
            if not unique_frames:
                update_cad_task(cad_task_id, status="error", stage="error", stage_text="В Модели не найдено рамок штампа.")
                return JSONResponse(status_code=400, content={"error": "В Модели не найдено ни одной рамки.", "log": stdout_text[-4000:]})

            update_cad_task(
                cad_task_id,
                stage="plotting",
                stage_text=f"Найдено рамок: {total_frames}. Печать листов...",
                total_sheets=total_frames,
                current_sheet=0,
                percent=20
            )

            with _tasks_lock:
                if CAD_TASKS.get(cad_task_id, {}).get("status") == "cancelled":
                    return JSONResponse(status_code=499, content={"error": "Cancelled by user", "status": "cancelled"})

            pdf_prefix = safe_pdf_path.replace(".pdf", "")
            with open(scr_path, "w", encoding="cp1251", errors="replace") as f:
                f.write(_build_plot_lisp(unique_frames, pdf_prefix))

            cmd2 = [ACAD_PATH, "/i", safe_dwg_path, "/l", "ru-RU", "/s", scr_path]
            if profile:
                cmd2.extend(["/p", profile])

            stop_watcher = threading.Event()
            def _watch_smart():
                while not stop_watcher.is_set():
                    with _tasks_lock:
                        t = CAD_TASKS.get(cad_task_id)
                        if t and t.get("status") == "cancelled":
                            _kill_accore()
                            break
                    p = glob.glob(f"{pdf_prefix_base}_[0-9]*.pdf")
                    count = len(p)
                    update_cad_task(
                        cad_task_id,
                        current_sheet=count,
                        total_sheets=total_frames,
                        stage_text=f"Печать листов: {count} из {total_frames}"
                    )
                    stop_watcher.wait(0.5)

            watcher_th = threading.Thread(target=_watch_smart, daemon=True)
            watcher_th.start()

            try:
                result = _run_accore(cmd2, ACAD_TIMEOUT_SEC)
                stdout_text = _decode_acad_out(result.stdout)
            except subprocess.TimeoutExpired:
                _kill_accore()
                parts = glob.glob(f"{pdf_prefix_base}_[0-9]*.pdf")
                if parts:
                    print(f"[TIMEOUT WARN] Таймаут {ACAD_TIMEOUT_SEC}s на шаге 2, но найдены {len(parts)} листов! Продолжаем сборку...")
                    stdout_text = f"AutoCAD timeout {ACAD_TIMEOUT_SEC}s on step 2, salvaged {len(parts)} parts."
                else:
                    update_cad_task(cad_task_id, status="error", stage="error", stage_text=f"Таймаут печати AutoCAD {ACAD_TIMEOUT_SEC}s.")
                    return JSONResponse(status_code=504, content={"error": f"AutoCAD timeout {ACAD_TIMEOUT_SEC}s on step 2."})
            finally:
                stop_watcher.set()
                watcher_th.join(timeout=0.5)

            with _tasks_lock:
                if CAD_TASKS.get(cad_task_id, {}).get("status") == "cancelled":
                    return JSONResponse(status_code=499, content={"error": "Cancelled by user", "status": "cancelled"})

        if "ERROR_NO_LAYOUTS" in stdout_text:
            print(f"ОШИБКА: В чертеже {safe_filename} нет настроенных листов!")
            update_cad_task(cad_task_id, status="error", stage="error", stage_text="В чертеже нет настроенных листов.")
            return JSONResponse(status_code=400, content={"error": "В чертеже нет ни одного листа (Layout).", "log": stdout_text})

        # 5. Сборка PDF и удаление пустых листов
        parts = sorted(
            glob.glob(f"{pdf_prefix_base}_[0-9]*.pdf"),
            key=lambda f: int(f.rsplit("_", 1)[-1].replace(".pdf", "")) if f.rsplit("_", 1)[-1].replace(".pdf", "").isdigit() else 0
        )
        sheet_count = len(parts) or (len(unique_frames) if is_smart else 1)
        update_cad_task(
            cad_task_id,
            stage="merging",
            stage_text=f"Сборка и подрезка листов PDF ({sheet_count} стр.)...",
            percent=92,
            eta_sec=5
        )

        if parts:
            try:
                _merge_pdf_parts(pdf_prefix_base, safe_pdf_path)
            except ValueError as e:
                update_cad_task(cad_task_id, status="error", stage="error", stage_text=str(e))
                return JSONResponse(status_code=400, content={"error": str(e), "log": stdout_text[-4000:]})
        else:
            for f in glob.glob(safe_pdf_path.replace(".pdf", "") + "_ps_*.pdf"):
                try:
                    os.remove(f)
                except Exception:
                    pass

        _remove_empty_pages(safe_pdf_path)
        if is_smart:
            _trim_pdf_to_content(safe_pdf_path, frames=unique_frames)

        print(f"Время выполнения: {time.time() - start_time:.1f} сек")
        if os.path.exists(safe_pdf_path):
            update_cad_task(cad_task_id, status="done", stage="done", stage_text="Конвертация успешно завершена!", percent=100, eta_sec=0)
            background_tasks.add_task(_safe_remove, safe_pdf_path)
            return FileResponse(path=safe_pdf_path, filename=safe_filename.replace(".dwg", ".pdf"), media_type='application/pdf')
        print("ОШИБКА ПЕЧАТИ:")
        print(stdout_text)
        update_cad_task(cad_task_id, status="error", stage="error", stage_text="Не удалось создать PDF.")
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
def convert_office(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not _office_lock.acquire(timeout=600):
        return JSONResponse(status_code=503, content={"error": "Office конвертер занят, попробуйте позже."})
    try:
        return _convert_office_impl(file, background_tasks)
    finally:
        _office_lock.release()

def _convert_office_impl(file: UploadFile, background_tasks: BackgroundTasks):
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
        background_tasks.add_task(_safe_remove, pdf_path)
        return FileResponse(path=pdf_path, filename=safe_filename.replace(ext, ".pdf"), media_type='application/pdf')
    else:
        return JSONResponse(status_code=500, content={"error": "PDF не создан.", "log": ""})

if __name__ == "__main__":
    print("--------------------------------------------------")
    print(" Сервер AutoCAD Core Console запущен! (Стабильная версия)")
    print(" Ожидание чертежей на порту 8000...")
    print("--------------------------------------------------")
    uvicorn.run(app, host="0.0.0.0", port=8000)

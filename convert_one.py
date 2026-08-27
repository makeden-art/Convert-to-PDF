"""One-shot DWG->PDF using smart frame search (no HTTP server)."""
import os
import sys
import time
import uuid
import shutil

import windows_cad_server as srv


def main():
    if len(sys.argv) < 2:
        print("Usage: python convert_one.py <file.dwg> [out.pdf]")
        return 2

    dwg_src = os.path.abspath(sys.argv[1])
    if not os.path.exists(dwg_src):
        print(f"DWG not found: {dwg_src}")
        return 1

    out_pdf = (
        os.path.abspath(sys.argv[2])
        if len(sys.argv) > 2
        else os.path.splitext(dwg_src)[0] + ".pdf"
    )

    if not srv.ACAD_PATH or not os.path.exists(srv.ACAD_PATH):
        print("accoreconsole.exe not found")
        return 1

    os.makedirs(srv.WORK_DIR, exist_ok=True)
    uid = uuid.uuid4().hex
    # ASCII-only copy: accoreconsole часто спотыкается о кириллицу в путях
    dwg = os.path.join(srv.WORK_DIR, f"input_{uid}.dwg")
    shutil.copy2(dwg_src, dwg)
    lines_file = os.path.join(srv.WORK_DIR, f"cad_lines_{uid}.txt")
    dump_scr = os.path.join(srv.WORK_DIR, f"dump_{uid}.scr")
    plot_scr = os.path.join(srv.WORK_DIR, f"plot_{uid}.scr")
    pdf_prefix = os.path.join(srv.WORK_DIR, f"out_{uid}")

    t0 = time.time()
    print(f"ACAD: {srv.ACAD_PATH}")
    print(f"DWG:  {dwg_src}")
    print(f"TMP:  {dwg}")
    print("Step 1/2: dump ISO candidates...")

    # Multiline LISP прямо в SCR — так accoreconsole уже успешно отрабатывал
    with open(dump_scr, "w", encoding="ascii", errors="replace") as f:
        f.write(srv._build_dump_lisp(lines_file))

    cmd1 = [srv.ACAD_PATH, "/i", dwg, "/l", "ru-RU", "/s", dump_scr]
    try:
        r1 = srv._run_accore(cmd1, srv.DUMP_TIMEOUT_SEC)
        print(srv._decode_acad_out(r1.stdout)[-1500:])
    except Exception as e:
        print(f"Dump failed: {e}")
        return 1
    finally:
        try:
            os.remove(dump_scr)
        except Exception:
            pass

    if not os.path.exists(lines_file):
        print("No dump file created")
        return 1

    dump_size = os.path.getsize(lines_file)
    print(f"Dump size: {dump_size} bytes")
    frames = srv._find_frames_from_dump(lines_file)
    try:
        os.remove(lines_file)
    except Exception:
        pass

    print(f"Frames found: {len(frames)}")
    for i, fr in enumerate(frames, 1):
        paper = srv._paper_for_frame(fr[4], fr[5])
        print(f"  {i}: {fr[0]:.1f},{fr[1]:.1f} -> {fr[2]:.1f},{fr[3]:.1f}  {fr[4]:.0f}x{fr[5]:.0f}  {paper}")

    if not frames:
        return 1

    # Опционально ограничить число листов: python convert_one.py file.dwg out.pdf 5
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else None
    if limit:
        frames = frames[:limit]
        print(f"Plot limit: {limit}")

    print("Step 2/2: plot frames...")
    # Каждый (command ...) — отдельная полная строка SCR
    with open(plot_scr, "w", encoding="ascii", errors="replace") as f:
        f.write(srv._build_plot_lisp(frames, pdf_prefix))

    cmd2 = [srv.ACAD_PATH, "/i", dwg, "/l", "ru-RU", "/s", plot_scr]
    try:
        r2 = srv._run_accore(cmd2, srv.ACAD_TIMEOUT_SEC)
        print(srv._decode_acad_out(r2.stdout)[-2000:])
    except Exception as e:
        print(f"Plot failed: {e}")
        return 1
    finally:
        try:
            os.remove(plot_scr)
        except Exception:
            pass

    try:
        srv._merge_pdf_parts(pdf_prefix, out_pdf)
    except ValueError as e:
        print(e)
        return 1

    srv._remove_empty_pages(out_pdf)
    srv._trim_pdf_to_content(out_pdf)
    try:
        os.remove(dwg)
    except Exception:
        pass
    print(f"OK: {out_pdf} ({os.path.getsize(out_pdf)} bytes) in {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Отдельный процесс для CAD-предпросмотра (можно принудительно завершить по таймауту)."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 5:
        print("usage: cad_preview_worker.py <input> <page> <out.png> <out.json> [original_src]", file=sys.stderr)
        return 2

    input_file = sys.argv[1]
    page = max(1, int(sys.argv[2]))
    out_png = Path(sys.argv[3])
    out_meta = Path(sys.argv[4])
    original_src = sys.argv[5] if len(sys.argv) > 5 else None

    from cad_converter import render_cad_preview_png

    try:
        png, total, meta = render_cad_preview_png(input_file, page=page, original_src=original_src)
        Path(out_png).write_bytes(png)
        meta["pages"] = total
        Path(out_meta).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return 0
    except Exception as e:
        Path(out_meta).write_text(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

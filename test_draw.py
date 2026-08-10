import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf.addons.drawing.frontend import Frontend
from ezdxf.addons.drawing.config import Configuration
from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
from ezdxf.addons.drawing.properties import RenderContext
import time

doc = ezdxf.readfile('/tmp/cad_pdf_6lpwizs2/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
print("Reading file...")
entities = []
for e in doc.modelspace():
    if e.dxftype() == 'INSERT':
        entities.append(e)

print(f"Found {len(entities)} INSERTs.")
out = PyMuPdfBackend()
frontend = Frontend(RenderContext(doc), out, config=Configuration())

start = time.time()
frontend.draw_entities(entities)
print(f"Drawing all INSERTs took {time.time() - start:.2f} seconds")

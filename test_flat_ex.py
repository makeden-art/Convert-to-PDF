import sys
sys.path.insert(0, '/app')
import ezdxf
from cad_converter import _safe_virtual_block_reference_entities

doc = ezdxf.readfile('/tmp/cad_pdf_6lpwizs2/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')

print('Flattening...')
flattened = []
exceptions = []

def flatten(entities):
    for e in entities:
        if e.dxftype() == 'INSERT':
            try:
                for virt_e in _safe_virtual_block_reference_entities(e, lambda *args: None):
                    if virt_e.dxftype() == 'INSERT':
                        flatten([virt_e])
                    else:
                        flattened.append(virt_e)
            except Exception as ex:
                exceptions.append((e, ex))
                flattened.append(e)
        else:
            flattened.append(e)

flatten(doc.modelspace())
print(f'Exceptions: {len(exceptions)}')
for e, ex in exceptions[:10]:
    print(f"Exception on {e}: {type(ex)} {ex}")

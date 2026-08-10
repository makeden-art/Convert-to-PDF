import os
import ezdxf
import matplotlib
import sys
sys.path.append('/opt/road-pdf-platform')
sys.path.append('/app')

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from ezdxf.addons.drawing import RenderContext
from ezdxf.addons.drawing.config import Configuration, ColorPolicy, LinePolicy
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.addons.drawing.frontend import Frontend
from converter.cad.cad_converter import _patch_frontend, _patch_mleader_zerodiv, SafeFrontend

_patch_mleader_zerodiv()
doc = ezdxf.readfile('/opt/road-pdf-platform/shared/documents/11-2025-1-ТКР9-1_Кальмиус_варианты.dxf')
ctx = RenderContext(doc)
fig = plt.figure(figsize=(10, 10), dpi=300)
ax = fig.add_axes([0, 0, 1, 1])
out = MatplotlibBackend(ax)

is_monochrome = True
config = Configuration(
    color_policy=ColorPolicy.COLOR_SWAP_BW,
    line_policy=LinePolicy.APPROXIMATE,
    max_flattening_distance=0.15,
    circle_approximation_count=32,
)
frontend = Frontend(ctx, out, config=config)
_patch_frontend(frontend, is_monochrome=is_monochrome)

entities = doc.modelspace()
frontend.draw_entities(entities)

fig.savefig('/tmp/test_ezdxf_black.pdf', format='pdf', bbox_inches='tight', facecolor='white')
print("Done!")

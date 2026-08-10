import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.addons.drawing.config import Configuration, ColorPolicy
import matplotlib.pyplot as plt

doc = ezdxf.readfile('/opt/road-pdf-platform/shared/documents/11-2025-1-ТКР9-1_Кальмиус_варианты.dxf')
layout = doc.layouts.get('Лист1')
fig = plt.figure()
ax = fig.add_axes([0, 0, 1, 1])

ctx = RenderContext(doc)
out = MatplotlibBackend(ax, adjust_figure=False)

def _override_properties(entity, properties):
    has_true_color = False
    try:
        if entity.dxf.hasattr('true_color'):
            has_true_color = True
        elif entity.dxf.color == 256 and hasattr(entity, 'doc') and entity.doc:
            layer = entity.doc.layers.get(entity.dxf.layer)
            if layer.dxf.hasattr('true_color'):
                has_true_color = True
    except Exception:
        pass
    
    if not has_true_color:
        properties.color = '#000000'

config = Configuration(color_policy=ColorPolicy.COLOR_SWAP_BW)
frontend = Frontend(ctx, out, config=config)
frontend.push_property_override_function(_override_properties)
frontend.draw_layout(layout)

fig.savefig('/opt/road-pdf-platform/shared/documents/test_ezdxf.pdf')
print("Done!")

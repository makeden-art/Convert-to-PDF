import ezdxf
from ezdxf.colors import rgb2int
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.config import Configuration, ColorPolicy
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
import matplotlib.pyplot as plt

doc = ezdxf.new()
doc.layers.add("L1", color=3) # Green ACI
doc.layers.add("L2", true_color=rgb2int((255, 0, 0))) # Red True Color

msp = doc.modelspace()
line1 = msp.add_line((0,0), (10,0), dxfattribs={'color': 1}) # Red ACI
line2 = msp.add_line((0,1), (10,1), dxfattribs={'true_color': rgb2int((0, 0, 255))}) # Blue TrueColor
line3 = msp.add_line((0,2), (10,2), dxfattribs={'layer': 'L1'}) # Green ACI ByLayer
line4 = msp.add_line((0,3), (10,3), dxfattribs={'layer': 'L2'}) # Red TrueColor ByLayer
line5 = msp.add_line((0,4), (10,4), dxfattribs={'layer': 'L1', 'true_color': rgb2int((255,0,255))}) # Magenta TrueColor override

ctx = RenderContext(doc)
fig = plt.figure()
ax = fig.add_axes([0,0,1,1])

class MyBackend(MatplotlibBackend):
    def draw_line(self, start, end, properties):
        print(f'draw_line color: {properties.color}')

out = MyBackend(ax)
# Base configuration: Keep everything original (COLOR), not COLOR_SWAP_BW so we don't confuse black with white in test
config = Configuration(color_policy=ColorPolicy.COLOR)
frontend = Frontend(ctx, out, config=config)

import types
orig_draw_entity = frontend.draw_entity

def patch_draw_entity(self, entity, properties=None):
    if properties is None:
        properties = self.ctx.resolve_all(entity)
        
    has_true_color = False
    try:
        if entity.dxf.hasattr('true_color'):
            has_true_color = True
        elif entity.dxf.color == 256: # BYLAYER
            if entity.doc:
                layer = entity.doc.layers.get(entity.dxf.layer)
                if layer.dxf.hasattr('true_color'):
                    has_true_color = True
    except Exception as e:
        print(f"Exception checking true color: {e}")
        pass
    
    if not has_true_color:
        properties.color = '#000000'
        
    orig_draw_entity(entity, properties)

frontend.draw_entity = types.MethodType(patch_draw_entity, frontend)
frontend.draw_layout(msp)
print('Done')

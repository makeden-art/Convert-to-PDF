import ezdxf
from ezdxf.addons.drawing.config import ColorPolicy
from ezdxf.colors import rgb2int
doc = ezdxf.new()
msp = doc.modelspace()
line1 = msp.add_line((0,0), (10,0), dxfattribs={'color': 1}) # ACI 1 (Red)
line2 = msp.add_line((0,1), (10,1), dxfattribs={'true_color': rgb2int((0, 0, 255))}) # True Color Blue

from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.config import Configuration
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
import matplotlib.pyplot as plt

ctx = RenderContext(doc)
# Try to override color mapping for monochrome
def my_color_mapping(color_index):
    return "#000000" # Force all ACI to black

# But what about True Color? True color bypasses ACI!
fig = plt.figure()
ax = fig.add_axes([0,0,1,1])

class MyBackend(MatplotlibBackend):
    def draw_line(self, start, end, properties):
        print(f'draw_line color: {properties.color}')

out = MyBackend(ax)
config = Configuration(color_policy=ColorPolicy.COLOR_SWAP_BW) # Use standard color policy
frontend = Frontend(ctx, out, config=config)

# Intercept entity properties resolution
original_resolve_all = ctx.resolve_all
def my_resolve_all(entity):
    props = original_resolve_all(entity)
    # If the entity doesn't have true_color in its hierarchy (we can cheat and check if the resolved color is from ACI)
    # Actually, Properties object has `color` (hex string).
    # It's hard to know if it was ACI or true_color just from Properties.
    return props

ctx.resolve_all = my_resolve_all

frontend.draw_layout(msp)
print('Done')

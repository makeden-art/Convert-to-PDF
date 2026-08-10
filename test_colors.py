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
fig = plt.figure()
ax = fig.add_axes([0,0,1,1])

# Test BLACK
class MyBackend(MatplotlibBackend):
    def draw_line(self, start, end, properties):
        print(f'draw_line color: {properties.color}')

out = MyBackend(ax)
config = Configuration(color_policy=ColorPolicy.BLACK)
frontend = Frontend(ctx, out, config=config)
frontend.draw_layout(msp)
print('BLACK policy finished')

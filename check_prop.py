from ezdxf.addons.drawing.properties import Properties
print([p for p in dir(Properties) if not p.startswith('_')])

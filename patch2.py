import re

with open('cad_converter.py', 'r', encoding='utf-8') as f:
    c = f.read()

c = c.replace('def new_apply_color_policy(color, color_policy):', 'def new_apply_color_policy(color, color_policy, custom_color=\"#000000\"):\n        if custom_color is None:\n            custom_color = \"#000000\"')
c = c.replace('return pipeline._orig_apply_color_policy(color, color_policy)', 'return pipeline._orig_apply_color_policy(color, color_policy, custom_color)')

with open('cad_converter.py', 'w', encoding='utf-8') as f:
    f.write(c)
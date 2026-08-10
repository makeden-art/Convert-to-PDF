import fitz # PyMuPDF

doc = fitz.open("/tmp/test_ezdxf.pdf")
colors = set()
for page in doc:
    for block in page.get_drawings():
        if block.get("color"):
            colors.add(tuple(block.get("color")))
        if block.get("fill"):
            colors.add(tuple(block.get("fill")))
print("Colors found in PDF:", colors)

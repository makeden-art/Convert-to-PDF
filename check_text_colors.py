import fitz
doc = fitz.open('/app/test_ezdxf_black.pdf')
colors = set()
for page in doc:
    for block in page.get_text("dict")["blocks"]:
        if "lines" in block:
            for line in block["lines"]:
                for span in line["spans"]:
                    colors.add(span["color"])
print('Text Colors:', colors)

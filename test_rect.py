import fitz
try:
    doc = fitz.open('/tmp/test.pdf')
    for i, page in enumerate(doc):
        print(f"Page {i}: rect={page.rect}")
except Exception as e:
    print(e)

import ezdxf
import sys

def main():
    doc = ezdxf.readfile(sys.argv[1])
    try:
        print("Modelspace:", repr(doc.modelspace().dxf.current_style_sheet))
    except:
        pass
    for layout in doc.layouts:
        try:
            print(layout.name, repr(layout.dxf.current_style_sheet))
        except:
            pass

if __name__ == "__main__":
    main()

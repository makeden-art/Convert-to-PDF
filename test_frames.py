import sys
from cad_converter import inspect_cad_frames
import json

if __name__ == "__main__":
    res = inspect_cad_frames(sys.argv[1])
    print(json.dumps(res, indent=2, ensure_ascii=False))

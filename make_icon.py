# -*- coding: utf-8 -*-
import os, shutil
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = r"C:\Users\박성준\Desktop\mmmoan\moana-logo-png_seeklogo-472212.png"

png = os.path.join(BASE, "moana.png")
shutil.copyfile(SRC, png)
im = Image.open(png).convert("RGBA")
ico = os.path.join(BASE, "moana.ico")
im.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("PNG:", png, "size", im.size)
print("ICO:", ico, "exists", os.path.exists(ico))

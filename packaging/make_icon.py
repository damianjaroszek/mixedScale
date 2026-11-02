"""Draws the Blendora icon (two overlapping discs) as a multi-size .ico."""
import os

from PIL import Image, ImageDraw

S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
bg = ImageDraw.Draw(img)
bg.rounded_rectangle([0, 0, S - 1, S - 1], radius=110, fill=(11, 13, 18, 255))
a = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(a).ellipse([70, 120, 342, 392], fill=(139, 123, 255, 255))
b = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(b).ellipse([170, 120, 442, 392], fill=(45, 224, 167, 205))
img = Image.alpha_composite(Image.alpha_composite(img, a), b)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blendora.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(out)

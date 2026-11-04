"""Generate the extension's code-native shield icon without external image assets."""
from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1] / 'extension/icons'
root.mkdir(parents=True, exist_ok=True)
im = Image.new('RGBA', (256, 256), (20, 43, 34, 255))
draw = ImageDraw.Draw(im)
draw.polygon([(48, 36), (208, 36), (208, 133), (193, 178), (163, 207), (128, 227), (93, 207), (63, 178), (48, 133)], fill='#BBED93')
draw.line([(86, 125), (115, 154), (174, 91)], fill='#18311C', width=19, joint='curve')
for size in (16, 48, 128):
    im.resize((size, size), Image.Resampling.LANCZOS).save(root/f'icon{size}.png')
print('Icons generated')

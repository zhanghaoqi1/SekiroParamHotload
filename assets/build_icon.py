"""Render the editable vector mark to antialiased PNG/Windows icon sizes.

Uses the same coordinates and palette as app-icon.svg; Pillow is build-only.
"""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
image = Image.new('RGBA',(1024,1024),(0,0,0,0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((32,32,992,992),radius=210,fill='#19211e')
draw.rounded_rectangle((34,34,990,990),radius=208,outline='#354039',width=4)
draw.arc((179,179,845,845),start=-55,end=215,fill='#c9a267',width=66)
# Arc strokes are centered in SVG; ImageDraw places its stroke inside the box.
# Use the matching centerline radius for the raster family.
import math
for angle in (-55,215):
    x=512+300*math.cos(math.radians(angle));y=512+300*math.sin(math.radians(angle))
    draw.ellipse((x-33,y-33,x+33,y+33),fill='#c9a267')
draw.polygon([(307,280),(303,420),(191,342)],fill='#c9a267')
draw.polygon([(342,721),(647,311),(726,232),(683,337),(382,750)],fill='#f2eee3')
draw.polygon([(348,706),(378,734),(322,808),(286,788)],fill='#c65538')
draw.line([(316,686),(401,753)],fill='#c65538',width=28)
for x,y in [(316,686),(401,753)]:
    draw.ellipse((x-14,y-14,x+14,y+14),fill='#c65538')
image.resize((512,512),Image.Resampling.LANCZOS).save(ROOT/'app-icon.png')
for size in (24,56,112):
    image.resize((size,size),Image.Resampling.LANCZOS).save(ROOT/f'app-icon-{size}.png')
image.save(ROOT/'app.ico',format='ICO',sizes=[(s,s) for s in (16,20,24,32,40,48,64,128,256)])

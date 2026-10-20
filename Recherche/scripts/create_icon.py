"""Legacy column icon for existing ICC/ICJ taskbar links.

Collection icons are built by create_collection_icons.cjs:
archive.ico = globe/magnifier, courts.ico = columns, un.ico = original geometric UN lettering (no official emblem).
Do not point the overarching archive shortcut at this legacy recherche.ico.
"""
from pathlib import Path
from PIL import Image, ImageDraw
base=Path(__file__).resolve().parents[1]
image=Image.new('RGBA',(256,256),(255,255,255,0))
draw=ImageDraw.Draw(image)
draw.rounded_rectangle((8,8,248,248),radius=45,fill='#194c42')
draw.polygon(((47,87),(128,41),(209,87)),fill='#e7eccb')
draw.rectangle((47,94,209,107),fill='white')
for x in (62,102,142,182):
    draw.rounded_rectangle((x,117,x+14,191),radius=3,fill='white')
draw.rectangle((44,204,212,215),fill='white')
draw.rectangle((53,193,203,201),fill='white')
image.save(base/'web'/'recherche.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])


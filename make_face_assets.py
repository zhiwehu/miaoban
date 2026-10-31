"""Generate original animated capsule-eye faces and lossless RLE EAF assets."""
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import struct
from PIL import Image, ImageDraw
from configure_wake_assets import pack, unpack

SIZE = 360
FPS = 10

def face(t, mood='neutral'):
    scale = 2
    im = Image.new('L', (SIZE*scale, SIZE*scale), 0)
    d = ImageDraw.Draw(im)
    # A black round face on a soft white disc, fitting the circular LCD.
    # Black edge-to-edge: no outer ring on the round physical display.
    # Mostly front-facing. Smoothly turn to a three-quarter glance, then return.
    side = 0.5 - 0.5*math.cos(2*math.pi*(t-4)/2) if 4 < t < 6 else 0
    look = 7*math.sin(t*1.5) + 34*side
    bounce = 2*math.sin(t*2)
    blink = 1.0
    for at in (1.8, 3.9, 7.3, 7.65):
        blink = min(blink, min(1, abs(t-at)/0.12))
    if mood == 'sleep':
        blink *= .28
    for i in range(2):
        cx = 145 + 70*i + look - i*16*side
        cy = 163 + bounce - i*12*side
        w = (29 - i*7*side)
        h = (65 - i*10*side)*(0.96 + .04*math.sin(t*3))
        if mood == 'shocked':
            h *= 1.15; w *= 1.2
        if mood == 'happy':
            h *= .72
        if mood == 'sad':
            cy += 9; h *= .8
        if mood == 'confused':
            cy += (1 if i else -1)*7*math.sin(t*2)
        if mood == 'winking' and i == 1 and 1 < t < 1.6:
            eye_open = .08
        else:
            eye_open = blink
        h = max(5, h*eye_open)
        eye = Image.new('L', (180, 200), 0)
        ed = ImageDraw.Draw(eye)
        ed.rounded_rectangle((90-w,100-h,90+w,100+h), radius=min(w,h), fill=255)
        eye = eye.rotate(12*side + (i*2-1)*3, resample=Image.Resampling.BICUBIC)
        im.paste(eye, (round(cx*scale-90),round(cy*scale-100)), eye)
    # Quantize to 16 grays for small, antialiased, lossless palette frames.
    return im.resize((SIZE,SIZE), Image.Resampling.LANCZOS).point(lambda v: round(v/17)*17)

def frame_data(im):
    raw = im.tobytes()
    blocks = []
    for y in range(0,SIZE,12):
        encoded = bytearray([0])  # EAF_ENCODING_RLE
        for value, group in itertools.groupby(raw[y*SIZE:(y+12)*SIZE]):
            count = sum(1 for _ in group)
            while count:
                n = min(255,count); encoded.extend((n,value)); count -= n
        blocks.append(bytes(encoded))
    header = b'_S\0' + b'1.0\0\0\0' + struct.pack('<BHHHH',8,SIZE,SIZE,len(blocks),12)
    palette = b''.join(bytes((i,i,i,0)) for i in range(256))
    return header + struct.pack('<'+'I'*len(blocks),*(len(b) for b in blocks)) + palette + b''.join(blocks)

def eaf(frames):
    table = bytearray(); data = bytearray(); cache = {}
    for im in frames:
        raw = im.tobytes()
        if raw not in cache:
            payload = b'ZZ' + frame_data(im)
            cache[raw] = (len(payload),len(data)); data.extend(payload)
        table.extend(struct.pack('<II',*cache[raw]))
    body = table + data
    return b'\x89EAF' + struct.pack('<III',len(frames),sum(body)&0xffffffff,len(body)) + body

def verify_eaf(blob, frames):
    """Decode every generated frame according to the device's palette/RLE format."""
    n,checksum,length = struct.unpack_from('<III',blob,4)
    assert n == len(frames) and length == len(blob)-16 and checksum == sum(blob[16:])&0xffffffff
    start = 16+n*8
    for i, expected in enumerate(frames):
        size,off = struct.unpack_from('<II',blob,16+i*8)
        payload = blob[start+off:start+off+size]
        assert payload[:4] == b'ZZ_S'
        f = payload[2:]
        depth,w,h,blocks,bh = struct.unpack_from('<BHHHH',f,9)
        assert (depth,w,h,bh)==(8,SIZE,SIZE,12)
        lengths = struct.unpack_from('<'+'I'*blocks,f,18)
        pos = 18+4*blocks+1024; pixels = bytearray()
        for length in lengths:
            block = f[pos:pos+length]; assert block[0] == 0 and len(block)%2 == 1
            decoded = b''.join(bytes([v])*count for count,v in zip(block[1::2],block[2::2]))
            assert len(decoded)==w*bh
            pixels.extend(decoded); pos += length
        assert bytes(pixels)==expected.tobytes() and pos==len(f)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',type=Path,default=Path('diagnostics/2026-10-23/face'))
    args=ap.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    base=Path('diagnostics/2026-10-23/fixes/assets-xiaobao-mn5.bin')
    original=unpack(base.read_bytes()); assets={k:v for k,v in original.items() if not k.endswith('.eaf') or k=='listen.eaf'}
    previews={}
    for mood,name in [('neutral','neutral'),('happy','Happy'),('sad','Sad'),('sleep','sleep'),('confused','confused')]:
        frames=[face(i/FPS,mood) for i in range(80)]
        blob=eaf(frames); verify_eaf(blob,frames)
        assets[name+'.eaf']=(blob,SIZE,SIZE); previews[mood]=frames
    index=json.loads(assets['index.json'][0])
    for item in index['emoji_collection']:
        if item['name'] in ('neutral','idle'): item['file']='neutral.eaf'
        if item['file'] in ('cry.eaf',): item['file']='Sad.eaf'
        if item['file'] in ('angry.eaf',): item['file']='confused.eaf'
        if item['file'] in ('shocked.eaf',): item['file']='Happy.eaf'
        item['eaf']={'loop':True,'fps':FPS}
    for layout in index['layout']:
        if layout['name']=='eye_anim':
            layout.update(align='GFX_ALIGN_CENTER',x=0,y=0,width=SIZE,height=SIZE)
        if layout['name'] in ('clock_label','toast_label'):
            layout['y']=258
        if layout['name']=='listen_anim': layout['y']=65
    assets['index.json']=(json.dumps(index,ensure_ascii=False).encode(),0,0)
    blob=pack(assets); assert len(blob)<=8*1024*1024
    restored=unpack(blob)
    for name,value in original.items():
        if not name.endswith('.eaf') and name!='index.json': assert restored[name]==value
    assert json.loads(restored['index.json'][0])['multinet_model']==index['multinet_model']
    target=args.output/'assets-capsule-eyes.bin'; target.write_bytes(blob)
    frames=previews['neutral']; frames[0].save(args.output/'front.png')
    frames[50].save(args.output/'side.png')
    frames[0].save(args.output/'preview.gif',save_all=True,append_images=frames[1:],duration=100,loop=0)
    sheet=Image.new('RGB',(SIZE*3,SIZE), '#eeeeee')
    for i,k in enumerate((0,18,50)): sheet.paste(frames[k],(i*SIZE,0))
    sheet.save(args.output/'poses.png')
    manifest={'assets_bytes':len(blob),'sha256':hashlib.sha256(blob).hexdigest(),'fps':FPS,'loop_seconds':8,'frame_count':80,'verified':'all frames RLE decoded pixel-exact; wake model, font and static icons unchanged','base_assets':str(base)}
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))

if __name__=='__main__': main()

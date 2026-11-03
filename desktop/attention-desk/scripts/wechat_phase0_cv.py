"""Local, experimental layout and bubble diagnostics. No Windows or network I/O."""
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import re
import unicodedata

import cv2
import numpy as np


def text_key(text):
    return ''.join(unicodedata.normalize('NFKC',text).split())


def calibrate(image,header,dpi):
    pixels=np.asarray(image.convert('RGB'));gray=cv2.cvtColor(pixels,cv2.COLOR_RGB2GRAY).astype(np.int16)
    height,width=gray.shape;scale=dpi/96
    first=max(int(width*.2),header[0]-int(180*scale));last=header[0]-int(10*scale)
    columns=[]
    for x in range(max(3,first),last):
        delta=np.maximum(abs(gray[int(height*.15):int(height*.8),x]-gray[int(height*.15):int(height*.8),x-2]),
                         abs(gray[int(height*.15):int(height*.8),x]-gray[int(height*.15):int(height*.8),x+2]))
        columns.append((float(np.mean(delta>7)),x))
    score,left=max(columns,default=(0,header[0]-int(24*scale)))
    if score<.7:left=header[0]-int(24*scale)
    left=max(0,int(left))
    row_scores=[]
    for y in range(header[3]+5,height-4):
        delta=abs(gray[y-2,left+5:width-20]-gray[y+2,left+5:width-20])
        row_scores.append((float(np.mean(delta>7)),y))
    header_rows=[row for row in row_scores if row[1]<height*.3]
    header_score,top=max(header_rows,default=(0,header[3]+int(24*scale)))
    if header_score<.75:top=header[3]+int(24*scale)
    input_rows=[row for row in row_scores if height*.55<row[1]<height*.9]
    input_score,bottom=max(input_rows,default=(0,int(height*.74)))
    if input_score<.75:bottom=int(height*.74)
    bottom=min(bottom,int(height*.87))
    if bottom-top<200:raise ValueError('Message viewport cannot be separated from input area')
    return {'pane_left':left,'message_viewport':[left+int(8*scale),top+4,width-int(16*scale),bottom-4],
            'input_denylist':[left,bottom,width,height],
            'header_region':[max(left,header[0]-int(16*scale)),max(0,header[1]-int(12*scale)),
                             min(width,header[2]+int(16*scale)),header[3]+int(12*scale)],
            'separator_confidence':{'vertical':round(score,3),'header':round(header_score,3),'input':round(input_score,3)},
            'calibration':'experimental_local_cv'}


def visual_hash(image):
    array=np.asarray(image.convert('L').resize((16,16))).astype(np.int16)
    bits=(array>array.mean()).flatten()
    return ''.join(f'{sum(int(bit)<<(3-index) for index,bit in enumerate(bits[start:start+4])):x}'
                   for start in range(0,len(bits),4))


def bubbles(image,lines,layout,dpi):
    pixels=np.asarray(image.convert('RGB'));height,width=pixels.shape[:2];scale=dpi/96
    left,top,right,bottom=layout['message_viewport']
    sample=pixels[top:bottom,right-12:right-4].reshape(-1,3)
    colors,counts=np.unique(sample,axis=0,return_counts=True)
    background=colors[counts.argmax()].astype(np.int16)
    region=pixels[top:bottom,left:right];rgb=region.astype(np.int16)
    distance=np.max(abs(rgb-background),axis=2)
    white=(np.min(rgb,axis=2)>230)&(distance>7)
    green=(rgb[:,:,1]>rgb[:,:,0]+12)&(rgb[:,:,1]>rgb[:,:,2]+12)&(rgb[:,:,1]>160)
    mask=np.uint8((white|green)*255)
    # Avatars are outside the bubble lane. Text holes remain part of one contour.
    lane=int(65*scale)
    mask[:,:lane]=0
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    count,labels,stats,centroids=cv2.connectedComponentsWithStats(mask,8)
    result=[]
    for x,y,bw,bh,area in stats[1:]:
        if bw<22*scale or bh<20*scale or area<300*scale*scale:continue
        if bw>(right-left)*.98 and bh>(bottom-top)*.9:continue
        box=[int(left+x),int(top+y),int(left+x+bw),int(top+y+bh)]
        included=[]
        for line in lines:
            words=line.get('words',[])
            if not words:continue
            lx=min(word['x'] for word in words);ly=min(word['y'] for word in words)
            rx=max(word['x']+word['width'] for word in words);ry=max(word['y']+word['height'] for word in words)
            cx,cy=(lx+rx)/2,(ly+ry)/2
            if box[0]-3<=cx<=box[2]+3 and box[1]-3<=cy<=box[3]+3:
                included.append((ly,lx,line['text']))
        text='\n'.join(row[2] for row in sorted(included))
        key=text_key(text)
        image_key=visual_hash(image.crop(tuple(box)))
        signature=hashlib.sha256(('text:'+key if key else 'visual:'+image_key).encode('utf-8')).hexdigest()
        result.append({'box':box,'signature':signature,'visual_hash':image_key,'chars':len(key),
                       'ocr_lines':len(included),'clipped':bool(y<=2 or y+bh>=bottom-top-2),
                       'type':'text_or_card' if key else 'image_or_unreadable','_text':text})
    return sorted(result,key=lambda row:(row['box'][1],row['box'][0]))


def compare_views(previous,current):
    old=[row['signature'] for row in previous if not row['clipped']]
    new=[row['signature'] for row in current if not row['clipped']]
    blocks=SequenceMatcher(None,old,new,autojunk=False).get_matching_blocks()
    old_counts,new_counts=Counter(old),Counter(new)
    strong={value for value in old if old_counts[value]==new_counts[value]==1}
    matched=sum(block.size for block in blocks)
    ordered_anchors=sum(value in strong for block in blocks for value in old[block.a:block.a+block.size])
    return {'matched_occurrences':matched,'ordered_unique_anchors':ordered_anchors,
            'same_visible_sequence':bool(old) and old==new,
            'old_count':len(old),'new_count':len(new),
            'boundary_verified':ordered_anchors>=2,
            'ambiguous_repeated_content':bool(set(old)&set(new)) and not strong}


def safe_bubbles(rows):
    return [{key:value for key,value in row.items() if not key.startswith('_')} for row in rows]


def translation(previous,current,viewport):
    a=np.asarray(previous.crop(tuple(viewport)).convert('L'),dtype=np.float32)
    b=np.asarray(current.crop(tuple(viewport)).convert('L'),dtype=np.float32)
    shift,response=cv2.phaseCorrelate(a,b)
    return {'dx':round(shift[0],2),'dy':round(shift[1],2),'response':round(response,3)}

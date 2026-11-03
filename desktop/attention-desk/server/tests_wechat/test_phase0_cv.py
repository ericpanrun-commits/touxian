import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image,ImageDraw

pytest.importorskip('cv2')

spec=importlib.util.spec_from_file_location('wechat_phase0_cv',Path(__file__).resolve().parents[2]/'scripts/wechat_phase0_cv.py')
cv=importlib.util.module_from_spec(spec);spec.loader.exec_module(cv)


def row(text,x,y,width=220):
    return {'text':text,'words':[{'x':x,'y':y,'width':width,'height':22}]}


def test_bubbles_keep_two_identical_occurrences_and_order():
    image=Image.new('RGB',(1000,900),(237,237,237));draw=ImageDraw.Draw(image)
    draw.rectangle((450,200,800,280),fill='white');draw.rectangle((450,350,800,430),fill='white')
    layout={'message_viewport':[300,150,950,700]}
    result=cv.bubbles(image,[row('收到',470,230),row('收到',470,380)],layout,96)
    assert len(result)==2 and result[0]['signature']==result[1]['signature']
    assert result[0]['box'][1]<result[1]['box'][1]
    assert json.loads(json.dumps(cv.safe_bubbles(result)))
    compared=cv.compare_views(result,result)
    assert compared['ambiguous_repeated_content'] and not compared['boundary_verified']


def test_overlap_requires_two_distinct_ordered_anchors():
    def item(value):return {'signature':value,'clipped':False}
    old=[item('a'),item('b'),item('c')];new=[item('x'),item('a'),item('b')]
    result=cv.compare_views(old,new)
    assert result['boundary_verified'] and result['ordered_unique_anchors']==2
    assert not cv.compare_views([item('a')],[item('a')])['boundary_verified']
    assert '_text' not in cv.safe_bubbles([{'signature':'a','_text':'private'}])[0]

"""Phase 0: one header-only crop and local OCR; run in a bounded child process."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy
from server.wechat_collect.visual_spike import _output_directory,inspect_header


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window-handle',type=int,required=True)
    parser.add_argument('--chat-name',required=True)
    parser.add_argument('--capture-mode',choices=['printwindow','printwindow_render','render_child','visible_header'],default='printwindow')
    parser.add_argument('--header-layout',choices=['window_top','chat_header'],default='chat_header')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    destination=_output_directory(args.report)
    policy=SchedulerPolicy(WeChatConfig(enabled=True))
    report=inspect_header(args.window_handle,args.chat_name,destination.parent,policy,args.capture_mode,args.header_layout)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    # No OCR text or private window titles are printed.
    print(json.dumps({key:report[key] for key in
                      ('level','target_header','header_captures','ocr_calls','deepseek_calls','vision_calls','safety')},
                     ensure_ascii=False))


if __name__=='__main__':
    main()

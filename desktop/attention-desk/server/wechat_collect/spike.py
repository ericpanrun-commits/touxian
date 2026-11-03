"""Bounded structural feasibility probe. Never opens chats or reads message bodies."""
from __future__ import annotations

from datetime import datetime, timezone
import unicodedata

from .config import WeChatConfig
from .scheduler import SchedulerPolicy

CAPABILITIES = (
    'actual_version', 'window_location', 'conversation_list', 'target_group_binding',
    'ordered_messages', 'body_text', 'sender', 'source_time', 'message_type',
    'image_location', 'foreground', 'background', 'minimized', 'muted_mobile_read_no_unread',
    'multi_screen_history', 'old_anchor', 'restart_selector_stability', 'focus_unchanged',
    'activity_cancellation',
)


def group_name_variants(chat_name):
    """Bounded exact queries for known punctuation variants; no private Name scan."""
    normalized=unicodedata.normalize('NFKC',chat_name)
    spaced=' '.join(normalized.replace('(',' ').replace(')',' ').split())
    values=[chat_name,normalized,''.join(chat_name.split()),''.join(normalized.split()),
            spaced,''.join(spaced.split())]
    return list(dict.fromkeys(value for value in values if value))


def summarize_readability(report):
    """Derive a bounded conclusion from actual structural observations only."""
    windows=report['windows']
    nodes=[node for window in windows for node in window.get('structure',[])]
    known=nodes and all('patterns' in node for node in nodes)
    content_roles={24,33,34,35,36,41,42}  # Table/list/outline/text; no Name/Value reads.
    msaa_content=any(row.get('role') in content_roles for node in nodes for row in node.get('msaa',{}).get('structure',[]))
    opaque=known and not any(node['patterns'].get('text') or node['patterns'].get('selection') or node['patterns'].get('scroll') for node in nodes) and not msaa_content
    if opaque and all(window.get('target_match_count')==0 for window in windows):
        for key in ('conversation_list','target_group_binding','body_text'):
            report['matrix'][key]={'status':'fail','reason':'uia_msaa_shell_only_no_accessible_chat_content'}
        for key in ('foreground','background'):
            present=any(window.get('is_foreground')==(key=='foreground') for window in windows)
            if present:
                report['matrix'][key]={'status':'partial','reason':'window_metadata_readable_chat_content_not_exposed'}
        report['level_reason']='NO_GO: UIA and standard MSAA expose window chrome only; cannot verify target group or read ordered messages.'
    return report


def _process_metadata():
    """WeChat executable metadata only; no process memory or chat content access."""
    import json
    import os
    import subprocess
    command = """$ErrorActionPreference='Stop'
$items=@(Get-Process -Name Weixin,WeChat -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{pid=$_.Id;path=$_.Path;file_version=$_.FileVersion} })
[pscustomobject]@{query_completed=$true;items=$items} | ConvertTo-Json -Compress
"""
    result=subprocess.run(['pwsh','-NoProfile','-NonInteractive','-Command',command],
                          stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                          timeout=5,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError('Process metadata query unavailable')
    value=json.loads(result.stdout.decode('utf-8-sig'))
    if not value.get('query_completed') or not isinstance(value.get('items'),list):
        raise RuntimeError('Process metadata query incomplete')
    return value['items']


def _msaa_structure(accessible,interface,policy):
    import ctypes
    from comtypes.automation import VARIANT
    from .windows import require_idle
    function=ctypes.OleDLL('oleacc').AccessibleChildren
    function.argtypes=[ctypes.POINTER(interface),ctypes.c_long,ctypes.c_long,ctypes.POINTER(VARIANT),ctypes.POINTER(ctypes.c_long)]
    function.restype=ctypes.c_long
    pending=[(accessible,0,-1)]
    result=[]
    while pending and len(result)<60:
        if policy:
            require_idle(policy)
        current,depth,parent=pending.pop(0)
        count=int(current.accChildCount)
        role=current.accRole(0)
        index=len(result)
        result.append({'depth':depth,'parent':parent,'role':role if isinstance(role,int) else None,'child_count':count})
        if count and depth<5:
            variants=(VARIANT*min(count,60-len(result)))()
            obtained=ctypes.c_long()
            function(current,0,len(variants),variants,ctypes.byref(obtained))
            for variant in variants[:obtained.value]:
                if variant.vt==9:  # VT_DISPATCH; no Name/Value/Text getters.
                    pending.append((variant.value.QueryInterface(interface),depth+1,index))
                elif variant.vt==3:  # Simple child ID. Its role is metadata only.
                    role=current.accRole(variant.value)
                    result.append({'depth':depth+1,'parent':index,'role':role if isinstance(role,int) else None,'child_count':0})
    return result


def _control_metadata(control,policy=None):
    result={'class_name':control.ClassName,'framework_id':control.FrameworkId,
            'control_type':control.ControlTypeName,'handle':control.NativeWindowHandle,
            'is_offscreen':control.IsOffscreen}
    getter=getattr(control,'GetPropertyValue',None)
    if getter:
        result['patterns']={name:bool(getter(property_id)) for name,property_id in
                            (('text',30040),('scroll',30034),('selection',30037),('legacy_accessible',30090))}
        if result['patterns']['legacy_accessible']:
            legacy=control.GetLegacyIAccessiblePattern()
            result['msaa']={'role':legacy.Role,'state':legacy.State,'child_id':legacy.ChildId}
            try:
                import comtypes.client
                accessibility=comtypes.client.GetModule('oleacc.dll')
                unknown=legacy.GetIAccessible()
                if unknown:
                    accessible=unknown.QueryInterface(accessibility.IAccessible)
                    result['msaa']['child_count']=accessible.accChildCount
                elif control.NativeWindowHandle:
                    # Query the standard client accessibility object directly;
                    # only its child count is read, never arbitrary Name/Value.
                    import ctypes
                    from ctypes import wintypes
                    interface=accessibility.IAccessible
                    accessible=ctypes.POINTER(interface)()
                    function=ctypes.OleDLL('oleacc').AccessibleObjectFromWindow
                    function.argtypes=[wintypes.HWND,wintypes.DWORD,ctypes.POINTER(comtypes.GUID),ctypes.POINTER(ctypes.POINTER(interface))]
                    function.restype=ctypes.c_long
                    code=function(control.NativeWindowHandle,0xfffffffc,ctypes.byref(interface._iid_),ctypes.byref(accessible))
                    result['msaa']['client_query_hresult']=code
                    if code==0 and accessible:
                        result['msaa']['client_child_count']=accessible.accChildCount
                        result['msaa']['structure']=_msaa_structure(accessible,interface,policy)
            except Exception as exc:
                result['msaa']['inspection_error_type']=type(exc).__name__
    return result


def inspect_structure(*, clock=None, chat_name=None):
    # No override exists for this diagnostic. The hard night rule wins.
    config = WeChatConfig(enabled=True)
    policy = SchedulerPolicy(config, **({'clock': clock} if clock else {}))
    at = policy.clock()
    allowed = policy.authorize(operation='uia', maximum_seconds=15)
    matrix = {key: {'status': 'not_tested', 'reason': 'quiet_hours' if not allowed.allowed else 'requires_bound_test_group'} for key in CAPABILITIES}
    report = {'recorded_at': at.isoformat(), 'timezone': config.timezone,
              'level': 'NO_GO', 'level_reason': 'unverified: ' + allowed.reason,
              'runtime_verified': False, 'matrix': matrix,
              'safety': {'messages_sent': 0, 'write_operations': 0, 'drafts_modified': 0,
                         'send_triggered': False, 'group_settings_modified': False},
              'real_operations': 0, 'uia_probe_attempted': False, 'desktop_state_checks': 0,
              'frameworks': [], 'windows': [],
              'note': 'NO_GO means automatic collection is blocked; untested is not a demonstrated UIA failure.'}
    if chat_name:
        report['allowlisted_test_chat']=chat_name
    if not allowed.allowed:
        return report
    from .windows import require_idle
    try:
        report['desktop_state_checks'] += 1
        before = require_idle(policy)
        report['processes']=_process_metadata()
        process_ids={item['pid'] for item in report['processes']}
        versions=sorted({item['file_version'] for item in report['processes'] if item.get('file_version')})
        if versions:
            matrix['actual_version']={'status':'pass','reason':'running_process_file_metadata','versions':versions}
        if not process_ids:
            matrix['window_location']={'status':'fail','reason':'wechat_process_not_running'}
            for result in matrix.values():
                if result['status']=='not_tested':
                    result['reason']='wechat_process_not_running'
            report['wechat_available']=False
            report['level_reason']='WeChat process not running; did not launch it or attempt UIA.'
            return report
        # This tool runs in its dedicated diagnostic process; initialise COM as MTA.
        import sys
        sys.coinit_flags = 0
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            # Top-level metadata only. Never enumerate child names or private chat previews.
            windows = []
            report['uia_probe_attempted'] = True
            report['real_operations'] += 1
            for control in auto.GetRootControl().GetChildren():
                policy.require(operation='uia')
                report['desktop_state_checks'] += 1
                state = require_idle(policy)
                if state.foreground != before.foreground or state.mouse != before.mouse:
                    report['interrupted_for_activity'] = True
                    report['activity_observation']={'foreground_changed':state.foreground!=before.foreground,
                                                    'physical_pointer_changed':state.mouse!=before.mouse,
                                                    'idle_before':round(before.idle_seconds,1),'idle_after':round(state.idle_seconds,1)}
                    matrix['activity_cancellation'] = {'status':'partial','reason':'passive_probe_stopped_after_foreground_or_pointer_change; chat_read_not_tested'}
                    raise RuntimeError('User activity')
                if control.ProcessId in process_ids:
                    window=_control_metadata(control,policy)
                    window['is_foreground']=control.NativeWindowHandle==before.foreground
                    windows.append(window)
                    report['real_operations'] += 1
                    # Structural types only: never read arbitrary Name/Value/Text fields.
                    tree=[]
                    pending=[(control,0,-1)]
                    while pending and len(tree)<120:
                        node,depth,parent=pending.pop(0)
                        require_idle(policy)
                        record=_control_metadata(node,policy)|{'depth':depth,'parent':parent}
                        index=len(tree)
                        tree.append(record)
                        if depth<5:
                            pending.extend((child,depth+1,index) for child in node.GetChildren())
                    window['structure']=tree
                    if chat_name:
                        if len(chat_name)>120 or '\n' in chat_name:
                            raise ValueError('Invalid allowlisted chat name')
                        from uiautomation import uiautomation as core
                        client=core._AutomationClient.instance().IUIAutomation
                        window['target_matches']=[]
                        window['target_match_count']=0
                        window['target_name_queries']=group_name_variants(chat_name)
                        for name in window['target_name_queries']:
                            condition=client.CreatePropertyCondition(auto.PropertyId.NameProperty,name)
                            require_idle(policy)
                            matches=control.Element.FindAll(7,condition)  # TreeScope_Subtree; provider-side exact name.
                            window['target_match_count']+=matches.Length
                            remaining=8-len(window['target_matches'])
                            window['target_matches'].extend(_control_metadata(auto.Control.CreateControlFromElement(matches.GetElement(i)),policy) for i in range(min(matches.Length,remaining)))
                        # A name match alone cannot certify the group/header or permit body reads.
                        found=window['target_match_count']>0
                        previous_match=matrix['target_group_binding']['status']=='partial'
                        matrix['target_group_binding']={'status':'partial' if found or previous_match else 'not_tested','reason':'exact_name_match_only_not_a_verified_binding' if found or previous_match else 'target_not_exposed_in_current_view'}
            report['windows'] = windows
            report['frameworks'] = sorted({w['framework_id'] for w in windows})
            matrix['window_location'] = {'status': 'pass' if windows else 'fail', 'reason': 'exact_wechat_process_id' if windows else 'no_known_window_found'}
        report['desktop_state_checks'] += 1
        after = require_idle(policy)
        unchanged = before.foreground == after.foreground and before.mouse == after.mouse
        matrix['focus_unchanged'] = {'status': 'pass' if unchanged else 'fail', 'reason': 'passive_probe_only'}
        report['level_reason'] = 'Bound-group reading, history and anchors still require actual certification.'
        summarize_readability(report)
    except Exception as exc:
        report['probe_error_type'] = type(exc).__name__
        report['probe_error_code'] = getattr(exc,'code','probe_incomplete')
        for result in matrix.values():
            if result['status']=='not_tested':
                result['reason']=report['probe_error_code']
        report['level_reason'] = 'Structural probe incomplete; no message reads attempted.'
    return report

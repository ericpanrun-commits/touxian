"""Media references from authorized message XML; never crawl other chats' caches."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import struct
from xml.etree import ElementTree as ET


def describe_content(content, message):
    kind = int(message.get('local_type') or 0) & 0xffffffff
    if not content.lstrip().startswith('<'):
        return content, None
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        # Never emit cryptographic credentials embedded in malformed media XML.
        return ('[媒体元数据无法解析]' if kind != 1 else '[消息 XML 无法解析]'), None
    if kind == 3:
        image = root.find('.//img')
        digest = image.get('md5', '') if image is not None else ''
        reference = {'kind': 'image', 'md5': digest.lower() if re.fullmatch(r'[a-fA-F0-9]{32}', digest) else None,
                     'local_id': message['local_id'], 'shard': message['shard'], 'cache_status': 'unchecked'}
        return '[图片]', reference
    if kind == 49:
        title = root.findtext('.//appmsg/title') or ''
        subtype = root.findtext('.//appmsg/type') or ''
        url = root.findtext('.//appmsg/url') or ''
        reference = {'kind': 'file' if subtype == '6' else 'miniapp' if subtype == '36' else 'link',
                     'title': title, 'subtype': subtype, 'local_id': message['local_id'], 'shard': message['shard'],
                     'url': url, 'cache_status': 'unchecked' if subtype == '6' else 'not_local_file'}
        if subtype=='6':
            md5=root.findtext('.//appmsg/md5') or root.findtext('.//appmsg/appattach/md5')
            if not md5:
                values={n.text.strip().lower() for n in root.findall('.//md5') if n.text and re.fullmatch(r'[a-fA-F0-9]{32}',n.text.strip())}
                md5=next(iter(values)) if len(values)==1 else None
            if md5 and re.fullmatch(r'[a-fA-F0-9]{32}',md5.strip()):reference['file_md5']=md5.strip().lower()
            size=root.findtext('.//appattach/totallen') or ''
            if size.isdigit() and int(size)<=50_000_000:reference['declared_size']=int(size)
        return '\n'.join(s for s in [title, root.findtext('.//appmsg/des'), url] if s), reference
    if kind == 10000:
        return root.findtext('.//revokemsg/content') or ''.join(root.itertext()).strip(), None
    # Other media: retain human-readable text, never serialize raw crypto XML.
    return ''.join(root.itertext()).strip() or f'[{message["type"]}]', None


def cache_references(reader, internal_id, message):
    if internal_id not in reader.allowed_ids:
        raise RuntimeError('chat_not_allowlisted')
    reference = message.get('media_reference')
    if not reference:
        return []
    account = Path(reader.account_dir).resolve()
    if reference['kind'] == 'image' and reference.get('md5'):
        base = account / 'msg' / 'attach' / hashlib.md5(internal_id.encode()).hexdigest()
        raw = reader.get_message_row(internal_id, message['local_id'], shard=message['shard'])
        packed = (raw or {}).get('packed_info') or b''
        candidates = re.findall(rb'[a-fA-F0-9]{32}', packed) if isinstance(packed, bytes) else []
        digests = list(dict.fromkeys([*(c.decode().lower() for c in candidates[:4]), reference['md5']]))
        # Enumerate only the authorized group's directories and the exact image names.
        matches = []
        for suffix, tier in [('_h.dat', 'original'), ('.dat', 'mid'), ('_t.dat', 'thumb')]:
            for digest in digests:
                for hit in base.glob('*/Img/' + digest + suffix):
                    if hit.resolve().is_relative_to(base.resolve()) and hit.is_file():
                        matches.append({'path': str(hit), 'tier': tier, 'bytes': hit.stat().st_size})
        reference['cache_status'] = 'available_encrypted' if matches else 'missing'
        reference['cache_files'] = matches
        return matches
    if reference['kind'] == 'file':
        # Only a path carried by this exact message is authoritative. Never
        # search the shared file cache by a display filename.
        getter=getattr(reader,'get_message_row',None)
        raw=(getter(internal_id,message['local_id'],shard=message['shard']) if getter and 'local_id' in message and 'shard' in message else {}) or {}
        packed=raw.get('packed_info') or b''
        paths=bound_file_paths(packed,raw.get('content') or '',account)
        if not paths and reference.get('file_md5') and reference.get('declared_size'):
            name=reference.get('title') or ''
            # A filename is only a lookup hint. No candidate is ingested unless
            # both the message's declared length and content MD5 agree.
            if Path(name).name==name and not any(c in name for c in ('/','\\','\x00')):
                base=account/'msg/file'
                for candidate in base.glob('*/'+__import__('glob').escape(name)):
                    if candidate.resolve().is_relative_to(base.resolve()) and candidate.is_file() and candidate.stat().st_size==reference['declared_size']:
                        with candidate.open('rb') as stream:digest=hashlib.file_digest(stream,'md5').hexdigest()
                        if digest==reference['file_md5']:paths.append(candidate.resolve())
        matches=[{'path':str(path),'tier':'original','bytes':path.stat().st_size} for path in paths]
        if matches:
            reference['binding']='message_path' if bound_file_paths(packed,raw.get('content') or '',account) else 'message_size_and_md5'
            reference['cache_status']='available';reference['cache_files']=matches
            return matches
        reference['cache_status'] = 'requires_message_bound_resource_path'
        reference['cache_files'] = []
        return []
    return []


def bound_file_paths(packed,content,account):
    """Paths embedded in the authorized row, constrained to its account's cache."""
    values=[]
    if isinstance(content,str) and content.lstrip().startswith('<'):
        try:
            root=ET.fromstring(content)
            values.extend(n.text for n in root.iter() if n.tag.lower() in {'filepath','file_path','localpath','local_path'} and n.text)
        except ET.ParseError:pass
    if isinstance(packed,bytes):
        for encoding in ('utf-8','utf-16-le'):
            text=packed.decode(encoding,errors='ignore')
            values.extend(re.findall(r'(?:[A-Za-z]:[\\/][^\x00<>"|\r\n]{1,800}|msg[\\/]file[\\/][^\x00<>"|\r\n]{1,500})\.(?:pdf|docx?|xlsx?|pptx?|txt|csv|png|jpe?g|webp|gif)',text,re.I))
    result=[]
    root=(Path(account)/'msg').resolve()
    for value in values:
        path=Path(value.replace('\\','/'))
        path=(path if path.is_absolute() else Path(account)/path).resolve()
        if path.is_relative_to(root) and path.is_file() and path.stat().st_size<=50_000_000 and path not in result:result.append(path)
    return result


def preserve_local_media(reader, internal_id, message, output):
    """Copy/decrypt only files referenced by one authorized message; no UI or CDN."""
    files = cache_references(reader, internal_id, message)
    reference = message.get('media_reference')
    if not files or not reference:
        return None
    output = Path(output).resolve()
    from .db_reader import private_directory
    private_directory(output)
    cache_root = Path(reader.account_dir).resolve() / 'msg'
    for selected in files:
        source = Path(selected['path']).resolve()
        # Recheck provenance at read time; a changed or truncated original must
        # not prevent trying a message-bound thumbnail that is actually readable.
        if not source.is_relative_to(cache_root):
            raise RuntimeError('media_path_outside_account')
        if source.stat().st_size>50_000_000:
            continue
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if reference['kind'] == 'file':
            if reference.get('binding')=='message_size_and_md5' and (len(data)!=reference.get('declared_size') or hashlib.md5(data).hexdigest()!=reference.get('file_md5')):
                reference['cache_status']='local_cache_changed';continue
            target = output / (digest + source.suffix)
            target.write_bytes(data)
            reference['evidence_sha256'] = digest
            reference['evidence_path'] = str(target)
            return str(target)
        # Encrypted bytes remain available even without a usable local key.
        encrypted = output / (digest + '.dat')
        encrypted.write_bytes(data)
        reference['encrypted_evidence_sha256'] = digest
        reference['encrypted_evidence_path'] = str(encrypted)
        try:
            decoded = decrypt_cached_image(reader, data)
        except (ValueError, RuntimeError, OSError, IndexError,struct.error):
            reference['cache_status'] = 'encrypted_evidence_saved';continue
        extension = image_extension(decoded)
        if extension is None:
            reference['cache_status'] = 'unsupported_local_format';continue
        evidence_digest = hashlib.sha256(decoded).hexdigest()
        target = output / (evidence_digest + '.' + extension)
        target.write_bytes(decoded)
        reference.update(evidence_path=str(target), evidence_sha256=evidence_digest,
                         evidence_tier=selected['tier'], cache_status='decrypted')
        return str(target)
    return None


def image_extension(data):
    if data.startswith(b'\xff\xd8\xff'):return 'jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'png'
    if data.startswith((b'GIF87a',b'GIF89a')):return 'gif'
    if data.startswith(b'RIFF') and data[8:12]==b'WEBP':return 'webp'
    return None


def decrypt_cached_image(reader, data):
    """Replica v1/v2 formats. Only cfg from the authorized DB-key read is used.

    Deliberately no media-key heap scan, global template search or interactive fetch.
    """
    if data[:6] == b'\x07\x08\x56\x32\x08\x07':
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        if not hasattr(reader, '_local_image_cfg'):
            extracted = reader.extract_master_key()
            reader._local_image_cfg = (extracted[1], extracted[2]) if extracted else None
        cfg = reader._local_image_cfg
        if not cfg:
            raise RuntimeError('local_image_key_unavailable')
        dword, wxid = cfg
        key = hashlib.md5((str(dword) + wxid).encode()).hexdigest()[:16].encode()
        xor_key = dword & 255
        aes_size, xor_size = struct.unpack_from('<II', data, 6)
        aes_length = ((aes_size // 16) + 1) * 16
        if len(data) < 15 + aes_length + xor_size:
            raise ValueError('truncated_local_image')
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        plain = decryptor.update(data[15:15 + aes_length]) + decryptor.finalize()
        pad = plain[-1]
        if not 1 <= pad <= 16 or plain[-pad:] != bytes([pad]) * pad:
            raise ValueError('invalid_local_image_key')
        middle_end = len(data) - xor_size
        decoded = plain[:-pad] + data[15 + aes_length:middle_end] + bytes(b ^ xor_key for b in data[middle_end:])
        return decoded
    if data[:6] == b'\x07\x08\x05\x56\x02\x05':
        body = data[22:]
    else:
        body = data
    if not body:raise ValueError('empty_local_image')
    for magic in (b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n',b'GIF87a',b'GIF89a',b'RIFF'):
        key = body[0] ^ magic[0]
        decoded = bytes(b ^ key for b in body)
        if decoded.startswith(magic) and image_extension(decoded):
            return decoded
    raise ValueError('unsupported_local_image')

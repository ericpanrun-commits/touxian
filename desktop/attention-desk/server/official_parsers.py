"""Static VSB HTML patterns observed on the five configured ZQU columns."""
from __future__ import annotations

import mimetypes
import re
from datetime import date
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup, NavigableString, Comment
from .official_sources import SourceFailure, canonical_url

PATTERNS = {
    'vsb_school': {'links': 'li[id^="line_"] a[href*="info/"]', 'title': '.arc-tit h1', 'date': '.arc-tit .arc-info'},
    'vsb_div': {'links': '.btlb-tm a[href*="info/"]', 'title': '#article-title', 'date': '#article-info'},
    'vsb_table': {'links': 'tr a[href*="info/"]', 'title': '#article-title', 'date': '#article-info'},
    'vsb_generic': {'links': 'a[href*="info/"]', 'title': '#article-title, .arc-tit h1, .article-title, .title h1, h1', 'date': '#article-info, .arc-info, .article-info, .info', 'body': '.v_news_content, #vsb_content'},
}
ARTICLE_PATH = re.compile(r'^/info/\d+/\d+\.htm$')
FILE_EXTENSIONS = {'pdf','doc','docx','txt','png','jpg','jpeg','xls','xlsx','zip','rar','ppt','pptx'}


class GenericListAdapter:
    """Shared static parser with verified per-site selector overrides."""
    def __init__(self, source):
        self.pattern = {**PATTERNS[source.parser_type], **source.selectors}

    def links(self, soup):
        return soup.select(self.pattern['links'])


def safe_html(node, url, hosts):
    allowed = {'div','p','br','span','strong','b','em','i','u','ul','ol','li','table','thead','tbody','tr','td','th','h1','h2','h3','h4','blockquote','a','img'}
    for element in list(node.find_all(True)):
        if element.name not in allowed:
            element.unwrap()
            continue
        attrs = {}
        if element.name in {'a','img'}:
            key = 'href' if element.name=='a' else 'src'
            try:
                attrs[key] = canonical_url(urljoin(url, element.get(key,'')), hosts)
                if element.name=='a': attrs['rel']='noopener noreferrer'
                elif element.get('alt'): attrs['alt']=str(element['alt'])[:300]
            except SourceFailure:
                pass
        element.attrs = attrs
    node.attrs = {}
    return str(node)[:500000]


def published_date(text):
    match = re.search(r'(?<!\d)((?:19|20|21)\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})(?:日)?', text)
    if match:
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            pass
    return None


def parse_list(body, url, source):
    soup = BeautifulSoup(body, 'lxml')
    adapter = GenericListAdapter(source)
    links = adapter.links(soup)
    if not links and not re.search(r'共\s*0\s*条|暂无(?:通知|公告)', soup.get_text()):
        raise SourceFailure('list_structure', '没有找到已验证的通知列表结构，原有通知保留。')
    entries = {}
    for link in links:
        try:
            target = canonical_url(urljoin(url, link['href']), source.allowed_hosts)
        except SourceFailure:
            continue
        if not ARTICLE_PATH.fullmatch(urlsplit(target).path):
            continue
        title = ' '.join((link.get('title') or link.get_text(' ', strip=True)).split())[:500]
        if not title:
            continue
        row = link.find_parent('li') or link.find_parent('tr') or link.parent
        if source.parser_type == 'vsb_school':
            month, day = row.select_one('.time i'), row.select_one('.time span')
            publication = published_date(f'{month.get_text(strip=True)}-{day.get_text(strip=True)}') if month and day else None
        else:
            # The title can contain dates; only the list's dedicated date cell counts.
            dates = row.select('td, span, time, em, i, .date, .time')
            publication = next((value for cell in reversed(dates) if (value := published_date(cell.get_text(' ', strip=True)))), None)
            if not publication:
                copy = BeautifulSoup(str(row), 'lxml')
                for a in copy.select('a'): a.decompose()
                publication = published_date(copy.get_text(' ',strip=True))
        entries.setdefault(target, {'url': target, 'title': title, 'published_at': publication})
    next_page = None
    for link in soup.select('a[href]'):
        if link.get_text(strip=True) in {'下页', '下一页'}:
            try:
                target = canonical_url(urljoin(url, link['href']), source.allowed_hosts)
                # Pagination cannot escape to a second site or an article.
                if urlsplit(target).hostname==urlsplit(source.list_url).hostname and not ARTICLE_PATH.fullmatch(urlsplit(target).path):
                    next_page = target
            except SourceFailure:
                pass
            break
    return sorted(entries.values(), key=lambda entry: (entry['published_at'] or '', entry['title']), reverse=True), next_page


def body_text(node):
    def render(part):
        if isinstance(part, Comment):
            return ''
        if isinstance(part, NavigableString):
            return str(part)
        if part.name == 'br':
            return '\n'
        text = ''.join(render(child) for child in part.children)
        if part.name in {'p', 'div', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            return '\n' + text + '\n'
        return text + ('\t' if part.name in {'td', 'th'} else '')
    text = render(node).replace('\r', '').replace('\xa0', ' ')
    text = '\n'.join(re.sub(r'[ \t]+', ' ', line).strip() for line in text.splitlines())
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_article(body, url, source, entry):
    soup = BeautifulSoup(body, 'lxml')
    pattern = GenericListAdapter(source).pattern
    node = soup.select_one(pattern.get('body', '.v_news_content, #vsb_content'))
    if node is None:
        raise SourceFailure('article_structure', '没有找到已验证的文章正文，未把导航当成原文。')
    title_node = soup.select_one(pattern['title'])
    title = (title_node.get_text(' ', strip=True) if title_node else entry['title'])[:500]
    date_node = soup.select_one(pattern['date'])
    publication = published_date(date_node.get_text(' ', strip=True)) if date_node else None
    if not publication:
        meta = soup.select_one('meta[name="PubDate"],meta[name="publishdate"],meta[property="article:published_time"]')
        publication = published_date(meta.get('content','')) if meta else None
    # VSB embeds some document-only notices through a literal PDF argument.
    # Read that URL as metadata; never evaluate JavaScript or download the PDF.
    embedded_pdfs = []
    for script in node.select('script'):
        embedded_pdfs.extend(re.findall(r'''showVsbpdfIframe\s*\(\s*["']([^"'<>]+\.pdf)["']\s*,''', script.get_text()))
    for element in node.select('script,style,noscript,iframe,object,embed,form,nav,header,footer,[hidden],[aria-hidden="true"]'):
        element.decompose()
    attachments, images = [], []
    seen = set()
    # VSB's generated attachment list can be a sibling of the body container.
    for link in [*node.select('a[href]'), *soup.select('a[href*="urltype=news.DownloadAttachUrl"]')]:
        name = link.get_text(' ', strip=True) or link.get('title') or ''
        try:
            target = canonical_url(urljoin(url, link['href']), source.allowed_hosts)
        except SourceFailure:
            continue
        extension = urlsplit(name).path.rsplit('.', 1)[-1].lower() if '.' in name else ''
        if extension not in FILE_EXTENSIONS:
            extension = urlsplit(target).path.rsplit('.',1)[-1].lower()
        if not name or target in seen or not ('download.jsp' in target or extension in FILE_EXTENSIONS):
            continue
        seen.add(target)
        attachments.append({'filename': name, 'url': target, 'extension': extension if extension in FILE_EXTENSIONS else None,
                            'mime': mimetypes.guess_type('file.'+extension)[0] if extension in FILE_EXTENSIONS else mimetypes.guess_type(name)[0], 'status': 'unparsed'})
    for path in embedded_pdfs:
        try:
            target = canonical_url(urljoin(url, path), source.allowed_hosts)
            if target not in seen:
                attachments.append({'filename': urlsplit(target).path.rsplit('/', 1)[-1], 'url': target, 'extension': 'pdf', 'mime': 'application/pdf', 'status': 'unparsed'})
                seen.add(target)
        except SourceFailure:
            pass
    for image in node.select('img[src]'):
        try:
            target = canonical_url(urljoin(url, image['src']), source.allowed_hosts)
            images.append({'url': target, 'alt': image.get('alt', ''), 'status': 'unparsed'})
        except SourceFailure:
            pass
    text = body_text(node)[:300000]
    if not text and not images and not attachments:
        raise SourceFailure('empty_body', '正文为空，未生成临时或虚构原文。')
    return {'title': title, 'published_at': publication or entry.get('published_at'), 'original_text': text or title,
            'attachments': attachments[:100], 'images': images[:100], 'body_html': safe_html(node,url,source.allowed_hosts), 'body_status': 'text' if text else 'image_only' if images else 'document_only'}

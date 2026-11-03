"""Small course catalogue and read-only suggestions; no model or external calls."""
from __future__ import annotations
import re
import unicodedata
from .personal_store import PersonalError, identifier

def normalized_name(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()

def course_public(connection, course_id):
    if course_id is None:
        return None
    row = connection.execute("SELECT id,name,normalized_name FROM course WHERE id=?", (course_id,)).fetchone()
    if row is None:
        raise PersonalError("所选课程不存在，请刷新课程列表；事项尚未保存。", "invalid_course", 400)
    return dict(row)

class CourseStore:
    def __init__(self, personal):
        self.personal = personal

    @staticmethod
    def resolve(connection, name):
        row = connection.execute("""SELECT c.* FROM course c JOIN course_alias a ON a.course_id=c.id
            WHERE a.normalized_alias=?""", (normalized_name(name),)).fetchone()
        return dict(row) if row else None

    def create(self, name):
        with self.personal.connection(write=True) as c:
            return self._create(c,name)

    @classmethod
    def _create(cls,c,name):
        name = " ".join(unicodedata.normalize("NFKC", name).split())
        key = normalized_name(name)
        if not key or len(name) > 120:
            raise PersonalError("课程名称请填写 1–120 字。", "invalid_course_name", 400)
        existing = cls.resolve(c,name)
        if existing:
            return {**existing,"created":False}
        course_id = identifier()
        c.execute("INSERT INTO course(id,name,normalized_name) VALUES (?,?,?)",(course_id,name,key))
        c.execute("INSERT INTO course_alias(normalized_alias,course_id) VALUES (?,?)",(key,course_id))
        return {**course_public(c,course_id),"created":True}

    @classmethod
    def _timetable(cls,c,settings,*,register=False):
        """Schedule owns times/titles; this catalogue alone owns Course IDs.

        A read projection may resolve existing identities but never create any.
        Explicit mappings and existing aliases must agree before registration.
        """
        mappings={};created=[];conflicts=[]
        configured=settings.get('course_mappings') or {}
        names={}
        for session in settings.get('sessions') or []:
            title=session.get('title') if isinstance(session,dict) else None
            if not isinstance(title,str) or not title.strip() or len(title)>120:
                conflicts.append({'title':title if isinstance(title,str) else '', 'reason':'invalid_course_name'})
                continue
            names.setdefault(title,None)
        for title in names:
            selected=configured.get(title);existing=cls.resolve(c,title)
            if selected is not None:
                course=c.execute('SELECT * FROM course WHERE id=?',(selected,)).fetchone() if isinstance(selected,str) else None
                if not course:
                    conflicts.append({'title':title,'reason':'unknown_explicit_course'});continue
                if existing and existing['id']!=selected:
                    conflicts.append({'title':title,'reason':'conflicting_course_alias'});continue
                mappings[title]=selected
                if register and not existing:
                    c.execute('INSERT INTO course_alias VALUES (?,?)',(normalized_name(title),selected))
            elif existing:
                mappings[title]=existing['id']
            elif register:
                course=cls._create(c,title);mappings[title]=course['id']
                if course['created']:created.append(course['id'])
        return {'course_mappings':mappings,'created':created,'conflicts':conflicts}

    def register_timetable(self,settings):
        """Register source-backed course names, never lessons or learning facts."""
        with self.personal.connection(write=True) as c:
            return self._timetable(c,settings,register=True)

    def timetable(self,settings):
        with self.personal.connection() as c:
            resolved=self._timetable(c,settings)
        return {**settings,'course_mappings':resolved['course_mappings'],'course_conflicts':resolved['conflicts']}

    def list(self, limit=100, offset=0):
        with self.personal.connection() as c:
            total = c.execute("SELECT count(*) FROM course").fetchone()[0]
            rows = [dict(r) for r in c.execute("SELECT * FROM course ORDER BY name,id LIMIT ? OFFSET ?", (limit,offset))]
            return {"items":rows,"total":total,"limit":limit,"offset":offset}

    def get(self, course_id):
        with self.personal.connection() as c:
            return course_public(c, course_id)

    def find(self, name):
        with self.personal.connection() as c:
            return self.resolve(c, name)

    @staticmethod
    def suggest(c, text):
        # Older schema fixtures deliberately use current readers before upgrading.
        # A v20 database with missing course tables still raises a storage error.
        if c.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0] < 20:
            return None
        text = normalized_name(text)
        matches = {}
        for row in c.execute("""SELECT a.normalized_alias,c.id,c.name FROM course_alias a
            JOIN course c ON c.id=a.course_id ORDER BY length(a.normalized_alias) DESC"""):
            alias = row["normalized_alias"]
            for match in re.finditer(re.escape(alias),text):
                # Explicit I/III is not the current II course just because its generic alias occurs.
                suffix = text[match.end():]
                if alias in {"高数","高等数学"}:
                    number = re.match(r"(iii|ii|iv|i|[123一二三])",suffix)
                    if number and number[0] not in {"ii","2","二"}:
                        continue
                if alias[-1:].isascii() and alias[-1:].isalnum() and suffix[:1].isascii() and suffix[:1].isalnum():
                    continue
                if alias[:1].isascii() and alias[:1].isalnum() and match.start() and text[match.start()-1].isascii() and text[match.start()-1].isalnum():
                    continue
                matches[row["id"]] = {"id":row["id"],"name":row["name"],"origin":"rule"}
        return next(iter(matches.values())) if len(matches)==1 else None

    @staticmethod
    def suggestions(c, snapshot, extraction):
        output = (extraction or {}).get("parsed_output") or {}
        events = output.get("events") or []
        source = snapshot["original_text"]
        result = {}
        if len(events) <= 1:
            suggestion = CourseStore.suggest(c,source)
            if suggestion:
                result[events[0]["id"] if events else "_default"] = suggestion
            return result
        quotes = {e["id"]:e["quote"] for e in output.get("evidence",[])}
        for event in events:
            ids = []
            for group in ("actions","deadlines","eligibility","materials","locations"):
                for fact in event.get(group,[]):
                    ids.extend(fact.get("evidence_ids") or [])
            if event.get("audience"):
                ids.extend(event["audience"].get("evidence_ids") or [])
            cited = "\n".join(quotes[i] for i in ids if i in quotes)
            suggestion = CourseStore.suggest(c,cited)
            if suggestion:
                result[event["id"]] = suggestion
        return result


"""Bounded requests and an evidence-first, fixed ten-part review schema."""
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ..personal_store import PersonalError

COURSE = 'course-math-ii'
TEXTBOOK = 'textbook-huang-v2-upper'
UNIT = 'unit-math-limits-pilot'
PROCESS_VERSION = 'school-pages-v1'
PROMPT_VERSION = 'lecture-review-learning-cycle-v6'
VISION_PROMPT_VERSION = 'lecture-page-vision-v1'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class LectureInput(Strict):
    course_id: str = Field(default=COURSE,min_length=1,max_length=256)
    lecture_date: str | None = None
    title: str = Field(min_length=1, max_length=200)
    notes_raw_item_id: str
    learning_unit_id: str | None = UNIT
    sequence_number: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode='after')
    def default_unit(self):
        if self.course_id!=COURSE and 'learning_unit_id' not in self.model_fields_set:
            self.learning_unit_id=None
        return self

    @field_validator('lecture_date')
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            if len(value) != 10 or date.fromisoformat(value).isoformat() != value:
                raise ValueError('日期请填写 YYYY-MM-DD。')
        return value


class VersionInput(Strict):
    expected_version: int = Field(ge=1)


class BookFileInput(VersionInput):
    textbook_id: str = Field(default=TEXTBOOK,min_length=1,max_length=256)
    raw_item_id: str
    file_role: Literal['full_textbook', 'chapter_scan'] = 'full_textbook'
    edition_confirmed: Literal[True]


class BookMetadataInput(Strict):
    course_id: str = Field(min_length=1,max_length=256)
    title: str = Field(min_length=1,max_length=200)
    edition: str | None = Field(default=None,max_length=120)
    editor: str | None = Field(default=None,max_length=120)
    publisher: str | None = Field(default=None,max_length=120)
    isbn: str | None = Field(default=None,max_length=40)
    role: Literal['primary','companion'] | None = None


class SectionInput(Strict):
    textbook_id: str = Field(default=TEXTBOOK,min_length=1,max_length=256)
    expected_version: int | None = Field(default=None,ge=1)
    title: str = Field(min_length=1, max_length=200)
    start_page: int = Field(ge=1, le=2000)
    end_page: int = Field(ge=1, le=2000)
    learning_unit_id: str | None = UNIT

    @model_validator(mode='after')
    def default_unit(self):
        if self.textbook_id!=TEXTBOOK and 'learning_unit_id' not in self.model_fields_set:
            self.learning_unit_id=None
        return self


class SectionLinks(VersionInput):
    section_ids: list[str] = Field(min_length=1, max_length=4)


class GenerateInput(VersionInput):
    request_id: str = Field(min_length=36, max_length=36)
    force: bool = False
    confirm_cost: Literal[True]
    use_vision: bool = True
    include_recent: bool = False
    repair_from: str | None = Field(default=None, min_length=36, max_length=36)


class VisionInput(Strict):
    request_id: str = Field(min_length=36, max_length=36)
    document_id: str
    pages: list[int] = Field(min_length=1, max_length=2)
    force: bool = False
    confirm_cost: Literal[True]


class FeedbackInput(Strict):
    action: Literal['accept', 'reject', 'comment']
    feedback: str = Field(default='', max_length=2000)
    expected_lecture_version: int = Field(ge=1)


class SourceTextChange(Strict):
    key: str = Field(min_length=2, max_length=180)
    segments: list[str] = Field(min_length=1, max_length=5)

    @field_validator('segments')
    @classmethod
    def bounded_segments(cls, values):
        if any(len(value)>600 for value in values):
            raise ValueError('单段校订文字最多 600 字。')
        return values


class SourceCorrectionInput(VersionInput):
    request_id: str = Field(min_length=36, max_length=36)
    draft_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    changes: list[SourceTextChange] = Field(min_length=1, max_length=8)
    reviewer: Literal['owner', 'codex_source_check'] = 'owner'


class PageAuditInput(Strict):
    expected_version: int = Field(ge=1)
    document_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    paragraphs: list[str] = Field(min_length=1,max_length=12)
    printed_page_label: str | None = Field(default=None,max_length=20)
    audit_render_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    reviewer: Literal['codex_visual_audit']
    uncertain_paragraphs: list[int] = Field(default_factory=list,max_length=12)
    uncertainties: list[str] = Field(default_factory=list,max_length=8)

    @field_validator('paragraphs')
    @classmethod
    def bounded_paragraphs(cls,values):
        if any(not v.strip() or len(v)>2500 for v in values) or sum(map(len,values))>16000:
            raise ValueError('逐页核查文字需为非空小段落，总量不超过 16000 字。')
        return values

    @model_validator(mode='after')
    def audit_uncertainties(self):
        if any(i<0 or i>=len(self.paragraphs) for i in self.uncertain_paragraphs) or any(len(t)>500 for t in self.uncertainties):
            raise ValueError('不确定段落索引或说明超出范围。')
        return self


class CitationCheck(Strict):
    kind: Literal['notes','definition','important_limit','method_conditions','textbook_supplement']
    source_id: str = Field(min_length=1,max_length=80)
    page: int = Field(ge=1,le=2000)
    correct: Literal[True]


class AcceptanceInput(Strict):
    expected_version: int = Field(ge=1)
    document_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    artifact_id: str
    retrieval_report_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    citation_checks: list[CitationCheck] = Field(min_length=5,max_length=12)
    reviewer: Literal['codex_visual_audit']


class Citation(Strict):
    source_id: str = Field(min_length=1, max_length=80)
    quote: str | None = Field(default=None, min_length=1, max_length=2500)


class Claim(Strict):
    text: str = Field(min_length=1, max_length=4000)
    provenance: Literal['notes', 'textbook', 'inference', 'uncertain']
    citations: list[Citation] = Field(min_length=1, max_length=4)


class Concept(Strict):
    name: str = Field(min_length=1, max_length=120)
    explanation: Claim


class MethodCard(Strict):
    signal: str = Field(min_length=1, max_length=400)
    method: str = Field(min_length=1, max_length=4000)
    conditions: str = Field(min_length=1, max_length=4000)
    common_misuse: str = Field(min_length=1, max_length=4000)
    citations: list[Citation] = Field(min_length=1, max_length=4)


class Supplement(Claim):
    notes_presence: Literal['not_found_in_provided_evidence']


class Mistakes(Strict):
    history_available: bool
    message: str = Field(max_length=400)
    items: list[Claim] = Field(max_length=5)


class SelfTest(Strict):
    question: str = Field(min_length=1, max_length=600)
    answer: str = Field(min_length=1, max_length=4000)
    citations: list[Citation] = Field(min_length=1, max_length=4)


class SuggestedAction(Strict):
    text: str = Field(min_length=1, max_length=300)
    duration_minutes: int = Field(ge=1, le=60)
    rationale: Claim


class PreviewCounts(Strict):
    total: int = Field(ge=0)
    correct: int = Field(ge=0)
    incorrect: int = Field(ge=0)
    unknown: int = Field(ge=0)


class PreviewWeakness(Strict):
    topic_id: str
    title: str
    incorrect: int = Field(ge=1)


class PreviewAttemptEvidence(Strict):
    attempt_id: str
    exercise_id: str
    topic_id: str
    question: str = Field(max_length=500)
    my_answer: str = Field(max_length=200)
    result: Literal['correct','incorrect','unknown']
    result_quote: str = Field(max_length=200)
    submitted_at: str | None
    raw_item_id: str
    snapshot_id: str
    lecture_link_basis: str


class BeforeLecturePerformance(Strict):
    lecture_id: str
    counts: PreviewCounts
    material_exposures: int = Field(ge=0)
    session_ids: list[str] = Field(max_length=10)
    weaknesses: list[PreviewWeakness] = Field(max_length=3)
    attempts: list[PreviewAttemptEvidence] = Field(max_length=5)
    timing_unknown: int = Field(ge=0)
    user_confirmed_before: int = Field(default=0,ge=0)
    notice: str = Field(max_length=400)


class LectureReview(Strict):
    schema_version: Literal['lecture-review-v1']
    core_concepts: list[Concept] = Field(min_length=3, max_length=7)
    notes_taught: list[Claim] = Field(min_length=1, max_length=12)
    must_master: list[Claim] = Field(min_length=1, max_length=12)
    method_cards: list[MethodCard] = Field(min_length=1, max_length=6)
    textbook_supplement: list[Supplement] = Field(max_length=8)
    understanding_gaps: list[Claim] = Field(max_length=5)
    connections: list[Claim] = Field(max_length=5)
    mistakes: Mistakes
    self_tests: list[SelfTest] = Field(min_length=3, max_length=5)
    suggested_actions: list[SuggestedAction] = Field(max_length=3)
    before_lecture: BeforeLecturePerformance | None = None


class GeneralLectureReview(LectureReview):
    """Sparse evidence must not be expanded to meet the Math pilot's counts."""
    schema_version: Literal['lecture-review-general-v1']
    source_scope: Literal['notes_only', 'notes_with_textbook']
    core_concepts: list[Concept] = Field(max_length=7)
    must_master: list[Claim] = Field(max_length=12)
    method_cards: list[MethodCard] = Field(max_length=6)
    self_tests: list[SelfTest] = Field(max_length=5)


class VisionPage(Strict):
    source_id: str
    transcript: str = Field(max_length=15000)
    clarity: Literal['clear', 'uncertain', 'unreadable']
    uncertainties: list[str] = Field(max_length=8)

    @model_validator(mode='after')
    def readable_has_text(self):
        if self.clarity == 'clear' and not self.transcript.strip():
            raise ValueError('清晰页面必须提供可核对的实际转写。')
        return self


class VisionOutput(Strict):
    pages: list[VisionPage] = Field(min_length=1, max_length=2)


def require_course(value,connection):
    from ..courses import course_public
    if not isinstance(value,str) or not value:
        raise PersonalError('请先选择已登记的课程。','invalid_course',400)
    return course_public(connection,value)

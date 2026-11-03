"""Server-selected review policies; a model cannot select its own safeguards."""
from dataclasses import dataclass
from .models import COURSE, PROMPT_VERSION, VISION_PROMPT_VERSION, LectureReview, GeneralLectureReview


@dataclass(frozen=True)
class ReviewProfile:
    key: str
    prompt: str
    evidence: str
    composition: str
    repair: str
    vision: str
    requires_textbook: bool

    @property
    def stage_prompts(self):
        return {'evidence': self.evidence, 'composition': self.composition, 'repair': self.repair}


MATH = ReviewProfile('math-ii', PROMPT_VERSION, 'lecture-evidence-selection-v1',
    'lecture-fact-composition-cycle-v6', 'lecture-targeted-repair-v4', VISION_PROMPT_VERSION, True)
GENERAL = ReviewProfile('general-course-v1', 'lecture-review-general-notes-v1',
    'lecture-general-evidence-selection-v1', 'lecture-general-fact-composition-v1',
    'lecture-targeted-repair-general-v1', 'lecture-general-page-vision-v1', False)


def profile_for(context):
    # Old retained Math contexts and protocol fixtures predate lecture metadata.
    # New contexts always carry the canonical server-owned course identity.
    course = context.get('lecture', {}).get('course_id')
    return GENERAL if course and course != COURSE else MATH


def review_model(context):
    return LectureReview if profile_for(context).requires_textbook else GeneralLectureReview


def source_scope(sources):
    return 'notes_with_textbook' if any(s['source_kind'] == 'textbook' for s in sources) else 'notes_only'

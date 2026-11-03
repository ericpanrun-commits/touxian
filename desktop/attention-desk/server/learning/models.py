from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class CapturedQuestion(Strict):
    question: str = Field(min_length=2, max_length=4000)
    options: dict[str, str] = Field(default_factory=dict, max_length=10)
    my_answer: str = Field(default='', max_length=2000)
    correct_answer: str = Field(default='', max_length=2000)
    result: Literal['correct', 'incorrect', 'unknown'] = 'unknown'
    result_quote: str = Field(default='', max_length=1000)
    explanation: str = Field(default='', max_length=3000)
    submitted_at: str | None = Field(default=None, max_length=50)
    topic: str = Field(default='', max_length=120)


class PreviewMaterial(Strict):
    title: str = Field(min_length=1,max_length=400)
    kind: Literal['reading','video','slides','other'] = 'other'
    completed: bool = False
    evidence_quote: str = Field(min_length=2,max_length=1000)
    available_at: str | None = Field(default=None,max_length=50)
    completed_at: str | None = Field(default=None,max_length=50)
    source_reference: str = Field(default='',max_length=500)


class CapturedPage(Strict):
    questions: list[CapturedQuestion] = Field(default_factory=list, max_length=30)
    preview_completed_quote: str = Field(default='', max_length=400)
    preview_materials: list[PreviewMaterial] = Field(default_factory=list,max_length=20)


class QuizQuestion(Strict):
    question: str = Field(min_length=6, max_length=1200)
    options: dict[Literal['A', 'B', 'C', 'D'], str] = Field(min_length=4, max_length=4)
    correct_key: Literal['A', 'B', 'C', 'D']
    explanation: str = Field(min_length=5, max_length=1600)
    source_ids: list[str] = Field(min_length=1, max_length=3)


class Quiz(Strict):
    questions: list[QuizQuestion] = Field(min_length=3, max_length=5)


CAUSES = Literal['概念没理解', '公式记错', '条件漏看', '方法选错', '计算错误', '审题错误', '粗心', '暂时无法判断']


class MistakeSuggestion(Strict):
    category: CAUSES
    reason: str = Field(min_length=2, max_length=1000)
    uncertain: bool = True
    evidence_quotes: list[str] = Field(default_factory=list,max_length=4)
    retest: QuizQuestion | None = None


class AnalysisAuthorizationInput(Strict):
    scope_version: Literal['official-deepseek-learning-text-v1']
    enabled: bool
    confirmation_source: Literal['explicit_user_chat','explicit_local_ui']
    confirmation_text: str = Field(min_length=2,max_length=1000)


class AnswerInput(Strict):
    request_id: str = Field(min_length=36, max_length=36)
    answers: dict[str, str] = Field(min_length=1, max_length=5)
    independent: bool


class HistoryInput(Strict):
    topic_ids: list[str] = Field(min_length=1, max_length=30)
    learned: bool = True


class CaptureInput(Strict):
    request_id: str = Field(min_length=36, max_length=36)
    text: str = Field(min_length=2, max_length=80000)
    title: str = Field(default='学习通学习记录', max_length=200)


class ExternalReviewInput(Strict):
    text: str = Field(min_length=2, max_length=30000)


class NotesVisionAuthorizationInput(Strict):
    scope_version: Literal['notes-rendered-pages-v1']
    enabled: bool
    confirmation_source: Literal['explicit_user_chat','explicit_local_ui']
    confirmation_text: str = Field(min_length=2,max_length=500)

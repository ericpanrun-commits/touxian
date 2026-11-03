"""Reuse a known failed composition only when every original source still matches."""
import json
from ..extraction_errors import ExtractionFailure
from .protocol import (EVIDENCE_PROMPT_VERSION, COMPOSITION_PROMPT_VERSION,
    ProtocolFailure, extract_evidence, compose_review, repair_roots)
from .profiles import profile_for


def saved_composition(personal, context, model):
    """Return one unspent repair candidate, never an unknown paid outcome.

    This does not schedule anything. A normal, cost-confirmed generation must
    reserve a new append-only version before using the candidate. That version
    records its parent before the single request, so a restart cannot spend the
    same original repair allowance again.
    """
    profile = profile_for(context)
    composition_versions = {'lecture-fact-composition-cycle-v5', COMPOSITION_PROMPT_VERSION} if profile.requires_textbook else {profile.composition}
    with personal.connection() as connection:
        rows = connection.execute("""SELECT a.* FROM review_artifact a
            JOIN attachment_job j ON j.id=a.job_id
            WHERE a.lecture_id=? AND a.content_json IS NULL AND a.status='needs_review'
            AND j.status='needs_review' ORDER BY a.version DESC LIMIT 10""", (context['lecture']['id'],)).fetchall()
        for row in rows:
            versions = json.loads(row['input_versions_json'])
            generation = versions.pop('generation', {})
            error = json.loads(row['error_json'] or '{}')
            if (error.get('code') != 'review_validation' or error.get('stage') != 'composition'
                    or generation.get('repair_used') is not False
                    or generation.get('model') != model or generation.get('context_id') != context['context_id']
                    or versions != context['input_versions']
                    or generation.get('stage_prompts', {}).get('evidence') != profile.evidence):
                continue
            consumed = connection.execute("""SELECT 1 FROM review_artifact
                WHERE json_extract(input_versions_json,'$.generation.recovered_from')=? LIMIT 1""", (row['id'],)).fetchone()
            if consumed:
                continue
            attempts = [dict(item) for item in connection.execute(
                'SELECT * FROM school_ai_attempt WHERE job_id=?', (row['job_id'],))]
            if any(item['status'] not in {'completed', 'failed'}
                    or item['prompt_version'].startswith('lecture-targeted-repair-') for item in attempts):
                continue
            recorded = generation.get('attempts', [])
            if len(recorded) != 2 or [(item.get('stage'), item.get('validation')) for item in recorded] != [
                    ('evidence', 'passed'), ('composition', 'failed')]:
                continue
            evidence_attempt = next((item for item in attempts if item['id'] == recorded[0].get('id')), None)
            composition_attempt = next((item for item in attempts if item['id'] == recorded[1].get('id')), None)
            if (not evidence_attempt or evidence_attempt['status'] != 'completed'
                    or evidence_attempt['prompt_version'] != profile.evidence
                    or not composition_attempt or composition_attempt['status'] != 'failed'
                    or composition_attempt['prompt_version'] not in composition_versions
                    or not evidence_attempt['raw_output'] or not composition_attempt['raw_output']):
                continue
            try:
                # Recompute facts from the actual current sources, not merely
                # from the saved model's statement that extraction succeeded.
                evidence = extract_evidence(evidence_attempt['raw_output'], context)
                if evidence != generation.get('evidence_extraction'):
                    continue
                compose_review(composition_attempt['raw_output'], evidence, context)
            except ProtocolFailure as failure:
                roots = repair_roots(failure.stage, failure.issues)
                if failure.stage != 'composition' or not roots or len(roots) > 8 or any(
                        issue['path'] == '/' for issue in failure.issues):
                    continue
                return {'artifact_id': row['id'], 'job_id': row['job_id'], 'evidence': evidence,
                    'evidence_attempt_id': evidence_attempt['id'], 'composition_attempt_id': composition_attempt['id'],
                    'original_raw': composition_attempt['raw_output'], 'failure': failure}
            except (ExtractionFailure, ValueError, KeyError, TypeError):
                continue
    return None

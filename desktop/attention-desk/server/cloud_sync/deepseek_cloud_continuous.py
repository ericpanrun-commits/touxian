"""Disabled-until-authorized future-notice source boundary.

The reviewed one-notice test document is deliberately not accepted here.
No process launch, grant creation or cloud request occurs on import.
"""
import json

from .deepseek_cloud_scope import CloudSource,ContinuousCloudGrant,ScopedCloudPublication,validate_future_scope
from .deepseek_cloud_evidence import ReviewedLocalEvidence
from .wechat_dispatch_authority import DispatchDenied


def continuous_approval(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate_approval_key')
            result[key]=value
        return result
    value=json.loads(raw,object_pairs_hook=unique)
    if (not isinstance(value,dict) or set(value)!={'version','mode','enabled','authorization_record','scope'}
            or type(value['version']) is not int or value['version']!=1 or value['mode']!='continuous'
            or value['enabled'] is not True or not isinstance(value['authorization_record'],str)
            or not value['authorization_record'].strip()):
        raise DispatchDenied('separate_continuous_cloud_approval_required')
    scope=dict(value['scope'])
    if type(scope.get('sources')) is not list:raise ValueError('five_explicit_sources_required')
    scope['sources']=tuple(CloudSource(**source) for source in scope['sources'])
    return ContinuousCloudGrant(**scope)


class ContinuousLocalEvidence(ReviewedLocalEvidence):
    def parse_approval(self,raw):return continuous_approval(raw)

    def descriptor(self,publication):
        if type(publication) is ContinuousCloudGrant:
            return publication
        if type(publication) is not ScopedCloudPublication:
            raise DispatchDenied('continuous_publication_required')
        return publication.scope

    def validate_preview(self,connection,publication,preview):
        validate_future_scope(connection,publication.scope,preview)
        return {**preview,'cloud_scope_binding':publication.binding()}

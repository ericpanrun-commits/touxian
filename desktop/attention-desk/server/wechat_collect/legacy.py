"""Inert compatibility marker. Does not import or initialise retired collectors."""
class DisabledLegacySource:
    def status(self):
        return {'ready':False,'enabled':False,'code':'legacy_source_disabled'}

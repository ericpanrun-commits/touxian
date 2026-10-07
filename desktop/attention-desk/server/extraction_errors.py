class ExtractionFailure(Exception):
    def __init__(self, code: str, message: str, *, raw_output=None, usage=None):
        super().__init__(message)
        self.code, self.raw_output, self.usage = code, raw_output, usage

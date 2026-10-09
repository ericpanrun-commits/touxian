class ExtractionFailure(Exception):
    def __init__(self, code: str, message: str, *, raw_output=None, usage=None, attempts=1, finish_reason=None):
        super().__init__(message)
        self.code, self.raw_output, self.usage = code, raw_output, usage
        self.attempts, self.finish_reason = attempts, finish_reason

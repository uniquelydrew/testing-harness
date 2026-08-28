"""Minimal synchronous ContextVar compatibility for the RHEL Python 3.6 target."""

import threading

_MISSING = object()


class _Token(object):
    def __init__(self, variable, old_value):
        self.variable = variable
        self.old_value = old_value


class ContextVar(object):
    def __init__(self, name, default=_MISSING):
        self.name = name
        self.default = default
        self._local = threading.local()

    def get(self, default=_MISSING):
        value = getattr(self._local, "value", _MISSING)
        if value is not _MISSING:
            return value
        if default is not _MISSING:
            return default
        if self.default is not _MISSING:
            return self.default
        raise LookupError(self.name)

    def set(self, value):
        old_value = getattr(self._local, "value", _MISSING)
        self._local.value = value
        return _Token(self, old_value)

    def reset(self, token):
        if token.variable is not self:
            raise ValueError("token was created by a different ContextVar")
        if token.old_value is _MISSING:
            try:
                del self._local.value
            except AttributeError:
                pass
        else:
            self._local.value = token.old_value

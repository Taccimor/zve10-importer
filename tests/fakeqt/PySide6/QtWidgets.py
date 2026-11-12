from ._fake import *
from . import _fake
def __getattr__(name):
    if name.startswith("__"): raise AttributeError(name)
    v = getattr(_fake, name, None)
    if v is None:
        v = _fake._Meta.__getattr__(_fake.D, name)
    return v

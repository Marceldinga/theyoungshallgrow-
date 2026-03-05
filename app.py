
# =========================
# SAFE CALL ADAPTER (signature-aware) ✅ FIXED
# =========================
def _call_with_supported_kwargs(fn, **kwargs):
    """
    Calls fn with only kwargs it supports.
    - If fn has **kwargs, passes everything.
    - Else filters to declared parameters only.

    CRITICAL:
    - We only catch signature-introspection errors.
    - We DO NOT catch exceptions raised by fn() itself,
      otherwise we would re-call with unfiltered kwargs and crash again.
    """
    # Unwrap decorators if any
    try:
        target = inspect.unwrap(fn)
    except Exception:
        target = fn

    # Only protect signature inspection
    try:
        sig = inspect.signature(target)
    except Exception:
        # Can't inspect -> call directly (best effort)
        return fn(**kwargs)

    params = sig.parameters

    # If function accepts **kwargs, pass everything
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(**kwargs)

    # Otherwise pass only supported keys
    supported = {k: v for k, v in kwargs.items() if k in params}
    return fn(**supported)

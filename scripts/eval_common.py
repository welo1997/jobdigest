"""Shared by the two matcher-quality harnesses. One definition, because both make the same
mistake otherwise.

`matcher_gate_eval.py` grades a model against a fixed answer key; `matcher_model_ab.py` compares
models on live shortlists. Both call the API in a loop over (profile x model x pass), and both had
the same two bugs on first writing:

1. **A failure that cannot be transient was retried once per remaining pass.** A bad key, a model
   id the account cannot reach, or an exhausted balance fails identically every time — 27 copies
   of one error above a summary nobody should trust.
2. **A summary computed from zero successful calls printed a grade.** "kept 0% of must-keeps
   (0 missed)" reads as a verdict on the model when it is a verdict on the credentials. A harness
   that cannot say "I measured nothing" is worse than no harness, because its output looks like
   evidence — the same failure shape as a source returning a round, plausible zero.

Both live here so a fix reaches both callers, and so neither grows its own copy of the rule.
"""

from __future__ import annotations

#: Statuses and SDK exception names that no later call will get past. Matched by status code and
#: class *name* rather than an `isinstance` chain against `anthropic.*`, so adding a provider or
#: upgrading the SDK does not silently turn a terminal failure back into a retried one.
_TERMINAL_STATUSES = (401, 403, 404)
_TERMINAL_NAMES = frozenset({"AuthenticationError", "PermissionDeniedError", "NotFoundError"})


def is_terminal_error(exc: BaseException) -> bool:
    """True when every remaining call would fail the same way, so the run should stop now."""
    return (getattr(exc, "status_code", None) in _TERMINAL_STATUSES
            or type(exc).__name__ in _TERMINAL_NAMES)


def abort_message(exc: BaseException) -> str:
    return (f"Aborting: {type(exc).__name__} is not transient — every remaining pass would "
            f"fail the same way.\n  {exc}")


def no_data_line(model: str, expected: int) -> str:
    """What to print for a model that returned nothing. Deliberately not a score."""
    return (f"{model}: NO DATA — 0 of {expected} pass(es) returned anything to grade. "
            f"This is not a score of zero; nothing was measured.")

"""Stable command exit codes for sins."""

OK = 0
FINDINGS = 1
ERROR = 2
LOOP_STOP = 3


def select_exit_code(*, error: bool = False, loop_stop: bool = False, findings: bool = False) -> int:
    """Apply the documented 2 > 3 > 1 > 0 precedence."""
    if error:
        return ERROR
    if loop_stop:
        return LOOP_STOP
    return FINDINGS if findings else OK

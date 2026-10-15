"""Finite JSON values at application boundaries."""

import json
import math

from .errors import UtilityError


def validate_arguments(arguments: dict) -> dict:
    """Copy a finite JSON object without coercing keys or Python objects."""
    def check(value):
        if value is None or type(value) in (str, bool, int):
            return
        if type(value) is float and math.isfinite(value):
            return
        if isinstance(value, list):
            for child in value:
                check(child)
            return
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            for child in value.values():
                check(child)
            return
        raise UtilityError("Arguments must contain finite JSON values and string object keys.")

    if not isinstance(arguments, dict):
        raise UtilityError("Arguments must be a JSON object.")
    try:
        check(arguments)
        return json.loads(json.dumps(arguments, allow_nan=False))
    except (ValueError, TypeError, OverflowError, RecursionError) as error:
        raise UtilityError("Arguments must contain finite, non-circular JSON values.") from error

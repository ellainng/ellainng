"""Expected application failures safe to show at the terminal boundary."""


class UtilityError(Exception):
    """A utility cannot be registered, inspected, or executed as requested."""

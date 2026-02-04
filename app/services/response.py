def list_response(items: list, limit: int, offset: int, total: int | None = None) -> dict:
    """Build a standardized list response dictionary.

    Args:
        items: List of items in this page
        limit: Maximum items per page
        offset: Number of items skipped
        total: Total count of all matching items (if None, uses len(items))

    Returns:
        Dictionary with items, total, limit, offset
    """
    # Use provided total or fall back to len(items)
    # Note: When total is not provided, this only returns count for current page
    # which may not be accurate for pagination. Services should provide total.
    actual_total = total if total is not None else len(items)
    return {"items": items, "total": actual_total, "limit": limit, "offset": offset}


from typing import Callable, ClassVar, Any


class ListResponseMixin:
    """Mixin providing standardized list response methods.

    Services using this mixin should implement:
    - list(db, ..., limit, offset) -> list: Returns paginated items
    - count(db, ...) -> int: Returns total count (optional but recommended)
    """

    list: ClassVar[Callable[..., list[Any]]]
    count: ClassVar[Callable[..., int]]

    @classmethod
    def list_response(cls, db, *args, **kwargs):
        """Build a list response with pagination metadata.

        If the service has a `count` method, it will be used to get the total.
        Otherwise, falls back to len(items) which may be inaccurate for pagination.
        """
        if "limit" in kwargs and "offset" in kwargs:
            limit = kwargs["limit"]
            offset = kwargs["offset"]
            items = cls.list(db, *args, **kwargs)
        else:
            if len(args) < 2:
                raise ValueError("limit and offset are required for list responses")
            *list_args, limit, offset = args
            items = cls.list(db, *list_args, limit=limit, offset=offset, **kwargs)

        # Try to get accurate total count if service provides count method
        total = None
        if hasattr(cls, "count"):
            try:
                # Build count kwargs without limit/offset
                count_kwargs = {k: v for k, v in kwargs.items() if k not in ("limit", "offset")}
                total = cls.count(db, *args[:-2] if not kwargs.get("limit") else args, **count_kwargs)
            except Exception:
                # Fall back to len(items) if count fails
                pass

        return list_response(items, limit, offset, total)

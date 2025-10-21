from typing import TypeVar, Generic, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime, timezone

T = TypeVar('T')

class APIResponseSchema(BaseModel, Generic[T]):
    """A generic Pydantic schema for standard API responses.

    This model is used as a `response_model` in FastAPI endpoints to ensure
    all successful responses follow a consistent structure. It is generic,
    allowing the `data` field to be typed with any Pydantic model or standard type.

    Attributes:
        success: A boolean indicating if the request was successful.
        code: A string code for the response status (e.g., "success").
        message: A human-readable message.
        timestamp: The UTC timestamp of the response in ISO 8601 format.
        data: The actual payload of the response, with a type specified
              by the generic TypeVar `T`.
    """
    success: bool = True
    code: str = "success"
    message: str = "Success"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    data: Optional[T] = None
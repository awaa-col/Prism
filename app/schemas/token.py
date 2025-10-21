from pydantic import BaseModel

class TokenResponse(BaseModel):
    """
    Standard token response containing both access and refresh tokens.
    """
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int

class RefreshTokenRequest(BaseModel):
    """
    Request model for refreshing an access token.
    """
    refresh_token: str

class LogoutRequest(BaseModel):
    """
    Request model for logging out.
    """
    refresh_token: str

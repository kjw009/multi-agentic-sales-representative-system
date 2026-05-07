import uuid

from pydantic import BaseModel, EmailStr

# --- AUTHENTICATION SCHEMAS ---
class SignupRequest(BaseModel):
    """
    Represents the data required to create a new user account.
    """
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    """
    Represents the data required to log in (authenticate) an existing user.
    """
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """
    Represents the JWT token issued after successful login.
    """
    access_token: str
    token_type: str = "bearer"
    seller_id: uuid.UUID

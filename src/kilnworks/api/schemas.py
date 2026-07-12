from pydantic import BaseModel, Field


class TokenRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    limit: int = Field(8, ge=1, le=50)


class DocumentInfo(BaseModel):
    id: str
    source_uri: str
    title: str
    status: str
    error: str | None = None


class JobInfo(BaseModel):
    id: int
    kind: str
    status: str
    attempts: int
    error: str | None = None

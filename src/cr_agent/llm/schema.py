"""Pydantic models for LLM structured output."""

from pydantic import BaseModel, Field


class ReviewFinding(BaseModel):
    """A single code review finding — LLM must output this exact format."""

    file: str = Field(description="File path relative to repo root")
    line_start: int = Field(description="Start line number")
    line_end: int = Field(description="End line number")
    severity: str = Field(description="critical / warning / suggestion / nitpick")
    category: str = Field(description="security / logic / performance / style")
    title: str = Field(description="One-line summary of the issue")
    description: str = Field(description="Detailed explanation")
    suggestion: str | None = Field(default=None, description="Fix suggestion (optional code)")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0.0-1.0")


class ReviewOutput(BaseModel):
    """Complete output from a single review agent."""

    findings: list[ReviewFinding] = Field(default_factory=list)
    summary: str = Field(default="", description="Overall assessment for this dimension")

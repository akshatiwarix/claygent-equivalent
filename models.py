"""Data shapes for the account brief.

Pydantic does two jobs here:
1. model_json_schema() gives us the JSON schema we send to the LLM (strict mode).
2. Validation checks the LLM's reply. Strict mode can't enforce things like
   "exactly 3 pains" or "under 25 words" on every provider, so we check here.
"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class _Strict(BaseModel):
    # Strict JSON schema mode requires additionalProperties: false.
    model_config = ConfigDict(extra="forbid")


class Snapshot(_Strict):
    company_name: str
    what_they_sell: str
    who_they_sell_to: str
    size_and_stage: str  # e.g. "~1,000 employees, Series C"


class Trigger(_Strict):
    type: Literal["funding", "launch", "leadership_hire", "hiring_trend", "other"]
    summary: str
    date: str  # YYYY-MM-DD
    source_url: str

    @field_validator("date")
    @classmethod
    def _valid_date(cls, v: str) -> str:
        date.fromisoformat(v)  # raises ValueError if not YYYY-MM-DD
        return v


class Pain(_Strict):
    pain: str
    trigger_index: int  # position of the related trigger in AccountBrief.triggers
    reasoning: str


class TalkTrack(_Strict):
    pain_index: int  # position of the related pain in AccountBrief.pains
    opener: str

    @field_validator("opener")
    @classmethod
    def _under_25_words(cls, v: str) -> str:
        n = len(v.split())
        if n >= 25:
            raise ValueError(f"opener is {n} words, must be under 25")
        return v


class AccountBrief(_Strict):
    snapshot: Snapshot
    triggers: list[Trigger]
    pains: list[Pain]
    talk_tracks: list[TalkTrack]
    confidence_notes: list[str]

    @model_validator(mode="after")
    def _check_structure(self) -> "AccountBrief":
        if len(self.pains) != 3:
            raise ValueError(f"need exactly 3 pains, got {len(self.pains)}")
        if len(self.talk_tracks) != 3:
            raise ValueError(f"need exactly 3 talk tracks, got {len(self.talk_tracks)}")
        for p in self.pains:
            if not 0 <= p.trigger_index < len(self.triggers):
                raise ValueError(f"pain points at missing trigger {p.trigger_index}")
        for t in self.talk_tracks:
            if not 0 <= t.pain_index < len(self.pains):
                raise ValueError(f"talk track points at missing pain {t.pain_index}")
        return self


def brief_json_schema() -> dict:
    """Schema for response_format. Pydantic already emits additionalProperties: false."""
    return AccountBrief.model_json_schema()

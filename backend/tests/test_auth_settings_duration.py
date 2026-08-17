"""Fork: free-form session duration instead of the 900/1800 presets."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.auth import UserUpdateRequest


def test_any_duration_in_range_is_accepted():
    for seconds in (300, 900, 1800, 3600, 14400):
        assert (
            UserUpdateRequest(
                conversation_max_duration=seconds
            ).conversation_max_duration
            == seconds
        )


def test_out_of_range_durations_are_rejected():
    for seconds in (0, 60, 299, 14401, 999999):
        with pytest.raises(ValidationError):
            UserUpdateRequest(conversation_max_duration=seconds)

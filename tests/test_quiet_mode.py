import pytest

from parivox_agents.capabilities import is_quiet_request, match_wake_phrase


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Okay Scribe", ""),
        ("OK, Scribe. What did we decide?", "What did we decide?"),
        ("okay scrib can you summarize", "can you summarize"),
        ("so okay, Room Scribe, you there?", "you there?"),
        ("Scribe, what's next?", None),
        ("okay everyone let's start", None),
    ],
)
def test_wake_phrase(text, expected):
    assert match_wake_phrase(text, ["Scribe", "Room Scribe"]) == expected


def test_wake_phrase_needs_a_name():
    assert match_wake_phrase("okay", []) is None


@pytest.mark.parametrize(
    "text",
    ["Can you be quiet for a bit", "give us a minute", "hold on", "please stop talking", "mute yourself"],
)
def test_quiet_requests(text):
    assert is_quiet_request(text)


@pytest.mark.parametrize("text", ["What's the quiet period for the launch?", "Summarize so far"])
def test_not_quiet_requests(text):
    assert not is_quiet_request(text)

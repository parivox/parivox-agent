import json

from parivox_agents import ParivoxJob, PresenceState
from parivox_agents.protocol import build_attributes, is_parivox_service, parse_payload


def test_attributes_identify_the_agent(metadata):
    attrs = build_attributes(ParivoxJob.from_metadata(metadata), PresenceState())
    assert attrs["nexus.type"] == "ai_avatar"
    assert attrs["nexus.agent_id"] == "agent_scribe"
    assert attrs["nexus.instance_id"] == "inst_1"
    assert attrs["nexus.spoken_name"] == "Scribe"
    assert attrs["nexus.avatar_provider"] == "audio_only"
    assert attrs["nexus.visual_representation_type"] == "livekit_audio_visualizer"
    assert attrs["nexus.control_ready"] == "false"
    assert attrs["nexus.hushed"] == "false"
    assert json.loads(attrs["nexus.skills"]) == ["escalate-to-human"]
    assert json.loads(attrs["nexus.suggested_responses"]) == []
    assert all(isinstance(value, str) for value in attrs.values())


def test_attributes_follow_state(metadata):
    state = PresenceState(hushed=True, quiet_mode=True, control_ready=True, suggested_responses=list("abcdefghij"))
    attrs = build_attributes(ParivoxJob.from_metadata(metadata), state)
    assert attrs["nexus.hushed"] == "true"
    assert attrs["nexus.quiet_mode"] == "true"
    assert attrs["nexus.control_ready"] == "true"
    assert len(json.loads(attrs["nexus.suggested_responses"])) == 8


def test_local_dev_agent_still_has_an_id():
    attrs = build_attributes(ParivoxJob.from_metadata(None), PresenceState())
    assert attrs["nexus.agent_id"] == "local-dev"


def test_parse_payload_tolerates_garbage():
    assert parse_payload('{"text": "hi"}') == {"text": "hi"}
    assert parse_payload("[]") == {}
    assert parse_payload("{") == {}
    assert parse_payload(None) == {}


def test_service_participants():
    assert is_parivox_service({"nexus.type": "translation_service"})
    assert is_parivox_service({"nexus.type": "ai_avatar"})
    assert not is_parivox_service({})
    assert not is_parivox_service(None)

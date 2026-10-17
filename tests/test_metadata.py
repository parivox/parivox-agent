from parivox_agents import ParivoxJob


def test_parses_a_parivox_dispatch(metadata):
    job = ParivoxJob.from_metadata(__import__("json").dumps(metadata))
    assert job.is_parivox
    assert job.agent_id == "agent_scribe"
    assert job.name == "Scribe"
    assert job.api_url == "https://parivox.example"
    assert job.has_callback_api
    assert job.persona.system_prompt == "Be brief."
    assert job.persona.has_skill("escalate-to-human")
    assert job.persona.config_for("escalate-to-human")["timeToWaitForHuman"] == 45
    assert job.persona.keyterms == ("Parivox",)
    assert job.persona.visual_representation_type == "livekit_audio_visualizer"
    assert job.handoff_contact.email == "owner@example.com"
    assert job.room_id == "room123"


def test_empty_or_broken_metadata_still_runs():
    for raw in (None, "", "not json", "[1, 2]", {"resolvedPersona": "nope"}):
        job = ParivoxJob.from_metadata(raw, room_name="playground")
        assert not job.is_parivox
        assert job.room_name == "playground"
        assert job.name == "Assistant"
        assert not job.has_callback_api


def test_explicit_handoff_contact_wins(metadata):
    metadata["handoffContact"] = {"phone": "+15550100"}
    job = ParivoxJob.from_metadata(metadata)
    assert job.handoff_contact.phone == "+15550100"
    assert job.handoff_contact.email == ""


def test_room_id_for_each_room_kind():
    assert ParivoxJob.from_metadata({"roomName": "pm-meet9"}).room_id == "meet9"
    assert ParivoxJob.from_metadata({"roomName": "pr-room1"}).room_id == "room1"
    assert ParivoxJob.from_metadata({"roomName": "adhoc"}).room_id == "adhoc"


def test_unknown_visual_type_falls_back_to_image():
    job = ParivoxJob.from_metadata({"resolvedPersona": {"visual_representation_type": "hologram"}})
    assert job.persona.visual_representation_type == "image"


def test_start_flags_accept_strings():
    job = ParivoxJob.from_metadata({"startHushed": "true", "startQuiet": True, "startVideoHidden": "no"})
    assert job.start_hushed and job.start_quiet and not job.start_video_hidden

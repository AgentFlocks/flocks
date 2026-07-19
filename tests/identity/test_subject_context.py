from flocks.identity import Entry, Subject


def test_subject_preserves_opaque_transport_attributes() -> None:
    subject = Subject(
        subject_id="channel-user-1",
        subject_type="channel_user",
        attributes={"evidence": {"provider": "feishu"}},
    )

    assert subject.attributes["evidence"]["provider"] == "feishu"
    assert subject.display_name is None
    assert subject.model_dump() == {
        "subject_id": "channel-user-1",
        "subject_type": "channel_user",
        "display_name": None,
        "attributes": {"evidence": {"provider": "feishu"}},
    }
    assert Entry.CHANNEL.value == "channel"

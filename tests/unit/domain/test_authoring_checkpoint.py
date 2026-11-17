"""A draft goes through the checkpointer's serializer and comes back whole (design §8.3):
LangGraph stores a model's field names, and a probe's capture is written `from` in YAML."""

import warnings

from hw_checker.bootstrap import checkpoint_serializer
from hw_checker.domain.authoring import CaptureSpec, DraftedRequirement
from tests.unit.services.authoring_data import capture_reply


def test_a_capture_accepts_from_and_its_field_name_and_still_writes_from() -> None:
    by_alias = CaptureSpec.model_validate({"name": "user_id", "from": "body:id"})
    by_name = CaptureSpec.model_validate({"name": "user_id", "source": "body:id"})

    assert by_alias == by_name
    assert by_name.model_dump(by_alias=True) == {"name": "user_id", "from": "body:id"}


def test_a_reply_with_a_capture_comes_back_from_the_checkpoint_serializer_equal() -> None:
    reply = capture_reply()
    serializer = checkpoint_serializer()

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        loaded = serializer.loads_typed(serializer.dumps_typed(reply))
        loaded.model_dump()

    assert loaded == reply
    assert all(isinstance(item, DraftedRequirement) for item in loaded.requirements)

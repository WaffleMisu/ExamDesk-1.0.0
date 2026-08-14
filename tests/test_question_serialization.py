from test_question_repository import make_question

from examdesk.questions import question_from_payload, question_payload_hash, question_to_payload


def test_question_payload_round_trip_and_stable_hash() -> None:
    question = make_question()
    payload = question_to_payload(question)
    restored = question_from_payload(payload)

    assert question_to_payload(restored) == payload
    assert question_payload_hash(payload) == question_payload_hash(question_to_payload(restored))


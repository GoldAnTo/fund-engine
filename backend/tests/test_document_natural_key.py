from __future__ import annotations


def test_documents_without_a_natural_key_store_null_and_do_not_collide(
    document_service,
) -> None:
    first = document_service.freeze(
        raw=b"first unlabelled snapshot",
        source_url="event://pasted-news",
    )
    second = document_service.freeze(
        raw=b"second unlabelled snapshot",
        source_url="event://pasted-news",
    )

    assert first.id != second.id
    assert first.natural_key is None
    assert second.natural_key is None

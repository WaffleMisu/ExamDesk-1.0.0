from examdesk import edition


def test_candidate_build_disables_admin(monkeypatch) -> None:
    monkeypatch.setattr(edition, "ADMIN_ENABLED", False)

    assert edition.admin_enabled() is False


def test_source_build_defaults_to_admin(monkeypatch) -> None:
    monkeypatch.setattr(edition, "ADMIN_ENABLED", True)

    assert edition.admin_enabled() is True

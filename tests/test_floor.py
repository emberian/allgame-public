from resident import floor


def test_one_holder_at_a_time():
    got, _ = floor.acquire("lounge", "t1", "Juniper", addressed=True)
    assert got
    got, holder = floor.acquire("lounge", "t1", "Rowan", addressed=True)
    assert not got and holder["who"] == "Juniper"
    floor.release("lounge", "t1", "Juniper")
    got, _ = floor.acquire("lounge", "t1", "Rowan", addressed=True)
    assert got
    floor.release("lounge", "t1", "Rowan")


def test_renew_only_while_held():
    floor.acquire("lounge", "t2", "Juniper", addressed=True)
    assert floor.renew("lounge", "t2", "Juniper")
    assert not floor.renew("lounge", "t2", "Rowan")
    floor.release("lounge", "t2", "Juniper")
    assert not floor.renew("lounge", "t2", "Juniper")


def test_release_is_safe_when_not_holding():
    floor.release("lounge", "t3", "Nobody")
    assert floor.holder("lounge", "t3") is None


def test_names_fold_case_and_diacritics():
    assert floor.same("Zoë", "zoe")
    assert not floor.same("Zoë", "Juniper")


def test_backoff_grows_with_share():
    assert floor.backoff_for(0.0) <= floor.backoff_for(0.5) <= floor.backoff_for(1.0)

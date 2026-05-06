from flocks.browser import daemon


def test_is_real_page_filters_edge_internal_pages() -> None:
    assert not daemon.is_real_page({"type": "page", "url": "edge://inspect/#remote-debugging"})


def test_is_real_page_accepts_normal_https_pages() -> None:
    assert daemon.is_real_page({"type": "page", "url": "https://example.com"})

from app.inbox.email_render import TRUCK_BANNER_URL, EmailDesignOut, render_email


def _html(**kw) -> str:
    html, _ = render_email(body_text="Hello", subject="s", design=EmailDesignOut(**kw))
    return html


def test_show_truck_true_includes_banner_after_logo_before_body():
    html = _html(show_truck=True)
    assert f'<img src="{TRUCK_BANNER_URL}"' in html
    assert html.index("truck-banner") < html.index("Hello")


def test_show_truck_false_or_plain_omits_banner():
    assert "truck-banner" not in _html(show_truck=False)
    assert "truck-banner" not in _html(show_truck=True, layout="plain")

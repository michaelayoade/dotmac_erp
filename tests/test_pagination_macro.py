from app.templates import templates


def _render_pagination(**kwargs: object) -> str:
    template = templates.env.from_string(
        '{% from "components/macros.html" import pagination %}{{ pagination(**kwargs) }}'
    )
    return template.render(kwargs=kwargs)


def test_pagination_links_preserve_limit_on_navigation() -> None:
    html = _render_pagination(page=2, total_pages=3, total_count=80, limit=50)

    assert 'href="?page=1&amp;limit=50"' in html
    assert 'href="?page=3&amp;limit=50"' in html

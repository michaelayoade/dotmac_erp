"""Brand home navigation is independent of module navigation and rail state.

Render the real header markup with inert icon macros. These tests do not boot
ERP or require the private UI package.
"""

from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

MODULES = [
    ("Inventory", "/inventory"),
    ("Finance", "/finance/dashboard"),
    ("People", "/people"),
    ("Training", "/people/training"),
    ("Discipline", "/people/hr/discipline"),
    ("Self Service", "/people/self"),
    ("Operations", "/fleet"),
    ("Procurement", "/procurement"),
    ("Expenses", "/expense"),
    ("Asset Management", "/fixed-assets"),
    ("Coach", "/coach/"),
    ("Public Sector", "/public-sector/"),
]


class Elements(HTMLParser):
    """Inspect link ownership without adding an HTML-parser dependency."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        element = {
            "tag": tag,
            "attrs": dict(attrs),
            "parents": list(self.stack),
            "text": "",
        }
        self.elements.append(element)
        if tag not in {"img", "br", "hr", "input", "meta", "link", "source"}:
            self.stack.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        for element in self.stack:
            element["text"] += data

    def testid(self, value):
        matches = [e for e in self.elements if e["attrs"].get("data-testid") == value]
        assert len(matches) == 1
        return matches[0]


def header_environment():
    return Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "components/macros.html": (
                            "{% macro icon_svg(name, classes='') %}"
                            '<svg aria-hidden="true" class="{{ classes }}"></svg>'
                            "{% endmacro %}"
                        )
                    }
                ),
                FileSystemLoader(str(TEMPLATES)),
            ]
        ),
        autoescape=True,
    )


def render_header(kind="shared", label="Inventory", href="/inventory", logo="single"):
    context = {
        "brand_name": "Dotmac",
        "brand_mark": "DM",
        "brand_logo_url": "/logo.svg" if logo != "none" else None,
        "brand_logo_dark_url": "/logo-dark.svg" if logo == "dual" else None,
        "brand_href": href,
        "module_label": label,
        "module_logo_bg": "from-emerald-500 to-teal-600",
        "module_logo_shadow": "shadow-emerald-500/25",
        "module_pill_class": "border-emerald-200 bg-emerald-50 text-emerald-700",
        "brand": SimpleNamespace(
            name="Dotmac", mark="DM", logo_url="/logo.svg" if logo != "none" else None
        ),
    }
    env = header_environment()
    if kind == "shared":
        return env.get_template("partials/_sidebar_header.html").render(**context)
    source = (TEMPLATES / "admin/base_admin.html").read_text()
    start = source.index("<!-- Logo and module title")
    end = source.index("<!-- Navigation -->", start)
    template = '{% from "components/macros.html" import icon_svg %}' + source[start:end]
    return env.from_string(template).render(**context)


def assert_independent_home_link(html, module_href, module_label):
    parsed = Elements(html)
    home = parsed.testid("sidebar-home-link")
    module = parsed.testid("sidebar-module-link")
    assert home["tag"] == module["tag"] == "a"
    assert home["attrs"]["href"] == "/"
    assert home["attrs"]["hx-boost"] == "false"
    assert "data-no-context" in home["attrs"]
    assert "home" in home["attrs"]["aria-label"]
    for element in [home, *home["parents"]]:
        assert not {"x-show", "x-if", "x-cloak"}.intersection(element["attrs"])
    assert ":href" not in home["attrs"]
    assert "@click.prevent" not in home["attrs"]
    assert not home["attrs"].get("@click.stop")
    assert module["attrs"]["href"] == module_href
    assert module["text"].strip() == module_label
    assert module["attrs"]["x-show"] == "!sidebarCollapsed"
    assert all(parent is not home for parent in module["parents"])
    for element in parsed.elements:
        if element["tag"] == "img":
            assert any(parent is home for parent in element["parents"])
        if element["tag"] in {"button", "a"} and element is not home:
            assert all(parent is not home for parent in element["parents"])
    return parsed


@pytest.mark.parametrize(("label", "href"), MODULES)
@pytest.mark.parametrize("logo", ["none", "single", "dual"])
def test_shared_brand_home_and_module_title_are_independent(label, href, logo):
    assert_independent_home_link(
        render_header(label=label, href=href, logo=logo), href, label
    )


@pytest.mark.parametrize("logo", ["none", "single"])
def test_admin_brand_home_preserves_admin_title_target(logo):
    assert_independent_home_link(render_header("admin", logo=logo), "/admin", "Admin")


@pytest.mark.parametrize("kind", ["shared", "admin"])
def test_expansion_is_a_separate_named_button_without_a_logo(kind):
    parsed = Elements(render_header(kind))
    buttons = [element for element in parsed.elements if element["tag"] == "button"]
    for name in ["Expand sidebar", "Collapse sidebar"]:
        matches = [e for e in buttons if e["attrs"].get("aria-label") == name]
        assert len(matches) == 1
        button = matches[0]
        assert button["attrs"]["type"] == "button"
        assert "href" not in button["attrs"]
        assert "location" not in button["attrs"]["@click"]
        assert any(
            e["tag"] == "svg" and any(p is button for p in e["parents"])
            for e in parsed.elements
        )
    expand = next(e for e in buttons if e["attrs"]["aria-label"] == "Expand sidebar")
    assert expand["attrs"]["x-show"] == "isDesktop && sidebarCollapsed"
    home = parsed.testid("sidebar-home-link")
    # The expand control is below, not alongside, the 40px logo in a 64px rail.
    assert expand["parents"][-1] is home["parents"][-2]


def test_mobile_close_control_is_preserved():
    parsed = Elements(render_header())
    close = next(
        e for e in parsed.elements if e["attrs"].get("aria-label") == "Close sidebar"
    )
    assert close["attrs"]["x-show"] == "!isDesktop"
    assert close["attrs"]["@click"] == "closeSidebar()"


def test_dual_logo_fallbacks_share_their_image_theme_wrapper():
    parsed = Elements(render_header(logo="dual"))
    images = [e for e in parsed.elements if e["tag"] == "img"]
    assert len(images) == 2
    assert images[0]["parents"][-1]["attrs"]["class"] == "dark:hidden"
    assert images[1]["parents"][-1]["attrs"]["class"] == "hidden dark:flex"
    for image in images:
        wrapper = image["parents"][-1]
        fallbacks = [
            e
            for e in parsed.elements
            if e["tag"] == "span" and e["parents"] and e["parents"][-1] is wrapper
        ]
        assert len(fallbacks) == 1
        assert "hidden" in fallbacks[0]["attrs"]["class"].split()
        assert "dark:flex" not in fallbacks[0]["attrs"]["class"].split()


def test_brand_text_is_escaped_without_changing_the_home_destination():
    env = header_environment()
    html = env.get_template("partials/_sidebar_header.html").render(
        brand_name='<img src=x onerror="alert(1)">',
        brand_mark="<script>bad()</script>",
        brand_logo_url=None,
        brand_logo_dark_url=None,
        brand_href="/inventory",
        module_label="Inventory",
        module_logo_bg="",
        module_logo_shadow="",
        module_pill_class="",
    )
    assert "<script>" not in html
    assert "<img src=x" not in html
    assert Elements(html).testid("sidebar-home-link")["attrs"]["href"] == "/"


def test_sidebar_shells_do_not_bypass_the_shared_header():
    # New module shells must use the fixed shared header. Admin is the one
    # intentional exception and has the same link contract tested above.
    for path in TEMPLATES.rglob("*.html"):
        source = path.read_text()
        if "<aside" not in source or "app-sidebar" not in source:
            continue
        if path.relative_to(TEMPLATES).as_posix() == "admin/base_admin.html":
            continue
        assert 'include "partials/_sidebar_header.html"' in source, str(path)

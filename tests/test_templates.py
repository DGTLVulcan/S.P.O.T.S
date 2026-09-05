import glob
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEMPLATES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "spots", "web", "templates")


def pages():
    for path in sorted(glob.glob(os.path.join(TEMPLATES, "*.html"))):
        name = os.path.basename(path)
        if name.startswith("_"):        # partials, not pages
            continue
        with open(path, encoding="utf-8") as fh:
            yield name, fh.read()


class MenuTests(unittest.TestCase):
    """The menu is the only way between pages, so every page needs it."""

    def test_every_page_includes_the_menu(self):
        for name, html in pages():
            self.assertIn("_menu.html", html, name)
            self.assertIn("menu.js", html, name)

    def test_every_page_says_which_item_is_current(self):
        for name, html in pages():
            self.assertRegex(html, r"\{%\s*set active = 'spots\.\w+'\s*%\}", name)

    def test_the_menu_comes_before_the_title(self):
        for name, html in pages():
            self.assertLess(html.index("_menu.html"), html.index("<h1>"), name)

    def test_no_page_keeps_its_own_navigation_links(self):
        # Anything that navigates belongs in the menu; the bar is for
        # controls that act on the page you are looking at.
        for name, html in pages():
            self.assertNotRegex(html, r'<a class="icon-btn"', name)

    def test_the_bar_keeps_only_buttons(self):
        for name, html in pages():
            bar = re.search(r'<div class="icon-nav">(.*?)</div>', html, re.S)
            if not bar:
                continue
            self.assertNotIn("<a ", bar.group(1), name)

    def test_the_menu_lists_every_page_endpoint(self):
        with open(os.path.join(TEMPLATES, "_menu.html"), encoding="utf-8") as fh:
            menu = fh.read()
        listed = set(re.findall(r'\("(spots\.\w+)"', menu))
        used = {re.search(r"'(spots\.\w+)'", html).group(1)
                for _, html in pages()}
        self.assertTrue(used <= listed, f"pages not reachable from the menu: {used - listed}")


class RangeStatusTests(unittest.TestCase):
    """A safety indicator is worthless if it is missing from a page."""

    def test_every_page_shows_the_banner_and_its_button(self):
        for name, html in pages():
            self.assertIn("_range_banner.html", html, name)
            self.assertIn("_range_toggle.html", html, name)
            self.assertIn("range_status.js", html, name)

    def test_the_banner_sits_between_the_bar_and_the_content(self):
        for name, html in pages():
            self.assertLess(html.index("</header>"), html.index("_range_banner.html"), name)
            body = html.index("<main") if "<main" in html else len(html)
            self.assertLess(html.index("_range_banner.html"), body, name)


class SettingsPanelTests(unittest.TestCase):
    """A panel with fields in it must offer a way to save them."""

    def test_every_panel_with_inputs_is_listed_as_a_form_panel(self):
        static = os.path.join(os.path.dirname(TEMPLATES), "static")
        with open(os.path.join(static, "settings.js"), encoding="utf-8") as fh:
            listed = set(re.findall(r'"(\w+)"', re.search(
                r"FORM_PANELS = \[([^\]]*)\]", fh.read()).group(1)))
        with open(os.path.join(TEMPLATES, "settings.html"), encoding="utf-8") as fh:
            html = fh.read()

        needs_saving = set()
        for panel in re.findall(
                r'<div class="settings-panel" data-panel="(\w+)"[^>]*>(.*?)(?=<div class="settings-panel"|</form>)',
                html, re.S):
            name, body = panel
            if re.search(r'<(?:input|select|textarea)[^>]*name="', body):
                needs_saving.add(name)

        self.assertTrue(needs_saving, "no settings panels found - has the markup changed?")
        missing = needs_saving - listed
        self.assertFalse(missing, f"panels with fields but no Save row: {missing}")


if __name__ == "__main__":
    unittest.main()


class CanvasColourTests(unittest.TestCase):
    """Every CSS variable a canvas reads has to exist in the stylesheet.

    A canvas cannot inherit a colour: it asks getComputedStyle for a token
    by name and falls back to a literal if there is none. That fallback is
    a single fixed colour, so a misspelt token silently pins one theme's
    ink onto both -- which is exactly what happened when the scope reticle
    asked for --ink-primary, a name that has never existed. It drew dark
    grey marks on a dark field and vanished.
    """

    def test_every_token_a_canvas_asks_for_exists(self):
        static = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "spots", "web", "static")
        with open(os.path.join(static, "style.css"), encoding="utf-8") as fh:
            css = fh.read()
        defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))

        asked = {}
        for path in sorted(glob.glob(os.path.join(static, "*.js"))):
            with open(path, encoding="utf-8") as fh:
                for name in re.findall(r"""getPropertyValue\(\s*["'](--[a-z0-9-]+)["']"""
                                       r"""|css\(\s*["'](--[a-z0-9-]+)["']""", fh.read()):
                    token = name[0] or name[1]
                    asked.setdefault(token, []).append(os.path.basename(path))

        self.assertTrue(asked, "no canvas colours found -- has the helper been renamed?")
        missing = {t: sorted(set(f)) for t, f in asked.items() if t not in defined}
        self.assertEqual(missing, {},
                         "these CSS variables are read by a canvas but never defined")

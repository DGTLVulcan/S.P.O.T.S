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


if __name__ == "__main__":
    unittest.main()

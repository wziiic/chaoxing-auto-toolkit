import base64
import hashlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'panel')]
from study_fonts import decode_font, prepare_question_fonts, reference
from study_core import StudyError


def fixture_font(width=100):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(['.notdef', 'symbol'])
    builder.setupCharacterMap({ord('悛'): 'symbol'})
    empty = TTGlyphPen(None)
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0)); pen.lineTo((width, 0)); pen.lineTo((width, 100)); pen.closePath()
    glyph = pen.glyph()
    builder.setupGlyf({'.notdef': empty.glyph(), 'symbol': glyph})
    builder.setupHorizontalMetrics({'.notdef': (1000, 0), 'symbol': (1000, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({'familyName':'Fixture','styleName':'Regular'})
    builder.setupOS2(); builder.setupPost(); builder.setupMaxp()
    output=io.BytesIO();builder.save(output)
    points=''.join(f'{x}{y}{flag & 1}' for (x,y),flag in zip(glyph.coordinates,glyph.flags))
    return base64.b64encode(output.getvalue()).decode(), hashlib.md5(points.encode()).hexdigest()


class FontTests(unittest.TestCase):
    def setUp(self):decode_font.cache_clear()

    def test_bundled_reference_skips_non_unicode_glyph_names(self):
        values=reference()
        self.assertGreater(len(values),20000)
        self.assertTrue(any(value=='全' for value in values.values()))

    def test_exact_outlines_and_content_keyed_cache(self):
        first, a=fixture_font();second, b=fixture_font(200)
        with patch('study_fonts.reference',return_value={a:'全',b:'中'}):
            self.assertEqual(decode_font(first),{'悛':'全'})
            self.assertEqual(decode_font(second),{'悛':'中'})

    def test_unknown_outline_remains_unresolved(self):
        font,_=fixture_font()
        with patch('study_fonts.reference',return_value={}):
            self.assertEqual(decode_font(font),{'悛':None})

    def test_malformed_font_is_not_silently_accepted(self):
        for value in ('invalid!',base64.b64encode(b'not a font').decode(),'A'*6_000_001):
            with self.assertRaises(StudyError):decode_font(value)

    def test_only_embedded_declared_font_is_used(self):
        font, fingerprint=fixture_font();frame=MagicMock()
        frame.evaluate.side_effect=[[f"@font-face {{font-family: 'font-cxsecret';src:url('data:font/ttf;base64,{font}')}}"],None]
        with patch('study_fonts.reference',return_value={fingerprint:'全'}):prepare_question_fonts(frame)
        self.assertEqual(frame.evaluate.call_args.args[1],{'悛':'全'})
        frame=MagicMock();frame.evaluate.side_effect=[[],None]
        prepare_question_fonts(frame)
        self.assertEqual(frame.evaluate.call_args.args[1],{})

    def test_external_font_is_not_fetched(self):
        frame=MagicMock();frame.evaluate.return_value=["@font-face {font-family:font-cxsecret;src:url(https://example.invalid/font)}"]
        with self.assertRaises(StudyError):prepare_question_fonts(frame)

    def test_browser_decodes_only_styled_text_and_never_changes_controls(self):
        from playwright.sync_api import sync_playwright
        import lxt
        script=(Path(__file__).resolve().parents[1]/'panel/study_questions.js').read_text(encoding='utf-8')
        with sync_playwright() as runtime:
            browser=lxt.launch_chromium(runtime,headless=True,standard_automation=True)
            try:
                page=browser.new_page()
                page.set_content('''<div data-question-id="q1" data-question-type="single">
                    <div class="stem">普通悛<span style="font-family:font-cxsecret">悛</span></div>
                    <label><input type="radio" value="A"><span style="font-family:font-cxsecret">悛</span></label>
                    <label><input type="radio" value="B">悛</label></div>''')
                original=page.content()
                page.evaluate("window.__lxtQuestionFontMap={'悛':'全'}")
                rows=page.evaluate('() => ('+script+')(document).snapshot()')
                self.assertEqual(rows[0]['stem'],'普通悛全')
                self.assertEqual(rows[0]['options'],[{'data':'A','content':'全'},{'data':'B','content':'悛'}])
                self.assertEqual(original,page.content())
                page.evaluate("window.__lxtQuestionFontMap={'悛':null}")
                with self.assertRaisesRegex(Exception,'无法识别'):page.evaluate('() => ('+script+')(document).snapshot()')
            finally:browser.close()


if __name__=='__main__':unittest.main()

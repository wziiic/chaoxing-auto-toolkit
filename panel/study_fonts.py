"""Read displayed quiz text through the page's own embedded glyph outlines.

Reference fingerprints and algorithm: SocialSisterYi, MIT; see assets/font-LICENSE.
No question text leaves this process and no live DOM text is rewritten.
"""
import base64
import hashlib
import io
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from study_core import StudyError


@lru_cache(maxsize=1)
def reference():
    values = json.loads((Path(__file__).parent/'assets/font-map.json').read_text(encoding='utf-8'))
    result = {}
    for code, fingerprint in values.items():
        if not re.fullmatch(r'uni[0-9A-Fa-f]{4,6}', code):
            continue
        point = int(code[3:], 16)
        # Phonetic symbols may share outlines with Chinese radicals (e.g. ㄬ/广).
        # This reference is for CJK text only, not phonetic/alphabetic aliases.
        if not (0x2E80 <= point <= 0x2FDF or 0x3400 <= point <= 0x9FFF
                or 0xF900 <= point <= 0xFAFF or 0x20000 <= point <= 0x323AF):
            continue
        char = unicodedata.normalize('NFKC', chr(point))
        if fingerprint in result and result[fingerprint] != char:
            result[fingerprint] = None  # Ambiguous outlines must never be guessed.
        else:
            result[fingerprint] = char
    return result


@lru_cache(maxsize=32)
def decode_font(encoded):
    from fontTools.ttLib import TTFont
    if len(encoded) > 6_000_000:
        raise StudyError('题目字体过大，未读取或提交')
    try:
        blob = base64.b64decode(encoded, validate=True)
        with TTFont(io.BytesIO(blob), lazy=False) as font:
            cmap = font.getBestCmap()
            if not cmap or len(cmap) > 4096 or 'glyf' not in font:
                raise ValueError('unsupported font')
            result = {}
            for code, name in cmap.items():
                glyph = font['glyf'][name]
                glyph.expand(font['glyf'])
                if glyph.numberOfContours <= 0:
                    continue
                # Fingerprint actual contour coordinates and on-curve flags.
                points = ''.join(f'{x}{y}{flag & 1}' for (x, y), flag in zip(glyph.coordinates, glyph.flags))
                char = reference().get(hashlib.md5(points.encode()).hexdigest())
                result[chr(code)] = char
            return result
    except StudyError:
        raise
    except Exception:
        raise StudyError('题目字体无法精确识别，未发送模型或提交') from None


def prepare_question_fonts(frame):
    styles = frame.evaluate("""() => Array.from(document.querySelectorAll('style'))
        .map(e=>e.textContent).filter(s=>/@font-face/i.test(s)&&/font-cxsecret/i.test(s))""")
    mapping = {}
    for style in styles:
        for block in re.findall(r'@font-face\s*\{([^}]+)\}', style, re.I):
            if not re.search(r'font-family\s*:\s*[\"\x27]?font-cxsecret\b', block, re.I):
                continue
            match = re.search(r'url\([\"\x27]?data:[^,;)]+(?:;[^,)]*)?;base64,([A-Za-z0-9+/=\s]+)', block, re.I)
            if not match:
                raise StudyError('题目自定义字体尚未加载或格式不支持，未发送模型或提交')
            current = decode_font(re.sub(r'\s+', '', match[1]))
            for char, decoded in current.items():
                if char in mapping and mapping[char] != decoded:
                    raise StudyError('题目存在冲突字体，未发送模型或提交')
                mapping[char] = decoded
    frame.evaluate('(mapping) => { window.__lxtQuestionFontMap = mapping; }', mapping)

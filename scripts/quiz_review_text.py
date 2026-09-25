"""Readable review text from source markup; never used to replace source content.

The bounded MathML presentation rules originated in the CHEM review adapter.
Unsupported structures remain explicit review diagnostics in the caller.
"""
from __future__ import annotations

from html import escape
import re
import xml.etree.ElementTree as ET

from extract_quiz_pool_review import clean_inline_text, plain_text_from_html

SUB = str.maketrans("0123456789+-=()aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ")
SUP = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
MATH = re.compile(r"<(?:[\w.-]+:)?math\b[^>]*>.*?</(?:[\w.-]+:)?math\s*>", re.I | re.S)


def math_text(node: ET.Element) -> str:
    tag = node.tag.rsplit("}", 1)[-1]
    kids = list(node)
    if tag in {"annotation", "annotation-xml"}:
        return ""
    if tag == "semantics":
        return next((math_text(k) for k in kids if k.tag.rsplit("}", 1)[-1]
                     not in {"annotation", "annotation-xml"}), "")
    if tag in {"mi", "mn", "mo", "mtext", "ms"}:
        return "".join(node.itertext())
    if tag in {"math", "mstyle", "mrow", "mpadded"}:
        return "".join(math_text(k) for k in kids)
    if tag == "mspace":
        return " "
    if tag in {"msub", "msup", "msubsup"}:
        if len(kids) != (3 if tag == "msubsup" else 2):
            raise ValueError("Invalid MathML child count: " + tag)
        parts = [math_text(k) for k in kids]
        result = parts[0]
        for i, text in enumerate(parts[1:]):
            is_sub = tag == "msub" or (tag == "msubsup" and i == 0)
            table = SUB if is_sub else SUP
            result += (text.translate(table) if all(ord(c) in table for c in text)
                       else ("_" if is_sub else "^") + "(" + text + ")")
        return result
    if tag == "mfrac" and len(kids) == 2:
        return "(" + math_text(kids[0]) + ")/(" + math_text(kids[1]) + ")"
    if tag == "msqrt":
        return "√(" + "".join(math_text(k) for k in kids) + ")"
    if tag == "mfenced":
        return node.get("open", "(") + node.get("separators", ",")[:1].join(
            math_text(k) for k in kids) + node.get("close", ")")
    raise ValueError("Unsupported MathML: " + tag)


def readable_html(raw: str) -> str:
    replacements: dict[str, str] = {}
    prefix = "COURSECRAFTREVIEWMATH"
    if prefix in raw:
        raise ValueError("Math placeholder collides with source text")

    def replace_math(match: re.Match) -> str:
        text = math_text(ET.fromstring(match.group()))
        if not text.strip():
            raise ValueError("MathML has no visible presentation")
        token = prefix + str(len(replacements)) + "END"
        replacements[token] = text
        return token

    # Parse before unescaping: Wiris annotations can contain escaped MathML.
    rendered = MATH.sub(replace_math, raw)
    rendered = re.sub(
        r"<(sub|sup)\b[^>]*>(.*?)</\1>",
        lambda m: math_text(ET.fromstring("<m" + m[1].lower() + "><mi></mi><mi>"
                           + escape(plain_text_from_html(m[2])) + "</mi></m" + m[1].lower() + ">")),
        rendered, flags=re.I | re.S,
    )
    rendered = plain_text_from_html(rendered)
    for token, text in replacements.items():
        rendered = rendered.replace(token, text)
    return clean_inline_text(rendered)

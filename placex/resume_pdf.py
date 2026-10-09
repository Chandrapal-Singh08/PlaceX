"""Render generated resume Markdown as a compact, ATS-readable PDF."""
import re
from importlib.resources import files
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer
from reportlab.pdfgen.canvas import Canvas
from io import BytesIO
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

pdfmetrics.registerFont(TTFont("ResumeSans", str(files("reportlab").joinpath("fonts/Vera.ttf"))))
pdfmetrics.registerFont(TTFont("ResumeSans-Bold", str(files("reportlab").joinpath("fonts/VeraBd.ttf"))))
pdfmetrics.registerFontFamily(
    "ResumeSans", normal="ResumeSans", bold="ResumeSans-Bold",
    italic="ResumeSans", boldItalic="ResumeSans-Bold",
)


def _inline_markup(text: str) -> str:
    escaped = escape(text, quote=False)
    escaped = re.sub(r"\\([\\*#|])", r"\1", escaped)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


def _page_number(canvas: Canvas, document) -> None:
    if canvas.getPageNumber() > 1:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawRightString(letter[0] - 0.68 * inch, 0.38 * inch, str(canvas.getPageNumber()))
        canvas.restoreState()


def generate_resume_pdf(markdown: str) -> bytes:
    """Generate a readable, single-column PDF from the builder's Markdown."""
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.68 * inch,
        leftMargin=0.68 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.65 * inch,
        title="Professional Resume",
        author="PlaceX",
    )
    base = getSampleStyleSheet()
    styles = {
        "name": ParagraphStyle(
            "ResumeName", parent=base["Title"], fontName="ResumeSans-Bold",
            fontSize=20, leading=23, textColor=colors.HexColor("#172554"),
            alignment=TA_CENTER, spaceAfter=3,
        ),
        "contact": ParagraphStyle(
            "ResumeContact", parent=base["Normal"], fontName="ResumeSans",
            fontSize=9, leading=12, textColor=colors.HexColor("#334155"),
            alignment=TA_CENTER, spaceAfter=8,
        ),
        "section": ParagraphStyle(
            "ResumeSection", parent=base["Heading2"], fontName="ResumeSans-Bold",
            fontSize=11, leading=14, textColor=colors.HexColor("#1e3a5f"),
            spaceBefore=10, spaceAfter=4, keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "ResumeBody", parent=base["BodyText"], fontName="ResumeSans",
            fontSize=9.7, leading=13, textColor=colors.HexColor("#1f2937"),
            spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "ResumeBullet", parent=base["BodyText"], fontName="ResumeSans",
            fontSize=9.6, leading=12.5, textColor=colors.HexColor("#1f2937"),
            leftIndent=13, firstLineIndent=-9, bulletIndent=0, spaceAfter=3,
            allowWidows=0, allowOrphans=0,
        ),
    }

    lines = markdown.splitlines()
    story = []
    first_content = True
    section_has_content = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# "):
            story.append(Paragraph(_inline_markup(line[2:]), styles["name"]))
            continue
        if line.startswith("## "):
            if section_has_content:
                story.append(Spacer(1, 2))
            story.append(Paragraph(_inline_markup(line[3:].upper()), styles["section"]))
            rule = HRFlowable(
                width="100%", thickness=0.55, color=colors.HexColor("#cbd5e1"),
                spaceBefore=0, spaceAfter=4,
            )
            rule.keepWithNext = True
            story.append(rule)
            section_has_content = False
            first_content = False
            continue
        if line.startswith("- "):
            story.append(Paragraph(
                _inline_markup(line[2:]), styles["bullet"], bulletText="-"
            ))
        else:
            style = styles["contact"] if first_content else styles["body"]
            story.append(Paragraph(_inline_markup(line), style))
            first_content = False
        section_has_content = True

    if not story:
        raise ValueError("Cannot generate a PDF from an empty resume.")
    document.build(story, onFirstPage=_page_number, onLaterPages=_page_number)
    return buffer.getvalue()

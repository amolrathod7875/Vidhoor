"""Draft export utilities for PDF and DOCX formats."""

from __future__ import annotations

import importlib
import re
from io import BytesIO


def _normalize_lines(text: str) -> list[str]:
	value = str(text or "").replace("\r\n", "\n")
	return [line.rstrip() for line in value.split("\n")]


def _to_printable_text(text: str) -> str:
	value = str(text or "").replace("\r\n", "\n")
	value = re.sub(r"^#{1,6}\s*", "", value, flags=re.MULTILINE)
	value = re.sub(r"\*\*(.*?)\*\*", r"\1", value)
	value = re.sub(r"__(.*?)__", r"\1", value)
	value = re.sub(r"`(.*?)`", r"\1", value)
	value = re.sub(r"^\s*[-*]\s+", "• ", value, flags=re.MULTILINE)
	value = re.sub(r"\[(?:[A-Z0-9_'\s]+)\]", "", value)
	value = re.sub(r"[ \t]+", " ", value)
	value = re.sub(r"\n{3,}", "\n\n", value)
	return value.strip()


def render_draft_pdf_bytes(title: str, draft_content: str, disclaimer: str) -> bytes:
	"""Render draft content to PDF bytes using reportlab."""
	try:
		reportlab_pagesizes = importlib.import_module("reportlab.lib.pagesizes")
		reportlab_units = importlib.import_module("reportlab.lib.units")
		reportlab_canvas = importlib.import_module("reportlab.pdfgen.canvas")
	except Exception as exc:
		raise RuntimeError("PDF export is unavailable because reportlab is not installed") from exc

	clean_title = _to_printable_text(title or "Legal Draft")
	clean_content = _to_printable_text(draft_content)
	clean_disclaimer = _to_printable_text(disclaimer)

	buffer = BytesIO()
	pdf = reportlab_canvas.Canvas(buffer, pagesize=reportlab_pagesizes.A4)
	width, height = reportlab_pagesizes.A4

	left_margin = 18 * reportlab_units.mm
	right_margin = 18 * reportlab_units.mm
	top_margin = 18 * reportlab_units.mm
	bottom_margin = 18 * reportlab_units.mm
	max_width = width - left_margin - right_margin

	y = height - top_margin
	pdf.setFont("Helvetica-Bold", 14)
	pdf.drawString(left_margin, y, clean_title[:120])
	y -= 10 * reportlab_units.mm

	pdf.setFont("Helvetica", 11)
	for raw in _normalize_lines(clean_content):
		line = raw or " "
		segments = []
		current = ""
		for word in line.split(" "):
			candidate = (current + " " + word).strip()
			if pdf.stringWidth(candidate, "Helvetica", 11) <= max_width:
				current = candidate
			else:
				if current:
					segments.append(current)
				current = word
		if current:
			segments.append(current)
		if not segments:
			segments = [""]

		for segment in segments:
			if y <= bottom_margin + 24 * reportlab_units.mm:
				pdf.showPage()
				y = height - top_margin
				pdf.setFont("Helvetica", 11)
			pdf.drawString(left_margin, y, segment)
			y -= 5.5 * reportlab_units.mm

	y -= 3 * reportlab_units.mm
	if y <= bottom_margin + 12 * reportlab_units.mm:
		pdf.showPage()
		y = height - top_margin

	pdf.setFont("Helvetica-Oblique", 9)
	for line in _normalize_lines(clean_disclaimer):
		if y <= bottom_margin:
			pdf.showPage()
			y = height - top_margin
			pdf.setFont("Helvetica-Oblique", 9)
		pdf.drawString(left_margin, y, line[:180])
		y -= 4.6 * reportlab_units.mm

	pdf.save()
	buffer.seek(0)
	return buffer.getvalue()


def render_draft_docx_bytes(title: str, draft_content: str, disclaimer: str) -> bytes:
	"""Render draft content to DOCX bytes using python-docx."""
	try:
		docx_module = importlib.import_module("docx")
	except Exception as exc:
		raise RuntimeError("DOCX export is unavailable because python-docx is not installed") from exc

	clean_title = _to_printable_text(title or "Legal Draft")
	clean_content = _to_printable_text(draft_content)
	clean_disclaimer = _to_printable_text(disclaimer)

	doc = docx_module.Document()
	doc.add_heading(clean_title or "Legal Draft", level=1)

	for line in _normalize_lines(clean_content):
		doc.add_paragraph(line)

	doc.add_paragraph("")
	disclaimer_para = doc.add_paragraph(clean_disclaimer)
	if disclaimer_para.runs:
		disclaimer_para.runs[0].italic = True

	buffer = BytesIO()
	doc.save(buffer)
	buffer.seek(0)
	return buffer.getvalue()

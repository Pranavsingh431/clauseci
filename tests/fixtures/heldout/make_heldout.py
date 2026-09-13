"""
Generates the held out semantic fixture.

This document is NOT in the Drive corpus and NOT in demo_contracts. It exists
only so a test can prove the analyzer reads the source rather than remembering
that Acme is 30 days.

Two things differ from the real amendment on purpose:
  the cap is 45 days, not 30
  the clause covers application logs only, not application plus diagnostic plus audit

The correct extraction must therefore be 45 days covering application_logs only.
"""
import os
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

OUT = os.path.dirname(os.path.abspath(__file__))
ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Title"], fontSize=15, leading=19, spaceAfter=4)
SUB = ParagraphStyle("SUB", parent=ss["Normal"], fontSize=9.5, alignment=TA_CENTER,
                     textColor="#444444", spaceAfter=14)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=11, leading=14, spaceBefore=11, spaceAfter=4)
BODY = ParagraphStyle("BODY", parent=ss["Normal"], fontSize=9.8, leading=13.6, spaceAfter=6)
META = ParagraphStyle("META", parent=ss["Normal"], fontSize=9.5, leading=14, spaceAfter=2)

story = [
    Paragraph("AMENDMENT NO. 3 TO DATA PROCESSING AGREEMENT", H1),
    Paragraph("Northwind Systems, Inc. and Acme Corporation", SUB),
]
for k, v in [
    ("Agreement ID", "NW-DPA-ACME-2026-A3-HELDOUT"),
    ("Counterparty", "Acme Corporation (Delaware, USA)"),
    ("Effective Date", "1 September 2026"),
    ("Status", "EXECUTED &ndash; FULLY SIGNED BY BOTH PARTIES"),
    ("Amends", "NW-DPA-ACME-2025-0114"),
]:
    story.append(Paragraph(f"<b>{k}:</b> {v}", META))
story.append(Spacer(1, 12))

for heading, paras in [
    ("1. PRECEDENCE",
     ["1.1 Notwithstanding any prior agreement between the parties, the provisions of this Amendment "
      "control and supersede any conflicting provision of the Data Processing Agreement."]),
    ("2. APPLICATION LOG RETENTION",
     ["2.1 <b>Application logs shall not be retained for more than forty-five (45) days</b> from the date "
      "of creation.",
      "2.2 For the avoidance of doubt, this Section 2 applies solely to application logs. Diagnostic logs "
      "and audit logs are not addressed by this Amendment and remain governed by the agreements in force."]),
    ("3. NO OTHER CHANGES",
     ["3.1 Except as expressly amended herein, the Data Processing Agreement remains in full force and effect."]),
]:
    story.append(Paragraph(heading, H2))
    for p in paras:
        story.append(Paragraph(p, BODY))

story.append(Spacer(1, 16))
story.append(Paragraph("SIGNATURES", H2))
for line in [
    "<b>NORTHWIND SYSTEMS, INC.</b> &nbsp; By: /s/ Dana Whitfield &nbsp; Title: VP Legal &nbsp; Date: 1 Sep 2026",
    "<b>ACME CORPORATION</b> &nbsp; By: /s/ Martin Reyes &nbsp; Title: General Counsel &nbsp; Date: 1 Sep 2026",
]:
    story.append(Paragraph(line, META))

path = os.path.join(OUT, "HO_Acme_DPA_Amendment_A3.pdf")
SimpleDocTemplate(path, pagesize=LETTER, leftMargin=0.9*inch, rightMargin=0.9*inch,
                  topMargin=0.8*inch, bottomMargin=0.8*inch).build(story)
print("wrote", path)

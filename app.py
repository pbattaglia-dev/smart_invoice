import io
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from dataclasses import dataclass

import pdfplumber
from flask import Flask, render_template, request, jsonify, send_file
from fpdf import FPDF

app = Flask(__name__)


def _load_config():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if not os.path.exists(path):
        print("ERROR: config.json not found.")
        print("Copy config.example.json to config.json and fill in your details.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


CONFIG = _load_config()
BILLED_TO = CONFIG["billed_to"]
FROM = CONFIG["from"]
BANK = CONFIG["bank"]


@dataclass
class TimeEntry:
    description: str
    duration_seconds: int
    entry_date: date


@dataclass
class InvoiceLineItem:
    description: str
    qty_hours: float
    unit_cost: float
    tag: str = ""

    @property
    def amount(self):
        return round(self.unit_cost * self.qty_hours, 2)


def _clean_duration(text):
    return text.replace("\x00", ":")


def _clean_description(text):
    text = re.sub(r"\x00([^*\x00]+)\x00", r"*\1*", text)
    text = re.sub(r"\x00([^*\x00]+\*)", r"*\1", text)
    text = re.sub(r"([A-Z])\x00([A-Z])", r"\1-\2", text)
    text = text.replace("\x00", ":")
    text = text.replace("\n", " ")
    return text.strip()


def _parse_duration_seconds(dur_str):
    parts = dur_str.split(":")
    if len(parts) != 3:
        return 0
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    return h * 3600 + m * 60 + s


def _extract_date(date_col_text):
    cleaned = date_col_text.replace("\x00", ":")
    dates = re.findall(r"(\d{2}/\d{2}/\d{4})", cleaned)
    if not dates:
        return None
    return datetime.strptime(dates[-1], "%m/%d/%Y").date()


def parse_toggl_pdf(file_stream):
    entries = []

    with pdfplumber.open(file_stream) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table[2:]:
                    if not row or not row[0] or not row[1]:
                        continue
                    desc = _clean_description(row[0])
                    dur_str = _clean_duration(row[1])
                    entry_date = _extract_date(row[5]) if len(row) > 5 and row[5] else None
                    if not entry_date:
                        continue
                    seconds = _parse_duration_seconds(dur_str)
                    if seconds > 0:
                        entries.append(TimeEntry(desc, seconds, entry_date))

    date_range_start = min(e.entry_date for e in entries) if entries else None
    date_range_end = max(e.entry_date for e in entries) if entries else None
    total_seconds = sum(e.duration_seconds for e in entries)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    total_hours_str = f"{h}:{m:02d}:{s:02d}"

    return entries, date_range_start, date_range_end, total_hours_str


def _extract_tag(description):
    m = re.search(r"\*([^*]+)\*", description)
    return m.group(1) if m else ""


def group_entries(entries, hourly_rate):
    totals = defaultdict(int)
    order = []
    seen = set()

    for e in entries:
        tag = _extract_tag(e.description)
        key = (tag, e.description)
        totals[key] += e.duration_seconds
        if key not in seen:
            seen.add(key)
            order.append(key)

    tagged_buckets = defaultdict(list)
    for tag, desc in order:
        qty = round(totals[(tag, desc)] / 3600, 2)
        tagged_buckets[tag].append(InvoiceLineItem(desc, qty, hourly_rate, tag))

    result = tagged_buckets.pop("", [])
    for tag in sorted(tagged_buckets.keys()):
        result.extend(tagged_buckets[tag])
    return result


class InvoicePDF(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=20)


def _count_lines(pdf, text, width):
    words = text.split(" ")
    line = ""
    count = 1
    for word in words:
        test = f"{line} {word}".strip()
        if pdf.get_string_width(test) > width - 1:
            count += 1
            line = word
        else:
            line = test
    return count


def generate_invoice_pdf(
    line_items,
    invoice_number,
    date_of_issue,
    due_date,
    purchase_order,
    hourly_rate,
    services_summary,
    date_range_start,
    date_range_end,
):
    pdf = InvoicePDF()
    pdf.add_page()
    lm = pdf.l_margin
    pw = pdf.w - lm - pdf.r_margin

    GRAY = (80, 90, 100)
    LGRAY = (100, 110, 120)
    DARK = (40, 40, 40)
    LH = 4.5

    # -- Title --
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_text_color(*GRAY)
    pdf.cell(pw, 12, "Invoice", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # -- Invoice number / Date of issue / Due date --
    col_w = pw / 3
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*LGRAY)
    pdf.cell(col_w, 4, "INVOICE NUMBER", new_x="RIGHT")
    pdf.cell(col_w, 4, "DATE OF ISSUE", new_x="RIGHT")
    pdf.cell(col_w, 4, "DUE DATE", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*DARK)
    pdf.cell(col_w, 6, invoice_number, new_x="RIGHT")
    pdf.cell(col_w, 6, date_of_issue, new_x="RIGHT")
    pdf.cell(col_w, 6, due_date, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # -- Billed to / From / Purchase order --
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*LGRAY)
    pdf.cell(col_w, 4, "BILLED TO", new_x="RIGHT")
    pdf.cell(col_w, 4, "FROM", new_x="RIGHT")
    pdf.cell(col_w, 4, "PURCHASE ORDER", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*DARK)
    y_start = pdf.get_y()

    pdf.multi_cell(col_w, LH, f"{BILLED_TO['company']}\n{BILLED_TO['region']}\n{BILLED_TO['email']}")
    y1 = pdf.get_y()
    pdf.set_xy(lm + col_w, y_start)
    pdf.multi_cell(col_w, LH, f"{FROM['name']}\nABN: {FROM['abn']}\n{FROM['address1']}\n{FROM['address2']}\n{FROM['postcode']}")
    y2 = pdf.get_y()
    pdf.set_xy(lm + 2 * col_w, y_start)
    pdf.cell(col_w, LH, purchase_order or "")
    pdf.set_y(max(y1, y2) + 5)

    # -- Line items table --
    id_w = pw * 0.05
    desc_w = pw * 0.50
    cost_w = pw * 0.13
    qty_w = pw * 0.12
    amt_w = pw * 0.20

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*GRAY)
    pdf.cell(id_w, 6, "#", new_x="RIGHT")
    pdf.cell(desc_w, 6, "Description", new_x="RIGHT")
    pdf.cell(cost_w, 6, "Unit cost", align="R", new_x="RIGHT")
    pdf.cell(qty_w, 6, "QTY", align="R", new_x="RIGHT")
    pdf.cell(amt_w, 6, "Amount", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(200, 200, 200)
    pdf.line(lm, pdf.get_y(), lm + pw, pdf.get_y())
    pdf.ln(1)

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*DARK)

    tags_seen = set()
    tag_number = 0
    item_id = 0

    for item in line_items:
        if item.tag and item.tag not in tags_seen:
            tags_seen.add(item.tag)
            tag_number += 1
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*GRAY)
            pdf.cell(desc_w, 6, f"{tag_number}. {item.tag.upper()}", new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(200, 200, 200)
            pdf.line(lm, pdf.get_y(), lm + pw, pdf.get_y())
            pdf.ln(1)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*DARK)
        item_id += 1
        label = item.description
        num_lines = _count_lines(pdf, label, desc_w)
        row_h = LH * num_lines

        footer_h = 70
        if pdf.get_y() + row_h > pdf.h - footer_h:
            pdf.add_page()

        y_row = pdf.get_y()
        pdf.set_xy(lm, y_row)
        pdf.cell(id_w, LH, str(item_id))
        pdf.set_xy(lm + id_w, y_row)
        pdf.multi_cell(desc_w, LH, label, align="L")
        y_after = pdf.get_y()
        actual_h = y_after - y_row

        mid_y = y_row + (actual_h - LH) / 2
        rate_str = str(int(hourly_rate)) if hourly_rate == int(hourly_rate) else f"{hourly_rate:.2f}"
        amt_str = f"{item.amount:.2f}" if item.amount != int(item.amount) else str(int(item.amount))

        pdf.set_xy(lm + id_w + desc_w, mid_y)
        pdf.cell(cost_w, LH, rate_str, align="R")
        pdf.set_xy(lm + id_w + desc_w + cost_w, mid_y)
        pdf.cell(qty_w, LH, f"{item.qty_hours}", align="R")
        pdf.set_xy(lm + id_w + desc_w + cost_w + qty_w, mid_y)
        pdf.cell(amt_w, LH, amt_str, align="R")

        pdf.set_y(y_after + 0.5)

    # -- Separator line --
    pdf.ln(3)
    pdf.set_draw_color(*GRAY)
    pdf.line(lm, pdf.get_y(), lm + pw, pdf.get_y())
    pdf.ln(5)

    tag_totals = defaultdict(float)
    for item in line_items:
        tag_totals[item.tag] += item.amount
    total = sum(tag_totals.values())

    # -- Terms + totals side by side --
    terms_w = pw * 0.50
    totals_w = pw * 0.50
    label_w = totals_w * 0.55
    val_w = totals_w * 0.45

    y_section = pdf.get_y()

    # Terms (left side)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*GRAY)
    pdf.cell(terms_w, 5, "TERMS", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*DARK)
    pdf.multi_cell(terms_w - 5, LH, services_summary)
    y_after_terms = pdf.get_y()

    # Totals (right side)
    def _total_row(label, value, bold=False):
        pdf.set_x(lm + terms_w)
        pdf.set_font("Helvetica", "B" if bold else "", 8)
        pdf.set_text_color(*GRAY)
        pdf.cell(label_w, 5.5, label)
        pdf.set_text_color(*DARK)
        pdf.cell(val_w, 5.5, value, align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_xy(lm + terms_w, y_section)
    has_tags = any(tag for tag in tag_totals if tag)
    if has_tags:
        _total_row("GENERAL", f"AU$ {tag_totals.get('', 0):,.2f}")
        tag_num = 0
        for tag in sorted(t for t in tag_totals if t):
            tag_num += 1
            _total_row(f"{tag_num}. {tag.upper()}", f"AU$ {tag_totals[tag]:,.2f}")
    else:
        _total_row("SUBTOTAL", f"AU$ {total:,.2f}")
    _total_row("(TAX RATE)", "0 %")
    _total_row("TAX", "AU$ 0")
    _total_row("SHIPPING", "AU$ 0")
    pdf.ln(4)

    pdf.set_x(lm + terms_w)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*GRAY)
    pdf.cell(label_w + val_w, 5, "INVOICE TOTAL", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(lm + terms_w)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*DARK)
    pdf.cell(label_w + val_w, 8, f"AU$ {total:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(max(pdf.get_y(), y_after_terms) + 8)

    # -- Bank details --
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*DARK)
    pdf.cell(pw, 5, "BANK ACCOUNT DETAILS", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(pw, LH, BANK["name"], new_x="LMARGIN", new_y="NEXT")
    pdf.cell(pw, LH, f"BSB {BANK['bsb']}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(pw, LH, f"Account {BANK['account']}", new_x="LMARGIN", new_y="NEXT")

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return buf


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/parse", methods=["POST"])
def parse():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file uploaded"}), 400

    try:
        entries, start, end, total_hours = parse_toggl_pdf(f.stream)
    except Exception as e:
        return jsonify({"error": f"Failed to parse PDF: {e}"}), 400

    grouped = group_entries(entries, 35)

    return jsonify({
        "date_range_start": start.isoformat() if start else None,
        "date_range_end": end.isoformat() if end else None,
        "total_hours": total_hours,
        "entry_count": len(entries),
        "line_items": [
            {
                "description": item.description,
                "qty_hours": item.qty_hours,
                "tag": item.tag,
            }
            for item in grouped
        ],
    })


@app.route("/generate", methods=["POST"])
def generate():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file uploaded"}), 400

    invoice_number = request.form.get("invoice_number", "")
    date_of_issue = request.form.get("date_of_issue", date.today().strftime("%d/%m/%Y"))
    due_date_str = request.form.get("due_date", (date.today() + timedelta(days=7)).strftime("%d/%m/%Y"))
    hourly_rate = float(request.form.get("hourly_rate", "35"))
    purchase_order = request.form.get("purchase_order", "")
    services_summary = request.form.get("services_summary", "")

    try:
        entries, start, end, _ = parse_toggl_pdf(f.stream)
    except Exception as e:
        return jsonify({"error": f"Failed to parse PDF: {e}"}), 400

    line_items = group_entries(entries, hourly_rate)

    overrides_raw = request.form.get("description_overrides", "{}")
    try:
        overrides = json.loads(overrides_raw)
    except json.JSONDecodeError:
        overrides = {}
    for idx_str, new_desc in overrides.items():
        idx = int(idx_str)
        if 0 <= idx < len(line_items):
            line_items[idx].description = new_desc

    buf = generate_invoice_pdf(
        line_items=line_items,
        invoice_number=invoice_number,
        date_of_issue=date_of_issue,
        due_date=due_date_str,
        purchase_order=purchase_order,
        hourly_rate=hourly_rate,
        services_summary=services_summary,
        date_range_start=start,
        date_range_end=end,
    )

    filename = f"Invoice_{invoice_number}.pdf" if invoice_number else "Invoice.pdf"
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=filename)


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", port=5050)

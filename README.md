# Smart Invoice

Turn Toggl Track PDF reports into formatted invoices. Drop a PDF, tweak a few fields, download the invoice.

## Setup

1. Copy the example config and fill in your details:

```bash
cp config.example.json config.json
```

Edit `config.json` with your billing info (name, ABN, address, bank details). This file is gitignored and never committed.

2. Run the app:

**macOS (double-click):** Open `launch.command` from Finder. First run creates a venv and installs deps (~10 sec).

**Manual:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:5050

## How It Works

1. **Upload** a Toggl detailed report PDF (drag-and-drop or click)
2. **Review** parsed time entries grouped by date, edit descriptions inline
3. **Configure** invoice number, PO, dates, hourly rate, services summary
4. **Generate** a formatted invoice PDF

## Tagged Sections

Entries containing `*Tag*` patterns (e.g. `*Content*`, `*Design*`) in the description are automatically separated into numbered sections. The preview and generated PDF show regular entries first, then each tagged group with its own subtotal in the price breakdown.

## Stack

- **Flask** -- web server
- **pdfplumber** -- Toggl PDF parsing
- **fpdf2** -- invoice PDF generation

## Defaults

| Field | Value |
|-------|-------|
| Currency | AU$ |
| Tax Rate | 0% |
| Default Rate | AU$ 35/hr |
| Due Date | Issue date + 7 days |

## License

MIT

"""
CNC Billing — single file (engine + modern UI).
Run:  python3 cnc_billing.py
"""
import os, sys, json, sqlite3, subprocess, datetime as dt, re
from pathlib import Path
from xml.sax.saxutils import escape
import tkinter as tk
from tkinter import ttk, messagebox

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Table, TableStyle,
                                Spacer, Image as RLImage)


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


APP_TITLE = "CNC_Billing"
DATA_DIR = Path(os.path.expanduser("~/CNCMonitorCare"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE = str(DATA_DIR / "cnc_billing.db")
SETTINGS_FILE = DATA_DIR / "company.json"
CUSTOMERS_FILE = DATA_DIR / "customers.json"
LOGO_FILE = Path(resource_path("logo.png"))
OUTPUT_DIR = DATA_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

REVIEW_URL = "https://g.page/r/CWXeF-fOPshiEAE/review"
REVIEW_TEXT = "Would love your feedback! Post a review to our profile."

BLACK = colors.HexColor('#000000')
DARK = colors.HexColor('#333333')
GRAY = colors.HexColor('#666666')
LIGHT_GRAY = colors.HexColor('#CCCCCC')
BOX_GRAY = colors.HexColor('#AAAAAA')
HEADER_BG = colors.HexColor('#222222')

DEFAULT_COMPANY = {
    "name": "CNC Monitor Care",
    "address": "No: 68, Thirumagal Nagar Main Road, Peelamedu",
    "city": "Coimbatore, Tamil Nadu 641004",
    "phone": "9043375600",
    "email": "support@cncmonitorcare.com",
    "gstin": "33AJYPP4045R1Z8",
    "state": "Tamil Nadu",
    "state_code": "33",
    "bank_name": "ICICI Bank",
    "bank_branch": "Avinashi Road, Coimbatore",
    "bank_account_name": "CNC Monitor Care",
    "bank_account_no": "058705003129",
    "bank_ifsc": "ICIC0000587",
}

TERMS = [
    "6 Month Warranty for Spares",
    "100% Advance before Delivery",
    "Delivery Time 5 days",
    "GST Taxes all included",
    "No Return",
    "No Refund",
]

DOC_TYPES = ["Quotation", "Proforma Invoice", "Invoice",
             "Delivery Challan", "Purchase Order"]

HSN_CHOICES = ["", "8462", "8466", "8471", "8473", "8504", "8528",
               "8537", "8544", "9031", "998713", "998714", "998719"]
UNIT_CHOICES = ["Nos", "Pcs", "Set", "Kit", "Box", "Mtr", "Kg", "Ltr",
                "Hr", "Day", "Job", "Service"]


def load_company():
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_COMPANY)
            merged.update({k: v for k, v in data.items() if v is not None})
            return merged
        except Exception:
            pass
    return dict(DEFAULT_COMPANY)


def save_company(data):
    SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                             encoding="utf-8")


COMPANY = load_company()


def load_customers():
    if CUSTOMERS_FILE.exists():
        try:
            return json.loads(CUSTOMERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_customers(data):
    CUSTOMERS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                              encoding="utf-8")


CUSTOMERS = load_customers()


def remember_customer(party):
    name = (party.get('party_name') or '').strip()
    if not name:
        return
    CUSTOMERS[name] = {
        'party_name': name,
        'party_address': party.get('party_address', ''),
        'party_gstin': party.get('party_gstin', ''),
        'party_state': party.get('party_state', ''),
        'party_phone': party.get('party_phone', ''),
        'party_email': party.get('party_email', ''),
    }
    save_customers(CUSTOMERS)


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_type TEXT NOT NULL,
        doc_no TEXT NOT NULL UNIQUE,
        doc_date TEXT NOT NULL,
        party_name TEXT, party_address TEXT, party_gstin TEXT,
        party_state TEXT, party_phone TEXT, party_email TEXT,
        subtotal REAL DEFAULT 0, discount REAL DEFAULT 0,
        cgst REAL DEFAULT 0, sgst REAL DEFAULT 0, igst REAL DEFAULT 0,
        total REAL DEFAULT 0, notes TEXT,
        items_json TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS counters (
        doc_type TEXT PRIMARY KEY, last_no INTEGER DEFAULT 0)""")
    for t in DOC_TYPES:
        c.execute("INSERT OR IGNORE INTO counters(doc_type,last_no) VALUES (?,0)", (t,))
    conn.commit()
    conn.close()


def next_doc_no(doc_type):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT last_no FROM counters WHERE doc_type=?", (doc_type,))
    row = c.fetchone()
    n = (row[0] if row else 0) + 1
    c.execute("INSERT OR REPLACE INTO counters(doc_type,last_no) VALUES (?,?)",
              (doc_type, n))
    conn.commit()
    conn.close()
    prefix = {"Quotation": "QT", "Proforma Invoice": "PI", "Invoice": "INV",
              "Delivery Challan": "DC", "Purchase Order": "PO"}[doc_type]
    return f"{prefix}-{dt.date.today().year}-{n:04d}"


def save_document(data):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO documents
        (id, doc_type,doc_no,doc_date,party_name,party_address,party_gstin,
         party_state,party_phone,party_email,subtotal,discount,cgst,sgst,igst,
         total,notes,items_json,created_at)
        VALUES (
          (SELECT id FROM documents WHERE doc_no=?),
          ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (data['doc_no'],
         data['doc_type'], data['doc_no'], data['doc_date'],
         data['party_name'], data['party_address'], data['party_gstin'],
         data['party_state'], data['party_phone'], data['party_email'],
         data['subtotal'], data['discount'], data['cgst'], data['sgst'],
         data['igst'], data['total'], data['notes'],
         json.dumps(data['items']), dt.datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_document(doc_no):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""SELECT doc_type,doc_no,doc_date,party_name,party_address,
                        party_gstin,party_state,party_phone,party_email,
                        subtotal,discount,cgst,sgst,igst,total,notes,items_json
                 FROM documents WHERE doc_no=?""", (doc_no,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    keys = ["doc_type", "doc_no", "doc_date", "party_name", "party_address",
            "party_gstin", "party_state", "party_phone", "party_email",
            "subtotal", "discount", "cgst", "sgst", "igst", "total",
            "notes", "items_json"]
    data = dict(zip(keys, row))
    try:
        data['items'] = json.loads(data.pop('items_json') or "[]")
    except Exception:
        data['items'] = []
    return data


def list_documents(limit=500):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""SELECT doc_no, doc_type, doc_date, party_name, total
                 FROM documents ORDER BY id DESC LIMIT ?""", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows


def list_usb_printers():
    printers = []
    try:
        if sys.platform.startswith('win'):
            return printers
        out = subprocess.run(["lpstat", "-v"], capture_output=True,
                             text=True, timeout=5).stdout
        for line in out.splitlines():
            m = re.match(r"device for (\S+): (.+)", line)
            if m:
                name, uri = m.group(1), m.group(2).strip()
                if uri.lower().startswith("usb://"):
                    printers.append((name, uri))
    except Exception:
        pass
    return printers


def number_to_words(n):
    ones = ["","One","Two","Three","Four","Five","Six","Seven","Eight",
            "Nine","Ten","Eleven","Twelve","Thirteen","Fourteen","Fifteen",
            "Sixteen","Seventeen","Eighteen","Nineteen"]
    tens = ["","","Twenty","Thirty","Forty","Fifty","Sixty","Seventy",
            "Eighty","Ninety"]
    def two(x):
        if x < 20: return ones[x]
        return tens[x // 10] + (" " + ones[x % 10] if x % 10 else "")
    def three(x):
        if x < 100: return two(x)
        return ones[x // 100] + " Hundred" + (" " + two(x % 100) if x % 100 else "")
    def whole(x):
        if x == 0: return "Zero"
        parts = []
        crore = x // 10000000; x %= 10000000
        lakh = x // 100000; x %= 100000
        thou = x // 1000; x %= 1000
        if crore: parts.append(whole(crore) + " Crore")
        if lakh: parts.append(two(lakh) + " Lakh")
        if thou: parts.append(two(thou) + " Thousand")
        if x: parts.append(three(x))
        return " ".join(parts)
    rupees = int(n)
    paise = int(round((n - rupees) * 100))
    s = whole(rupees) + " Rupees"
    if paise: s += " and " + two(paise) + " Paise"
    return s + " Only"


def make_pdf(data, path):
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=15*mm, rightMargin=15*mm,
                            topMargin=12*mm, bottomMargin=12*mm,
                            title=f"{data['doc_type']} {data['doc_no']}",
                            author=COMPANY['name'])
    styles = getSampleStyleSheet()
    h_small = ParagraphStyle('hsm', parent=styles['Normal'], fontSize=8.5, leading=11)
    h_doc = ParagraphStyle('hd', parent=styles['Heading2'], fontSize=13,
                           alignment=1, textColor=colors.white)
    cell = ParagraphStyle('cl', parent=styles['Normal'], fontSize=8.5, leading=11)
    cell_r = ParagraphStyle('cr', parent=styles['Normal'], fontSize=8.5,
                            leading=11, alignment=2)

    story = []

    fallback = Paragraph(
        f"<b><font size=14 color='#000000'>{escape(COMPANY['name'])}</font></b><br/>"
        f"{escape(COMPANY['address'])}<br/>{escape(COMPANY['city'])}<br/>"
        f"Phone: {escape(COMPANY['phone'])} | {escape(COMPANY['email'])}<br/>"
        f"GSTIN: {escape(COMPANY['gstin'])} | State: "
        f"{escape(COMPANY['state'])} ({escape(COMPANY['state_code'])})",
        h_small)

    left = fallback
    if LOGO_FILE.exists():
        try:
            iw, ih = ImageReader(str(LOGO_FILE)).getSize()
            max_w = 120 * mm
            max_h = 22 * mm
            scale = min(max_w / iw, max_h / ih)
            target_w = iw * scale
            target_h = ih * scale
            logo_img = RLImage(str(LOGO_FILE), width=target_w, height=target_h)
            logo_img.hAlign = 'LEFT'
            company_text = Paragraph(
                f"{escape(COMPANY['address'])}<br/>"
                f"{escape(COMPANY['city'])}<br/>"
                f"Phone: {escape(COMPANY['phone'])} | {escape(COMPANY['email'])}<br/>"
                f"GSTIN: {escape(COMPANY['gstin'])} | State: "
                f"{escape(COMPANY['state'])} ({escape(COMPANY['state_code'])})",
                h_small)
            left = Table([[logo_img], [company_text]], colWidths=[130 * mm])
            left.setStyle(TableStyle([
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]))
        except Exception:
            left = fallback

    title_bar = Table([[Paragraph(data['doc_type'].upper(), h_doc)]],
                      colWidths=[40*mm])
    title_bar.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HEADER_BG),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    header = Table([[left, title_bar]], colWidths=[130*mm, 40*mm])
    header.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.append(header)
    story.append(Spacer(1, 4*mm))

    party = Paragraph(
        f"<b>To:</b><br/>{escape(data['party_name'] or '')}<br/>"
        f"{escape(data['party_address'] or '').replace(chr(10), '<br/>')}<br/>"
        f"GSTIN: {escape(data['party_gstin'] or '-')}<br/>"
        f"State: {escape(data['party_state'] or '-')}<br/>"
        f"Phone: {escape(data['party_phone'] or '-')}<br/>"
        f"Email: {escape(data['party_email'] or '-')}", cell)
    meta = Table([
        ["Doc No:", data['doc_no']],
        ["Date:", data['doc_date']],
        ["Place of Supply:", data.get('party_state') or COMPANY['state']],
    ], colWidths=[28*mm, 42*mm])
    meta.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('TEXTCOLOR', (0, 0), (0, -1), DARK),
    ]))
    box = Table([[party, meta]], colWidths=[100*mm, 70*mm])
    box.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, BOX_GRAY),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(box)
    story.append(Spacer(1, 4*mm))

    head = ["#", "Item Name", "HSN/SAC", "Qty", "Unit", "Rate", "Amount"]
    rows = [[Paragraph(f"<b>{h}</b>", cell) for h in head]]
    for i, it in enumerate(data['items'], 1):
        amt = float(it['qty']) * float(it['rate'])
        rows.append([
            Paragraph(str(i), cell),
            Paragraph(escape(it['desc'] or ''), cell),
            Paragraph(escape(it.get('hsn', '') or ''), cell),
            Paragraph(f"{float(it['qty']):g}", cell_r),
            Paragraph(escape(it.get('unit', 'Nos') or 'Nos'), cell),
            Paragraph(f"{float(it['rate']):,.2f}", cell_r),
            Paragraph(f"{amt:,.2f}", cell_r),
        ])
    items = Table(rows,
                  colWidths=[10*mm, 74*mm, 22*mm, 16*mm, 14*mm, 20*mm, 24*mm],
                  repeatRows=1)
    items.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.4, BOX_GRAY),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(items)

    totals = Table([
        ["Subtotal", f"{data['subtotal']:,.2f}"],
        ["Discount", f"-{data['discount']:,.2f}"],
        ["CGST", f"{data['cgst']:,.2f}"],
        ["SGST", f"{data['sgst']:,.2f}"],
        ["IGST", f"{data['igst']:,.2f}"],
        ["Grand Total", f"{data['total']:,.2f}"],
    ], colWidths=[40*mm, 30*mm], hAlign='RIGHT')
    totals.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEABOVE', (0, -1), (-1, -1), 0.6, BLACK),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(Spacer(1, 3*mm))
    story.append(totals)
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        f"<b>Amount in words:</b> {number_to_words(data['total'])}", h_small))

    if data.get('notes'):
        story.append(Spacer(1, 3*mm))
        story.append(Paragraph(f"<b>Notes:</b> {escape(data['notes'])}", h_small))

    bank_lines = [
        f"<b>Bank Name:</b> {escape(COMPANY.get('bank_name', ''))}",
        f"<b>Branch:</b> {escape(COMPANY.get('bank_branch', ''))}",
        f"<b>Account Name:</b> {escape(COMPANY.get('bank_account_name', ''))}",
        f"<b>Account No:</b> {escape(COMPANY.get('bank_account_no', ''))}",
        f"<b>IFSC Code:</b> {escape(COMPANY.get('bank_ifsc', ''))}",
    ]
    bank_title = Paragraph("<b>BANK DETAILS</b>", cell)
    bank_body = Paragraph("<br/>".join(bank_lines), cell)
    bank_table = Table([[bank_title], [bank_body]], colWidths=[80 * mm])
    bank_table.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEBELOW', (0, 0), (-1, 0), 0.4, BOX_GRAY),
    ]))

    terms_items = "".join(f"&bull;&nbsp; {escape(t)}<br/>" for t in TERMS)
    terms_title = Paragraph("<b>TERMS &amp; CONDITIONS</b>", cell)
    terms_body = Paragraph(f"<font size=8>{terms_items}</font>", cell)
    terms_table = Table([[terms_title], [terms_body]], colWidths=[80 * mm])
    terms_table.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEBELOW', (0, 0), (-1, 0), 0.4, BOX_GRAY),
    ]))

    side = Table([[bank_table, terms_table]], colWidths=[85 * mm, 85 * mm])
    side.setStyle(TableStyle([
        ('BOX', (0, 0), (0, 0), 0.5, BOX_GRAY),
        ('BOX', (1, 0), (1, 0), 0.5, BOX_GRAY),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(Spacer(1, 6*mm))
    story.append(side)

    story.append(Spacer(1, 10*mm))
    sign = Table([
        ["", f"For {COMPANY['name']}"],
        ["", ""],
        ["", "Authorised Signatory"],
    ], colWidths=[110*mm, 60*mm])
    sign.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 1), (-1, 1), 18),
    ]))
    story.append(sign)

    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        f"<font size=7 color='#666666'>For any queries, contact "
        f"{escape(COMPANY['email'])} | {escape(COMPANY['phone'])}</font>",
        h_small))

    story.append(Spacer(1, 2*mm))
    review = Paragraph(
        f"<font size=8 color='#000000'><b>{REVIEW_TEXT}</b> "
        f"<link href='{REVIEW_URL}' color='#000000'><u>{REVIEW_URL}</u></link>"
        f"</font>", h_small)
    review_box = Table([[review]], colWidths=[170 * mm])
    review_box.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.4, BLACK),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(review_box)

    doc.build(story)
    return path


# ============================================================
# UI THEME (modern look)
# ============================================================
BG = "#F3F1EE"
PANEL = "#FFFFFF"
INK = "#1D1D1F"
MUTE = "#6B6B70"
LINE = "#DCD8D2"
SOFT = "#FAF8F6"
RED = "#C4161C"
RED_DK = "#A31217"
GREEN = "#1F8A3B"
GREEN_DK = "#186E2E"
UI_DARK = "#222222"

FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI", 10, "bold")
FONT_S = ("Segoe UI", 9)
FONT_H = ("Segoe UI", 11, "bold")


def apply_theme(root):
    s = ttk.Style(root)
    s.theme_use("clam")
    root.configure(bg=BG)
    s.configure(".", font=FONT, background=PANEL, foreground=INK)
    s.configure("TFrame", background=PANEL)
    s.configure("Bg.TFrame", background=BG)
    s.configure("TLabel", background=PANEL, foreground=INK)
    s.configure("Mute.TLabel", foreground=MUTE, font=FONT_S)
    s.configure("Card.TLabel", background=PANEL, foreground=INK, font=FONT_H)

    for w in ("TEntry", "TCombobox"):
        s.configure(w, fieldbackground=SOFT, background=SOFT, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, padding=5)
        s.map(w, bordercolor=[("focus", RED)], lightcolor=[("focus", RED)],
              darkcolor=[("focus", RED)])
    s.map("TCombobox", fieldbackground=[("readonly", SOFT)])

    def button(name, bg, hover, fg="white"):
        s.configure(name, background=bg, foreground=fg, bordercolor=bg,
                    focusthickness=0, padding=(14, 7), font=FONT_B, relief="flat")
        s.map(name, background=[("active", hover), ("pressed", hover)])

    button("Primary.TButton", RED, RED_DK)
    button("Success.TButton", GREEN, GREEN_DK)
    button("Ghost.TButton", PANEL, SOFT, fg=INK)
    s.configure("Ghost.TButton", bordercolor=LINE, font=FONT)
    s.map("Ghost.TButton", bordercolor=[("active", RED)])

    s.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                foreground=INK, rowheight=28, borderwidth=0, font=FONT)
    s.configure("Treeview.Heading", background=UI_DARK, foreground="white",
                font=FONT_B, relief="flat", padding=6)
    s.map("Treeview.Heading", background=[("active", UI_DARK)])
    s.map("Treeview", background=[("selected", RED)],
          foreground=[("selected", "white")])


def card(parent, title):
    outer = tk.Frame(parent, bg=LINE, padx=1, pady=1)
    inner = tk.Frame(outer, bg=PANEL, padx=14, pady=12)
    inner.pack(fill="both", expand=True)

    ttk.Label(inner, text=title, style="Card.TLabel").pack(anchor="w", pady=(0, 6))

    content = tk.Frame(inner, bg=PANEL)
    content.pack(fill="both", expand=True)
    return outer, content


# ============================================================
# DIALOGS
# ============================================================
class PreviewDialog(tk.Toplevel):
    def __init__(self, master, pdf_path):
        super().__init__(master)
        self.title(f"Print Preview — {pdf_path.name}")
        self.geometry("900x760")
        self.transient(master)
        self.pdf_path = pdf_path

        info = ttk.Frame(self, padding=8)
        info.pack(fill='x')
        ttk.Label(info, text=f"File: {pdf_path}").pack(side='left')

        btns = ttk.Frame(self, padding=(8, 0, 8, 8))
        btns.pack(fill='x')

        ttk.Button(btns, text="Open in Viewer",
                   command=self._open_viewer).pack(side='left', padx=4)
        ttk.Button(btns, text="Send to Printer",
                   command=self._send_to_printer).pack(side='left', padx=4)
        ttk.Button(btns, text="Close",
                   command=self.destroy).pack(side='right', padx=4)

        self.status = ttk.Label(self,
                                text="Check the document, then click "
                                     "'Send to Printer' if it looks good.")
        self.status.pack(anchor='w', padx=8, pady=(0, 4))

        container = ttk.Frame(self)
        container.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        self.canvas = tk.Canvas(container, bg="#DDDDDD", highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self._render_pdf()
        self._open_viewer()

    def _render_pdf(self):
        try:
            from pypdfium2 import PdfDocument
            pdf = PdfDocument(str(self.pdf_path))
            page = pdf[0]
            bitmap = page.render(scale=1.5)
            pil_image = bitmap.to_pil()
            from PIL import ImageTk
            self._tk_img = ImageTk.PhotoImage(pil_image)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor='nw', image=self._tk_img)
            self.canvas.config(scrollregion=self.canvas.bbox("all"))
            self.status.config(text=f"Preview (page 1 of {len(pdf)}).")
        except Exception:
            self.canvas.delete("all")
            self.canvas.create_text(
                20, 20, anchor='nw', fill="#333333", font=FONT,
                text=("In-app preview needs pypdfium2 and Pillow.\n\n"
                      "Install: pip3 install pypdfium2 pillow"))

    def _open_viewer(self):
        try:
            if sys.platform.startswith('win'):
                os.startfile(str(self.pdf_path))
            elif sys.platform == 'darwin':
                subprocess.run(["open", str(self.pdf_path)], check=False)
            else:
                subprocess.run(["xdg-open", str(self.pdf_path)], check=False)
        except Exception as e:
            messagebox.showerror("Open", f"Could not open PDF:\n{e}", parent=self)

    def _send_to_printer(self):
        printer = getattr(self.master, "printer_var", None)
        printer = printer.get().strip() if printer else ""
        if printer:
            try:
                r = subprocess.run(["lp", "-d", printer, str(self.pdf_path)],
                                   capture_output=True, text=True, timeout=15)
                if r.returncode == 0:
                    self.status.config(text=f"Sent to USB printer: {printer}")
                else:
                    self.status.config(text=f"Print failed: {r.stderr.strip()}")
            except Exception as e:
                self.status.config(text=f"Print failed: {e}")
        else:
            self._open_viewer()
            self.status.config(text="No USB printer selected. Opened PDF.")


class SettingsDialog(tk.Toplevel):
    FIELDS = [
        ("Company Name", "name"), ("Address", "address"),
        ("City / State / PIN", "city"), ("Phone", "phone"),
        ("Email", "email"), ("GSTIN", "gstin"),
        ("State", "state"), ("State Code", "state_code"),
        ("Bank Name", "bank_name"), ("Bank Branch", "bank_branch"),
        ("Account Name", "bank_account_name"), ("Account No", "bank_account_no"),
        ("IFSC Code", "bank_ifsc"),
    ]

    def __init__(self, master, on_save):
        super().__init__(master)
        self.title("Company Settings")
        self.geometry("540x560")
        self.transient(master)
        self.grab_set()
        self.on_save = on_save
        self.vars = {}

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill='both', expand=True)

        for i, (label, key) in enumerate(self.FIELDS):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky='e',
                                            padx=(0, 8), pady=4)
            v = tk.StringVar(value=COMPANY.get(key, ""))
            self.vars[key] = v
            ttk.Entry(frm, textvariable=v, width=42).grid(
                row=i, column=1, sticky='we', pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=len(self.FIELDS), column=0, columnspan=2,
                  sticky='e', pady=(14, 0))
        ttk.Button(btns, text="Reset", command=self._reset).pack(side='left', padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side='left', padx=4)
        ttk.Button(btns, text="Save", command=self._save).pack(side='left', padx=4)

    def _reset(self):
        for k, v in self.vars.items():
            v.set(DEFAULT_COMPANY.get(k, ""))

    def _save(self):
        data = {k: v.get().strip() for k, v in self.vars.items()}
        save_company(data)
        COMPANY.clear(); COMPANY.update(data)
        self.on_save()
        messagebox.showinfo("Saved", "Company details saved.", parent=self)
        self.destroy()


class HistoryDialog(tk.Toplevel):
    def __init__(self, master, on_open):
        super().__init__(master)
        self.title("Document History")
        self.geometry("820x500")
        self.transient(master)
        self.on_open = on_open

        frm = ttk.Frame(self, padding=8)
        frm.pack(fill='both', expand=True)

        cols = ("doc_no", "doc_type", "doc_date", "party", "total")
        self.tree = ttk.Treeview(frm, columns=cols, show='headings')
        for c, w, t in zip(cols, (130, 130, 90, 300, 110),
                           ("Doc No", "Type", "Date", "Party", "Total")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor='w')
        self.tree.pack(fill='both', expand=True)

        for row in list_documents():
            doc_no, dtype, ddate, party, total = row
            self.tree.insert('', 'end', values=(
                doc_no, dtype, ddate, party or "-", f"{total or 0:,.2f}"))
        self.tree.bind("<Double-1>", self._open)

        btns = ttk.Frame(frm)
        btns.pack(fill='x', pady=(8, 0))
        ttk.Button(btns, text="Open", command=self._open).pack(side='right', padx=4)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side='right', padx=4)

    def _open(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        doc_no = self.tree.item(sel[0], 'values')[0]
        self.on_open(doc_no)
        self.destroy()


# ============================================================
# BASE APP (engine, headless of UI layout)
# ============================================================
class BillingApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1200x800")
        self.minsize(1000, 720)
        self.items = []

        try:
            if LOGO_FILE.exists():
                self._icon_img = tk.PhotoImage(file=str(LOGO_FILE))
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass

        self._build_ui()
        self._refresh_doc_no()
        self._recalc()

    # Default UI (overridden by ModernBillingApp)
    def _build_ui(self):
        ttk.Label(self, text="Loading…").pack()

    # ---------- Actions shared by all UI variants ----------
    def _refresh_customer_list(self):
        names = sorted(CUSTOMERS.keys())
        if hasattr(self, "customer_pick"):
            self.customer_pick['values'] = names
            if names and not self.customer_pick.get():
                self.customer_pick.set(names[0])

    def _refresh_usb_printers(self):
        if not hasattr(self, "printer_pick"):
            return
        printers = [name for name, _ in list_usb_printers()]
        self.printer_pick['values'] = printers
        if printers:
            self.printer_var.set(printers[0])

    def _load_customer(self, event=None):
        name = self.customer_pick.get().strip()
        if name not in CUSTOMERS:
            return
        rec = CUSTOMERS[name]
        for key, widget in self.party.items():
            val = rec.get(key, '')
            if isinstance(widget, tk.Text):
                widget.delete('1.0', 'end'); widget.insert('1.0', val)
            else:
                widget.delete(0, 'end'); widget.insert(0, val)

    def _save_current_customer(self):
        party = {}
        for k, w in self.party.items():
            party[k] = (w.get('1.0', 'end').strip()
                        if isinstance(w, tk.Text) else w.get().strip())
        if not party.get('party_name'):
            messagebox.showwarning("Missing", "Enter a Party Name first")
            return
        remember_customer(party)
        self._refresh_customer_list()
        self.customer_pick.set(party['party_name'])
        messagebox.showinfo("Saved",
                            f"Customer '{party['party_name']}' saved.")

    def _refresh_doc_no(self):
        self.doc_no.delete(0, 'end')
        self.doc_no.insert(0, next_doc_no(self.doc_type.get()))

    def _open_settings(self):
        SettingsDialog(self, on_save=lambda: messagebox.showinfo(
            "Updated", f"Company: {COMPANY['name']}"))

    def _open_history(self):
        HistoryDialog(self, on_open=self._load_by_doc_no)

    def _load_by_doc_no(self, doc_no):
        data = get_document(doc_no)
        if not data:
            messagebox.showinfo("Not Found", f"Document {doc_no} not found.")
            return
        self._apply_data(data)

    def _apply_data(self, data):
        self.doc_type.set(data.get('doc_type', DOC_TYPES[2]))
        self.doc_no.delete(0, 'end'); self.doc_no.insert(0, data.get('doc_no', ''))
        self.doc_date.delete(0, 'end'); self.doc_date.insert(0, data.get('doc_date', ''))
        for k, w in self.party.items():
            val = data.get(k, '') or ''
            if isinstance(w, tk.Text):
                w.delete('1.0', 'end'); w.insert('1.0', val)
            else:
                w.delete(0, 'end'); w.insert(0, val)
        self.items = data.get('items', [])
        self._refresh_tree()
        self.notes.delete('1.0', 'end')
        self.notes.insert('1.0', data.get('notes', ''))
        if hasattr(self, "discount"):
            self.discount.delete(0, 'end')
            self.discount.insert(0, str(data.get('discount', 0) or 0))
        self._recalc()

    def _add_item(self):
        try:
            qty = float(self.fields['qty'].get() or 0)
            rate = float(self.fields['rate'].get() or 0)
        except ValueError:
            messagebox.showerror("Invalid", "Qty and Rate must be numbers")
            return
        desc = self.fields['desc'].get().strip()
        if not desc:
            messagebox.showerror("Invalid", "Item Name required")
            return
        self.items.append({
            'desc': desc,
            'hsn': self.fields['hsn'].get().strip(),
            'qty': qty,
            'unit': self.fields['unit'].get().strip() or 'Nos',
            'rate': rate,
        })
        self._refresh_tree(); self._recalc()
        self.fields['desc'].delete(0, 'end')
        self.fields['hsn'].set("")
        self.fields['qty'].delete(0, 'end')
        self.fields['rate'].delete(0, 'end')
        self.fields['desc'].focus_set()

    def _remove_item(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        del self.items[idx]
        self._refresh_tree(); self._recalc()

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for n, it in enumerate(self.items):
            amt = float(it['qty']) * float(it['rate'])
            self.tree.insert('', 'end', values=(
                it['desc'], it.get('hsn', ''), f"{it['qty']:g}",
                it.get('unit', 'Nos'), f"{it['rate']:,.2f}", f"{amt:,.2f}"))

    def _compute(self):
        subtotal = sum(float(i['qty']) * float(i['rate']) for i in self.items)
        try: discount = float(self.discount.get() or 0)
        except ValueError: discount = 0
        try: gst = float(self.gst_rate.get() or 0)
        except ValueError: gst = 0
        taxable = max(subtotal - discount, 0)
        if self.supply.get().startswith("Intra"):
            cgst = taxable * gst / 200; sgst = cgst; igst = 0
        else:
            cgst = sgst = 0; igst = taxable * gst / 100
        total = taxable + cgst + sgst + igst
        return dict(subtotal=subtotal, discount=discount, cgst=cgst,
                    sgst=sgst, igst=igst, total=total)

    def _recalc(self):
        t = self._compute()
        if hasattr(self, "summary"):
            try:
                self.summary.config(
                    text=f"Subtotal: {t['subtotal']:,.2f}   "
                         f"Tax: {t['cgst']+t['sgst']+t['igst']:,.2f}   "
                         f"Total: {t['total']:,.2f}")
            except Exception:
                pass
        return t

    def _collect(self):
        t = self._compute()
        party = {}
        for k, w in self.party.items():
            party[k] = (w.get('1.0', 'end').strip()
                        if isinstance(w, tk.Text) else w.get().strip())
        return {
            'doc_type': self.doc_type.get(),
            'doc_no': self.doc_no.get().strip() or next_doc_no(self.doc_type.get()),
            'doc_date': self.doc_date.get().strip(),
            'items': self.items,
            'notes': self.notes.get('1.0', 'end').strip(),
            **party, **t,
        }

    def _build_pdf_only(self):
        if not self.items:
            messagebox.showwarning("Empty", "Add at least one item")
            return None
        try:
            data = self._collect()
            safe = data['doc_no'].replace('/', '_')
            pdf_path = OUTPUT_DIR / f"{safe}.pdf"
            make_pdf(data, pdf_path)
            return data, pdf_path
        except Exception as e:
            messagebox.showerror("Error", f"Could not generate PDF:\n{e}")
            return None

    def _preview(self):
        result = self._build_pdf_only()
        if not result:
            return
        _, pdf_path = result
        PreviewDialog(self, pdf_path)

    def _generate(self):
        result = self._build_pdf_only()
        if not result:
            return
        data, pdf_path = result
        save_document(data)
        if data.get('party_name'):
            try:
                remember_customer(data); self._refresh_customer_list()
            except Exception:
                pass
        messagebox.showinfo("Done", f"Generated:\n{pdf_path}")
        try:
            if sys.platform.startswith('win'):
                os.startfile(pdf_path)
            elif sys.platform == 'darwin':
                subprocess.run(["open", str(pdf_path)], check=False)
            else:
                subprocess.run(["xdg-open", str(pdf_path)], check=False)
        except Exception:
            pass

    def _print(self):
        result = self._build_pdf_only()
        if not result:
            return
        data, pdf_path = result
        save_document(data)
        printer = self.printer_var.get().strip() if hasattr(self, "printer_var") else ""
        try:
            if sys.platform.startswith('win'):
                os.startfile(str(pdf_path), "print")
            elif printer:
                subprocess.run(["lp", "-d", printer, str(pdf_path)],
                               capture_output=True, text=True, timeout=15)
            elif sys.platform == 'darwin':
                subprocess.run(["open", str(pdf_path)], check=False)
            else:
                subprocess.run(["lp", str(pdf_path)], check=False)
        except Exception as e:
            messagebox.showerror("Print", f"Could not print: {e}")


# ============================================================
# MODERN UI (inherits from BillingApp)
# ============================================================
class ModernBillingApp(BillingApp):

    def _build_ui(self):
        apply_theme(self)
        self.configure(padx=0, pady=0)

        self._build_header()
        body = ttk.Frame(self, style="Bg.TFrame", padding=(14, 12, 14, 0))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_party(body)
        self._build_items(body)
        self._build_footer()

        self._refresh_customer_list()
        self._refresh_usb_printers()

    def _build_header(self):
        bar = tk.Frame(self, bg=PANEL, padx=16, pady=8)
        bar.pack(fill="x")
        tk.Frame(self, bg=RED, height=3).pack(fill="x")

        try:
            img = tk.PhotoImage(file=str(LOGO_FILE))
            k = max(1, img.width() // 260)
            self._logo = img.subsample(k, k) if k > 1 else img
            tk.Label(bar, image=self._logo, bg=PANEL).pack(side="left")
        except Exception:
            tk.Label(bar, text=COMPANY["name"], bg=PANEL,
                     font=("Segoe UI", 16, "bold"), fg=RED).pack(side="left")

        right = tk.Frame(bar, bg=PANEL)
        right.pack(side="right")
        ttk.Button(right, text="History", style="Ghost.TButton",
                   command=self._open_history).pack(side="right", padx=(6, 0))
        ttk.Button(right, text="Settings", style="Ghost.TButton",
                   command=self._open_settings).pack(side="right", padx=(6, 0))
        ttk.Button(right, text="New", style="Ghost.TButton",
                   command=self._new_doc).pack(side="right", padx=(6, 0))

        meta = tk.Frame(bar, bg=PANEL)
        meta.pack(side="right", padx=24)
        self.doc_type = self._labeled(meta, "Type", ttk.Combobox, 0,
                                      values=DOC_TYPES, state="readonly", width=17)
        self.doc_type.current(2)
        self.doc_type.bind("<<ComboboxSelected>>", lambda e: self._refresh_doc_no())
        self.doc_no = self._labeled(meta, "Doc no", ttk.Entry, 1, width=17)
        self.doc_date = self._labeled(meta, "Date", ttk.Entry, 2, width=12)
        self.doc_date.insert(0, dt.date.today().isoformat())

    @staticmethod
    def _labeled(parent, text, cls, col, **kw):
        ttk.Label(parent, text=text, style="Mute.TLabel").grid(
            row=0, column=col, sticky="w", padx=(0 if col == 0 else 10, 0))
        w = cls(parent, **kw)
        w.grid(row=1, column=col, padx=(0 if col == 0 else 10, 0))
        return w

    def _build_party(self, body):
        outer, left = card(body, "Party details")
        outer.grid(row=0, column=0, sticky="ns", padx=(0, 12))

        ttk.Label(left, text="Saved customer", style="Mute.TLabel").pack(anchor="w")
        row = ttk.Frame(left)
        row.pack(fill="x", pady=(2, 4))
        self.customer_pick = ttk.Combobox(row, state="readonly", width=26)
        self.customer_pick.pack(side="left", fill="x", expand=True)
        self.customer_pick.bind("<<ComboboxSelected>>", self._load_customer)
        ttk.Button(row, text="↻", width=3, style="Ghost.TButton",
                   command=self._refresh_customer_list).pack(side="left", padx=(4, 0))

        self.party = {}
        for label, key, is_text in [
            ("Name", "party_name", False), ("Address", "party_address", True),
            ("GSTIN", "party_gstin", False), ("State", "party_state", False),
            ("Phone", "party_phone", False), ("Email", "party_email", False),
        ]:
            ttk.Label(left, text=label, style="Mute.TLabel").pack(anchor="w", pady=(6, 0))
            if is_text:
                w = tk.Text(left, width=30, height=4, font=FONT, bg=SOFT, fg=INK,
                            relief="flat", highlightthickness=1,
                            highlightbackground=LINE, highlightcolor=RED,
                            padx=6, pady=5, insertbackground=INK)
            else:
                w = ttk.Entry(left, width=32)
            w.pack(fill="x")
            self.party[key] = w
        self.party["party_state"].insert(0, COMPANY.get("state", ""))

        ttk.Button(left, text="Save customer", style="Ghost.TButton",
                   command=self._save_current_customer).pack(fill="x", pady=(12, 0))

    def _build_items(self, body):
        outer, right = card(body, "Line items")
        outer.grid(row=0, column=1, sticky="nsew")

        cols = ("desc", "hsn", "qty", "unit", "rate", "amount")
        wrap = ttk.Frame(right)
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", height=10)
        spec = (("Item name", 300, "w"), ("HSN", 80, "w"), ("Qty", 60, "e"),
                ("Unit", 60, "w"), ("Rate", 100, "e"), ("Amount", 110, "e"))
        for c, (t, w, a) in zip(cols, spec):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("odd", background=SOFT)
        self.tree.bind("<Delete>", lambda e: self._remove_item())

        add = tk.Frame(right, bg=PANEL)
        add.pack(fill="x", pady=(12, 0))
        self.fields = {}
        spec = [("desc", "Item name", ttk.Entry, 30, {}),
                ("hsn", "HSN", ttk.Combobox, 8, {"values": HSN_CHOICES}),
                ("qty", "Qty", ttk.Entry, 6, {}),
                ("unit", "Unit", ttk.Combobox, 7, {"values": UNIT_CHOICES}),
                ("rate", "Rate", ttk.Entry, 10, {})]
        for i, (key, label, cls, width, kw) in enumerate(spec):
            ttk.Label(add, text=label, style="Mute.TLabel").grid(
                row=0, column=i, sticky="w", padx=(0 if i == 0 else 6, 0))
            w = cls(add, width=width, **kw)
            w.grid(row=1, column=i, padx=(0 if i == 0 else 6, 0), sticky="we")
            w.bind("<Return>", lambda _e: self._add_item())
            self.fields[key] = w
        add.columnconfigure(0, weight=1)
        self.fields["unit"].set("Nos")
        ttk.Button(add, text="Add item", style="Primary.TButton",
                   command=self._add_item).grid(row=1, column=5, padx=(10, 0))
        ttk.Button(add, text="Remove", style="Ghost.TButton",
                   command=self._remove_item).grid(row=1, column=6, padx=(6, 0))

    def _build_footer(self):
        outer, bot = card(self, "Totals & notes")
        outer.pack(fill="x", padx=14, pady=12)
        bot.columnconfigure(1, weight=1)

        tax = ttk.Frame(bot)
        tax.grid(row=0, column=0, sticky="nw", padx=(0, 18))
        for i, (label, attr, val, w) in enumerate([
                ("Discount (₹)", "discount", "0", 12), ("GST %", "gst_rate", "18", 8)]):
            ttk.Label(tax, text=label, style="Mute.TLabel").grid(row=0, column=i, sticky="w", padx=(0 if i == 0 else 8, 0))
            e = ttk.Entry(tax, width=w)
            e.insert(0, val)
            e.grid(row=1, column=i, padx=(0 if i == 0 else 8, 0))
            e.bind("<KeyRelease>", lambda _e: self._recalc())
            setattr(self, attr, e)
        ttk.Label(tax, text="Supply", style="Mute.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.supply = ttk.Combobox(tax, state="readonly", width=26,
                                   values=["Intra-State (CGST+SGST)", "Inter-State (IGST)"])
        self.supply.current(0)
        self.supply.grid(row=3, column=0, columnspan=2, sticky="we")
        self.supply.bind("<<ComboboxSelected>>", lambda _e: self._recalc())

        mid = ttk.Frame(bot)
        mid.grid(row=0, column=1, sticky="nsew", padx=(0, 18))
        ttk.Label(mid, text="Notes", style="Mute.TLabel").pack(anchor="w")
        self.notes = tk.Text(mid, height=4, font=FONT, bg=SOFT, fg=INK, relief="flat",
                             highlightthickness=1, highlightbackground=LINE,
                             highlightcolor=RED, padx=6, pady=5, insertbackground=INK)
        self.notes.pack(fill="both", expand=True)

        sm = ttk.Frame(bot)
        sm.grid(row=0, column=2, sticky="ne")
        self._sum_labels = {}
        for i, (key, text) in enumerate([("sub", "Subtotal"), ("disc", "Discount"), ("tax", "Tax")]):
            ttk.Label(sm, text=text, style="Mute.TLabel").grid(row=i, column=0, sticky="w")
            v = ttk.Label(sm, text="0.00", font=FONT)
            v.grid(row=i, column=1, sticky="e", padx=(30, 0))
            self._sum_labels[key] = v
        tk.Frame(sm, bg=INK, height=2).grid(row=3, column=0, columnspan=2, sticky="we", pady=(6, 4))
        ttk.Label(sm, text="Grand total", font=FONT_B).grid(row=4, column=0, sticky="w")
        self._sum_labels["total"] = ttk.Label(sm, text="₹ 0.00", font=("Segoe UI", 16, "bold"), foreground=RED)
        self._sum_labels["total"].grid(row=4, column=1, sticky="e", padx=(30, 0))
        self.summary = self._sum_labels["total"]

        acts = ttk.Frame(bot)
        acts.grid(row=1, column=0, columnspan=3, sticky="we", pady=(12, 0))
        ttk.Button(acts, text="Print", style="Primary.TButton",
                   command=self._print).pack(side="right", padx=(6, 0))
        ttk.Button(acts, text="Generate PDF", style="Success.TButton",
                   command=self._generate).pack(side="right", padx=(6, 0))
        ttk.Button(acts, text="Preview", style="Ghost.TButton",
                   command=self._preview).pack(side="right", padx=(6, 0))
        self.printer_var = tk.StringVar()
        self.printer_pick = ttk.Combobox(acts, textvariable=self.printer_var,
                                         state="readonly", width=22)
        self.printer_pick.pack(side="right", padx=(0, 10))
        ttk.Label(acts, text="USB printer", style="Mute.TLabel").pack(side="right", padx=(0, 6))

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for n, it in enumerate(self.items):
            amt = float(it["qty"]) * float(it["rate"])
            self.tree.insert("", "end", tags=("odd",) if n % 2 else (), values=(
                it["desc"], it.get("hsn", ""), f"{it['qty']:g}",
                it.get("unit", "Nos"), f"{it['rate']:,.2f}", f"{amt:,.2f}"))

    def _recalc(self):
        t = self._compute()
        if hasattr(self, "_sum_labels"):
            self._sum_labels["sub"].config(text=f"{t['subtotal']:,.2f}")
            self._sum_labels["disc"].config(text=f"-{t['discount']:,.2f}")
            self._sum_labels["tax"].config(text=f"{t['cgst'] + t['sgst'] + t['igst']:,.2f}")
            self._sum_labels["total"].config(text=f"₹ {t['total']:,.2f}")
        return t

    def _new_doc(self):
        self.items = []
        self._refresh_tree()
        for key, w in self.party.items():
            if isinstance(w, tk.Text):
                w.delete("1.0", "end")
            else:
                w.delete(0, "end")
        self.party["party_state"].insert(0, COMPANY.get("state", ""))
        self.notes.delete("1.0", "end")
        self.discount.delete(0, "end")
        self.discount.insert(0, "0")
        self.doc_date.delete(0, "end")
        self.doc_date.insert(0, dt.date.today().isoformat())
        self._refresh_doc_no()
        self._recalc()


def main():
    init_db()
    ModernBillingApp().mainloop()


if __name__ == "__main__":
    main()

import os, sys, json, sqlite3, subprocess, datetime as dt, re
from pathlib import Path
from xml.sax.saxutils import escape
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

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
}

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
            target_h = 16 * mm
            target_w = target_h * (iw / ih)
            if target_w > 80 * mm:
                target_w = 80 * mm
                target_h = target_w * (ih / iw)
            logo_img = RLImage(str(LOGO_FILE), width=target_w, height=target_h)
            logo_img.hAlign = 'LEFT'
            company_text = Paragraph(
                f"{escape(COMPANY['address'])}<br/>"
                f"{escape(COMPANY['city'])}<br/>"
                f"Phone: {escape(COMPANY['phone'])} | {escape(COMPANY['email'])}<br/>"
                f"GSTIN: {escape(COMPANY['gstin'])} | State: "
                f"{escape(COMPANY['state'])} ({escape(COMPANY['state_code'])})",
                h_small)
            left = Table([[logo_img], [company_text]], colWidths=[95 * mm])
            left.setStyle(TableStyle([
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]))
        except Exception:
            left = fallback

    title_bar = Table([[Paragraph(data['doc_type'].upper(), h_doc)]],
                      colWidths=[70*mm])
    title_bar.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HEADER_BG),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    header = Table([[left, title_bar]], colWidths=[100*mm, 70*mm])
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

    story.append(Spacer(1, 12*mm))
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


class PreviewDialog(tk.Toplevel):
    def __init__(self, master, pdf_path):
        super().__init__(master)
        self.title(f"Print Preview — {pdf_path.name}")
        self.geometry("900x760")
        self.transient(master)
        self.pdf_path = pdf_path

        info = ttk.Frame(self, padding=8)
        info.pack(fill='x')
        ttk.Label(info, text=f"File: {pdf_path}",
                  font=('Segoe UI', 9)).pack(side='left')

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
                                     "'Send to Printer' if it looks good.",
                                foreground="#555555")
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
            self.status.config(
                text=f"Preview (page 1 of {len(pdf)}). "
                     f"Click 'Send to Printer' when ready.")
        except Exception:
            self.canvas.delete("all")
            self.canvas.create_text(
                20, 20, anchor='nw', fill="#333333",
                font=('Segoe UI', 10),
                text=("In-app preview needs pypdfium2 and Pillow.\n\n"
                      "Install with:\n"
                      "    pip3 install pypdfium2 pillow\n\n"
                      "Meanwhile, the PDF is already open in your viewer."))

    def _open_viewer(self):
        try:
            if sys.platform.startswith('win'):
                os.startfile(str(self.pdf_path))
            elif sys.platform == 'darwin':
                subprocess.run(["open", str(self.pdf_path)], check=False)
            else:
                subprocess.run(["xdg-open", str(self.pdf_path)], check=False)
        except Exception as e:
            messagebox.showerror("Open", f"Could not open PDF:\n{e}",
                                 parent=self)

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
        ("Company Name", "name"),
        ("Address", "address"),
        ("City / State / PIN", "city"),
        ("Phone", "phone"),
        ("Email", "email"),
        ("GSTIN", "gstin"),
        ("State", "state"),
        ("State Code", "state_code"),
    ]

    def __init__(self, master, on_save):
        super().__init__(master)
        self.title("Company Settings")
        self.geometry("520x420")
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

    def _build_ui(self):
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill='x')

        ttk.Label(bar, text="Type:").pack(side='left')
        self.doc_type = ttk.Combobox(bar, values=DOC_TYPES, state='readonly',
                                     width=18)
        self.doc_type.current(2)
        self.doc_type.pack(side='left', padx=4)
        self.doc_type.bind('<<ComboboxSelected>>', lambda e: self._refresh_doc_no())

        ttk.Label(bar, text="Doc No:").pack(side='left', padx=(12, 0))
        self.doc_no = ttk.Entry(bar, width=18)
        self.doc_no.pack(side='left', padx=4)

        ttk.Label(bar, text="Date:").pack(side='left', padx=(12, 0))
        self.doc_date = ttk.Entry(bar, width=12)
        self.doc_date.insert(0, dt.date.today().isoformat())
        self.doc_date.pack(side='left', padx=4)

        ttk.Button(bar, text="Settings",
                   command=self._open_settings).pack(side='right', padx=4)
        ttk.Button(bar, text="History",
                   command=self._open_history).pack(side='right', padx=4)

        body = ttk.Frame(self, padding=8)
        body.pack(fill='both', expand=True)

        left = ttk.LabelFrame(body, text="Party Details", padding=8)
        left.pack(side='left', fill='y', padx=(0, 8))

        cm = ttk.Frame(left)
        cm.pack(fill='x', pady=(0, 6))
        ttk.Label(cm, text="Saved Customer:").pack(anchor='w')
        pick_row = ttk.Frame(cm)
        pick_row.pack(fill='x')
        self.customer_pick = ttk.Combobox(pick_row, state='readonly', width=28)
        self.customer_pick.pack(side='left', fill='x', expand=True)
        self.customer_pick.bind('<<ComboboxSelected>>', self._load_customer)
        ttk.Button(pick_row, text="↻", width=3,
                   command=self._refresh_customer_list).pack(side='left', padx=(4, 0))

        self.party = {}
        for label, key, is_text in [
            ("Name", "party_name", False),
            ("Address", "party_address", True),
            ("GSTIN", "party_gstin", False),
            ("State", "party_state", False),
            ("Phone", "party_phone", False),
            ("Email", "party_email", False),
        ]:
            ttk.Label(left, text=label).pack(anchor='w', pady=(4, 0))
            if is_text:
                w = tk.Text(left, width=32, height=4)
                w.pack(fill='x')
            else:
                w = ttk.Entry(left, width=34)
                w.pack(fill='x')
            self.party[key] = w

        right = ttk.LabelFrame(body, text="Line Items", padding=8)
        right.pack(side='left', fill='both', expand=True)

        cols = ("desc", "hsn", "qty", "unit", "rate", "amount")
        self.tree = ttk.Treeview(right, columns=cols, show='headings', height=12)
        for c, w, t in zip(cols, (280, 80, 60, 60, 90, 100),
                           ("Item Name", "HSN", "Qty", "Unit", "Rate", "Amount")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor='w')
        self.tree.pack(fill='both', expand=True)

        add = ttk.Frame(right)
        add.pack(fill='x', pady=(8, 0))
        self.fields = {}

        ttk.Label(add, text="Item Name:").pack(side='left')
        e = ttk.Entry(add, width=26); e.pack(side='left', padx=2)
        self.fields['desc'] = e

        ttk.Label(add, text="HSN:").pack(side='left')
        h = ttk.Combobox(add, width=8, values=HSN_CHOICES); h.pack(side='left', padx=2)
        self.fields['hsn'] = h

        ttk.Label(add, text="Qty:").pack(side='left')
        q = ttk.Entry(add, width=6); q.pack(side='left', padx=2)
        self.fields['qty'] = q

        ttk.Label(add, text="Unit:").pack(side='left')
        u = ttk.Combobox(add, width=6, values=UNIT_CHOICES); u.set("Nos")
        u.pack(side='left', padx=2)
        self.fields['unit'] = u

        ttk.Label(add, text="Rate:").pack(side='left')
        r = ttk.Entry(add, width=8); r.pack(side='left', padx=2)
        self.fields['rate'] = r

        for w in self.fields.values():
            w.bind("<Return>", lambda _e: self._add_item())

        ttk.Button(add, text="Add",
                   command=self._add_item).pack(side='left', padx=4)
        ttk.Button(add, text="Remove",
                   command=self._remove_item).pack(side='left', padx=2)
        ttk.Button(add, text="Save Customer",
                   command=self._save_current_customer).pack(side='left', padx=8)

        bot = ttk.LabelFrame(self, text="Totals & Notes", padding=8)
        bot.pack(fill='x', padx=8, pady=(0, 8))

        r1 = ttk.Frame(bot); r1.pack(fill='x')
        ttk.Label(r1, text="Discount:").pack(side='left')
        self.discount = ttk.Entry(r1, width=10); self.discount.insert(0, "0")
        self.discount.pack(side='left', padx=4)

        ttk.Label(r1, text="GST %:").pack(side='left', padx=(12, 0))
        self.gst_rate = ttk.Entry(r1, width=6); self.gst_rate.insert(0, "18")
        self.gst_rate.pack(side='left', padx=4)

        ttk.Label(r1, text="Supply:").pack(side='left', padx=(12, 0))
        self.supply = ttk.Combobox(r1, state='readonly', width=22,
                                   values=["Intra-State (CGST+SGST)",
                                           "Inter-State (IGST)"])
        self.supply.current(0)
        self.supply.pack(side='left', padx=4)

        ttk.Button(r1, text="Recalculate",
                   command=self._recalc).pack(side='left', padx=8)
        ttk.Button(r1, text="Preview",
                   command=self._preview).pack(side='right', padx=4)
        ttk.Button(r1, text="Generate PDF",
                   command=self._generate).pack(side='right', padx=4)
        ttk.Button(r1, text="Print",
                   command=self._print).pack(side='right', padx=4)
        ttk.Label(r1, text="USB Printer:").pack(side='right', padx=(12, 2))
        self.printer_var = tk.StringVar()
        self.printer_pick = ttk.Combobox(r1, textvariable=self.printer_var,
                                         state='readonly', width=20)
        self.printer_pick.pack(side='right', padx=2)

        ttk.Label(bot, text="Notes:").pack(anchor='w', pady=(8, 0))
        self.notes = tk.Text(bot, height=3)
        self.notes.pack(fill='x')

        self.summary = ttk.Label(bot,
                                 text="Subtotal: 0.00   Tax: 0.00   Total: 0.00",
                                 font=('Segoe UI', 10, 'bold'))
        self.summary.pack(anchor='w', pady=(8, 0))

        self._refresh_customer_list()
        self._refresh_usb_printers()

    def _refresh_customer_list(self):
        names = sorted(CUSTOMERS.keys())
        self.customer_pick['values'] = names
        if names and not self.customer_pick.get():
            self.customer_pick.set(names[0])

    def _refresh_usb_printers(self):
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
                widget.delete('1.0', 'end')
                widget.insert('1.0', val)
            else:
                widget.delete(0, 'end')
                widget.insert(0, val)

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
        self.doc_no.delete(0, 'end')
        self.doc_no.insert(0, data.get('doc_no', ''))
        self.doc_date.delete(0, 'end')
        self.doc_date.insert(0, data.get('doc_date', ''))
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
        for it in self.items:
            amt = float(it['qty']) * float(it['rate'])
            self.tree.insert('', 'end', values=(
                it['desc'], it.get('hsn', ''), f"{it['qty']:g}",
                it.get('unit', 'Nos'), f"{it['rate']:.2f}", f"{amt:.2f}"))

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
        self.summary.config(
            text=f"Subtotal: {t['subtotal']:,.2f}   "
                 f"Tax: {t['cgst']+t['sgst']+t['igst']:,.2f}   "
                 f"Total: {t['total']:,.2f}")
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
                remember_customer(data)
                self._refresh_customer_list()
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
        printer = self.printer_var.get().strip()
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


def main():
    init_db()
    app = BillingApp()
    app.mainloop()


if __name__ == "__main__":
    main()

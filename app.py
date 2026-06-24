# region ── IMPORTS ─────────────────────────────────────────────
from flask import Flask, render_template, request, jsonify, session, Response
import os
import json
import re
import requests
import sqlite3
from datetime import datetime, date
# endregion

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bagelalley-secret-change-me")

# region ── SECURITY: proxy, rate limiting, validatie-helpers ────
import hmac
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Railway zit achter een reverse proxy: vertrouw 1 proxy-hop zodat we het
# echte client-IP zien (nodig voor correcte rate limiting) en de juiste scheme.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Rate limiter op ALLE endpoints (default), met strengere limieten per route.
# In-memory volstaat voor 1 instance; voor meerdere workers/instances:
# zet RATELIMIT_STORAGE_URI naar bijv. een Redis-URL.
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["300 per hour", "60 per minute"],
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
    strategy="fixed-window",
)

@limiter.request_filter
def _rate_limit_exempt():
    # Statische bestanden en infra-endpoints niet limiteren (anders breken
    # asset-loads en health checks). Echte endpoints blijven wél begrensd.
    return request.endpoint in {"static", "ping", "robots_txt", "sitemap_xml"}

def _safe_eq(a, b) -> bool:
    """Constant-time string-vergelijking (tegen timing-attacks)."""
    return hmac.compare_digest(str(a), str(b))

def _clean_str(value, maxlen: int) -> str:
    """Strip + harde lengtelimiet voor vrije-tekst input."""
    return str(value or "").strip()[:maxlen]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _valid_iso_date(s: str):
    """Geeft een geldige ISO-datum terug, of None bij ongeldige invoer."""
    try:
        return date.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None

@app.errorhandler(429)
def _ratelimit_handler(e):
    return jsonify({
        "succes": False,
        "antwoord": "Te veel verzoeken — wacht even en probeer het zo opnieuw.",
        "fout": "Te veel verzoeken — wacht even en probeer het zo opnieuw.",
    }), 429
# endregion

# region ── SENDGRID MAIL ───────────────────────────────────────
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
MAIL_VAN         = os.environ.get("MAIL_VAN", "jorisvrr@gmail.com")
MAIL_VAN_NAAM    = "Bagel Alley"

def datum_nl(datum_str: str) -> str:
    """Zet 2026-06-08 om naar 'maandag 8 juni 2026'."""
    from datetime import date as dt
    dagen   = ["maandag","dinsdag","woensdag","donderdag","vrijdag","zaterdag","zondag"]
    maanden = ["januari","februari","maart","april","mei","juni",
               "juli","augustus","september","oktober","november","december"]
    d = dt.fromisoformat(datum_str)
    return f"{dagen[d.weekday()]} {d.day} {maanden[d.month - 1]} {d.year}"

def mail_html(naam: str, datum_str: str, tijd: str, gasten: int) -> str:
    datum_display = datum_nl(datum_str)
    voornaam = naam.split()[0]
    return f"""<!DOCTYPE html>
<html lang="nl">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Reservering bevestigd — Bagel Alley</title></head>
<body style="margin:0;padding:0;background:#f5f5f0;font-family:'Georgia',serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f5f5f0;">
<tr><td align="center" style="padding:32px 16px;">
<table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;">

  <!-- HERO -->
  <tr><td style="background:#8758ce;border-radius:16px 16px 0 0;padding:48px 48px 40px;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:36px;">
      <tr>
        <td>
          <table cellpadding="0" cellspacing="0" border="0"><tr>
            <td style="width:44px;height:44px;background:rgba(255,255,255,0.25);border-radius:50%;text-align:center;vertical-align:middle;">
              <span style="color:#fff;font-family:Georgia,serif;font-size:11px;font-weight:bold;line-height:44px;display:block;">BA</span>
            </td>
            <td style="padding-left:12px;">
              <div style="color:#fff;font-size:16px;font-weight:bold;">Bagel Alley</div>
              <div style="color:rgba(255,255,255,0.65);font-size:12px;font-style:italic;">Wassenaar</div>
            </td>
          </tr></table>
        </td>
        <td style="text-align:right;vertical-align:top;">
          <span style="background:rgba(255,255,255,0.15);color:#fff;font-family:Arial,sans-serif;font-size:11px;letter-spacing:1px;padding:5px 12px;border-radius:20px;text-transform:uppercase;">Reservering</span>
        </td>
      </tr>
    </table>
    <div style="color:rgba(255,255,255,0.6);font-family:Arial,sans-serif;font-size:12px;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px;">Bevestigd</div>
    <div style="color:#fff;font-size:48px;font-weight:bold;line-height:1;margin-bottom:6px;">{voornaam},<br>je tafel<br>staat klaar.</div>
    <div style="color:rgba(255,255,255,0.75);font-size:15px;font-style:italic;margin-top:16px;">We kijken ernaar uit je te verwelkomen.</div>
  </td></tr>

  <!-- DETAILS -->
  <tr><td style="background:#fff;padding:0 48px;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px solid #f0eaf8;padding:36px 0;">
      <tr>
        <td style="width:50%;padding-right:24px;border-right:1px solid #f0eaf8;">
          <div style="font-family:Arial,sans-serif;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#c9a8f0;margin-bottom:6px;">Datum</div>
          <div style="font-size:22px;font-weight:bold;color:#1e1e2e;line-height:1.2;">{datum_display}</div>
        </td>
        <td style="width:50%;padding-left:24px;">
          <div style="font-family:Arial,sans-serif;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#c9a8f0;margin-bottom:6px;">Tijdstip</div>
          <div style="font-size:28px;font-weight:bold;color:#1e1e2e;">{tijd}</div>
        </td>
      </tr>
    </table>
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="padding:28px 0;border-bottom:1px solid #f0eaf8;">
      <tr>
        <td style="width:50%;padding-right:24px;">
          <div style="font-family:Arial,sans-serif;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#c9a8f0;margin-bottom:6px;">Naam</div>
          <div style="font-size:18px;font-weight:bold;color:#1e1e2e;">{naam}</div>
        </td>
        <td style="width:50%;padding-left:24px;">
          <div style="font-family:Arial,sans-serif;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#c9a8f0;margin-bottom:6px;">Gasten</div>
          <div style="font-size:18px;font-weight:bold;color:#1e1e2e;">{gasten} {'persoon' if gasten == 1 else 'personen'}</div>
        </td>
      </tr>
    </table>
  </td></tr>

  <!-- LOCATIE -->
  <tr><td style="background:#f3eeff;padding:28px 48px;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
      <td>
        <div style="font-family:Arial,sans-serif;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#8758ce;margin-bottom:6px;">Locatie</div>
        <div style="font-size:16px;font-weight:bold;color:#1e1e2e;">Langstraat 42, Wassenaar</div>
        <div style="font-family:Arial,sans-serif;font-size:12px;color:#888;margin-top:3px;">Parkeren mogelijk in de omgeving</div>
      </td>
      <td style="text-align:right;">
        <a href="https://maps.google.com/?q=Langstraat+42+Wassenaar"
           style="display:inline-block;background:#8758ce;color:#fff;font-family:Arial,sans-serif;font-size:13px;font-weight:bold;text-decoration:none;padding:11px 22px;border-radius:8px;">
          Route plannen
        </a>
      </td>
    </tr></table>
  </td></tr>

  <!-- SFEER -->
  <tr><td style="background:#fff;padding:36px 48px;">
    <div style="font-family:Arial,sans-serif;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#c9a8f0;margin-bottom:10px;">Wat je kunt verwachten</div>
    <div style="font-size:20px;font-weight:bold;color:#1e1e2e;margin-bottom:12px;">Vers gebakken bagels, elke dag.</div>
    <div style="font-family:Arial,sans-serif;font-size:13px;color:#888;line-height:1.7;margin-bottom:16px;">
      Van hartige classics tot zoete creaties — onze bagels worden elke ochtend vers gebakken. Neem de tijd, geniet van de sfeer.
    </div>
    <a href="https://www.bagelalley.nl/menu-takeaway"
       style="font-family:Arial,sans-serif;font-size:13px;font-weight:bold;color:#8758ce;text-decoration:none;border-bottom:1px solid #c9a8f0;padding-bottom:2px;">
      Bekijk het menu →
    </a>
  </td></tr>

  <!-- OPENINGSTIJDEN -->
  <tr><td style="background:#1e1e2e;padding:32px 48px;">
    <div style="font-family:Arial,sans-serif;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#8758ce;margin-bottom:16px;">Openingstijden</div>
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="font-family:Arial,sans-serif;font-size:13px;color:#aaa;padding-bottom:6px;">Ma · Wo · Do · Vr · Za · Zo</td>
        <td style="font-family:Arial,sans-serif;font-size:13px;color:#fff;font-weight:bold;text-align:right;padding-bottom:6px;">08:00 – 16:00</td>
      </tr>
      <tr>
        <td style="font-family:Arial,sans-serif;font-size:13px;color:#aaa;">Dinsdag</td>
        <td style="font-family:Arial,sans-serif;font-size:13px;color:#555;text-align:right;">Gesloten</td>
      </tr>
    </table>
  </td></tr>

  <!-- SPAM TIP -->
  <tr><td style="background:#f3eeff;padding:12px 48px;text-align:center;">
    <div style="font-family:Arial,sans-serif;font-size:11px;color:#8758ce;">
      📬 Staat deze mail in je spam? Markeer hem als 'Geen spam' om toekomstige mails te ontvangen.
    </div>
  </td></tr>

  <!-- FOOTER -->
  <tr><td style="background:#1e1e2e;border-top:1px solid rgba(255,255,255,0.06);border-radius:0 0 16px 16px;padding:24px 48px;text-align:center;">
    <div style="font-family:Arial,sans-serif;font-size:12px;color:#555;line-height:1.8;">
      <a href="https://www.bagelalley.nl" style="color:#8758ce;text-decoration:none;font-weight:bold;">bagelalley.nl</a>
      &nbsp;·&nbsp;
      <a href="tel:0705146116" style="color:#555;text-decoration:none;">070-514 61 16</a>
      &nbsp;·&nbsp;
      <a href="mailto:info@bagelalley.nl" style="color:#555;text-decoration:none;">info@bagelalley.nl</a>
    </div>
    <div style="font-family:Arial,sans-serif;font-size:11px;color:#333;margin-top:8px;">Langstraat 42, 2242 KM Wassenaar</div>
  </td></tr>

</table></td></tr></table>
</body></html>"""

def stuur_bevestigingsmail(naam: str, email: str, datum: str, tijd: str, gasten: int):
    """Stuurt HTML bevestigingsmail via SendGrid REST API (requests, SSL verify uitgeschakeld voor Mac)."""
    try:
        import requests as req_lib
        datum_display = datum_nl(datum)
        payload = {
            "personalizations": [{"to": [{"email": email}]}],
            "from": {"email": MAIL_VAN, "name": MAIL_VAN_NAAM},
            "reply_to": {"email": "info@bagelalley.nl"},
            "subject": f"Reservering bevestigd — {datum_display} om {tijd} · Bagel Alley",
            "content": [{"type": "text/html", "value": mail_html(naam, datum, tijd, gasten)}]
        }
        resp = req_lib.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=payload,
            headers={"Authorization": f"Bearer {SENDGRID_API_KEY}", "Content-Type": "application/json"},
            verify=False,
            timeout=10
        )
        print(f"[MAIL] Verstuurd naar {email} | status {resp.status_code}")
        if resp.status_code not in (200, 202):
            print(f"[MAIL FOUT] {resp.text}")
    except Exception as e:
        print(f"[MAIL FOUT] {e}")
# endregion = os.environ.get("SECRET_KEY", "bagelalley-secret-change-me")

# region ── DATABASE SETUP ─────────────────────────────────────
DB_PATH = "boekingen.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS boekingen (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                naam      TEXT    NOT NULL,
                email     TEXT    NOT NULL,
                gasten    INTEGER NOT NULL,
                datum     TEXT    NOT NULL,
                tijd      TEXT    NOT NULL,
                telefoon  TEXT    DEFAULT '',
                aangemaakt TEXT   DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()

init_db()

ALLE_SLOTS = [
    "08:00","08:30","09:00","09:30",
    "10:00","10:30","11:00","11:30",
    "12:00","12:30","13:00","13:30",
    "14:00","14:30","15:00","15:30"
]

def get_boekingen_voor_datum(datum: str) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM boekingen WHERE datum = ? ORDER BY tijd",
            (datum,)
        ).fetchall()
    return [dict(r) for r in rows]

def get_volle_slots(datum: str) -> list:
    # Alleen actieve (niet-geannuleerde) boekingen blokkeren een slot
    return [b["tijd"] for b in get_boekingen_voor_datum(datum)
            if not b.get("geannuleerd")]

def bereken_stats(datum: str) -> dict:
    boekingen = get_boekingen_voor_datum(datum)
    volle_slots = get_volle_slots(datum)
    return {
        "totaal": len(boekingen),
        "gasten": sum(b["gasten"] for b in boekingen),
        "vrij":   len(ALLE_SLOTS) - len(volle_slots)
    }
# endregion

# region ── BEDRIJFSINSTELLINGEN ────────────────────────────────
# Pas dit blok aan voor elk nieuw bedrijf
BEDRIJF = {
    "naam": "Bagel Alley",
    "avatar_naam": "Sonja",
    "avatar_initialen": "SO",
    "telefoon": "070-514 61 16",
    "email": "info@bagelalley.nl",
    "adres": "Langstraat 42, 2242 KM Wassenaar",
    "openingstijden": {
        0: ("08:00", "16:00"),   # maandag
        1: None,                  # dinsdag gesloten
        2: ("08:00", "16:00"),   # woensdag
        3: ("08:00", "16:00"),   # donderdag
        4: ("08:00", "16:00"),   # vrijdag
        5: ("08:00", "16:00"),   # zaterdag
        6: ("08:00", "16:00"),   # zondag
    }
}
# endregion

# region ── SEO / STRUCTURED DATA ───────────────────────────────
# Canonieke domein. Zet desnoods de env var SITE_URL als je een ander
# (sub)domein gebruikt, bijv. https://bagelalley.up.railway.app
SITE_URL = os.environ.get("SITE_URL", "https://bagelalley.nl").rstrip("/")

# Geo-coordinaten van de zaak — controleer deze in Google Maps en pas
# ze hier aan als ze niet exact kloppen (rechtsklik op de pin → coordinaten).
GEO = {"lat": 52.1463, "lng": 4.4012}

# (Optioneel) social profielen van de zaak — vul aan voor 'sameAs'.
SOCIAL = [
    # "https://www.instagram.com/bagelalley/",
    # "https://www.facebook.com/bagelalley/",
]

_DAGEN = {0: "Monday", 1: "Tuesday", 2: "Wednesday",
          3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday"}

def _tel_e164(nummer: str) -> str:
    digits = re.sub(r"\D", "", nummer)
    if digits.startswith("0"):
        digits = "31" + digits[1:]
    return "+" + digits

def build_restaurant_schema() -> str:
    spec = []
    for idx, val in BEDRIJF["openingstijden"].items():
        if val:
            spec.append({
                "@type": "OpeningHoursSpecification",
                "dayOfWeek": "https://schema.org/" + _DAGEN[idx],
                "opens": val[0],
                "closes": val[1],
            })
    schema = {
        "@context": "https://schema.org",
        "@type": "Restaurant",
        "name": BEDRIJF["naam"],
        "image": SITE_URL + "/static/og-image.jpg",
        "logo": SITE_URL + "/static/favicon/android-chrome-512x512.png",
        "url": SITE_URL,
        "telephone": _tel_e164(BEDRIJF["telefoon"]),
        "email": BEDRIJF["email"],
        "priceRange": "\u20ac\u20ac",
        "servesCuisine": ["Bagels", "Breakfast", "Lunch", "High Tea", "Tapas", "Koffie"],
        "acceptsReservations": True,
        "hasMenu": SITE_URL + "/menu",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "Langstraat 42",
            "postalCode": "2242 KM",
            "addressLocality": "Wassenaar",
            "addressRegion": "Zuid-Holland",
            "addressCountry": "NL",
        },
        "geo": {
            "@type": "GeoCoordinates",
            "latitude": GEO["lat"],
            "longitude": GEO["lng"],
        },
        "openingHoursSpecification": spec,
    }
    if SOCIAL:
        schema["sameAs"] = SOCIAL
    return json.dumps(schema, ensure_ascii=False)

@app.context_processor
def inject_seo_globals():
    return {
        "site_url": SITE_URL,
        "restaurant_schema_json": build_restaurant_schema(),
    }

@app.route("/robots.txt")
def robots_txt():
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n\n"
        "Sitemap: " + SITE_URL + "/sitemap.xml\n"
    )
    return Response(body, mimetype="text/plain")

@app.route("/sitemap.xml")
def sitemap_xml():
    vandaag = date.today().isoformat()
    paginas = [
        ("/", "1.0"),
        ("/menu", "0.9"),
        ("/shop", "0.9"),
        ("/reserveren", "0.8"),
        ("/bestellen", "0.7"),
        ("/jobs", "0.5"),
    ]
    items = "".join(
        "<url><loc>{0}{1}</loc><lastmod>{2}</lastmod><changefreq>weekly</changefreq>"
        "<priority>{3}</priority></url>".format(SITE_URL, pad, vandaag, prio)
        for pad, prio in paginas
    )
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + items + "</urlset>")
    return Response(xml, mimetype="application/xml")
# endregion

# region ── LINK HULPFUNCTIE ────────────────────────────────────
def link(tekst, url):
    """Maakt een klikbare HTML link met huisstijl."""
    return f'<a href="{url}" target="_blank" style="color:#8758ce;font-weight:600;text-decoration:none;border-bottom:1px solid #c9a8f0;">{tekst}</a>'
# endregion

# region ── KENNISBANK ──────────────────────────────────────────
# Alle antwoorden van de chatbot — vul per bedrijf in
KENNISBANK = {
    "openingstijden": f'We zijn open van maandag en woensdag t/m zondag van 08:00 tot 16:00. Op dinsdag zijn wij gesloten. Meer info vind je {link("hier", "https://www.bagelalley.nl")}.',
    "adres":          f'Je vindt ons op Langstraat 42, 2242 KM Wassenaar. Plan je route {link("hier", "https://maps.google.com/?q=Langstraat+42+Wassenaar")}.',
    "contact":        f'Bel ons op 070-514 61 16 of neem contact op via {link("ons contactformulier", "https://www.bagelalley.nl/contact")}.',
    "menu":           f'We serveren vers ontbijt en lunch met zelfgemaakte bagels, sandwiches en salades. Bekijk het volledige menu {link("hier", "https://www.bagelalley.nl/menu-takeaway")}.',
    "bagels":         f'Onze bagels zijn zelfgemaakt met verse ingrediënten, elke dag vers gebakken. Bekijk alle opties {link("hier", "https://www.bagelalley.nl/menu-takeaway")}.',
    "high_tea":       f'Onze High Tea is perfect voor een bijzondere gelegenheid — de Take Away box is €39,50 voor 2 personen. Bekijk ons aanbod {link("hier", "https://www.bagelalley.nl/high-tea")}.',
    "tapas":          f'We hebben een heerlijk tapas aanbod! Bekijk de tapaskaart {link("hier", "https://www.bagelalley.nl/tapas")}.',
    "takeaway":       f'Takeaway is zeker mogelijk! Bestel online via onze webshop — ga {link("hier", "https://www.bagelalley.nl/online-shop")} naartoe.',
    "shop":           f'Online bestellen kan via onze webshop. Klik {link("hier", "https://www.bagelalley.nl/online-shop")} voor pie & box orders.',
    "cadeaubon":      f'Cadeaubonnen zijn verkrijgbaar vanaf €5,00, af te halen in de zaak. Meer info vind je {link("hier", "https://www.bagelalley.nl/cadeaubon")}.',
    "parking":        f'In Wassenaar zijn er parkeerplekken in de omgeving van de Langstraat. Bekijk de route {link("hier", "https://maps.google.com/?q=Langstraat+42+Wassenaar")}.',
    "allergieen":     f'Voor vragen over allergenen of dieetwensen neem je contact op via {link("dit formulier", "https://www.bagelalley.nl/contact")} of bel 070-514 61 16.',
    "groepen":        f'Voor groepen of speciale gelegenheden bel je ons op 070-514 61 16 of stuur een bericht via {link("onze contactpagina", "https://www.bagelalley.nl/contact")}.',
}
# endregion

# region ── CLAUDE AI INSTELLINGEN ─────────────────────────────
# Zet API key als omgevingsvariabele: export ANTHROPIC_API_KEY=sk-ant-...
# Zonder key: fallback naar keyword-matching
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SYSTEM_PROMPT = f"""Je bent Sonja, de eigenares van Bagel Alley in Wassenaar. Je beantwoordt vragen van gasten op een warme, persoonlijke manier.

Bagel Alley is een gezellig ontbijt- en lunchcafé met verse, zelfgemaakte bagels, Amerikaans-geïnspireerd eten en een warme sfeer.

Kennisbank:
{json.dumps(KENNISBANK, ensure_ascii=False, indent=2)}

Bedrijfsinfo:
- Naam: Bagel Alley
- Telefoon: 070-514 61 16
- Adres: Langstraat 42, 2242 KM Wassenaar
- Website: https://www.bagelalley.nl
- Open: ma, wo, do, vr, za, zo van 08:00-16:00 | dinsdag gesloten

Regels:
- Antwoord ALTIJD in dezelfde taal als de gebruiker
- Wees warm, informeel en enthousiast — praat als een echte eigenares die haar zaak kent
- Kort en to-the-point (max 2-3 zinnen)
- Voeg altijd een klikbare HTML link toe als er een relevante pagina is, gebruik dit format exact:
  <a href="https://..." target="_blank" style="color:#8758ce;font-weight:600;text-decoration:none;border-bottom:1px solid #c9a8f0;">ankertekst</a>
  Gebruik ankerteksten zoals "hier", "ons menu", "het formulier" — schrijf NOOIT de URL uit
- Als je iets niet weet, verwijs naar 070-514 61 16
- Geen markdown, geen opsommingstekens — gewone tekst
- Als iemand wil reserveren of een tafel boeken: antwoord NOOIT met een link of telefoonnummer. Zeg kortweg: "Reserveer via het formulier hieronder! 📅" — het formulier opent automatisch in de chat.
"""
# endregion

# region ── AI ANTWOORDFUNCTIES ─────────────────────────────────
def reageer_met_ai(bericht: str) -> str:
    """Stuurt bericht naar Claude API, fallback naar keyword-matching."""
    if not ANTHROPIC_API_KEY:
        return reageer_zonder_ai(bericht)
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": bericht}],
            },
            timeout=10,
        )
        data = resp.json()
        return data["content"][0]["text"].strip()
    except Exception:
        return reageer_zonder_ai(bericht)


def reageer_zonder_ai(bericht: str) -> str:
    """Fallback keyword-matching zonder API key."""
    b = bericht.lower()
    # Let op: gebruik \b woordgrens voor "open" zodat "verkopen" niet matcht
    if re.search(r'\bopen\b', b) or any(w in b for w in ["tijden", "uur", "wanneer", "openingstijden"]):
        return KENNISBANK["openingstijden"]
    elif any(w in b for w in ["adres", "locatie", "waar", "vinden", "wassenaar"]):
        return KENNISBANK["adres"]
    elif any(w in b for w in ["telefoon", "bellen", "mail", "contact", "bereiken"]):
        return KENNISBANK["contact"]
    elif any(w in b for w in ["high tea", "high-tea", "hightea", "tea"]):
        return KENNISBANK["high_tea"]
    elif any(w in b for w in ["tapas"]):
        return KENNISBANK["tapas"]
    elif any(w in b for w in ["takeaway", "afhalen", "meenemen", "take away"]):
        return KENNISBANK["takeaway"]
    elif any(w in b for w in ["shop", "webshop", "online", "bestell"]):
        return KENNISBANK["shop"]
    elif any(w in b for w in ["cadeau", "bon", "kado", "gift"]):
        return KENNISBANK["cadeaubon"]
    elif any(w in b for w in ["menu", "eten", "kaart", "lunch", "ontbijt"]):
        return KENNISBANK["menu"]
    elif any(w in b for w in ["bagel"]):
        return KENNISBANK["bagels"]
    elif any(w in b for w in ["allergie", "glutenvrij", "vegan", "vegetarisch", "lactose", "dieet"]):
        return KENNISBANK["allergieen"]
    elif any(w in b for w in ["groep", "verjaardag", "feest", "jubileum"]):
        return KENNISBANK["groepen"]
    elif any(w in b for w in ["parke", "auto"]):
        return KENNISBANK["parking"]
    elif any(w in b for w in ["hallo", "hoi", "hey", "hi", "hello", "goedemorgen", "goedemiddag"]):
        return "Hallo! Welkom bij Bagel Alley Wassenaar 🥯 Leuk dat je er bent!"
    elif any(w in b for w in ["doei", "bye", "tot ziens", "bedankt", "dankjewel", "thanks"]):
        return "STOP"
    else:
        return f'Dat weet ik niet zeker. Bel ons op 070-514 61 16 of kijk {link("op onze website", "https://www.bagelalley.nl")}!'
# endregion

# region ── FLASK ROUTES ────────────────────────────────────────
@app.route("/")
def home():
    return render_template("index.html", bedrijf=BEDRIJF)


@app.route("/menu")
def menu():
    return render_template("menu.html", bedrijf=BEDRIJF)


@app.route("/shop")
def shop():
    return render_template("shop.html", bedrijf=BEDRIJF)


@app.route("/bestellen")
def bestellen():
    return render_template("bestellen.html", bedrijf=BEDRIJF)


@app.route("/jobs")
def jobs():
    return render_template("jobs.html", bedrijf=BEDRIJF)


@app.route("/menukaart/<filename>")
def menukaart_img(filename):
    """Serveert de menukaart webp bestanden vanuit de static/menu map."""
    import mimetypes
    from flask import send_from_directory
    menu_dir = os.path.join(os.path.dirname(__file__), "static", "menu")
    return send_from_directory(menu_dir, filename)


@app.route("/chat", methods=["POST"])
@limiter.limit("20 per minute")
def chat():
    data = request.get_json(silent=True) or {}
    bericht = _clean_str(data.get("bericht"), 1000)   # harde lengtelimiet
    if not bericht:
        return jsonify({"antwoord": "Ik begreep je niet, kun je het opnieuw proberen?"})
    antwoord = reageer_met_ai(bericht)
    return jsonify({"antwoord": antwoord})


@app.route("/callback", methods=["POST"])
@limiter.limit("10 per minute")
def callback():
    data = request.get_json(silent=True) or {}
    naam = _clean_str(data.get("naam"), 100)
    telefoon = _clean_str(data.get("telefoon"), 30)
    if not naam or not telefoon:
        return jsonify({"succes": False, "fout": "Naam en telefoon zijn verplicht."}), 400
    print(f"[TERUGBELVERZOEK] {naam} — {telefoon}")
    return jsonify({"succes": True})


@app.route("/volle-slots", methods=["GET"])
@limiter.limit("60 per minute")
def volle_slots():
    parsed = _valid_iso_date(request.args.get("datum")) or date.today()
    return jsonify({"volle_slots": get_volle_slots(parsed.isoformat())})


@app.route("/reserveren", methods=["GET"])
def reserveren_get():
    vandaag = date.today().isoformat()
    boekingen = get_boekingen_voor_datum(vandaag)
    volle_slots = get_volle_slots(vandaag)
    stats = bereken_stats(vandaag)
    return render_template(
        "booking.html",
        boekingen=boekingen,
        volle_slots=volle_slots,
        stats=stats
    )


@app.route("/reserveren", methods=["POST"])
@limiter.limit("10 per minute")
def reserveren_post():
    data = request.get_json(silent=True) or {}
    naam     = _clean_str(data.get("naam"), 100)
    email    = _clean_str(data.get("email"), 150)
    telefoon = _clean_str(data.get("telefoon"), 30)
    tijd     = _clean_str(data.get("tijd"), 10)

    # aantal gasten veilig parsen en begrenzen
    try:
        gasten = int(str(data.get("gasten", 2)).split()[0])
    except (ValueError, IndexError):
        return jsonify({"succes": False, "fout": "Ongeldig aantal gasten."}), 400
    if gasten < 1 or gasten > 50:
        return jsonify({"succes": False, "fout": "Aantal gasten moet tussen 1 en 50 liggen."}), 400

    if not naam:
        return jsonify({"succes": False, "fout": "Naam is verplicht."}), 400
    if not _EMAIL_RE.match(email):
        return jsonify({"succes": False, "fout": "Ongeldig e-mailadres."}), 400
    if tijd not in ALLE_SLOTS:
        return jsonify({"succes": False, "fout": "Ongeldig tijdslot."}), 400

    parsed = _valid_iso_date(data.get("datum"))
    if parsed is None:
        return jsonify({"succes": False, "fout": "Ongeldige datum."}), 400
    datum = parsed.isoformat()
    if parsed.weekday() == 1:  # 1 = dinsdag (gesloten)
        return jsonify({"succes": False, "fout": "Bagel Alley is op dinsdag gesloten."}), 400

    volle_slots = get_volle_slots(datum)
    if tijd in volle_slots:
        return jsonify({"succes": False, "fout": f"Tijdslot {tijd} is helaas al bezet."})

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO boekingen (naam, email, gasten, datum, tijd, telefoon) VALUES (?,?,?,?,?,?)",
            (naam, email, gasten, datum, tijd, telefoon)
        )
        conn.commit()

    print(f"[RESERVERING] {naam} | {email} | {datum} om {tijd} | {gasten} gasten")
    stuur_bevestigingsmail(naam, email, datum, tijd, gasten)
    stuur_notificatie({"naam": naam, "datum": datum, "tijd": tijd, "gasten": gasten})
    stats = bereken_stats(datum)
    return jsonify({"succes": True, "tijd": tijd, "stats": stats})
# endregion

# region ── SERVER-SENT EVENTS (live notificaties) ──────────────
import queue, threading
_notif_queues = []
_notif_lock   = threading.Lock()

def _broadcast(data: str):
    with _notif_lock:
        dode = []
        for q in _notif_queues:
            try:
                q.put_nowait(data)
            except Exception:
                dode.append(q)
        for q in dode:
            _notif_queues.remove(q)

_geziene_notif_ids = set()

def stuur_notificatie(boeking: dict):
    import json as _json
    global _geziene_notif_ids
    notif_id = f"{boeking.get('naam')}-{boeking.get('datum')}-{boeking.get('tijd')}"
    if notif_id in _geziene_notif_ids:
        return
    _geziene_notif_ids.add(notif_id)
    _broadcast(_json.dumps(boeking))

@app.route("/admin/events")
def admin_events():
    if not session.get("admin_ingelogd"):
        return "Niet ingelogd", 401
    q = queue.Queue(maxsize=20)
    with _notif_lock:
        _notif_queues.append(q)
    def stream():
        yield "data: connected\n\n"
        while True:
            try:
                msg = q.get(timeout=25)
                yield f"data: {msg}\n\n"
            except queue.Empty:
                yield ": ping\n\n"
    from flask import Response, stream_with_context
    return Response(stream_with_context(stream()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
# endregion

# region ── ADMIN INSTELLINGEN ───────────────────────────────────
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
# Zet ADMIN_PASS als env var in Railway en roteer het oude wachtwoord:
# dit zat hardcoded in de broncode en staat dus in de git-historie.
ADMIN_PASS = os.environ.get("ADMIN_PASS", "bagelalley2024")
# endregion

# region ── ADMIN ROUTES ──────────────────────────────────────────
from functools import wraps

def login_vereist(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_ingelogd"):
            return jsonify({"fout": "Niet ingelogd"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/admin")
def admin():
    return render_template("admin.html")

@app.route("/admin/login", methods=["POST"])
@limiter.limit("5 per minute")
def admin_login():
    data = request.get_json(silent=True) or {}
    gebruiker = _clean_str(data.get("gebruiker"), 100)
    wachtwoord = str(data.get("wachtwoord") or "")[:200]
    if _safe_eq(gebruiker, ADMIN_USER) and _safe_eq(wachtwoord, ADMIN_PASS):
        session["admin_ingelogd"] = True
        return jsonify({"succes": True})
    return jsonify({"succes": False, "fout": "Onjuiste inloggegevens"}), 401

@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin_ingelogd", None)
    return jsonify({"succes": True})

@app.route("/admin/boekingen", methods=["GET"])
@login_vereist
def admin_boekingen():
    datum = request.args.get("datum", date.today().isoformat())
    boekingen = get_boekingen_voor_datum(datum)
    stats = bereken_stats(datum)
    return jsonify({"boekingen": boekingen, "stats": stats})

@app.route("/admin/week", methods=["GET"])
@login_vereist
def admin_week():
    from datetime import timedelta
    start = request.args.get("start", date.today().isoformat())
    week = {}
    start_datum = date.fromisoformat(start)
    for i in range(7):
        dag = start_datum + timedelta(days=i)
        iso = dag.isoformat()
        week[iso] = get_boekingen_voor_datum(iso)
    return jsonify({"week": week})

@app.route("/admin/annuleer/<int:boeking_id>", methods=["POST"])
@login_vereist
def admin_annuleer(boeking_id):
    with sqlite3.connect(DB_PATH) as conn:
        try:
            conn.execute("ALTER TABLE boekingen ADD COLUMN geannuleerd INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        conn.execute("UPDATE boekingen SET geannuleerd = 1 WHERE id = ?", (boeking_id,))
        conn.commit()
    return jsonify({"succes": True})


@app.route("/admin/verwijder/<int:boeking_id>", methods=["POST"])
@login_vereist
def admin_verwijder(boeking_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM boekingen WHERE id = ?", (boeking_id,))
        conn.commit()
    return jsonify({"succes": True})
# endregion

@app.route("/ping")
def ping():
    return "ok", 200


# region ── OPSTARTEN ───────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=False, host="0.0.0.0", port=port)
# endregion

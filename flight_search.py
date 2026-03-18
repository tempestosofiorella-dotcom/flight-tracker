import requests
import json
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ── Config ───────────────────────────────────────────────────────────────────
SERPAPI_KEY    = os.environ["SERPAPI_KEY"]
EMAIL_FROM     = os.environ["EMAIL_FROM"]
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]
EMAIL_TO       = os.environ["EMAIL_TO"]

PASSENGERS = 4

# Umbrales por persona (USD)
THRESHOLD_RT1   = 1000   # ARG ↔ Europa (round trip)
THRESHOLD_RT2   = 900    # Europa ↔ Japón (round trip)
THRESHOLD_TOTAL = 1800   # Total ambos round trips

# ── Búsquedas a ejecutar ─────────────────────────────────────────────────────
# RT1: EZE ↔ Europa | salida Oct 19-25, vuelta Nov 19-30
RT1_SEARCHES = [
    ("EZE", "MAD", "2026-10-19", "2026-11-23"),
    ("EZE", "MAD", "2026-10-22", "2026-11-25"),
    ("EZE", "LIS", "2026-10-20", "2026-11-23"),
    ("EZE", "BCN", "2026-10-21", "2026-11-24"),
]

# RT2: Europa ↔ Japón | salida ~Oct 26-Nov 1, vuelta 11-12 días después
RT2_SEARCHES = [
    ("MAD", "NRT", "2026-10-27", "2026-11-08"),
    ("MAD", "HND", "2026-10-27", "2026-11-08"),
    ("LIS", "NRT", "2026-10-27", "2026-11-08"),
    ("BCN", "NRT", "2026-10-28", "2026-11-09"),
]

# ── Funciones ────────────────────────────────────────────────────────────────
def search_flights(origin, destination, outbound_date, return_date):
    params = {
        "engine":         "google_flights",
        "departure_id":   origin,
        "arrival_id":     destination,
        "outbound_date":  outbound_date,
        "return_date":    return_date,
        "currency":       "USD",
        "hl":             "en",
        "type":           "1",       # round trip
        "adults":         PASSENGERS,
        "travel_class":   "1",       # economy
        "stops":          "2",       # max 1 escala
        "api_key":        SERPAPI_KEY,
    }

    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=30)
        data = response.json()
    except Exception as e:
        print(f"  ERROR en request {origin}→{destination}: {e}")
        return None

    if "error" in data:
        print(f"  ERROR API {origin}→{destination}: {data['error']}")
        return None

    best_price = None
    best_flight = None

    for group in ["best_flights", "other_flights"]:
        for flight in data.get(group, []):
            price = flight.get("price")
            if price and (best_price is None or price < best_price):
                best_price = price
                best_flight = flight

    if best_price is None:
        return None

    # SerpApi devuelve precio TOTAL para todos los pasajeros
    price_pp = best_price / PASSENGERS

    # Extraer info del primer vuelo (ida)
    airline = ""
    stops = 0
    duration = ""
    if best_flight and best_flight.get("flights"):
        first_leg = best_flight["flights"][0]
        airline = first_leg.get("airline", "")
        duration = best_flight.get("total_duration", "")
        stops = len(best_flight["flights"]) - 1

    return {
        "origin":       origin,
        "destination":  destination,
        "outbound":     outbound_date,
        "return":       return_date,
        "price_total":  best_price,
        "price_pp":     price_pp,
        "airline":      airline,
        "stops":        stops,
        "duration_min": duration,
    }


def build_combinations(results_rt1, results_rt2):
    combos = []
    for r1 in results_rt1:
        for r2 in results_rt2:
            # El hub de salida de RT2 debe coincidir con el destino de RT1
            if r1["destination"] == r2["origin"]:
                total_pp = r1["price_pp"] + r2["price_pp"]
                combos.append({
                    "rt1":      r1,
                    "rt2":      r2,
                    "total_pp": total_pp,
                    "total_x4": total_pp * PASSENGERS,
                })
    combos.sort(key=lambda x: x["total_pp"])
    return combos


def stops_label(n):
    if n == 0: return "✈️ Directo"
    if n == 1: return "1 escala"
    return f"{n} escalas"


def format_email(results_rt1, results_rt2, combos):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    best = combos[0] if combos else None
    alert = best and best["total_pp"] < THRESHOLD_TOTAL

    style = """
    <style>
      body { font-family: Arial, sans-serif; color: #333; }
      h2 { color: #1a73e8; }
      h3 { color: #444; margin-top: 24px; }
      table { border-collapse: collapse; width: 100%; margin-top: 8px; }
      th { background: #1a73e8; color: white; padding: 8px 12px; text-align: left; }
      td { padding: 7px 12px; border-bottom: 1px solid #eee; }
      tr:hover td { background: #f5f5f5; }
      .good { color: #188038; font-weight: bold; }
      .ok   { color: #e37400; }
      .high { color: #c5221f; }
      .alert-box { background: #e6f4ea; border: 2px solid #188038; padding: 12px 16px;
                   border-radius: 6px; margin-bottom: 16px; }
      small { color: #888; }
    </style>
    """

    banner = ""
    if alert:
        banner = f"""
        <div class="alert-box">
          🚨 <b>OFERTA DETECTADA</b> — USD {best['total_pp']:.0f}/persona
          (total x4: <b>USD {best['total_x4']:.0f}</b>)
        </div>
        """

    def price_class(pp, threshold):
        if pp < threshold * 0.90: return "good"
        if pp < threshold: return "ok"
        return "high"

    # Tabla combinaciones
    rows_combo = ""
    for c in combos[:5]:
        cls = price_class(c["total_pp"], THRESHOLD_TOTAL)
        rows_combo += f"""
        <tr>
          <td>{c['rt1']['origin']} ↔ {c['rt1']['destination']}</td>
          <td>{c['rt1']['outbound']} → {c['rt1']['return']}</td>
          <td>{c['rt1']['airline']} · {stops_label(c['rt1']['stops'])}</td>
          <td class="{price_class(c['rt1']['price_pp'], THRESHOLD_RT1)}">USD {c['rt1']['price_pp']:.0f}</td>
          <td>{c['rt2']['origin']} ↔ {c['rt2']['destination']}</td>
          <td>{c['rt2']['outbound']} → {c['rt2']['return']}</td>
          <td>{c['rt2']['airline']} · {stops_label(c['rt2']['stops'])}</td>
          <td class="{price_class(c['rt2']['price_pp'], THRESHOLD_RT2)}">USD {c['rt2']['price_pp']:.0f}</td>
          <td class="{cls}"><b>USD {c['total_pp']:.0f}</b></td>
          <td class="{cls}"><b>USD {c['total_x4']:.0f}</b></td>
        </tr>
        """

    table_combo = f"""
    <h3>Top combinaciones</h3>
    <table>
      <tr>
        <th>RT1 Ruta</th><th>RT1 Fechas</th><th>RT1 Vuelo</th><th>RT1 $/pp</th>
        <th>RT2 Ruta</th><th>RT2 Fechas</th><th>RT2 Vuelo</th><th>RT2 $/pp</th>
        <th>Total $/pp</th><th>Total x4</th>
      </tr>
      {rows_combo}
    </table>
    """

    # Tabla detalle RT1
    rows_rt1 = ""
    for r in sorted(results_rt1, key=lambda x: x["price_pp"]):
        cls = price_class(r["price_pp"], THRESHOLD_RT1)
        rows_rt1 += f"""
        <tr>
          <td>{r['origin']} ↔ {r['destination']}</td>
          <td>{r['outbound']} / {r['return']}</td>
          <td>{r['airline']}</td>
          <td>{stops_label(r['stops'])}</td>
          <td class="{cls}">USD {r['price_pp']:.0f}/pp</td>
          <td>USD {r['price_total']:.0f} total</td>
        </tr>
        """

    # Tabla detalle RT2
    rows_rt2 = ""
    for r in sorted(results_rt2, key=lambda x: x["price_pp"]):
        cls = price_class(r["price_pp"], THRESHOLD_RT2)
        rows_rt2 += f"""
        <tr>
          <td>{r['origin']} ↔ {r['destination']}</td>
          <td>{r['outbound']} / {r['return']}</td>
          <td>{r['airline']}</td>
          <td>{stops_label(r['stops'])}</td>
          <td class="{cls}">USD {r['price_pp']:.0f}/pp</td>
          <td>USD {r['price_total']:.0f} total</td>
        </tr>
        """

    table_rt1 = f"""
    <h3>RT1 — Argentina ↔ Europa</h3>
    <p>Umbral "oferta": <b>USD {THRESHOLD_RT1}/pp</b></p>
    <table>
      <tr><th>Ruta</th><th>Fechas</th><th>Aerolínea</th><th>Escalas</th><th>Precio/pp</th><th>Total x4</th></tr>
      {rows_rt1}
    </table>
    """

    table_rt2 = f"""
    <h3>RT2 — Europa ↔ Japón</h3>
    <p>Umbral "oferta": <b>USD {THRESHOLD_RT2}/pp</b></p>
    <table>
      <tr><th>Ruta</th><th>Fechas</th><th>Aerolínea</th><th>Escalas</th><th>Precio/pp</th><th>Total x4</th></tr>
      {rows_rt2}
    </table>
    """

    legend = f"""
    <p style="margin-top:24px">
      <span class="good">■ Verde</span> = por debajo del umbral &nbsp;
      <span class="ok">■ Naranja</span> = cerca del umbral &nbsp;
      <span class="high">■ Rojo</span> = sobre el umbral
    </p>
    <p><small>Actualizado: {now} · 4 pasajeros · economy · max 1 escala</small></p>
    """

    html = f"<html><head>{style}</head><body>"
    html += "<h2>✈️ Flight Tracker — Europa + Japón 2026</h2>"
    html += banner
    html += table_combo if combos else "<p>No se encontraron combinaciones válidas.</p>"
    html += table_rt1
    html += table_rt2
    html += legend
    html += "</body></html>"
    return html


def send_email(subject, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*50}")
    print(f"Flight Search — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*50}\n")

    results_rt1 = []
    results_rt2 = []

    print(">> RT1: Argentina ↔ Europa")
    for origin, dest, out_date, ret_date in RT1_SEARCHES:
        print(f"   {origin} ↔ {dest}  ({out_date} / {ret_date})", end="  ")
        result = search_flights(origin, dest, out_date, ret_date)
        if result:
            results_rt1.append(result)
            print(f"USD {result['price_pp']:.0f}/pp  {stops_label(result['stops'])}")
        else:
            print("sin resultados")

    print("\n>> RT2: Europa ↔ Japón")
    for origin, dest, out_date, ret_date in RT2_SEARCHES:
        print(f"   {origin} ↔ {dest}  ({out_date} / {ret_date})", end="  ")
        result = search_flights(origin, dest, out_date, ret_date)
        if result:
            results_rt2.append(result)
            print(f"USD {result['price_pp']:.0f}/pp  {stops_label(result['stops'])}")
        else:
            print("sin resultados")

    combos = build_combinations(results_rt1, results_rt2)

    print(f"\n>> Combinaciones encontradas: {len(combos)}")
    if combos:
        best = combos[0]
        print(f"   Mejor: USD {best['total_pp']:.0f}/pp  (total x4: USD {best['total_x4']:.0f})")

    # Subject del email
    if combos:
        best = combos[0]
        if best["total_pp"] < THRESHOLD_TOTAL:
            subject = f"🚨 OFERTA VIAJE! USD {best['total_pp']:.0f}/pp · Total x4 USD {best['total_x4']:.0f}"
        else:
            subject = f"✈️ Flight Update — Mejor precio: USD {best['total_pp']:.0f}/pp (x4: USD {best['total_x4']:.0f})"
    else:
        subject = "✈️ Flight Update — Sin combinaciones disponibles"

    html = format_email(results_rt1, results_rt2, combos)
    send_email(subject, html)
    print(f"\n>> Email enviado: {subject}\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Test automatizado de analisis de conversaciones WhatsApp → CRM
Envia cada conversacion al modelo Claude y compara campos clave
contra las respuestas esperadas definidas en EXPECTED_RESULTS.

Uso:
  export ANTHROPIC_API_KEY=sk-...
  python3 test_conversations.py [--ids 1,2,3] [--verbose]
"""
import json, sys, os, time, argparse, urllib.request, urllib.error, datetime

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"
BASE_URL = "https://api.anthropic.com/v1/messages"

# ─── SYSTEM PROMPT (identico al de prototipo-crm.html) ────────
TODAY = datetime.date.today().isoformat()

SYSTEM_PROMPT = f"""Eres un asistente de CRM para Maxim Domenech Peru, empresa de alquiler de maquinaria de elevacion (plataformas tijera y articuladas).

FECHA ACTUAL: {TODAY}

Analiza la siguiente conversacion de WhatsApp entre un comercial y un contacto. Tu tarea es:

1. CLASIFICAR si la conversacion es relevante para el CRM (comercial/ventas) o no (personal/spam/interno).
   - Conversaciones relevantes: solicitudes de presupuesto, reservas de equipos, averias/incidencias de equipos alquilados, clientes existentes pidiendo mas equipos
   - NO relevantes: conversaciones personales, spam, coordinacion interna entre empleados, proveedores, reclamos de facturacion, consultas academicas sin intencion de compra

2. EXTRAER la siguiente informacion si esta disponible:
   - nombre_contacto, apellido_contacto
   - empresa, razon_social, ruc
   - email, telefono_adicional
   - sector (construccion, mineria, industrial, energia, gobierno, salud, otro)
   - equipo_interes (tipo y altura si se menciona)
   - ubicacion_obra (ciudad, distrito, direccion)
   - urgencia: "alta" si es averia/emergencia o el cliente dice "urgente"/"lo antes posible"/"esta semana"; "media" si tiene fecha definida proxima (1-4 semanas); "baja" si es consulta general o fecha lejana (mas de 1 mes)
   - tipo_solicitud: "presupuesto" | "reserva" | "averia" | "extension" | "consulta"

3. DECIDIR que acciones tomar:
   - crear_contacto: true/false
   - actualizar_contacto: true/false
   - crear_empresa: true/false (SOLO true si el contacto proporciona un numero de RUC explicitamente en la conversacion. Si solo menciona el nombre de la empresa sin RUC, poner false)
   - crear_deal: true/false (true para CUALQUIER oportunidad activa: presupuestos concretos, reservas, extensiones, Y TAMBIEN averias/incidencias ya que representan un servicio tecnico facturable. FALSE para consultas generales sin intencion concreta de alquiler, ej: "estamos evaluando opciones", "queria saber que tienen")
   - etapa_deal: "consulta_inicial" | "cotizacion" | "negociacion" | "servicio_tecnico" | null
   - crear_tarea: true/false
   - tarea_asunto: string
   - tarea_descripcion: string
   - tarea_fecha_vencimiento: YYYY-MM-DD. Para averias/urgentes: fecha actual. Para cotizaciones: dia siguiente. Para reservas con fecha: 3 dias antes de la entrega.
   - datos_pendientes: []
   - resumen: 2-3 frases

4. Responde SOLO en formato JSON valido. Si la conversacion no es relevante, responde: {{"relevante": false, "razon": "..."}}"""


# ─── CONVERSACIONES (se cargan del JS) ─────────────────────────
def load_conversations():
    """Parsea el array CONVERSATIONS del archivo JS generado."""
    path = os.path.join(os.path.dirname(__file__), "prototipo-crm.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    import re
    match = re.search(r'const CONVERSATIONS = (\[.*?\]);\s*\n', html, re.DOTALL)
    if not match:
        print("ERROR: No se encontro CONVERSATIONS en prototipo-crm.html")
        sys.exit(1)

    # Convertir JS a JSON valido (keys sin comillas → con comillas)
    js_text = match.group(1)
    # Reemplazar keys sin comillas
    js_text = re.sub(r'(\s)(\w+)\s*:', r'\1"\2":', js_text)
    # Reemplazar comillas simples en valores por dobles (cuidado con apostrofes)
    # Mejor parsear con un enfoque distinto: usar node
    return _parse_with_node(match.group(1))


def _parse_with_node(js_array):
    """Usa Node.js para parsear el array JS de forma segura."""
    import subprocess, tempfile
    code = f"process.stdout.write(JSON.stringify({js_array}))"
    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(code)
        tmp = f.name
    try:
        result = subprocess.run(['node', tmp], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            print(f"Node error: {result.stderr}")
            sys.exit(1)
        return json.loads(result.stdout)
    finally:
        os.unlink(tmp)


def format_conversation(conv):
    """Formatea la conversacion como texto (igual que el frontend)."""
    lines = []
    for m in conv["messages"]:
        lines.append(f"[{m['time']}] {m['sender']}: {m['text']}")
    return "\n".join(lines)


# ─── RESPUESTAS ESPERADAS ─────────────────────────────────────
# Campos clave que validamos para cada conversacion
EXPECTED = {
    # --- Solicitudes de presupuesto ---
    1:  {"relevante": True, "nombre": "Carlos", "apellido": "Ramirez", "empresa": "Constructora Vida", "crear_contacto": True, "crear_deal": True, "crear_empresa": False, "urgencia": "media"},
    2:  {"relevante": True, "nombre": "Maria", "apellido": "Torres", "empresa": "Grupo Industrial del Norte", "crear_contacto": True, "crear_deal": False, "crear_empresa": False, "urgencia": "baja"},
    3:  {"relevante": True, "nombre": "Jorge", "apellido": "Mendez", "empresa": "Minera Altiplano", "ruc": "20567891234", "crear_contacto": True, "crear_deal": True, "crear_empresa": True, "urgencia": "alta"},
    4:  {"relevante": False},
    5:  {"relevante": True, "nombre": "Rosa", "apellido": "Gutierrez", "empresa": "Edificaciones del Sur", "crear_contacto": False, "actualizar_contacto": True, "crear_deal": True},
    # --- Averias ---
    6:  {"relevante": True, "nombre": "Fernando", "apellido": "Diaz", "empresa": "Constructora Lima", "ruc": "20498765432", "crear_deal": True, "urgencia": "alta", "crear_tarea": True},
    7:  {"relevante": True, "nombre": "Adriana", "apellido": "Vega", "empresa": "Ingenieria Total", "ruc": "20345678901", "urgencia": "alta", "crear_tarea": True},
    8:  {"relevante": True, "nombre": "Roberto", "apellido": "Castillo", "empresa": "Mantenimientos Pro", "urgencia": "alta", "crear_tarea": True},
    9:  {"relevante": True, "nombre": "Gabriela", "apellido": "Flores", "empresa": "Grupo Acero", "ruc": "20234567890", "urgencia": "alta", "crear_tarea": True},
    10: {"relevante": True, "nombre": "Miguel", "apellido": "Paredes", "empresa": "Electro Industrial", "ruc": "20678901234", "urgencia": "alta", "crear_tarea": True},
    # --- Reservas con fecha ---
    11: {"relevante": True, "nombre": "Patricia", "apellido": "Huaman", "empresa": "JCM Ingenieros", "ruc": "20789012345", "crear_deal": True, "crear_empresa": True},
    12: {"relevante": True, "nombre": "Andres", "apellido": "Rojas", "empresa": "Constructora Pacifico", "ruc": "20890123456", "crear_deal": True, "crear_empresa": True},
    13: {"relevante": True, "nombre": "Lucia", "apellido": "Vargas", "empresa": "Hospital Regional", "ruc": "20345678901", "crear_deal": True},
    14: {"relevante": True, "nombre": "Ricardo", "apellido": "Soto", "empresa": "Inmobiliaria Cenit", "ruc": "20901234567", "crear_deal": True, "crear_empresa": True},
    15: {"relevante": True, "nombre": "Carmen", "apellido": "Quispe", "empresa": "Minera del Sur", "ruc": "20123456789", "crear_deal": True, "crear_empresa": True},
    16: {"relevante": True, "nombre": "Diego", "crear_deal": True, "actualizar_contacto": True},
    17: {"relevante": True, "nombre": "Valeria", "apellido": "Medina", "empresa": "Telecomunicaciones Andinas", "ruc": "20678901234", "crear_deal": True, "crear_empresa": True},
    18: {"relevante": True, "nombre": "Hugo", "apellido": "Salazar", "ruc": "20789012345", "crear_deal": True},
    19: {"relevante": True, "nombre": "Elena", "apellido": "Paredes", "empresa": "Ingenieria & Puentes", "ruc": "20890123456", "crear_deal": True, "crear_empresa": True},
    20: {"relevante": True, "nombre": "Oscar", "apellido": "Ramos", "empresa": "Petroquimica del Norte", "ruc": "20456789012", "crear_deal": True, "crear_empresa": True},
    # --- Presupuestos variados ---
    21: {"relevante": True, "nombre": "Pilar", "apellido": "Navarro", "ruc": "20345678901", "crear_deal": True},
    22: {"relevante": True, "nombre": "Raul", "apellido": "Espinoza", "ruc": "20567890123", "crear_deal": True, "crear_empresa": True},
    23: {"relevante": True, "nombre": "Sandra", "apellido": "Ruiz", "empresa": "Cementos Lima", "ruc": "20678901234", "crear_deal": True, "crear_empresa": True},
    24: {"relevante": True, "nombre": "Felipe", "apellido": "Morales", "ruc": "20789012345", "crear_deal": True},
    25: {"relevante": True, "nombre": "Isabel", "apellido": "Campos", "ruc": "20890123456", "crear_deal": True, "crear_empresa": True},
    # --- No relevantes ---
    26: {"relevante": False},
    27: {"relevante": False},
    28: {"relevante": False},
    29: {"relevante": False},
    30: {"relevante": False},
    # --- Mas averias ---
    31: {"relevante": True, "nombre": "Alejandro", "apellido": "Cruz", "ruc": "20345678912", "urgencia": "alta", "crear_tarea": True},
    32: {"relevante": True, "nombre": "Natalia", "ruc": "20456789123", "urgencia": "alta", "crear_tarea": True},
    33: {"relevante": True, "nombre": "Cesar", "apellido": "Vargas", "ruc": "20567891234", "crear_tarea": True},
    34: {"relevante": True, "nombre": "Laura", "apellido": "Paz", "ruc": "20678912345", "urgencia": "alta", "crear_tarea": True},
    35: {"relevante": True, "nombre": "Antonio", "apellido": "Valdivia", "ruc": "20789123456", "crear_tarea": True},
    # --- Reservas adicionales ---
    36: {"relevante": True, "nombre": "Veronica", "apellido": "Luna", "ruc": "20891234567", "crear_deal": True, "crear_empresa": True},
    37: {"relevante": True, "nombre": "Javier", "apellido": "Rios", "empresa": "Constructora Andes", "ruc": "20912345678", "crear_deal": True, "crear_empresa": True},
    38: {"relevante": True, "nombre": "Claudia", "apellido": "Moreno", "ruc": "20123456790", "crear_deal": True},
    39: {"relevante": True, "nombre": "Martin", "apellido": "Delgado", "ruc": "20234567891", "crear_deal": True, "crear_empresa": True},
    40: {"relevante": True, "nombre": "Beatriz", "apellido": "Ochoa", "ruc": "20345678912", "crear_deal": True, "crear_empresa": True},
}


# ─── LLAMADA A CLAUDE ──────────────────────────────────────────
def call_claude(conversation_text):
    """Llama a la API de Claude y devuelve el JSON parseado."""
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1500,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": f"Analiza esta conversacion de WhatsApp:\n\n{conversation_text}"}]
    }).encode()

    req = urllib.request.Request(
        BASE_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    text = data["content"][0]["text"].strip()
    # Limpiar posible markdown
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


# ─── VALIDACION ────────────────────────────────────────────────
def validate(conv_id, result, expected):
    """Compara resultado vs esperado. Retorna (passed_checks, total_checks, errors)."""
    errors = []
    total = 0
    passed = 0

    # Normalizar: la respuesta puede venir plana o con sub-objetos datos/acciones
    def get_field(key):
        field_map = {"nombre": "nombre_contacto", "apellido": "apellido_contacto"}
        mapped = field_map.get(key, key)
        # Buscar primero en nivel plano, luego en sub-objetos
        if mapped in result:
            return result[mapped]
        if "datos" in result and isinstance(result["datos"], dict) and mapped in result["datos"]:
            return result["datos"][mapped]
        if "acciones" in result and isinstance(result["acciones"], dict) and mapped in result["acciones"]:
            return result["acciones"][mapped]
        return None

    for key, exp_val in expected.items():
        total += 1
        if key == "relevante":
            actual = result.get("relevante")
        elif not result.get("relevante"):
            actual = None
        else:
            actual = get_field(key)

        # Comparar
        if key == "relevante" and actual == exp_val:
            passed += 1
        elif key == "relevante" and actual != exp_val:
            errors.append(f"  {key}: esperado={exp_val} actual={actual}")
        elif not expected.get("relevante", True):
            # Si no es relevante, solo checamos relevante
            passed += 1
        elif isinstance(exp_val, str):
            if actual and exp_val.lower() in str(actual).lower():
                passed += 1
            else:
                errors.append(f"  {key}: esperado='{exp_val}' actual='{actual}'")
        elif isinstance(exp_val, bool):
            if actual == exp_val:
                passed += 1
            else:
                errors.append(f"  {key}: esperado={exp_val} actual={actual}")
        else:
            if actual == exp_val:
                passed += 1
            else:
                errors.append(f"  {key}: esperado={exp_val} actual={actual}")

    return passed, total, errors


# ─── ANALISIS DE VARIANZA ──────────────────────────────────────
def analyze_variance(all_runs):
    """Analiza varianza entre multiples ejecuciones del mismo caso."""
    variance_report = {}

    for cid, runs in all_runs.items():
        field_values = {}  # campo -> [valor_run1, valor_run2, ...]
        for run in runs:
            resp = run.get("response", {})
            if not resp:
                continue
            flat = flatten_response(resp)
            for k, v in flat.items():
                field_values.setdefault(k, []).append(str(v))

        # Calcular consistencia por campo
        field_stats = {}
        for field, values in field_values.items():
            unique = set(values)
            consistency = values.count(max(set(values), key=values.count)) / len(values)
            field_stats[field] = {
                "consistency": round(consistency * 100),
                "values": list(unique),
                "majority": max(set(values), key=values.count)
            }

        variance_report[cid] = field_stats

    return variance_report


def flatten_response(resp):
    """Aplana la respuesta JSON a un dict de campo:valor."""
    flat = {}
    for k, v in resp.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                flat[f"{k}.{k2}"] = v2
        elif isinstance(v, list):
            flat[k] = json.dumps(v, ensure_ascii=False)
        else:
            flat[k] = v
    return flat


def print_variance_report(variance, conv_names):
    """Imprime reporte de varianza legible."""
    print(f"\n{'='*60}")
    print(f"ANALISIS DE VARIANZA (campos inconsistentes)")
    print(f"{'='*60}")

    any_issues = False
    for cid, fields in sorted(variance.items()):
        unstable = {f: s for f, s in fields.items() if s["consistency"] < 100}
        if not unstable:
            continue
        any_issues = True
        name = conv_names.get(cid, f"Conv {cid}")
        print(f"\n  [{cid}] {name}")
        for field, stats in sorted(unstable.items(), key=lambda x: x[1]["consistency"]):
            vals = ", ".join(stats["values"][:4])
            print(f"    {field:<35s} {stats['consistency']:3d}% consistente  valores: {vals}")

    if not any_issues:
        print("\n  Todos los campos son 100% consistentes entre ejecuciones.")
    print()


# ─── MAIN ──────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Test conversaciones CRM")
    parser.add_argument("--ids", type=str, help="IDs a probar (ej: 1,2,3). Default: todos")
    parser.add_argument("--verbose", "-v", action="store_true", help="Mostrar detalles de cada test")
    parser.add_argument("--runs", "-r", type=int, default=1, help="Repeticiones por conversacion (para medir varianza)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay entre llamadas (seg)")
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: Configura ANTHROPIC_API_KEY")
        sys.exit(1)

    print("Cargando conversaciones...")
    convs = load_conversations()
    print(f"  {len(convs)} conversaciones cargadas")

    if args.ids:
        ids = [int(x) for x in args.ids.split(",")]
        convs = [c for c in convs if c["id"] in ids]
        print(f"  Filtrando: {len(convs)} conversaciones seleccionadas")

    total_passed = 0
    total_checks = 0
    total_convs_ok = 0
    results_log = []
    all_runs = {}  # cid -> [run1, run2, ...]
    conv_names = {}

    n_runs = args.runs
    total_calls = len(convs) * n_runs

    print(f"\n{'='*60}")
    if n_runs > 1:
        print(f"EJECUTANDO TESTS ({len(convs)} conversaciones x {n_runs} runs = {total_calls} llamadas)")
    else:
        print(f"EJECUTANDO TESTS ({len(convs)} conversaciones)")
    print(f"{'='*60}\n")

    call_num = 0
    for i, conv in enumerate(convs):
        cid = conv["id"]
        conv_names[cid] = conv["name"]
        expected = EXPECTED.get(cid)
        if not expected:
            print(f"  [{cid}] SKIP - sin respuesta esperada")
            continue

        all_runs[cid] = []
        run_results = []

        for run in range(n_runs):
            call_num += 1
            run_label = f" run {run+1}/{n_runs}" if n_runs > 1 else ""
            print(f"  [{call_num:3d}/{total_calls}] {conv['name'][:40]:<40s}{run_label} ", end="", flush=True)

            try:
                text = format_conversation(conv)
                result = call_claude(text)
                passed, checks, errors = validate(cid, result, expected)

                run_results.append({"passed": passed, "checks": checks, "errors": errors})
                all_runs[cid].append({"response": result, "passed": passed, "checks": checks, "errors": errors})

                if not errors:
                    print(f"OK  ({passed}/{checks})")
                else:
                    print(f"FAIL ({passed}/{checks})")
                    if args.verbose:
                        for e in errors:
                            print(f"       {e}")

                results_log.append({
                    "id": cid,
                    "run": run + 1,
                    "name": conv["name"],
                    "passed": passed,
                    "total": checks,
                    "errors": errors,
                    "response": result
                })

            except Exception as e:
                print(f"ERROR: {e}")
                results_log.append({"id": cid, "run": run + 1, "name": conv["name"], "error": str(e)})
                all_runs[cid].append({"error": str(e)})

            time.sleep(args.delay)

        # Agregar stats para esta conversacion (promedio de runs)
        valid_runs = [r for r in run_results if "error" not in r]
        if valid_runs:
            avg_passed = sum(r["passed"] for r in valid_runs) / len(valid_runs)
            avg_checks = valid_runs[0]["checks"]
            total_passed += sum(r["passed"] for r in valid_runs)
            total_checks += sum(r["checks"] for r in valid_runs)
            if all(not r["errors"] for r in valid_runs):
                total_convs_ok += 1

    # ── RESUMEN ──
    tested = len([cid for cid in all_runs if any("error" not in r for r in all_runs[cid])])
    print(f"\n{'='*60}")
    print(f"RESUMEN")
    print(f"{'='*60}")
    print(f"  Conversaciones: {tested} probadas")
    if n_runs > 1:
        print(f"  Runs por conv:  {n_runs}")
        print(f"  Total llamadas: {call_num}")
    print(f"  100% correctas: {total_convs_ok}/{tested} ({100*total_convs_ok/max(tested,1):.0f}%)")
    print(f"  Campos OK:      {total_passed}/{total_checks} ({100*total_passed/max(total_checks,1):.0f}%)")
    print(f"{'='*60}")

    # ── VARIANZA ──
    if n_runs > 1:
        variance = analyze_variance(all_runs)
        print_variance_report(variance, conv_names)

        # Tabla de fiabilidad por conversacion
        print(f"{'='*60}")
        print(f"FIABILIDAD POR CONVERSACION")
        print(f"{'='*60}")
        for cid in sorted(all_runs.keys()):
            runs = all_runs[cid]
            valid = [r for r in runs if "error" not in r]
            if not valid:
                continue
            scores = [r["passed"]/r["checks"]*100 for r in valid]
            avg = sum(scores) / len(scores)
            mn, mx = min(scores), max(scores)
            name = conv_names.get(cid, "")[:40]
            stable = "ESTABLE" if mn == mx else "VARIABLE"
            print(f"  [{cid:2d}] {name:<40s} avg:{avg:5.1f}%  min:{mn:.0f}%  max:{mx:.0f}%  {stable}")
        print()

    # Guardar log
    log_path = os.path.join(os.path.dirname(__file__), "test_results.json")
    log_data = {
        "summary": {
            "tested": tested,
            "runs_per_conv": n_runs,
            "ok": total_convs_ok,
            "fields_passed": total_passed,
            "fields_total": total_checks
        },
        "results": results_log
    }
    if n_runs > 1:
        log_data["variance"] = analyze_variance(all_runs)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
    print(f"  Log guardado en: {log_path}")


if __name__ == "__main__":
    main()

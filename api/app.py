import os
import re
import logging
import unicodedata
from datetime import datetime, timezone, timedelta

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests as http_client


app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

app.config["MAX_CONTENT_LENGTH"] = 4096

CORS(app, resources={r"/submit": {"origins": ALLOWED_ORIGIN}}, methods=["POST"])

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["30/minute"],
    storage_uri="memory://",
)

BRT = timezone(timedelta(hours=-3))

ALLOWED_VALUES = {
    "R$ 4.000 a R$ 10.000",
    "R$ 10.000 a R$ 20.000",
    "R$ 20.000 a R$ 35.000",
    "R$ 35.000 a R$ 50.000",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("consignado")


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

@app.after_request
def set_security_headers(response):
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "geolocation=(), camera=(), microphone=()"
    )
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------

_CONTROL_CHARS = re.compile(
    r"[\x00-\x1f\x7f-\x9f]"
)
_ZERO_WIDTH = re.compile(
    r"[\u200b\u200c\u200d\u200e\u200f\ufeff\u2060\u2061\u2062\u2063\u2064]"
)
_NAME_PATTERN = re.compile(r"^[a-zA-ZÀ-ÿ\s'\\-]+$")
_CITY_PATTERN = re.compile(r"^[a-zA-ZÀ-ÿ0-9\s'\\-\\.]+$")


def sanitize(value, max_length):
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value)
    value = _CONTROL_CHARS.sub("", value)
    value = _ZERO_WIDTH.sub("", value)
    value = value.strip()
    return value[:max_length]


def mask_cpf(cpf_digits):
    if len(cpf_digits) == 11:
        return f"***.{cpf_digits[3:6]}.***-**"
    return "***"


def escape_discord(text):
    replacements = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}
    for char, entity in replacements.items():
        text = text.replace(char, entity)
    return text


# ---------------------------------------------------------------------------
# CPF validation
# ---------------------------------------------------------------------------

def valid_cpf(raw):
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    for offset, factor in [(9, 10), (10, 11)]:
        total = sum(
            int(digits[i]) * (factor - i) for i in range(offset)
        )
        remainder = (total * 10) % 11
        if remainder >= 10:
            remainder = 0
        if remainder != int(digits[offset]):
            return False
    return True


# ---------------------------------------------------------------------------
# Discord notification
# ---------------------------------------------------------------------------

def send_discord_embed(data, meta):
    if not DISCORD_WEBHOOK_URL:
        log.warning("DISCORD_WEBHOOK_URL not configured")
        return False

    now = datetime.now(BRT)
    timestamp_display = now.strftime("%d/%m/%Y às %H:%M:%S (BRT)")

    payload = {
        "username": "Consignado CLT",
        "embeds": [
            {
                "title": "Nova solicitação de crédito",
                "description": "Um cliente preencheu o formulário de solicitação.",
                "color": 15118650,
                "fields": [
                    {
                        "name": "Nome",
                        "value": escape_discord(data["nome"]),
                        "inline": True,
                    },
                    {
                        "name": "Telefone",
                        "value": escape_discord(data["telefone"]),
                        "inline": True,
                    },
                    {
                        "name": "CPF",
                        "value": escape_discord(data["cpf"]),
                        "inline": True,
                    },
                    {
                        "name": "Cidade",
                        "value": escape_discord(data["cidade"]),
                        "inline": True,
                    },
                    {
                        "name": "Valor desejado",
                        "value": escape_discord(data["valor"]),
                        "inline": True,
                    },
                    {
                        "name": "\u200b",
                        "value": "\u200b",
                        "inline": True,
                    },
                    {
                        "name": "IP",
                        "value": f"`{meta['ip']}`",
                        "inline": True,
                    },
                    {
                        "name": "User-Agent",
                        "value": f"`{escape_discord(meta['user_agent'][:120])}`",
                        "inline": False,
                    },
                    {
                        "name": "Referer",
                        "value": escape_discord(meta["referer"] or "Direto"),
                        "inline": True,
                    },
                    {
                        "name": "Origem",
                        "value": escape_discord(meta["origin"] or "—"),
                        "inline": True,
                    },
                    {
                        "name": "Data / Hora",
                        "value": timestamp_display,
                        "inline": False,
                    },
                ],
                "footer": {
                    "text": "Consignado CLT • Sistema de captação",
                },
                "timestamp": now.astimezone(timezone.utc).isoformat(),
            }
        ],
    }

    try:
        resp = http_client.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code not in (200, 204):
            log.error("Discord responded with status %d", resp.status_code)
            return False
        return True
    except http_client.RequestException:
        log.exception("Failed to reach Discord")
        return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/submit", methods=["POST"])
@limiter.limit("5/minute")
def submit():
    content_type = request.content_type or ""
    if "application/json" not in content_type:
        return jsonify({"error": "Content-Type inválido"}), 415

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Payload inválido"}), 400

    # -- Honeypot --
    website = data.get("website", "")
    if isinstance(website, str) and website.strip():
        log.info("Honeypot triggered — request silently accepted")
        return jsonify({"status": "ok"}), 200

    # -- Extract and sanitize --
    nome = sanitize(data.get("nome", ""), 120)
    telefone = sanitize(data.get("telefone", ""), 20)
    cpf = sanitize(data.get("cpf", ""), 14)
    valor = sanitize(data.get("valor", ""), 30)
    cidade = sanitize(data.get("cidade", ""), 80)
    consent = data.get("consent")

    # -- Validate --
    errors = []

    if not nome or len(nome.split()) < 2 or not _NAME_PATTERN.match(nome):
        errors.append("Nome inválido")

    tel_digits = re.sub(r"\D", "", telefone)
    if len(tel_digits) < 10 or len(tel_digits) > 11:
        errors.append("Telefone inválido")

    if not valid_cpf(cpf):
        errors.append("CPF inválido")

    if valor not in ALLOWED_VALUES:
        errors.append("Valor inválido")

    if not cidade or len(cidade) < 2 or not _CITY_PATTERN.match(cidade):
        errors.append("Cidade inválida")

    if consent is not True:
        errors.append("Consentimento necessário")

    if errors:
        return jsonify({"error": errors[0]}), 422

    # -- Request metadata --
    meta = {
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr or "—").split(",")[0].strip(),
        "user_agent": sanitize(request.headers.get("User-Agent", "—"), 256),
        "referer": sanitize(request.headers.get("Referer", ""), 256) or None,
        "origin": sanitize(request.headers.get("Origin", ""), 256) or None,
    }

    validated = {
        "nome": nome,
        "telefone": telefone,
        "cpf": cpf,
        "valor": valor,
        "cidade": cidade,
    }

    cpf_digits = re.sub(r"\D", "", cpf)
    log.info(
        "Lead received — nome=%s, cidade=%s, valor=%s, cpf=%s, ip=%s",
        nome,
        cidade,
        valor,
        mask_cpf(cpf_digits),
        meta["ip"],
    )

    success = send_discord_embed(validated, meta)
    if not success:
        return jsonify({"error": "Erro interno. Tente novamente."}), 503

    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Catch-all for unsupported methods/routes
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Recurso não encontrado"}), 404


@app.errorhandler(405)
def method_not_allowed(_):
    return jsonify({"error": "Método não permitido"}), 405


@app.errorhandler(413)
def payload_too_large(_):
    return jsonify({"error": "Payload excede o limite"}), 413


@app.errorhandler(429)
def rate_limit_exceeded(_):
    return jsonify({"error": "Muitas requisições. Aguarde e tente novamente."}), 429


@app.errorhandler(500)
def internal_error(_):
    return jsonify({"error": "Erro interno"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))


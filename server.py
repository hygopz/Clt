import os
from datetime import datetime
from flask import Flask, request, jsonify
import requests

app = Flask(__name__, static_folder='.', static_url_path='')

WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')
NOTIFY_MODE = os.getenv('NOTIFY_MODE', 'terminal').lower()
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

VALID_NOTIFY_MODES = {'discord', 'terminal', 'telegram', 'both'}

if NOTIFY_MODE not in VALID_NOTIFY_MODES:
    raise RuntimeError(f'NOTIFY_MODE inválido: {NOTIFY_MODE}')

def valid_cpf(raw):
    cpf = ''.join(filter(str.isdigit, raw))
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    def calc_digit(nums, factor):
        total = sum(int(digit) * multiplier for digit, multiplier in zip(nums, range(factor, 1, -1)))
        remainder = (total * 10) % 11
        return 0 if remainder in (10, 11) else remainder
    first = calc_digit(cpf[:9], 10)
    second = calc_digit(cpf[:10], 11)
    return first == int(cpf[9]) and second == int(cpf[10])


def notify_terminal(values):
    print('=== NOVA SOLICITAÇÃO ===')
    print('Nome:', values.get('nome', ''))
    print('Telefone:', values.get('telefone', ''))
    print('CPF:', values.get('cpf', ''))
    print('Valor desejado:', values.get('valor', ''))
    print('Cidade:', values.get('cidade', ''))
    print('Consentimento:', 'Sim' if values.get('consent') else 'Não')
    print('========================')


def notify_telegram(values):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    text = (
        f"Nova solicitação recebida\n"
        f"Nome: {values.get('nome', 'Não informado')}\n"
        f"Telefone: {values.get('telefone', 'Não informado')}\n"
        f"CPF: {values.get('cpf', 'Não informado')}\n"
        f"Valor desejado: {values.get('valor', 'Não informado')}\n"
        f"Cidade: {values.get('cidade', 'Não informado')}\n"
        f"Consentimento: {'Sim' if values.get('consent') else 'Não'}"
    )
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    return requests.post(url, data={'chat_id': TELEGRAM_CHAT_ID, 'text': text}, timeout=10)


def notify_discord(payload):
    if not WEBHOOK_URL:
        return None
    headers = {'Content-Type': 'application/json'}
    response = requests.post(WEBHOOK_URL, json=payload, headers=headers, timeout=10)
    print('Discord status:', response.status_code)
    print(response.text)
    return response


@app.route('/submit', methods=['POST'])
def submit():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({'error': 'Dados inválidos'}), 400

    nome = data.get('nome', '').strip()
    telefone = data.get('telefone', '').strip()
    cpf = data.get('cpf', '').strip()
    valor = data.get('valor', '').strip()
    cidade = data.get('cidade', '').strip()
    consent = data.get('consent', False)
    website = data.get('website', '').strip()

    if website:
        return jsonify({'error': 'Bot detectado'}), 400
    if len(nome.split()) < 2:
        return jsonify({'error': 'Nome inválido'}), 400
    if len(''.join(filter(str.isdigit, telefone))) < 10:
        return jsonify({'error': 'Telefone inválido'}), 400
    if not valid_cpf(cpf):
        return jsonify({'error': 'CPF inválido'}), 400
    if not valor:
        return jsonify({'error': 'Valor inválido'}), 400
    if len(cidade) < 2:
        return jsonify({'error': 'Cidade inválida'}), 400
    if consent is not True:
        return jsonify({'error': 'Consentimento necessário'}), 400

    values = {
        'nome': nome,
        'telefone': telefone,
        'cpf': cpf,
        'valor': valor,
        'cidade': cidade,
        'consent': consent
    }

    if NOTIFY_MODE in ('terminal', 'both'):
        notify_terminal(values)

    if NOTIFY_MODE in ('telegram', 'both'):
        telegram_response = notify_telegram(values)
        if telegram_response is None or telegram_response.status_code != 200:
            print('Telegram falhou:', telegram_response.status_code if telegram_response else 'sem resposta')

    if NOTIFY_MODE in ('discord', 'both'):
        payload = {
            'username': 'Solicitação CLT',
            'embeds': [
                {
                    'title': 'Nova solicitação recebida',
                    'description': 'Um cliente solicitou contato pelo site.',
                    'color': 16763904,
                    'fields': [
                        {'name': 'Nome', 'value': nome or 'Não informado', 'inline': True},
                        {'name': 'Telefone', 'value': telefone or 'Não informado', 'inline': True},
                        {'name': 'CPF', 'value': cpf or 'Não informado', 'inline': True},
                        {'name': 'Valor desejado', 'value': valor or 'Não informado', 'inline': True},
                        {'name': 'Cidade', 'value': cidade or 'Não informado', 'inline': True},
                        {'name': 'Consentimento', 'value': 'Sim' if consent else 'Não', 'inline': True},
                    ],
                    'footer': {'text': 'Página de contato - Consignado CLT'},
                    'timestamp': datetime.utcnow().isoformat() + 'Z'
                }
            ]
        }

        discord_response = notify_discord(payload)
        if discord_response is None:
            return jsonify({'error': 'Webhook não configurado'}), 500
        if discord_response.status_code not in (200, 204):
            return jsonify({
                'status': discord_response.status_code,
                'discord': discord_response.text
            }), 502

    return jsonify({'status': 'ok'}), 200

@app.route('/')
def index():
    return app.send_static_file('index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

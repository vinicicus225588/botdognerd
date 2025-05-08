from flask import Flask, request
import requests
import os
import time as sleep_time
from datetime import datetime, timedelta, time

app = Flask(__name__)

# 1) Armazena horário da última mensagem por número
t_last_seen = {}
# 2) Armazena histórico de mensagens para cada usuário
user_sessions = {}

# 3) Carrega prompt de sistema (texto base para o assistente) — sem saudações
with open("dognerd_whatsapp_prompt.py", "r") as f:
    original_prompt = f.read()

# 3.1) Impede saudações automáticas do prompt
NO_GREET = "🚫 Atenção: não inclua nenhuma saudação ou 'bom dia/boa noite' automática.\n\n"
SYSTEM_PROMPT_CONTENT = NO_GREET + original_prompt
SYSTEM_PROMPT = {"role": "system", "content": SYSTEM_PROMPT_CONTENT}

# 3.2) Lista de frases que não devem aparecer nas respostas
PROHIBITED_PHRASES = [
    "Oi! Como posso te ajudar hoje?",
    "Oi, como posso te ajudar hoje?",
    "Olá, como posso te ajudar hoje?",
    "Olá! como posso te ajudar hoje?",
    "Como posso te ajudar hoje?",
    "Oi! em que posso ajudar?",
    "Oi, em que posso ajudar?"
]

GREETING_TRIGGERS = {
    "oi", "oie", "oiee", "olá", "ola",
    "tudo bem", "tudo bem?", "bom dia", "boa tarde", "boa noite"
}

def sanitize_reply(text: str) -> str:
    """Remove qualquer frase indesejada do texto de resposta."""
    for phrase in PROHIBITED_PHRASES:
        text = text.replace(phrase, "")
    return text.strip()

def get_history(user_id):
    """Retorna a sessão de mensagens do usuário, criando se não existir"""
    return user_sessions.setdefault(user_id, [])

def save_message(user_id, role, content):
    """Adiciona uma mensagem ao histórico do usuário"""
    get_history(user_id).append({"role": role, "content": content})

def is_human_hours(now: datetime) -> bool:
    """
    Retorna True se for de segunda a sexta (weekday 0–4)
    e entre 09:00 e 19:00.
    """
    return now.weekday() < 5 and time(18, 0) <= now.time() < time(19, 0)

@app.route('/')
def home():
    return 'WhatsApp Bot is running!'

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    now = datetime.now()

    # ──────────── Desliga o bot em horário humano ────────────
    if is_human_hours(now):
        return "", 200
    # ─────────────────────────────────────────────────────────

    incoming = request.values.get('Body', '').strip()
    user = request.values.get('From', '')
    prev = t_last_seen.get(user)
    t_last_seen[user] = now

    # prepara versão limpa para checar trigger
    incoming_clean = incoming.lower().strip().rstrip("?!")

    # Se é hiato >8h ou trigger de saudação, envia nossos 2 balões
    if (not prev or (now - prev > timedelta(hours=8))) or (incoming_clean in GREETING_TRIGGERS):
        user_sessions[user] = []
        save_message(user, SYSTEM_PROMPT['role'], SYSTEM_PROMPT['content'])

        # Balão 1
        part1 = (
            "Oiee! 🐶 Aqui é o Dog Nerdson falando! Bão? "
            "Tô de plantão de 19:00 às 9:00 am e pego finais de semana tb! Quase um doutô! 👨‍⚕️ 😇"
        )
        save_message(user, 'assistant', part1)
        send_whatsapp_message(user, part1)

        # espera 4,5 segundos
        sleep_time.sleep(4.5)

        # Balão 2
        part2 = (
            "Me mande a sua dúvida que eu manjo de todos os paranauês da DogNerd. "
            "Sei ajudar nas medidas, no prazo ou no que precisar!\n\n"
            "Se der ruim, meus humanos te chamam no horário comercial! 😁"
        )
        save_message(user, 'assistant', part2)
        send_whatsapp_message(user, part2)

        return "", 200

    # Processa mídia de áudio
    if request.values.get("NumMedia") != "0":
        media_url = request.values.get("MediaUrl0")
        media_type = request.values.get("MediaContentType0")
        if media_type and "audio" in media_type:
            save_message(user, 'assistant', 'Recebi seu áudio e já respondo! 🎧')
            send_whatsapp_message(user, 'Recebi seu áudio e já respondo! 🎧')
            sleep_time.sleep(2)
            transcript = transcribe_audio(media_url)
            user_text = transcript or 'Tive dificuldade pra ouvir seu áudio. Pode repetir, por favor?'
            save_message(user, 'user', user_text)
            reply = sanitize_reply(get_openai_response(user))
            save_message(user, 'assistant', reply)
            sleep_time.sleep(2)
            send_whatsapp_message(user, reply)
            return "", 200

    # Processa texto
    if incoming:
        save_message(user, 'user', incoming)
        reply = sanitize_reply(get_openai_response(user))
        save_message(user, 'assistant', reply)
        sleep_time.sleep(2)
        send_whatsapp_message(user, reply)

    return "", 200

def transcribe_audio(audio_url):
    try:
        audio_data = requests.get(audio_url)
        headers = {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"}
        files = {'file': ('audio.ogg', audio_data.content, 'application/octet-stream')}
        data = {'model': 'whisper-1', 'language': 'pt'}
        response = requests.post(
            'https://api.openai.com/v1/audio/transcriptions',
            headers=headers, files=files, data=data
        )
        if response.status_code == 200:
            return response.json().get("text")
    except Exception as e:
        print(f"Erro na transcrição de áudio: {e}")
    return None

def get_openai_response(user_id):
    """Gera resposta usando todo o histórico de mensagens salvas do usuário"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "Erro: chave da OpenAI não encontrada."

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "DogNerdBot/1.0"
    }

    history = get_history(user_id)
    body = {
        "model": "gpt-3.5-turbo-0125",
        "messages": history,
        "temperature": 0.7
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions", headers=headers,
            json=body, timeout=10
        )
        data = response.json()
        if response.status_code == 200:
            return data['choices'][0]['message']['content'].strip()
        elif response.status_code == 429:
            return "Estamos com alta demanda. Tente novamente em instantes. 😊"
        elif 400 <= response.status_code < 500:
            return f"Erro da OpenAI ({response.status_code}): {data.get('error', {}).get('message', 'Erro na requisição')}"
        else:
            return f"Erro do servidor OpenAI ({response.status_code})"
    except requests.exceptions.Timeout:
        return "Estamos com instabilidade momentânea. Tente novamente em breve. 🙏"
    except requests.exceptions.RequestException as e:
        return f"Falha na conexão com a OpenAI: {str(e)}"
    except Exception:
        return "Erro inesperado. Tente novamente. 🚧"

def send_whatsapp_message(to, body):
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_whatsapp_number = 'whatsapp:+14155238886'

    if not account_sid or not auth_token:
        print("Erro: credenciais da Twilio ausentes.")
        return False

    try:
        url = f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json'
        data = {'From': from_whatsapp_number, 'To': to, 'Body': body}
        response = requests.post(url, data=data, auth=(account_sid, auth_token), timeout=10)
        if response.status_code == 201:
            print("Mensagem enviada com sucesso!")
            return True
        else:
            print(f"Erro ao enviar mensagem: {response.status_code}")
            print(f"Resposta: {response.text}")
            return False
    except Exception as e:
        print(f"Erro ao conectar com Twilio: {str(e)}")
        return False

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000)
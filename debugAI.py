import urllib.request
import urllib.error
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
def test_gemini_connection():
    print("=== Диагностика подключения к Google API ===")
    
    # 1. Чтение ключа из конфигурации
    if not CONFIG_FILE.exists():
        print("❌ Ошибка: Файл config.json не найден. Запусти основной оверлей хотя бы один раз и сохрани настройки.")
        return

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        print(f"❌ Ошибка чтения config.json: {e}")
        return

    api_key = config.get("ai_api_key", "").strip()
    if not api_key:
        print("❌ Ошибка: Поле 'ai_api_key' пустое. Укажи ключ в настройках оверлея.")
        return

    # Маскируем ключ (показываем только начало и конец)
    masked_key = f"{api_key[:10]}...{api_key[-5:]}" if len(api_key) > 15 else "НЕДЕЙСТВИТЕЛЬНЫЙ ФОРМАТ"
    print(f"✅ Найден API-ключ: {masked_key}")

    # 2. Настройка маршрутизации (Прокси)
    print("\nЕсли используешь клиент для VLESS (v2rayN, nekoray и т.д.), укажи его локальный HTTP-порт.")
    proxy_port = input("Введи порт (например, 10808) или нажми Enter для прямого подключения: ").strip()
    
    if proxy_port:
        proxy_url = f"http://127.0.0.1:{proxy_port}"
        print(f"🔄 Настраиваю прокси: {proxy_url}")
        proxy_handler = urllib.request.ProxyHandler({'https': proxy_url, 'http': proxy_url})
        opener = urllib.request.build_opener(proxy_handler)
        urllib.request.install_opener(opener)
    else:
        print("⚠️ Используется прямое подключение (скорее всего, будет заблокировано).")

    # 3. Отправка тестового запроса
    model = "gemini-3.5-flash-lite"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"parts": [{"text": "как дела друг?"}]}],
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    
    print("\n⏳ Отправка запроса к API... (таймаут 15 секунд)")
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            print(f"✅ Статус ответа: {response.getcode()}")
            result = json.loads(response.read().decode('utf-8'))
            answer = result['candidates'][0]['content']['parts'][0]['text']
            print(f"🤖 Ответ ИИ: {answer.strip()}")
            print("\n🎉 Тест успешно пройден! Можешь переносить порт прокси в основной код.")
            
    except urllib.error.HTTPError as e:
        try:
            err_msg = json.loads(e.read().decode('utf-8'))['error']['message']
        except Exception:
            err_msg = str(e)
        print(f"\n❌ Ошибка API ({e.code}): {err_msg}")
        if e.code == 400:
            print("👉 API-ключ недействителен (скопирован с ошибкой или удален в Google AI Studio).")
        
    except urllib.error.URLError as e:
        print(f"\n❌ Ошибка сети: {e.reason}")
        print("👉 Соединение сброшено или тайм-аут. Google заблокирован.")
        print("👉 Убедись, что VPN-клиент включен, и ты указал правильный порт.")
        
    except Exception as e:
        print(f"\n❌ Неизвестная ошибка: {e}")

if __name__ == "__main__":
    test_gemini_connection()
    input("\nНажми Enter для выхода...")
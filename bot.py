import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
import pandas as pd
import io
import os
import asyncio
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from math import radians, sin, cos, sqrt, atan2
from telegram.error import Conflict

# -------------------- Health check HTTP server (обязателен для Render Free) --------------------
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        # подавляем шум в логах
        return

def start_health_server(port: int):
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

# -------------------- Настройки --------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

GOOGLE_SHEETS_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vSWt19kiS7cdliNwfs9SriPW-LGrr4lmLl2Q6AojRqGyqwy9lI91PB-9OYKi5LOJBbbB5dx6uaqA5tK/"
    "pub?gid=0&single=true&output=csv"
)
MAX_DISTANCE_KM = 10

# Render прокидывает порт через переменную PORT — слушаем ровно его
PORT = int(os.environ.get("PORT", "10000"))

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------- Работа с данными --------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

def load_data_from_google_sheets():
    try:
        df = pd.read_csv(GOOGLE_SHEETS_CSV_URL)
        df["Широта"] = pd.to_numeric(df["Широта"], errors="coerce")
        df["Долгота"] = pd.to_numeric(df["Долгота"], errors="coerce")
        df = df.dropna(subset=["Широта", "Долгота"])
        return df
    except Exception as e:
        logger.error(f"Ошибка загрузки данных: {e}")
        return pd.DataFrame()

def get_unique_categories(df):
    return df["Тип"].dropna().unique().tolist()

def get_subcategories_by_type(df, selected_type):
    return df[df["Тип"] == selected_type]["Подтип"].dropna().unique().tolist()

def find_nearest_places(df, user_lat, user_lon, place_type, subcategory, max_distance=MAX_DISTANCE_KM, n=5):
    filtered_df = df[(df["Тип"] == place_type) & (df["Подтип"] == subcategory)]
    if filtered_df.empty:
        return []
    distances = []
    for _, row in filtered_df.iterrows():
        distance = haversine(user_lat, user_lon, row["Широта"], row["Долгота"])
        if distance <= max_distance:
            distances.append((row, distance))
    if not distances:
        return []
    distances.sort(key=lambda x: x[1])
    return distances[:n]

def generate_yandex_maps_link(lat, lon):
    return f"https://yandex.ru/maps/?pt={lon},{lat}&z=17&l=map"

# -------------------- UI --------------------
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("🗺️ План поездки")],
        [KeyboardButton("🔍 Поиск места")],
        [KeyboardButton("🏠 Главное меню")],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    msg = "🏠 Главное меню:\n\nВыберите режим работы:"
    if update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup)
    else:
        await update.callback_query.message.reply_text(msg, reply_markup=reply_markup)

# -------------------- Хэндлеры --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update, context)

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text == "🗺️ План поездки":
        await send_trip_plan(update, context)
    elif text == "🔍 Поиск места":
        await request_location(update, context)
    elif text == "🏠 Главное меню":
        await show_main_menu(update, context)
    else:
        await handle_unknown_message(update, context)

async def send_trip_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    df = load_data_from_google_sheets()
    if df.empty:
        await update.message.reply_text("❌ В данный момент база данных недоступна. Попробуйте позже.")
        return
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    csv_buffer.seek(0)
    await update.message.reply_document(
        document=io.BytesIO(csv_buffer.getvalue().encode()),
        filename="food_guide_plan.csv",
        caption="📋 Вот все наши точки! Можно импортировать CSV в карты.",
    )

async def request_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📍 Отправить местоположение", request_location=True)],
        [KeyboardButton("🏠 Главное меню")],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        f"📍 Поделитесь местоположением — поищу места в радиусе {MAX_DISTANCE_KM} км:",
        reply_markup=reply_markup,
    )

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = update.message.location
    if not location:
        await update.message.reply_text("❌ Не удалось получить местоположение. Попробуйте ещё раз.")
        return
    context.user_data["user_lat"] = location.latitude
    context.user_data["user_lon"] = location.longitude

    df = load_data_from_google_sheets()
    categories = get_unique_categories(df)
    if not categories:
        await update.message.reply_text("❌ В базе нет доступных категорий.")
        return

    keyboard = [[InlineKeyboardButton(category, callback_data=f"type_{category}")] for category in categories]
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])
    await update.message.reply_text(
        f"📍 Местоположение получено! Теперь выберите тип места:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_type = query.data.replace("type_", "")
    context.user_data["selected_type"] = selected_type

    df = load_data_from_google_sheets()
    subcategories = get_subcategories_by_type(df, selected_type)
    if not subcategories:
        await query.edit_message_text("❌ Для выбранного типа нет доступных подкатегорий.")
        return

    keyboard = [[InlineKeyboardButton(sc, callback_data=f"subtype_{sc}")] for sc in subcategories]
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])
    await query.edit_message_text(
        f"🏷️ Выбран тип: {selected_type}\nТеперь выберите подкатегорию:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_subcategory_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_subtype = query.data.replace("subtype_", "")
    selected_type = context.user_data.get("selected_type")
    user_lat = context.user_data.get("user_lat")
    user_lon = context.user_data.get("user_lon")

    if not all([selected_type, user_lat, user_lon]):
        await query.edit_message_text("❌ Ошибка: данные не найдены. Начните поиск заново.")
        return

    df = load_data_from_google_sheets()
    nearest_places = find_nearest_places(df, user_lat, user_lon, selected_type, selected_subtype)
    if not nearest_places:
        await query.edit_message_text(
            f"❌ В радиусе {MAX_DISTANCE_KM} км не найдено мест по выбранным критериям."
        )
        return

    context.user_data["nearest_places"] = [(place.to_dict(), dist) for place, dist in nearest_places]

    message_text = f"🏆 Найденные места ({selected_type} - {selected_subtype}) в радиусе {MAX_DISTANCE_KM} км:\n\n"
    keyboard = []
    for i, (place, distance) in enumerate(nearest_places, 1):
        d = place.to_dict()
        message_text += f"{i}. {d['Название']} (расстояние: {distance:.1f} км)\n"
        if "Примечание" in d and pd.notna(d["Примечание"]):
            message_text += f"   📝 {d['Примечание']}\n"
        message_text += "\n"
        keyboard.append([InlineKeyboardButton(f"{i}. {d['Название']}", callback_data=f"place_{i-1}")])

    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])
    await query.edit_message_text(message_text + "Выберите место для маршрута:", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_place_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.replace("place_", ""))
    nearest_places = context.user_data.get("nearest_places", [])
    if idx >= len(nearest_places):
        await query.edit_message_text("❌ Ошибка: место не найдено.")
        return
    place_data, distance = nearest_places[idx]
    yandex_url = generate_yandex_maps_link(place_data["Широта"], place_data["Долгота"])

    msg = (
        f"📍 {place_data['Название']}\n"
        f"📌 Адрес: {place_data['Адрес']}\n"
        f"📏 Расстояние: {distance:.1f} км\n"
        f"🏷️ Тип: {place_data['Тип']} - {place_data['Подтип']}\n"
    )
    if "Примечание" in place_data and pd.notna(place_data["Примечание"]):
        msg += f"📝 Примечание: {place_data['Примечание']}\n"

    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]]))
    await context.bot.send_message(chat_id=query.message.chat.id, text=f"🗺️ [Построить маршрут в Яндекс.Картах]({yandex_url})", parse_mode="Markdown")

async def handle_main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [KeyboardButton("🗺️ План поездки")],
        [KeyboardButton("🔍 Поиск места")],
        [KeyboardButton("🏠 Главное меню")],
    ]
    await query.message.reply_text("🏠 Главное меню:\n\nВыберите режим работы:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def handle_unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤔 Я не понимаю эту команду. Используйте кнопки меню.")

# ---- error handler: подавляем 409 во время перекрытия инстансов при деплое ----
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        # второй инстанс на деплое — игнорируем, PTB сам переподключится
        return
    logger.exception("Unhandled error", exc_info=err)

def setup_bot_handlers(app: Application):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(CallbackQueryHandler(handle_category_selection, pattern="^type_"))
    app.add_handler(CallbackQueryHandler(handle_subcategory_selection, pattern="^subtype_"))
    app.add_handler(CallbackQueryHandler(handle_place_selection, pattern="^place_"))
    app.add_handler(CallbackQueryHandler(handle_main_menu_callback, pattern="^main_menu$"))
    app.add_handler(MessageHandler(filters.ALL, handle_unknown_message))
    app.add_error_handler(error_handler)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Переменная окружения BOT_TOKEN не задана.")

    # Python 3.13: явно создаём и назначаем event loop (иначе get_event_loop() падает)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()
    setup_bot_handlers(app)

    # локальный HTTP для Render (держим порт открытым)
    start_health_server(PORT)
    logger.info("Starting bot in POLLING mode with health-check on /")

    # на всякий случай снимаем webhook перед polling
    try:
        loop.run_until_complete(app.bot.delete_webhook(drop_pending_updates=True))
    except Exception as e:
        logger.warning(f"Не удалось удалить webhook: {e}")

    # запускаем polling (сбрасываем старые апдейты, чтобы не догонять историю)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
